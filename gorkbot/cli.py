"""CLI interface for gorkbot."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

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
    """Run a deterministic end-to-end demo of the pure statechart and runtime loop."""
    print("\033[1;32m=== gorkbot 0.0.1 Architecture Demo ===\033[0m\n")
    print("Testing pure state transitions, multi-turn tool loops, and effect dispatch...\n")

    # 1. Mock Model Provider to simulate deterministic model responses
    class MockModelProvider:
        def __init__(self):
            self.turn = 0

        def call(self, effect: CallModel) -> ModelCompleted:
            self.turn += 1
            last_msg = effect.messages[-1]
            # Turn 1: model decides to call a tool
            if self.turn == 1:
                return ModelCompleted(
                    content="Let me write the brokie schema file for you.",
                    tool_calls=[
                        {
                            "id": "call_write_1",
                            "type": "function",
                            "function": {
                                "name": "write_file",
                                "arguments": '{"path": "brokie/schema.sql", "content": "CREATE TABLE deals (id INTEGER PRIMARY KEY, name TEXT, vendor TEXT, free_tier TEXT, url TEXT);"}',
                            },
                        }
                    ],
                    usage={"prompt_tokens": 120, "completion_tokens": 45},
                )
            # Turn 2: model sees tool output and returns final answer
            return ModelCompleted(
                content="I have created `brokie/schema.sql` with the `deals` table schema.",
                tool_calls=[],
                usage={"prompt_tokens": 190, "completion_tokens": 25},
            )

    tools = LocalToolRunner(workspace_root=Path("./.demo_workspace"))
    store = JsonlRecordStore(root=Path("./.demo_records"))
    metrics = MetricsObserver()

    runtime = Runtime(
        model_provider=MockModelProvider(),
        tool_runner=tools,
        store=store,
        transport=ConsoleTransport(bot_name="gorkbot-demo"),
        observers=[metrics],
    )

    print("[Step 1] Sending prompt: 'make a tiny brokie schema'")
    output, state = runtime.chat("make a tiny brokie schema: write it to brokie/schema.sql")

    print("\n[Step 2] Final State Verification:")
    print(f" - Status: {state.status.value}")
    print(f" - Total Messages in history: {len(state.messages)}")
    print(f" - Final Output: {output}")
    print(f" - Total Tool Calls tracked by Observer: {metrics.total_tool_calls}")
    print(f" - Total Tokens: {metrics.total_prompt_tokens + metrics.total_completion_tokens}")

    # Check written file
    target_file = Path("./.demo_workspace/brokie/schema.sql")
    if target_file.exists():
        print(f"\n[Step 3] File Verification: '{target_file}' exists!")
        print(f"Content:\n{target_file.read_text(encoding='utf-8')}")
    else:
        print(f"\n[Error] File '{target_file}' was not created.")

    print("\n\033[1;32m=== Demo Completed Successfully ===\033[0m\n")


def interactive_chat():
    """Run an interactive console chat with real model endpoints."""
    print("\033[1;36m=== gorkbot 0.0.1 Interactive Session ===\033[0m")
    print("Type your message (or 'exit' / 'quit' to stop).\n")

    runtime = Runtime()
    state = State(session_id="cli_interactive", system_prompt="You are gorkbot, a helpful and precise assistant.")

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
    parser = argparse.ArgumentParser(description="gorkbot 0.0.1 CLI")
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
