//! Model access. Two wire formats cover the spike:
//! - OpenAI chat completions: any provider in config.json (MLX, Nous Portal, any API key).
//! - OpenAI Responses with a ChatGPT sign-in: the ChatGPT subscription.

use std::io::{BufRead, BufReader};
use std::sync::mpsc;

use anyhow::{Result, anyhow, bail};
use serde_json::{Value, json};

use crate::codex_auth;
use crate::endpoint::Endpoint;
use crate::journal::Entry;
use crate::tools::ToolSpec;

/// The public Responses API, which a ChatGPT sign-in may use on the plan's usage.
const CODEX_URL: &str = "https://api.openai.com/v1/responses";

#[derive(Debug, Clone, Default)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: String,
}

/// What one model call cost in tokens, as the provider reported it.
/// `input` counts the whole prompt, `cached` the part of it served from the provider's prompt cache.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct TokenUsage {
    pub input: u64,
    pub cached: u64,
    pub output: u64,
    pub total: u64,
}

impl TokenUsage {
    pub fn to_json(&self) -> Value {
        json!({ "input": self.input, "cached": self.cached, "output": self.output, "total": self.total })
    }

    /// The `"usage"` an assistant entry was committed with; None for entries from before usage was kept.
    pub fn from_json(v: &Value) -> Option<Self> {
        let n = |k: &str| v[k].as_u64().unwrap_or(0);
        v.is_object().then(|| TokenUsage { input: n("input"), cached: n("cached"), output: n("output"), total: n("total") })
    }

    /// Chat completions: `prompt_tokens`, `prompt_tokens_details.cached_tokens`, `completion_tokens`, `total_tokens`.
    /// DeepSeek-style servers report cache hits as `prompt_cache_hit_tokens` instead.
    fn from_chat(u: &Value) -> Option<Self> {
        let input = u["prompt_tokens"].as_u64()?;
        let output = u["completion_tokens"].as_u64().unwrap_or(0);
        let cached = u["prompt_tokens_details"]["cached_tokens"].as_u64().or_else(|| u["prompt_cache_hit_tokens"].as_u64()).unwrap_or(0);
        Some(TokenUsage { input, cached, output, total: u["total_tokens"].as_u64().unwrap_or(input + output) })
    }

    /// Responses: `input_tokens`, `input_tokens_details.cached_tokens`, `output_tokens`, `total_tokens`.
    fn from_responses(u: &Value) -> Option<Self> {
        let input = u["input_tokens"].as_u64()?;
        let output = u["output_tokens"].as_u64().unwrap_or(0);
        let cached = u["input_tokens_details"]["cached_tokens"].as_u64().unwrap_or(0);
        Some(TokenUsage { input, cached, output, total: u["total_tokens"].as_u64().unwrap_or(input + output) })
    }
}

#[derive(Debug)]
pub struct Reply {
    pub text: String,
    pub tool_calls: Vec<ToolCall>,
    /// Stopped part way. The text is what had arrived; half-made tool calls are dropped.
    pub interrupted: bool,
    /// None when the provider did not say (some local servers never do).
    pub usage: Option<TokenUsage>,
}

impl Reply {
    pub fn to_json(&self) -> Value {
        let mut v = json!({
            "text": self.text,
            "tool_calls": self.tool_calls.iter()
                .map(|c| json!({"id": c.id, "name": c.name, "arguments": c.arguments}))
                .collect::<Vec<_>>(),
        });
        if self.interrupted {
            v["interrupted"] = json!(true);
        }
        if let Some(u) = &self.usage {
            v["usage"] = u.to_json();
        }
        v
    }
}

pub fn tool_calls_of(body: &Value) -> Vec<ToolCall> {
    body["tool_calls"]
        .as_array()
        .map(|calls| {
            calls
                .iter()
                .map(|c| ToolCall {
                    id: c["id"].as_str().unwrap_or_default().into(),
                    name: c["name"].as_str().unwrap_or_default().into(),
                    arguments: c["arguments"].as_str().unwrap_or("{}").into(),
                })
                .collect()
        })
        .unwrap_or_default()
}

pub enum Model {
    /// Any OpenAI-compatible chat endpoint. MLX by default.
    Chat { endpoint: Endpoint, model: String },
    /// The ChatGPT subscription, signed in with `login`.
    Codex { model: String, auth: std::path::PathBuf },
}

impl Model {
    /// `codex:<model>`, or `<provider>:<model>` for a provider in config.json.
    /// A provider that needs a key and has none is refused here, before any call.
    /// `session_id` fills a provider's session header, if it has one.
    pub fn parse(spec: &str, data_dir: &std::path::Path, session_id: &str) -> Result<Self> {
        let (kind, model) = spec.split_once(':').ok_or_else(|| anyhow!("model must be <provider>:<id>, e.g. mlx:<id>"))?;
        if kind == "codex" {
            return Ok(Model::Codex { model: model.into(), auth: data_dir.join("codex-auth.json") });
        }
        Ok(Model::Chat { endpoint: Endpoint::open(data_dir, kind, session_id)?, model: model.into() })
    }

    /// The model's reply to the chain, streamed into `out` as it arrives.
    pub fn complete(&self, system: &str, chain: &[Entry], tools: &[ToolSpec], out: &mut Sink) -> Result<Reply> {
        match self {
            Model::Chat { endpoint, model } => chat(endpoint, model, system, chain, tools, out).map_err(|e| endpoint.redact(e)),
            Model::Codex { model, auth } => codex(model, auth, system, chain, tools, out),
        }
    }
}

/// Entries the model reads as the user speaking: the user's own messages, reports, other agents' messages,
/// Arity's notices, and the person's changes to a task card. A model's own card
/// proposals have no text; its tool call already records them.
fn user_text(e: &Entry) -> Option<&str> {
    match e.kind.as_str() {
        "user" | "report" | "message" | "notice" | "draft" => e.body["text"].as_str(),
        _ => None,
    }
}

/// A `"compact"` entry as the model reads it: a handoff from its earlier self, spoken as the user
/// so every wire format and chat template accepts it wherever it lands.
pub(crate) fn compact_text(e: &Entry) -> Option<String> {
    (e.kind == "compact").then(|| {
        let summary = e.body["summary"].as_str().unwrap_or_default().trim();
        let mut text = format!(
            "[Context compacted] The earlier part of this conversation was replaced by this summary to save space:\n\n{summary}"
        );
        if e.body["continue"] == true {
            text.push_str("\n\nContinue the work from where it left off.");
        }
        text
    })
}

/// Arity names itself to every endpoint; OpenCode Go asks clients to.
pub const USER_AGENT: &str = concat!("Arity/", env!("CARGO_PKG_VERSION"));

fn client() -> reqwest::blocking::Client {
    reqwest::blocking::Client::builder()
        .user_agent(USER_AGENT)
        .timeout(std::time::Duration::from_secs(300))
        .build()
        .expect("http client")
}

/// Where a reply goes as it arrives. Called with each piece of text, and with ""
/// now and then while waiting; returns false to stop the reply where it is.
pub type Sink<'a> = dyn FnMut(&str) -> bool + 'a;

/// Server-sent events, read on a thread of their own so a stop is noticed even
/// while the server is silent. A stopped reply's reader is left to finish alone.
struct Events {
    rx: mpsc::Receiver<std::io::Result<String>>,
    stopped: bool,
}

impl Events {
    fn new(resp: impl std::io::Read + Send + 'static) -> Self {
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(resp).lines() {
                if tx.send(line).is_err() {
                    break;
                }
            }
        });
        Events { rx, stopped: false }
    }

    /// The next event, or None at the end of the stream or once `out` says stop.
    fn next(&mut self, out: &mut Sink) -> Result<Option<Value>> {
        loop {
            if !out("") {
                self.stopped = true;
                return Ok(None);
            }
            match self.rx.recv_timeout(std::time::Duration::from_millis(100)) {
                Ok(line) => {
                    let line = line?;
                    let Some(data) = line.strip_prefix("data:").map(str::trim) else { continue };
                    if data == "[DONE]" {
                        return Ok(None);
                    }
                    if let Ok(v) = serde_json::from_str(data) {
                        return Ok(Some(v));
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => return Ok(None),
            }
        }
    }
}

/// The chain as OpenAI chat messages.
pub(crate) fn chat_messages(system: &str, chain: &[Entry]) -> Vec<Value> {
    let mut messages = vec![json!({"role": "system", "content": system})];
    for e in chain {
        if let Some(text) = user_text(e) {
            messages.push(json!({"role": "user", "content": text}));
            continue;
        }
        if let Some(text) = compact_text(e) {
            messages.push(json!({"role": "user", "content": text}));
            continue;
        }
        messages.push(match e.kind.as_str() {
            "system" => match e.body["text"].as_str().filter(|t| !t.is_empty()) {
                Some(text) => json!({"role": "system", "content": text}),
                None => continue,
            },
            "assistant" => {
                let calls = tool_calls_of(&e.body);
                let mut m = json!({"role": "assistant", "content": e.body["text"]});
                if !calls.is_empty() {
                    m["tool_calls"] = calls
                        .iter()
                        .map(|c| json!({"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}))
                        .collect();
                }
                m
            }
            "tool" => json!({"role": "tool", "tool_call_id": e.body["call_id"], "content": e.body["output"]}),
            "tool_intent" | "rating" => continue,
            _ => continue,
        });
    }
    messages
}

fn chat(endpoint: &Endpoint, model: &str, system: &str, chain: &[Entry], tools: &[ToolSpec], out: &mut Sink) -> Result<Reply> {
    let mut body = json!({
        "model": model,
        "messages": chat_messages(system, chain),
        "tools": tools.iter().map(|t| json!({"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.parameters}})).collect::<Vec<_>>(),
        "max_tokens": 2048,
        "stream": true,
        "stream_options": { "include_usage": true },
    });
    let mut resp = endpoint.observe(endpoint.post(&client(), "/chat/completions").json(&body).send()?);
    let mut status = resp.status();
    if !status.is_success() {
        let detail = resp.text().unwrap_or_default();
        // Servers that do not know stream_options and refuse unknown fields: ask again without it.
        if !(matches!(status.as_u16(), 400 | 422) && detail.contains("stream_options")) {
            bail!("{status}: {detail}");
        }
        body.as_object_mut().unwrap().remove("stream_options");
        resp = endpoint.observe(endpoint.post(&client(), "/chat/completions").json(&body).send()?);
        status = resp.status();
        if !status.is_success() {
            bail!("{status}: {}", resp.text().unwrap_or_default());
        }
    }
    chat_stream(&mut Events::new(resp), out)
}

/// A chat completions stream: text and tool calls in deltas, and with `include_usage`, a last chunk
/// with `usage` and no choices. A server that ignores `stream_options` leaves usage None.
fn chat_stream(events: &mut Events, out: &mut Sink) -> Result<Reply> {
    let (mut text, mut calls, mut usage) = (String::new(), Vec::<ToolCall>::new(), None);
    while let Some(ev) = events.next(out)? {
        if !ev["error"].is_null() {
            bail!("{}", ev["error"]);
        }
        if let Some(u) = TokenUsage::from_chat(&ev["usage"]) {
            usage = Some(u);
        }
        let delta = &ev["choices"][0]["delta"];
        if let Some(t) = delta["content"].as_str().filter(|t| !t.is_empty()) {
            text.push_str(t);
            out(t);
        }
        // A call arrives in pieces: its id and name first, then its arguments bit by bit.
        for c in delta["tool_calls"].as_array().into_iter().flatten() {
            let i = c["index"].as_u64().map_or(calls.len().saturating_sub(1), |i| i as usize);
            if i >= calls.len() {
                calls.resize(i + 1, ToolCall::default());
            }
            let call = &mut calls[i];
            if let Some(id) = c["id"].as_str().filter(|s| !s.is_empty()) {
                call.id = id.into();
            }
            if let Some(name) = c["function"]["name"].as_str().filter(|_| call.name.is_empty()) {
                call.name = name.into();
            }
            match &c["function"]["arguments"] {
                Value::String(s) => call.arguments.push_str(s),
                Value::Null => {}
                // Some servers send arguments as an object, not a string.
                other => call.arguments.push_str(&other.to_string()),
            }
        }
    }
    let interrupted = events.stopped;
    if interrupted {
        calls.clear();
    }
    Ok(Reply { text: text.trim().into(), tool_calls: calls, interrupted, usage })
}

/// The chain as Responses input items.
pub(crate) fn codex_input(chain: &[Entry]) -> Vec<Value> {
    let mut input = Vec::new();
    for e in chain {
        if let Some(text) = user_text(e) {
            input.push(json!({"role": "user", "content": [{"type": "input_text", "text": text}]}));
            continue;
        }
        if let Some(text) = compact_text(e) {
            input.push(json!({"role": "user", "content": [{"type": "input_text", "text": text}]}));
            continue;
        }
        match e.kind.as_str() {
            "system" => {
                if let Some(text) = e.body["text"].as_str().filter(|t| !t.is_empty()) {
                    input.push(json!({"role": "developer", "content": [{"type": "input_text", "text": text}]}));
                }
            }
            "assistant" => {
                if let Some(text) = e.body["text"].as_str().filter(|t| !t.is_empty()) {
                    input.push(json!({"role": "assistant", "content": [{"type": "output_text", "text": text}]}));
                }
                for c in tool_calls_of(&e.body) {
                    input.push(json!({"type": "function_call", "call_id": c.id, "name": c.name, "arguments": c.arguments}));
                }
            }
            "tool" => input.push(json!({"type": "function_call_output", "call_id": e.body["call_id"], "output": e.body["output"]})),
            "tool_intent" | "rating" => {}
            _ => {}
        }
    }
    input
}

fn codex(model: &str, auth: &std::path::Path, system: &str, chain: &[Entry], tools: &[ToolSpec], out: &mut Sink) -> Result<Reply> {
    let (token, _account) = codex_auth::access(auth)?;
    let body = json!({
        "model": model,
        "instructions": system,
        "input": codex_input(chain),
        "tools": tools.iter().map(|t| json!({"type": "function", "name": t.name,
            "description": t.description, "parameters": t.parameters, "strict": false})).collect::<Vec<_>>(),
        "tool_choice": "auto",
        "parallel_tool_calls": false,
        "stream": true,
        "store": false,
    });
    let resp = client()
        .post(CODEX_URL)
        .bearer_auth(token)
        .json(&body)
        .send()?;
    if !resp.status().is_success() {
        let status = resp.status();
        bail!("{status}: {}", resp.text().unwrap_or_default());
    }

    codex_stream(&mut Events::new(resp), out)
}

/// A Responses stream. Text deltas are shown as they come; finished output items make the reply,
/// and `response.completed` carries the usage.
fn codex_stream(events: &mut Events, out: &mut Sink) -> Result<Reply> {
    let mut streamed = String::new();
    let mut reply = Reply { text: String::new(), tool_calls: vec![], interrupted: false, usage: None };
    while let Some(ev) = events.next(out)? {
        match ev["type"].as_str().unwrap_or_default() {
            "response.output_text.delta" => {
                if let Some(t) = ev["delta"].as_str() {
                    streamed.push_str(t);
                    out(t);
                }
            }
            "response.output_item.done" => {
                let item = &ev["item"];
                match item["type"].as_str().unwrap_or_default() {
                    "message" => {
                        for part in item["content"].as_array().into_iter().flatten() {
                            if let Some(t) = part["text"].as_str() {
                                reply.text.push_str(t);
                            }
                        }
                    }
                    "function_call" => reply.tool_calls.push(ToolCall {
                        id: item["call_id"].as_str().unwrap_or_default().into(),
                        name: item["name"].as_str().unwrap_or_default().into(),
                        arguments: item["arguments"].as_str().unwrap_or("{}").into(),
                    }),
                    _ => {}
                }
            }
            "response.failed" | "error" => bail!("codex error: {ev}"),
            "response.completed" => {
                reply.usage = TokenUsage::from_responses(&ev["response"]["usage"]);
                break;
            }
            _ => {}
        }
    }
    if events.stopped {
        return Ok(Reply { text: streamed, tool_calls: vec![], interrupted: true, usage: None });
    }
    Ok(reply)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};

    fn dir(name: &str, providers: Value) -> std::path::PathBuf {
        let d = std::env::temp_dir().join(format!("arity-model-{name}-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("config.json"), json!({ "workers": [], "providers": providers }).to_string()).unwrap();
        d
    }

    #[test]
    fn specs_resolve_through_providers() -> Result<()> {
        let d = dir("parse", json!({ "lab": { "url": "http://lab:1/v1" } }));
        match Model::parse("lab:org/m:q4", &d, "c")? {
            Model::Chat { endpoint, model } => {
                assert_eq!((endpoint.base.as_str(), model.as_str(), endpoint.key), ("http://lab:1/v1", "org/m:q4", None));
                assert!(endpoint.headers.is_empty());
            }
            _ => panic!("lab is a chat provider"),
        }
        assert!(matches!(Model::parse("mlx:x", &d, "c")?, Model::Chat { .. }), "mlx is a provider by default");
        assert!(matches!(Model::parse("codex:gpt", &d, "c")?, Model::Codex { .. }));
        assert!(Model::parse("nope:x", &d, "c").is_err());
        assert!(Model::parse("no-colon", &d, "c").is_err());
        Ok(())
    }

    #[test]
    fn a_provider_without_its_key_is_refused() -> Result<()> {
        let d = dir("nokey", json!({}));
        let err = Model::parse("nous:Hermes-4-70B", &d, "c").err().expect("nous needs a key").to_string();
        assert!(err.contains("arity key set nous"), "{err}");
        crate::keys::set(&d, "nous", "sk-test")?;
        assert!(matches!(Model::parse("nous:Hermes-4-70B", &d, "c")?, Model::Chat { endpoint: Endpoint { key: Some(_), .. }, .. }));
        // OpenCode Go shares OpenCode's key and sends a session id that is the same for one conversation only.
        assert!(Model::parse("opencode-go:kimi-k3", &d, "c").is_err());
        crate::keys::set(&d, "opencode", "sk-oc")?;
        let session = |conv: &str| match Model::parse("opencode-go:kimi-k3", &d, conv).unwrap() {
            Model::Chat { endpoint, .. } => (endpoint.key, endpoint.headers),
            _ => panic!(),
        };
        let (key, h1) = session("chat-1");
        assert_eq!(key.as_deref(), Some("sk-oc"));
        assert_eq!(h1[0].0, "x-opencode-session");
        assert!(!h1[0].1.contains("chat"));
        assert_eq!(h1, session("chat-1").1);
        assert_ne!(h1, session("chat-2").1);
        Ok(())
    }

    #[test]
    fn a_key_never_appears_in_an_error() -> Result<()> {
        // A server that rejects the request and echoes every header back, key included.
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let port = listener.local_addr()?.port();
        std::thread::spawn(move || {
            for mut s in listener.incoming().flatten() {
                let mut buf = [0u8; 8192];
                let n = s.read(&mut buf).unwrap_or(0);
                let echo = serde_json::to_string(&String::from_utf8_lossy(&buf[..n])).unwrap();
                let body = format!("{{\"error\": {echo}}}");
                let _ = write!(s, "HTTP/1.1 401 Unauthorized\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}", body.len());
            }
        });
        let key = "sk-secret-0123456789";
        for (name, url) in [("echo", format!("http://127.0.0.1:{port}/v1")), ("closed", "http://127.0.0.1:9/v1".into())] {
            let d = dir(name, json!({ name: { "url": url, "key": true } }));
            crate::keys::set(&d, name, key)?;
            let err = Model::parse(&format!("{name}:m"), &d, "c")?.complete("", &[], &[], &mut |_| true).err().expect("call fails");
            let text = format!("{err:#} {err:?}");
            assert!(!text.contains(key), "{name}: {text}");
            assert!(!std::fs::read_to_string(d.join("config.json"))?.contains(key));
        }
        Ok(())
    }

    fn sse(events: &[Value]) -> Events {
        let mut s: String = events.iter().map(|e| format!("data: {e}\n\n")).collect();
        s.push_str("data: [DONE]\n\n");
        Events::new(std::io::Cursor::new(s.into_bytes()))
    }

    #[test]
    fn chat_streams_report_usage_with_cached_tokens_in_the_last_chunk() -> Result<()> {
        let mut seen = String::new();
        let reply = chat_stream(
            &mut sse(&[
                json!({ "choices": [{ "delta": { "content": "Hel" } }], "usage": null }),
                json!({ "choices": [{ "delta": { "content": "lo" } }], "usage": null }),
                json!({ "choices": [], "usage": { "prompt_tokens": 18400, "completion_tokens": 12, "total_tokens": 18412,
                    "prompt_tokens_details": { "cached_tokens": 13248 } } }),
            ]),
            &mut |t| {
                seen.push_str(t);
                true
            },
        )?;
        assert_eq!((reply.text.as_str(), seen.as_str()), ("Hello", "Hello"));
        assert_eq!(reply.usage, Some(TokenUsage { input: 18400, cached: 13248, output: 12, total: 18412 }));
        assert_eq!(TokenUsage::from_json(&reply.to_json()["usage"]), reply.usage, "round-trips through the journal body");

        // A server that ignores stream_options: no usage, and nothing else changes.
        let plain = chat_stream(&mut sse(&[json!({ "choices": [{ "delta": { "content": "Hi" } }] })]), &mut |_| true)?;
        assert_eq!((plain.text.as_str(), plain.usage), ("Hi", None));
        assert!(plain.to_json().get("usage").is_none());
        // DeepSeek-style cache hits; a missing total is input + output.
        let ds = TokenUsage::from_chat(&json!({ "prompt_tokens": 100, "completion_tokens": 5, "prompt_cache_hit_tokens": 64 }));
        assert_eq!(ds, Some(TokenUsage { input: 100, cached: 64, output: 5, total: 105 }));
        Ok(())
    }

    #[test]
    fn responses_streams_report_usage_on_response_completed() -> Result<()> {
        let reply = codex_stream(
            &mut sse(&[
                json!({ "type": "response.output_text.delta", "delta": "Done." }),
                json!({ "type": "response.output_item.done", "item": { "type": "message", "content": [{ "type": "output_text", "text": "Done." }] } }),
                json!({ "type": "response.output_item.done", "item": { "type": "function_call", "call_id": "c1", "name": "list_tasks", "arguments": "{}" } }),
                json!({ "type": "response.completed", "response": { "usage": { "input_tokens": 5000, "output_tokens": 40,
                    "total_tokens": 5040, "input_tokens_details": { "cached_tokens": 4096 } } } }),
            ]),
            &mut |_| true,
        )?;
        assert_eq!((reply.text.as_str(), reply.tool_calls.len()), ("Done.", 1));
        assert_eq!(reply.usage, Some(TokenUsage { input: 5000, cached: 4096, output: 40, total: 5040 }));
        let bare = codex_stream(&mut sse(&[json!({ "type": "response.completed", "response": {} })]), &mut |_| true)?;
        assert_eq!(bare.usage, None);
        Ok(())
    }

    /// A one-request-at-a-time HTTP server: `answer` gets each request's JSON body and returns (status line, body).
    fn serve(answer: impl Fn(&Value) -> (&'static str, String) + Send + 'static) -> Result<u16> {
        let listener = std::net::TcpListener::bind("127.0.0.1:0")?;
        let port = listener.local_addr()?.port();
        std::thread::spawn(move || {
            for mut s in listener.incoming().flatten() {
                let mut buf = Vec::new();
                let mut chunk = [0u8; 8192];
                let body = loop {
                    let n = s.read(&mut chunk).unwrap_or(0);
                    if n == 0 {
                        break Value::Null;
                    }
                    buf.extend_from_slice(&chunk[..n]);
                    let text = String::from_utf8_lossy(&buf).to_string();
                    let Some(end) = text.find("\r\n\r\n") else { continue };
                    let len = text[..end]
                        .lines()
                        .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0)))
                        .unwrap_or(0);
                    if buf.len() >= end + 4 + len {
                        break serde_json::from_slice(&buf[end + 4..end + 4 + len]).unwrap_or(Value::Null);
                    }
                };
                let (status, reply) = answer(&body);
                let kind = if status.starts_with("200") { "text/event-stream" } else { "application/json" };
                let _ = write!(s, "HTTP/1.1 {status}\r\nContent-Type: {kind}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{reply}", reply.len());
            }
        });
        Ok(port)
    }

    #[test]
    fn chat_asks_for_usage_and_falls_back_when_a_server_refuses_stream_options() -> Result<()> {
        let stream = |usage: bool| {
            let mut s = format!("data: {}\n\n", json!({ "choices": [{ "delta": { "content": "ok" } }] }));
            if usage {
                s.push_str(&format!("data: {}\n\n", json!({ "choices": [], "usage": { "prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11 } })));
            }
            s + "data: [DONE]\n\n"
        };
        // Honours stream_options.
        let port = serve(move |body| {
            assert_eq!(body["stream_options"], json!({ "include_usage": true }));
            ("200 OK", stream(true))
        })?;
        let d = dir("usage-ok", json!({ "ok": { "url": format!("http://127.0.0.1:{port}/v1") } }));
        let reply = Model::parse("ok:m", &d, "c")?.complete("s", &[], &[], &mut |_| true)?;
        assert_eq!(reply.usage, Some(TokenUsage { input: 10, cached: 0, output: 1, total: 11 }));

        // Refuses it: asked again without, and the reply comes back with no usage.
        let asked = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let counter = asked.clone();
        let port = serve(move |body| {
            counter.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            if body.get("stream_options").is_some() {
                ("400 Bad Request", json!({ "error": "Unrecognized request argument supplied: stream_options" }).to_string())
            } else {
                ("200 OK", stream(false))
            }
        })?;
        let d = dir("usage-refused", json!({ "strict": { "url": format!("http://127.0.0.1:{port}/v1") } }));
        let reply = Model::parse("strict:m", &d, "c")?.complete("s", &[], &[], &mut |_| true)?;
        assert_eq!((reply.text.as_str(), reply.usage), ("ok", None));
        assert_eq!(asked.load(std::sync::atomic::Ordering::SeqCst), 2);
        Ok(())
    }

    #[test]
    fn a_compact_entry_renders_as_a_framed_handoff_in_both_formats() {
        let entry = |id: i64, kind: &str, body: Value| Entry { id, kind: kind.into(), body, model: None };
        let chain = vec![
            entry(9, "compact", json!({ "summary": "Goal: ship M1.", "compacted_count": 8, "continue": true })),
            entry(10, "system", json!({ "sections": { "date": "Today is 2026-11-21." }, "text": "Today is 2026-11-21." })),
        ];
        let chat = chat_messages("S", &chain);
        assert_eq!(chat[1]["role"], "user");
        let text = chat[1]["content"].as_str().unwrap();
        assert!(text.starts_with("[Context compacted]") && text.contains("Goal: ship M1.") && text.ends_with("left off."), "{text}");
        let codex = codex_input(&chain);
        assert_eq!(codex[0]["role"], "user");
        assert_eq!(codex[0]["content"][0]["text"], text);
        assert_eq!(codex[1]["role"], "developer");
    }

    #[test]
    fn system_entries_render_and_tool_intent_is_omitted() {
        let entry = |id: i64, kind: &str, body: Value| Entry {
            id,
            kind: kind.into(),
            body,
            model: None,
        };
        let chain = vec![
            entry(1, "system", json!({ "sections": { "date": "Today is 2026-11-21." }, "text": "Today is 2026-11-21." })),
            entry(2, "system", json!({ "sections": {}, "text": "" })),
            entry(3, "user", json!({ "text": "Hello" })),
            entry(4, "tool_intent", json!({ "call_id": "c1", "name": "list_tasks", "arguments": "{}" })),
            entry(5, "rating", json!({ "task": 1, "score": 5, "note": "great" })),
            entry(6, "assistant", json!({ "text": "Hi there.", "tool_calls": [] })),
            entry(7, "system", json!({ "sections": { "card": null }, "text": "No task card is open now." })),
        ];

        let chat = chat_messages("STATIC PREFIX", &chain);
        assert_eq!(
            chat,
            vec![
                json!({ "role": "system", "content": "STATIC PREFIX" }),
                json!({ "role": "system", "content": "Today is 2026-11-21." }),
                json!({ "role": "user", "content": "Hello" }),
                json!({ "role": "assistant", "content": "Hi there." }),
                json!({ "role": "system", "content": "No task card is open now." }),
            ]
        );

        let codex = codex_input(&chain);
        assert_eq!(
            codex,
            vec![
                json!({ "role": "developer", "content": [{ "type": "input_text", "text": "Today is 2026-11-21." }] }),
                json!({ "role": "user", "content": [{ "type": "input_text", "text": "Hello" }] }),
                json!({ "role": "assistant", "content": [{ "type": "output_text", "text": "Hi there." }] }),
                json!({ "role": "developer", "content": [{ "type": "input_text", "text": "No task card is open now." }] }),
            ]
        );
    }
}
