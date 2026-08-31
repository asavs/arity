"""Arity's legacy v0 prototype, retained in its original single-file location.

Read .wiki/spine.md and .wiki/tier-two.md first; this is those pages made to run.
Every seam from axiom 12 is a class you can swap:

    Store     — where tiers and records live            (JsonlStore: lines on disk)
    Ledger    — seats, quota, presence                   (seeded from env keys)
    Harness   — a for-loop around POST /chat/completions (the only kind there is)
    Transport — how a channel reaches a human            (ConsoleTransport prints)

No fakes. Every kernel turn is a real call to whatever seat the caster picked.
The ``.gorkbot`` state directory remains a compatibility surface for now. Run
``python arity.py demo`` to play S1, S3, S7, S36, S39 against available seats.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import statistics
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Optional

# ----------------------------------------------------------------------------- clock, ids

class Clock:
    """Injectable so the pulse and tests can move time without waiting for it."""
    def __init__(self, offset: float = 0.0): self.offset = offset
    def now(self) -> float: return time.time() + self.offset
    def advance(self, seconds: float) -> None: self.offset += seconds

def new_id(prefix: str = "") -> str:
    return prefix + uuid.uuid4().hex[:10]

def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

# ----------------------------------------------------------------------------- store seam

class Store:
    """Append-only records by kind. Tiers, entries, scorecard rows, friction — all land here."""
    def append(self, kind: str, rec: dict) -> None: raise NotImplementedError
    def query(self, kind: str, **match) -> list[dict]: raise NotImplementedError

class JsonlStore(Store):
    def __init__(self, root: Path):
        self.root = Path(root); self.root.mkdir(parents=True, exist_ok=True)
    def _path(self, kind: str) -> Path: return self.root / f"{kind}.jsonl"
    def append(self, kind: str, rec: dict) -> None:
        with self._path(kind).open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
    def query(self, kind: str, **match) -> list[dict]:
        p = self._path(kind)
        if not p.exists(): return []
        out = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip(): continue
            r = json.loads(line)
            if all(r.get(k) == v for k, v in match.items()): out.append(r)
        return out

# ----------------------------------------------------------------------------- axiom 7 table

# window_s: assured warm window (0 = opportunistic, design for a miss)
# read_x / write_x: cache read / write price as a multiple of normal input
# price_in: dollars per million input tokens (reference unit for quota cost, axiom 7)
CACHE_TABLE: dict[str, dict] = {
    "anthropic": dict(window_s=300,  read_x=0.10, write_x=1.25, storage_per_M_hr=0.0,  price_in=10.0),
    "openai":    dict(window_s=1800, read_x=0.10, write_x=1.25, storage_per_M_hr=0.0,  price_in=4.0),
    "gemini":    dict(window_s=0,    read_x=0.10, write_x=1.00, storage_per_M_hr=4.5,  price_in=2.0),
    "xai":       dict(window_s=0,    read_x=0.25, write_x=1.00, storage_per_M_hr=0.0,  price_in=2.0),
    "nim":       dict(window_s=0,    read_x=1.00, write_x=1.00, storage_per_M_hr=0.0,  price_in=0.0),
}

def cold_cost(k: "Kernel", clock: Clock) -> dict:
    """What it costs if this kernel dies right now vs. stays warm. A lookup and a multiply."""
    t = CACHE_TABLE[k.seat.provider]
    m = k.prefix_tokens / 1e6
    cold = m * t["price_in"]
    warm = cold * t["read_x"]
    if clock.now() > k.cache_expires_at: warm = cold          # already cold; no discount
    return dict(cold=round(cold, 4), warm=round(warm, 4), penalty=round(cold - warm, 4),
                expires_in=round(k.cache_expires_at - clock.now()))

# ----------------------------------------------------------------------------- seats & ledger

@dataclass
class Seat:
    id: str
    provider: str            # key into CACHE_TABLE
    endpoint: str            # OpenAI-compatible base URL
    key_env: str             # env var holding the key; the kernel never sees the key
    models: list[str]
    kind: str = "api"        # 'api' | 'quota'
    remaining: float = 1.0   # fraction of allowance left (guess)
    reset_at: float = float("inf")
    expires_at: float = float("inf")
    cache_boundary: str = ""
    presence: bool = False   # a human is live on this seat right now
    confidence: float = 0.3
    source: str = "seed"
    last_headers: dict = field(default_factory=dict)
    spent_tokens: int = 0

    def key(self) -> str: return os.environ.get(self.key_env, "")
    def dies_at(self) -> float: return min(self.reset_at, self.expires_at)

class Ledger:
    """Every account as a row. Always a guess with a confidence, never a fact."""
    def __init__(self, clock: Clock, store: Store):
        self.clock, self.store, self.seats = clock, store, {}   # type: ignore
        self.probes: list[Callable[[Seat], Optional[dict]]] = [self._probe_headers, self._probe_presence]

    def add(self, seat: Seat) -> Seat:
        self.seats[seat.id] = seat; return seat

    @classmethod
    def from_env(cls, clock: Clock, store: Store) -> "Ledger":
        L = cls(clock, store)
        if os.environ.get("GEMINI_API_KEY"):
            L.add(Seat("gemini-main", "gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                       "GEMINI_API_KEY", ["gemini-3.6-flash", "gemini-3.5-flash-lite"], kind="quota",
                       reset_at=clock.now() + 86400, cache_boundary="gemini:project-main"))
        if os.environ.get("NVIDIA_NIM_API_KEY"):
            L.add(Seat("nim-main", "nim", "https://integrate.api.nvidia.com/v1",
                       "NVIDIA_NIM_API_KEY", ["nvidia/nemotron-3-nano-30b-a3b"], kind="api",
                       cache_boundary="nim:main"))
        if os.environ.get("OPENROUTER_API_KEY"):
            L.add(Seat("openrouter", "openai", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY",
                       ["openai/gpt-5.6-terra"], cache_boundary="openrouter:main"))
        return L

    # -- probes: each returns an observation dict or None; best confidence wins
    def _probe_headers(self, s: Seat) -> Optional[dict]:
        h = {k.lower(): v for k, v in s.last_headers.items()}
        rem = next((v for k, v in h.items() if "ratelimit-remaining" in k), None)
        reset = next((v for k, v in h.items() if "ratelimit-reset" in k), None)
        if rem is None: return None
        obs = dict(confidence=0.7, source="headers")
        try: obs["remaining_raw"] = float(rem)
        except ValueError: pass
        if reset:
            try: obs["reset_at"] = self.clock.now() + float(reset)
            except ValueError: pass
        return obs

    def _probe_presence(self, s: Seat) -> Optional[dict]:
        """S36: is a human session on this provider being written right now?"""
        live = False
        root = Path.home() / ".claude" / "projects"
        if s.provider == "anthropic" and root.exists():
            cutoff = time.time() - 60
            live = any(p.stat().st_mtime > cutoff for p in root.glob("*/*.jsonl"))
        return dict(presence=live, confidence=0.9, source="session-files")

    def probe(self, s: Seat) -> Seat:
        obs = [o for o in (p(s) for p in self.probes) if o]
        for o in sorted(obs, key=lambda o: o["confidence"]):
            for k, v in o.items():
                if k in ("confidence", "source"): continue
                setattr(s, k, v) if hasattr(s, k) else None
            s.confidence, s.source = o["confidence"], o["source"]
        self.store.append("ledger", dict(at=self.clock.now(), seat=s.id, remaining=s.remaining,
                                         presence=s.presence, confidence=s.confidence, source=s.source))
        return s

    def seats_for(self, models: list[str]) -> list[tuple[Seat, str]]:
        out = []
        for m in models:
            for s in self.seats.values():
                if m in s.models and s.key(): out.append((s, m))
        return out

    def reserve(self, s: Seat, tokens: int) -> bool:
        """Hold quota for a purpose (the report turn). v0: refuse only when we know it's gone."""
        return s.remaining > 0.0

    def meter(self, s: Seat, usage: dict) -> None:
        n = int(usage.get("total_tokens") or 0)
        s.spent_tokens += n
        if s.kind == "quota": s.remaining = max(0.0, s.remaining - n / 2_000_000)   # crude allowance model
        self.store.append("spend", dict(at=self.clock.now(), seat=s.id, **{k: usage.get(k) for k in
                          ("prompt_tokens", "completion_tokens", "total_tokens")}))

# ----------------------------------------------------------------------------- roles (denial sets)

class Denied(Exception): pass

@dataclass
class Role:
    name: str
    tier: int                       # 0 knows Asa, 1 knows the project, 2 knows the task
    allow_tools: set[str]
    allow_channels: set[str]
    deny_paths: list[str] = field(default_factory=list)
    deny_names: list[str] = field(default_factory=list)
    deny_hosts: list[str] = field(default_factory=list)
    aptitude: list[str] = field(default_factory=list)   # what it wants in a model
    hand_to: set[str] = field(default_factory=set)
    public: bool = False
    persona: str = ""

class Roles:
    def __init__(self): self._r: dict[str, Role] = {}
    def add(self, r: Role) -> Role: self._r[r.name] = r; return r
    def get(self, name: str) -> Role: return self._r[name]
    def enforce(self, role: Role, action: str, target: str = "") -> None:
        """Raise unless the role's allow set covers the action. Nothing is 'ask'."""
        if action == "tool" and target not in role.allow_tools: raise Denied(f"{role.name} may not use tool {target}")
        if action == "post" and target not in role.allow_channels: raise Denied(f"{role.name} may not post to {target}")
        if action == "path" and any(target.replace("\\", "/").lower().startswith(p.lower()) for p in role.deny_paths):
            raise Denied(f"{role.name} may not touch {target}")
        if action == "host" and any(h in target for h in role.deny_hosts): raise Denied(f"{role.name} may not reach {target}")
        if action == "hand" and target not in role.hand_to: raise Denied(f"{role.name} may not hand to {target}")

# ----------------------------------------------------------------------------- tiers (memory)

UNIVERSAL_FACTS = (
    "You are one kernel in a staff. You hold a role for a period. You will be visited; you are not told when you end. "
    "Do the task, use only the tools you have, and keep replies short. If you cannot proceed, say so plainly."
)

class BriefLeak(Exception): pass

class Tiers:
    """The compiler that decides what a kernel knows (axiom 8)."""
    def __init__(self, store: Store, clock: Clock, biograph: str = "", projects: dict[str, str] | None = None):
        self.store, self.clock, self.biograph, self.projects = store, clock, biograph, projects or {}

    def write(self, tier: int, kind: str, body: Any, by: str, project: str = "") -> dict:
        rec = dict(at=self.clock.now(), tier=tier, kind=kind, by=by, project=project, body=body)
        self.store.append("tiers", rec); return rec

    def retrieve(self, tier: int, project: str = "", kind: str = "", n: int = 5) -> list[dict]:
        rows = [r for r in self.store.query("tiers") if r["tier"] == tier
                and (not project or r["project"] == project) and (not kind or r["kind"] == kind)]
        return rows[-n:]

    def redact(self, text: str, deny_paths: list[str], deny_names: list[str]) -> str:
        for p in deny_paths: text = text.replace(p, "<redacted-path>")
        for n in deny_names: text = re.sub(re.escape(n), "<redacted>", text, flags=re.I)
        return text

    def assemble(self, role: Role, task: dict, predecessor: Optional[dict] = None) -> str:
        parts = [UNIVERSAL_FACTS, f"Date: {time.strftime('%Y-%m-%d %H:%M', time.localtime(self.clock.now()))}."]
        if role.persona: parts.append(role.persona)
        if role.hand_to: parts.append("Staff you may hand work to, by exact name: " + ", ".join(sorted(role.hand_to)) + ".")
        if role.tier <= 0 and self.biograph: parts.append("About the person you serve:\n" + self.biograph)
        if role.tier <= 1 and task.get("project") in self.projects:
            parts.append(f"Project {task['project']}:\n" + self.projects[task["project"]])
            for r in self.retrieve(1, task["project"], n=3):
                parts.append(f"[{r['kind']} by {r['by']}] {json.dumps(r['body'], default=str)[:600]}")
        parts.append("Task:\n" + task["want"])
        if predecessor:
            own = predecessor.get("own_report") or "ABSENT"
            parts.append("Your predecessor's own report:\n" + json.dumps(own, default=str)[:800])
            if predecessor.get("entry"): parts.append("The archivist's entry on it:\n" + json.dumps(predecessor["entry"], default=str)[:800])
        brief = "\n\n".join(parts)
        for p in role.deny_paths:
            if p.lower() in brief.lower(): raise BriefLeak(f"brief for {role.name} contains denied path {p}")
        for n in role.deny_names:
            if re.search(re.escape(n), brief, re.I): raise BriefLeak(f"brief for {role.name} contains denied name {n}")
        return brief

# ----------------------------------------------------------------------------- cadence

PRIOR_GAP = {"call": 5, "dm": 1500, "project": 7200, "public": 3600}

class Cadence:
    """How long until the next message, probably. A median and a late-night discount."""
    def __init__(self, clock: Clock): self.clock = clock
    def predict(self, convo: "Convo") -> dict:
        gaps = convo.gaps[-8:] or [PRIOR_GAP.get(convo.kind, 1500)]
        med = statistics.median(gaps)
        hour = time.localtime(self.clock.now()).tm_hour
        if hour >= 23 or hour < 7: med *= 3
        def p_return_before(t: float) -> float:
            horizon = t - self.clock.now()
            return sum(1 for g in gaps if g <= horizon) / len(gaps)
        return dict(p50=med, p_return_before=p_return_before)

@dataclass
class Convo:
    id: str
    kind: str = "dm"
    last_at: float = 0.0
    gaps: list[float] = field(default_factory=list)
    def touch(self, now: float) -> None:
        if self.last_at: self.gaps.append(now - self.last_at)
        self.last_at = now

# ----------------------------------------------------------------------------- scorecard & standing

class Scorecard:
    """Who should hold a role, in what order, and why. Standing is the part that remembers being wrong."""
    def __init__(self, store: Store, clock: Clock):
        self.store, self.clock = store, clock
        self.standing: dict[tuple, float] = {}

    def record(self, role: str, model: str, task_class: str, metrics: dict) -> None:
        self.store.append("scorecard", dict(at=self.clock.now(), role=role, model=model, task_class=task_class, **metrics))
        key = (role, model, task_class)
        if metrics.get("caught_wrong"): self.standing[key] = self.standing.get(key, 1.0) * 0.6
        elif metrics.get("ok"): self.standing[key] = min(1.0, self.standing.get(key, 1.0) + 0.1)

    def rank(self, role: Role, task_class: str, known_models: list[str]) -> list[dict]:
        rows = self.store.query("scorecard", role=role.name, task_class=task_class)
        scored = []
        for m in known_models:
            mine = [r for r in rows if r["model"] == m]
            if mine:
                q = statistics.mean(r.get("quality", 0.5) for r in mine)
                rel = statistics.mean(1.0 if r.get("ok") else 0.0 for r in mine)
                s, reason = 0.7 * q + 0.3 * rel, f"quality {q:.2f}, reliability {rel:.2f}, n={len(mine)}"
            else:
                s, reason = 0.5, "no evidence yet"
            s *= self.standing.get((role.name, m, task_class), 1.0)
            scored.append((m, s, reason))
        scored.sort(key=lambda x: -x[1])
        top = scored[0][1] if scored else 1.0
        return [dict(model=m, confidence=round(s / top, 2), reason=r) for m, s, r in scored]

# ----------------------------------------------------------------------------- harness: the loop

TOOL_SCHEMAS = {
    "read_file":  dict(description="Read a text file.", parameters=dict(type="object", properties=dict(path=dict(type="string")), required=["path"])),
    "write_file": dict(description="Write a text file (creates or overwrites).", parameters=dict(type="object", properties=dict(path=dict(type="string"), content=dict(type="string")), required=["path", "content"])),
    "bash":       dict(description="Run a shell command in the workspace.", parameters=dict(type="object", properties=dict(command=dict(type="string")), required=["command"])),
    "post":       dict(description="Post a message to a channel you are a member of.", parameters=dict(type="object", properties=dict(channel=dict(type="string"), text=dict(type="string")), required=["channel", "text"])),
    "handoff":    dict(description="Hand a task to another role as a structured record.", parameters=dict(type="object", properties=dict(to_role=dict(type="string"), want=dict(type="string"), evidence=dict(type="string")), required=["to_role", "want"])),
}

class ToolBox:
    """Executes tool calls for a kernel, checking every one against its role's denials."""
    def __init__(self, system: "System", k: "Kernel"): self.sys, self.k = system, k

    def schemas(self) -> list[dict]:
        out = []
        for n in sorted(self.k.role.allow_tools):
            spec = json.loads(json.dumps(TOOL_SCHEMAS[n]))
            if n == "handoff":   # the model may only name roles it is allowed to hand to
                spec["parameters"]["properties"]["to_role"]["enum"] = sorted(self.k.role.hand_to)
            if n == "post":
                spec["parameters"]["properties"]["channel"]["enum"] = sorted(self.k.role.allow_channels)
            out.append(dict(type="function", function=dict(name=n, **spec)))
        return out

    def call(self, name: str, args: dict) -> str:
        roles, k = self.sys.roles, self.k
        roles.enforce(k.role, "tool", name)
        ws = self.sys.workspace
        if name == "read_file":
            roles.enforce(k.role, "path", args["path"])
            p = (ws / args["path"]) if not Path(args["path"]).is_absolute() else Path(args["path"])
            return p.read_text(encoding="utf-8")[:8000] if p.exists() else f"no such file: {args['path']}"
        if name == "write_file":
            roles.enforce(k.role, "path", args["path"])
            p = ws / args["path"]; p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(args["content"], encoding="utf-8"); return f"wrote {p.relative_to(ws)} ({len(args['content'])} chars)"
        if name == "bash":
            for h in k.role.deny_hosts:
                if h in args["command"]: raise Denied(f"{k.role.name} may not reach {h}")
            r = subprocess.run(args["command"], shell=True, cwd=ws, capture_output=True, text=True, timeout=60)
            return (r.stdout + r.stderr)[-4000:] or f"(exit {r.returncode})"
        if name == "post":
            return self.sys.redphone.post(args["channel"], Message(sender=k.role.name, kind="text", body=args["text"]), by_kernel=k)
        if name == "handoff":
            roles.enforce(k.role, "hand", args["to_role"])
            rec = dict(**{"from": k.role.name}, to_role=args["to_role"], want=args["want"], evidence=args.get("evidence", ""),
                       project=k.task.get("project", ""), budget=k.task.get("budget", 20000) // 2, depth=k.task.get("depth", 0) + 1)
            return self.sys.redphone.handoff(k, rec)
        raise Denied(f"unknown tool {name}")

class Harness:
    """A for-loop around POST /chat/completions. That is all a harness is."""
    def __init__(self, ledger: Ledger, max_steps: int = 8, timeout: int = 120):
        self.ledger, self.max_steps, self.timeout = ledger, max_steps, timeout

    def _call(self, seat: Seat, model: str, messages: list[dict], tools: list[dict], effort: str) -> tuple[dict, dict]:
        body: dict = dict(model=model, messages=messages, temperature=0.2, max_tokens=800 if effort == "low" else 1600)
        if tools: body["tools"] = tools
        req = urllib.request.Request(seat.endpoint.rstrip("/") + "/chat/completions", data=json.dumps(body).encode(),
                                     headers={"Authorization": f"Bearer {seat.key()}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                seat.last_headers = dict(r.headers.items()); return json.load(r), seat.last_headers
        except urllib.error.HTTPError as e:
            seat.last_headers = dict(e.headers.items()); err = e.read().decode(errors="replace")[:400]
            raise RuntimeError(f"{seat.provider} {e.code}: {err}")

    def run(self, k: "Kernel", user_msg: str, toolbox: Optional[ToolBox], effort: str = "low") -> str:
        k.messages.append(dict(role="user", content=user_msg))
        tools = toolbox.schemas() if toolbox else []
        for step in range(self.max_steps):
            resp, _ = self._call(k.seat, k.model, k.messages, tools, effort)
            usage = resp.get("usage") or {}
            self.ledger.meter(k.seat, usage)
            k.prefix_tokens = int(usage.get("prompt_tokens") or k.prefix_tokens)
            msg = resp["choices"][0]["message"]
            k.messages.append({kk: v for kk, v in msg.items() if kk in ("role", "content", "tool_calls")})
            calls = msg.get("tool_calls") or []
            if not calls or not toolbox: return (msg.get("content") or "").strip()
            for c in calls:
                name = c["function"]["name"]
                try: args = json.loads(c["function"].get("arguments") or "{}")
                except json.JSONDecodeError: args = {}
                try: out = toolbox.call(name, args)
                except Denied as d: out = f"DENIED: {d}"
                except Exception as e: out = f"ERROR: {e}"
                k.tool_log.append(dict(step=step, tool=name, args=args, out=str(out)[:300]))
                k.messages.append(dict(role="tool", tool_call_id=c.get("id", new_id("call_")), content=str(out)[:4000]))
        return "(stopped: too many tool steps)"

# ----------------------------------------------------------------------------- kernels

@dataclass
class Kernel:
    id: str
    role: Role
    seat: Seat
    model: str
    task: dict
    convo: Optional[Convo]
    identity: tuple
    born_at: float
    cache_expires_at: float
    effort: str = "low"
    state: str = "alive"
    prefix_tokens: int = 0
    messages: list[dict] = field(default_factory=list)
    tool_log: list[dict] = field(default_factory=list)
    open_promise: bool = False

class Registry:
    def __init__(self): self.live: dict[str, Kernel] = {}
    def add(self, k: Kernel) -> None: self.live[k.id] = k
    def remove(self, k: Kernel) -> None: self.live.pop(k.id, None)
    def holder(self, role: str, convo_id: str = "") -> Optional[Kernel]:
        for k in self.live.values():
            if k.role.name == role and (not convo_id or (k.convo and k.convo.id == convo_id)): return k
        return None
    def warm(self) -> list[Kernel]: return [k for k in self.live.values() if k.state == "alive"]

REPORT_PROMPT = ("You're being visited one last time. No tools. In a few lines: what you were doing, what you "
                 "believe you changed and why (start each change with 'changed:'), what's open, the last thing you "
                 "know is safe, and one piece of advice for whoever picks this up.")

class Runtime:
    """spawn / turn / die — a model gets a body for a period (definitions; axioms 7, 9)."""
    def __init__(self, system: "System"): self.sys = system

    def spawn(self, seat: Seat, model: str, role: Role, brief: str, effort: str, task: dict, convo: Optional[Convo]) -> Kernel:
        now = self.sys.clock.now()
        window = CACHE_TABLE[seat.provider]["window_s"]
        k = Kernel(id=new_id("k_"), role=role, seat=seat, model=model, task=task, convo=convo,
                   identity=(seat.provider, seat.endpoint, model, seat.cache_boundary, convo.id if convo else "none", sha(brief)),
                   born_at=now, cache_expires_at=now + (window or 600), effort=effort,
                   messages=[dict(role="system", content=brief)])
        self.sys.registry.add(k)
        self.sys.log(f"spawn {k.id} role={role.name} model={model} seat={seat.id} effort={effort}")
        return k

    def turn(self, k: Kernel, msg: str, tools: bool = True) -> str:
        if k.state != "alive": raise RuntimeError(f"{k.id} is {k.state}")
        out = self.sys.harness.run(k, msg, ToolBox(self.sys, k) if tools else None, k.effort)
        k.cache_expires_at = self.sys.clock.now() + (CACHE_TABLE[k.seat.provider]["window_s"] or 600)   # reads refresh
        if k.convo: k.convo.touch(self.sys.clock.now())
        return out

    def write_report(self, k: Kernel, reason: str) -> Optional[dict]:
        if not self.sys.ledger.reserve(k.seat, 400): return None
        try: text = self.sys.harness.run(k, REPORT_PROMPT, None, "low")
        except Exception as e: return dict(partial=True, error=str(e))
        changes = [ln.split("changed:", 1)[1].strip() for ln in text.splitlines() if "changed:" in ln.lower()]
        return dict(kernel=k.id, identity=list(k.identity), trigger=reason, body=text, believed_changes=changes)

    def die(self, k: Kernel, reason: str) -> dict:
        k.state = "dying"
        report = self.write_report(k, reason)
        env = dict(kernel=k.id, role=k.role.name, model=k.model, seat=k.seat.id, identity=list(k.identity), task=k.task,
                   transcript=[m for m in k.messages if m.get("role") != "system"], tool_log=k.tool_log,
                   born_at=k.born_at, died_at=self.sys.clock.now(), ended_by=reason, open_promise=k.open_promise,
                   prefix_tokens=k.prefix_tokens)
        self.sys.registry.remove(k); k.state = "dead"
        self.sys.tiers.write(k.role.tier, "own_report", report or dict(ABSENT=reason), by=k.id, project=k.task.get("project", ""))
        self.sys.log(f"die {k.id} reason={reason} report={'yes' if report else 'ABSENT'}")
        entry = self.sys.archivist.write_entry(env, report, reason)
        return dict(report=report, entry=entry)

# ----------------------------------------------------------------------------- archivist

class Archivist:
    """Reads each dead kernel, checks claims against artifacts, writes the impartial entry. Records; never polices."""
    def __init__(self, system: "System"): self.sys = system

    def write_entry(self, env: dict, report: Optional[dict], reason: str) -> dict:
        claims = (report or {}).get("believed_changes") or []
        hay = " ".join(json.dumps(t, default=str) for t in env["tool_log"]).lower()
        checked = []
        for c in claims:
            words = [w for w in re.findall(r"[a-z0-9_./-]{4,}", c.lower())]
            hit = any(w in hay for w in words)
            checked.append(dict(claim=c, verified=hit))
        flags = [] if report else [f"REPORT_ABSENT: {reason}"]
        if env.get("open_promise"): flags.append("orphaned")
        if report and report.get("partial"): flags.append("REPORT_PARTIAL")
        n_tools = len(env["tool_log"])
        summary = (f"{env['role']} kernel {env['kernel']} on {env['model']} ran {n_tools} tool call(s) and ended by {reason}. "
                   f"{sum(c['verified'] for c in checked)}/{len(checked)} claimed changes are supported by the tool log.")
        entry = dict(kernel=env["kernel"], at=self.sys.clock.now(), summary=summary, changes=checked, flags=flags,
                     sources=dict(tool_calls=n_tools, transcript_turns=len(env["transcript"])))
        self.sys.tiers.write(self.sys.roles.get(env["role"]).tier, "entry", entry, by="archivist", project=env["task"].get("project", ""))
        self.sys.store.append("envelopes", env)
        # close the loop: what the archivist found is evidence for casting (axiom 3; standing goes down when caught wrong)
        verified = sum(c["verified"] for c in checked)
        self.sys.scorecard.record(env["role"], env["model"], env["task"].get("task_class", "general"),
                                  dict(ok=bool(report) and not any(f.startswith("REPORT") for f in flags),
                                       quality=(verified / len(checked)) if checked else 0.5,
                                       caught_wrong=bool(checked) and verified == 0 and n_tools == 0))
        self.sys.log(f"archivist entry for {env['kernel']}: {summary}")
        return entry

# ----------------------------------------------------------------------------- red phone (channels)

@dataclass
class Message:
    sender: str
    kind: str          # text | handoff | friction | entry | keepalive
    body: Any
    at: float = 0.0
    id: str = field(default_factory=lambda: new_id("m_"))

@dataclass
class Channel:
    id: str
    visibility: str                 # public | private
    members: set[str]               # 'asa' or role names
    convo: Convo

class Transport:
    def egress(self, channel: Channel, msg: Message) -> None: raise NotImplementedError

class ConsoleTransport(Transport):
    def egress(self, channel: Channel, msg: Message) -> None:
        body = msg.body if isinstance(msg.body, str) else json.dumps(msg.body, default=str)
        print(f"  [{channel.id}] {msg.sender}: {body[:400]}")

class RedPhone:
    """Channels, public and private, whose members are humans and bots. The spine."""
    def __init__(self, system: "System", transports: list[Transport]):
        self.sys, self.transports, self.channels = system, transports, {}   # type: ignore

    def channel(self, id: str, visibility: str, members: set[str], kind: str = "dm") -> Channel:
        ch = Channel(id, visibility, set(members), Convo(id, kind)); self.channels[id] = ch; return ch

    def post(self, channel_id: str, msg: Message, by_kernel: Optional[Kernel] = None, deliver: bool = True) -> str:
        ch = self.channels[channel_id]
        if by_kernel: self.sys.roles.enforce(by_kernel.role, "post", channel_id)
        msg.at = self.sys.clock.now()
        self.sys.store.append("channels", dict(channel=channel_id, **asdict(msg)))
        if "asa" in ch.members or ch.visibility == "public":
            for t in self.transports: t.egress(ch, msg)
        if deliver:
            for m in ch.members:
                if m != "asa" and m != msg.sender and msg.kind in ("text", "handoff", "keepalive"):
                    self.sys.address(m, ch, msg)
        return msg.id

    def dm(self, role: str, text: str) -> str:
        ch_id = f"dm:{role}"
        if ch_id not in self.channels: self.channel(ch_id, "private", {"asa", role}, "dm")
        return self.post(ch_id, Message(sender="asa", kind="text", body=text))

    def handoff(self, from_k: Kernel, rec: dict) -> str:
        if rec["depth"] > 3 or rec["budget"] <= 0: return "REFUSED: depth or budget exhausted"
        ch_id = f"project:{rec['project']}" if rec.get("project") else f"dm:{rec['to_role']}"
        if ch_id not in self.channels: self.channel(ch_id, "private", {"asa", rec["from"], rec["to_role"]}, "project")
        self.channels[ch_id].members.add(rec["to_role"])
        # 'hand' was already enforced by the toolbox; a handoff record may land in the receiver's channel
        # even when the sender may not chat there. That's the difference between a record and a message.
        mid = self.post(ch_id, Message(sender=rec["from"], kind="handoff", body=rec), by_kernel=None)
        return f"handed off as {mid}"

# ----------------------------------------------------------------------------- casting

class Caster:
    """Per prompt: which model, which seat, what effort (axioms 3, 7)."""
    def __init__(self, system: "System"): self.sys = system

    def cast(self, task: dict, role: Role, convo: Optional[Convo]) -> Kernel:
        s = self.sys
        if convo:
            k = s.registry.holder(role.name, convo.id)
            if k and cold_cost(k, s.clock)["penalty"] > 0:
                s.log(f"cast: keep warm {k.id} (penalty ${cold_cost(k, s.clock)['penalty']})"); return k
        gap = s.cadence.predict(convo)["p50"] if convo else 0
        known = sorted({m for st in s.ledger.seats.values() for m in st.models})
        ranked = s.scorecard.rank(role, task.get("task_class", "general"), known)
        cands = s.ledger.seats_for([r["model"] for r in ranked])
        before = len(cands)
        cands = [(st, m) for st, m in cands if not st.presence]                                   # S36
        dropped_presence = before - len(cands)
        cands = [(st, m) for st, m in cands if CACHE_TABLE[st.provider]["window_s"] >= gap or CACHE_TABLE[st.provider]["window_s"] == 0]   # S3
        cands = [(st, m) for st, m in cands if st.remaining > 0]
        order = {r["model"]: i for i, r in enumerate(ranked)}
        cands.sort(key=lambda sm: (order[sm[1]], sm[0].dies_at()))                                   # rank, then dies soonest
        if not cands: raise RuntimeError(f"no seat for {role.name} (dropped {dropped_presence} for presence)")
        seat, model = cands[0]
        effort = "high" if task.get("stakes") == "high" else "low"
        why = next(r["reason"] for r in ranked if r["model"] == model)
        s.log(f"cast: {role.name} -> {model} on {seat.id} (gap p50={gap:.0f}s, presence-dropped={dropped_presence}, because {why})")
        brief = s.tiers.assemble(role, task, task.get("predecessor"))
        return s.runtime.spawn(seat, model, role, brief, effort, task, convo)

# ----------------------------------------------------------------------------- pulse

KEEPALIVE = "hi luv u"

class Pulse:
    """The heartbeat (axiom 11). Keepalive while a return is likelier than not; else let it go cold."""
    def __init__(self, system: "System"): self.sys = system

    def tick(self) -> list[str]:
        s, acted = self.sys, []
        for st in list(s.ledger.seats.values()): s.ledger.probe(st)
        for k in s.registry.warm():
            if not k.convo: continue
            cc = cold_cost(k, s.clock)
            p = s.cadence.predict(k.convo)["p_return_before"](k.cache_expires_at)
            ping_cost = cc["warm"]
            if p * cc["cold"] > ping_cost and cc["expires_in"] < 120:
                try:
                    reply = s.runtime.turn(k, KEEPALIVE, tools=False)
                    s.redphone.post(k.convo.id, Message(sender="pulse", kind="keepalive", body=KEEPALIVE), deliver=False)
                    acted.append(f"keepalive {k.id} (p={p:.2f}, reply={reply[:40]!r})")
                except Exception as e: acted.append(f"keepalive failed {k.id}: {e}")
            elif cc["expires_in"] < 0 or p == 0:
                s.runtime.die(k, "went quiet"); acted.append(f"let go {k.id} (p={p:.2f})")
        for a in acted: s.log("pulse: " + a)
        return acted

# ----------------------------------------------------------------------------- system wiring

class System:
    def __init__(self, root: Path, biograph: str = "", projects: dict[str, str] | None = None, verbose: bool = True):
        self.root = Path(root); self.workspace = self.root / "workspace"; self.workspace.mkdir(parents=True, exist_ok=True)
        self.verbose = verbose
        self.clock = Clock()
        self.store = JsonlStore(self.root / "store")
        self.ledger = Ledger.from_env(self.clock, self.store)
        self.roles = Roles()
        self.tiers = Tiers(self.store, self.clock, biograph, projects)
        self.cadence = Cadence(self.clock)
        self.scorecard = Scorecard(self.store, self.clock)
        self.harness = Harness(self.ledger)
        self.registry = Registry()
        self.runtime = Runtime(self)
        self.archivist = Archivist(self)
        self.redphone = RedPhone(self, [ConsoleTransport()])
        self.caster = Caster(self)
        self.pulse = Pulse(self)

    def log(self, line: str) -> None:
        self.store.append("log", dict(at=self.clock.now(), line=line))
        if self.verbose: print("    · " + line)

    def address(self, role_name: str, ch: Channel, msg: Message) -> None:
        """A message landed in a channel a role is a member of: the holder (or a fresh kernel) takes a turn."""
        role = self.roles.get(role_name)
        if msg.kind == "handoff":
            rec = msg.body
            task = dict(want=rec["want"], project=rec.get("project", ""), budget=rec["budget"], depth=rec["depth"], task_class="handoff")
            k = self.caster.cast(task, role, ch.convo)
            try:
                out = self.runtime.turn(k, f"Handoff from {rec['from']}: {rec['want']}\nEvidence: {rec.get('evidence','')}")
                # the answer to a record goes back where the record was, as a record — not as chat
                self.redphone.post(ch.id, Message(sender=role_name, kind="text", body=out), by_kernel=None, deliver=False)
            finally:
                if k.state == "alive": self.runtime.die(k, "task done")
            return
        body = msg.body if isinstance(msg.body, str) else json.dumps(msg.body)
        task = dict(want=body, project=ch.id.split(":", 1)[1] if ch.id.startswith("project:") else "", task_class=ch.convo.kind)
        k = self.caster.cast(task, role, ch.convo)
        out = self.runtime.turn(k, body)
        self.redphone.post(ch.id, Message(sender=role_name, kind="text", body=out), by_kernel=k, deliver=False)

# ----------------------------------------------------------------------------- default roster

def default_roles(R: Roles, home: str) -> None:
    R.add(Role("voice", tier=0, allow_tools={"handoff", "post"}, allow_channels={"dm:voice", "project:brokie"},
               hand_to={"builder", "librarian"}, aptitude=["judgment", "warmth", "brevity"],
               persona="You are the Voice: the one person Asa talks to. Understand what he meant. Answer directly if you can in one or two sentences; "
                       "otherwise hand the work to a role with the handoff tool and tell him in one sentence that it's on its way. Never do deep work yourself."))
    R.add(Role("builder", tier=2, allow_tools={"read_file", "write_file", "bash", "post"}, allow_channels={"project:brokie", "friction"},
               deny_paths=[home], deny_names=["asa"], deny_hosts=["pythia"], aptitude=["cheap", "fast", "tool-heavy"],
               persona="You are a builder. Do exactly the task in the workspace with the tools you have, then reply with what you changed in two lines."))
    R.add(Role("librarian", tier=1, allow_tools={"read_file", "post"}, allow_channels={"project:brokie"},
               deny_paths=[home], deny_names=["asa"], aptitude=["citation discipline", "long context"],
               persona="You are the librarian. You read; you never write files. Answer with sources or say you couldn't find one."))

# ----------------------------------------------------------------------------- demo: five stories

def demo() -> None:
    import sys as _sys
    try: _sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp1252
    except Exception: pass
    home = str(Path.home()).replace("\\", "/")
    root = Path(__file__).parent / ".gorkbot"
    sys_ = System(root, biograph="Asa builds many things at once and is tired of holding all the worlds in his head. Prefers short answers.",
                  projects={"brokie": "brokie: a catalog of free-tier developer deals. Code lives in the workspace as brokie/. Trunk is 1shit/."})
    default_roles(sys_.roles, home)
    rp = sys_.redphone
    if not sys_.ledger.seats: print("no seats: set GEMINI_API_KEY or NVIDIA_NIM_API_KEY"); return
    print(f"seats: {', '.join(f'{s.id}({s.provider})' for s in sys_.ledger.seats.values())}\n")

    print("== S1 · Text from a walk ==")
    rp.dm("voice", "make a tiny brokie schema: one table `deals` with name, vendor, free_tier, url. write it to brokie/schema.sql")

    print("\n== S3 · Sporadic conversation ==")
    ch = rp.channels["dm:voice"]; ch.convo.gaps = [1440, 2760]           # tonight's real gaps: 24 and 46 minutes
    print("  (predicted gap p50 = %.0fs; any seat with a shorter assured window is refused)" % sys_.cadence.predict(ch.convo)["p50"])
    rp.dm("voice", "what did you just have built?")

    print("\n== S36 · The staff and I share a seat ==")
    first = next(iter(sys_.ledger.seats.values())); first.presence = True
    print(f"  (marking {first.id} as live-with-asa; a fresh cast must not land there)")
    try:
        kb = sys_.caster.cast(dict(want="reply with the single word: ready", task_class="general"), sys_.roles.get("builder"), None)
        sys_.runtime.turn(kb, "reply with the single word: ready", tools=False); sys_.runtime.die(kb, "task done")
    except RuntimeError as e: print("  cast refused:", e)
    first.presence = False

    print("\n== S7 · A kernel dies (with report, then without) ==")
    k = sys_.registry.holder("voice", "dm:voice")
    if k: sys_.runtime.die(k, "recast")
    seat = next(iter(sys_.ledger.seats.values()))
    k2 = sys_.caster.cast(dict(want="say hello in five words", task_class="general"), sys_.roles.get("librarian"), None)
    sys_.runtime.turn(k2, "say hello in five words")
    saved, seat_of_k2 = k2.seat.remaining, k2.seat
    seat_of_k2.remaining = 0.0                                              # quota wall: reserve() will refuse the report turn
    sys_.runtime.die(k2, "quota wall"); seat_of_k2.remaining = saved

    print("\n== S39 · hi luv u ==")
    k3 = sys_.caster.cast(dict(want="chat", task_class="dm"), sys_.roles.get("voice"), ch.convo)
    sys_.runtime.turn(k3, "I'm going quiet for a bit.")
    k3.cache_expires_at = sys_.clock.now() + 60                             # window about to close
    print("  tick 1 (a return is likely):"); sys_.pulse.tick()
    ch.convo.gaps = [7200, 9000]; k3.cache_expires_at = sys_.clock.now() - 1
    print("  tick 2 (odds gone):"); sys_.pulse.tick()

    print("\n== the record ==")
    for r in sys_.store.query("tiers")[-4:]:
        print(f"  tier{r['tier']} {r['kind']:10s} by {r['by']:14s} {json.dumps(r['body'], default=str)[:110]}")
    spend = sys_.store.query("spend")
    print(f"\n  {len(spend)} model calls, {sum(int(r.get('total_tokens') or 0) for r in spend)} tokens, "
          f"records in {root/'store'}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "demo": demo()
    else: print(__doc__)
