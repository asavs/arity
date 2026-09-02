"""Demo: one moment, one bot messaging another, one three-way trial. All against
mock wires, so the flow can be followed without a key.

Run it and read the printout next to the README's flow paragraph. Every line
it prints is one hop.
"""
from __future__ import annotations

from . import cast, trial
from .loop import Loop
from .types import Message, Spec
from .wire_mock import MockWire


def mock_spec(role: str = "generalist") -> Spec:
    return Spec(seat="mock", model="mock-1", role=role)


def one_moment() -> None:
    print("\n== one moment: asa -> reception ==")
    state = cast.resolve(mock_spec(), bot="reception")
    print(f"  cast -> State session={state.session_id} bot={state.bot} "
          f"blocks={len(state.system)} tools={[t['name'] for t in state.tools]}")

    loop = Loop(model_for=lambda spec: MockWire("reception"))
    final = loop.run(state, Message(sender="asa", text="hi, what are you?"))
    print(f"  loop -> idle with output: {final.output!r}")


def two_bots() -> None:
    print("\n== two bots: asa -> reception -> engineer -> reception -> asa ==")
    # One loop serves every kernel. One scripted wire answers for whichever bot asks.
    script = [
        ("message", "engineer", "can you lint src/app.ts for me?"),   # reception asks engineer
        "ran oxlint: 2 errors fixed, 0 warnings.",                    # engineer answers
        "engineer says: 2 errors fixed, 0 warnings.",                 # reception reports to asa
    ]
    wire = MockWire("shared", script)
    loop = Loop(model_for=lambda spec: wire)
    loop.live["reception"] = cast.resolve(mock_spec(), bot="reception")
    loop.live["engineer"] = cast.resolve(mock_spec("typescript-developer"), bot="engineer")

    loop.run(loop.live["reception"], Message(sender="asa", text="get the linter run on app.ts"))
    print(f"  live kernels: {list(loop.live)}")


def three_way() -> None:
    print("\n== three-way trial: same conversation, three skill sets ==")
    base = cast.resolve(mock_spec(), bot="reception")
    base.messages.append({"role": "user", "content": "[asa] earlier context the forks all share"})

    specs = trial.product(
        base.spec,
        skills=[(), ("dmmulroy/oxlinter-rules",), ("matpock/oxlinter-101",)],
    )
    print(f"  product -> {len(specs)} specs, varying skills only")

    loop = Loop(model_for=lambda spec: MockWire(",".join(spec.skills) or "no-skill"))
    ranked = trial.run(base, specs, Message(sender="asa", text="lint this file"), pick=1, loop=loop)
    for s in ranked:
        print(f"  {'WIN ' if s.won else '    '} {s.result.spec.skills or '()'} -> {s.result.output}")


if __name__ == "__main__":
    one_moment()
    two_bots()
    three_way()
