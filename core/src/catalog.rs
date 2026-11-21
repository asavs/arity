//! Which models ChatGPT and the configured providers offer, from each one's `/models`,
//! cached for a day in models-cache.json. A provider without that list offers
//! the `models` named for it in config.json.

use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

use crate::endpoint::{self, Endpoint};
use crate::{codex_auth, config, keys};

/// Offered for ChatGPT until its list has been fetched once.
const CODEX_MODELS: &[&str] = &["gpt-6.1-terra", "gpt-6.2-sol"];
const MAX_AGE: u64 = 24 * 3600;

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
}

/// `<provider>:<model>` for every provider that is usable: configured, with a key if it needs one.
pub fn models(data_dir: &Path, refresh: bool) -> anyhow::Result<Vec<String>> {
    let path = data_dir.join("models-cache.json");
    let mut cache: Value = std::fs::read_to_string(&path).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(json!({}));
    let mut out = vec![];
    let mut sources: Vec<(String, Vec<String>)> = vec![];
    let auth = data_dir.join("codex-auth.json");
    if codex_auth::signed_in(&auth) {
        sources.push(("codex".into(), CODEX_MODELS.iter().map(|m| m.to_string()).collect()));
    }
    for p in config::load(data_dir)?.providers {
        if !p.needs_key || keys::get(data_dir, &p.key_name).is_some() {
            sources.push((p.name, p.models));
        }
    }
    for (name, fallback) in sources {
        if refresh || !fresh(&cache[&name]) {
            let endpoint = if name == "codex" {
                codex_auth::access(&auth).ok().map(|(token, _)| Endpoint::bare("codex", codex_auth::RESOURCE, Some(token)))
            } else {
                Endpoint::open(data_dir, &name, "").ok()
            };
            if let Some(ids) = endpoint.and_then(|e| e.models()) {
                cache[&name] = json!({ "at": now(), "ids": ids });
            }
        }
        let ids: Vec<String> = match cache[&name]["ids"].as_array() {
            Some(ids) => ids.iter().filter_map(|m| m.as_str().map(String::from)).collect(),
            None => fallback,
        };
        out.extend(ids.iter().map(|m| format!("{name}:{m}")));
    }
    let _ = std::fs::write(&path, serde_json::to_string_pretty(&cache)?);
    Ok(out)
}

fn fresh(cached: &Value) -> bool {
    cached["at"].as_u64().is_some_and(|at| now() < at + MAX_AGE)
}

/// Whether a provider takes its key, found without running a model.
#[derive(Debug, Clone, PartialEq, uniffi::Enum)]
pub enum KeyCheck {
    Works,
    Rejected { reason: String },
    Unreachable { reason: String },
    /// It needs a key and has none.
    NoKey,
}

/// Lists the provider's models with its key, then asks for a reply to no
/// messages from the first one. Either is refused at once for a bad key; a good
/// key gets a complaint about the empty request instead, and nothing runs.
pub fn check(data_dir: &Path, provider: &str) -> anyhow::Result<KeyCheck> {
    let p = config::load(data_dir)?.providers.into_iter().find(|p| p.name == provider)
        .ok_or_else(|| anyhow::anyhow!("no provider {provider} in config.json"))?;
    if p.needs_key && keys::get(data_dir, &p.key_name).is_none() {
        return Ok(KeyCheck::NoKey);
    }
    let e = Endpoint::open(data_dir, provider, "arity-key-check")?;
    let client = endpoint::quick();
    let verdict = |resp: reqwest::Result<reqwest::blocking::Response>| -> Option<KeyCheck> {
        let resp = match resp {
            Ok(r) => e.observe(r),
            Err(err) => return Some(KeyCheck::Unreachable { reason: err.without_url().to_string() }),
        };
        let status = resp.status();
        if status != 401 && status != 403 {
            return None;
        }
        let v: Value = resp.json().unwrap_or_default();
        let said = v["message"].as_str().or(v["error"]["message"].as_str()).or(v["error"].as_str()).unwrap_or("the key was refused");
        Some(KeyCheck::Rejected { reason: e.redact(anyhow::anyhow!("{said}")).to_string() })
    };
    if let Some(v) = verdict(e.get(&client, "/models").send()) {
        return Ok(v);
    }
    let model = e.models().and_then(|ids| ids.into_iter().next()).or(p.models.first().cloned());
    if let Some(model) = model.filter(|_| e.key.is_some()) {
        if let Some(v) = verdict(e.post(&client, "/chat/completions").json(&json!({ "model": model, "messages": [] })).send()) {
            return Ok(v);
        }
    }
    Ok(KeyCheck::Works)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};

    /// A provider that takes only "sk-good", and says so with the key it was given in the reply.
    fn server() -> u16 {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let port = listener.local_addr().unwrap().port();
        std::thread::spawn(move || {
            for mut s in listener.incoming().flatten() {
                let mut buf = [0u8; 4096];
                let n = s.read(&mut buf).unwrap_or(0);
                let req = String::from_utf8_lossy(&buf[..n]).to_lowercase();
                let (status, body) = if req.contains("bearer sk-good") {
                    if req.starts_with("get") { ("200 OK", r#"{"data":[{"id":"m"}]}"#.to_string()) } else { ("400 Bad Request", r#"{"message":"messages is required"}"#.into()) }
                } else {
                    let key = req.split("bearer ").nth(1).and_then(|r| r.split_whitespace().next()).unwrap_or("").to_string();
                    ("401 Unauthorized", format!(r#"{{"message":"invalid key {key}"}}"#))
                };
                let _ = write!(s, "HTTP/1.1 {status}\r\nContent-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}", body.len());
            }
        });
        port
    }

    #[test]
    fn a_key_is_checked_without_running_a_model() -> anyhow::Result<()> {
        let port = server();
        let d = std::env::temp_dir().join(format!("arity-check-{}", std::process::id()));
        std::fs::create_dir_all(&d)?;
        let url = format!("http://127.0.0.1:{port}/v1");
        std::fs::write(d.join("config.json"), json!({ "providers": { "p": { "url": url, "key": true }, "dead": { "url": "http://127.0.0.1:9/v1", "key": true } } }).to_string())?;
        assert_eq!(check(&d, "p")?, KeyCheck::NoKey);
        keys::set(&d, "p", "sk-good")?;
        assert_eq!(check(&d, "p")?, KeyCheck::Works);
        keys::set(&d, "p", "sk-bad-1234")?;
        assert_eq!(check(&d, "p")?, KeyCheck::Rejected { reason: "invalid key [key]".into() });
        keys::set(&d, "dead", "sk-good")?;
        assert!(matches!(check(&d, "dead")?, KeyCheck::Unreachable { .. }));
        std::fs::remove_dir_all(&d)?;
        Ok(())
    }
}
