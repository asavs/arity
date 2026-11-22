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

#[derive(Debug)]
pub struct Reply {
    pub text: String,
    pub tool_calls: Vec<ToolCall>,
    /// Stopped part way. The text is what had arrived; half-made tool calls are dropped.
    pub interrupted: bool,
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
    /// `conversation` fills a provider's session header, if it has one.
    pub fn parse(spec: &str, data_dir: &std::path::Path, conversation: &str) -> Result<Self> {
        let (kind, model) = spec.split_once(':').ok_or_else(|| anyhow!("model must be <provider>:<id>, e.g. mlx:<id>"))?;
        if kind == "codex" {
            return Ok(Model::Codex { model: model.into(), auth: data_dir.join("codex-auth.json") });
        }
        Ok(Model::Chat { endpoint: Endpoint::open(data_dir, kind, conversation)?, model: model.into() })
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
    fn new(resp: reqwest::blocking::Response) -> Self {
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
        messages.push(match e.kind.as_str() {
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
            _ => continue,
        });
    }
    messages
}

fn chat(endpoint: &Endpoint, model: &str, system: &str, chain: &[Entry], tools: &[ToolSpec], out: &mut Sink) -> Result<Reply> {
    let body = json!({
        "model": model,
        "messages": chat_messages(system, chain),
        "tools": tools.iter().map(|t| json!({"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.parameters}})).collect::<Vec<_>>(),
        "max_tokens": 2048,
        "stream": true,
    });
    let resp = endpoint.observe(endpoint.post(&client(), "/chat/completions").json(&body).send()?);
    let status = resp.status();
    if !status.is_success() {
        bail!("{status}: {}", resp.text().unwrap_or_default());
    }
    let mut events = Events::new(resp);
    let (mut text, mut calls) = (String::new(), Vec::<ToolCall>::new());
    while let Some(ev) = events.next(out)? {
        if !ev["error"].is_null() {
            bail!("{}", ev["error"]);
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
    Ok(Reply { text: text.trim().into(), tool_calls: calls, interrupted })
}

/// The chain as Responses input items.
pub(crate) fn codex_input(chain: &[Entry]) -> Vec<Value> {
    let mut input = Vec::new();
    for e in chain {
        if let Some(text) = user_text(e) {
            input.push(json!({"role": "user", "content": [{"type": "input_text", "text": text}]}));
            continue;
        }
        match e.kind.as_str() {
            "assistant" => {
                if let Some(text) = e.body["text"].as_str().filter(|t| !t.is_empty()) {
                    input.push(json!({"role": "assistant", "content": [{"type": "output_text", "text": text}]}));
                }
                for c in tool_calls_of(&e.body) {
                    input.push(json!({"type": "function_call", "call_id": c.id, "name": c.name, "arguments": c.arguments}));
                }
            }
            "tool" => input.push(json!({"type": "function_call_output", "call_id": e.body["call_id"], "output": e.body["output"]})),
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

    // Text deltas are shown as they come; finished output items make the reply.
    let mut events = Events::new(resp);
    let mut streamed = String::new();
    let mut reply = Reply { text: String::new(), tool_calls: vec![], interrupted: false };
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
            "response.completed" => break,
            _ => {}
        }
    }
    if events.stopped {
        return Ok(Reply { text: streamed, tool_calls: vec![], interrupted: true });
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
}
