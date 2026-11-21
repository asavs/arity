//! `config.json` in the data folder. Written with defaults on first use, so
//! there is always a file to edit.

use std::path::Path;

use anyhow::{Context, Result};
use serde_json::{Value, json};

pub struct Config {
    /// Models a task for an agent may be given, picked blind and uniformly.
    pub workers: Vec<String>,
}

const DEFAULT_WORKERS: &[&str] = &[
    "mlx:mlx-community/Qwen3.5-4B-MLX-4bit",
    "mlx:mlx-community/gemma-4-e4b-it-4bit",
    "codex:gpt-6.1-terra",
];

pub fn load(data_dir: &Path) -> Result<Config> {
    let path = data_dir.join("config.json");
    if !path.exists() {
        std::fs::create_dir_all(data_dir)?;
        std::fs::write(&path, serde_json::to_string_pretty(&json!({ "workers": DEFAULT_WORKERS }))?)?;
    }
    let v: Value = serde_json::from_str(&std::fs::read_to_string(&path)?)
        .with_context(|| format!("reading {}", path.display()))?;
    let workers = v["workers"]
        .as_array()
        .map(|a| a.iter().filter_map(|m| m.as_str().map(String::from)).collect())
        .unwrap_or_default();
    Ok(Config { workers })
}
