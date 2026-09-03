"""The Grader: reduces N model candidate outputs down to 1.

Hybrid evaluation:
1. Deterministic filter: Disqualifies errors, crashes, and empty text.
   If only one candidate survives, return it immediately (zero tokens spent).
2. Semantic judge: Prompts a model to compare the survivors, parse the winning
   number, and return the winning ModelCompleted.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from .types import CallModel, ModelCompleted


def is_clean(candidate: ModelCompleted) -> bool:
    """True if the candidate returned meaningful text or tool calls without an error."""
    text = (candidate.text or "").strip()
    if not text and not candidate.tool_calls:
        return False
    if text.startswith("error:") or text.startswith("(no answer:"):
        return False
    return True


class Judge:
    """A callable reducer for ParFold: candidates -> winning ModelCompleted."""

    def __init__(
        self,
        wire: Any = None,
        task: str = "",
        fallback: Callable[[list[ModelCompleted]], ModelCompleted] | None = None,
    ):
        self.wire = wire
        self.task = task
        self.fallback = fallback or (lambda cs: cs[0])

    def __call__(self, candidates: list[ModelCompleted]) -> ModelCompleted:
        if not candidates:
            return ModelCompleted(text="error: no candidates to judge", tool_calls=[], usage={})

        # 1. Deterministic layer: filter out errors and empty answers
        survivors = [(i + 1, c) for i, c in enumerate(candidates) if is_clean(c)]

        if not survivors:
            # Everything failed; fallback to whatever we have
            return self.fallback(candidates)

        if len(survivors) == 1:
            # Only one clean candidate survived; no need to spend judge tokens
            return survivors[0][1]

        if self.wire is None:
            # No model wire provided; fallback deterministically (e.g. first clean)
            return survivors[0][1]

        # 2. Semantic layer: ask the judge model to pick the best
        prompt = self._build_prompt(survivors)
        effect = CallModel(
            system="You are an impartial judge. Compare the candidates and pick the best one.",
            tools=[],
            messages=[{"role": "user", "content": prompt}],
        )

        try:
            decision = self.wire.call(effect)
            pick_idx = self._parse_pick(decision.text, len(candidates))
            if pick_idx is not None:
                return candidates[pick_idx]
        except Exception:
            pass

        return survivors[0][1]

    def _build_prompt(self, survivors: list[tuple[int, ModelCompleted]]) -> str:
        blocks = [f"Original task:\n{self.task or '(unspecified task)'}\n"]
        for num, c in survivors:
            content = c.text
            if c.tool_calls:
                tool_names = ", ".join(call["name"] for call in c.tool_calls)
                content = f"[tool calls: {tool_names}]\n{content}"
            blocks.append(f"--- Candidate {num} ---\n{content}")
        blocks.append(
            "Which candidate is best? Reply strictly in this format:\n"
            "WINNER: <number>\n"
            "REASON: <one sentence justification>"
        )
        return "\n\n".join(blocks)

    def _parse_pick(self, text: str, total: int) -> int | None:
        """Extract 0-indexed candidate index from 'WINNER: X' or first valid digit."""
        match = re.search(r"WINNER:\s*(\d+)", text, re.IGNORECASE)
        if match:
            idx = int(match.group(1)) - 1
            if 0 <= idx < total:
                return idx
        # Fallback search for any digit matching candidate numbers
        for digit in re.findall(r"\b(\d+)\b", text):
            idx = int(digit) - 1
            if 0 <= idx < total:
                return idx
        return None
