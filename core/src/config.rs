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
//! that carries a stable id per conversation. `"usage": "headers"` says it reports
//! its limits on its replies. `"billing": "subscription"` marks a flat-fee plan,
//! whose models count as free while it has quota left; otherwise a keyed provider
//! is pay per token. `free_suffix` marks models that are free on the provider's
//! subscription, such as Nous's `:free` ones. A provider on this computer
//! (localhost) is local.

use std::path::Path;

use anyhow::{Context, Result};
use serde_json::{Value, json};

#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
pub enum HookEvent {
    BeforeTool,
    AfterTool,
    OnYield,
    AfterTurn,
}

impl HookEvent {
    pub fn as_str(self) -> &'static str {
        match self {
            HookEvent::BeforeTool => "before_tool",
            HookEvent::AfterTool => "after_tool",
            HookEvent::OnYield => "on_yield",
            HookEvent::AfterTurn => "after_turn",
        }
    }

    pub fn parse(s: &str) -> Option<Self> {
        match s.trim() {
            "before_tool" => Some(HookEvent::BeforeTool),
            "after_tool" => Some(HookEvent::AfterTool),
            "on_yield" => Some(HookEvent::OnYield),
            "after_turn" => Some(HookEvent::AfterTurn),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, uniffi::Record)]
pub struct HookConfig {
    pub name: String,
    pub event: HookEvent,
    /// Optional tool filter: None / "" / "*" matches all tools, or comma-separated tool names (e.g. "edit_file,write_file").
    pub tool: Option<String>,
    /// Shell command executed with JSON hook context on stdin.
    pub command: String,
    pub enabled: bool,
}

pub struct Config {
    /// Models a task for an agent may be given, picked blind and uniformly.
    pub workers: Vec<String>,
    /// Sorted by name.
    pub providers: Vec<Provider>,
    /// User-configured shell hooks.
    pub hooks: Vec<HookConfig>,
    /// Compact a conversation before a model call once its last prompt reached this many tokens
    /// (`"auto_compact_tokens"`; `null` or `0` turns it off).
    pub auto_compact_tokens: Option<u64>,
}

pub const DEFAULT_AUTO_COMPACT_TOKENS: u64 = 120_000;

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
    /// It reports its limits as `x-ratelimit-*` headers on its replies (`"usage": "headers"`).
    pub usage_headers: bool,
    /// A flat-fee plan (`"billing": "subscription"`), rather than pay per token.
    pub subscription: bool,
    /// Models whose id ends with this are free on the subscription (`"free_suffix": ":free"`).
    pub free_suffix: Option<String>,
}

impl Provider {
    /// Runs on this computer.
    pub fn local(&self) -> bool {
        let host = self.url.split("://").nth(1).unwrap_or("").split(['/', ':']).next().unwrap_or("");
        let host = if self.url.contains("://[") { "::1" } else { host };
        matches!(host, "127.0.0.1" | "localhost" | "::1") || host.ends_with(".localhost")
    }
}

const DEFAULT_WORKERS: &[&str] = &[
    "mlx:mlx-community/Qwen3.5-4B-MLX-4bit",
    "mlx:mlx-community/gemma-4-e4b-it-4bit",
    "codex:gpt-6.1-terra",
];

fn default_providers() -> Value {
    json!({
        "openai": { "url": "https://api.openai.com/v1", "key": true,
                    "account": "openai", "account_title": "OpenAI", "title": "API", "note": "Pay per token", "usage": "headers",
                    "page": "https://platform.openai.com/api-keys" },
        // API key from portal.nousresearch.com; see plans/v0.0.1.md.
        "nous": { "url": "https://inference-api.nousresearch.com/v1", "key": true,
                  "models": ["Hermes-4-70B", "Hermes-4-405B", "Hermes-4.3-36B"],
                  "account": "nous", "account_title": "Nous Portal", "title": "Inference", "usage": "headers", "free_suffix": ":free",
                  "note": "Free models on a subscription, credits for the rest", "page": "https://portal.nousresearch.com" },
        // OpenCode Zen, now "Console": API key from opencode.ai; every text model also speaks chat completions.
        "opencode": { "url": "https://opencode.ai/inference/openai/v1", "key": true,
                      "account": "opencode", "account_title": "OpenCode", "title": "Zen", "note": "Pay per request",
                      "page": "https://opencode.ai" },
        // The OpenCode Go subscription, on the same key. Go asks other clients for a session id per conversation.
        "opencode-go": { "url": "https://opencode.ai/inference/go/openai/v1", "key": "opencode",
                         "session_header": "x-opencode-session",
                         "account": "opencode", "account_title": "OpenCode", "title": "Go", "note": "Subscription", "billing": "subscription",
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
            usage_headers: p["usage"] == "headers",
            subscription: p["billing"] == "subscription",
            free_suffix: p["free_suffix"].as_str().map(String::from),
        });
    }
    out.sort_by(|a, b| a.name.cmp(&b.name));
    let mut hooks = vec![];
    for h in v["hooks"].as_array().into_iter().flatten() {
        let Some(name) = h["name"].as_str().map(str::trim).filter(|s| !s.is_empty()) else {
            continue;
        };
        let Some(event) = h["event"].as_str().and_then(HookEvent::parse) else {
            continue;
        };
        let Some(command) = h["command"].as_str().map(str::trim).filter(|s| !s.is_empty()) else {
            continue;
        };
        let tool = h["tool"]
            .as_str()
            .map(str::trim)
            .filter(|s| !s.is_empty())
            .map(String::from);
        let enabled = h["enabled"].as_bool().unwrap_or(true);
        hooks.push(HookConfig {
            name: name.into(),
            event,
            tool,
            command: command.into(),
            enabled,
        });
    }
    let auto_compact_tokens = match v.get("auto_compact_tokens") {
        None => Some(DEFAULT_AUTO_COMPACT_TOKENS),
        Some(n) => n.as_u64().filter(|&n| n > 0),
    };
    Ok(Config { workers: strings(&v["workers"]), providers: out, hooks, auto_compact_tokens })
}

fn hook_to_json(h: &HookConfig) -> Value {
    let mut obj = serde_json::Map::new();
    obj.insert("name".into(), json!(h.name));
    obj.insert("event".into(), json!(h.event.as_str()));
    if let Some(tool) = h.tool.as_deref().map(str::trim).filter(|s| !s.is_empty()) {
        obj.insert("tool".into(), json!(tool));
    }
    obj.insert("command".into(), json!(h.command));
    obj.insert("enabled".into(), json!(h.enabled));
    Value::Object(obj)
}

/// Add or update a user hook in `config.json`, keeping the rest of the file.
pub fn save_hook(data_dir: &Path, hook: &HookConfig) -> Result<()> {
    let name = hook.name.trim();
    anyhow::ensure!(
        !name.is_empty() && name.chars().all(|c| c.is_ascii_alphanumeric() || c == '-' || c == '_'),
        "a hook's name is non-empty letters, digits, dashes, or underscores"
    );
    let command = hook.command.trim();
    anyhow::ensure!(!command.is_empty(), "a hook needs a non-empty command");
    let normalized = HookConfig {
        name: name.into(),
        event: hook.event,
        tool: hook.tool.as_deref().map(str::trim).filter(|s| !s.is_empty()).map(String::from),
        command: command.into(),
        enabled: hook.enabled,
    };
    let mut v = read(data_dir)?;
    let mut hooks = parse(&v)?.hooks;
    if let Some(existing) = hooks.iter_mut().find(|h| h.name == name) {
        *existing = normalized;
    } else {
        hooks.push(normalized);
    }
    v["hooks"] = Value::Array(hooks.iter().map(hook_to_json).collect());
    std::fs::write(path(data_dir), serde_json::to_string_pretty(&v)?)?;
    Ok(())
}

/// Remove a user hook by name from `config.json`. Returns whether one was removed.
pub fn remove_hook(data_dir: &Path, name: &str) -> Result<bool> {
    let name = name.trim();
    let mut v = read(data_dir)?;
    let mut hooks = parse(&v)?.hooks;
    let before = hooks.len();
    hooks.retain(|h| h.name != name);
    let removed = hooks.len() < before;
    if removed {
        v["hooks"] = Value::Array(hooks.iter().map(hook_to_json).collect());
        std::fs::write(path(data_dir), serde_json::to_string_pretty(&v)?)?;
    }
    Ok(removed)
}

/// Enable or disable an existing hook in `config.json`. Fails if the hook does not exist.
pub fn set_hook_enabled(data_dir: &Path, name: &str, enabled: bool) -> Result<()> {
    let name = name.trim();
    let mut v = read(data_dir)?;
    let mut hooks = parse(&v)?.hooks;
    let Some(h) = hooks.iter_mut().find(|h| h.name == name) else {
        anyhow::bail!("no hook named {name:?} in config.json");
    };
    h.enabled = enabled;
    v["hooks"] = Value::Array(hooks.iter().map(hook_to_json).collect());
    std::fs::write(path(data_dir), serde_json::to_string_pretty(&v)?)?;
    Ok(())
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

/// Set or disable automatic pre-turn compaction (`auto_compact_tokens`) in `config.json`.
pub fn set_auto_compact_tokens(data_dir: &Path, tokens: Option<u64>) -> Result<()> {
    let value = match tokens {
        None | Some(0) => Value::Null,
        Some(n) => {
            anyhow::ensure!(n >= 4_000, "auto-compaction threshold must be at least 4,000 tokens (or off)");
            json!(n)
        }
    };
    let mut v = read(data_dir)?;
    v["auto_compact_tokens"] = value;
    parse(&v)?;
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
        assert!(go.subscription && !c.providers[4].subscription, "Go is a subscription, Zen is not");
        assert!(!c.providers[1].local(), "mlx moved to another host is not local");
        let defaults = parse(&json!({}))?;
        assert!(defaults.providers.iter().find(|p| p.name == "mlx").unwrap().local());
        assert!(!defaults.providers.iter().find(|p| p.name == "nous").unwrap().local());
        assert_eq!(defaults.auto_compact_tokens, Some(DEFAULT_AUTO_COMPACT_TOKENS));
        assert_eq!(parse(&json!({ "auto_compact_tokens": 50000 }))?.auto_compact_tokens, Some(50000));
        assert_eq!(parse(&json!({ "auto_compact_tokens": null }))?.auto_compact_tokens, None);
        assert_eq!(parse(&json!({ "auto_compact_tokens": 0 }))?.auto_compact_tokens, None);
        Ok(())
    }

    #[test]
    fn auto_compact_tokens_saves_and_disables() -> Result<()> {
        let d = std::env::temp_dir().join(format!("arity-compact-cfg-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);
        assert_eq!(load(&d)?.auto_compact_tokens, Some(DEFAULT_AUTO_COMPACT_TOKENS));
        set_auto_compact_tokens(&d, Some(64_000))?;
        assert_eq!(load(&d)?.auto_compact_tokens, Some(64_000));
        set_auto_compact_tokens(&d, None)?;
        assert_eq!(load(&d)?.auto_compact_tokens, None);
        assert!(set_auto_compact_tokens(&d, Some(500)).is_err());
        let _ = std::fs::remove_dir_all(&d);
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

    #[test]
    fn hooks_are_saved_toggled_and_removed() -> Result<()> {
        let d = std::env::temp_dir().join(format!("arity-config-hooks-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&d);

        // Absent "hooks" key parses as empty; malformed entries are skipped.
        assert!(load(&d)?.hooks.is_empty());
        let parsed = parse(&json!({
            "hooks": [
                { "name": "ok", "event": "after_tool", "command": "echo hi" },
                { "name": "", "event": "after_tool", "command": "echo bad" },
                { "name": "bad_ev", "event": "bogus", "command": "echo bad" },
                { "name": "no_cmd", "event": "before_tool", "command": "   " }
            ]
        }))?;
        assert_eq!(parsed.hooks.len(), 1);
        assert_eq!(parsed.hooks[0].name, "ok");
        assert!(parsed.hooks[0].enabled, "enabled defaults to true");

        // Validation on save.
        assert!(save_hook(&d, &HookConfig {
            name: "bad name".into(),
            event: HookEvent::BeforeTool,
            tool: None,
            command: "true".into(),
            enabled: true,
        }).is_err());
        assert!(save_hook(&d, &HookConfig {
            name: "empty_cmd".into(),
            event: HookEvent::BeforeTool,
            tool: None,
            command: "   ".into(),
            enabled: true,
        }).is_err());

        // Save two hooks and replace the first in-place.
        let h1 = HookConfig {
            name: "fmt_check".into(),
            event: HookEvent::AfterTool,
            tool: Some("edit_file,write_file".into()),
            command: "cargo fmt --check".into(),
            enabled: true,
        };
        let h2 = HookConfig {
            name: "test-gate".into(),
            event: HookEvent::OnYield,
            tool: None,
            command: "cargo test".into(),
            enabled: true,
        };
        save_hook(&d, &h1)?;
        save_hook(&d, &h2)?;
        assert_eq!(load(&d)?.hooks, vec![h1.clone(), h2.clone()]);

        let h1_updated = HookConfig {
            command: "cargo fmt".into(),
            ..h1.clone()
        };
        save_hook(&d, &h1_updated)?;
        assert_eq!(load(&d)?.hooks, vec![h1_updated.clone(), h2.clone()], "replace keeps position");

        // Toggle enabled on/off, and fail on unknown hook.
        set_hook_enabled(&d, "fmt_check", false)?;
        assert!(!load(&d)?.hooks[0].enabled);
        set_hook_enabled(&d, "fmt_check", true)?;
        assert!(load(&d)?.hooks[0].enabled);
        assert!(set_hook_enabled(&d, "missing", false).is_err());

        // Remove returns true when present, false otherwise, keeping the rest of config.json.
        assert!(remove_hook(&d, "fmt_check")?);
        assert!(!remove_hook(&d, "fmt_check")?);
        assert_eq!(load(&d)?.hooks, vec![h2]);
        assert!(!load(&d)?.workers.is_empty(), "workers preserved");
        assert!(!load(&d)?.providers.is_empty(), "providers preserved");

        std::fs::remove_dir_all(&d)?;
        Ok(())
    }
}
