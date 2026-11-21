//! Sign in with ChatGPT, the way OpenAI documents it for open-source and locally
//! hosted apps: https://developers.openai.com/siwc/token-sharing-open-source
//!
//! The first sign-in registers Arity as an agent of the user's own and returns a
//! client id issued to them; later sign-ins reuse it, and the same host id. Tokens
//! are for the public Responses API and draw on the ChatGPT plan's usage. They stay
//! in codex-auth.json in the data folder (mode 0600).

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, anyhow, bail, ensure};
use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD as B64;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

const REGISTER: &str = "dynamic_agent_client";
const AUTHORIZE_URL: &str = "https://auth.openai.com/api/accounts/authorize";
const TOKEN_URL: &str = "https://auth.openai.com/api/accounts/oauth/token";
const REDIRECT_URI: &str = "http://127.0.0.1:1455/auth/callback";
const SHARING: &str = "chatgpt.tokens.use.direct";
pub const RESOURCE: &str = "https://api.openai.com/v1";

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
}

fn read(path: &Path) -> Value {
    std::fs::read_to_string(path).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(json!({}))
}

/// Signed in the current way, with a client id issued to the user.
pub fn signed_in(path: &Path) -> bool {
    let saved = read(path);
    saved["client_id"].is_string() && saved["refresh_token"].is_string()
}

/// Opens the browser and waits for the sign-in. Returns the account's email.
pub fn login(path: &Path) -> Result<String> {
    let mut saved = read(path);
    if !saved["client_id"].is_string() {
        saved = json!({}); // tokens from the old Codex CLI client are no use here
    }
    // The host id names this installation to OpenAI. Kept before the first sign-in, as the docs ask.
    if !saved["host_id"].is_string() {
        let b: [u8; 16] = rand::random();
        let hex: String = b.iter().map(|x| format!("{x:02x}")).collect();
        saved["host_id"] = json!(format!("urn:uuid:{}-{}-4{}-a{}-{}", &hex[..8], &hex[8..12], &hex[13..16], &hex[17..20], &hex[20..]));
        write(path, &saved)?;
    }
    let client = saved["client_id"].as_str().map(String::from);
    let verifier = B64.encode(rand::random::<[u8; 32]>());
    let challenge = B64.encode(Sha256::digest(verifier.as_bytes()));
    let state = B64.encode(rand::random::<[u8; 16]>());
    let nonce = B64.encode(rand::random::<[u8; 16]>());
    let mut params = vec![
        ("client_id", client.clone().unwrap_or(REGISTER.into())),
        ("ext_agent_host_id", saved["host_id"].as_str().unwrap().into()),
        ("response_type", "code".into()),
        ("redirect_uri", REDIRECT_URI.into()),
        ("scope", format!("openid profile email offline_access resource.invoke {SHARING}")),
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

    // The listener must be up before the browser opens.
    let listener = TcpListener::bind("127.0.0.1:1455").context("port 1455 is busy (is another app signing in to ChatGPT?)")?;
    println!("Sign in with ChatGPT in your browser. If it does not open, visit:\n\n{url}\n");
    let _ = std::process::Command::new("open").arg(&url).status();
    let query = serve(listener)?;
    let param = |name: &str| query.split('&').find_map(|kv| kv.strip_prefix(&format!("{name}="))).map(dec);
    ensure!(param("state").as_deref() == Some(state.as_str()), "state mismatch; ignoring this sign-in");
    if let Some(e) = param("error") {
        bail!("ChatGPT sign-in failed: {}", param("error_description").unwrap_or(e));
    }
    let issued = match (client, param("client_id")) {
        (Some(saved), Some(got)) if saved != got => bail!("OpenAI returned a different client id; sign-in ignored"),
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
        tokens["scope"].as_str().is_some_and(|s| s.split(' ').any(|x| x == SHARING)),
        "the sign-in finished without allowing Arity to use your ChatGPT plan; sign in again and allow it"
    );
    // The token came straight from auth.openai.com over TLS; check it is for us and this attempt.
    let id = claims(tokens["id_token"].as_str().unwrap_or_default()).ok_or_else(|| anyhow!("no ID token"))?;
    let aud_ok = id["aud"].as_str() == Some(issued.as_str()) || id["aud"].as_array().is_some_and(|a| a.iter().any(|x| x == &json!(issued)));
    ensure!(aud_ok && id["nonce"].as_str() == Some(nonce.as_str()), "the ID token is not for this sign-in");
    let who = id["email"].as_str().or(id["sub"].as_str()).unwrap_or("your account").to_string();
    saved["client_id"] = json!(issued);
    saved["email"] = json!(who);
    save(path, saved, &tokens)?;
    Ok(who)
}

/// Who is signed in, if anyone.
pub fn account(path: &Path) -> Option<String> {
    signed_in(path).then(|| read(path)["email"].as_str().unwrap_or("signed in").to_string())
}

/// Forget the tokens. The client id and host id stay, so signing in again
/// reuses the registration instead of making a new one.
pub fn logout(path: &Path) -> Result<()> {
    let mut saved = read(path);
    if let Some(o) = saved.as_object_mut() {
        for k in ["access_token", "refresh_token", "expires_at", "email"] {
            o.remove(k);
        }
    }
    write(path, &saved)
}

/// Stop a sign-in that is waiting for the browser.
pub fn cancel() {
    if let Ok(mut s) = std::net::TcpStream::connect("127.0.0.1:1455") {
        let _ = s.write_all(b"GET /cancel HTTP/1.1\r\n\r\n");
    }
}

/// A valid access token, refreshing it first if it is about to expire, and the
/// ChatGPT account id from it if it carries one.
pub fn access(path: &Path) -> Result<(String, String)> {
    let not_in = "not signed in to ChatGPT; run `arity login` or use Sign in with ChatGPT";
    // Refresh tokens rotate, so the app and the CLI must not refresh at once.
    let lock = std::fs::File::create(path.with_extension("lock")).context(not_in)?;
    lock.lock()?;
    let saved = read(path);
    let client = saved["client_id"].as_str().ok_or_else(|| anyhow!("{not_in}"))?.to_string();
    let saved = if saved["expires_at"].as_u64().unwrap_or(0) <= now() + 60 {
        let refresh = saved["refresh_token"].as_str().ok_or_else(|| anyhow!("{not_in}"))?.to_string();
        let fresh = post_form(&[
            ("grant_type", "refresh_token".into()),
            ("client_id", client),
            ("refresh_token", refresh),
            ("resource", RESOURCE.into()),
        ])
        .context("ChatGPT sign-in expired; sign in again")?;
        save(path, saved, &fresh)?
    } else {
        saved
    };
    let token = saved["access_token"].as_str().ok_or_else(|| anyhow!("{not_in}"))?.to_string();
    let account = claims(&token)
        .and_then(|c| c["https://api.openai.com/auth"]["chatgpt_account_id"].as_str().map(String::from))
        .unwrap_or_default();
    Ok((token, account))
}

fn save(path: &Path, mut saved: Value, tokens: &Value) -> Result<Value> {
    saved["access_token"] = tokens["access_token"].clone();
    if tokens["refresh_token"].is_string() {
        saved["refresh_token"] = tokens["refresh_token"].clone();
    }
    saved["expires_at"] = json!(now() + tokens["expires_in"].as_u64().unwrap_or(3600));
    write(path, &saved)?;
    Ok(saved)
}

fn write(path: &Path, v: &Value) -> Result<()> {
    std::fs::create_dir_all(path.parent().unwrap())?;
    std::fs::write(path, serde_json::to_string_pretty(v)?)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(())
}

fn form(pairs: &[(&str, String)]) -> String {
    pairs.iter().map(|(k, v)| format!("{k}={}", enc(v))).collect::<Vec<_>>().join("&")
}

fn post_form(pairs: &[(&str, String)]) -> Result<Value> {
    let resp = reqwest::blocking::Client::new()
        .post(TOKEN_URL)
        .header("content-type", "application/x-www-form-urlencoded")
        .body(form(pairs))
        .send()?;
    let status = resp.status();
    let v: Value = resp.json()?;
    if !status.is_success() {
        // The body names the error; it never echoes tokens back, but keep only the fields that explain it.
        bail!("token endpoint {status}: {} {}", v["error"], v["error_description"]);
    }
    Ok(v)
}

/// The claims inside a JWT, unverified.
fn claims(token: &str) -> Option<Value> {
    let payload = token.split('.').nth(1)?;
    serde_json::from_slice(&B64.decode(payload.trim_end_matches('=')).ok()?).ok()
}

/// Waits for the browser's redirect and returns its query string.
fn serve(listener: TcpListener) -> Result<String> {
    for stream in listener.incoming().flatten() {
        let mut stream = stream;
        let mut line = String::new();
        if BufReader::new(&stream).read_line(&mut line).is_err() {
            continue;
        }
        // "GET /auth/callback?code=...&state=... HTTP/1.1"
        let target = line.split_whitespace().nth(1).unwrap_or_default().to_string();
        if target == "/cancel" && stream.peer_addr().is_ok_and(|a| a.ip().is_loopback()) {
            bail!("sign-in cancelled");
        }
        let Some(query) = target.strip_prefix("/auth/callback?") else {
            let _ = stream.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
            continue;
        };
        let page = "Arity: ChatGPT sign-in received. You can close this tab.";
        let _ = write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {}\r\n\r\n{page}", page.len());
        return Ok(query.to_string());
    }
    bail!("the sign-in listener stopped")
}

fn enc(s: &str) -> String {
    s.bytes()
        .map(|b| match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => (b as char).to_string(),
            _ => format!("%{b:02X}"),
        })
        .collect()
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tokens_from_the_old_codex_client_ask_for_a_new_sign_in() {
        let dir = std::env::temp_dir().join(format!("arity-siwc-{}", std::process::id()));
        let path = dir.join("codex-auth.json");
        write(&path, &json!({ "access_token": "a", "refresh_token": "r", "expires_at": now() + 999 })).unwrap();
        assert!(!signed_in(&path));
        let err = access(&path).unwrap_err().to_string();
        assert!(err.contains("arity login"), "{err}");
        write(&path, &json!({ "client_id": "oaiapp_x", "host_id": "urn:uuid:x", "access_token": "a.e30.s", "refresh_token": "r", "expires_at": now() + 999 })).unwrap();
        assert!(signed_in(&path));
        assert_eq!(access(&path).unwrap(), ("a.e30.s".into(), String::new()));
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
