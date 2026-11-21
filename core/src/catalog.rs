//! Which models ChatGPT and the configured providers offer, from each one's `/models`,
//! cached for a day in models-cache.json. A provider without that list offers
//! the `models` named for it in config.json.

use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

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
    let mut sources: Vec<(String, String, Option<String>, Vec<String>)> = vec![];
    let auth = data_dir.join("codex-auth.json");
    if codex_auth::signed_in(&auth) {
        let token = if refresh || !fresh(&cache["codex"]) { codex_auth::access(&auth).ok().map(|t| t.0) } else { None };
        sources.push(("codex".into(), codex_auth::RESOURCE.into(), token, CODEX_MODELS.iter().map(|m| m.to_string()).collect()));
    }
    for p in config::load(data_dir)?.providers {
        let key = keys::get(data_dir, &p.name);
        if p.needs_key && key.is_none() {
            continue;
        }
        sources.push((p.name, p.url, key, p.models));
    }
    for (name, url, key, fallback) in sources {
        if refresh || !fresh(&cache[&name]) {
            if let Some(ids) = fetch(&url, key.as_deref()) {
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

/// The ids from an OpenAI-style `GET /models`, or None if there is no such list.
/// With a ChatGPT sign-in the list is `models`, with `slug`s, and only those marked
/// `list` are meant to be offered.
fn fetch(base: &str, key: Option<&str>) -> Option<Vec<String>> {
    let client = reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build().ok()?;
    let mut req = client.get(format!("{base}/models"));
    if let Some(key) = key {
        req = req.bearer_auth(key);
    }
    let v: Value = req.send().ok()?.error_for_status().ok()?.json().ok()?;
    let mut ids: Vec<String> = match v["data"].as_array() {
        Some(data) => data.iter().filter_map(|m| m["id"].as_str().map(String::from)).collect(),
        None => v["models"].as_array()?.iter().filter(|m| m["visibility"] == "list").filter_map(|m| m["slug"].as_str().map(String::from)).collect(),
    };
    ids.sort();
    Some(ids)
}
