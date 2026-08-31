"""Command-line interface for Arity."""
from __future__ import annotations

import argparse
import sys
import os
from pathlib import Path
import json
import time

from . import __version__

# -----------------------------------------------------------------------------
# Terminal & Encoding Safeguards for Windows & Cross-Platform Consoles
# -----------------------------------------------------------------------------
for _s in (sys.stdout, sys.stderr):
    if hasattr(_s, "reconfigure"):
        try:
            # line_buffering: device-login codes must reach a pipe/background log immediately
            _s.reconfigure(encoding="utf-8", errors="replace", line_buffering=True)
        except Exception:
            pass


def safe_print(*args, **kwargs) -> None:
    """Print with fallback encoding protection to prevent charmap/UnicodeEncodeError on legacy consoles."""
    file = kwargs.get("file", sys.stdout)
    sep = kwargs.get("sep", " ")
    end = kwargs.get("end", "\n")
    text = sep.join(str(a) for a in args) + end
    try:
        file.write(text)
        file.flush()
    except UnicodeEncodeError:
        enc = getattr(file, "encoding", None) or "ascii"
        safe_text = text.encode(enc, errors="replace").decode(enc)
        file.write(safe_text)
        file.flush()
    except Exception:
        try:
            print(*args, **kwargs)
        except Exception:
            pass

from .ledger import Seat, SeatLedger
from .orchestrator import ArityOrchestrator
from .spirals import render_brand_mark
from .tools import positive_int, resolve_arity
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
    print(render_brand_mark())
    print("\033[1;32m====================================================\033[0m")
    print("\033[1;32m    Arity End-to-End Orchestration Demo (7 Parts)   \033[0m")
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
    """Run a clean, responsive console chat with the Secretary and live cache warmth indicator."""
    print(render_brand_mark())
    print("\033[1;36m=== Arity switchboard (The Secretary) ===\033[0m")
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
    print(render_brand_mark())
    print("\033[1;36m====================================================\033[0m")
    print("\033[1;36m             Arity System Health & Status           \033[0m")
    print("\033[1;36m====================================================\033[0m\n")
    print("\033[1;33m[1. Active Seats: Provider | Model | Fallback Harness]\033[0m")
    for s in orchestrator.ledger.list_seats():
        status_str = "\033[1;32mLIVE\033[0m" if not s.presence else "\033[1;33mLOCKED (PRESENCE)\033[0m"
        acc_str = f" ({s.account.split('@')[0]})" if s.account else ""
        prov_str = f"{s.provider}{acc_str}"
        harness_label = s.harness
        print(f"  • {prov_str:25} | {s.model:24} | harness: {harness_label:8} | {status_str} | ${s.base_price_per_m:.4f}/M")
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
    print("\033[1;36m              Registered Arity Skills               \033[0m")
    print("\033[1;36m====================================================\033[0m\n")
    for sk in registry.list_skills():
        tags_str = f"[{', '.join(sk.tags)}]" if sk.tags else ""
        print(f"  \033[1;33m• {sk.name:30}\033[0m {tags_str}")
        print(f"    {sk.description}\n")
    print("\033[1;36m====================================================\033[0m\n")


def show_roles():
    """List all registered staff roles, skills, and dynamically granted tools."""
    from .roles import RoleRegistry
    from .tools import SandboxToolRunner
    registry = RoleRegistry()
    print("\033[1;35m====================================================\033[0m")
    print("\033[1;35m                 Registered Staff Roles             \033[0m")
    print("\033[1;35m====================================================\033[0m\n")
    for role in registry.list_roles():
        runner = SandboxToolRunner(role=role)
        granted_tools = [s["function"]["name"] for s in runner.get_schemas() if s.get("function", {}).get("name") != "search"]
        print(f"  \033[1;33m• {role.name:20}\033[0m")
        print(f"    Description:   {role.description}")
        if role.skills:
            print(f"    Skills:        {', '.join(role.skills)}")
        print(f"    Granted Tools: \033[1;32m{', '.join(granted_tools)}\033[0m")
        if role.denial_set.denied_paths:
            print(f"    Path Locks:    {', '.join(role.denial_set.denied_paths)}")
        print()
    print("\033[1;35m  Types (attach to any role as role:type, e.g. developer:python, reviewer:rust)\033[0m")
    for pack in registry.list_types():
        print(f"  \033[1;33m* {pack.name:20}\033[0m {pack.description}")
        print(f"    Skills:        {', '.join(pack.skills) or '-'}")
        print(f"    Verify:        {pack.verify.get('test_command', '-')}")
    print()
    print("\033[1;35m====================================================\033[0m\n")


def handle_auth_command(args: argparse.Namespace) -> int:
    """Handle Arity authentication subcommands."""
    from .auth import (
        AuthConfigurationError,
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
        print("\n\033[1;36m=================== Arity Auth Status ===================\033[0m")
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
        print(f"\033[1;32m[Arity Auth]\033[0m Imported {len(imported)} credentials into Arity state file ~/.arity/auth.json:")
        for p in imported:
            print(f"  - {p}")
        print()

    elif action == "login":
        from .auth import login_anthropic
        provider = (getattr(args, "provider", "") or "").lower()
        try:
            if provider in ("google", "agy", "antigravity"):
                login_google_antigravity()
            elif provider in ("openai", "codex", "chatgpt"):
                login_openai_codex()
            elif provider in ("xai", "grok"):
                login_xai_grok()
            elif provider in ("anthropic", "claude"):
                login_anthropic()
            else:
                print(
                    f"[Arity auth] Unknown provider '{provider}'.",
                    file=sys.stderr,
                )
                return 2
        except AuthConfigurationError as exc:
            print(f"[Arity auth] {exc}", file=sys.stderr)
            return 1

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
    return 0


def handle_race_command(args: argparse.Namespace) -> None:
    """Compare one task across candidate variants; implementation lives in race.py."""
    from .race import RaceConfig, render_report, run_race

    cfg = RaceConfig(
        prompt=getattr(args, "prompt", "") or "",
        task_name=getattr(args, "task", None),
        variants=getattr(args, "variants", None) or "models",
        role=getattr(args, "role", None) or "builder",
        test_command=getattr(args, "test_cmd", None),
        workers=int(getattr(args, "workers", 4) or 4),
        mock=bool(getattr(args, "mock", False)),
        as_json=bool(getattr(args, "json", False)),
        tester=bool(getattr(args, "tester", False)),
        teardown=(True if getattr(args, "teardown", False) else (False if getattr(args, "keep", False) else None)),
        judges=[j.strip() for j in (getattr(args, "judges", "") or "").split(",") if j.strip()],
        review=getattr(args, "review", None) or "tie",
        conference=int(getattr(args, "conference", 0) or 0),
    )
    report = run_race(cfg)
    if cfg.as_json:
        safe_print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        render_report(report, printer=safe_print)


def handle_run_command(args: argparse.Namespace) -> None:
    """Run a front-door trial and deliver the fact-ranked or human-selected result."""
    from pathlib import Path as _P
    from .race import render_report, run_front_door
    judges = [j.strip() for j in args.judges.split(",") if j.strip()] if getattr(args, "judges", None) else None
    # A background/piped run must never block on the secretary's question.
    noninteractive = os.environ.get("ARITY_NONINTERACTIVE") == "1"
    interactive = sys.stdin.isatty() and sys.stdout.isatty() and not args.json and not noninteractive
    rep, delivery = run_front_door(
        args.prompt or "", task_name=args.task, role=args.role, candidates=args.candidates, judges=judges,
        conference=args.conference, tester=args.tester, out_dir=_P(args.out) if args.out else None, mock=args.mock,
        printer=safe_print, interactive=interactive, quiet=not args.verbose,
    )
    if args.json:
        safe_print(json.dumps({"delivery": delivery.to_dict(), "report": rep.to_dict()}, indent=2))
        return
    if args.verbose:
        render_report(rep, printer=safe_print)
    if delivery.answer:
        safe_print(delivery.answer)
        safe_print()
    elif delivery.files:
        safe_print("delivered: " + ", ".join(delivery.files))
    safe_print(f"[2mreceipt: {delivery.receipt}[0m")


def show_standings(by: str = "model") -> None:
    """Multi-axis standings: aggregates over trial_axes and judgement records, no composite."""
    from .standings import render_standings, standings
    safe_print(render_standings(standings(by=by), by=by))


def show_tasks() -> None:
    """List the race task bank."""
    from .tasks import TaskBank
    safe_print("\n\033[1;36m================= Arity Task Bank ==================\033[0m")
    for t in TaskBank().list_tasks():
        safe_print(f"  \033[1;33m{t.name:16}\033[0m {t.description}")
        safe_print(f"  {'':16} module={t.module} entrypoint={t.entrypoint} hidden_tests={len(t.hidden_tests)} tags={', '.join(t.tags)}")
    safe_print("\033[1;36m=====================================================\033[0m\n")


def _positive_arity_arg(value: str) -> int:
    try:
        return positive_int(value, name="--arity")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _non_empty_trial_id(value: str) -> str:
    if not value:
        raise argparse.ArgumentTypeError("trial_id must be a non-empty string")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="arity",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=(
            render_brand_mark(width=23, height=9, seeds=55)
            + f"\n\nArity {__version__}: a small, provider-agnostic trial kernel for agent harnesses."
        ),
        epilog="Python API: import arity.",
    )
    parser.add_argument("--version", action="version", version=f"Arity {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("demo", help="Run the architectural demo")
    subparsers.add_parser("chat", help="Start an interactive chat session with The Secretary")
    subparsers.add_parser("status", help="Show seat health, scorecard, and wire status")
    subparsers.add_parser("skills", help="List active and discovered skills")
    subparsers.add_parser("roles", help="List staff roles, capabilities, and denial sets")
    subparsers.add_parser("redphone", help="Inspect Red Phone channels")

    trials_parser = subparsers.add_parser(
        "trials", help="List persisted trial journals without running agents"
    )
    trials_parser.add_argument("--json", action="store_true", help="Emit a versioned JSON catalog")

    watch_parser = subparsers.add_parser(
        "watch", help="Show a blind-safe trial view"
    )
    watch_parser.add_argument(
        "trial_id",
        nargs="?",
        type=_non_empty_trial_id,
        help="Optional persisted trial id to select",
    )
    watch_parser.add_argument(
        "--ascii",
        action="store_true",
        help="Use ASCII glyphs in follow mode",
    )
    watch_parser.add_argument(
        "--no-motion",
        action="store_true",
        help="Disable follow-mode motion",
    )
    watch_parser.add_argument(
        "--follow",
        action="store_true",
        help="Refresh in a supported interactive terminal",
    )
    watch_parser.add_argument(
        "--cache-policy",
        choices=("conservative", "exact", "off"),
        default="conservative",
        help="Cache deadline display: shortest recorded, recorded policies, or off",
    )

    trial_parser = subparsers.add_parser(
        "trial", help="Inspect or replay one persisted trial journal"
    )
    trial_subparsers = trial_parser.add_subparsers(dest="trial_action", required=True)
    trial_show = trial_subparsers.add_parser(
        "show", help="Show a content-safe projection of one trial"
    )
    trial_show.add_argument(
        "trial_id", type=_non_empty_trial_id, help="Exact persisted trial id"
    )
    trial_show.add_argument("--json", action="store_true", help="Emit graph-ready metadata as JSON")
    trial_replay = trial_subparsers.add_parser(
        "replay", help="Replay and validate one trial's ordered event stream"
    )
    trial_replay.add_argument(
        "trial_id", type=_non_empty_trial_id, help="Exact persisted trial id"
    )
    trial_replay.add_argument("--json", action="store_true", help="Emit the full replay record as JSON")

    race_parser = subparsers.add_parser(
        "race",
        help="Compare one task across candidate variants; facts rank first and blind review is optional",
    )
    race_parser.add_argument("prompt", type=str, nargs="?", default="", help="Ad-hoc task brief (or use --task)")
    race_parser.add_argument("--task", "-t", type=str, default=None, help="Task from the bank (see `arity tasks`); brings hidden tests")
    race_parser.add_argument("--variants", "-v", type=str, default="models", help="Preset: models | harness | tools | skills | context, or custom 'model=..+harness=..+tools=..+skills=a/b+ctx=..' list")
    race_parser.add_argument("--role", "-r", type=str, default="builder", help="Builder role (default: builder)")
    race_parser.add_argument("--tester", action="store_true", help="Have the tester role author hidden tests before the race")
    race_parser.add_argument("--test-cmd", type=str, default=None, help="Override the candidate's own test command")
    race_parser.add_argument("--workers", "-w", type=int, default=4, help="Max parallel candidates")
    race_parser.add_argument("--mock", action="store_true", help="Canned providers (good / slow / liar), ephemeral store, no tokens spent")
    race_parser.add_argument("--keep", action="store_true", help="Keep sandboxes after the race (default for live runs)")
    race_parser.add_argument("--teardown", action="store_true", help="Delete sandboxes after the race (default for --mock)")
    race_parser.add_argument("--judges", type=str, default="", help="Review phase: comma-separated judge models that read a blind bundle and rank (reviewer role)")
    race_parser.add_argument("--review", choices=["tie", "always", "never"], default="tie", help="When the review phase runs (default: only when facts tie)")
    race_parser.add_argument("--conference", type=int, default=0, metavar="ROUNDS", help="After the isolated build, wake the candidates up together for ROUNDS rounds (peers' work visible, notes via message(to='peer:X')), then re-verify")
    race_parser.add_argument("--json", action="store_true", help="Emit the full report as JSON")
    subparsers.add_parser("tasks", help="List the race task bank")
    standings_parser = subparsers.add_parser("standings", help="Multi-axis standings from the trial record (success, hidden pass, lies, cost, judge facts)")
    standings_parser.add_argument("--by", choices=["model", "signature", "harness"], default="model")

    lock_parser = subparsers.add_parser("lock", help="Lock human presence on a seat")
    lock_parser.add_argument("seat_id", type=str, help="Seat ID to presence-lock")

    unlock_parser = subparsers.add_parser("unlock", help="Release human presence on a seat")
    unlock_parser.add_argument("seat_id", type=str, help="Seat ID to unlock")

    run_parser = subparsers.add_parser(
        "run",
        help=(
            "Front door: trial a brief across a candidate limit, deliver the fact-ranked result, "
            "and ask the Secretary when tie reviewers disagree"
        ),
    )
    run_parser.add_argument("prompt", type=str, nargs="?", default="", help="What you want (or use --task)")
    run_parser.add_argument("--task", "-t", type=str, default=None, help="Task from the bank (brings hidden tests)")
    run_parser.add_argument("--role", "-r", type=str, default="developer:python", help="Role, optionally typed (developer:python, scout, secretary)")
    run_parser.add_argument(
        "--arity", "-a", "--candidates", "-n", dest="candidates", type=_positive_arity_arg, default=None,
        help=(
            "Positive maximum candidate count; may resolve fewer unique seats "
            "(precedence: this flag, ARITY, then 3)"
        ),
    )
    run_parser.add_argument(
        "--judges", type=str, default=None,
        help="Blind reviewer models on a factual tie (default: the resolved candidates)",
    )
    run_parser.add_argument("--conference", type=int, default=0, metavar="ROUNDS", help="Let the candidates sort out a final draft together")
    run_parser.add_argument("--tester", action="store_true", help="Have the tester role write hidden acceptance tests first")
    run_parser.add_argument("--out", "-o", type=str, default=None, help="Where to deliver (default: deliveries/<task_id>/)")
    run_parser.add_argument("--mock", action="store_true", help="Canned providers, no tokens")
    run_parser.add_argument("--verbose", "-v", action="store_true", help="Also print the race table")
    run_parser.add_argument("--json", action="store_true", help="Emit the report and delivery as JSON")
    auth_parser = subparsers.add_parser("auth", help="Manage OAuth subscriptions and credentials")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_action")
    auth_subparsers.add_parser("status", help="Show active credentials and expiry status")
    auth_subparsers.add_parser("import", help="Auto-import active sessions from OMP and Codex")
    
    login_cmd = auth_subparsers.add_parser("login", help="Authenticate with a provider")
    login_cmd.add_argument("provider", type=str, choices=["google", "agy", "openai", "codex", "xai", "grok", "anthropic", "claude"], help="Provider to authenticate")
    logout_cmd = auth_subparsers.add_parser("logout", help="Log out from a provider")
    logout_cmd.add_argument("provider", type=str, help="Provider to remove")

    args = parser.parse_args()

    try:
        if args.command == "run":
            resolve_arity(args.candidates, default=3)
        elif args.command == "chat":
            resolve_arity(default=1)
    except ValueError as exc:
        parser.error(str(exc))

    if args.command == "demo":
        run_demo()
    elif args.command == "race":
        handle_race_command(args)
    elif args.command == "tasks":
        show_tasks()
    elif args.command == "standings":
        show_standings(by=args.by)
    elif args.command == "trials":
        from .inspection_cli import run_trials_command
        return run_trials_command(args)
    elif args.command == "watch":
        from .watch_cli import run_watch_command
        return run_watch_command(args)
    elif args.command == "trial":
        from .inspection_cli import run_trial_command
        return run_trial_command(args)
    elif args.command == "chat":
        interactive_chat()
    elif args.command == "status":
        show_status()
    elif args.command == "skills":
        show_skills()
    elif args.command == "roles":
        show_roles()
    elif args.command == "auth":
        return handle_auth_command(args)
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
        handle_run_command(args)
    else:
        run_demo()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
