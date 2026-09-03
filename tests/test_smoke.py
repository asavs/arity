"""One smoke test. Not a suite.

It runs the whole loop against the mock wire in a throwaway home and checks
the four things that would mean the kernel is broken: a moment answers, a
bot can message a bot, a journal folds back into the same conversation, and
a trial records an outcome the scorecard can count.

    python -m pytest tests/        or        python tests/test_smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["ARITY_HOME"] = tempfile.mkdtemp(prefix="arity-test-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arity import cast, scorecard, store, trial    # noqa: E402
from arity.loop import Loop                        # noqa: E402
from arity.types import ExecuteTool, Message, Send, Spec, Status
from arity.wire_mock import MockWire               # noqa: E402

# Cast picks the mock seat, so bots woken by the post office need no key.
cast.DEFAULT_MODEL = "mock-1"
MOCK = Spec(seat="mock", model="mock-1", role="generalist")


def test_one_moment_answers():
    loop = Loop(model_for=lambda spec: MockWire("r"))
    state = loop.wake("reception", spec=MOCK)
    state = loop.run(state, Message("asa", "hello"))
    assert state.output and "hello" in state.output
    assert store.birth_of(state.session_id)["bot"] == "reception"


def test_bot_messages_bot_and_journal_folds_back():
    wire = MockWire("shared", [("message", "engineer", "lint it"), "done: 0 errors", "engineer says done"])
    loop = Loop(model_for=lambda spec: wire)
    reception = loop.wake("reception")
    loop.run(reception, Message("asa", "get it linted"))     # the post office wakes the engineer

    assert reception.output == "engineer says done"
    engineer = loop.live["engineer"]
    assert engineer.spec.role == "typescript-developer"
    assert store.birth_of(engineer.session_id)["parent"]["session"] == reception.session_id

    replayed = Loop(model_for=lambda spec: wire).resume(reception.session_id)
    assert replayed.messages == reception.messages


def test_trial_records_an_outcome_the_scorecard_counts():
    base = Loop(model_for=lambda spec: MockWire("b")).wake("reception", spec=MOCK)
    specs = trial.product(base.spec, model=["mock-1", "mock-2"])
    loop = Loop(model_for=lambda spec: MockWire(spec.model))
    forks = trial.run(base, specs, Message("asa", "which is better?"), loop=loop)
    ranked = trial.judge(forks, pick=1)

    assert ranked[0].won and ranked[0].result.spec.model == "mock-2"
    assert scorecard.ranked("generalist")[0] == "mock-2"
    assert store.birth_of(forks[0].session_id)["parent"] == {"session": base.session_id}

    # A fork is retired when it answers, and the winner's turn folds into the base.
    assert not any(f.session_id in store.unfinished() for f in forks)
    store.adopt(base.session_id, forks[1].session_id)
    base.messages = list(forks[1].messages)
    replayed = Loop(model_for=lambda spec: MockWire("mock-2")).resume(base.session_id)
    assert replayed.messages == base.messages


def test_busy_bot_queues_message_for_next_turn():
    wire = MockWire("shared", ["tool finished", "next turn processed"])
    loop = Loop(model_for=lambda spec: wire)
    engineer = loop.wake("engineer")
    engineer.status = Status.WAITING_TOOLS  # Engineer is currently busy waiting on tools!

    reception = loop.wake("reception")
    # Reception messages Engineer while Engineer is busy:
    result = loop.deliver(reception, Send(to="engineer", text="urgent update", call_id="c1"))

    # 1. Reception sees Engineer's busy status immediately:
    assert result is not None
    assert "currently busy" in result.output
    assert "waiting_tools" in result.output
    assert "next turn boundary" in result.output

    # 2. The message is waiting safely in Engineer's pending queue:
    assert len(engineer.pending) == 1
    assert engineer.pending[0].text == "urgent update"

    # 3. When Engineer finishes its current turn and goes IDLE, pending is drained:
    engineer.status = Status.IDLE
    loop.run(engineer, Message("asa", "finish work"))
    assert len(engineer.pending) == 0

    # 4. Engineer's reply landed cleanly in Reception's pending queue without call stack recursion:
    assert len(reception.pending) == 1
    assert reception.pending[0].text == "next turn processed"

def test_addressing_new_bot_spawns_clean_desk():
    wire = MockWire("shared", ["first answer", "second answer"])
    loop = Loop(model_for=lambda spec: wire)
    reception = loop.wake("reception")

    # 1. First interaction with engineer builds up context:
    res1 = loop.deliver(reception, Send(to="engineer", text="first task", call_id="c1"))
    eng1 = loop.live["engineer"]
    assert res1 is not None and res1.output == "first answer"
    assert len(eng1.messages) >= 2
    first_session = eng1.session_id

    # 2. Reception asks for new:engineer (fresh worker with clean desk):
    res2 = loop.deliver(reception, Send(to="new:engineer", text="fresh task", call_id="c2"))
    eng2 = loop.live["engineer"]
    assert res2 is not None and res2.output == "second answer"
    assert eng2.session_id != first_session
    # eng2 has only the fresh task and its answer (no baggage from task 1):
    assert not any("first task" in str(m.get("content", "")) for m in eng2.messages)
    assert any("fresh task" in str(m.get("content", "")) for m in eng2.messages)

def test_engineer_has_hands_read_file_and_bash():
    loop = Loop(model_for=lambda spec: MockWire("m"))
    engineer = loop.wake("engineer")
    tool_names = [t["name"] for t in engineer.tools]
    assert "read_file" in tool_names
    assert "bash" in tool_names

    tools = loop.tools_for(engineer.spec)

    # 1. Dedicated tool: read_file
    res_read = tools.execute(ExecuteTool(call_id="c1", name="read_file", arguments={"path": "pyproject.toml"}))
    assert "project" in res_read.output or "build-system" in res_read.output

    # 2. Shell tool: bash
    res_bash = tools.execute(ExecuteTool(call_id="c2", name="bash", arguments={"command": "echo arity-hands"}))
    assert "arity-hands" in res_bash.output

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
