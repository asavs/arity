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

from arity import scorecard, store, trial          # noqa: E402
from arity.loop import Loop                        # noqa: E402
from arity.types import Message, Spec              # noqa: E402
from arity.wire_mock import MockWire               # noqa: E402

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
    reception = loop.wake("reception", spec=MOCK)
    loop.wake("engineer", spec=Spec("mock", "mock-1", "typescript-developer"))
    loop.run(reception, Message("asa", "get it linted"))

    assert reception.output == "engineer says done"
    engineer = loop.live["engineer"]
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
    assert scorecard.best_spec("generalist").model == "mock-2"
    assert store.birth_of(forks[0].session_id)["parent"] == {"session": base.session_id}


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok ", name)
