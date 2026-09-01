"""Arity auth — OAuth 2.0 and cross-tool credential management.

Implements native Python OAuth 2.0 (PKCE & RFC 8628 Device Authorization) for
Google Antigravity (Cloud Code Assist), OpenAI Codex, and xAI Grok subscriptions,
with auto-import from ~/.omp/agent/agent.db, ~/.codex/auth.json, and automatic
background token refreshing.
"""
from __future__ import annotations

import base64
import logging
from .diagnostics import record_data_loss

logger = logging.getLogger(__name__)
import hashlib
import http.server
import json
import sys
import os
import secrets
import shutil
import sqlite3
import tempfile
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
GOOGLE_ANTIGRAVITY_CLIENT_ID_ENV = "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_ID"
GOOGLE_ANTIGRAVITY_CLIENT_SECRET_ENV = "ARITY_GOOGLE_ANTIGRAVITY_CLIENT_SECRET"
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
OPENAI_CLIENT_ID_ENV = "ARITY_OPENAI_CLIENT_ID"
OPENAI_AUTH_URL = "https://auth.openai.com/oauth/authorize"
OPENAI_TOKEN_URL = "https://auth.openai.com/oauth/token"
OPENAI_DEVICE_AUTH_URL = "https://auth.openai.com/api/v1/oauth/device/code"
OPENAI_SCOPES = "openid profile email offline_access api.connectors.read api.connectors.invoke"

# xAI SuperGrok / Grok Subscription
XAI_CLIENT_ID_ENV = "ARITY_XAI_CLIENT_ID"
XAI_DEVICE_AUTH_URL = "https://auth.x.ai/oauth2/device/code"
XAI_TOKEN_URL = "https://auth.x.ai/oauth2/token"
XAI_SCOPES = "openid profile email offline_access grok-cli:access api:access"


class AuthConfigurationError(RuntimeError):
    """Raised before OAuth side effects when required client configuration is absent."""


def _configured_value(explicit: Optional[str], env_name: str) -> str:
    """Resolve an explicit credential before consulting the current process environment."""
    value = explicit if explicit is not None else os.environ.get(env_name, "")
    return value.strip()


def _require_client_id(provider: str, env_name: str, explicit: Optional[str] = None) -> str:
    client_id = _configured_value(explicit, env_name)
    if client_id:
        return client_id
    raise AuthConfigurationError(
        f"{provider} OAuth requires {env_name}. "
        f"Set {env_name} in the environment before running login."
    )


def _require_google_oauth_client(
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> tuple[str, str]:
    """Resolve Google client configuration without retaining a bundled client identity."""
    resolved_id = _configured_value(client_id, GOOGLE_ANTIGRAVITY_CLIENT_ID_ENV)
    resolved_secret = _configured_value(client_secret, GOOGLE_ANTIGRAVITY_CLIENT_SECRET_ENV)
    missing = [
        name
        for name, value in (
            (GOOGLE_ANTIGRAVITY_CLIENT_ID_ENV, resolved_id),
            (GOOGLE_ANTIGRAVITY_CLIENT_SECRET_ENV, resolved_secret),
        )
        if not value
    ]
    if missing:
        raise AuthConfigurationError(
            "Google Antigravity OAuth requires "
            + " and ".join(missing)
            + ". Set these environment variables before running login."
        )
    return resolved_id, resolved_secret


# -----------------------------------------------------------------------------
# 1. Token Store & Cross-Tool Discovery
# -----------------------------------------------------------------------------

class TokenStore:
    """Manage Arity's plaintext credential file.

    Writes use same-directory atomic replacement and mode ``0600`` on POSIX,
    but each update is a read-modify-replace operation and is not transactional
    across threads or processes. On Windows, confidentiality depends on the
    destination directory's ACLs. Callers providing a custom ``auth_path`` must
    secure its parent directory. This store is not encrypted.
    """

    def __init__(self, auth_path: Optional[Path] = None):
        self.auth_path = auth_path or (Path.home() / ".arity" / "auth.json")
    def _ensure_dir(self) -> None:
        self.auth_path.parent.mkdir(parents=True, exist_ok=True)

    def _write_all(self, credentials: dict[str, dict[str, Any]]) -> None:
        """Flush a complete JSON file before atomically replacing the prior file."""
        self._ensure_dir()
        serialized = json.dumps(credentials, indent=2)
        descriptor: Optional[int] = None
        temp_path: Optional[Path] = None
        try:
            descriptor, raw_temp_path = tempfile.mkstemp(
                prefix=f".{self.auth_path.name}.",
                suffix=".tmp",
                dir=self.auth_path.parent,
            )
            temp_path = Path(raw_temp_path)
            if os.name == "posix":
                os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                descriptor = None
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.auth_path)
            temp_path = None
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    # Cleanup must not hide the write or replace failure.
                    pass
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    # Cleanup must not hide the write or replace failure.
                    pass

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
        creds = self.load_all()
        creds[key] = data
        self._write_all(creds)

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
            self._write_all(creds)
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
            conn = None
            try:
                conn = sqlite3.connect(str(omp_db), timeout=2.0)
                cur = conn.cursor()
                cur.execute("SELECT id, provider, credential_type, data FROM auth_credentials WHERE disabled_cause IS NULL")
                for row_id, prov, ctype, raw_data in cur.fetchall():
                    if raw_data:
                        try:
                            parsed = json.loads(raw_data)
                            if isinstance(parsed, dict):
                                email = parsed.get("email")
                                key = f"{prov}:{email}" if email else f"{prov}:{row_id}"
                                discovered[key] = parsed
                                if prov not in discovered:
                                    discovered[prov] = parsed
                        except (json.JSONDecodeError, KeyError, ValueError) as exc:
                            logger.warning("Failed to parse OMP credential row: %s", exc)
            except (TypeError, AttributeError):
                raise
            except Exception as exc:
                logger.warning("Failed querying OMP database %s: %s", omp_db, exc)
            finally:
                if conn:
                    try:
                        conn.close()
                    except Exception:
                        pass

        # 2. Check Codex CLI auth.json
        codex_auth = Path.home() / ".codex" / "auth.json"
        if codex_auth.exists() and "openai-codex" not in discovered:
            try:
                cdata = json.loads(codex_auth.read_text(encoding="utf-8"))
                if isinstance(cdata, dict):
                    tokens = cdata.get("tokens", {})
                    if isinstance(tokens, dict) and tokens.get("access_token") and tokens.get("refresh_token"):
                        discovered["openai-codex"] = {
                            "access": tokens["access_token"],
                            "refresh": tokens["refresh_token"],
                            "accountId": tokens.get("account_id"),
                            "source": "codex-cli",
                        }
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Failed to parse Codex auth file %s: %s", codex_auth, exc)
        return discovered

    def import_all(self) -> dict[str, dict[str, Any]]:
        """Import discovered credentials into Arity's active state file."""
        discovered = self.discover_external_credentials()
        existing = self.load_all()
        merged = {**discovered, **existing}
        self._write_all(merged)
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
                    client_id=cred.get("clientId") or cred.get("client_id"),
                    client_secret=cred.get("clientSecret") or cred.get("client_secret"),
                )
                merged = {**cred, **refreshed}
                self.save_credential(key, merged)
                return merged
            elif provider == "openai-codex":
                refreshed = refresh_openai_token(
                    refresh_token=refresh_token,
                    client_id=cred.get("clientId") or cred.get("client_id"),
                )
                merged = {**cred, **refreshed}
                self.save_credential(key, merged)
                return merged
            elif provider == "xai-oauth":
                refreshed = refresh_xai_token(
                    refresh_token=refresh_token,
                    client_id=cred.get("clientId") or cred.get("client_id"),
                )
                merged = {**cred, **refreshed}
                self.save_credential(key, merged)
                return merged
        except Exception as e:
            # A silent failure here surfaced as a 2.5h hang downstream. Say it once, plainly.
            print(f"[Arity auth] token refresh failed for '{key}': {e}. Run: arity auth login {provider.split('-')[0]}", file=sys.stderr)

        return cred


# -----------------------------------------------------------------------------
# 2. Token Refreshing Implementations
# -----------------------------------------------------------------------------

def refresh_google_antigravity_token(
    refresh_token: str,
    project_id: str,
    *,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> dict[str, Any]:
    """Refresh Google OAuth access token using Antigravity client credentials."""
    oauth_client_id, oauth_client_secret = _require_google_oauth_client(
        client_id,
        client_secret,
    )
    payload = {
        "client_id": oauth_client_id,
        "client_secret": oauth_client_secret,
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
        "refresh": res.get("refresh_token") or refresh_token,
        "expires": int((time.time() + expires_in - 300) * 1000),
        "projectId": project_id,
    }


def refresh_openai_token(
    refresh_token: str,
    *,
    client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Refresh OpenAI OAuth access token."""
    oauth_client_id = _require_client_id("OpenAI Codex", OPENAI_CLIENT_ID_ENV, client_id)
    payload = {
        "client_id": oauth_client_id,
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
        "refresh": res.get("refresh_token") or refresh_token,
        "expires": int((time.time() + expires_in - 300) * 1000),
    }


def refresh_xai_token(
    refresh_token: str,
    *,
    client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Refresh xAI OAuth access token."""
    oauth_client_id = _require_client_id("xAI Grok", XAI_CLIENT_ID_ENV, client_id)
    payload = {
        "client_id": oauth_client_id,
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
        "refresh": res.get("refresh_token") or refresh_token,
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
    *,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
) -> dict[str, Any]:
    """Execute native PKCE loopback authentication with Google Antigravity."""
    oauth_client_id, oauth_client_secret = _require_google_oauth_client(
        client_id,
        client_secret,
    )
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    redirect_uri = f"http://localhost:{port}/oauth-callback"

    auth_params = {
        "client_id": oauth_client_id,
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
                        b"<p>You can close this tab and return to Arity.</p></body></html>"
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
            # Benign: URL was printed to console; browser open failure is cosmetic.
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
        "client_id": oauth_client_id,
        "client_secret": oauth_client_secret,
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
    except Exception as exc:
        logger.warning("Failed to fetch Google Antigravity user email: %s", exc)
        record_data_loss("GoogleAntigravityEmailFetch", exc)
    # Discover / Onboard Cloud Code Assist Project
    print(f"\033[1;33m[Google Antigravity Auth]\033[0m Discovering Cloud Code Assist companion project...")
    project_id = discover_and_onboard_antigravity_project(access_token)

    cred_data = {
        "access": access_token,
        "refresh": refresh_token,
        "expires": expires_ms,
        "projectId": project_id,
        "clientId": oauth_client_id,
        "clientSecret": oauth_client_secret,
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
        except Exception as exc:
            logger.warning("Antigravity project onboard attempt failed: %s", exc)
            record_data_loss("GoogleAntigravityOnboard", exc)
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

def _device_poll_disposition(err_body: str, http_code: int) -> str:
    """Classify one rejected RFC 8628 poll.

    Returns "authorization_pending" or "slow_down" to keep polling, another OAuth error code
    for a terminal failure, or "" when the body carries no OAuth error and the status is not
    one the spec uses while waiting — the caller then re-raises the transport error itself.
    A body that does not parse stays pending on 400/428, since servers send unparseable bodies
    while authorization is still outstanding.
    """
    try:
        err_type = json.loads(err_body).get("error", "")
    except Exception:
        err_type = ""
    if err_type:
        return err_type
    return "authorization_pending" if http_code in (400, 428) else ""


def login_xai_grok(
    open_browser: bool = True,
    timeout: float = 120.0,
    *,
    client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Execute RFC 8628 Device Authorization flow for xAI SuperGrok."""
    oauth_client_id = _require_client_id("xAI Grok", XAI_CLIENT_ID_ENV, client_id)
    payload = {
        "client_id": oauth_client_id,
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
            # Benign: URL was printed to console; browser open failure is cosmetic.
            pass
    start_time = time.time()
    poll_payload = {
        "client_id": oauth_client_id,
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
                    "clientId": oauth_client_id,
                    "authorizedAt": int(time.time() * 1000),
                }
                store = TokenStore()
                store.save_credential("xai-oauth", cred_data)
                print(f"\033[1;32m[xAI Grok Auth]\033[0m Successfully authenticated xAI Grok subscription.")
                return cred_data
        except urllib.error.HTTPError as e:
            # Terminal errors (access_denied, expired_token) arrive as HTTP 400 like the
            # still-waiting ones, so they must be raised out of the loop rather than polled on.
            disposition = _device_poll_disposition(e.read().decode("utf-8", errors="replace"), e.code)
            if disposition == "authorization_pending":
                continue
            if disposition == "slow_down":
                interval += 5
                continue
            if not disposition:
                raise
            raise RuntimeError(f"xAI Auth Error: {disposition}")

    raise TimeoutError(f"xAI Device authorization timed out after {timeout}s.")


# -----------------------------------------------------------------------------
# 5. OpenAI Codex Native Login Flow (PKCE Loopback)
# -----------------------------------------------------------------------------

def login_openai_codex(
    port: int = 14555,
    open_browser: bool = True,
    timeout: float = 120.0,
    *,
    client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Execute native PKCE loopback authentication with OpenAI Codex."""
    oauth_client_id = _require_client_id("OpenAI Codex", OPENAI_CLIENT_ID_ENV, client_id)
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    redirect_uri = f"http://localhost:{port}/callback"

    auth_params = {
        "client_id": oauth_client_id,
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
                        b"<p>You can close this tab and return to Arity.</p></body></html>"
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
            # Benign: URL was printed to console; browser open failure is cosmetic.
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
        "client_id": oauth_client_id,
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
    except Exception as exc:
        logger.warning("Failed to parse JWT claims from Codex token: %s", exc)
        record_data_loss("CodexJWTClaimsParse", exc)
    cred_data = {
        "access": access_token,
        "refresh": refresh_token,
        "expires": expires_ms,
        "clientId": oauth_client_id,
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

ANTHROPIC_CLIENT_ID_ENV = "ARITY_ANTHROPIC_CLIENT_ID"
ANTHROPIC_AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
ANTHROPIC_TOKEN_URL = "https://api.anthropic.com/v1/oauth/token"
ANTHROPIC_CALLBACK_PORT = 54545
ANTHROPIC_SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"


def login_anthropic(
    port: int = ANTHROPIC_CALLBACK_PORT,
    open_browser: bool = True,
    timeout: float = 120.0,
    *,
    client_id: Optional[str] = None,
) -> dict[str, Any]:
    """Execute native PKCE loopback authentication with Anthropic Claude."""
    oauth_client_id = _require_client_id(
        "Anthropic Claude",
        ANTHROPIC_CLIENT_ID_ENV,
        client_id,
    )
    verifier, challenge = generate_pkce_pair()
    state = secrets.token_urlsafe(32)
    redirect_uri = f"http://localhost:{port}/callback"

    auth_params = {
        "client_id": oauth_client_id,
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
                        b"<p>You can close this tab and return to Arity.</p></body></html>"
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
            # Benign: URL was printed to console; browser open failure is cosmetic.
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
        "client_id": oauth_client_id,
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
        "clientId": oauth_client_id,
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
