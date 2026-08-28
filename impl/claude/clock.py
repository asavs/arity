"""Keeping time — how long until the next message, and the heartbeat that acts on it.

`predict` is a running median over the real gaps with a time-of-day discount; it needs to beat
every provider's assumption that you'll reply in five minutes, not to be smart. `tick` is the
pulse: while a return is likelier than not and a ping costs less than going cold, ping the warm
kernel and it answers with almost nothing; when the odds are gone, let it go cold and let the
archivist write. No kernel is told the hour it would have died.
"""

import time

import ledger as ledger_mod

PRIOR = {"call": 5, "dm": 1500, "project": 7200, "public": 21600}
KEEPALIVE = "hi luv u"
KEEPALIVE_OUT_TOKENS = 30
IDLE_T = 4 * 3600

class Convo:
    def __init__(self, cid, kind="dm", channel=None):
        self.id, self.kind, self.channel = cid, kind, channel or cid
        self.recent_gaps, self.last_at = [], None
        self.typing = self.call_open = False

    def saw_message(self, at=None):
        at = at or time.time()
        if self.last_at is not None:
            self.recent_gaps.append(max(1.0, at - self.last_at))
        self.last_at = at

class Prediction:
    def __init__(self, p50, gaps):
        self.p50, self.gaps = p50, gaps

    def p_return_before(self, seconds_from_now):
        """Empirical, not fancy: what share of past gaps were shorter than this deadline."""
        if seconds_from_now <= 0:
            return 0.0
        if not self.gaps:
            return 0.5
        return sum(1 for g in self.gaps if g <= seconds_from_now) / float(len(self.gaps))

def predict(convo, now=None):
    now = now or time.time()
    gaps = list(convo.recent_gaps[-8:]) or [float(PRIOR.get(convo.kind, 1500))]
    s = sorted(gaps)
    med = s[len(s) // 2] if len(s) % 2 else (s[len(s) // 2 - 1] + s[len(s) // 2]) / 2.0
    if convo.kind == "call" and convo.call_open:
        med = 5.0
    if convo.typing:
        med = min(med, 60.0)
    hour = time.localtime(now).tm_hour                     # late night, longer gaps
    return Prediction(med * (2.0 if hour < 7 else 0.8 if hour < 10 else 1.0), gaps)

def ping_cost(k):
    """One keepalive turn: a cached read of the prefix, plus a few words back."""
    t = k.seat.price()
    prefix_M = k.prefix_tokens / 1e6
    return (prefix_M * t["price_in_per_M"] * t["read_x"]
            + (KEEPALIVE_OUT_TOKENS / 1e6) * t["price_out_per_M"])

def tick(core, now=None, asa_idle=0.0):
    now = now or time.time()
    events = []
    for k in core.registry.warm():
        if k.convo is None:
            continue                                       # a leaf isn't waiting on anybody
        horizon = k.cache_expires_at - now
        p = predict(k.convo, now).p_return_before(horizon)
        cc, pc = ledger_mod.cold_cost(k, now), ping_cost(k)
        ev = {"kernel": k.id, "p_return": p, "horizon_s": horizon,
              "cold_penalty": cc["penalty"], "ping_cost": pc, "kept": p * cc["penalty"] > pc}
        if ev["kept"]:
            core.redphone.post(k.convo.channel, "pulse", "keepalive", KEEPALIVE)
            ev["reply"] = k.turn(KEEPALIVE, use_tools=False, max_tokens=80).text.strip()[:120]
        else:
            k.die("quiet")
        events.append(ev)
        core.store.append("pulse.jsonl", ev)
    # idle ticks are paid for by quota that would otherwise expire
    if asa_idle > IDLE_T:
        dying = [s.id for s in core.ledger.seats if s.reset_at - now < 3600 and s.free > 0]
        if dying:
            core.redphone.post("dm:asa", "pulse", "text", "%s. nothing asked."
                               % time.strftime("%H:%M", time.localtime(now)))
            events.append({"idle_post": True, "seats_expiring": dying})
    return events
