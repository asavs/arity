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
    /// The provider named in config.json. `session_id` fills its session header, if it has one.
    pub fn open(data_dir: &Path, provider: &str, session_id: &str) -> Result<Self> {
        let providers = config::load(data_dir)?.providers;
        let Some(p) = providers.into_iter().find(|p| p.name == provider) else {
            let names: Vec<String> = config::load(data_dir)?.providers.into_iter().map(|p| p.name).collect();
            bail!("unknown provider {provider}; config.json has {} and codex", names.join(", "));
        };
        let key = keys::get(data_dir, &p.key_name);
        if p.needs_key && key.is_none() {
            bail!("{provider} has no API key; run `arity key set {}` or add one in Settings", p.key_name);
        }
        // Pass persisted 32-hex session IDs through unchanged; hash arbitrary names for backward compatibility.
        let session: String = if is_hex_session_id(session_id) {
            session_id.to_string()
        } else {
            sha2::Sha256::digest(session_id.as_bytes())[..16].iter().map(|b| format!("{b:02x}")).collect()
        };
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

fn is_hex_session_id(s: &str) -> bool {
    s.len() == 32 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn persisted_hex_session_id_passes_through_and_arbitrary_names_hash() -> Result<()> {
        let dir = std::env::temp_dir().join(format!("arity-endpoint-sid-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir)?;
        std::fs::write(dir.join("config.json"), json!({ "workers": [], "providers": {} }).to_string())?;
        crate::keys::set(&dir, "opencode", "sk-oc")?;

        let persisted = "0123456789abcdef0123456789abcdef";
        let ep_persisted = Endpoint::open(&dir, "opencode-go", persisted)?;
        assert_eq!(ep_persisted.headers, vec![("x-opencode-session".into(), persisted.into())]);

        let ep_name = Endpoint::open(&dir, "opencode-go", "chat-1")?;
        assert_eq!(ep_name.headers.len(), 1);
        assert_eq!(ep_name.headers[0].0, "x-opencode-session");
        assert!(is_hex_session_id(&ep_name.headers[0].1));
        assert_ne!(ep_name.headers[0].1, "chat-1");
        assert!(!ep_name.headers[0].1.contains("chat"));

        // Uppercase or wrong length still hashes instead of passing through raw.
        let uppercase = "0123456789ABCDEF0123456789ABCDEF";
        let ep_upper = Endpoint::open(&dir, "opencode-go", uppercase)?;
        assert!(is_hex_session_id(&ep_upper.headers[0].1));
        assert_ne!(ep_upper.headers[0].1, uppercase);

        let _ = std::fs::remove_dir_all(&dir);
        Ok(())
    }
}
