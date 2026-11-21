//! Which models the configured providers offer, from each one's `/models`,
//! cached for a day in models-cache.json. A provider without that list offers
//! the `models` named for it in config.json.

use std::path::Path;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

use crate::{config, keys};

/// The ChatGPT backend has no list to fetch.
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
    if data_dir.join("codex-auth.json").exists() {
        out.extend(CODEX_MODELS.iter().map(|m| format!("codex:{m}")));
    }
    for p in config::load(data_dir)?.providers {
        let key = keys::get(data_dir, &p.name);
        if p.needs_key && key.is_none() {
            continue;
        }
        let cached = &cache[&p.name];
        let fresh = cached["at"].as_u64().is_some_and(|at| now() < at + MAX_AGE);
        if refresh || !fresh {
            if let Some(ids) = fetch(&p.url, key.as_deref()) {
                cache[&p.name] = json!({ "at": now(), "ids": ids });
            }
        }
        let ids: Vec<String> = match cache[&p.name]["ids"].as_array() {
            Some(ids) => ids.iter().filter_map(|m| m.as_str().map(String::from)).collect(),
            None => p.models,
        };
        out.extend(ids.iter().map(|m| format!("{}:{m}", p.name)));
    }
    let _ = std::fs::write(&path, serde_json::to_string_pretty(&cache)?);
    Ok(out)
}

/// The ids from an OpenAI-style `GET /models`, or None if there is no such list.
fn fetch(base: &str, key: Option<&str>) -> Option<Vec<String>> {
    let client = reqwest::blocking::Client::builder().timeout(Duration::from_secs(5)).build().ok()?;
    let mut req = client.get(format!("{base}/models"));
    if let Some(key) = key {
        req = req.bearer_auth(key);
    }
    let v: Value = req.send().ok()?.error_for_status().ok()?.json().ok()?;
    let mut ids: Vec<String> = v["data"].as_array()?.iter().filter_map(|m| m["id"].as_str().map(String::from)).collect();
    ids.sort();
    Some(ids)
}
