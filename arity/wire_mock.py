"""Wire: the plug behind the Model seam that answers from a script. No key, no network.

Used by the demo, by the "mock" seat in seats.json, and by anyone who wants
to watch the loop run without paying for it. It prints what it was handed so
the payload is visible at every hop.
"""
from __future__ import annotations

from .types import CallModel, ModelCompleted


class MockWire:
    """`script` is a list of replies, popped one per call.

        "some text"                          plain text
        ("message", "engineer", "content")   a call to the message tool

    Once the script runs out it echoes the last message back with a label.
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
