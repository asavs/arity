"""The composer, and the thing that holds everything else.

`cast` is the only place the three axes — seat, model, effort — meet the cache math, the cadence
guess and the scorecard. The scorecard lives here too, because learning who's good is just the
improvement side of deciding who answers — the same object, later in time. Core is the rest: a
roster, a ledger, a journal and a clock, knowing who to cast the moment a message arrives.
"""

import time

import archivist as archivist_mod
import clock
import kernel as kernel_mod
import ledger as ledger_mod
import redphone as redphone_mod
import roles as roles_mod
import tiers as tiers_mod

SMALL = 300              # tokens; under this a task is low effort
SWITCH_VALUE = 0.02      # dollars a full rank of model improvement is worth on one prompt
STANDING_FLOOR, STANDING_HIT, STANDING_HEAL = 0.25, 0.6, 1.15

class NoSeat(Exception):
    pass

class Candidate:
    def __init__(self, model, score, confidence, reason):
        self.model, self.score, self.confidence, self.reason = model, score, confidence, reason

class Scorecard:
    """Standing is a multiplier under 1 that a model earns back with clean runs. That one line
    is the whole difference between a router and a staff."""

    PATH = "scorecard.json"

    def __init__(self, store):
        self.store = store
        self.rows = store.get_json(self.PATH, default={}) or {}

    def _row(self, role, task_class, model):
        key = "%s|%s" % (getattr(role, "name", role), task_class)
        self.rows.setdefault(key, {})
        return self.rows[key].setdefault(model, {
            "trials": 0, "verified": 0, "unverified": 0, "wall_sum": 0.0, "tokens": 0,
            "standing": 1.0, "last": None})

    def rank(self, role, task_class, available_models):
        """Ordered candidates, each with a reason the Voice can repeat out loud."""
        seen = self.rows.get("%s|%s" % (role.name, task_class), {})
        scored = []
        for m in available_models:
            r = seen.get(m)
            if not r or not r["trials"]:
                continue
            good = r["verified"] / float(max(1, r["verified"] + r["unverified"]))
            speed = 1.0 / (1.0 + (r["wall_sum"] / max(1, r["trials"])) / 10.0)
            w = role.aptitude
            score = (w.get("quality", 1.0) * good + w.get("speed", 1.0) * speed) * r["standing"]
            scored.append(Candidate(m, score, 0.0,
                                    "%d trial(s), %.0f%% of claims held up, standing %.2f"
                                    % (r["trials"], 100 * good, r["standing"])))
        if not scored:
            return [Candidate(m, 0.0, 0.0, "no evidence yet — falling back to declared wants")
                    for m in available_models]
        scored.sort(key=lambda c: -c.score)
        top = scored[0].score or 1.0
        for c in scored:
            c.confidence = c.score / top
        # untried models go last, but they do go on the list — that's how they become evidence
        return scored + [Candidate(m, 0.0, 0.0, "untried for this role") for m in available_models
                         if all(c.model != m for c in scored)]

    def record(self, role, task_class, model, verified=0, unverified=0, wall=0.0, tokens=0):
        r = self._row(role, task_class, model)
        r["trials"] += 1
        r["verified"] += verified
        r["unverified"] += unverified
        r["wall_sum"] += wall
        r["tokens"] += tokens
        r["last"] = time.time()
        if unverified:
            r["standing"] = max(STANDING_FLOOR, r["standing"] * (STANDING_HIT ** unverified))
        else:
            r["standing"] = min(1.0, r["standing"] * STANDING_HEAL)
        self.store.put_json(self.PATH, self.rows)
        return r

class Core:
    def __init__(self, root, sink=print):
        self.sink = sink
        self.store = tiers_mod.Store(root)
        self.ledger = ledger_mod.Ledger(self.store)
        self.ledger.seed_from_env()
        self.roles = roles_mod.build()
        self.tiers = tiers_mod.Tiers(self.store)
        self.scorecard = Scorecard(self.store)
        self.registry = kernel_mod.Registry()
        self.redphone = redphone_mod.RedPhone(self.store, sink)
        self.redphone.core = self
        self.archivist = archivist_mod.Archivist(self)
        self.workers, self.notes = [], []

    def note(self, s):
        self.notes.append(s)
        self.sink("      . " + s)

    def all_models(self):
        return list(dict.fromkeys(s.model for s in self.ledger.seats))   # order preserved

    def switch_gain(self, warm, role, task):
        """What a better model is worth on this prompt, in the same units as the cache."""
        ranked = self.scorecard.rank(role, task.get("class", "general"), self.all_models())
        mine = next((c for c in ranked if c.model == warm.seat.model), None)
        if not ranked or mine is None or ranked[0].model == warm.seat.model:
            return 0.0
        return max(0.0, ranked[0].confidence - mine.confidence) * SWITCH_VALUE

    def cast(self, task, role, convo=None, archive=True, depth=0, now=None):
        now = now or time.time()
        task_class = task.get("class", "general")
        warm = self.registry.warm_for(convo)
        if warm is not None and warm.role_fits(role):
            cc = ledger_mod.cold_cost(warm, now)
            gain = self.switch_gain(warm, role, task)
            if cc["penalty"] >= gain:
                self.note("kept warm kernel %s: going cold costs $%.4f, a better model is worth "
                          "$%.4f" % (warm.id, cc["penalty"], gain))
                return warm
            self.note("recasting %s: a better model is worth $%.4f, the cache only $%.4f"
                      % (warm.id, gain, cc["penalty"]))
            warm.die("recast")

        # a leaf isn't waiting on anybody, so the window filter only bites conversations
        gap = clock.predict(convo, now).p50 if convo else 0.0
        cands = self.scorecard.rank(role, task_class, self.all_models())
        self.note("scorecard for %s/%s: %s"
                  % (role.name, task_class,
                     ", ".join("%s (%s)" % (c.model, c.reason) for c in cands[:3])))
        seats = self.ledger.seats_for([c.model for c in cands])
        for why, keep in (("a human is live on that seat", lambda s: not s.presence),
                          ("cache window under the predicted %ds gap" % int(gap),
                           lambda s: s.cache_window >= gap or s.kind == "api"),
                          ("not enough left to run and still report",
                           lambda s: s.free >= kernel_mod.REPORT_TURN_TOKENS)):
            dropped = [s.id for s in seats if not keep(s)]
            if dropped:
                self.note("dropped %s: %s" % (", ".join(dropped), why))
            seats = [s for s in seats if keep(s)]
        seats = self.ledger.dying_soonest(seats)
        if not seats:
            self.redphone.escalate("no seat for role %s on %s" % (role.name, task_class), "asa")
            raise NoSeat("no seat fits role %s" % role.name)

        seat = seats[0]
        self.ledger.probe(seat)
        effort = ("high" if task.get("stakes") == "high"
                  else "low" if task.get("size", 1000) < SMALL else "medium")
        k = kernel_mod.spawn(self, seat, role, tiers_mod.assemble(role, task, now=now),
                             effort, convo=convo, archive=archive, depth=depth)
        k.task_class = task_class
        self.note("cast %s: %s on %s (effort=%s, window=%ds, identity=%s)"
                  % (role.name, seat.model, seat.id, effort, seat.cache_window, k.identity))
        return k

    def handoff_sink(self, sender_kernel):
        """The `handoff` tool from the sending kernel's side, bounded by depth and budget."""
        def sink(args):
            to = str(args.get("to_role", "")).strip().lower()
            if to not in self.roles:
                return "no such role: %r. The staff is: %s" % (to, ", ".join(sorted(self.roles)))
            rec = redphone_mod.task_record(
                sender_kernel.role.name, to, args.get("want", ""),
                args.get("project", "brokie"), "proj:brokie", tier=self.roles[to].tier,
                budget=max(0, 2 - sender_kernel.depth), depth=sender_kernel.depth + 1,
                ev=["dm:asa"])
            try:
                self.redphone.handoff(sender_kernel, rec)
            except roles_mod.Denied as e:
                return str(e)
            return self.run_handoff(rec)
        return sink

    def run_handoff(self, rec):
        role = self.roles[rec["to_role"]]
        k = self.cast({"want": rec["want"], "project": rec.get("project", "brokie"),
                       "class": "build", "stakes": "med", "size": 800},
                      role, convo=None, depth=rec["depth"])
        self.workers.append(k)
        t = k.turn("A task record landed in %s.\n\nfrom: %s\nwant: %s\n\nDo it now with your "
                   "tools, then say in one line what you wrote."
                   % (rec["channel"], rec["from"], rec["want"]))
        wrote = [c["args"].get("path") for c in k.toolbox.log
                 if c["tool"] == "write_file" and c["ok"]]
        reply = dict(rec)
        reply.update({"from": role.name, "to_role": rec["from"], "kernel": k.id, "budget": 0,
                      "result": t.text.strip()[:400], "files": wrote, "evidence": wrote,
                      "want": "reply to " + rec["from"]})
        # the builder may not chat in this channel; a record still goes back the same way
        self.redphone.post(rec["channel"], role.name, "handoff", reply, sender_role=role)
        return "%s reported: %s (files: %s)" % (role.name, reply["result"],
                                                ", ".join(wrote) or "none")
