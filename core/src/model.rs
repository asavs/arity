//! Model access. Two wire formats cover the spike:
//! - OpenAI chat completions: any provider in config.json (MLX, Nous Portal, any API key).
//! - OpenAI Responses with a ChatGPT sign-in: the ChatGPT subscription.

use std::io::{BufRead, BufReader};

use anyhow::{Result, anyhow, bail};
use serde_json::{Value, json};

use crate::codex_auth;
use crate::journal::Entry;
use crate::tools::ToolSpec;

/// The public Responses API, which a ChatGPT sign-in may use on the plan's usage.
const CODEX_URL: &str = "https://api.openai.com/v1/responses";

#[derive(Debug, Clone)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: String,
}

#[derive(Debug)]
pub struct Reply {
    pub text: String,
    pub tool_calls: Vec<ToolCall>,
}

impl Reply {
    pub fn to_json(&self) -> Value {
        json!({
            "text": self.text,
            "tool_calls": self.tool_calls.iter()
                .map(|c| json!({"id": c.id, "name": c.name, "arguments": c.arguments}))
                .collect::<Vec<_>>(),
        })
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
    /// `codex:<model>`, or `<provider>:<model>` for a provider in config.json.
    /// A provider that needs a key and has none is refused here, before any call.
    pub fn parse(spec: &str, data_dir: &std::path::Path) -> Result<Self> {
        let (kind, model) = spec.split_once(':').ok_or_else(|| anyhow!("model must be <provider>:<id>, e.g. mlx:<id>"))?;
        if kind == "codex" {
            return Ok(Model::Codex { model: model.into(), auth: data_dir.join("codex-auth.json") });
        }
        let providers = crate::config::load(data_dir)?.providers;
        let Some(p) = providers.iter().find(|p| p.name == kind) else {
            let names: Vec<&str> = providers.iter().map(|p| p.name.as_str()).collect();
            bail!("unknown provider {kind}; config.json has {} and codex", names.join(", "));
        };
        let key = crate::keys::get(data_dir, kind);
        if p.needs_key && key.is_none() {
            bail!("{kind} has no API key; run `arity key set {kind}` or add one in Settings");
        }
        Ok(Model::Chat { base: p.url.clone(), model: model.into(), key })
    }

    pub fn complete(&self, system: &str, chain: &[Entry], tools: &[ToolSpec]) -> Result<Reply> {
        match self {
            Model::Chat { base, model, key } => chat(base, model, key.as_deref(), system, chain, tools).map_err(|e| redact(e, key.as_deref())),
            Model::Codex { model, auth } => codex(model, auth, system, chain, tools),
        }
    }
}

/// An error with the key cut out, in case a server echoed it back. Errors reach
/// the journal and the screen; keys must not.
fn redact(e: anyhow::Error, key: Option<&str>) -> anyhow::Error {
    match key {
        Some(k) if format!("{e:#}").contains(k) => anyhow!("{}", format!("{e:#}").replace(k, "[key]")),
        _ => e,
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

fn chat(base: &str, model: &str, key: Option<&str>, system: &str, chain: &[Entry], tools: &[ToolSpec]) -> Result<Reply> {
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
    let body = json!({
        "model": model,
        "messages": messages,
        "tools": tools.iter().map(|t| json!({"type": "function", "function": {
            "name": t.name, "description": t.description, "parameters": t.parameters}})).collect::<Vec<_>>(),
        "max_tokens": 2048,
    });
    let mut req = client().post(format!("{base}/chat/completions")).json(&body);
    if let Some(key) = key {
        req = req.bearer_auth(key);
    }
    let resp = req.send()?;
    let status = resp.status();
    let v: Value = resp.json()?;
    if !status.is_success() {
        bail!("{status}: {v}");
    }
    let msg = &v["choices"][0]["message"];
    let tool_calls = msg["tool_calls"]
        .as_array()
        .map(|calls| {
            calls
                .iter()
                .map(|c| ToolCall {
                    id: c["id"].as_str().unwrap_or_default().into(),
                    name: c["function"]["name"].as_str().unwrap_or_default().into(),
                    // Some servers send arguments as an object, not a string.
                    arguments: match &c["function"]["arguments"] {
                        Value::String(s) => s.clone(),
                        other => other.to_string(),
                    },
                })
                .collect()
        })
        .unwrap_or_default();
    Ok(Reply { text: msg["content"].as_str().unwrap_or_default().trim().into(), tool_calls })
}

fn codex(model: &str, auth: &std::path::Path, system: &str, chain: &[Entry], tools: &[ToolSpec]) -> Result<Reply> {
    let (token, account) = codex_auth::access(auth)?;
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
    let body = json!({
        "model": model,
        "instructions": system,
        "input": input,
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

    // Server-sent events. Only finished output items matter here.
    let mut reply = Reply { text: String::new(), tool_calls: vec![] };
    for line in BufReader::new(resp).lines() {
        let line = line?;
        let Some(data) = line.strip_prefix("data: ") else { continue };
        let Ok(ev) = serde_json::from_str::<Value>(data) else { continue };
        match ev["type"].as_str().unwrap_or_default() {
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
        match Model::parse("lab:org/m:q4", &d)? {
            Model::Chat { base, model, key } => assert_eq!((base.as_str(), model.as_str(), key), ("http://lab:1/v1", "org/m:q4", None)),
            _ => panic!("lab is a chat provider"),
        }
        assert!(matches!(Model::parse("mlx:x", &d)?, Model::Chat { .. }), "mlx is a provider by default");
        assert!(matches!(Model::parse("codex:gpt", &d)?, Model::Codex { .. }));
        assert!(Model::parse("nope:x", &d).is_err());
        assert!(Model::parse("no-colon", &d).is_err());
        Ok(())
    }

    #[test]
    fn a_provider_without_its_key_is_refused() -> Result<()> {
        let d = dir("nokey", json!({}));
        let err = Model::parse("nous:Hermes-4-70B", &d).err().expect("nous needs a key").to_string();
        assert!(err.contains("arity key set nous"), "{err}");
        crate::keys::set(&d, "nous", "sk-test")?;
        assert!(matches!(Model::parse("nous:Hermes-4-70B", &d)?, Model::Chat { key: Some(_), .. }));
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
            let err = Model::parse(&format!("{name}:m"), &d)?.complete("", &[], &[]).err().expect("call fails");
            let text = format!("{err:#} {err:?}");
            assert!(!text.contains(key), "{name}: {text}");
            assert!(!std::fs::read_to_string(d.join("config.json"))?.contains(key));
        }
        Ok(())
    }
}
