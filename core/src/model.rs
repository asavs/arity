//! Model access. Two wire formats cover the spike:
//! - OpenAI chat completions: MLX, and later Nous Portal, OpenRouter, any API key.
//! - OpenAI Responses on the ChatGPT backend: the ChatGPT subscription.

use std::io::{BufRead, BufReader};
use std::sync::mpsc;

use anyhow::{Result, anyhow, bail};
use serde_json::{Value, json};

use crate::codex_auth;
use crate::journal::Entry;
use crate::tools::ToolSpec;

const CODEX_URL: &str = "https://chatgpt.com/backend-api/codex/responses";

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
    Chat { base: String, model: String, key: Option<String> },
    /// The ChatGPT subscription, signed in with `login`.
    Codex { model: String, auth: std::path::PathBuf },
}

impl Model {
    /// `mlx:<model>` or `codex:<model>`.
    pub fn parse(spec: &str, data_dir: &std::path::Path) -> Result<Self> {
        let (kind, model) = spec.split_once(':').ok_or_else(|| anyhow!("model must be mlx:<id> or codex:<id>"))?;
        Ok(match kind {
            "mlx" => Model::Chat {
                base: std::env::var("ARITY_MLX_URL").unwrap_or_else(|_| "http://127.0.0.1:8080/v1".into()),
                model: model.into(),
                key: None,
            },
            "codex" => Model::Codex { model: model.into(), auth: data_dir.join("codex-auth.json") },
            other => bail!("unknown provider {other}"),
        })
    }

    /// The model's reply to the chain, streamed into `out` as it arrives.
    pub fn complete(&self, system: &str, chain: &[Entry], tools: &[ToolSpec], out: &mut Sink) -> Result<Reply> {
        match self {
            Model::Chat { base, model, key } => chat(base, model, key.as_deref(), system, chain, tools, out),
            Model::Codex { model, auth } => codex(model, auth, system, chain, tools, out),
        }
    }
}

/// Entries the model reads as the user speaking: the user's own messages, reports,
/// Arity's notices, and the person's changes to a task card. A model's own card
/// proposals have no text; its tool call already records them.
fn user_text(e: &Entry) -> Option<&str> {
    match e.kind.as_str() {
        "user" | "report" | "notice" | "draft" => e.body["text"].as_str(),
        _ => None,
    }
}

fn client() -> reqwest::blocking::Client {
    reqwest::blocking::Client::builder()
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

fn chat(base: &str, model: &str, key: Option<&str>, system: &str, chain: &[Entry], tools: &[ToolSpec], out: &mut Sink) -> Result<Reply> {
    let body = json!({
        "model": model,
        "messages": chat_messages(system, chain),
        "tools": tools.iter().map(|t| json!({"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.parameters}})).collect::<Vec<_>>(),
        "max_tokens": 2048,
        "stream": true,
    });
    let mut req = client().post(format!("{base}/chat/completions")).json(&body);
    if let Some(key) = key {
        req = req.bearer_auth(key);
    }
    let resp = req.send()?;
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
    let (token, account) = codex_auth::access(auth)?;
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
        .header("chatgpt-account-id", account)
        .header("OpenAI-Beta", "responses=experimental")
        .header("originator", "arity")
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
