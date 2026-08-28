"""demo.py - Plays the five stories (S1, S3, S36, S7, S39) against real seats and prints log."""

from __future__ import annotations
import os
import time
from store import Store
from ledger import SeatLedger
from roles import RoleRegistry
from scorecard import Scorecard
from archivist import Archivist
from redphone import RedPhone
from cast import Caster, KernelRegistry
from pulse import Pulse
from harness import METRICS
import tiers


def run_demo() -> None:
    print("=" * 60)
    print("ARITY v0 DEMO - PLAYING 5 STORIES")
    print("=" * 60)

    # Infrastructure Setup
    store = Store(workspace_dir="workspace")
    ledger = SeatLedger()
    roles = RoleRegistry()
    scorecard = Scorecard()
    archivist = Archivist(scorecard)
    registry = KernelRegistry()
    caster = Caster(ledger, scorecard, store, registry)
    phone = RedPhone(roles, store)
    pulse = Pulse(registry, ledger, store, archivist)

    # -------------------------------------------------------------
    # STORY 1: S1 - DM to Voice -> Handoff to Builder -> Archivist Verifies
    # -------------------------------------------------------------
    print("\n--- STORY 1 (S1): Voice DM, Builder Handoff & Archivist Audit ---")
    asa_prompt = (
        "make a tiny brokie schema: one table `deals` with name, vendor, "
        "free_tier, url. write it to brokie/schema.sql"
    )
    phone.dm("asa", "voice", asa_prompt)
    print(f"[Asa -> Voice]: {asa_prompt}")

    voice_role = roles.get("voice")
    task, builder_kernel = phone.handoff(
        from_role=voice_role,
        to_role_name="builder",
        want=asa_prompt,
        cast_fn=lambda role, task_context: caster.cast(role, task_context, convo_id="convo_s1"),
    )
    print(f"[Voice -> Builder Handoff]: Task status={task.status}")

    # Builder dies with report, archivist audits
    report, entry = builder_kernel.die("task done", store, archivist, ledger)
    print(f"[Builder Self-Report]: {report}")
    print(f"[Archivist Entry Summary]: {entry.summary}")
    print(f"[Archivist Verified Changes]: {entry.verified_changes}")

    brief_sentence = phone.voice_brief_asa(task, entry)
    print(f"[Voice -> Asa Phone Brief]: \"{brief_sentence}\"")

    # -------------------------------------------------------------
    # STORY 2: S3 - Sporadic Cadence & Warm Kernel Retention
    # -------------------------------------------------------------
    print("\n--- STORY 2 (S3): Sporadic Cadence Keeps Warm Kernel ---")
    k1 = caster.cast(voice_role, "First chat turn", convo_id="convo_s3", recent_gaps=[10.0])
    k1.turn("Hello, keeping context warm.")
    print(f"[Turn 1]: Spawned {k1.id} on seat {k1.seat.model}, cache_expires_in={k1.cache_expires_at - time.time():.1f}s")

    k2 = caster.cast(voice_role, "Second turn shortly after", convo_id="convo_s3", recent_gaps=[15.0])
    is_warm = (k1.id == k2.id)
    print(f"[Turn 2]: Cast returned kernel {k2.id}. Warm kernel retained: {is_warm}")

    # -------------------------------------------------------------
    # STORY 3: S36 - Presence Live Filtering
    # -------------------------------------------------------------
    print("\n--- STORY 3 (S36): Seat Presence Live Avoidance ---")
    seat_to_block = ledger.seats[0]
    seat_to_block.presence = True
    print(f"[Presence Update]: Marked seat {seat_to_block.id} ({seat_to_block.model}) presence=True (Asa typing)")

    fresh_builder_k = caster.cast(
        roles.get("builder"),
        "Build ancillary index",
        convo_id="convo_fresh_s36",
        recent_gaps=[50.0],
    )
    print(f"[Fresh Cast]: Selected seat={fresh_builder_k.seat.id} ({fresh_builder_k.seat.model}), presence={fresh_builder_k.seat.presence}")
    assert fresh_builder_k.seat.id != seat_to_block.id
    assert not fresh_builder_k.seat.presence
    seat_to_block.presence = False  # Reset

    # -------------------------------------------------------------
    # STORY 4: S7 - Die with Report vs Quota Wall Absence & Standing
    # -------------------------------------------------------------
    print("\n--- STORY 4 (S7): Normal Report vs Forced Quota Wall (ABSENT) & Standing Drop ---")
    # A. Normal Death
    k_norm = caster.cast(roles.get("scout"), "Scout job", convo_id="convo_s7_a")
    rep_a, entry_a = k_norm.die("work done", store, archivist, ledger)
    print(f"[Normal Die]: Report present={bool(rep_a)}, flags={entry_a.flags}")

    # B. Quota Wall Death
    k_quota = caster.cast(roles.get("scout"), "Scout job blocked", convo_id="convo_s7_b")
    ledger._forced_quota_wall = True
    rep_b, entry_b = k_quota.die("quota wall hit", store, archivist, ledger)
    ledger._forced_quota_wall = False
    print(f"[Quota Wall Die]: Report present={bool(rep_b)}, flags={entry_b.flags}")

    # C. Standing penalty on unverified claim
    print(f"[Scorecard]: Initial scout standing={scorecard.standing.get(('scout', k_norm.seat.model), 1.0):.2f}")
    k_liar = caster.cast(roles.get("scout"), "Phantom file task", convo_id="convo_s7_c")
    fake_env = k_liar.trace("finished")
    fake_report = "I wrote the file phantom_unreal.py and committed everything."
    entry_c = archivist.enqueue(fake_env, fake_report, "finished", store)
    new_standing = scorecard.standing.get(("scout", k_liar.seat.model), 1.0)
    print(f"[Archivist Audit of False Claim]: flags={entry_c.flags}")
    print(f"[Scorecard]: Updated scout standing after false claim={new_standing:.2f} (DROPPED)")

    # -------------------------------------------------------------
    # STORY 5: S39 - Pulse Keepalive ('hi luv u') & Eviction
    # -------------------------------------------------------------
    print("\n--- STORY 5 (S39): Pulse Heartbeat ('hi luv u') & Quiet Eviction ---")
    k_pulse = caster.cast(voice_role, "Standby session", convo_id="convo_pulse")
    k_pulse.prefix_tokens = 50_000

    # 1. Pulse tick when return odds are high -> keepalive
    pulse_res1 = pulse.tick()
    print(f"[Pulse Tick 1 (Warm cache worth keeping)]: {pulse_res1}")

    # 2. Expire the cache window and run pulse tick -> quiet eviction
    k_pulse.cache_expires_at = time.time() - 10.0
    pulse_res2 = pulse.tick()
    print(f"[Pulse Tick 2 (Cache expired / cold)]: {pulse_res2}")

    # -------------------------------------------------------------
    # METRICS SUMMARY
    # -------------------------------------------------------------
    print("\n" + "=" * 60)
    print("DEMO EXECUTION COMPLETE")
    print(f"Total Model Calls:     {METRICS['total_calls']}")
    print(f"Total Prompt Tokens:   {METRICS['total_prompt_tokens']}")
    print(f"Total Completion Tokens:{METRICS['total_completion_tokens']}")
    print("=" * 60)


if __name__ == "__main__":
    run_demo()
