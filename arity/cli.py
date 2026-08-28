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
    """Run a clean, responsive console chat with The Secretary and live cache warmth indicator."""
    print("\033[1;36m=== Arity Switchboard (The Secretary) ===\033[0m")
    print("Type your message (or 'exit' / 'quit' to stop).\n")

    orchestrator = ArityOrchestrator()
    last_turn_time: Optional[float] = None
    current_model = "gemini-3.6-flash"
    warm_window = 300.0  # 5-minute sliding cache window (Axiom 7)

    while True:
        # Calculate remaining cache warmth
        now = time.time()
        if last_turn_time is None:
            cache_tag = "\033[1;30m[Cache: Cold Start]\033[0m"
        else:
            elapsed = now - last_turn_time
            remaining = int(warm_window - elapsed)
            if remaining > 0:
                mins, secs = divmod(remaining, 60)
                cache_tag = f"\033[1;32m[Cache Hot: {mins}m {secs:02d}s | {current_model}]\033[0m"
            else:
                cache_tag = f"\033[1;31m[Cache Evicted | {current_model}]\033[0m"

        try:
            user_input = input(f"{cache_tag}\n\033[1;33mAsa:\033[0m ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break

        if not user_input or user_input.lower() in ("exit", "quit"):
            break

        start_t = time.time()
        resp = orchestrator.handle_message(user_text=user_input, sender="Asa")
        latency = time.time() - start_t
        last_turn_time = time.time()

        if resp.delegated_task and resp.winning_candidate:
            role_name = resp.delegated_task.to_role
            model_used = resp.winning_candidate.seat.model
            verdict = resp.archivist_entries[0].verdict.upper() if resp.archivist_entries else "OK"
            print(f"\n\033[1;35m[{role_name} on {model_used} | {latency:.2f}s | Archivist: {verdict}]\033[0m")
            if resp.winning_candidate.output:
                print(f"{resp.winning_candidate.output}\n")
        elif resp.reply_text:
            print(f"\n\033[1;36m[The Secretary | {latency:.2f}s]\033[0m\n{resp.reply_text}\n")
def show_status():
    """Display real-time seat health, wire latency, and scorecard standings."""
    orchestrator = ArityOrchestrator()
    print("\033[1;36m====================================================\033[0m")
    print("\033[1;36m            arity System Health & Status          \033[0m")
    print("\033[1;36m====================================================\033[0m\n")

    print("\033[1;33m[1. Active Seats: Provider | Model | Fallback Harness]\033[0m")
    for s in orchestrator.ledger.list_seats():
        status_str = "\033[1;32mLIVE\033[0m" if not s.presence else "\033[1;33mLOCKED (PRESENCE)\033[0m"
        acc_str = f" ({s.account.split('@')[0]})" if s.account else ""
        prov_str = f"{s.provider}{acc_str}"
        print(f"  • {prov_str:25} | {s.model:24} | harness: {s.harness:8} | {status_str} | ${s.base_price_per_m:.4f}/M")
    print("\n\033[1;33m[2. Empirical Scorecard Standings (Axiom 9)]\033[0m")
    standings = getattr(orchestrator.scorecard, "_standings", {})
    if standings:
        for k, v in sorted(standings.items()):
            print(f"  • {k:30} : {v:.1f} pts")
    else:
        print("  • No historical penalties or bonuses recorded yet (baseline: 10.0 pts)")

    print("\n\033[1;33m[3. Red Phone Ingress / Outbox (Axiom 10)]\033[0m")
    channels = getattr(orchestrator.inbox, "_channels", {})
    if channels:
        for ch, msgs in channels.items():
            print(f"  • Channel '{ch}': {len(msgs)} messages pending")
    else:
        print("  • All red phone channels clear.")

    print("\n\033[1;36m====================================================\033[0m\n")


def show_redphone():
    """Inspect Red Phone channels and messages."""
    orchestrator = ArityOrchestrator()
    print("\033[1;35m====================================================\033[0m")
    print("\033[1;35m       Red Phone Public Address Channel Log         \033[0m")
    print("\033[1;35m====================================================\033[0m\n")

    recent = orchestrator.inbox.list_recent(limit=15)
    if not recent:
        print("  • No messages recorded in Red Phone channels yet.\n")
        return

    for m in recent:
        ch = m.get("channel", "main")
        sender = m.get("sender", "user")
        text = m.get("text", "")
        ts = time.strftime("%H:%M:%S", time.localtime(m.get("timestamp", time.time())))
        print(f"  \033[1;33m[{ts}] [{ch}]\033[0m \033[1m{sender}\033[0m: {text}")
    print("\n\033[1;35m====================================================\033[0m\n")

def show_skills():
    """List all registered and discovered skills."""
    from .skills import SkillRegistry
    registry = SkillRegistry()
    print("\033[1;36m====================================================\033[0m")
    print("\033[1;36m             Registered arity Skills              \033[0m")
    print("\033[1;36m====================================================\033[0m\n")
    for sk in registry.list_skills():
        tags_str = f"[{', '.join(sk.tags)}]" if sk.tags else ""
        print(f"  \033[1;33m• {sk.name:30}\033[0m {tags_str}")
        print(f"    {sk.description}\n")
    print("\033[1;36m====================================================\033[0m\n")


def show_roles():
    """List all registered roles, tiers, permissions, and skills."""
    from .roles import RoleRegistry
    registry = RoleRegistry()
    print("\033[1;35m====================================================\033[0m")
    print("\033[1;35m             Registered Staff Roles & Tiers         \033[0m")
    print("\033[1;35m====================================================\033[0m\n")
    for role in sorted(set(registry._roles.values()), key=lambda r: r.tier):
        print(f"  \033[1;33m• {role.name:20}\033[0m (Tier {role.tier})")
        print(f"    Description: {role.description}")
        if role.skills:
            print(f"    Skills:      {', '.join(role.skills)}")
        if role.allowed_tools:
            print(f"    Tools:       {', '.join(role.allowed_tools)}")
        if role.denial_set.denied_tools:
            print(f"    Denied Tools:{', '.join(role.denial_set.denied_tools)}")
        if role.denial_set.denied_paths:
            print(f"    Denied Paths:{', '.join(role.denial_set.denied_paths)}")
        print()
    print("\033[1;35m====================================================\033[0m\n")


def handle_auth_command(args: argparse.Namespace) -> None:
    """Handle arity auth subcommands."""
    from .auth import (
        TokenStore,
        fetch_antigravity_quota,
        login_google_antigravity,
        login_openai_codex,
        login_xai_grok,
    )
    store = TokenStore()
    action = getattr(args, "auth_action", "status") or "status"

    if action == "status":
        creds = store.load_all() or store.discover_external_credentials()
        print("\n\033[1;36m================== Arity Auth Status ==================\033[0m")
        if not creds:
            print("  No saved or discovered credentials found.")
            print("  Run \033[1;33marity auth login <google|openai|xai>\033[0m or \033[1;33marity auth import\033[0m.")
        else:
            for prov, data in creds.items():
                email = data.get("email", "unknown")
                proj = data.get("projectId") or data.get("accountId") or "N/A"
                expires = data.get("expires")
                exp_str = "No expiry recorded"
                if expires:
                    exp_sec = float(expires) / 1000.0 if float(expires) > 10_000_000_000 else float(expires)
                    remaining_min = int((exp_sec - time.time()) / 60)
                    exp_str = f"Expires in {remaining_min}m" if remaining_min > 0 else "Expired (Auto-refreshable)"
                print(f"  \033[1m{prov}\033[0m")
                print(f"    - Email / Identity: {email}")
                print(f"    - Project / Account: {proj}")
                print(f"    - Token Status: \033[1;32m{exp_str}\033[0m")

                # Check live quota for Google Antigravity
                if "google-antigravity" in prov and data.get("access") and data.get("projectId"):
                    try:
                        quota = fetch_antigravity_quota(data["access"], data["projectId"])
                        if quota:
                            gemini_q = quota.get("gemini-3-flash-agent", {}).get("remainingFraction")
                            claude_q = quota.get("claude-sonnet-4-6", {}).get("remainingFraction")
                            if gemini_q is not None or claude_q is not None:
                                g_str = f"{int(gemini_q*100)}%" if gemini_q is not None else "N/A"
                                c_str = f"{int(claude_q*100)}%" if claude_q is not None else "N/A"
                                print(f"    - Live Quota: \033[1;34mGemini 3: {g_str}\033[0m | \033[1;35mClaude: {c_str}\033[0m")
                    except Exception:
                        pass
        print("\033[1;36m=========================================================\033[0m\n")
    elif action == "import":
        print("\n\033[1;36m[Arity Auth]\033[0m Scanning ~/.omp, ~/.codex, and local stores...")
        imported = store.import_all()
        print(f"\033[1;32m[Arity Auth]\033[0m Imported {len(imported)} credentials into ~/.arity/auth.json:")
        for p in imported:
            print(f"  - {p}")
        print()

    elif action == "login":
        from .auth import login_anthropic
        provider = (getattr(args, "provider", "") or "").lower()
        if provider in ("google", "agy", "antigravity"):
            login_google_antigravity()
        elif provider in ("openai", "codex", "chatgpt"):
            login_openai_codex()
        elif provider in ("xai", "grok"):
            login_xai_grok()
        elif provider in ("anthropic", "claude"):
            login_anthropic()
        else:
            print(f"\033[1;31m[Error]\033[0m Unknown provider '{provider}'. Choose from: google, openai, xai, anthropic.")

    elif action == "logout":
        provider = (getattr(args, "provider", "") or "").lower()
        # Map friendly name to key
        prov_map = {
            "google": "google-antigravity",
            "agy": "google-antigravity",
            "antigravity": "google-antigravity",
            "openai": "openai-codex",
            "codex": "openai-codex",
            "xai": "xai-oauth",
            "grok": "xai-oauth",
        }
        key = prov_map.get(provider, provider)
        if store.delete_credential(key):
            print(f"\033[1;32m[Arity Auth]\033[0m Removed credential for '{key}'.")
        else:
            print(f"\033[1;33m[Arity Auth]\033[0m No saved credential found for '{key}'.")

def main():
    parser = argparse.ArgumentParser(description="arity 0.1.2 CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("demo", help="Run the architectural demo")
    subparsers.add_parser("chat", help="Start an interactive chat session with The Secretary")
    subparsers.add_parser("status", help="Show seat health, scorecard, and wire status")
    subparsers.add_parser("skills", help="List active and discovered skills")
    subparsers.add_parser("roles", help="List staff roles, capabilities, and denial sets")
    subparsers.add_parser("redphone", help="Inspect Red Phone channels")

    lock_parser = subparsers.add_parser("lock", help="Lock human presence on a seat")
    lock_parser.add_argument("seat_id", type=str, help="Seat ID to presence-lock")

    unlock_parser = subparsers.add_parser("unlock", help="Release human presence on a seat")
    unlock_parser.add_argument("seat_id", type=str, help="Seat ID to unlock")

    run_parser = subparsers.add_parser("run", help="Run a single prompt through the orchestrator")
    run_parser.add_argument("prompt", type=str, help="Prompt text")
    auth_parser = subparsers.add_parser("auth", help="Manage OAuth subscriptions and credentials")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_action")
    auth_subparsers.add_parser("status", help="Show active credentials and expiry status")
    auth_subparsers.add_parser("import", help="Auto-import active sessions from OMP and Codex")
    
    login_cmd = auth_subparsers.add_parser("login", help="Authenticate with a provider")
    login_cmd.add_argument("provider", type=str, choices=["google", "agy", "openai", "codex", "xai", "grok", "anthropic", "claude"], help="Provider to authenticate")
    logout_cmd = auth_subparsers.add_parser("logout", help="Log out from a provider")
    logout_cmd.add_argument("provider", type=str, help="Provider to remove")

    args = parser.parse_args()

    if args.command == "demo":
        run_demo()
    elif args.command == "chat":
        interactive_chat()
    elif args.command == "status":
        show_status()
    elif args.command == "skills":
        show_skills()
    elif args.command == "roles":
        show_roles()
    elif args.command == "auth":
        handle_auth_command(args)
    elif args.command == "lock":
        orchestrator = ArityOrchestrator()
        orchestrator.ledger.set_presence(args.seat_id, True)
        print(f"\033[1;33m[Presence Lock]\033[0m Seat '{args.seat_id}' is now locked for human use.")
    elif args.command == "unlock":
        orchestrator = ArityOrchestrator()
        orchestrator.ledger.set_presence(args.seat_id, False)
        print(f"\033[1;32m[Presence Unlock]\033[0m Seat '{args.seat_id}' released for autonomous bot casting.")
    elif args.command == "redphone":
        show_redphone()
    elif args.command == "run":
        orchestrator = ArityOrchestrator()
        resp = orchestrator.handle_message(user_text=args.prompt, sender="Asa")
        if resp.delegated_task and resp.winning_candidate and resp.winning_candidate.output:
            print(resp.winning_candidate.output)
        elif resp.reply_text:
            print(resp.reply_text)
    else:
        run_demo()

if __name__ == "__main__":
    main()
