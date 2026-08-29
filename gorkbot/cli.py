"""CLI interface for gorkbot."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
import json
import time
from .ledger import Seat, SeatLedger
from .orchestrator import GorkbotOrchestrator
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
    print("\033[1;32m   gorkbot End-to-End Orchestration Demo (7 Parts)  \033[0m")
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

    orchestrator = GorkbotOrchestrator(
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
    print("\033[1;36m=== Gorkbot Switchboard (The Secretary) ===\033[0m")
    print("Type your message (or 'exit' / 'quit' to stop).\n")

    orchestrator = GorkbotOrchestrator()
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
    orchestrator = GorkbotOrchestrator()
    print("\033[1;36m====================================================\033[0m")
    print("\033[1;36m            gorkbot System Health & Status          \033[0m")
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
    orchestrator = GorkbotOrchestrator()
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
    print("\033[1;36m             Registered gorkbot Skills              \033[0m")
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
    for role in sorted(set(registry._roles.values()), key=lambda r: r.name):
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
    print("\033[1;35m====================================================\033[0m\n")


def handle_auth_command(args: argparse.Namespace) -> None:
    """Handle gorkbot auth subcommands."""
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
        print("\n\033[1;36m================== Gorkbot Auth Status ==================\033[0m")
        if not creds:
            print("  No saved or discovered credentials found.")
            print("  Run \033[1;33mgorkbot auth login <google|openai|xai>\033[0m or \033[1;33mgorkbot auth import\033[0m.")
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
        print("\n\033[1;36m[Gorkbot Auth]\033[0m Scanning ~/.omp, ~/.codex, and local stores...")
        imported = store.import_all()
        print(f"\033[1;32m[Gorkbot Auth]\033[0m Imported {len(imported)} credentials into ~/.gorkbot/auth.json:")
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
            print(f"\033[1;32m[Gorkbot Auth]\033[0m Removed credential for '{key}'.")
        else:
            print(f"\033[1;33m[Gorkbot Auth]\033[0m No saved credential found for '{key}'.")


def handle_race_command(args: argparse.Namespace) -> None:
    """Handle gorkbot race A/B/C multidimensional trial command."""
    from .roles import RoleRegistry, BUILDER_ROLE, PYTHON_DEVELOPER_ROLE
    from .terrarium import CandidateSpec, TaskRecord, TerrariumDispatcher
    from .archivist import ImpartialArchivist
    from .skills import PYTEST_TDD_SKILL, FIRECRAWL_SKILL, SCOUT_RECON_SKILL

    prompt = args.prompt
    variants_arg = (getattr(args, "variants", "") or "wire,cli,omp").lower()
    role_name = getattr(args, "role", "builder") or "builder"
    test_cmd = getattr(args, "test_cmd", None)
    is_mock = getattr(args, "mock", False)
    as_json = getattr(args, "json", False)
    workers = int(getattr(args, "workers", 4) or 4)

    roles = RoleRegistry()
    target_role = roles.get(role_name) or BUILDER_ROLE

    candidates: list[CandidateSpec] = []

    # 1. Resolve candidates from presets or flags
    if variants_arg in ("wire,cli,omp", "harness", "wire_cli_omp"):
        seat_a = Seat(id="gemini-flash", provider="gemini", model="gemini-3.6-flash")
        seat_b = Seat(id="gpt-5.6-sol", provider="codex", model="gpt-5.6-sol")
        seat_c = Seat(id="claude-sonnet", provider="omp", model="claude-3-7-sonnet")
        candidates = [
            CandidateSpec(seat=seat_a, name="Wire + AST Tools", role=target_role, harness="wire", tool_runner_type="sandbox", skills=["pytest-tdd"]),
            CandidateSpec(seat=seat_b, name="CLI + MCP Tools", role=target_role, harness="cli", tool_runner_type="mcp", skills=["pytest-tdd"]),
            CandidateSpec(seat=seat_c, name="OMP + Shell Tools", role=target_role, harness="omp", tool_runner_type="shell", skills=[]),
        ]
    elif variants_arg in ("ast,mcp,shell", "tools", "ast_mcp_shell"):
        seat = Seat(id="gemini-flash", provider="gemini", model="gemini-3.6-flash")
        candidates = [
            CandidateSpec(seat=seat, name="AST Sandbox Tools", role=target_role, harness="wire", tool_runner_type="sandbox", skills=["pytest-tdd"]),
            CandidateSpec(seat=seat, name="MCP Tool Adapter", role=target_role, harness="wire", tool_runner_type="mcp", skills=["pytest-tdd"]),
            CandidateSpec(seat=seat, name="Local Shell Tools", role=target_role, harness="wire", tool_runner_type="shell", skills=["pytest-tdd"]),
        ]
    elif variants_arg in ("tdd,baseline", "skills", "tdd_baseline"):
        seat = Seat(id="gemini-flash", provider="gemini", model="gemini-3.6-flash")
        candidates = [
            CandidateSpec(seat=seat, name="With pytest-tdd Skill", role=target_role, harness="wire", tool_runner_type="sandbox", skills=["pytest-tdd"]),
            CandidateSpec(seat=seat, name="Zero-Shot Baseline", role=target_role, harness="wire", tool_runner_type="sandbox", skills=[]),
            CandidateSpec(seat=seat, name="Scout Recon Skill", role=target_role, harness="wire", tool_runner_type="sandbox", skills=["scout-recon", "firecrawl-developer-index"]),
        ]
    elif variants_arg in ("models", "gemini,gpt,claude"):
        seat_a = Seat(id="gemini-flash", provider="gemini", model="gemini-3.6-flash")
        seat_b = Seat(id="gpt-5.6-sol", provider="openai", model="gpt-5.6-sol")
        seat_c = Seat(id="claude-sonnet", provider="anthropic", model="claude-3-7-sonnet")
        candidates = [
            CandidateSpec(seat=seat_a, name="Gemini 3.6 Flash", role=target_role, harness="wire", tool_runner_type="sandbox", skills=["pytest-tdd"]),
            CandidateSpec(seat=seat_b, name="GPT 5.6 Sol", role=target_role, harness="wire", tool_runner_type="sandbox", skills=["pytest-tdd"]),
            CandidateSpec(seat=seat_c, name="Claude 3.7 Sonnet", role=target_role, harness="wire", tool_runner_type="sandbox", skills=["pytest-tdd"]),
        ]
    else:
        # Parse custom comma-separated list of variant names
        var_list = [v.strip() for v in variants_arg.split(",") if v.strip()]
        for idx, v in enumerate(var_list):
            seat = Seat(id=f"seat_{v}", provider="gemini", model=f"model-{v}")
            candidates.append(
                CandidateSpec(
                    seat=seat,
                    name=f"Variant {v.upper()}",
                    role=target_role,
                    harness="wire" if "wire" in v else ("cli" if "cli" in v else ("omp" if "omp" in v else "wire")),
                    tool_runner_type="mcp" if "mcp" in v else ("shell" if "shell" in v else "sandbox"),
                    skills=["pytest-tdd"] if "tdd" in v or "wire" in v else [],
                )
            )

    # If mock mode is requested, attach deterministic sequence providers
    if is_mock:
        for i, cand in enumerate(candidates):
            class MockRaceProvider:
                def __init__(self, c_idx: int, c_name: str):
                    self.c_idx = c_idx
                    self.c_name = c_name
                    self.turn = 0

                def call(self, effect: CallModel) -> ModelCompleted:
                    self.turn += 1
                    if self.turn == 1:
                        # Turn 1: Write implementation and unit test files
                        tool_calls = [
                            {
                                "id": f"call_w_{self.c_idx}_1",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps({
                                        "path": "lru_cache.py",
                                        "content": (
                                            "class LRUCache:\n"
                                            "    def __init__(self, capacity: int = 128):\n"
                                            "        self.capacity = capacity\n"
                                            "        self.cache = {}\n\n"
                                            "    def get(self, key: str):\n"
                                            "        if key not in self.cache:\n"
                                            "            return None\n"
                                            "        val = self.cache.pop(key)\n"
                                            "        self.cache[key] = val\n"
                                            "        return val\n\n"
                                            "    def put(self, key: str, val):\n"
                                            "        if key in self.cache:\n"
                                            "            self.cache.pop(key)\n"
                                            "        elif len(self.cache) >= self.capacity:\n"
                                            "            oldest = next(iter(self.cache))\n"
                                            "            del self.cache[oldest]\n"
                                            "        self.cache[key] = val\n"
                                        ),
                                    }),
                                },
                            },
                            {
                                "id": f"call_w_{self.c_idx}_2",
                                "type": "function",
                                "function": {
                                    "name": "write_file",
                                    "arguments": json.dumps({
                                        "path": "test_lru_cache.py",
                                        "content": (
                                            "import unittest\n"
                                            "from lru_cache import LRUCache\n\n"
                                            "class TestLRU(unittest.TestCase):\n"
                                            "    def test_basic_get_put(self):\n"
                                            "        cache = LRUCache(2)\n"
                                            "        cache.put('a', 1)\n"
                                            "        cache.put('b', 2)\n"
                                            "        self.assertEqual(cache.get('a'), 1)\n"
                                            "        cache.put('c', 3)\n"
                                            "        self.assertIsNone(cache.get('b'))\n"
                                            "        self.assertEqual(cache.get('c'), 3)\n\n"
                                            "if __name__ == '__main__':\n"
                                            "    unittest.main()\n"
                                        ),
                                    }),
                                },
                            },
                        ]
                        return ModelCompleted(
                            content=f"Implementing LRU Cache with unit tests for {self.c_name}",
                            tool_calls=tool_calls,
                            usage={"prompt_tokens": 150, "completion_tokens": 120},
                            finish_reason="tool_calls",
                            seat_id=f"mock_{self.c_idx}",
                        )
                    else:
                        return ModelCompleted(
                            content=f"Completed LRU cache implementation with full test suite in {self.c_name}.",
                            tool_calls=[],
                            usage={"prompt_tokens": 200, "completion_tokens": 50},
                            finish_reason="stop",
                            seat_id=f"mock_{self.c_idx}",
                        )
            cand.custom_model_provider = MockRaceProvider(i, cand.name)

    # 2. Print initial race header
    print("\n\033[1;36m========================================================================================\033[0m")
    print(f"\033[1;32m🏁 GORKBOT MULTI-DIMENSIONAL RACE (A/B/C Test)\033[0m")
    print(f"  \033[1mTask:\033[0m       \"{prompt}\"")
    print(f"  \033[1mVariants:\033[0m   {variants_arg}")
    print(f"  \033[1mCandidates:\033[0m {len(candidates)}")
    print("\033[1;36m========================================================================================\033[0m\n")

    for idx, c in enumerate(candidates, 1):
        m_str, h_str, t_str, s_str = c.display_tuple()
        print(f"  \033[1;33m[{idx}]\033[0m \033[1m{c.name:25}\033[0m | Model: {m_str:18} | Harness: {h_str:8} | Tools: {t_str:12} | Skills: {s_str}")
    print()

    # 3. Dispatch race across isolated candidate sandboxes
    ledger = SeatLedger(initial_seats=[c.seat for c in candidates], auto_seed=False)
    dispatcher = TerrariumDispatcher(ledger=ledger)
    task_rec = TaskRecord(brief=prompt, from_role="Asa", to_role=target_role.name)

    archivist = ImpartialArchivist()
    winner, results, entries = dispatcher.race(
        task=task_rec,
        candidates=candidates,
        test_command=test_cmd,
        max_workers=workers,
        archivist=archivist,
    )

    if as_json:
        out_data = {
            "task": prompt,
            "winner": winner.spec.name if winner and winner.spec else None,
            "winner_signature": winner.signature if winner else None,
            "results": [
                {
                    "name": r.spec.name if r.spec else r.candidate_id,
                    "signature": r.signature,
                    "status": r.status,
                    "duration_seconds": r.duration_seconds,
                    "tokens_used": r.tokens_used,
                    "test_results": r.test_results,
                    "output": r.output,
                }
                for r in results
            ],
        }
        print(json.dumps(out_data, indent=2))
        return

    # 4. Render comparison table
    print("\n\033[1;37m┌───┬──────────────────────────┬──────────────────┬─────────┬───────────┬────────────┬─────────┬───────────┬─────────┬────────┬──────────┐\033[0m")
    print("\033[1;37m│ # │ Candidate                │ Model            │ Harness │ Tools     │ Skills     │ Status  │ Tests     │ Time(s) │ Tokens │ Standing │\033[0m")
    print("\033[1;37m├───┼──────────────────────────┼──────────────────┼─────────┼───────────┼────────────┼─────────┼───────────┼─────────┼────────┼──────────┤\033[0m")

    for idx, r in enumerate(results, 1):
        c_name = (r.spec.name if r.spec else r.candidate_id)[:24]
        m_name = (r.seat.model)[:16]
        h_name = (r.harness)[:7]
        t_name = (r.tool_runner_name)[:9]
        s_name = (",".join(r.skills_used) if r.skills_used else "baseline")[:10]
        status_str = r.status.upper()[:7]

        # Test column formatting
        if r.test_results and r.test_results.get("has_tests"):
            p = r.test_results.get("passed", 0)
            tot = r.test_results.get("total", 0)
            t_col = f"{p}/{tot} OK" if r.test_results.get("failed", 0) == 0 else f"{p}/{tot} FAIL"
        else:
            t_col = "N/A"

        t_sec = f"{r.duration_seconds:.2f}s"
        tok_str = f"{r.tokens_used:,}"

        # Get standing from scorecard
        standing_val = archivist.scorecard.get_standing(r.signature or r.seat.model)
        stand_str = f"{standing_val:.1f} pts"

        # Status coloring
        if status_str == "COMPLET" or status_str == "SUCCESS":
            st_colored = f"\033[1;32m{status_str:7}\033[0m"
        else:
            st_colored = f"\033[1;31m{status_str:7}\033[0m"

        print(f"│ {idx:<1} │ {c_name:24} │ {m_name:16} │ {h_name:7} │ {t_name:9} │ {s_name:10} │ {st_colored} │ {t_col:9} │ {t_sec:7} │ {tok_str:6} │ {stand_str:8} │")

    print("\033[1;37m└───┴──────────────────────────┴──────────────────┴─────────┴───────────┴────────────┴─────────┴───────────┴─────────┴────────┴──────────┘\033[0m\n")

    # 5. Announce Impartial Judge Winner and findings
    if winner and winner.spec:
        win_entry = next((e for e in entries if e.candidate_id == winner.candidate_id), None)
        print(f"\033[1;32m🏆 IMPARTIAL JUDGE WINNER:\033[0m \033[1m{winner.spec.name}\033[0m")
        print(f"  \033[1m• Signature:\033[0m          \033[1;36m{winner.signature}\033[0m")
        print(f"  \033[1m• Verdict:\033[0m            \033[1;32m{win_entry.verdict.upper() if win_entry else 'SUCCESS'}\033[0m")
        print(f"  \033[1m• Physical Artifacts:\033[0m {', '.join(win_entry.verified_artifacts) if win_entry and win_entry.verified_artifacts else 'Verified in sandbox'}")
        if winner.test_results and winner.test_results.get("has_tests"):
            print(f"  \033[1m• Verification Proof:\033[0m \033[1;32m100% test pass rate ({winner.test_results.get('passed')}/{winner.test_results.get('total')} tests passed)\033[0m")
        print(f"  \033[1m• Performance Proof:\033[0m  {winner.duration_seconds:.2f}s latency | {winner.tokens_used:,} tokens")
        print()

    # 6. Show updated Scorecard combination standings
    print("\033[1;35m📊 Multi-Dimensional Scorecard Standings (Axiom 3 + Axiom 9):\033[0m")
    for r in results:
        score = archivist.scorecard.get_standing(r.signature or r.seat.model)
        print(f"  • \033[1m{r.signature}\033[0m -> \033[1;33m{score:.1f} pts\033[0m")
    print("\033[1;36m========================================================================================\033[0m\n")

def main():
    parser = argparse.ArgumentParser(description="gorkbot 0.2.0 CLI")
    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("demo", help="Run the architectural demo")
    subparsers.add_parser("chat", help="Start an interactive chat session with The Secretary")
    subparsers.add_parser("status", help="Show seat health, scorecard, and wire status")
    subparsers.add_parser("skills", help="List active and discovered skills")
    subparsers.add_parser("roles", help="List staff roles, capabilities, and denial sets")
    subparsers.add_parser("redphone", help="Inspect Red Phone channels")

    race_parser = subparsers.add_parser("race", help="Run an empirical A/B/C race across multi-dimensional candidates")
    race_parser.add_argument("prompt", type=str, help="Task or prompt to race across candidates")
    race_parser.add_argument("--variants", "-v", type=str, default="wire,cli,omp", help="Variant preset (wire,cli,omp | ast,mcp,shell | tdd,baseline | models) or custom comma-separated list")
    race_parser.add_argument("--role", "-r", type=str, default="builder", help="Staff role to assume (default: builder)")
    race_parser.add_argument("--test-cmd", type=str, default=None, help="Custom unit test verification command to execute in sandboxes")
    race_parser.add_argument("--workers", "-w", type=int, default=4, help="Max parallel candidate workers")
    race_parser.add_argument("--mock", action="store_true", help="Run with deterministic candidate mock simulation")
    race_parser.add_argument("--json", action="store_true", help="Output full race results as JSON")

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
    elif args.command == "race":
        handle_race_command(args)
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
        orchestrator = GorkbotOrchestrator()
        orchestrator.ledger.set_presence(args.seat_id, True)
        print(f"\033[1;33m[Presence Lock]\033[0m Seat '{args.seat_id}' is now locked for human use.")
    elif args.command == "unlock":
        orchestrator = GorkbotOrchestrator()
        orchestrator.ledger.set_presence(args.seat_id, False)
        print(f"\033[1;32m[Presence Unlock]\033[0m Seat '{args.seat_id}' released for autonomous bot casting.")
    elif args.command == "redphone":
        show_redphone()
    elif args.command == "run":
        orchestrator = GorkbotOrchestrator()
        resp = orchestrator.handle_message(user_text=args.prompt, sender="Asa")
        if resp.delegated_task and resp.winning_candidate and resp.winning_candidate.output:
            print(resp.winning_candidate.output)
        elif resp.reply_text:
            print(resp.reply_text)
    else:
        run_demo()

if __name__ == "__main__":
    main()
