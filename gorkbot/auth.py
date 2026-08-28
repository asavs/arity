"""gorkbot auth — Autonomous OAuth 2.0 & cross-tool credential management.

Implements native Python OAuth 2.0 (PKCE & RFC 8628 Device Authorization) for
Google Antigravity (Cloud Code Assist), OpenAI Codex, and xAI Grok subscriptions,
with auto-import from ~/.omp/agent/agent.db, ~/.codex/auth.json, and automatic
background token refreshing.
"""
from __future__ import annotations

import base64
import hashlib
import http.server
import json
import os
import secrets
import shutil
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


# -----------------------------------------------------------------------------
# Provider OAuth Constants
# -----------------------------------------------------------------------------

# Google Antigravity / Cloud Code Assist (Gemini 3 Pro/Flash & Claude via Google Cloud)
GOOGLE_ANTIGRAVITY_CLIENT_ID = (
    os.environ.get("ARITY_GOOGLE_ANTIGRAVITY_CLIENT_ID", "")
)
GOOGLE_ANTIGRAVITY_CLIENT_SECRET = (
    os.environ.get("ARITY_GOOGLE_ANTIGRAVITY_CLIENT_SECRET", "")
)
GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_CLOUD_CODE_ENDPOINT = "https://daily-cloudcode-pa.googleapis.com"
GOOGLE_ANTIGRAVITY_CALLBACK_PORT = 51121
GOOGLE_ANTIGRAVITY_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

# OpenAI Codex / ChatGPT Plus/Team Subscription
OPENAI_CLIENT_ID = os.environ.get("ARITY_OPENAI_CLIENT_ID", "")
OPENAI_AUTH_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_DEVICE_AUTH_URL = "https://auth.openai.com/api/v1/oauth/device/code"
OPENAI_SCOPES = "openid profile email offline_access api.connectors.read api.connectors.invoke"

# xAI SuperGrok / Grok Subscription
XAI_CLIENT_ID = os.environ.get("ARITY_XAI_CLIENT_ID", "")
XAI_DEVICE_AUTH_URL = "https://auth.x.ai/oauth2/device/code"
XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"
XAI_SCOPES = "openid profile email offline_access grok-cli:access api:access"


# -----------------------------------------------------------------------------
# 1. Token Store & Cross-Tool Discovery
# -----------------------------------------------------------------------------

class TokenStore:
    """Manages persistent credentials in ~/.gorkbot/auth.json with multi-account support."""

    def __init__(self, auth_path: Optional[Path] = None):
        self.auth_path = auth_path or (Path.home() / ".gorkbot" / "auth.json")

    def _ensure_dir() -> None:
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> dict[str, dict[str, Any]]:
        """Load all saved credentials."""
        if not self.auth_path.exists():
            return {}
        try:
            return json.loads(self.auth_path.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def save_credential(self, key: str, data: dict[str, Any]) -> None:
        """Save or update credential for a given key / account."""
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        creds = self.load_all()
        creds[key] = data
        self.auth_path.write_text(json.dumps(creds, indent=2), encoding="utf-8")

    def delete_credential(self, key: str) -> bool:
        """Delete credential for a provider or account key."""
        creds = self.load_all()
        deleted = False
        # Direct key match
        if key in creds:
            del creds[key]
            deleted = True
        # Also check all sub-accounts matching key:*
        sub_keys = [k for k in creds if k.startswith(f"{key}:")]
        for sk in sub_keys:
            del creds[sk]
            deleted = True

        if deleted:
            self.auth_path.parent.mkdir(parents=True, exist_ok=True)
            self.auth_path.write_text(json.dumps(creds, indent=2), encoding="utf-8")
        return deleted

    def get_credential(self, key: str) -> Optional[dict[str, Any]]:
        """Get credential by exact key or first matching account for base provider."""
        creds = self.load_all() or self.discover_external_credentials()
        if key in creds:
            return creds[key]

        # Look for sub-account keys matching key:*
        for k, v in creds.items():
            if k.startswith(f"{key}:"):
                return v

        return None

    def get_all_for_provider(self, provider: str) -> list[tuple[str, dict[str, Any]]]:
        """Get all (key, credential) pairs for a provider (e.g. all Google Antigravity accounts)."""
        creds = self.load_all() or self.discover_external_credentials()
        results: list[tuple[str, dict[str, Any]]] = []
        seen_emails: set[str] = set()

        for k, v in creds.items():
            if k == provider or k.startswith(f"{provider}:"):
                email = v.get("email") or v.get("projectId") or k
                if email not in seen_emails:
                    seen_emails.add(email)
                    results.append((k, v))
        return results

    def discover_external_credentials(self) -> dict[str, dict[str, Any]]:
        """Scan ~/.omp/agent/agent.db, ~/.codex/auth.json, preserving every account."""
        discovered: dict[str, dict[str, Any]] = {}

        # 1. Check OMP SQLite Database
        omp_db = Path.home() / ".omp" / "agent" / "agent.db"
        if omp_db.exists():
            try:
                conn = sqlite3.connect(str(omp_db), timeout=2.0)
                cur = conn.cursor()
                cur.execute("SELECT id, provider, credential_type, data FROM auth_credentials WHERE disabled_cause IS NULL")
                for row_id, prov, ctype, raw_data in cur.fetchall():
                    if raw_data:
                        try:
                            parsed = json.loads(raw_data)
                            email = parsed.get("email")
                            key = f"{prov}:{email}" if email else f"{prov}:{row_id}"
                            discovered[key] = parsed
                            if prov not in discovered:
                                discovered[prov] = parsed
                        except Exception:
                            continue
                conn.close()
            except Exception:
                pass

        # 2. Check Codex CLI auth.json
        codex_auth = Path.home() / ".codex" / "auth.json"
        if codex_auth.exists() and "openai-codex" not in discovered:
            try:
                cdata = json.loads(codex_auth.read_text(encoding="utf-8"))
                tokens = cdata.get("tokens", {})
                if tokens.get("access_token") and tokens.get("refresh_token"):
                    discovered["openai-codex"] = {
                        "access": tokens["access_token"],
                        "refresh": tokens["refresh_token"],
                        "accountId": tokens.get("account_id"),
                        "source": "codex-cli",
                    }
            except Exception:
                pass

        return discovered

    def import_all(self) -> dict[str, dict[str, Any]]:
        """Explicitly import all discovered credentials into ~/.gorkbot/auth.json."""
        discovered = self.discover_external_credentials()
        existing = self.load_all()
        merged = {**discovered, **existing}
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)
        self.auth_path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        return merged

    def refresh_if_needed(self, key: str) -> Optional[dict[str, Any]]:
        """Check if access token is expired (or expires in < 5m) and refresh it."""
        cred = self.get_credential(key)
        if not cred:
            return None

        expires = cred.get("expires")
        now_ms = time.time() * 1000

        needs_refresh = False
        if expires:
            exp_ms = float(expires)
            if exp_ms < 10_000_000_000:
                exp_ms *= 1000
            if now_ms >= (exp_ms - 300_000):
                needs_refresh = True

        if not needs_refresh:
            return cred

        refresh_token = cred.get("refresh") or cred.get("refresh_token")
        if not refresh_token:
            return cred

        provider = key.split(":")[0]
        try:
            if provider == "google-antigravity":
                refreshed = refresh_google_antigravity_token(
                    refresh_token=refresh_token,
                    project_id=cred.get("projectId") or cred.get("project_id", ""),
                )
                merged = {**cred, **refreshed}
                self.save_credential(key, merged)
                return merged
            elif provider == "openai-codex":
                refreshed = refresh_openai_token(refresh_token=refresh_token)
                merged = {**cred, **refreshed}
                self.save_credential(key, merged)
                return merged
            elif provider == "xai-oauth":
                refreshed = refresh_xai_token(refresh_token=refresh_token)
                merged = {**cred, **refreshed}
                self.save_credential(key, merged)
                return merged
        except Exception:
            pass

        return cred


# -----------------------------------------------------------------------------
# 2. Token Refreshing Implementations
# -----------------------------------------------------------------------------

def refresh_google_antigravity_token(refresh_token: str, project_id: str) -> dict[str, Any]:
    """Refresh Google OAuth access token using Antigravity client credentials."""
    payload = {
        "client_id": GOOGLE_ANTIGRAVITY_CLIENT_ID,
        "client_secret": GOOGLE_ANTIGRAVITY_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))

    expires_in = res.get("expires_in", 3600)
    return {
        "access": res["access_token"],
        "refresh": res.get("refresh_token", refresh_token),
        "expires": int((time.time() + expires_in - 300) * 1000),
        "projectId": project_id,
    }


def refresh_openai_token(refresh_token: str) -> dict[str, Any]:
    """Refresh OpenAI OAuth access token."""
    payload = {
        "client_id": OPENAI_CLIENT_ID,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))

    expires_in = res.get("expires_in", 3600)
    return {
        "access": res["access_token"],
        "refresh": res.get("refresh_token", refresh_token),
        "expires": int((time.time() + expires_in - 300) * 1000),
    }


def refresh_xai_token(refresh_token: str) -> dict[str, Any]:
    """Refresh xAI OAuth access token."""
    payload = {
        "client_id": XAI_CLIENT_ID,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        XAI_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        res = json.loads(resp.read().decode("utf-8"))

    expires_in = res.get("expires_in", 3600)
    return {
        "access": res["access_token"],
        "refresh": res.get("refresh_token", refresh_token),
        "expires": int((time.time() + expires_in - 300) * 1000),
    }


# -----------------------------------------------------------------------------
# 3. Google Antigravity Native Login Flow (PKCE + Loopback Server)
# -----------------------------------------------------------------------------

def generate_pkce_pair() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("utf-8")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")
    return verifier, challenge


def login_google_antigravity(
    port: int = GOOGLE_ANTIGRAVITY_CALLBACK_PORT,
    open_browser: bool = True,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Execute native PKCE loopback authentication with Google Antigravity."""
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    redirect_uri = f"http://localhost:{port}/oauth-callback"

    auth_params = {
        "client_id": GOOGLE_ANTIGRAVITY_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(GOOGLE_ANTIGRAVITY_SCOPES),
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "access_type": "offline",
        "prompt": "consent",
    }
    full_auth_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    auth_code: Optional[str] = None
    callback_error: Optional[str] = None
    server_done = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code, callback_error
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/oauth-callback":
                qs = urllib.parse.parse_qs(parsed.query)
                received_state = qs.get("state", [""])[0]
                if received_state != state:
                    callback_error = "Invalid state returned by Google"
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch error.")
                elif "code" in qs:
                    auth_code = qs["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h1>Authentication Successful!</h1>"
                        b"<p>You can close this tab and return to Gorkbot.</p></body></html>"
                    )
                else:
                    callback_error = qs.get("error", ["Unknown OAuth error"])[0]
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(f"OAuth error: {callback_error}".encode("utf-8"))
                server_done.set()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            # Silence default HTTP server logging
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print(f"\n\033[1;36m[Google Antigravity Auth]\033[0m Opening browser for authentication...")
    print(f"If your browser doesn't open automatically, visit:\n{full_auth_url}\n")

    if open_browser:
        try:
            webbrowser.open(full_auth_url)
        except Exception:
            pass

    # Wait for callback or timeout
    finished = server_done.wait(timeout=timeout)
    server.shutdown()
    server.server_close()

    if not finished:
        raise TimeoutError(f"Authentication timed out after {timeout} seconds.")
    if callback_error:
        raise RuntimeError(f"Google OAuth error: {callback_error}")
    if not auth_code:
        raise RuntimeError("No authorization code received from callback.")

    # Exchange authorization code for tokens
    print("\033[1;33m[Google Antigravity Auth]\033[0m Exchanging authorization code for tokens...")
    token_params = {
        "client_id": GOOGLE_ANTIGRAVITY_CLIENT_ID,
        "client_secret": GOOGLE_ANTIGRAVITY_CLIENT_SECRET,
        "code": auth_code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    data = urllib.parse.urlencode(token_params).encode("utf-8")
    req = urllib.request.Request(
        GOOGLE_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        token_res = json.loads(resp.read().decode("utf-8"))

    access_token = token_res["access_token"]
    refresh_token = token_res.get("refresh_token", "")
    expires_in = token_res.get("expires_in", 3600)
    expires_ms = int((time.time() + expires_in - 300) * 1000)

    # Get user email
    email = ""
    try:
        user_req = urllib.request.Request(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        with urllib.request.urlopen(user_req, timeout=10) as uresp:
            udata = json.loads(uresp.read().decode("utf-8"))
            email = udata.get("email", "")
    except Exception:
        pass

    # Discover / Onboard Cloud Code Assist Project
    print(f"\033[1;33m[Google Antigravity Auth]\033[0m Discovering Cloud Code Assist companion project...")
    project_id = discover_and_onboard_antigravity_project(access_token)

    cred_data = {
        "access": access_token,
        "refresh": refresh_token,
        "expires": expires_ms,
        "projectId": project_id,
        "email": email,
        "authorizedAt": int(time.time() * 1000),
    }

    store = TokenStore()
    if email:
        store.save_credential(f"google-antigravity:{email}", cred_data)
    store.save_credential("google-antigravity", cred_data)
    print(f"\033[1;32m[Google Antigravity Auth]\033[0m Successfully authenticated as \033[1m{email or 'user'}\033[0m (Project: \033[1m{project_id}\033[0m)")
    return cred_data


def discover_and_onboard_antigravity_project(access_token: str) -> str:
    """Query :loadCodeAssist and :onboardUser to resolve the Antigravity GCP project."""
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "antigravity/hub/2.8.0 (aidev_client; os_type=windows; arch=x64; cl=963137146)",
    }
    payload = {"metadata": {"ideType": "ANTIGRAVITY"}}

    load_url = f"{GOOGLE_CLOUD_CODE_ENDPOINT}/v1internal:loadCodeAssist"
    req = urllib.request.Request(
        load_url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=20) as resp:
        res = json.loads(resp.read().decode("utf-8"))

    # Check if project already allocated
    project_id = res.get("cloudaicompanionProject")
    if not project_id:
        # Check currentTier or provision free tier
        onboard_url = f"{GOOGLE_CLOUD_CODE_ENDPOINT}/v1internal:onboardUser"
        onboard_payload = {"tierId": "free-tier", "metadata": {"ideType": "ANTIGRAVITY"}}
        oreq = urllib.request.Request(
            onboard_url,
            data=json.dumps(onboard_payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(oreq, timeout=20) as oresp:
                json.loads(oresp.read().decode("utf-8"))
        except Exception:
            pass

        # Reload after onboarding
        with urllib.request.urlopen(req, timeout=20) as rresp:
            rres = json.loads(rresp.read().decode("utf-8"))
            project_id = rres.get("cloudaicompanionProject")

    return project_id or "default-antigravity"


def fetch_antigravity_quota(access_token: str, project_id: str) -> dict[str, Any]:
    """Fetch live remaining quota percentages and reset windows from Cloud Code Assist."""
    endpoint = f"{GOOGLE_CLOUD_CODE_ENDPOINT}/v1internal:fetchAvailableModels"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "User-Agent": "antigravity/hub/2.8.0 (aidev_client; os_type=windows; arch=x64; cl=963137146)",
    }
    payload = {"project": project_id}
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            res = json.loads(resp.read().decode("utf-8"))
            models = res.get("models", {})
            quota_summary: dict[str, Any] = {}
            for mname, minfo in models.items():
                qinfo = minfo.get("quotaInfo", {})
                if isinstance(qinfo, list):
                    qinfo = qinfo[0] if qinfo else {}
                frac = qinfo.get("remainingFraction")
                reset = qinfo.get("resetTime")
                if frac is not None:
                    quota_summary[mname] = {
                        "remainingFraction": frac,
                        "resetTime": reset,
                    }
            return quota_summary
    except Exception:
        return {}

# -----------------------------------------------------------------------------
# 4. xAI Grok Native Login Flow (RFC 8628 Device Authorization)
# -----------------------------------------------------------------------------

def login_xai_grok(open_browser: bool = True, timeout: float = 120.0) -> dict[str, Any]:
    """Execute RFC 8628 Device Authorization flow for xAI SuperGrok."""
    payload = {
        "client_id": XAI_CLIENT_ID,
        "scope": XAI_SCOPES,
    }
    data = urllib.parse.urlencode(payload).encode("utf-8")
    req = urllib.request.Request(
        XAI_DEVICE_AUTH_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        dev_res = json.loads(resp.read().decode("utf-8"))

    device_code = dev_res["device_code"]
    user_code = dev_res.get("user_code", "")
    verification_uri = dev_res.get("verification_uri") or dev_res.get("verification_uri_complete") or "https://auth.x.ai"
    interval = dev_res.get("interval", 5)

    print(f"\n\033[1;36m[xAI Grok Auth]\033[0m Complete authorization in your browser:")
    print(f"  URL:  \033[1m{verification_uri}\033[0m")
    print(f"  Code: \033[1;32m{user_code}\033[0m\n")

    if open_browser:
        try:
            webbrowser.open(verification_uri)
        except Exception:
            pass

    start_time = time.time()
    poll_payload = {
        "client_id": XAI_CLIENT_ID,
        "device_code": device_code,
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
    }
    poll_data = urllib.parse.urlencode(poll_payload).encode("utf-8")

    while (time.time() - start_time) < timeout:
        time.sleep(interval)
        poll_req = urllib.request.Request(
            XAI_TOKEN_URL,
            data=poll_data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(poll_req, timeout=15) as resp:
                token_res = json.loads(resp.read().decode("utf-8"))
                access_token = token_res["access_token"]
                refresh_token = token_res.get("refresh_token", "")
                expires_in = token_res.get("expires_in", 3600)
                expires_ms = int((time.time() + expires_in - 300) * 1000)

                cred_data = {
                    "access": access_token,
                    "refresh": refresh_token,
                    "expires": expires_ms,
                    "authorizedAt": int(time.time() * 1000),
                }
                store = TokenStore()
                store.save_credential("xai-oauth", cred_data)
                print(f"\033[1;32m[xAI Grok Auth]\033[0m Successfully authenticated xAI Grok subscription.")
                return cred_data
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8")
            try:
                err_json = json.loads(err_body)
                err_type = err_json.get("error", "")
                if err_type == "authorization_pending":
                    continue
                elif err_type == "slow_down":
                    interval += 5
                    continue
                else:
                    raise RuntimeError(f"xAI Auth Error: {err_type}")
            except Exception:
                if e.code == 400 or e.code == 428:
                    continue
                raise

    raise TimeoutError(f"xAI Device authorization timed out after {timeout}s.")


# -----------------------------------------------------------------------------
# 5. OpenAI Codex Native Login Flow (PKCE Loopback)
# -----------------------------------------------------------------------------

def login_openai_codex(
    port: int = 14555,
    open_browser: bool = True,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Execute native PKCE loopback authentication with OpenAI Codex."""
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    redirect_uri = f"http://localhost:{port}/callback"

    auth_params = {
        "client_id": OPENAI_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": OPENAI_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    full_auth_url = f"{OPENAI_AUTH_URL}?{urllib.parse.urlencode(auth_params)}"

    auth_code: Optional[str] = None
    callback_error: Optional[str] = None
    server_done = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code, callback_error
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/callback":
                qs = urllib.parse.parse_qs(parsed.query)
                received_state = qs.get("state", [""])[0]
                if received_state != state:
                    callback_error = "State mismatch"
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch error.")
                elif "code" in qs:
                    auth_code = qs["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h1>OpenAI Authentication Successful!</h1>"
                        b"<p>You can close this tab and return to Gorkbot.</p></body></html>"
                    )
                else:
                    callback_error = qs.get("error", ["Unknown OAuth error"])[0]
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(f"OAuth error: {callback_error}".encode("utf-8"))
                server_done.set()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print(f"\n\033[1;36m[OpenAI Codex Auth]\033[0m Opening browser for authentication...")
    print(f"If your browser doesn't open automatically, visit:\n{full_auth_url}\n")

    if open_browser:
        try:
            webbrowser.open(full_auth_url)
        except Exception:
            pass

    finished = server_done.wait(timeout=timeout)
    server.shutdown()
    server.server_close()

    if not finished:
        raise TimeoutError(f"OpenAI Authentication timed out after {timeout} seconds.")
    if callback_error:
        raise RuntimeError(f"OpenAI OAuth error: {callback_error}")
    if not auth_code:
        raise RuntimeError("No authorization code received.")

    print("\033[1;33m[OpenAI Codex Auth]\033[0m Exchanging code for tokens...")
    token_params = {
        "client_id": OPENAI_CLIENT_ID,
        "code": auth_code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    data = urllib.parse.urlencode(token_params).encode("utf-8")
    req = urllib.request.Request(
        OPENAI_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        token_res = json.loads(resp.read().decode("utf-8"))

    access_token = token_res["access_token"]
    refresh_token = token_res.get("refresh_token", "")
    expires_in = token_res.get("expires_in", 3600)
    expires_ms = int((time.time() + expires_in - 300) * 1000)

    # Extract account_id from JWT access_token claims if available
    account_id = None
    try:
        parts = access_token.split(".")
        if len(parts) >= 2:
            padded = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
            claims = json.loads(base64.urlsafe_b64decode(padded.encode("utf-8")).decode("utf-8"))
            account_id = claims.get("https://api.openai.com/auth.account_id") or claims.get("account_id")
    except Exception:
        pass

    cred_data = {
        "access": access_token,
        "refresh": refresh_token,
        "expires": expires_ms,
        "accountId": account_id,
        "authorizedAt": int(time.time() * 1000),
    }

    store = TokenStore()
    store.save_credential("openai-codex", cred_data)
    print(f"\033[1;32m[OpenAI Codex Auth]\033[0m Successfully authenticated ChatGPT Codex subscription.")
    return cred_data


# -----------------------------------------------------------------------------
# 6. Anthropic Claude Native Login Flow (PKCE Loopback)
# -----------------------------------------------------------------------------

ANTHROPIC_CLIENT_ID = os.environ.get("ARITY_ANTHROPIC_CLIENT_ID", "")
ANTHROPIC_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
ANTHROPIC_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
ANTHROPIC_CALLBACK_PORT = 54545
ANTHROPIC_SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"


def login_anthropic(
    port: int = ANTHROPIC_CALLBACK_PORT,
    open_browser: bool = True,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Execute native PKCE loopback authentication with Anthropic Claude."""
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    redirect_uri = f"http://localhost:{port}/callback"

    auth_params = {
        "client_id": ANROPIC_CLIENT_ID if "ANROPIC_CLIENT_ID" in locals() else ANTHROPIC_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": ANTHROPIC_SCOPES,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    }
    full_auth_url = f"{ANTHROPIC_AUTHORIZE_URL}?{urllib.parse.urlencode(auth_params)}"

    auth_code: Optional[str] = None
    callback_error: Optional[str] = None
    server_done = threading.Event()

    class CallbackHandler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal auth_code, callback_error
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == "/callback":
                qs = urllib.parse.parse_qs(parsed.query)
                received_state = qs.get("state", [""])[0]
                if received_state != state:
                    callback_error = "State mismatch"
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(b"State mismatch error.")
                elif "code" in qs:
                    auth_code = qs["code"][0]
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(
                        b"<html><body><h1>Claude Authentication Successful!</h1>"
                        b"<p>You can close this tab and return to Gorkbot.</p></body></html>"
                    )
                else:
                    callback_error = qs.get("error", ["Unknown OAuth error"])[0]
                    self.send_response(400)
                    self.end_headers()
                    self.wfile.write(f"OAuth error: {callback_error}".encode("utf-8"))
                server_done.set()
            else:
                self.send_response(404)
                self.end_headers()

        def log_message(self, format, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), CallbackHandler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()

    print(f"\n\033[1;36m[Anthropic Claude Auth]\033[0m Opening browser for authentication...")
    print(f"If your browser doesn't open automatically, visit:\n{full_auth_url}\n")

    if open_browser:
        try:
            webbrowser.open(full_auth_url)
        except Exception:
            pass

    finished = server_done.wait(timeout=timeout)
    server.shutdown()
    server.server_close()

    if not finished:
        raise TimeoutError(f"Anthropic Authentication timed out after {timeout} seconds.")
    if callback_error:
        raise RuntimeError(f"Anthropic OAuth error: {callback_error}")
    if not auth_code:
        raise RuntimeError("No authorization code received.")

    print("\033[1;33m[Anthropic Claude Auth]\033[0m Exchanging code for tokens...")
    token_payload = {
        "client_id": ANTHROPIC_CLIENT_ID,
        "code": auth_code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    data = json.dumps(token_payload).encode("utf-8")
    req = urllib.request.Request(
        ANTHROPIC_TOKEN_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        token_res = json.loads(resp.read().decode("utf-8"))

    access_token = token_res["access_token"]
    refresh_token = token_res.get("refresh_token", "")
    expires_in = token_res.get("expires_in", 3600)
    expires_ms = int((time.time() + expires_in - 300) * 1000)
    account = token_res.get("account", {})
    email = account.get("email_address", "")
    account_id = account.get("uuid", "")

    cred_data = {
        "access": access_token,
        "refresh": refresh_token,
        "expires": expires_ms,
        "accountId": account_id,
        "email": email,
        "authorizedAt": int(time.time() * 1000),
    }

    store = TokenStore()
    if email:
        store.save_credential(f"anthropic:{email}", cred_data)
    store.save_credential("anthropic", cred_data)
    print(f"\033[1;32m[Anthropic Claude Auth]\033[0m Successfully authenticated Claude account \033[1m{email or 'user'}\033[0m.")
    return cred_data
