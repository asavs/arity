//! Sign in with ChatGPT: OAuth with PKCE against OpenAI's public Codex client,
//! the same flow Codex CLI and pi use. The tokens stay in codex-auth.json in the data folder.

use std::io::{BufRead, BufReader, Write};
use std::net::TcpListener;
use std::path::Path;
use std::sync::mpsc;
use std::time::{SystemTime, UNIX_EPOCH};

use anyhow::{Context, Result, anyhow, bail};
use base64::Engine;
use base64::engine::general_purpose::URL_SAFE_NO_PAD as B64;
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

const CLIENT_ID: &str = "app_EMoamEEZ73f0CkXaXp7hrann";
const AUTHORIZE_URL: &str = "https://auth.openai.com/oauth/authorize";
const TOKEN_URL: &str = "https://auth.openai.com/oauth/token";
const REDIRECT_URI: &str = "http://localhost:1455/auth/callback";
const SCOPES: &str = "openid profile email offline_access";

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
}

pub fn login(path: &Path) -> Result<String> {
    let verifier = B64.encode(rand::random::<[u8; 32]>());
    let challenge = B64.encode(Sha256::digest(verifier.as_bytes()));
    let state = B64.encode(rand::random::<[u8; 16]>());
    let url = format!(
        "{AUTHORIZE_URL}?response_type=code&client_id={CLIENT_ID}&redirect_uri={}&scope={}\
         &code_challenge={challenge}&code_challenge_method=S256&state={state}\
         &id_token_add_organizations=true&codex_cli_simplified_flow=true",
        enc(REDIRECT_URI),
        enc(SCOPES)
    );

    // "localhost" may resolve to IPv4 or IPv6 in the browser, so listen on both.
    let (tx, rx) = mpsc::channel::<(String, String)>();
    let mut bound = 0;
    for addr in ["127.0.0.1:1455", "[::1]:1455"] {
        if let Ok(listener) = TcpListener::bind(addr) {
            bound += 1;
            let tx = tx.clone();
            std::thread::spawn(move || serve(listener, tx));
        }
    }
    if bound == 0 {
        bail!("port 1455 is busy (is Codex CLI signing in at the same time?)");
    }

    println!("Sign in with ChatGPT in your browser. If it does not open, visit:\n\n{url}\n");
    let _ = std::process::Command::new("open").arg(&url).status();

    let (code, got_state) = rx.recv()?;
    if got_state != state {
        bail!("state mismatch; ignoring this sign-in");
    }
    let tokens = post_form(&[
        ("grant_type", "authorization_code"),
        ("client_id", CLIENT_ID),
        ("code", &code),
        ("code_verifier", &verifier),
        ("redirect_uri", REDIRECT_URI),
    ])?;
    save(path, &tokens, None)?;
    let (_, account) = access(path)?;
    Ok(account)
}

/// A valid access token and the ChatGPT account id, refreshing if needed.
pub fn access(path: &Path) -> Result<(String, String)> {
    let saved: Value = serde_json::from_str(
        &std::fs::read_to_string(path).context("not signed in to ChatGPT; sign in first")?,
    )?;
    let saved = if saved["expires_at"].as_u64().unwrap_or(0) <= now() + 60 {
        let refresh = saved["refresh_token"].as_str().ok_or_else(|| anyhow!("no refresh token"))?;
        let fresh = post_form(&[
            ("grant_type", "refresh_token"),
            ("client_id", CLIENT_ID),
            ("refresh_token", refresh),
            ("scope", "openid profile email"),
        ])?;
        save(path, &fresh, Some(refresh))?
    } else {
        saved
    };
    let token = saved["access_token"].as_str().ok_or_else(|| anyhow!("no access token"))?.to_string();
    let account = account_id(&token).ok_or_else(|| anyhow!("token has no chatgpt_account_id claim"))?;
    Ok((token, account))
}

fn save(path: &Path, tokens: &Value, old_refresh: Option<&str>) -> Result<Value> {
    let saved = json!({
        "access_token": tokens["access_token"],
        "refresh_token": tokens["refresh_token"].as_str().or(old_refresh),
        "expires_at": now() + tokens["expires_in"].as_u64().unwrap_or(3600),
    });
    std::fs::create_dir_all(path.parent().unwrap())?;
    std::fs::write(path, serde_json::to_string_pretty(&saved)?)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600))?;
    }
    Ok(saved)
}

fn post_form(form: &[(&str, &str)]) -> Result<Value> {
    let body = form.iter().map(|(k, v)| format!("{k}={}", enc(v))).collect::<Vec<_>>().join("&");
    let resp = reqwest::blocking::Client::new()
        .post(TOKEN_URL)
        .header("content-type", "application/x-www-form-urlencoded")
        .body(body)
        .send()?;
    let status = resp.status();
    let v: Value = resp.json()?;
    if !status.is_success() {
        bail!("token endpoint {status}: {v}");
    }
    Ok(v)
}

/// The account id is a claim inside the access token (a JWT).
fn account_id(token: &str) -> Option<String> {
    let payload = token.split('.').nth(1)?;
    let claims: Value = serde_json::from_slice(&B64.decode(payload.trim_end_matches('=')).ok()?).ok()?;
    claims["https://api.openai.com/auth"]["chatgpt_account_id"].as_str().map(String::from)
}

fn serve(listener: TcpListener, tx: mpsc::Sender<(String, String)>) {
    for stream in listener.incoming().flatten() {
        let mut stream = stream;
        let mut line = String::new();
        if BufReader::new(&stream).read_line(&mut line).is_err() {
            continue;
        }
        // "GET /auth/callback?code=...&state=... HTTP/1.1"
        let target = line.split_whitespace().nth(1).unwrap_or_default().to_string();
        let Some(query) = target.strip_prefix("/auth/callback?") else {
            let _ = stream.write_all(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n");
            continue;
        };
        let param = |name: &str| {
            query.split('&').find_map(|kv| kv.strip_prefix(&format!("{name}="))).map(dec).unwrap_or_default()
        };
        let page = "Signed in to Arity spike. You can close this tab.";
        let _ = write!(stream, "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: {}\r\n\r\n{page}", page.len());
        let _ = tx.send((param("code"), param("state")));
        return;
    }
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
