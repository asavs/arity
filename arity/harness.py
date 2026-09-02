"""Harness: where a kernel runs.

Our own loop is one harness. A headless coding CLI is another. The important
thing, and the reason this file is short, is that from the moment's point of
view they are the same: something that takes the payload and gives back a
ModelCompleted. So a harness is just another plug behind the Model seam.

    Spec.harness == "kernel"   the loop sends the payload down a wire (wire_*.py)
    Spec.harness == "claude"   the loop sends the payload to `claude -p`
    Spec.harness == "codex"    the loop sends the payload to `codex exec`
    Spec.harness == "agy"      the loop sends the payload to `agy`

What a CLI harness gives up: our tools. The CLI brings its own, runs them
itself, and only the final text comes back. So a CLI-harnessed kernel has an
empty tool block and one ModelCompleted per turn with no tool_calls. What it
gives back: a whole agentic run for the price of one call.

This bends the "naive from scratch" rule for 1.0.0. It is here because it
costs thirty lines and makes "same prompt through three harnesses" a trial
like any other.
"""
from __future__ import annotations

import subprocess

from . import seats
from .types import CallModel, ModelCompleted, Spec
from .wire_anthropic import AnthropicWire
from .wire_mock import MockWire
from .wire_openai import OpenAIWire

# Checked on this machine 2026-09-02. claude and codex read the prompt from
# stdin; agy only takes it as an argument, which puts the whole payload on the
# command line (Windows caps that around 32k characters).
COMMANDS = {
    "claude": (["claude", "-p", "--output-format", "text"], "stdin"),
    "codex":  (["codex", "exec"], "stdin"),
    "agy":    (["agy", "--prompt"], "arg"),
}


class CLIHarness:
    """ModelSeam. Hands the whole payload as one prompt to a headless CLI."""

    def __init__(self, name: str):
        self.command, self.via = COMMANDS[name]

    def call(self, effect: CallModel) -> ModelCompleted:
        prompt = effect.system + "\n\n" + "\n\n".join(
            f"[{m['role']}] {m['content']}" for m in effect.messages)
        if self.via == "stdin":
            done = subprocess.run(self.command, input=prompt, capture_output=True, text=True)
        else:
            done = subprocess.run(self.command + [prompt], capture_output=True, text=True)
        return ModelCompleted(text=done.stdout.strip(), tool_calls=[], usage={})


def for_spec(spec: Spec):
    """The loop asks this for its Model seam. One place decides wire or CLI."""
    if spec.harness != "kernel":
        return CLIHarness(spec.harness)
    provider = seats.lookup(spec.seat).provider
    if provider == "anthropic":
        return AnthropicWire(spec.seat, spec.model, spec.effort)
    if provider == "mock":
        return MockWire(spec.model)
    return OpenAIWire(spec.seat, spec.model, spec.effort)
