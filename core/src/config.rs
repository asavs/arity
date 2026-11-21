//! `config.json` in the data folder. Written with defaults on first use, so
//! there is always a file to edit.
//!
//! ```json
//! { "workers": ["mlx:…", "codex:…"],
//!   "providers": { "mlx": { "url": "http://127.0.0.1:8080/v1" },
//!                  "nous": { "url": "https://inference-api.nousresearch.com/v1", "key": true,
//!                            "models": ["Hermes-4-70B"] } } }
//! ```
//! A provider is any OpenAI-compatible chat endpoint. `key: true` means it needs
//! an API key, kept in keys.json, never here. `models` is offered when the
//! endpoint has no `/models` list. `mlx`, `nous` and `opencode` are there unless overridden.

use std::path::Path;

use anyhow::{Context, Result};
use serde_json::{Value, json};

pub struct Config {
    /// Models a task for an agent may be given, picked blind and uniformly.
    pub workers: Vec<String>,
    /// Sorted by name.
    pub providers: Vec<Provider>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Provider {
    pub name: String,
    pub url: String,
    pub needs_key: bool,
    pub models: Vec<String>,
}

const DEFAULT_WORKERS: &[&str] = &[
    "mlx:mlx-community/Qwen3.5-4B-MLX-4bit",
    "mlx:mlx-community/gemma-4-e4b-it-4bit",
    "codex:gpt-6.1-terra",
];

fn default_providers() -> Value {
    json!({
        "mlx": { "url": "http://127.0.0.1:8080/v1" },
        // API key from portal.nousresearch.com; see plans/v0.0.1.md.
        "nous": { "url": "https://inference-api.nousresearch.com/v1", "key": true,
                  "models": ["Hermes-4-70B", "Hermes-4-405B", "Hermes-4.3-36B"] },
        // OpenCode Zen, now "Console": API key from opencode.ai; every text model also speaks chat completions.
        "opencode": { "url": "https://opencode.ai/inference/openai/v1", "key": true },
    })
}

fn path(data_dir: &Path) -> std::path::PathBuf {
    data_dir.join("config.json")
}

fn read(data_dir: &Path) -> Result<Value> {
    let path = path(data_dir);
    if !path.exists() {
        std::fs::create_dir_all(data_dir)?;
        let v = json!({ "workers": DEFAULT_WORKERS, "providers": default_providers() });
        std::fs::write(&path, serde_json::to_string_pretty(&v)?)?;
    }
    serde_json::from_str(&std::fs::read_to_string(&path)?).with_context(|| format!("reading {}", path.display()))
}

pub fn load(data_dir: &Path) -> Result<Config> {
    parse(&read(data_dir)?)
}

fn parse(v: &Value) -> Result<Config> {
    let strings = |v: &Value| -> Vec<String> {
        v.as_array().map(|a| a.iter().filter_map(|m| m.as_str().map(String::from)).collect()).unwrap_or_default()
    };
    let mut providers = default_providers();
    for (name, p) in v["providers"].as_object().into_iter().flatten() {
        providers[name] = p.clone();
    }
    let mut out = vec![];
    for (name, p) in providers.as_object().unwrap() {
        anyhow::ensure!(!name.contains(':') && name != "codex", "provider name {name:?} is not allowed");
        let url = p["url"].as_str().with_context(|| format!("provider {name} has no url"))?;
        out.push(Provider {
            name: name.clone(),
            url: url.trim_end_matches('/').into(),
            needs_key: p["key"].as_bool().unwrap_or(false),
            models: strings(&p["models"]),
        });
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(Config { workers: strings(&v["workers"]), providers: out })
}

/// Add a model to `workers`, or take it out. Leaves the rest of the file as it is.
pub fn set_worker(data_dir: &Path, model: &str, on: bool) -> Result<()> {
    let mut v = read(data_dir)?;
    let mut workers = parse(&v)?.workers;
    workers.retain(|m| m != model);
    if on {
        workers.push(model.into());
    }
    v["workers"] = json!(workers);
    std::fs::write(path(data_dir), serde_json::to_string_pretty(&v)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn providers_merge_over_the_defaults() -> Result<()> {
        let c = parse(&json!({
            "workers": ["mlx:a"],
            "providers": { "mlx": { "url": "http://h:1/v1/" }, "groq": { "url": "https://g/v1", "key": true } },
        }))?;
        let names: Vec<&str> = c.providers.iter().map(|p| p.name.as_str()).collect();
        assert_eq!(names, ["groq", "mlx", "nous", "opencode"]);
        assert_eq!(c.providers[1].url, "http://h:1/v1");
        assert!(c.providers[0].needs_key && !c.providers[1].needs_key);
        assert!(parse(&json!({ "providers": { "a:b": { "url": "x" } } })).is_err());
        assert!(parse(&json!({ "providers": { "x": {} } })).is_err());
        Ok(())
    }
}
