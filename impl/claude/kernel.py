"""One model runtime, one context, one period.

A kernel is born with everything it will ever be allowed — a seat, a role, a brief, an effort —
and wakes cold. It's told it will be visited, never the hour it would die. Death is the
transition that makes memory, so most of this file is dying properly: reserve the quota, let it
speak once with no tools, capture the evidence out of band, hand both accounts on.
"""

import time
import uuid

import harness

REPORT_TURN_TOKENS = 700
REPORT_PROMPT = (
    "You're being visited one last time. No tools. In a few lines: what you were doing, what "
    "you believe you changed and why, what's open, the last thing you know is safe, and one "
    "piece of advice for whoever picks this up. If you changed files, name their paths exactly."
)

class Identity:
    # provider + endpoint + model + cache boundary + session + hash(brief)
    def __init__(self, provider, endpoint, model, cache_boundary, session, brief_hash):
        self.tuple = (provider, endpoint, model, cache_boundary, session, brief_hash)

    def __repr__(self):
        p, _e, m, cb, s, h = self.tuple
        return "%s|%s|%s|session=%s|brief=%s" % (p, m, cb, s, h)

class Report:
    def __init__(self, kernel_id, identity, body, trigger, status="own"):
        self.kernel, self.identity, self.body = kernel_id, str(identity), body
        self.trigger, self.status, self.at = trigger, status, time.time()

    def as_dict(self):
        return {"kernel": self.kernel, "identity": self.identity, "at": self.at,
                "body": self.body, "trigger": self.trigger, "status": self.status}

class Kernel:
    def __init__(self, core, seat, role, brief, effort, convo=None, archive=True, depth=0):
        self.core, self.seat, self.role, self.brief, self.effort = core, seat, role, brief, effort
        self.id = "k_" + uuid.uuid4().hex[:8]
        self.convo, self.archive, self.depth = convo, archive, depth
        self.inbox, self.transcript = [], []    # inbox: messages posted where this role is
        self.born_at = self.last_turn_at = time.time()
        self.state, self.prefix_tokens, self.tools_blocked = "alive", 0, False
        self.harness = harness.HARNESSES[role.harness]
        self.handle = self.harness.start(seat, brief.render(), role, effort)
        self.toolbox = harness.Toolbox(role, core.store.workspace,
                                       handoff_sink=core.handoff_sink(self))
        self.identity = Identity(seat.provider, seat.endpoint, seat.model, seat.cache_boundary,
                                 convo.id if convo else self.id, brief.hash())
        self.cache_expires_at = self.born_at + seat.cache_window

    def __repr__(self):
        return "<kernel %s %s on %s %s>" % (self.id, self.role.name, self.seat.id, self.state)

    def role_fits(self, role):
        return role.name == self.role.name

    def turn(self, msg, use_tools=True, max_tokens=None):
        if self.state == "dead":
            raise RuntimeError("kernel %s is dead" % self.id)
        tb = None if (self.tools_blocked or not use_tools) else self.toolbox
        t = self.harness.run(self.handle, self.core.ledger.proxy, msg,
                             toolbox=tb, use_tools=bool(tb), max_tokens=max_tokens)
        self.core.ledger.meter(self.seat, t.usage)
        self.prefix_tokens = self.handle.prefix_tokens
        self.last_turn_at = time.time()
        self.cache_expires_at = self.last_turn_at + self.seat.cache_window
        self.transcript.append({"at": self.last_turn_at, "in": msg, "out": t.text,
                                "usage": t.usage, "rounds": t.rounds,
                                "tools": [c["tool"] for c in t.tool_log]})
        return t

    def enqueue_turn(self, msg):
        """Posting never blocks on a reply — a delivered message waits here until drained."""
        self.inbox.append(msg)

    def checkpoint(self):
        """Never sever mid-step. Ours is small: the last tool call that actually succeeded."""
        done = [c for c in self.toolbox.log if c["ok"]]
        return done[-1] if done else None

    def write_report(self, reason):
        """Reserve first, so a quota wall can't take the kernel and its account in one breath."""
        led = self.core.ledger
        if not led.reserve(self.seat, REPORT_TURN_TOKENS):
            return Report(self.id, self.identity,
                          "no quota left on %s for a report turn" % self.seat.id,
                          reason, status="absent")
        try:
            t = self.turn(REPORT_PROMPT + "\n\n(you are stopping because: %s)" % reason,
                          use_tools=False, max_tokens=REPORT_TURN_TOKENS)
            if not t.text.strip():
                return Report(self.id, self.identity, "the report turn came back empty",
                              reason, status="absent")
            return Report(self.id, self.identity, t.text.strip(), reason)
        except harness.ProviderError as e:
            return Report(self.id, self.identity, "cut off mid-report: %s" % e,
                          reason, status="partial")
        finally:
            led.release(self.seat, REPORT_TURN_TOKENS)

    def trace(self, reason):
        return {"kernel": self.id, "role": self.role.name, "tier": self.role.tier,
                "task_class": getattr(self, "task_class", "general"), "public": self.role.public,
                "identity": str(self.identity), "seat": self.seat.id,
                "provider": self.seat.provider, "model": self.seat.model,
                "born_at": self.born_at, "died_at": time.time(), "ended_by": reason,
                "transcript": self.transcript, "tool_log": self.toolbox.log,
                "prefix_tokens": self.prefix_tokens,
                "tokens_used": sum(t["usage"]["prompt_tokens"] + t["usage"]["completion_tokens"]
                                   for t in self.transcript)}

    def die(self, reason):
        if self.state == "dead":
            return None
        self.state, self.tools_blocked = "dying", True
        safe = self.checkpoint()
        report = self.write_report(reason)
        env = self.trace(reason)
        env["last_safe_artifact"] = safe
        self.harness.stop(self.handle)
        self.state = "dead"
        self.core.registry.remove(self)
        self.core.store.write_envelope(env)
        self.core.tiers.write(self.role.tier,
                              "kernel_self_report" if report.status == "own"
                              else "self_report_absence", report.as_dict(), by=self.id)
        if self.archive:
            self.core.archivist.enqueue(env, report, reason)
        return report

class Registry:
    def __init__(self):
        self.live = []

    def add(self, k):
        self.live.append(k)

    def remove(self, k):
        self.live = [x for x in self.live if x is not k]

    def warm(self):
        return [k for k in self.live if k.state == "alive"]

    def warm_for(self, convo):
        if not convo:
            return None
        return next((k for k in self.warm() if k.convo and k.convo.id == convo.id), None)

    def holder(self, role):
        return next((k for k in self.warm() if k.role.name == role.name), None)

def spawn(core, seat, role, brief, effort, convo=None, archive=True, depth=0):
    k = Kernel(core, seat, role, brief, effort, convo=convo, archive=archive, depth=depth)
    core.registry.add(k)
    core.store.append("kernels.jsonl", {"kernel": k.id, "role": role.name, "seat": seat.id,
                                        "identity": str(k.identity), "at": k.born_at,
                                        "cache_window_s": seat.cache_window})
    return k
