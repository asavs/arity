"""Five little stories, all model turns real. Run with: python demo.py"""

from __future__ import annotations

import json
from pathlib import Path

from casting import Caster, Conversation, Pulse, Scorecard
from harness import ChatHarness, Tool
from ledger import Ledger
from memory import Task, Tiers
from redphone import RedPhone, TaskRecord
from roles import registry as role_registry
from runtime import Archivist, KernelRegistry, Runtime
from store import Store


ROOT = Path(__file__).resolve().parent
WORKSPACE = ROOT / "workspace"


def log(story: str, message: str) -> None:
    print(f"[{story}] {message}", flush=True)


def main() -> None:
    store = Store(ROOT)
    work = Store(WORKSPACE)
    roles = role_registry(WORKSPACE)
    ledger = Ledger.from_env()
    if len({seat.provider for seat in ledger.seats}) < 2:
        raise SystemExit("demo needs two configured providers (Gemini and NVIDIA NIM are expected)")
    tiers = Tiers(store)
    scorecard = Scorecard(store)
    phone = RedPhone(store, roles)
    phone.channel("dm-asa", "private", ("human", "voice"))
    phone.channel("project-brokie", "private", ("voice", "builder", "observer"))
    harness = ChatHarness(ledger)
    kernels = KernelRegistry()
    archivist = Archivist(tiers, scorecard)
    runtime = Runtime(harness, ledger, tiers, archivist, kernels)
    caster = Caster(ledger, tiers, scorecard, kernels, runtime.spawn)

    # S1: the Voice turns a DM into a bounded handoff; the builder really writes through a tool.
    convo = Conversation("asa-voice", "dm", [1200, 2700, 1800])
    voice_task = Task("S1-voice", "Delegate a tiny deals schema to the builder", "brokie")
    voice = caster.cast(voice_task, roles["voice"], convo)
    records: list[TaskRecord] = []

    def handoff(to_role: str, want: str, budget: int = 1800) -> dict:
        rec = TaskRecord("voice", to_role, want, [], 2, min(budget, 1800), 1,
                         "project-brokie", "dm-asa")
        phone.handoff(rec)
        records.append(rec)
        return {"accepted": True, "record": rec.__dict__}

    handoff_tool = Tool("handoff", "Delegate deep work as a bounded structured record.",
        {"type": "object", "properties": {"to_role": {"type": "string", "enum": ["builder"]},
         "want": {"type": "string"}, "budget": {"type": "integer", "maximum": 1800}},
         "required": ["to_role", "want"]}, handoff)
    phone.post("dm-asa", "human", "make a tiny brokie schema: one table deals with name, vendor, free_tier, url")
    voice.turn("Make exactly one handoff to builder for this request. Use the handoff tool; do not do the work yourself.",
               {"handoff": handoff_tool})
    if len(records) != 1:
        raise RuntimeError("voice did not create exactly one handoff")
    builder_task = Task("S1-build", records[0].want, "brokie", context={"path": "brokie/schema.sql"})
    builder = caster.cast(builder_task, roles["builder"], Conversation("s1-build", "project", [7200]))

    def write_file(path: str, content: str) -> dict:
        target = work.path(path)
        roles["builder"].enforce("paths", str(target))
        written = work.write_text(path, content)
        return {"path": str(written), "bytes": len(content.encode())}

    write_tool = Tool("write_file", "Write UTF-8 text inside the assigned workspace.",
        {"type": "object", "properties": {"path": {"type": "string"},
         "content": {"type": "string"}}, "required": ["path", "content"]}, write_file)
    builder.turn("Use write_file now. Write brokie/schema.sql containing one SQLite-compatible CREATE TABLE deals statement with columns name, vendor, free_tier, and url. Keep it tiny.",
                 {"write_file": write_tool})
    death = builder.die("task complete")
    schema = work.path("brokie/schema.sql")
    if not schema.exists() or "CREATE TABLE" not in schema.read_text(encoding="utf-8").upper():
        raise RuntimeError("builder did not write the schema")
    verified = sum(change["verified"] for change in death.entry["changes"])
    phone.reply(records[0], "builder", {"path": str(schema), "archivist_verified": verified})
    log("S1", f"{builder.seat.provider}/{builder.seat.model} wrote {schema.relative_to(ROOT)}; archivist verified {verified} claim(s)")

    # S3: a second sporadic DM reuses the exact warm identity.
    old_identity = voice.identity
    same_voice = caster.cast(Task("S3", "answer a second short DM", "brokie"), roles["voice"], convo)
    same_voice.turn("Second DM: reply in one short sentence confirming the brokie handoff is recorded.", {})
    log("S3", f"warm kernel kept={same_voice is voice}; identity unchanged={same_voice.identity == old_identity}")

    # S36: presence applies to the whole account/cache boundary, so a fresh cast goes elsewhere.
    ledger.mark_boundary_presence(voice.seat.cache_boundary, True)
    observer = caster.cast(Task("S36", "observe presence routing", "brokie"), roles["observer"],
                           Conversation("fresh-presence", "project", [10]))
    if observer.seat.cache_boundary == voice.seat.cache_boundary:
        raise RuntimeError("fresh cast landed on the human-live boundary")
    observer.turn("In five words or fewer, acknowledge this routing check.", {})
    log("S36", f"live={voice.seat.cache_boundary}; fresh={observer.seat.cache_boundary}")

    # S7: one ordinary death gets a report; quota exhaustion prevents the next report.
    reported = observer.die("routine recast")
    observer2 = caster.cast(Task("S7", "show an absent report after quota wall", "brokie"),
                            roles["observer"], Conversation("quota-wall", "project", [10]))
    observer2.turn("Reply only: checkpoint safe", {})
    observer2.seat.remaining = 0
    absent = observer2.die("forced quota wall")
    log("S7", f"first report={'present' if reported.report else 'ABSENT'}; second report={'present' if absent.report else 'ABSENT'}; flags={absent.entry['flags']}")

    # S39: use the measured rule once each way. Large recent gaps make the second probability tiny.
    pulse = Pulse(caster.cadence, ledger)
    convo.recent_gaps = [30, 60, 120]
    first = pulse.tick(voice, convo)
    convo.recent_gaps = [100_000, 120_000, 140_000]
    second = pulse.tick(voice, convo)
    log("S39", f"{first} with {Pulse.KEEPALIVE!r}, then {second}")

    print(f"TOTAL model_calls={harness.calls} tokens={ledger.tokens}", flush=True)
    print("CACHE_TABLE " + json.dumps({k: {"window_s": v["window_s"], "read_x": v["read_x"],
          "write_x": v["write_x"]} for k, v in __import__('ledger').CACHE_TABLE.items()}), flush=True)


if __name__ == "__main__":
    main()
