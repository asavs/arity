"""Arity wire — direct provider protocols and transparent harness fallbacks.

Axiom 13: The Seam Test — Own the pure from-scratch wire protocol, keep the
external harness swappable as a seamless fallback and A/B benchmark.
"""
from __future__ import annotations

import json
import sys
import os
import logging

logger = logging.getLogger(__name__)
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ._version import USER_AGENT
from .handlers import CLIModelProvider, GeminiModelProvider, OpenAIModelProvider
from .seams import ModelProvider
from .types import CallModel, ModelCompleted, ModelFailed


# -----------------------------------------------------------------------------
# 1. Local OAuth Credential Discovery
# -----------------------------------------------------------------------------

def load_local_oauth_credentials() -> dict[str, dict[str, Any]]:
    """Discover active OAuth subscription credentials from TokenStore (standalone + external)."""
    try:
        from .auth import TokenStore
        store = TokenStore()
        return store.load_all() or store.discover_external_credentials()
    except Exception:
        return {}


# -----------------------------------------------------------------------------
# 1.1 Direct Google Antigravity Wire Provider (Gemini 3 & Claude on GCP)
# -----------------------------------------------------------------------------

@dataclass
class AntigravityWireProvider:
    """Direct HTTPS wire caller for Google Cloud Code Assist (Antigravity)."""
    access_token: str
    project_id: str
    model: str = "gemini-3-flash-agent"
    timeout: float = 60.0
    account_key: str = "google-antigravity"  # which account's token/project this seat spends

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        import uuid
        import time

        # 1. Attempt token auto-refresh for THIS seat's account. Refreshing the bare provider key
        #    returned the first account on file and silently moved every seat onto it.
        try:
            from .auth import TokenStore
            store = TokenStore()
            refreshed = store.refresh_if_needed(self.account_key)
            if refreshed and refreshed.get("access"):
                self.access_token = refreshed["access"]
                if refreshed.get("projectId"):
                    self.project_id = refreshed["projectId"]
        except (TypeError, AttributeError):
            raise
        except Exception as exc:
            logger.warning("Token auto-refresh failed for account %s: %s", self.account_key, exc)
        endpoint = "https://daily-cloudcode-pa.googleapis.com/v1internal:generateContent"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": "antigravity/hub/2.8.0 (aidev_client; os_type=windows; arch=x64; cl=963137146)",
            "Accept": "application/json",
        }

        # Map model IDs to Antigravity wire configurations
        wire_model = self.model
        model_enum = None
        is_claude = "claude" in self.model.lower()
        is_oss = "gpt-oss" in self.model.lower() or "oss" in self.model.lower()

        if is_oss:
            wire_model = "gpt-oss-120b-medium"
        elif is_claude:
            wire_model = "claude-opus-4-6-thinking" if "opus" in self.model.lower() else "claude-sonnet-4-6"
        elif "pro" in self.model.lower():
            wire_model = "gemini-3.1-pro-low"
            model_enum = "MODEL_PLACEHOLDER_M36"
        elif "flash" in self.model.lower():
            wire_model = "gemini-3-flash-agent"
            model_enum = "MODEL_PLACEHOLDER_M132"

        session_id = f"-{int(time.time() * 1000)}"
        request_id = f"agent/main/{int(time.time() * 1000)}/{str(uuid.uuid4())}/1"

        from .gemini_format import to_contents, tool_declarations, parse_parts, usage_from
        contents, system_text = to_contents(effect.messages)
        system_instruction: Optional[dict[str, Any]] = {"role": "user", "parts": [{"text": system_text}]} if system_text else None

        inner_request: dict[str, Any] = {
            "sessionId": session_id,
            "contents": contents,
            "generationConfig": {
                "maxOutputTokens": 64000 if is_claude else 65536,
                "temperature": effect.temperature,
            },
        }

        if system_instruction:
            inner_request["systemInstruction"] = system_instruction

        if model_enum:
            inner_request["labels"] = {"model_enum": model_enum}

        decls = tool_declarations(effect.tools)
        if decls:
            inner_request["tools"] = [{"functionDeclarations": decls}]

        if is_claude:
            inner_request["toolConfig"] = {
                "functionCallingConfig": {
                    "mode": "VALIDATED",
                }
            }
        elif effect.tools:
            inner_request["toolConfig"] = {"functionCallingConfig": {"mode": "AUTO"}}

        payload = {
            "project": self.project_id,
            "requestId": request_id,
            "model": wire_model,
            "userAgent": "antigravity",
            "requestType": "agent",
            "request": inner_request,
        }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                candidates = res.get("response", {}).get("candidates", [])
                if not candidates:
                    candidates = res.get("candidates", [])

                content_text, tool_calls = None, []
                if candidates:
                    content_text, tool_calls = parse_parts(candidates[0].get("content", {}).get("parts", []))

                usage_meta = res.get("response", {}).get("usageMetadata", {})
                usage = {
                    "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                    # thoughts are output the account pays for; candidatesTokenCount alone under-reports
                    "completion_tokens": usage_meta.get("candidatesTokenCount", 0) + usage_meta.get("thoughtsTokenCount", 0),
                    "thought_tokens": usage_meta.get("thoughtsTokenCount", 0),
                    "total_tokens": usage_meta.get("totalTokenCount", 0),
                }

                return ModelCompleted(
                    content=content_text,
                    tool_calls=tool_calls,
                    usage=usage,
                    finish_reason="stop",
                    seat_id=f"wire:antigravity:{wire_model}",
                )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            retryable = e.code in (401, 403, 429, 500, 502, 503)
            return ModelFailed(
                error=f"Antigravity wire HTTP {e.code}: {err_body}",
                seat_id=f"wire:antigravity:{wire_model}",
                retryable=retryable,
            )
        except Exception as e:
            return ModelFailed(
                error=f"Antigravity wire request failed: {str(e)}",
                seat_id=f"wire:antigravity:{wire_model}",
                retryable=True,
            )

# -----------------------------------------------------------------------------
# 2. Direct OpenAI Codex Wire Provider (ChatGPT Subscription)
# -----------------------------------------------------------------------------

@dataclass
class CodexWireProvider:
    """Direct HTTPS wire caller for ChatGPT's Codex backend."""
    access_token: str
    account_id: str
    model: str = "gpt-5.6-sol"
    timeout: float = 60.0

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        endpoint = "https://chatgpt.com/backend-api/codex/responses"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "chatgpt-account-id": str(self.account_id),
            "OpenAI-Beta": "responses=true",
            "originator": "codex",
            "version": "0.144.1",
            "User-Agent": USER_AGENT,
            "accept": "text/event-stream",
        }

        # Convert messages into Codex input items
        input_items: list[dict[str, Any]] = []
        import uuid
        for msg in effect.messages:
            role = msg.get("role")
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls", [])

            if role == "tool":
                input_items.append({
                    "type": "function_call_output",
                    "call_id": msg.get("tool_call_id") or msg.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                    "output": str(content),
                })
            elif role == "assistant":
                if content:
                    input_items.append({
                        "role": "assistant",
                        "content": [{"type": "output_text", "text": str(content)}],
                    })
                if tool_calls:
                    for tc in tool_calls:
                        fn = tc.get("function", {})
                        raw_args = fn.get("arguments", "{}")
                        args_str = raw_args if isinstance(raw_args, str) else json.dumps(raw_args)
                        input_items.append({
                            "type": "function_call",
                            "call_id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                            "name": fn.get("name", "unknown"),
                            "arguments": args_str,
                        })
            else:  # "user" or "system"
                if content:
                    prefix = f"[{role.upper()}]\n" if role == "system" else ""
                    input_items.append({
                        "role": "user",
                        "content": [{"type": "input_text", "text": f"{prefix}{content}"}],
                    })

        if not input_items:
            input_items.append({"role": "user", "content": [{"type": "input_text", "text": "Hello"}]})

        payload: dict[str, Any] = {
            "model": self.model or "gpt-5.6-sol",
            "input": input_items,
            "store": False,
            "stream": True,
        }

        if effect.tools:
            tools_payload = []
            for t in effect.tools:
                fn = t.get("function", {})
                if fn:
                    tools_payload.append({
                        "type": "function",
                        "name": fn.get("name"),
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                    })
            if tools_payload:
                payload["tools"] = tools_payload

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

        collected_text: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        prompt_tokens = 0
        completion_tokens = 0

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                for raw_line in resp:
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[5:].strip()
                    if data_str == "[DONE]":
                        break
                    try:
                        event = json.loads(data_str)
                        etype = event.get("type")
                        if etype == "response.output_text.delta":
                            collected_text.append(event.get("delta", ""))
                        elif etype == "response.output_item.done":
                            item = event.get("item", {})
                            if item.get("type") == "function_call":
                                tool_calls.append({
                                    "id": item.get("call_id") or item.get("id"),
                                    "type": "function",
                                    "function": {
                                        "name": item.get("name"),
                                        "arguments": item.get("arguments", "{}"),
                                    },
                                })
                        elif etype in ("response.completed", "response.done", "response.incomplete"):
                            # The Responses API's terminal event is response.completed; output_tokens
                            # already includes reasoning tokens. Waiting for response.done alone meant
                            # every GPT candidate was metered by len(text)/4, minus its tool arguments.
                            res = event.get("response", {})
                            usage_raw = res.get("usage", {}) or {}
                            if usage_raw:
                                prompt_tokens = int(usage_raw.get("input_tokens", 0) or 0)
                                completion_tokens = int(usage_raw.get("output_tokens", 0) or 0)
                    except (json.JSONDecodeError, KeyError, ValueError) as exc:
                        logger.debug("Skipping unparseable SSE line %r: %s", data_str, exc)
                        continue
            output_text = "".join(collected_text).strip()
            return ModelCompleted(
                content=output_text or None,
                tool_calls=tool_calls,
                usage={
                    "prompt_tokens": prompt_tokens or (sum(len(str(m.get("content") or "")) for m in effect.messages) // 4),
                    "completion_tokens": completion_tokens or (
                        (len(output_text) + sum(len(str(tc["function"].get("arguments", ""))) for tc in tool_calls)) // 4
                    ),
                    "estimated": not (prompt_tokens and completion_tokens),
                },
                finish_reason="tool_calls" if tool_calls else "stop",
                seat_id=f"wire:codex:{self.model}",
            )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            retryable = e.code in (401, 403, 429, 500, 502, 503)
            return ModelFailed(
                error=f"Codex wire HTTP {e.code}: {err_body}",
                seat_id=f"wire:codex:{self.model}",
                retryable=retryable,
            )
        except Exception as e:
            return ModelFailed(
                error=f"Codex wire request failed: {str(e)}",
                seat_id=f"wire:codex:{self.model}",
                retryable=True,
            )


# -----------------------------------------------------------------------------
# 3. Direct xAI Grok Wire Provider (SuperGrok Subscription)
# -----------------------------------------------------------------------------

@dataclass
class GrokWireProvider:
    """Direct HTTPS wire caller for xAI Grok using OAuth subscription tokens."""
    access_token: str
    model: str = "grok-4.5"
    timeout: float = 60.0

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        endpoint = "https://api.x.ai/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.access_token}",
            "User-Agent": USER_AGENT,
        }

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": effect.messages,
            "temperature": effect.temperature,
        }
        if effect.tools:
            payload["tools"] = effect.tools
        if effect.max_tokens:
            payload["max_tokens"] = effect.max_tokens

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(endpoint, data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                choice = res.get("choices", [{}])[0]
                message = choice.get("message", {})
                content = message.get("content")
                tool_calls = message.get("tool_calls", [])
                usage = res.get("usage", {})

                return ModelCompleted(
                    content=content,
                    tool_calls=tool_calls,
                    usage=usage,
                    finish_reason=choice.get("finish_reason", "stop"),
                    seat_id=f"wire:grok:{self.model}",
                )
        except urllib.error.HTTPError as e:
            err_body = e.read().decode("utf-8", errors="replace")
            retryable = e.code in (401, 403, 429, 500, 502, 503)
            return ModelFailed(
                error=f"Grok wire HTTP {e.code}: {err_body}",
                seat_id=f"wire:grok:{self.model}",
                retryable=retryable,
            )
        except Exception as e:
            return ModelFailed(
                error=f"Grok wire request failed: {str(e)}",
                seat_id=f"wire:grok:{self.model}",
                retryable=True,
            )


# -----------------------------------------------------------------------------
# 4. Fallback Model Provider (Seamless Wire -> Harness Protection)
# -----------------------------------------------------------------------------

@dataclass
class FallbackModelProvider:
    """Tries a high-speed primary wire provider, automatically falling back to a robust harness."""
    primary: ModelProvider
    fallback: ModelProvider
    name: str = "wire_with_fallback"
    fallback_count: int = 0
    total_calls: int = 0
    last_latency_seconds: float = 0.0
    # Same wire, other accounts. On a quota/rate error (429) the seat rotates here first;
    # the CLI harness is the last resort because it changes the harness axis.
    alternates: list = field(default_factory=list)
    rotations: int = 0

    def call(self, effect: CallModel) -> ModelCompleted | ModelFailed:
        import time
        self.total_calls += 1
        start_t = time.time()

        try:
            result = self.primary.call(effect)
            self.last_latency_seconds = time.time() - start_t
            if isinstance(result, ModelCompleted):
                return result
            error = result.error
        except Exception as e:
            error = str(e)
            self.last_latency_seconds = time.time() - start_t

        if "429" in error or "RESOURCE_EXHAUSTED" in error or "quota" in error.lower():
            for alt in list(self.alternates):
                try:
                    alt_result = alt.call(effect)
                except Exception as e:
                    alt_result = ModelFailed(error=str(e))
                if isinstance(alt_result, ModelCompleted):
                    self.rotations += 1
                    print(f"\033[1;33m[Seat Rotation #{self.rotations}]\033[0m '{getattr(self.primary, 'account_key', 'primary')}' is out of quota; "
                          f"continuing on '{getattr(alt, 'account_key', 'alternate')}'.", file=sys.stderr)
                    self.alternates.remove(alt)
                    self.alternates.append(self.primary)
                    self.primary = alt
                    return alt_result
                error = f"{error} | alternate {getattr(alt, 'account_key', '?')}: {alt_result.error}"

        self.fallback_count += 1
        print(f"\033[1;33m[Seam Fallback #{self.fallback_count}]\033[0m Primary '{getattr(self.primary, 'model', 'wire')}' failed: {error}. Shifting to fallback harness...", file=sys.stderr)
        return self.fallback.call(effect)

# -----------------------------------------------------------------------------
# 5. Master Factory for Wire Providers with Fallback
# -----------------------------------------------------------------------------

def create_wire_model_provider(seat: Any) -> ModelProvider:
    """Create a high-speed Wire Provider with automatic CLI harness fallback."""
    provider = getattr(seat, "provider", "").lower()
    model = getattr(seat, "model", "")
    account = getattr(seat, "account", None)
    harness = getattr(seat, "harness", "arity")
    api_key = getattr(seat, "api_key", None)
    endpoint = getattr(seat, "endpoint", "")

    creds = load_local_oauth_credentials()
    from .auth import TokenStore
    store = TokenStore()

    # 1. Google (Antigravity backend with OMP fallback)
    if provider in ("google", "antigravity"):
        account_key = f"google-antigravity:{account}" if account else "google-antigravity"
        agy_data = creds.get(account_key) or store.get_credential(account_key) or {}
        token = agy_data.get("access")
        proj_id = agy_data.get("projectId") or agy_data.get("project_id", "")
        if token and proj_id:
            wire = AntigravityWireProvider(
                access_token=token,
                project_id=proj_id,
                model=model or "gemini-3.6-flash",
                account_key=account_key,
            )
            # Sibling accounts with their own quota: tried on a 429 before any CLI fallback.
            alternates: list[ModelProvider] = []
            own_email = agy_data.get("email")
            for key, acc in store.get_all_for_provider("google-antigravity"):
                same = key == account_key or (own_email and acc.get("email") == own_email)
                if same or not acc.get("access") or not (acc.get("projectId") or acc.get("project_id")):
                    continue
                alternates.append(AntigravityWireProvider(
                    access_token=acc["access"], project_id=acc.get("projectId") or acc.get("project_id", ""),
                    model=model or "gemini-3.6-flash", account_key=key,
                ))
            fallback_harness = harness if harness in ("omp", "claude", "codex", "grok") else "omp"
            cli = CLIModelProvider(harness=fallback_harness, model=model or "gemini-3.6-flash")
            return FallbackModelProvider(primary=wire, fallback=cli, name=f"google+{fallback_harness}", alternates=alternates)
        return CLIModelProvider(harness="omp", model=model or "gemini-3.6-flash")

    # 2. OpenAI (ChatGPT backend with Codex CLI fallback)
    elif provider in ("openai", "codex", "codex-direct"):
        codex_data = creds.get("openai-codex") or store.get_credential("openai-codex") or {}
        token = codex_data.get("access")
        account_id = codex_data.get("accountId") or codex_data.get("account_id")
        if token and account_id:
            wire = CodexWireProvider(access_token=token, account_id=str(account_id), model=model or "gpt-5.6-sol")
            fallback_harness = harness if harness in ("codex", "omp", "claude") else "codex"
            cli = CLIModelProvider(harness=fallback_harness, model=model or "gpt-5.6-sol")
            return FallbackModelProvider(primary=wire, fallback=cli, name=f"openai+{fallback_harness}")
        return CLIModelProvider(harness="codex", model=model or "gpt-5.6-sol")

    # 3. xAI (Grok backend with Grok build fallback)
    elif provider in ("xai", "grok", "grok-direct"):
        xai_data = creds.get("xai-oauth") or store.get_credential("xai-oauth") or {}
        token = xai_data.get("access")
        if token:
            wire = GrokWireProvider(access_token=token, model=model or "grok-4.5")
            fallback_harness = harness if harness in ("grok", "omp") else "grok"
            cli = CLIModelProvider(harness=fallback_harness, model=model or "grok-4.5")
            return FallbackModelProvider(primary=wire, fallback=cli, name=f"xai+{fallback_harness}")
        return CLIModelProvider(harness="grok", model=model or "grok-4.5")

    # 4. Anthropic (Claude Code harness)
    elif provider in ("anthropic", "claude"):
        return CLIModelProvider(harness="claude", model=model or "claude-3-7-sonnet")

    # 5. Metered API Providers
    elif provider in ("gemini", "google-api"):
        return GeminiModelProvider(api_key=api_key, model=model or "gemini-3.6-flash")
    elif provider == "nvidia":
        return OpenAIModelProvider(
            api_key=api_key,
            base_url=endpoint or "https://integrate.api.nvidia.com/v1",
            model=model or "nvidia/nemotron-3-nano-30b-a3b",
        )

    # Fallback to direct harness or OpenAI completions
    elif provider in ("claude", "codex", "omp", "cli"):
        return CLIModelProvider(harness=provider, model=model or "default")

    return OpenAIModelProvider(
        api_key=api_key,
        base_url=endpoint or "https://api.openai.com/v1",
        model=model or "gpt-4o",
    )
