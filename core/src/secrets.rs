//! 0600 secrets (`keys.json`, `codex-auth.json` + `codex-auth.lock`) and rate-limit header parsing.

use std::io::Write;
use std::path::Path;

use anyhow::Result;

/// Write bytes atomically to `path` with mode `0600` on Unix.
/// Creates the temp file with `0600` permissions before writing any secret data,
/// then atomically renames it into place.
fn write_private_atomic(path: &Path, bytes: &[u8]) -> Result<()> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let file_name = path
        .file_name()
        .and_then(|n| n.to_str())
        .unwrap_or("secret");
    let nonce: u64 = rand::random();
    let tmp = path.with_file_name(format!(".{file_name}.{}.{nonce:016x}.tmp", std::process::id()));

    let write_res = (|| -> Result<()> {
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        let mut file = opts.open(&tmp)?;
        file.write_all(bytes)?;
        file.sync_all()?;
        Ok(())
    })();

    if let Err(err) = write_res {
        let _ = std::fs::remove_file(&tmp);
        return Err(err);
    }

    if let Err(err) = std::fs::rename(&tmp, path) {
        let _ = std::fs::remove_file(&tmp);
        return Err(err.into());
    }

    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }

    Ok(())
}

fn percent_encode(s: &str, uppercase: bool) -> String {
    let mut out = String::with_capacity(s.len());
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char);
            }
            _ if uppercase => out.push_str(&format!("%{b:02X}")),
            _ => out.push_str(&format!("%{b:02x}")),
        }
    }
    out
}

pub mod keys {
    //! API keys, one per provider/key-name, stored in `keys.json` (mode `0600`).

    use std::collections::BTreeMap;
    use std::path::Path;

    use anyhow::{Result, bail};

    use super::{percent_encode, write_private_atomic};

    fn read_map(data_dir: &Path) -> BTreeMap<String, String> {
        std::fs::read_to_string(data_dir.join("keys.json"))
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_default()
    }

    pub fn get(data_dir: &Path, key_name: &str) -> Option<String> {
        read_map(data_dir).remove(key_name).filter(|k| !k.is_empty())
    }

    /// Store or remove an API key in `keys.json` (mode `0600`, atomic temp write + rename).
    /// An empty key deletes the entry; internal whitespace is rejected.
    pub fn set(data_dir: &Path, key_name: &str, key: &str) -> Result<()> {
        let key = key.trim();
        if key.chars().any(char::is_whitespace) {
            bail!("a key must not contain spaces or line breaks");
        }
        let mut keys = read_map(data_dir);
        if key.is_empty() {
            keys.remove(key_name);
        } else {
            keys.insert(key_name.to_string(), key.to_string());
        }
        let payload = serde_json::to_string_pretty(&keys)?;
        write_private_atomic(&data_dir.join("keys.json"), payload.as_bytes())
    }

    /// Replace raw and URL-encoded occurrences of `secret` with `[redacted]`.
    pub fn redact(msg: &str, secret: Option<&str>) -> String {
        let Some(secret) = secret.filter(|s| !s.is_empty()) else {
            return msg.to_string();
        };
        let mut out = msg.replace(secret, "[redacted]");
        let enc_upper = percent_encode(secret, true);
        if enc_upper != secret {
            out = out.replace(&enc_upper, "[redacted]");
        }
        let enc_lower = percent_encode(secret, false);
        if enc_lower != secret && enc_lower != enc_upper {
            out = out.replace(&enc_lower, "[redacted]");
        }
        out
    }
}

pub mod codex_auth {
    //! Sign in with ChatGPT (SIWC) PKCE OAuth for open-source and locally hosted apps:
    //! https://developers.openai.com/siwc/token-sharing-open-source
    //!
    //! Tokens stay in `codex-auth.json` (mode `0600`) with exclusive refresh locking
    //! via `codex-auth.lock`.

    use std::io::{BufRead, BufReader, Write};
    use std::net::TcpListener;
    use std::path::{Path, PathBuf};
    use std::time::{Duration, SystemTime, UNIX_EPOCH};

    use anyhow::{Context, Result, anyhow, bail, ensure};
    use base64::Engine;
    use base64::engine::general_purpose::URL_SAFE_NO_PAD as B64;
    use serde_json::{Value, json};
    use sha2::{Digest, Sha256};

    use super::{percent_encode, write_private_atomic};

    const REGISTER: &str = "dynamic_agent_client";
    const AUTHORIZE_URL: &str = "https://auth.openai.com/api/accounts/authorize";
    const TOKEN_URL: &str = "https://auth.openai.com/api/accounts/oauth/token";
    const REDIRECT_URI: &str = "http://127.0.0.1:1455/auth/callback";
    const SHARING: &str = "chatgpt.tokens.use.direct";
    pub const RESOURCE: &str = "https://api.openai.com/v1";

    const AUTH_FILE: &str = "codex-auth.json";
    const LOCK_FILE: &str = "codex-auth.lock";
    const NOT_SIGNED_IN: &str =
        "not signed in to ChatGPT; run `arity login` or use Sign in with ChatGPT";

    fn auth_path(data_dir: &Path) -> PathBuf {
        data_dir.join(AUTH_FILE)
    }

    fn lock_path(data_dir: &Path) -> PathBuf {
        data_dir.join(LOCK_FILE)
    }

    fn now_secs() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs()
    }

    fn read_auth(data_dir: &Path) -> Value {
        std::fs::read_to_string(auth_path(data_dir))
            .ok()
            .and_then(|s| serde_json::from_str(&s).ok())
            .unwrap_or_else(|| json!({}))
    }

    fn write_auth(data_dir: &Path, v: &Value) -> Result<()> {
        let payload = serde_json::to_string_pretty(v)?;
        write_private_atomic(&auth_path(data_dir), payload.as_bytes())
    }

    /// Whether the user is signed in with a dynamic agent client id and refresh token.
    pub fn signed_in(data_dir: &Path) -> bool {
        let saved = read_auth(data_dir);
        saved["client_id"].is_string() && saved["refresh_token"].is_string()
    }

    /// The signed-in account's email/label, if signed in.
    pub fn account(data_dir: &Path) -> Option<String> {
        signed_in(data_dir).then(|| {
            read_auth(data_dir)["email"]
                .as_str()
                .unwrap_or("signed in")
                .to_string()
        })
    }

    /// Opens the browser and waits for the ChatGPT SIWC PKCE sign-in. Returns the account label.
    pub fn login(data_dir: &Path) -> Result<String> {
        let mut saved = read_auth(data_dir);
        if !saved["client_id"].is_string() {
            // Discard tokens from older non-SIWC clients.
            saved = json!({});
        }
        if !saved["host_id"].is_string() {
            let b: [u8; 16] = rand::random();
            let hex: String = b.iter().map(|x| format!("{x:02x}")).collect();
            saved["host_id"] = json!(format!(
                "urn:uuid:{}-{}-4{}-a{}-{}",
                &hex[..8],
                &hex[8..12],
                &hex[13..16],
                &hex[17..20],
                &hex[20..]
            ));
            write_auth(data_dir, &saved)?;
        }

        let client = saved["client_id"].as_str().map(String::from);
        let verifier = B64.encode(rand::random::<[u8; 32]>());
        let challenge = B64.encode(Sha256::digest(verifier.as_bytes()));
        let state = B64.encode(rand::random::<[u8; 16]>());
        let nonce = B64.encode(rand::random::<[u8; 16]>());

        let mut params = vec![
            ("client_id", client.clone().unwrap_or_else(|| REGISTER.into())),
            (
                "ext_agent_host_id",
                saved["host_id"].as_str().unwrap_or_default().into(),
            ),
            ("response_type", "code".into()),
            ("redirect_uri", REDIRECT_URI.into()),
            (
                "scope",
                format!("openid profile email offline_access resource.invoke {SHARING}"),
            ),
            ("resource", RESOURCE.into()),
            ("state", state.clone()),
            ("nonce", nonce.clone()),
            ("code_challenge_method", "S256".into()),
            ("code_challenge", challenge),
        ];
        if client.is_none() {
            params.push(("agent_name_hint", "Arity".into()));
        }
        let url = format!("{AUTHORIZE_URL}?{}", form(&params));

        let listener = TcpListener::bind("127.0.0.1:1455")
            .context("port 1455 is busy (is another app signing in to ChatGPT?)")?;
        println!("Sign in with ChatGPT in your browser. If it does not open, visit:\n\n{url}\n");
        let _ = std::process::Command::new("open").arg(&url).status();

        let query = serve(listener)?;
        let param = |name: &str| {
            query
                .split('&')
                .find_map(|kv| kv.strip_prefix(&format!("{name}=")))
                .map(dec)
        };
        ensure!(
            param("state").as_deref() == Some(state.as_str()),
            "state mismatch; ignoring this sign-in"
        );
        if let Some(e) = param("error") {
            bail!(
                "ChatGPT sign-in failed: {}",
                param("error_description").unwrap_or(e)
            );
        }
        let issued = match (client, param("client_id")) {
            (Some(saved), Some(got)) if saved != got => {
                bail!("OpenAI returned a different client id; sign-in ignored")
            }
            (Some(saved), _) => saved,
            (None, Some(got)) if got != REGISTER => got,
            (None, _) => bail!("OpenAI did not issue a client id"),
        };
        let code = param("code").ok_or_else(|| anyhow!("no authorization code"))?;
        let tokens = post_form(&[
            ("grant_type", "authorization_code".into()),
            ("client_id", issued.clone()),
            ("code", code),
            ("code_verifier", verifier),
            ("redirect_uri", REDIRECT_URI.into()),
            ("resource", RESOURCE.into()),
        ])?;
        ensure!(
            tokens["scope"]
                .as_str()
                .is_some_and(|s| s.split(' ').any(|x| x == SHARING)),
            "the sign-in finished without allowing Arity to use your ChatGPT plan; sign in again and allow it"
        );

        let id = claims(tokens["id_token"].as_str().unwrap_or_default())
            .ok_or_else(|| anyhow!("no ID token"))?;
        let aud_ok = id["aud"].as_str() == Some(issued.as_str())
            || id["aud"]
                .as_array()
                .is_some_and(|a| a.iter().any(|x| x == &json!(issued)));
        ensure!(
            aud_ok && id["nonce"].as_str() == Some(nonce.as_str()),
            "the ID token is not for this sign-in"
        );
        let who = id["email"]
            .as_str()
            .or(id["sub"].as_str())
            .unwrap_or("your account")
            .to_string();
        saved["client_id"] = json!(issued);
        saved["email"] = json!(who);
        save(data_dir, saved, &tokens)?;
        Ok(who)
    }

    /// Forget tokens while preserving `client_id` and `host_id` so re-signing-in reuses registration.
    pub fn logout(data_dir: &Path) -> Result<()> {
        if !auth_path(data_dir).exists() {
            return Ok(());
        }
        let mut saved = read_auth(data_dir);
        if let Some(o) = saved.as_object_mut() {
            for k in ["access_token", "refresh_token", "expires_at", "email"] {
                o.remove(k);
            }
        }
        write_auth(data_dir, &saved)
    }

    /// Cancel an in-progress local OAuth callback listener.
    pub fn cancel() {
        if let Ok(mut s) = std::net::TcpStream::connect("127.0.0.1:1455") {
            let _ = s.write_all(b"GET /cancel HTTP/1.1\r\n\r\n");
        }
    }

    /// Return `(access_token, account_id)`, refreshing under an exclusive lock on `codex-auth.lock`
    /// if the token expires within 60 seconds.
    pub fn access(data_dir: &Path) -> Result<(String, String)> {
        if !auth_path(data_dir).exists() {
            bail!("{NOT_SIGNED_IN}");
        }
        let mut lock_opts = std::fs::OpenOptions::new();
        lock_opts.create(true).write(true).truncate(false);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            lock_opts.mode(0o600);
        }
        let lock = lock_opts.open(lock_path(data_dir)).context(NOT_SIGNED_IN)?;
        lock.lock()?;

        let saved = read_auth(data_dir);
        let client = saved["client_id"]
            .as_str()
            .ok_or_else(|| anyhow!("{NOT_SIGNED_IN}"))?
            .to_string();

        let saved = if saved["expires_at"].as_u64().unwrap_or(0) <= now_secs() + 60 {
            let refresh = saved["refresh_token"]
                .as_str()
                .ok_or_else(|| anyhow!("{NOT_SIGNED_IN}"))?
                .to_string();
            let fresh = post_form(&[
                ("grant_type", "refresh_token".into()),
                ("client_id", client),
                ("refresh_token", refresh),
                ("resource", RESOURCE.into()),
            ])
            .context("ChatGPT sign-in expired; sign in again")?;
            save(data_dir, saved, &fresh)?
        } else {
            saved
        };

        let token = saved["access_token"]
            .as_str()
            .ok_or_else(|| anyhow!("{NOT_SIGNED_IN}"))?
            .to_string();
        let account = claims(&token)
            .and_then(|c| {
                c["https://api.openai.com/auth"]["chatgpt_account_id"]
                    .as_str()
                    .map(String::from)
            })
            .unwrap_or_default();
        Ok((token, account))
    }

    fn save(data_dir: &Path, mut saved: Value, tokens: &Value) -> Result<Value> {
        ensure!(
            tokens["access_token"].is_string(),
            "token endpoint response missing access_token"
        );
        saved["access_token"] = tokens["access_token"].clone();
        if tokens["refresh_token"].is_string() {
            saved["refresh_token"] = tokens["refresh_token"].clone();
        }
        saved["expires_at"] = json!(now_secs() + tokens["expires_in"].as_u64().unwrap_or(3600));
        write_auth(data_dir, &saved)?;
        Ok(saved)
    }

    fn form(pairs: &[(&str, String)]) -> String {
        pairs
            .iter()
            .map(|(k, v)| format!("{k}={}", percent_encode(v, true)))
            .collect::<Vec<_>>()
            .join("&")
    }

    fn post_form(pairs: &[(&str, String)]) -> Result<Value> {
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(30))
            .user_agent(concat!("Arity/", env!("CARGO_PKG_VERSION")))
            .build()?;
        let resp = client
            .post(TOKEN_URL)
            .header("content-type", "application/x-www-form-urlencoded")
            .body(form(pairs))
            .send()?;
        let status = resp.status();
        let body = resp.text().unwrap_or_default();
        let v: Value = serde_json::from_str(&body).unwrap_or_else(|_| json!({}));
        if !status.is_success() {
            bail!(
                "token endpoint {status}: {} {}",
                v["error"].as_str().unwrap_or("unknown_error"),
                v["error_description"].as_str().unwrap_or_default()
            );
        }
        Ok(v)
    }

    fn claims(token: &str) -> Option<Value> {
        let payload = token.split('.').nth(1)?;
        serde_json::from_slice(&B64.decode(payload.trim_end_matches('=')).ok()?).ok()
    }

    fn serve(listener: TcpListener) -> Result<String> {
        for stream in listener.incoming().flatten() {
            let mut stream = stream;
            let mut line = String::new();
            if BufReader::new(&stream).read_line(&mut line).is_err() {
                continue;
            }
            let target = line
                .split_whitespace()
                .nth(1)
                .unwrap_or_default()
                .to_string();
            if target == "/cancel" && stream.peer_addr().is_ok_and(|a| a.ip().is_loopback()) {
                bail!("sign-in cancelled");
            }
            let Some(query) = target.strip_prefix("/auth/callback?") else {
                let _ = stream.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
                continue;
            };
            let page = "Arity: ChatGPT sign-in received. You can close this tab.";
            let _ = write!(
                stream,
                "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {}\r\n\r\n{page}",
                page.len()
            );
            return Ok(query.to_string());
        }
        bail!("the sign-in listener stopped")
    }

    fn dec(s: &str) -> String {
        let bytes = s.as_bytes();
        let mut out = Vec::with_capacity(bytes.len());
        let mut i = 0;
        while i < bytes.len() {
            match bytes[i] {
                b'%' if i + 2 < bytes.len() => {
                    out.push(u8::from_str_radix(&s[i + 1..i + 3], 16).unwrap_or(b'?'));
                    i += 3;
                }
                b'+' => {
                    out.push(b' ');
                    i += 1;
                }
                b => {
                    out.push(b);
                    i += 1;
                }
            }
        }
        String::from_utf8_lossy(&out).into_owned()
    }
}

pub mod rate_limits {
    //! Parse `x-ratelimit-{limit,remaining,reset}-<name>` HTTP headers into `RateLimitRecord`s.

    use crate::types::RateLimitRecord;

    /// Parse provider rate-limit headers.
    /// Supports both numeric seconds (e.g. `59.2`) and Go-style durations (e.g. `26m14.9s`, `1h2m3s`, `500ms`).
    pub fn parse_headers<'a>(
        provider: &str,
        headers: impl Iterator<Item = (&'a str, &'a str)>,
        observed_ms: i64,
    ) -> Vec<RateLimitRecord> {
        let headers: Vec<(String, &str)> = headers
            .map(|(k, v)| (k.to_ascii_lowercase(), v.trim()))
            .collect();
        let find = |kind: &str, name: &str| {
            let needle = format!("x-ratelimit-{kind}-{name}");
            headers
                .iter()
                .find(|(k, _)| *k == needle)
                .map(|(_, v)| *v)
        };

        let mut out: Vec<RateLimitRecord> = headers
            .iter()
            .filter_map(|(k, v)| {
                let name = k.strip_prefix("x-ratelimit-limit-")?;
                if name.is_empty() {
                    return None;
                }
                let limit_val: u64 = v.parse().ok()?;
                let remaining: u64 = find("remaining", name)?.parse().ok()?;
                let reset_secs = find("reset", name).and_then(parse_reset_secs);
                Some(RateLimitRecord {
                    provider: provider.to_string(),
                    limit_name: name.to_string(),
                    limit_val,
                    remaining,
                    reset_secs,
                    observed_ms,
                })
            })
            .collect();

        out.sort_by(|a, b| a.limit_name.cmp(&b.limit_name));
        out.dedup_by(|a, b| a.limit_name == b.limit_name);
        out
    }

    /// Parse `"59.2"`, `"6m0s"`, `"26m14.9s"`, `"1h2m3s"`, or `"500ms"` as whole seconds rounded up.
    fn parse_reset_secs(raw: &str) -> Option<u64> {
        let s = raw.trim();
        if s.is_empty() {
            return None;
        }
        if let Ok(n) = s.parse::<f64>() {
            return (n.is_finite() && n >= 0.0).then_some(n.ceil() as u64);
        }

        let bytes = s.as_bytes();
        let mut i = 0;
        let mut total = 0.0f64;
        let mut num = String::new();
        let mut saw_unit = false;

        while i < bytes.len() {
            let b = bytes[i];
            match b {
                b'0'..=b'9' | b'.' => {
                    num.push(b as char);
                    i += 1;
                }
                b'm' if i + 1 < bytes.len() && bytes[i + 1] == b's' => {
                    let n: f64 = std::mem::take(&mut num).parse().ok()?;
                    if !n.is_finite() || n < 0.0 {
                        return None;
                    }
                    total += n * 0.001;
                    saw_unit = true;
                    i += 2;
                }
                b'h' | b'm' | b's' => {
                    let n: f64 = std::mem::take(&mut num).parse().ok()?;
                    if !n.is_finite() || n < 0.0 {
                        return None;
                    }
                    let mult = match b {
                        b'h' => 3600.0,
                        b'm' => 60.0,
                        _ => 1.0,
                    };
                    total += n * mult;
                    saw_unit = true;
                    i += 1;
                }
                _ => return None,
            }
        }

        (saw_unit && num.is_empty() && total.is_finite()).then_some(total.ceil() as u64)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::RateLimitRecord;
    use base64::Engine;
    use base64::engine::general_purpose::URL_SAFE_NO_PAD as B64;
    use serde_json::json;

    fn temp_dir(label: &str) -> std::path::PathBuf {
        let nonce: u64 = rand::random();
        let dir = std::env::temp_dir().join(format!(
            "arity-secrets-{label}-{}-{nonce:08x}",
            std::process::id()
        ));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn keys_private_atomic_set_get_delete_and_reject_whitespace() -> Result<()> {
        let dir = temp_dir("keys");
        keys::set(&dir, "nous", "  sk-test-123 \n")?;
        assert_eq!(keys::get(&dir, "nous").as_deref(), Some("sk-test-123"));

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(dir.join("keys.json"))?
                .permissions()
                .mode()
                & 0o777;
            assert_eq!(mode, 0o600);
        }

        assert!(keys::set(&dir, "nous", "sk-bad key").is_err());
        assert!(keys::set(&dir, "nous", "sk-bad\nkey").is_err());
        assert_eq!(keys::get(&dir, "nous").as_deref(), Some("sk-test-123"));

        keys::set(&dir, "nous", "")?;
        assert_eq!(keys::get(&dir, "nous"), None);

        std::fs::remove_dir_all(&dir)?;
        Ok(())
    }

    #[test]
    fn keys_redact_raw_and_url_encoded() {
        let secret = "sk-proj+abc/def=99";
        let msg = "err raw=sk-proj+abc/def=99 upper=sk-proj%2Babc%2Fdef%3D99 lower=sk-proj%2babc%2fdef%3d99";
        let redacted = keys::redact(msg, Some(secret));
        assert!(!redacted.contains("sk-proj"));
        assert_eq!(
            redacted,
            "err raw=[redacted] upper=[redacted] lower=[redacted]"
        );
        assert_eq!(keys::redact("clean msg", None), "clean msg");
        assert_eq!(keys::redact("clean msg", Some("")), "clean msg");
    }

    #[test]
    fn codex_auth_old_tokens_rejected_and_jwt_account_id_extracted() -> Result<()> {
        let dir = temp_dir("codex");
        assert!(!codex_auth::signed_in(&dir));
        assert_eq!(codex_auth::account(&dir), None);
        assert!(codex_auth::access(&dir).is_err());

        let future = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs()
            + 3600;

        // Legacy tokens without client_id are treated as signed out.
        write_private_atomic(
            &dir.join("codex-auth.json"),
            json!({
                "access_token": "a.e30.s",
                "refresh_token": "r1",
                "expires_at": future
            })
            .to_string()
            .as_bytes(),
        )?;
        assert!(!codex_auth::signed_in(&dir));
        assert!(codex_auth::access(&dir).unwrap_err().to_string().contains("arity login"));

        // Valid SIWC auth with chatgpt_account_id inside JWT claims.
        let claims = B64.encode(
            json!({
                "https://api.openai.com/auth": {
                    "chatgpt_account_id": "acct_12345"
                }
            })
            .to_string(),
        );
        let jwt = format!("hdr.{claims}.sig");
        write_private_atomic(
            &dir.join("codex-auth.json"),
            json!({
                "client_id": "oaiapp_test",
                "host_id": "urn:uuid:test-host",
                "email": "asa@example.com",
                "access_token": jwt,
                "refresh_token": "r2",
                "expires_at": future
            })
            .to_string()
            .as_bytes(),
        )?;
        assert!(codex_auth::signed_in(&dir));
        assert_eq!(codex_auth::account(&dir).as_deref(), Some("asa@example.com"));
        let (tok, acc) = codex_auth::access(&dir)?;
        assert_eq!(tok, jwt);
        assert_eq!(acc, "acct_12345");

        // Logout clears tokens while keeping client_id and host_id.
        codex_auth::logout(&dir)?;
        assert!(!codex_auth::signed_in(&dir));
        let remaining: serde_json::Value =
            serde_json::from_str(&std::fs::read_to_string(dir.join("codex-auth.json"))?)?;
        assert_eq!(remaining["client_id"], "oaiapp_test");
        assert_eq!(remaining["host_id"], "urn:uuid:test-host");
        assert!(remaining.get("access_token").is_none());

        std::fs::remove_dir_all(&dir)?;
        Ok(())
    }

    #[test]
    fn rate_limit_headers_parse_seconds_and_durations() {
        let observed_ms = 1_700_000_000_000;
        let records = rate_limits::parse_headers(
            "openai",
            [
                ("X-RateLimit-Limit-Requests", "50"),
                ("x-ratelimit-remaining-requests", "49"),
                ("x-ratelimit-reset-requests", "59.2"),
                ("x-ratelimit-limit-tokens-1h", "6000000"),
                ("x-ratelimit-remaining-tokens-1h", "5999968"),
                ("x-ratelimit-reset-tokens-1h", "26m14.9s"),
                ("x-ratelimit-limit-daily", "1000"),
                ("x-ratelimit-remaining-daily", "900"),
                ("x-ratelimit-reset-daily", "1h2m3s"),
                ("x-ratelimit-limit-burst", "10"),
                ("x-ratelimit-remaining-burst", "9"),
                ("x-ratelimit-reset-burst", "500ms"),
                ("x-ratelimit-limit-odd", "7"), // missing remaining
                ("content-type", "text/event-stream"),
            ]
            .into_iter(),
            observed_ms,
        );

        assert_eq!(
            records,
            vec![
                RateLimitRecord {
                    provider: "openai".into(),
                    limit_name: "burst".into(),
                    limit_val: 10,
                    remaining: 9,
                    reset_secs: Some(1),
                    observed_ms,
                },
                RateLimitRecord {
                    provider: "openai".into(),
                    limit_name: "daily".into(),
                    limit_val: 1000,
                    remaining: 900,
                    reset_secs: Some(3723),
                    observed_ms,
                },
                RateLimitRecord {
                    provider: "openai".into(),
                    limit_name: "requests".into(),
                    limit_val: 50,
                    remaining: 49,
                    reset_secs: Some(60),
                    observed_ms,
                },
                RateLimitRecord {
                    provider: "openai".into(),
                    limit_name: "tokens-1h".into(),
                    limit_val: 6000000,
                    remaining: 5999968,
                    reset_secs: Some(1575),
                    observed_ms,
                },
            ]
        );
    }
}
