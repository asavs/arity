//! One OpenAI-compatible provider, ready to call: its URL, its key, the headers
//! it asks for, and where its usage is recorded. Chatting, listing models and
//! checking the key all build their requests here, so each provider's rules
//! live in one place.

use std::path::{Path, PathBuf};
use std::time::Duration;

use anyhow::{Result, anyhow, bail};
use reqwest::blocking::{Client, RequestBuilder, Response};
use sha2::Digest;

use crate::{config, keys, usage};

pub struct Endpoint {
    pub provider: String,
    pub base: String,
    pub key: Option<String>,
    pub headers: Vec<(String, String)>,
    /// The data folder, when the provider reports usage on its replies.
    usage_dir: Option<PathBuf>,
}

impl Endpoint {
    /// The provider named in config.json. `conversation` fills its session header, if it has one.
    pub fn open(data_dir: &Path, provider: &str, conversation: &str) -> Result<Self> {
        let providers = config::load(data_dir)?.providers;
        let Some(p) = providers.into_iter().find(|p| p.name == provider) else {
            let names: Vec<String> = config::load(data_dir)?.providers.into_iter().map(|p| p.name).collect();
            bail!("unknown provider {provider}; config.json has {} and codex", names.join(", "));
        };
        let key = keys::get(data_dir, &p.key_name);
        if p.needs_key && key.is_none() {
            bail!("{provider} has no API key; run `arity key set {}` or add one in Settings", p.key_name);
        }
        // Stable for the conversation, and says nothing about it.
        let session: String = sha2::Sha256::digest(conversation.as_bytes())[..16].iter().map(|b| format!("{b:02x}")).collect();
        Ok(Endpoint {
            headers: p.session_header.into_iter().map(|h| (h, session.clone())).collect(),
            usage_dir: p.usage_headers.then(|| data_dir.to_path_buf()),
            provider: p.name,
            base: p.url,
            key,
        })
    }

    /// An endpoint that is not in config.json, such as the ChatGPT sign-in's.
    pub fn bare(provider: &str, base: &str, key: Option<String>) -> Self {
        Endpoint { provider: provider.into(), base: base.into(), key, headers: vec![], usage_dir: None }
    }

    pub fn get(&self, client: &Client, path: &str) -> RequestBuilder {
        self.dress(client.get(format!("{}{path}", self.base)))
    }

    pub fn post(&self, client: &Client, path: &str) -> RequestBuilder {
        self.dress(client.post(format!("{}{path}", self.base)))
    }

    fn dress(&self, mut req: RequestBuilder) -> RequestBuilder {
        if let Some(key) = &self.key {
            req = req.bearer_auth(key);
        }
        for (name, value) in &self.headers {
            req = req.header(name, value);
        }
        req
    }

    /// Note what a reply says about usage, then hand it back.
    pub fn observe(&self, resp: Response) -> Response {
        if let Some(dir) = &self.usage_dir {
            usage::record(dir, &self.provider, resp.headers());
        }
        resp
    }

    /// An error with the key cut out, in case a server echoed it back. Errors
    /// reach the journal and the screen; keys must not.
    pub fn redact(&self, e: anyhow::Error) -> anyhow::Error {
        match &self.key {
            Some(k) if format!("{e:#}").contains(k.as_str()) => anyhow!("{}", format!("{e:#}").replace(k.as_str(), "[key]")),
            _ => e,
        }
    }

    /// The model ids from `GET /models`, or None if there is no such list.
    /// With a ChatGPT sign-in the list is `models`, with `slug`s, and only those
    /// marked `list` are meant to be offered.
    pub fn models(&self) -> Option<Vec<String>> {
        let v: serde_json::Value = self.get(&quick(), "/models").send().ok()?.error_for_status().ok()?.json().ok()?;
        let mut ids: Vec<String> = match v["data"].as_array() {
            Some(data) => data.iter().filter_map(|m| m["id"].as_str().map(String::from)).collect(),
            None => v["models"].as_array()?.iter().filter(|m| m["visibility"] == "list").filter_map(|m| m["slug"].as_str().map(String::from)).collect(),
        };
        ids.sort();
        Some(ids)
    }
}

/// A client for short calls: model lists and key checks.
pub fn quick() -> Client {
    Client::builder().user_agent(crate::model::USER_AGENT).timeout(Duration::from_secs(10)).build().expect("http client")
}
