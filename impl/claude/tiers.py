"""The journal: where records land, and what a kernel is allowed to know.

`Store` is the store seam — json-lines under one run directory today, sqlite tomorrow, and
nothing above it should notice. `Tiers` is its typed face: both accounts of a kernel land in
the tier that kernel belonged to. `assemble` is the compiler: universal facts everyone gets,
memory gated by how close the role sits to Asa, the predecessor's two accounts if we're
recasting, the task — then the step S8 proved necessary, scanning for anything crossing a
denial and refusing rather than shipping a leaf that knows too much.
"""

import hashlib
import json
import pathlib
import re
import time

class BriefLeak(Exception):
    pass

class Store:
    def __init__(self, root):
        self.root = pathlib.Path(root)
        self.workspace = self.root / "workspace"
        for sub in ("channels", "tiers", "kernels", "workspace"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)

    def append(self, rel, obj):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, default=str) + "\n")
        return p

    def read_log(self, rel):
        p = self.root / rel
        if not p.exists():
            return []
        return [json.loads(ln) for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def put_json(self, rel, obj):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
        return p

    def get_json(self, rel, default=None):
        p = self.root / rel
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default

    def log_message(self, msg):
        return self.append("channels/%s.jsonl" % msg["channel"].replace(":", "_"), msg)

    def channel_log(self, channel_id):
        return self.read_log("channels/%s.jsonl" % channel_id.replace(":", "_"))

    def write_envelope(self, env):
        """Out of band, where the kernel that produced it can't reach."""
        return self.put_json("kernels/%s.json" % env["kernel"], env)

class Tiers:
    """A tier is a distance from Asa, not a folder — but it has to be a folder somewhere."""

    def __init__(self, store):
        self.store = store

    def write(self, tier, kind, body, by):
        rec = {"tier": tier, "kind": kind, "by": by, "at": time.time(), "body": body}
        self.store.append("tiers/tier%d.jsonl" % tier, rec)
        return rec

    def retrieve(self, tier, kind=None):
        rows = self.store.read_log("tiers/tier%d.jsonl" % tier)
        return [r for r in rows if r["kind"] == kind] if kind else rows

def universal_facts(now=None):
    return [
        "Today is %s." % time.strftime("%Y-%m-%d %H:%M", time.localtime(now or time.time())),
        "You are one kernel: one model, one context, holding a named role for a period.",
        "You will be visited. You are not told the hour you would stop.",
        "Tools are the only way you change anything. Saying you did something is not doing it.",
        "Before you stop you get one turn with no tools, to write your own account.",
    ]

# Tier 0 is the biograph — the Voice's distance from Asa, and nobody else's.
TIER0 = ["Asa Schaeffer built you. He is holding too many separate worlds in his head at once "
         "and you exist so he doesn't have to.",
         "He prefers his own machine over a rented one, as a rule rather than a mood.",
         "He texts in bursts and then vanishes for hours. That's normal, not a problem."]
TIER1 = {"brokie": ["brokie is the library of the latest deals — free tiers, trials, inference "
                    "prices.",
                    "It is one of three legs under casting, with API cocktail and megaminds.",
                    "Its schema lives under brokie/ and stays small on purpose."]}
RULES = {"brokie": ["Write files with the tools. Do not paste a file into your reply and call "
                    "it written."]}

class Brief:
    def __init__(self, role, task):
        self.role, self.task = role, task
        self.facts, self.memory, self.rules, self.predecessor = [], [], [], {}

    def render(self):
        out = ["You hold the role: %s." % self.role.name, "", "Facts:"]
        out += ["- " + f for f in self.facts]
        if self.memory:
            out += ["", "What you're allowed to know:"] + ["- " + m for m in self.memory]
        if self.predecessor:
            out += ["", "The kernel that held this role before you left two accounts.",
                    "Its own report (what it meant): %s" % self.predecessor.get("own_report"),
                    "The archivist's entry (what it did): %s" % self.predecessor.get("entry")]
        if self.rules:
            out += ["", "House rules for this work:"] + ["- " + r for r in self.rules]
        out += ["", "Your task right now:", str(self.task.get("want", ""))]
        if self.task.get("context"):
            out += ["", "Context:", str(self.task["context"])]
        return "\n".join(out)

    def hash(self):
        return hashlib.sha256(self.render().encode("utf-8")).hexdigest()[:16]

def scan(text, role):
    """Paths and names — the two things that actually leak."""
    flat = text.replace("\\", "/").lower()
    leaks = ["path:" + p for p in role.deny_paths if p.replace("\\", "/").lower() in flat]
    return leaks + ["name:" + n for n in role.deny_names
                    if re.search(r"\b" + re.escape(n) + r"\b", text, re.IGNORECASE)]

def assemble(role, task, predecessor=None, now=None):
    b = Brief(role, task)
    b.facts = universal_facts(now)
    if role.tier <= 0:
        b.memory += TIER0
    if role.tier <= 1:
        b.memory += TIER1.get(task.get("project", ""), [])
    if predecessor:
        b.predecessor = {"own_report": predecessor.get("own_report") or "ABSENT",
                         "entry": predecessor.get("entry") or "ABSENT"}
    b.rules = RULES.get(task.get("project", ""), [])
    leaks = scan(b.render(), role)
    if leaks:
        raise BriefLeak("brief for %s crosses a denial: %s" % (role.name, ", ".join(leaks)))
    return b
