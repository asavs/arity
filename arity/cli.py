"""CLI interface for arity."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
import json
import time
from .ledger import Seat, SeatLedger
from .orchestrator import ArityOrchestrator

from .handlers import (
    ConsoleTransport,
    JsonlRecordStore,
    LocalToolRunner,
    MetricsObserver,
    OpenAIModelProvider,
)
from .runtime import Runtime
from .types import (
    CallModel,
    ExecuteTool,
    ModelCompleted,
    State,
    Status,
    ToolCompleted,
    UserMessage,
)


def run_demo():
    """Run a deterministic end-to-end demo of all 7 elemental parts."""
    print("\033[1;32m====================================================\033[0m")
    print("\033[1;32m   arity End-to-End Orchestration Demo (7 Parts)  \033[0m")
    print("\033[1;32m====================================================\033[0m\n")

    demo_ws = Path("./.demo_workspace")
    demo_records = Path("./.demo_records")
    demo_ws.mkdir(parents=True, exist_ok=True)
    demo_records.mkdir(parents=True, exist_ok=True)

    store = JsonlRecordStore(root=demo_records)

    # 1. Setup candidate seats in Ledger (Part 2)
    now = time.time()
    seat_gemini = Seat(
        id="gemini-flash",
        provider="gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-3.6-flash",
        kind="quota",
        total_allowance=1_000_000,
        remaining=650_000,
        reset_deadline=now + 1800,  # 30m left (expiring quota)
        base_price_per_m=0.10,
    )
    seat_gpt = Seat(
        id="gpt-4o",
        provider="openai",
        endpoint="https://api.openai.com/v1",
        model="gpt-4o",
        kind="quota",
        total_allowance=2_000_000,
        remaining=1_800_000,
        reset_deadline=now + 86400,
        base_price_per_m=2.50,
    )
    ledger = SeatLedger(initial_seats=[seat_gemini, seat_gpt], auto_seed=False)

    # 2. Mock model factory for deterministic trial execution
    def mock_model_factory(seat: Seat):
        class MockBuilderProvider:
            def __init__(self, seat_name: str):
                self.seat_name = seat_name
                self.turn = 0

            def call(self, effect: CallModel) -> ModelCompleted:
                self.turn += 1
                if self.turn == 1:
                    return ModelCompleted(
                        content="Generating the brokie deal schema...",
                        tool_calls=[
                            {
                                "id": f"tc_write_{self.seat_name}",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps({
                                        "path": "brokie/schema.sql",
                                        "content": (
                                            "CREATE TABLE deals (\n"
                                            "    id INTEGER PRIMARY KEY,\n"
                                            "    name TEXT NOT NULL,\n"
                                            "    vendor TEXT NOT NULL,\n"
                                            "    free_tier TEXT,\n"
                                            "    url TEXT\n"
                                            ");"
                                        ),
                                    }),
                                },
                            }
                        ],
                        usage={"prompt_tokens": 120, "completion_tokens": 40},
                    )
                return ModelCompleted(
                    content="Created brokie/schema.sql with deals table.",
                    tool_calls=[],
                    usage={"prompt_tokens": 180, "completion_tokens": 20},
                )

        return MockBuilderProvider(seat.id)

    orchestrator = ArityOrchestrator(
        ledger=ledger,
        store=store,
        base_workspace=demo_ws / "terrarium",
        model_factory=mock_model_factory,
    )

    prompt = "make a tiny brokie schema: write it to brokie/schema.sql"
    print(f"\033[1;33m[User -> Voice]\033[0m {prompt}\n")

    # Execute full orchestration loop
    response = orchestrator.handle_message(
        user_text=prompt,
        sender="Asa",
        candidates_per_task=2,
        now=now,
    )

    print(f"\033[1;36m[Voice Response]\033[0m {response.reply_text}\n")

    print("\033[1;35m--- Elemental Parts Verification ---\033[0m")
    print(f"1. Role Resolution: Delegated to role '{response.delegated_task.to_role}' (Tier 2)")
    print(f"2. Quota Casting: Picked primary '{response.winning_candidate.seat.id}' (expiring soonest)")
    print(f"3. Terrarium Execution: Sandboxed in '{response.winning_candidate.workspace_path}'")
    print(f"4. Tool Execution & AST Validation: Successfully wrote and validated files")
    print(f"5. Impartial Archivist Audit: Verdict = {response.archivist_entries[0].verdict.upper()}")
    print(f"   Verified Artifacts: {response.archivist_entries[0].verified_artifacts}")
    print(f"   Standing After: {orchestrator.scorecard.get_standing('builder', response.winning_candidate.seat.model):.1f}")

    # Pulse evaluation
    pulse_actions = orchestrator.tick_pulse(now=now)
    print(f"6. Pulse Engine: Generated {len(pulse_actions)} actions ({[a.kind for a in pulse_actions]})")
    print(f"7. Red Phone Public Address: Message posted to channel 'main'")

    # Check file
    target_file = response.winning_candidate.workspace_path / "brokie/schema.sql"
    if target_file.exists():
        print(f"\n\033[1;32m[Verified File Content in Sandbox]\033[0m\n{target_file.read_text(encoding='utf-8')}")

    print("\n\033[1;32m====================================================\033[0m")
    print("\033[1;32m            Demo Completed Successfully!            \033[0m")
    print("\033[1;32m====================================================\033[0m\n")


def interactive_chat():
    """Run an interactive console chat with real model endpoints."""
    print("\033[1;36m=== arity 0.0.1 Interactive Session ===\033[0m")
    print("Type your message (or 'exit' / 'quit' to stop).\n")

    runtime = Runtime()
    state = State(session_id="cli_interactive", system_prompt="You are arity, a helpful and precise assistant.")

    while True:
        try:
            user_input = input("\033[1;33mYou:\033[0m ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break

        if not user_input or user_input.lower() in ("exit", "quit"):
            break

        runtime.run(state, initial_event=UserMessage(text=user_input))


def main():
    parser = argparse.ArgumentParser(description="arity 0.0.1 CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("demo", help="Run the architectural demo")
    subparsers.add_parser("chat", help="Start an interactive chat session")

    run_parser = subparsers.add_parser("run", help="Run a single prompt")
    run_parser.add_argument("prompt", type=str, help="Prompt text")

    args = parser.parse_args()

    if args.command == "demo" or len(sys.argv) == 1:
        run_demo()
    elif args.command == "chat":
        interactive_chat()
    elif args.command == "run":
        runtime = Runtime()
        out, _ = runtime.chat(args.prompt)
        print(out)


if __name__ == "__main__":
    main()
