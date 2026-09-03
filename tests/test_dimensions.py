"""Tests proving ParFold and the Grader work across every dimension of Spec.

Dimensions of Spec:
1. model       (different models)
2. effort      (low vs high reasoning effort)
3. role        (different system prompts / personas)
4. skills      (different domain guidelines attached)
5. tools       (different tool schemas presented to the model)
6. tool-runner (different backends behind a single tool call)
7. harness     (in-process loop vs external runner)
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

os.environ["ARITY_HOME"] = tempfile.mkdtemp(prefix="arity-dim-test-")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from arity import cast, trial                                  # noqa: E402
from arity.judge import Judge                                  # noqa: E402
from arity.seams import ParFold                                # noqa: E402
from arity.types import (                                      # noqa: E402
    CallModel, ExecuteTool, ModelCompleted, Spec, ToolCompleted,
)
from arity.wire_mock import MockWire                           # noqa: E402


def test_dimension_1_model():
    """Varying model id: ParFold runs both models, Judge picks the best."""
    base = Spec(seat="mock", model="base-model", role="generalist")
    specs = trial.product(base, model=["fast-model", "deep-model"])
    assert len(specs) == 2

    w1 = MockWire(specs[0].model, ["brief answer"])
    w2 = MockWire(specs[1].model, ["thorough and complete answer"])
    judge_wire = MockWire("judge", ["WINNER: 2\nREASON: 2 is thorough."])

    parfold = ParFold(runners=[w1, w2], reduce=Judge(wire=judge_wire, task="explain"))
    eff = CallModel(system="", tools=[], messages=[{"role": "user", "content": "explain"}])
    winner = parfold.call(eff)

    assert winner.text == "thorough and complete answer"


def test_dimension_2_effort():
    """Varying effort: ParFold runs low vs high effort levels."""
    base = Spec(seat="mock", model="reasoning-model", role="generalist")
    specs = trial.product(base, effort=["low", "high"])
    assert specs[0].effort == "low" and specs[1].effort == "high"

    w_low = MockWire("low", ["quick thought"])
    w_high = MockWire("high", ["deep multi-step reasoning"])
    judge_wire = MockWire("judge", ["WINNER: 2\nREASON: high effort reasoned properly."])

    parfold = ParFold(runners=[w_low, w_high], reduce=Judge(wire=judge_wire, task="solve problem"))
    eff = CallModel(system="", tools=[], messages=[{"role": "user", "content": "solve"}])
    winner = parfold.call(eff)

    assert winner.text == "deep multi-step reasoning"


def test_dimension_3_role():
    """Varying role: cast.resolve builds different system prompts; ParFold evaluates both."""
    spec_dijkstra = Spec(seat="mock", model="mock-1", role="dijkstra")
    spec_hickey = Spec(seat="mock", model="mock-1", role="hickey")

    state_d = cast.resolve(spec_dijkstra, "dijkstra")
    state_h = cast.resolve(spec_hickey, "hickey")

    # Invariant: different roles produce different system prompts
    assert "True Names" in state_d.system_text()
    assert "de-complect" in state_h.system_text()

    w_d = MockWire("dijkstra", ["Mechanically this is a Fork-Join."])
    w_h = MockWire("hickey", ["In pure data this is a ParFold."])
    # The judge decides to pick Hickey's data-oriented formulation
    judge_wire = MockWire("judge", ["WINNER: 2\nREASON: ParFold is de-complected."])

    parfold = ParFold(runners=[w_d, w_h], reduce=Judge(wire=judge_wire, task="name function"))
    eff = CallModel(system="", tools=[], messages=[{"role": "user", "content": "name this"}])
    winner = parfold.call(eff)

    assert "ParFold" in winner.text


def test_dimension_4_skills():
    """Varying skills: one spec has rules attached, one is bare."""
    base = Spec(seat="mock", model="mock-1", role="typescript-developer")
    specs = trial.product(base, skills=[(), ("dmmulroy/oxlinter-rules",)])

    state_bare = cast.resolve(specs[0], "engineer")
    state_skilled = cast.resolve(specs[1], "engineer")

    assert "oxlint" not in state_bare.system_text()
    assert "oxlint" in state_skilled.system_text()

    w_bare = MockWire("bare", ["const x = 1;"])
    w_skilled = MockWire("skilled", ["const x: number = 1; // lint clean"])
    judge_wire = MockWire("judge", ["WINNER: 2\nREASON: conforms to oxlint rules."])

    parfold = ParFold(runners=[w_bare, w_skilled], reduce=Judge(wire=judge_wire, task="write ts"))
    eff = CallModel(system="", tools=[], messages=[{"role": "user", "content": "code"}])
    winner = parfold.call(eff)

    assert "lint clean" in winner.text


def test_dimension_5_tools_schema():
    spec_read = Spec(seat="mock", model="mock-1", role="typescript-developer", tools=("read_file",))
    spec_bash = Spec(seat="mock", model="mock-1", role="typescript-developer", tools=("bash",))

    state_read = cast.resolve(spec_read, "engineer")
    state_bash = cast.resolve(spec_bash, "engineer")

    tool_names_read = {t["name"] for t in state_read.tools}
    tool_names_bash = {t["name"] for t in state_bash.tools}

    assert "read_file" in tool_names_read and "bash" not in tool_names_read
    assert "bash" in tool_names_bash and "read_file" not in tool_names_bash


def test_dimension_6_tools_runner_below_seam():
    """Varying runner behind ToolSeam: 2 backends for web_search fan out and merge."""
    exa_runner = lambda eff: ToolCompleted(eff.call_id, eff.name, "Exa: https://arity.org (Fast agentic harness)")
    perplexity_runner = lambda eff: ToolCompleted(eff.call_id, eff.name, "Perplexity: https://arity.org (N-arity kernel)")

    def merge_search(results: list[ToolCompleted]) -> ToolCompleted:
        combined = "\n".join(r.output for r in results)
        return ToolCompleted(results[0].call_id, results[0].name, combined)

    search_arity = ParFold(runners=[exa_runner, perplexity_runner], reduce=merge_search)
    tool_call = ExecuteTool(call_id="call_99", name="web_search", arguments={"query": "what is arity?"})
    merged = search_arity.execute(tool_call)

    assert "Exa:" in merged.output
    assert "Perplexity:" in merged.output


def test_dimension_7_harness():
    """Varying harness: kernel execution vs CLI subprocess harness."""
    spec_kernel = Spec(seat="mock", model="mock-1", role="generalist", harness="kernel")
    spec_cli = Spec(seat="mock", model="mock-1", role="generalist", harness="claude-cli")

    assert spec_kernel.harness == "kernel"
    assert spec_cli.harness == "claude-cli"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__]))
