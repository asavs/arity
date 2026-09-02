"""Demo: one moment, then one three-way trial, against a mock wire.

Run it and read the printout next to the README's flow paragraph. Every line
it prints is one hop. No key is needed; the mock wire answers from a script.
"""
from __future__ import annotations

from . import cast, trial
from .loop import Loop
from .seams import LocalTools
from .types import CallModel, ModelCompleted, Spec, UserMessage


class MockWire:
    """ModelSeam. Answers with a canned line that names the spec it was asked as."""

    def __init__(self, label: str):
        self.label = label

    def call(self, effect: CallModel) -> ModelCompleted:
        print(f"    wire[{self.label}] <- payload: {len(effect.tools)} tools, "
              f"{len(effect.system)} chars of system, {len(effect.messages)} messages")
        last = effect.messages[-1]["content"]
        return ModelCompleted(text=f"({self.label}) heard: {last[:40]}", tool_calls=[], usage={})


def one_moment() -> None:
    print("\n== one moment ==")
    spec = Spec(seat="mock", model="mock-1", role="generalist")
    state = cast.resolve(spec, bot="reception")
    print(f"  cast -> State session={state.session_id} bot={state.bot} "
          f"blocks={len(state.system)} tools={len(state.tools)}")

    loop = Loop(model=MockWire("A"), tools=LocalTools([]))
    final = loop.run(state, UserMessage("hi, what are you?"))
    print(f"  loop -> halted with output: {final.output!r}")


def three_way() -> None:
    print("\n== three-way trial ==")
    base = cast.resolve(Spec(seat="mock", model="mock-1", role="generalist"), bot="reception")
    base.messages.append({"role": "user", "content": "earlier context the forks all share"})

    specs = trial.product(
        base.spec,
        skills=[(), ("dmmulroy/oxlinter-rules",), ("matpock/oxlinter-101",)],
    )
    print(f"  product -> {len(specs)} specs, varying skills only")

    # In real use trial.run picks the Model seam per spec via harness.for_spec.
    # Here we monkeypatch it so every fork talks to a labelled mock.
    import arity.trial as t
    t.for_spec = lambda spec: MockWire(",".join(spec.skills) or "no-skill")

    ranked = trial.run(base, specs, UserMessage("lint this file"), task_kind="lint", pick=1)
    for s in ranked:
        print(f"  {'WIN ' if s.won else '    '} {s.result.spec.skills or '()'} -> {s.result.output}")


if __name__ == "__main__":
    one_moment()
    three_way()
