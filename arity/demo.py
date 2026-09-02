"""Demo: one moment, one bot messaging another, one three-way trial. All against
mock wires, so the flow can be followed without a key.

Run it and read the printout next to the README's flow paragraph. Every line
it prints is one hop.
"""
from __future__ import annotations

from . import cast, trial
from .loop import Loop
from .seams import LocalTools
from .types import CallModel, Message, ModelCompleted, Spec


class MockWire:
    """ModelSeam. Answers from a short script instead of a provider.

    `script` is a list of replies. A string is plain text. A ("message", to,
    content) tuple is a call to the message tool. The wire pops one per call
    and falls back to echoing once the script runs out.
    """

    def __init__(self, label: str, script: list | None = None):
        self.label = label
        self.script = list(script or [])

    def call(self, effect: CallModel) -> ModelCompleted:
        print(f"    wire[{self.label}] <- payload: {len(effect.tools)} tools, "
              f"{len(effect.system)} chars of system, {len(effect.messages)} messages")
        if self.script:
            step = self.script.pop(0)
            if isinstance(step, tuple):
                _, to, content = step
                call = {"id": "c1", "name": "message", "arguments": {"to": to, "content": content}}
                return ModelCompleted(text="", tool_calls=[call], usage={})
            return ModelCompleted(text=step, tool_calls=[], usage={})
        last = str(effect.messages[-1]["content"])
        return ModelCompleted(text=f"({self.label}) heard: {last[:48]}", tool_calls=[], usage={})


def mock_spec() -> Spec:
    return Spec(seat="mock", model="mock-1", role="generalist")


def one_moment() -> None:
    print("\n== one moment: asa -> reception ==")
    state = cast.resolve(mock_spec(), bot="reception")
    print(f"  cast -> State session={state.session_id} bot={state.bot} "
          f"blocks={len(state.system)} tools={[t['name'] for t in state.tools]}")

    loop = Loop(model=MockWire("reception"), tools=LocalTools([]))
    final = loop.run(state, Message(sender="asa", text="hi, what are you?"))
    print(f"  loop -> idle with output: {final.output!r}")


def two_bots() -> None:
    print("\n== two bots: asa -> reception -> engineer -> reception -> asa ==")
    # One loop serves every kernel; the mock wire answers for whichever bot is asking.
    script = [
        ("message", "engineer", "can you lint src/app.ts for me?"),   # reception asks engineer
        "ran oxlint: 2 errors fixed, 0 warnings.",                    # engineer answers
        "engineer says: 2 errors fixed, 0 warnings.",                 # reception reports to asa
    ]
    loop = Loop(model=MockWire("shared", script), tools=LocalTools([]))
    reception = cast.resolve(mock_spec(), bot="reception")
    loop.live["reception"] = reception
    # cast.birth would pick a real seat for the engineer; give it the mock spec instead
    loop.live["engineer"] = cast.resolve(Spec("mock", "mock-1", "typescript-developer"), bot="engineer")

    loop.run(reception, Message(sender="asa", text="get the linter run on app.ts"))
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

    def mock_loop(spec: Spec) -> Loop:
        return Loop(model=MockWire(",".join(spec.skills) or "no-skill"), tools=LocalTools([]))

    ranked = trial.run(base, specs, Message(sender="asa", text="lint this file"),
                       pick=1, make_loop=mock_loop)
    for s in ranked:
        print(f"  {'WIN ' if s.won else '    '} {s.result.spec.skills or '()'} -> {s.result.output}")


if __name__ == "__main__":
    one_moment()
    two_bots()
    three_way()
