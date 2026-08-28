"""Where a kernel runs: one real POST per turn, with a for-loop around the tool calls.

Nothing clever on purpose. `start` opens a context, `run` sends it to an OpenAI-shaped
/chat/completions and keeps going until the model stops asking for tools. Swap this file for pi
or Claude Code or a phone pipeline and nothing above it moves.
"""

import json
import pathlib
import time
import urllib.error
import urllib.request

import roles

STATS = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0,
         "tool_calls": 0, "http_errors": 0}
MAX_TOOL_ROUNDS = 8
EFFORT_TOKENS = {"low": 400, "medium": 900, "high": 1800}

class ProviderError(Exception):
    def __init__(self, status, body):
        Exception.__init__(self, "HTTP %s: %s" % (status, str(body)[:400]))
        self.status, self.body = status, body

class QuotaWall(ProviderError):
    pass

def _text(message):
    c = message.get("content")
    if isinstance(c, list):                      # some dialects return content parts
        return "".join(p.get("text", "") for p in c if isinstance(p, dict))
    return c or ""

def post_chat(seat, payload, proxy, timeout=120):
    """The one place bytes leave the machine. The key comes from the proxy, not the kernel."""
    url, last = seat.endpoint + "/chat/completions", None
    body = json.dumps(payload).encode("utf-8")
    for attempt in range(4):
        req = urllib.request.Request(url, data=body, headers=proxy.headers(seat), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                seat.last_headers = dict(r.headers.items())
                data = json.loads(r.read().decode("utf-8"))
            STATS["calls"] += 1
            return data
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            seat.last_headers = dict(e.headers.items()) if e.headers else {}
            STATS["http_errors"] += 1
            last = ProviderError(e.code, raw)
            if e.code == 429:
                seat.recent_429s += 1
                last = QuotaWall(e.code, raw)
                time.sleep(2 + 3 * attempt)
            elif e.code == 400 and "max_completion_tokens" in raw and "max_tokens" in payload:
                payload["max_completion_tokens"] = payload.pop("max_tokens")
                body = json.dumps(payload).encode("utf-8")
            elif 500 <= e.code < 600:
                time.sleep(1 + attempt)
            else:
                raise last
        except (urllib.error.URLError, TimeoutError) as e:
            STATS["http_errors"] += 1
            last = ProviderError(0, repr(e))
            time.sleep(1 + attempt)
    raise last

# The body a role is allowed. list_files takes one useless optional property because some
# compat layers reject an empty parameter schema.
SCHEMAS = {
    "write_file": {"name": "write_file", "parameters": {"type": "object", "required":
                   ["path", "content"], "properties": {
                       "path": {"type": "string", "description": "e.g. brokie/schema.sql"},
                       "content": {"type": "string"}}},
                   "description": "Write a text file in your workspace. Paths are relative."},
    "read_file": {"name": "read_file", "description": "Read a file from your workspace.",
                  "parameters": {"type": "object", "required": ["path"],
                                 "properties": {"path": {"type": "string"}}}},
    "list_files": {"name": "list_files", "description": "List every file in your workspace.",
                   "parameters": {"type": "object", "required": [], "properties": {
                       "subdir": {"type": "string", "description": "optional, ignored"}}}},
    "handoff": {"name": "handoff", "parameters": {"type": "object", "required":
                ["to_role", "want"], "properties": {
                    "to_role": {"type": "string", "description": "e.g. builder"},
                    "want": {"type": "string", "description": "the whole job, in plain words"},
                    "project": {"type": "string", "description": "e.g. brokie"}}},
                "description": "Hand work to another role. Returns what they did."},
}

def tool_specs(role):
    return [{"type": "function", "function": SCHEMAS[t]} for t in sorted(role.tools)
            if t in SCHEMAS]

class Toolbox:
    """Confined to a workspace. Denials are checked against what the kernel *names*, because
    it only ever names relative paths — the absolute one is ours, not its."""

    def __init__(self, role, workspace, handoff_sink=None):
        self.role, self.workspace = role, pathlib.Path(workspace)
        self.handoff_sink, self.log = handoff_sink, []

    def _resolve(self, given):
        roles.enforce(self.role, "path", given)
        p = pathlib.Path(str(given))
        if p.is_absolute() or ".." in p.parts:
            raise roles.Denied("path must be relative and inside the workspace: %r" % given)
        full = (self.workspace / p).resolve()
        if not str(full).startswith(str(self.workspace.resolve())):
            raise roles.Denied("path escapes the workspace: %r" % given)
        return full

    def call(self, name, args):
        roles.enforce(self.role, "tool", name)
        STATS["tool_calls"] += 1
        started = time.time()
        try:
            out, ok = self._dispatch(name, args), True
        except Exception as e:                       # a denial is a result, not a crash
            out, ok = "%s: %s" % (type(e).__name__, e), False
        self.log.append({"tool": name, "args": args, "ok": ok,
                         "result": str(out)[:600], "at": started})
        return out

    def _dispatch(self, name, args):
        if name == "write_file":
            full = self._resolve(args["path"])
            full.parent.mkdir(parents=True, exist_ok=True)
            full.write_text(args.get("content", ""), encoding="utf-8")
            return "wrote %s (%d bytes)" % (args["path"], len(args.get("content", "")))
        if name == "read_file":
            full = self._resolve(args["path"])
            if not full.exists():
                return "no such file: %s" % args["path"]
            return full.read_text(encoding="utf-8")[:4000]
        if name == "list_files":
            got = [str(p.relative_to(self.workspace)).replace("\\", "/")
                   for p in self.workspace.rglob("*") if p.is_file()]
            return "\n".join(sorted(got)) or "(workspace is empty)"
        if name == "handoff":
            return self.handoff_sink(args) if self.handoff_sink else "no handoff route from here"
        return "unknown tool %r" % name

class Handle:
    def __init__(self, seat, system, tools, max_tokens):
        self.seat, self.tools, self.max_tokens = seat, tools, max_tokens
        self.messages = [{"role": "system", "content": system}]
        self.prefix_tokens = 0

class Turn:
    def __init__(self, text, usage, rounds, tool_log):
        self.text, self.usage, self.rounds, self.tool_log = text, usage, rounds, tool_log

class HttpHarness:
    name = "http"

    def start(self, seat, system, role, effort="medium"):
        return Handle(seat, system, tool_specs(role), EFFORT_TOKENS.get(effort, 900))

    def stop(self, handle):
        handle.messages = []

    def run(self, handle, proxy, user_msg, toolbox=None, use_tools=True, max_tokens=None):
        """One turn: a real call, then more real calls while the model asks for tools."""
        handle.messages.append({"role": "user", "content": user_msg})
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0}
        start = len(toolbox.log) if toolbox else 0
        text, rounds, last_prompt = "", 0, 0
        for rounds in range(1, MAX_TOOL_ROUNDS + 1):
            payload = {"model": handle.seat.model, "messages": handle.messages,
                       "max_tokens": max_tokens or handle.max_tokens}
            if use_tools and handle.tools:
                payload["tools"], payload["tool_choice"] = handle.tools, "auto"
            data = post_chat(handle.seat, payload, proxy)
            u = data.get("usage") or {}
            det = u.get("prompt_tokens_details") or {}
            last_prompt = int(u.get("prompt_tokens") or 0)
            for k, v in (("prompt_tokens", last_prompt),
                         ("completion_tokens", int(u.get("completion_tokens") or 0)),
                         ("cached_tokens", int(det.get("cached_tokens") or 0))):
                usage[k] += v
                STATS[k] += v
            msg = (data.get("choices") or [{}])[0].get("message") or {}
            calls, text = msg.get("tool_calls") or [], _text(msg)
            handle.messages.append({"role": "assistant", "content": text or "",
                                    "tool_calls": calls} if calls
                                   else {"role": "assistant", "content": text})
            if not calls or not toolbox:
                break
            for c in calls:
                fn = c.get("function") or {}
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                handle.messages.append(
                    {"role": "tool", "tool_call_id": c.get("id") or fn.get("name", ""),
                     "content": str(toolbox.call(fn.get("name", ""), args))[:6000]})
        # the prefix is what the *last* call had to read, not the sum of the rounds
        handle.prefix_tokens = max(handle.prefix_tokens, last_prompt)
        return Turn(text, usage, rounds, toolbox.log[start:] if toolbox else [])

HARNESSES = {"http": HttpHarness()}
