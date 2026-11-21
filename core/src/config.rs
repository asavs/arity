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
//! endpoint has no `/models` list. `openai`, `nous`, `opencode`, `opencode-go` and `mlx` are there unless overridden,
//! field by field. `account` groups providers that share a key into one account
//! with several plans, as OpenCode's Zen and Go do. `key` may
//! name another provider whose key is shared; `session_header` names a header
//! that carries a stable id per conversation.

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
    /// Whose key it uses: its own name, or another provider's when they share one.
    pub key_name: String,
    /// A header that carries a stable id per conversation, for providers that ask for one.
    pub session_header: Option<String>,
    pub models: Vec<String>,
    /// One of the providers Arity comes with; it can be changed but not removed.
    pub builtin: bool,
    /// The account it belongs to, which holds the key: `opencode` for both Zen and Go.
    pub account: String,
    pub account_title: String,
    /// The plan's name within the account, and a line about what it costs.
    pub title: String,
    pub note: String,
    /// Where to get its key, or to subscribe.
    pub page: Option<String>,
}

const DEFAULT_WORKERS: &[&str] = &[
    "mlx:mlx-community/Qwen3.5-4B-MLX-4bit",
    "mlx:mlx-community/gemma-4-e4b-it-4bit",
    "codex:gpt-6.1-terra",
];

fn default_providers() -> Value {
    json!({
        "openai": { "url": "https://api.openai.com/v1", "key": true,
                    "account": "openai", "account_title": "OpenAI", "title": "API", "note": "Pay per token",
                    "page": "https://platform.openai.com/api-keys" },
        // API key from portal.nousresearch.com; see plans/v0.0.1.md.
        "nous": { "url": "https://inference-api.nousresearch.com/v1", "key": true,
                  "models": ["Hermes-4-70B", "Hermes-4-405B", "Hermes-4.3-36B"],
                  "account": "nous", "account_title": "Nous Portal", "title": "Inference",
                  "note": "Free models on a subscription, credits for the rest", "page": "https://portal.nousresearch.com" },
        // OpenCode Zen, now "Console": API key from opencode.ai; every text model also speaks chat completions.
        "opencode": { "url": "https://opencode.ai/inference/openai/v1", "key": true,
                      "account": "opencode", "account_title": "OpenCode", "title": "Zen", "note": "Pay per request",
                      "page": "https://opencode.ai" },
        // The OpenCode Go subscription, on the same key. Go asks other clients for a session id per conversation.
        "opencode-go": { "url": "https://opencode.ai/inference/go/openai/v1", "key": "opencode",
                         "session_header": "x-opencode-session",
                         "account": "opencode", "account_title": "OpenCode", "title": "Go", "note": "Subscription",
                         "page": "https://opencode.ai/go" },
        "mlx": { "url": "http://127.0.0.1:8080/v1",
                 "account": "mac", "account_title": "This Mac", "title": "MLX", "note": "mlx_vlm.server on port 8080" },
    })
}

/// The built-in accounts in the order they are shown; ones the user adds follow.
pub const ACCOUNT_ORDER: &[&str] = &["openai", "opencode", "nous", "mac"];

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
    let defaults = default_providers();
    let mut providers = defaults.clone();
    // The user's fields override the defaults one by one, so changing a URL keeps the rest.
    for (name, p) in v["providers"].as_object().into_iter().flatten() {
        match (providers[name].as_object_mut(), p.as_object()) {
            (Some(base), Some(over)) => base.extend(over.clone()),
            _ => providers[name] = p.clone(),
        }
    }
    let mut out = vec![];
    for (name, p) in providers.as_object().unwrap() {
        anyhow::ensure!(!name.contains(':') && name != "codex", "provider name {name:?} is not allowed");
        let url = p["url"].as_str().with_context(|| format!("provider {name} has no url"))?;
        out.push(Provider {
            name: name.clone(),
            url: url.trim_end_matches('/').into(),
            needs_key: p["key"].as_bool().unwrap_or(false) || p["key"].is_string(),
            key_name: p["key"].as_str().unwrap_or(name).into(),
            session_header: p["session_header"].as_str().map(String::from),
            models: strings(&p["models"]),
            builtin: defaults.get(name).is_some(),
            account: p["account"].as_str().unwrap_or(name).into(),
            account_title: p["account_title"].as_str().unwrap_or(name).into(),
            title: p["title"].as_str().unwrap_or("Chat completions").into(),
            note: p["note"].as_str().unwrap_or(url).into(),
            page: p["page"].as_str().map(String::from),
        });
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(Config { workers: strings(&v["workers"]), providers: out })
}

/// Add an OpenAI-compatible provider, or change one, keeping the rest of the file.
pub fn add_provider(data_dir: &Path, name: &str, url: &str, needs_key: bool) -> Result<()> {
    let (name, url) = (name.trim(), url.trim().trim_end_matches('/'));
    anyhow::ensure!(
        !name.is_empty() && name != "codex" && name.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-'),
        "a provider's name is lowercase letters, digits and dashes, and not codex"
    );
    anyhow::ensure!(url.starts_with("http://") || url.starts_with("https://"), "the URL starts with http:// or https://");
    let mut v = read(data_dir)?;
    if !v["providers"].is_object() {
        v["providers"] = json!({});
    }
    v["providers"][name] = json!({ "url": url, "key": needs_key });
    parse(&v)?;
    std::fs::write(path(data_dir), serde_json::to_string_pretty(&v)?)?;
    Ok(())
}

/// Remove a provider the user added. Built-in ones go back to their defaults.
pub fn remove_provider(data_dir: &Path, name: &str) -> Result<()> {
    let mut v = read(data_dir)?;
    if let Some(p) = v["providers"].as_object_mut() {
        p.remove(name);
    }
    std::fs::write(path(data_dir), serde_json::to_string_pretty(&v)?)?;
    Ok(())
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
        assert_eq!(names, ["groq", "mlx", "nous", "openai", "opencode", "opencode-go"]);
        assert_eq!(c.providers[1].url, "http://h:1/v1");
        assert_eq!(c.providers[1].account_title, "This Mac", "an override keeps the fields it does not name");
        assert!(c.providers[0].needs_key && !c.providers[1].needs_key);
        assert!(parse(&json!({ "providers": { "a:b": { "url": "x" } } })).is_err());
        assert!(parse(&json!({ "providers": { "x": {} } })).is_err());
        assert!(c.providers[2].builtin && !c.providers[0].builtin);
        let go = &c.providers[5];
        assert_eq!((go.account.as_str(), go.title.as_str()), ("opencode", "Go"));
        assert!(go.needs_key && go.key_name == "opencode" && go.session_header.is_some());
        Ok(())
    }

    #[test]
    fn providers_are_added_and_removed() -> Result<()> {
        let d = std::env::temp_dir().join(format!("arity-config-{}", std::process::id()));
        add_provider(&d, "groq", "https://api.groq.com/openai/v1/", true)?;
        let g = load(&d)?.providers.into_iter().find(|p| p.name == "groq").unwrap();
        assert_eq!((g.url.as_str(), g.needs_key, g.builtin), ("https://api.groq.com/openai/v1", true, false));
        assert!(add_provider(&d, "Bad Name", "https://x", false).is_err());
        assert!(add_provider(&d, "codex", "https://x", false).is_err());
        assert!(add_provider(&d, "x", "ftp://x", false).is_err());
        remove_provider(&d, "groq")?;
        assert!(!load(&d)?.providers.iter().any(|p| p.name == "groq"));
        assert!(!load(&d)?.workers.is_empty(), "the rest of the file is kept");
        std::fs::remove_dir_all(&d)?;
        Ok(())
    }
}
