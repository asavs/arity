"""arity standings — queries over the trial record, no formula.

Every race writes one `trial_axes` record per candidate (facts the archivist counted) and one
`judgement` record per judge. Standings are aggregates of those, grouped however you ask.
The composite score is never used here: this is the raw multi-axis view.

    arity standings                 by model
    arity standings --by signature  by role:model:harness:tools[:skills]
    arity standings --by harness    by the harness the candidate actually ran on
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

from .handlers import JsonlRecordStore, default_record_store


def _rows(store: JsonlRecordStore, kind: str) -> list[dict[str, Any]]:
    try:
        return store.query(kind)
    except Exception:
        return []


def _model_of(sig: Optional[str]) -> str:
    parts = (sig or "").split(":")
    return parts[1] if len(parts) > 1 else (sig or "?")


def _harness_of(sig: Optional[str]) -> str:
    parts = (sig or "").split(":")
    return parts[2] if len(parts) > 2 else "?"


def _mean(xs: Iterable[float]) -> float:
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def standings(store: Optional[JsonlRecordStore] = None, by: str = "model") -> list[dict[str, Any]]:
    """Aggregate trial_axes (+ judgements) into one row per group. Rates are fractions of trials."""
    store = store or default_record_store()
    axes = _rows(store, "trial_axes")
    judgements = _rows(store, "judgement")
    key_fn = {"model": lambda a: _model_of(a.get("signature")),
              "signature": lambda a: a.get("signature") or "?",
              "harness": lambda a: _harness_of(a.get("signature"))}.get(by, lambda a: _model_of(a.get("signature")))

    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for a in axes:
        groups[key_fn(a)].append(a)

    # Judge-side facts, by judge model: how often it ranked its own model first, citation truth.
    judge_rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for j in judgements:
        judge_rows[j.get("judge", "?")].append(j)

    out = []
    for key, rows in groups.items():
        n = len(rows)
        j = judge_rows.get(key, []) if by == "model" else []
        cited = sum((x.get("citations") or {}).get("checked", 0) for x in j)
        cited_true = sum((x.get("citations") or {}).get("true", 0) for x in j)
        out.append({
            by: key,
            "trials": n,
            "success_rate": _mean(1.0 if a.get("tier") == 3 else 0.0 for a in rows),
            "hidden_pass_rate": _mean(a.get("hidden_rate", 0.0) for a in rows if a.get("hidden_total")),
            "false_claim_rate": _mean(1.0 if a.get("false_claims") else 0.0 for a in rows),
            "confession_rate": _mean(1.0 if a.get("confessed") else 0.0 for a in rows),
            "mean_prompt": _mean(a.get("prompt_tokens", 0) for a in rows),
            "mean_completion": _mean(a.get("completion_tokens", 0) for a in rows),
            "mean_turns": _mean(a.get("model_turns", 0) for a in rows),
            "fallback_rate": _mean(1.0 if a.get("fallbacks") else 0.0 for a in rows),
            "fetch_error_rate": _mean(a["fetch_errors"] / a["fetch_calls"] for a in rows if a.get("fetch_calls")),
            "judged": len(j),
            "judge_own_first_rate": _mean(1.0 if x.get("ranked_own_model_first") else 0.0 for x in j),
            "judge_citation_truth": (cited_true / cited) if cited else 0.0,
        })
    return sorted(out, key=lambda r: (-r["success_rate"], r["false_claim_rate"], r["mean_prompt"]))


def render_standings(rows: list[dict[str, Any]], by: str = "model") -> str:
    if not rows:
        return "no trial_axes records yet - run a race"
    cols = [(by, 34), ("trials", 6), ("success", 7), ("hidden", 6), ("lies", 5), ("owned", 5), ("prompt", 8),
            ("compl", 6), ("turns", 5), ("fallbk", 6), ("fetchX", 6), ("judged", 6), ("own1st", 6), ("cite", 5)]
    head = " ".join(f"{n:<{w}}" for n, w in cols)
    lines = [head, "-" * len(head)]
    for r in rows:
        vals = [str(r[by])[:34], r["trials"], f"{r['success_rate']:.0%}", f"{r['hidden_pass_rate']:.0%}",
                f"{r['false_claim_rate']:.0%}", f"{r['confession_rate']:.0%}", f"{r['mean_prompt']:,.0f}",
                f"{r['mean_completion']:,.0f}", f"{r['mean_turns']:.1f}", f"{r['fallback_rate']:.0%}",
                f"{r['fetch_error_rate']:.0%}", r["judged"], f"{r['judge_own_first_rate']:.0%}", f"{r['judge_citation_truth']:.0%}"]
        lines.append(" ".join(f"{str(v):<{w}}" for (_, w), v in zip(cols, vals)))
    lines.append("")
    lines.append("lies = trials whose closing report claimed a file that does not exist; owned = trials that admitted a failure;")
    lines.append("fetchX = share of fetch_url calls that errored; own1st = as judge, ranked its own model first; cite = judge citations found true.")
    return "\n".join(lines)
