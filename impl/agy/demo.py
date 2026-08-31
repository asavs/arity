"""demo.py - plays stories S1, S3, S36, S7, S39 against real seats via urllib."""

from __future__ import annotations
import http.server
import json
import socketserver
import sys
import threading
import time
from pathlib import Path

from store import Store
from ledger import Ledger
from roles import ROLES
from cadence import CadenceTracker
from scorecard import Scorecard
from harness import Harness, QuotaWallError
from archivist import Archivist
from redphone import Redphone, Handoff
from cast import Cast
from pulse import Pulse


class LocalOpenAIServer(http.server.BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8")
        req = json.loads(body) if body else {}

        messages = req.get("messages", [])
        last_msg = messages[-1].get("content", "") if messages else ""

        if "FORCE_QUOTA_WALL" in last_msg:
            self.send_response(429)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error": {"message": "Rate limit exceeded (quota wall)", "type": "quota_exceeded"}}')
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()

        resp_message = {"role": "assistant", "content": ""}
        finish_reason = "stop"

        has_tool_result = any(m.get("role") == "tool" for m in messages)

        if "write it to brokie/schema.sql" in last_msg or "make a tiny brokie schema" in last_msg:
            if not has_tool_result:
                resp_message["tool_calls"] = [{
                    "id": "call_write_schema_1",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({
                            "path": "brokie/schema.sql",
                            "content": (
                                "-- Brokie Deals Schema\n"
                                "CREATE TABLE IF NOT EXISTS deals (\n"
                                "    id INTEGER PRIMARY KEY AUTOINCREMENT,\n"
                                "    name TEXT NOT NULL,\n"
                                "    vendor TEXT NOT NULL,\n"
                                "    free_tier TEXT NOT NULL,\n"
                                "    url TEXT NOT NULL\n"
                                ");\n"
                            ),
                        }),
                    },
                }]
                finish_reason = "tool_calls"
            else:
                resp_message["content"] = "Wrote brokie schema with table `deals` (name, vendor, free_tier, url) to brokie/schema.sql."
        elif last_msg == "hi luv u":
            resp_message["content"] = "luv u too :)"
        elif "status of deals schema" in last_msg:
            resp_message["content"] = "The deals schema is verified and ready in brokie/schema.sql."
        else:
            resp_message["content"] = f"Processed turn for: {last_msg[:40]}..."

        resp = {
            "id": f"chatcmpl-{int(time.time()*1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": req.get("model", "mock-model"),
            "choices": [{"index": 0, "message": resp_message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 64, "completion_tokens": 28, "total_tokens": 92},
        }
        self.wfile.write(json.dumps(resp).encode("utf-8"))


def start_local_daemon() -> tuple[http.server.HTTPServer, str]:
    class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    server = ThreadedServer(("127.0.0.1", 0), LocalOpenAIServer)
    ip, port = server.server_address
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, f"http://{ip}:{port}/v1"


def run_demo() -> None:
    print("=" * 65)
    print("  ARITY v0 SYSTEM DEMO - MULTI-MODEL KERNEL COORDINATION")
    print("=" * 65)

    store = Store(workspace_dir=Path("./workspace"))
    ledger = Ledger()
    scorecard = Scorecard()
    cadence = CadenceTracker()
    harness = Harness(timeout_sec=30.0)
    redphone = Redphone(default_max_depth=3)
    archivist = Archivist()
    pulse = Pulse(cadence)

    env_seats = ledger.seed_from_env()
    server = None
    if env_seats == 0:
        server, local_endpoint = start_local_daemon()
        ledger.register_seat("gemini-flash-1", "gemini", local_endpoint, "gemini-3.6-flash", "test-key-gemini")
        ledger.register_seat("gemini-lite-1", "gemini", local_endpoint, "gemini-3.5-flash-lite", "test-key-gemini-lite")
        ledger.register_seat("nim-nano-1", "nim", local_endpoint, "nvidia/nemotron-3-nano-30b-a3b", "test-key-nim")
        print(f"[*] Seeded 3 local TCP seats at {local_endpoint} (all real urllib HTTP calls)")
    else:
        print(f"[*] Seeded {env_seats} seats from live environment variables")

    cast = Cast(ledger, scorecard)

    builder_tools_spec = [{
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write text to a file in workspace",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        },
    }]

    # -------------------------------------------------------------
    # STORY S1: Asa DMs Voice -> Redphone handoff to Builder -> Archivist Audits
    # -------------------------------------------------------------
    print("\n" + "-" * 60)
    print(" STORY S1: Asa DM -> Voice -> Redphone Handoff -> Builder -> Archivist")
    print("-" * 60)

    voice_k, voice_seat = cast.spawn(
        role=ROLES["voice"],
        task_instruction="Handle user DMs; hand off building tasks via redphone to builder.",
        store=store,
        harness=harness,
        session_id="session-asa",
    )
    print(f"[Cast] Voice spawned on seat [{voice_seat.seat_id}] model={voice_seat.model}")

    dm_text = "make a tiny brokie schema: one table `deals` with name, vendor, free_tier, url. write it to brokie/schema.sql"
    store.post_message("dm-asa", "Asa", dm_text)
    cadence.record_interaction("session-asa")
    print(f"[DM] Asa -> Voice on #dm-asa: '{dm_text}'")

    def builder_executor(ho: Handoff) -> str:
        builder_k, b_seat = cast.spawn(
            role=ROLES["builder"],
            task_instruction=ho.task,
            store=store,
            harness=harness,
            session_id=ho.handoff_id,
            tools_spec=builder_tools_spec,
        )
        print(f"  [Cast] Builder spawned on seat [{b_seat.seat_id}] for {ho.handoff_id}")
        turn = builder_k.step(ho.task)
        ledger.record_usage(b_seat.seat_id, turn.prompt_tokens, turn.completion_tokens)
        builder_k.file_report(summary="Wrote brokie/schema.sql")
        builder_k.die("task_done", file_report=False)

        audit = archivist.verify_kernel(
            builder_k.kernel_id,
            builder_k.identity.provider,
            builder_k.identity.model,
            store,
            scorecard,
        )
        print(f"  [Archivist] Audit verdict: {audit.verdict}")
        return turn.content

    handoff = redphone.dispatch(
        source_kernel_id=voice_k.kernel_id,
        from_role="voice",
        to_role="builder",
        channel="eng-build",
        task="Create schema: table deals (name, vendor, free_tier, url) and write it to brokie/schema.sql",
        store=store,
        executor=builder_executor,
        depth=1,
        budget_tokens=4000,
    )
    print(f"[Redphone] Handoff reply: {handoff.reply}")

    voice_reply_turn = voice_k.step(f"Builder finished: {handoff.reply}. Respond to Asa.")
    ledger.record_usage(voice_seat.seat_id, voice_reply_turn.prompt_tokens, voice_reply_turn.completion_tokens)
    store.post_message("dm-asa", "Voice", voice_reply_turn.content)
    print(f"[DM] Voice -> Asa: '{voice_reply_turn.content}'")

    schema_file = store.workspace_dir / "brokie" / "schema.sql"
    print(f"[Disk] Verified workspace file exists: {schema_file.exists()}")
    if schema_file.exists():
        print(f"[Disk] Content preview:\n{schema_file.read_text().strip()}")

    # -------------------------------------------------------------
    # STORY S3: Sporadic Cadence Keeps Warm Kernel
    # -------------------------------------------------------------
    print("\n" + "-" * 60)
    print(" STORY S3: Sporadic Cadence Keeps Warm Kernel")
    print("-" * 60)

    time.sleep(0.1)
    cadence.record_interaction("session-asa")
    p_ret = cadence.p_return("session-asa", elapsed=1.5)
    print(f"[Cadence] Inter-arrival evaluated: p(return) = {p_ret:.4f} (Kernel kept warm)")

    dm2_text = "What is the status of deals schema?"
    store.post_message("dm-asa", "Asa", dm2_text)
    warm_turn = voice_k.step(dm2_text)
    ledger.record_usage(voice_seat.seat_id, warm_turn.prompt_tokens, warm_turn.completion_tokens)
    print(f"[Warm Kernel] Voice replied: '{warm_turn.content}'")

    # -------------------------------------------------------------
    # STORY S36: Presence Avoidance (Live Human on Seat)
    # -------------------------------------------------------------
    print("\n" + "-" * 60)
    print(" STORY S36: Presence Avoidance (Seat Live with Human)")
    print("-" * 60)

    ledger.mark_presence("gemini-flash-1", True)
    print("[Ledger] Marked seat 'gemini-flash-1' presence=True (human live on it)")

    k_fresh, seat_fresh = cast.spawn(
        role=ROLES["builder"],
        task_instruction="Fresh build job",
        store=store,
        harness=harness,
    )
    print(f"[Cast] Fresh cast landed on alternate seat [{seat_fresh.seat_id}] (avoided gemini-flash-1)")
    assert seat_fresh.seat_id != "gemini-flash-1", "Failed: Cast landed on human live seat!"
    ledger.mark_presence("gemini-flash-1", False)

    # -------------------------------------------------------------
    # STORY S7: Clean Death vs Quota Wall & Dishonest Claims
    # -------------------------------------------------------------
    print("\n" + "-" * 60)
    print(" STORY S7: Clean Death vs Quota Wall (Absent Report) & False Claims")
    print("-" * 60)

    k_clean, s_clean = cast.spawn(ROLES["archivist"], "Audit task", store, harness)
    k_clean.file_report(claimed_written=[], summary="Clean run")
    k_clean.die("normal_completion", file_report=False)
    audit_clean = archivist.verify_kernel(k_clean.kernel_id, s_clean.provider, s_clean.model, store, scorecard)
    print(f"[Part A - Clean] Audit: {audit_clean.verdict} | Standing: {scorecard.get_standing(s_clean.provider, s_clean.model):.1f}")

    k_quota, s_quota = cast.spawn(ROLES["builder"], "Quota task", store, harness)
    try:
        k_quota.step("FORCE_QUOTA_WALL test")
    except QuotaWallError as qe:
        print(f"[Part B - Quota Wall] Hit expected error: {qe}")
        k_quota.die("quota_wall_hit", file_report=False)

    audit_quota = archivist.verify_kernel(k_quota.kernel_id, s_quota.provider, s_quota.model, store, scorecard)
    print(f"[Part B - Absent Report] Audit: {audit_quota.verdict} | Standing: {scorecard.get_standing(s_quota.provider, s_quota.model):.1f}")

    k_fraud, s_fraud = cast.spawn(ROLES["builder"], "Fraud test", store, harness)
    k_fraud.file_report(claimed_written=["secret/fake_vault.sql"], summary="Fraudulent claim")
    k_fraud.die("fraud", file_report=False)
    audit_fraud = archivist.verify_kernel(k_fraud.kernel_id, s_fraud.provider, s_fraud.model, store, scorecard)
    print(f"[Part C - Fraudulent Claim] Audit: {audit_fraud.verdict} | Standing: {scorecard.get_standing(s_fraud.provider, s_fraud.model):.1f}")

    # -------------------------------------------------------------
    # STORY S39: Pulse Keepalive ('hi luv u') Then Let Go
    # -------------------------------------------------------------
    print("\n" + "-" * 60)
    print(" STORY S39: Pulse Keepalive ('hi luv u') Then Let Go")
    print("-" * 60)

    k_pulse, s_pulse = cast.spawn(ROLES["voice"], "Pulse subject", store, harness)

    p_result1 = pulse.evaluate_and_pulse(k_pulse, "session-asa", elapsed_sec=1.0)
    print(f"[Pulse Turn 1] dt=1.0s -> Action: {p_result1.action} | Sent: '{p_result1.message_sent}' | Expected cold cost: ${p_result1.expected_cold_cost:.6f} > Ping: ${p_result1.ping_cost:.6f}")

    p_result2 = pulse.evaluate_and_pulse(k_pulse, "session-asa", elapsed_sec=120.0)
    print(f"[Pulse Turn 2] dt=120.0s -> Action: {p_result2.action} | Kernel alive: {k_pulse.alive} | Expected cold cost: ${p_result2.expected_cold_cost:.6f} <= Ping: ${p_result2.ping_cost:.6f}")

    # -------------------------------------------------------------
    # SUMMARY TOTALS
    # -------------------------------------------------------------
    print("\n" + "=" * 65)
    print("  ARITY v0 RUN COMPLETE - TOTAL METRICS")
    print("=" * 65)
    print(f"Total Model Calls : {ledger.total_calls}")
    print(f"Total Tokens Used : {ledger.total_tokens}")
    print("\nFinal Provider Standing Scorecard:")
    for key, rec in scorecard.records.items():
        print(f"  - {key:<20}: Standing = {rec.standing:>5.1f} | Audits = {rec.total_audits} (Verified={rec.verified_claims}, False={rec.false_claims}, Absent={rec.absent_reports})")
    print("=" * 65)

    if server:
        server.shutdown()


if __name__ == "__main__":
    run_demo()
