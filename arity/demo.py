"""Demo: one moment, one bot messaging another, one three-way trial. All against
mock wires, so the flow can be followed without a key.

Run it and read the printout next to the README's flow paragraph. Every line
it prints is one hop.
"""
from __future__ import annotations

from . import trial
from .loop import Loop
from .types import Message, Spec
from .wire_mock import MockWire


def mock_spec(role: str = "generalist") -> Spec:
    return Spec(seat="mock", model="mock-1", role=role)


def one_moment() -> None:
    print("\n== one moment: asa -> reception ==")
    loop = Loop(model_for=lambda spec: MockWire("reception"))
    state = loop.wake("reception", spec=mock_spec())
    print(f"  cast -> State session={state.session_id} bot={state.bot} "
          f"blocks={len(state.system)} tools={[t['name'] for t in state.tools]}")

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
    reception = loop.wake("reception", spec=mock_spec())
    loop.wake("engineer", spec=mock_spec("typescript-developer"))

    loop.run(reception, Message(sender="asa", text="get the linter run on app.ts"))
    print(f"  live kernels: {list(loop.live)}")

    # The journal is the state. Fold it back and compare.
    replayed = Loop(model_for=lambda spec: wire).resume(reception.session_id)
    print(f"  resume -> same conversation: {replayed.messages == reception.messages}")


def three_way() -> None:
    print("\n== three-way trial: same conversation, three skill sets ==")
    base = Loop(model_for=lambda spec: MockWire("base")).wake("reception", spec=mock_spec())
    base.messages.append({"role": "user", "content": "[asa] earlier context the forks all share"})

    specs = trial.product(
        base.spec,
        skills=[(), ("dmmulroy/oxlinter-rules",), ("matpock/oxlinter-101",)],
    )
    print(f"  product -> {len(specs)} specs, varying skills only")

    loop = Loop(model_for=lambda spec: MockWire(",".join(spec.skills) or "no-skill"))
    forks = trial.run(base, specs, Message(sender="asa", text="lint this file"), loop=loop)
    ranked = trial.judge(forks, pick=1)
    for s in ranked:
        print(f"  {'WIN ' if s.won else '    '} {s.result.spec.skills or '()'} -> {s.result.output}")


if __name__ == "__main__":
    one_moment()
    two_bots()
    three_way()
    # Demo kernels are never retired, so release their presence locks by hand.
    from .loop import release
    for bot in ("reception", "engineer"):
        release(bot)
