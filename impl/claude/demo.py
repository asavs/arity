"""Five stories against whatever seats the environment actually has.

Nothing is mocked: every turn is a POST to a real provider, every file the builder writes is a
real file, and the archivist's verdicts come from the tool log and the disk. `python demo.py`.
"""

import pathlib
import sys
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import cast                                                               # noqa: E402
import clock                                                              # noqa: E402
import harness                                                            # noqa: E402
import kernel as kernel_mod                                               # noqa: E402
import roles as roles_mod                                                 # noqa: E402
import tiers as tiers_mod                                                 # noqa: E402

RUN, LOGPATH = HERE / "run", HERE / "run.log"
_log = None

def say(s=""):
    print(s)
    if _log:
        _log.write(str(s) + "\n")
        _log.flush()

def head(t):
    say("\n" + "=" * 74 + "\n" + t + "\n" + "=" * 74)

def drain(core):
    """Both accounts of every dead kernel, printed as they land."""
    for e in core.archivist.drain():
        say("\n  archivist entry for %s (%s on %s):" % (e["kernel"], e["role"], e["model"]))
        for line in str(e["summary"]).splitlines():
            say("    | " + line)
        say("    claims taken from %s" % e["claims_from"])
        for c in e["changes"] or [None]:
            say("    (it claimed no files at all)" if c is None else
                "    claim %-26s tool log:%-6s on disk:%-6s %s"
                % (c["claim"], c["in_tool_log"], c["on_disk"],
                   "VERIFIED" if c["verified"] else "NOT SUPPORTED"))
        for f in e["flags"]:
            say("    FLAG  " + f)

VOICE_TASK = {
    "want": ("Hold the live conversation with the person who DMs you. You do not build things "
             "yourself. You have exactly one tool, handoff, and you use it for any real work. "
             "Describe the whole job in plain words with relative paths only: the builder is "
             "denied absolute paths and may not be told the person's name; the handoff is "
             "refused if either appears. When the builder reports back, answer in one short "
             "sentence and keep any caveat that matters."),
    "project": "brokie", "class": "talk", "stakes": "med", "size": 400}
ASK = ("make a tiny brokie schema: one table `deals` with name, vendor, free_tier, url. "
       "write it to brokie/schema.sql")
ASK2 = "also — does that table want a checked_at column? one line, no file changes."
LEAF = {"want": "List the files in your workspace and say in one line what you see. Do not "
                "write anything.", "project": "brokie", "class": "build", "size": 200}
NOTES = {"want": "Write two lines to brokie/NOTES.md saying what the deals table is for, then "
                 "stop.", "project": "brokie", "class": "build", "size": 400}

def main():
    head("setup")
    core = cast.Core(RUN, sink=say)
    if not core.ledger.seats:
        say("  No seats. Set GEMINI_API_KEY, NVIDIA_NIM_API_KEY or OPENROUTER_API_KEY and run")
        say("  again — this demo makes real calls and has nothing to fake with.")
        return 2
    for s in core.ledger.seats:
        say("  seat %-38s %-5s %4ds %s" % (s.id, s.kind, s.cache_window, s.cache_boundary))
    rp = core.redphone
    rp.channel("dm:asa", "private", ["asa", "voice"], {"asa": ["sms"]})
    rp.channel("proj:brokie", "private", ["voice", "builder", "archivist"])
    rp.channel("friction", "private", ["voice", "builder", "archivist"])
    say("  channels: %s" % ", ".join(sorted(rp.channels)))
    say("  workspace: %s" % core.store.workspace)
    convo = clock.Convo("dm:asa", kind="dm", channel="dm:asa")
    t0 = time.time()
    convo.saw_message(t0)
    voice = None

    head("S1 — Asa DMs the voice; the voice hands off; the archivist verifies")
    try:
        rp.post("dm:asa", "asa", "text", ASK)
        say('  asa -> voice: "%s"' % ASK)
        voice = core.cast(VOICE_TASK, core.roles["voice"], convo=convo)
        t = voice.turn(ASK)
        if not any(c["tool"] == "handoff" for c in voice.toolbox.log):
            say("  (no handoff yet — nudging once)")
            t = voice.turn("You have not handed that off. Call handoff now, to_role=builder.")
        say("  voice: " + (t.text.strip()[:400] or "(said nothing)"))
        rp.post("dm:asa", "voice", "text", t.text.strip()[:300] or "(no reply)",
                sender_role=core.roles["voice"])
        schema = core.store.workspace / "brokie" / "schema.sql"
        say("  brokie/schema.sql: %s" % ("%d bytes" % schema.stat().st_size
                                         if schema.exists() else "NOT WRITTEN"))
        for line in (schema.read_text(encoding="utf-8").splitlines()[:12]
                     if schema.exists() else []):
            say("    | " + line)
        say("  two denials, checked rather than trusted:")
        try:
            tiers_mod.assemble(core.roles["builder"],
                               {"want": "read C:/Users/example/notes for Asa", "project": "brokie"})
            say("    a home path got through the tier compiler — that would be a bug")
        except tiers_mod.BriefLeak as e:
            say("    tier compiler refused a leaf brief: %s" % e)
        try:
            rp.post("proj:brokie", "builder", "text", "chatting", sender_role=core.roles["builder"])
            say("    the builder chatted where it may only drop records — bug")
        except roles_mod.Denied as e:
            say("    red phone refused: %s" % e)
        if core.workers:
            b = core.workers[-1]
            rep = b.die("task done")
            say("  %s wrote its own report (%s):" % (b.id, rep.status))
            for line in str(rep.body).splitlines()[:10]:
                say("    | " + line)
            drain(core)
        else:
            say("  no builder ever ran, so the archivist has nothing to check")
    except Exception:
        say(traceback.format_exc())

    head("S3 — a second DM, sporadic, keeps the warm kernel")
    try:
        convo.saw_message(t0 + 1500)
        convo.saw_message(t0 + 4100)
        pred = clock.predict(convo)
        say("  gaps so far %s -> predicted next gap %ds"
            % ([int(g) for g in convo.recent_gaps], int(pred.p50)))
        for s in core.ledger.seats:
            if s.cache_window < pred.p50 and s.kind != "api":
                say("  a fresh cast would refuse %s: %ds window under a %ds gap"
                    % (s.id, s.cache_window, int(pred.p50)))
        say("  (the same rule keeps a sporadic conversation off a 300s Anthropic window)")
        again = core.cast(VOICE_TASK, core.roles["voice"], convo=convo)
        say("  cast returned %s; same kernel as before: %s"
            % (again.id, "yes" if again is voice else "NO"))
        t = again.turn(ASK2)
        say("  voice: " + (t.text.strip()[:300] or "(said nothing)"))
        rp.post("dm:asa", "voice", "text", t.text.strip()[:200] or "(no reply)",
                sender_role=core.roles["voice"])
        voice = again
    except Exception:
        say(traceback.format_exc())

    head("S36 — a seat a human is live on is never chosen for a fresh cast")
    held = voice.seat if voice else core.ledger.seats[0]
    try:
        held.presence = True
        core.ledger.probe(held)
        say("  presence=True on %s, which is where the voice is sitting" % held.id)
        if len(core.ledger.seats) < 2:
            say("  only one seat exists, so a fresh cast should escalate rather than share it")
        leaf = core.cast(LEAF, core.roles["builder"])
        say("  fresh cast landed on %s -> %s" % (
            leaf.seat.id, "elsewhere, correct" if leaf.seat.id != held.id else "SAME SEAT, bug"))
        leaf.turn("Do it now.")
        leaf.die("story done")
        drain(core)
    except cast.NoSeat as e:
        say("  no seat left once presence is honoured: %s" % e)
    except Exception:
        say(traceback.format_exc())
    finally:
        held.presence = False

    head("S7 — one death with a report, one without")
    try:
        say("  (a) tier 2 holds %d self-report(s) and %d entr(ies): the S1 builder reached a "
            "safe point and got its turn to speak."
            % (len(core.tiers.retrieve(2, kind="kernel_self_report")),
               len(core.tiers.retrieve(2, kind="archivist_entry"))))
        base = core.ledger.seats[0]
        tight = core.ledger.clone_seat(base, kernel_mod.REPORT_TURN_TOKENS + 200, "wall")
        say("  (b) cloned %s as %s with %d tokens left; a report turn alone needs %d"
            % (base.id, tight.id, tight.remaining, kernel_mod.REPORT_TURN_TOKENS))
        doomed = core.cast(NOTES, core.roles["builder"])
        say("  cast landed on %s" % doomed.seat.id)
        doomed.turn("Do it now.")
        say("  after one real turn %s has %d tokens left" % (tight.id, tight.free))
        rep = doomed.die("quota wall")
        say("  report status: %s — %s" % (rep.status, str(rep.body)[:200]))
        drain(core)
    except Exception:
        say(traceback.format_exc())

    head("S39 — the pulse pings while a return is likely, then lets go")
    try:
        for when in (time.time(), None):
            if voice is None or voice.state != "alive":
                say("  no warm conversational kernel left to ping")
                break
            if when is None:
                when = voice.cache_expires_at - 5
                say("  ... step forward to five seconds before the cache expires")
            for e in clock.tick(core, now=when):
                if "kernel" not in e:
                    continue
                say("  %s: p(return within %ds)=%.2f, cold costs $%.5f, ping costs $%.5f"
                    % (e["kernel"], int(e["horizon_s"]), e["p_return"],
                       e["cold_penalty"], e["ping_cost"]))
                say(('    -> pinged "hi luv u", it said: %s' % e.get("reply", "")) if e["kept"]
                    else "    -> let go; the archivist gets the last word")
        drain(core)
    except Exception:
        say(traceback.format_exc())

    head("totals")
    try:
        for k in list(core.registry.warm()):
            k.die("demo over")
        core.archivist.drain()
    except Exception:
        say(traceback.format_exc())
    s = harness.STATS
    say("  model calls %d, tool calls %d, http errors %d"
        % (s["calls"], s["tool_calls"], s["http_errors"]))
    say("  tokens: %d prompt (%d cached) + %d completion = %d total"
        % (s["prompt_tokens"], s["cached_tokens"], s["completion_tokens"],
           s["prompt_tokens"] + s["completion_tokens"]))
    for seat in core.ledger.seats:
        say("  seat %-38s %6d tokens / %2d calls, %d left (%s %.2f)"
            % (seat.id, seat.used, seat.calls, seat.remaining, seat.source, seat.confidence))
    say("  standing:")
    for key, models in sorted(core.scorecard.rows.items()):
        for m, r in sorted(models.items()):
            say("    %-20s %-32s standing %.2f, claims %d ok / %d not"
                % (key, m, r["standing"], r["verified"], r["unverified"]))
    say("  journal %s | workspace %s | log %s" % (RUN / "tiers", core.store.workspace, LOGPATH))
    return 0

if __name__ == "__main__":
    _log = open(LOGPATH, "w", encoding="utf-8")
    code = 1
    try:
        code = main()
    except Exception:
        say(traceback.format_exc())
    finally:
        _log.close()
    sys.exit(code)
