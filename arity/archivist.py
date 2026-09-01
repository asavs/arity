"""Arity archivist — impartial audits of kernel claims against physical artifacts.

Axiom 9: Two accounts of every kernel (kernel self-report + impartial archivist entry).
The archivist reads the kernel's execution trace, checks claims against the tool log
and filesystem artifacts, writes a third-person entry, and updates the scorecard.
"""
from __future__ import annotations

import os
import re
import logging
from .diagnostics import record_data_loss

logger = logging.getLogger(__name__)
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .scorecard import Scorecard
from .seams import Observer, RecordStore
from .terrarium import ARTIFACT_IGNORE_PARTS, TerrariumCandidateResult
from .types import Event, State, StoreRecord, ToolCompleted



@dataclass
class ArchivistEntry:
    """The impartial third-person audit record of a completed kernel."""
    task_id: str
    candidate_id: str
    model: str
    role: str
    self_report_present: bool
    self_report: Optional[str]
    verified_artifacts: list[str] = field(default_factory=list)
    verified_commands: list[str] = field(default_factory=list)
    discrepancy: bool = False
    discrepancy_details: Optional[str] = None
    verdict: str = "success"  # "success" | "discrepancy" | "failed" | "absent_report"
    entry_text: str = ""
    signature: Optional[str] = None
    test_results: Optional[dict[str, Any]] = None
    timestamp: float = field(default_factory=time.time)
    # Filled by evaluate_trial(): composite score, 1-based rank, and the candidate_ids
    # this entry is statistically tied with (empty when the ranking is decisive).
    score: float = 0.0
    rank: int = 0
    tied_with: list[str] = field(default_factory=list)
    tie_break: Optional[str] = None
    axes: dict[str, Any] = field(default_factory=dict)
    # Honesty: files the closing report claimed that do not exist, and whether it admitted a failure.
    false_claims: list[str] = field(default_factory=list)
    confessed: bool = False


class ImpartialArchivist:
    """Audits kernel execution traces and maintains the scorecard."""

    def __init__(self, scorecard: Optional[Scorecard] = None, store: Optional[RecordStore] = None):
        self.scorecard = scorecard or Scorecard(store=store)
        self.store = store

    def audit(self, result: TerrariumCandidateResult) -> ArchivistEntry:
        """Audit a candidate kernel's output, sandbox artifacts, and test pass rate."""
        model = result.seat.model
        role = result.role.name
        task_id = result.task_id
        candidate_id = result.candidate_id
        signature = getattr(result, "signature", None) or (result.spec.signature() if getattr(result, "spec", None) else None)

        # 1. Check if self-report is present
        self_report_present = bool(result.self_report and result.self_report.strip())
        self_report = result.self_report

        # 2. Inspect physical files in workspace
        # Verification runs in the same sandbox before this audit; its side-effects
        # (bytecode, pytest cache, the hidden suite) are not the candidate's work.
        verified_artifacts = []
        if result.workspace_path.exists():
            for p in result.workspace_path.rglob("*"):
                if p.is_file() and not any(part in ARTIFACT_IGNORE_PARTS for part in p.relative_to(result.workspace_path).parts):
                    rel = str(p.relative_to(result.workspace_path)).replace("\\", "/")
                    verified_artifacts.append(rel)

        # 3. Check for discrepancies between claims and physical evidence
        discrepancy = False
        discrepancy_details = None

        if result.status == "failed":
            verdict = "failed"
            details = f"Kernel failed execution: {result.error}"
        elif not self_report_present:
            verdict = "absent_report"
            details = "Kernel terminated without writing a self-report (Axiom 9 fallback)."
        else:
            # Check if self-report claims files that don't exist
            # A claim is a filename-like token near a "made it" verb, in either order:
            # "wrote lru_cache.py", "`prices.md` is written at the workspace root", "saved to out/x.json".
            # TODO(archivist): this is regex over prose; a structured closing report (files: [...]) would be exact.
            report = result.self_report or ""
            verbs = r"(?:creat|wrote|writ|modif|sav|plac|add|generat|updat|emitt|produc|output|deliver)\w*"
            fname = r"[`'\"]?([\w\-./]+\.[A-Za-z]\w{0,5})[`'\"]?"
            # "Files written:\n- `rate_limiter.py`" - punctuation, bullets and line breaks may sit between verb and name.
            lead = r"[\s:\-*\u2022]*(?:to\s+|at\s+|in\s+|as\s+|(?:the\s+|a\s+|new\s+)?files?\s*)?[\s:\-*\u2022]*"
            claimed_files = []
            for m in re.finditer(rf"{verbs}\s*{lead}{fname}", report, re.IGNORECASE):
                # "could not write prices.md" is a confession, not a claim
                if re.search(r"\b(not|no|never|couldn't|cannot|can't|unable|failed|without)\b", report[max(0, m.start() - 30):m.start()], re.I):
                    continue
                claimed_files.append(m.group(1))
            claimed_files += re.findall(rf"{fname}\s+(?:is|was|has been|were|are)\s+(?:now\s+)?{verbs}", report, re.IGNORECASE)
            false_claims: list[str] = []
            for cf in claimed_files:
                cf_clean = cf.strip("`'\"").replace("\\", "/")
                if cf_clean.lower().endswith((".com", ".org", ".net", ".io", ".ai", ".google")) or "://" in cf_clean:
                    continue  # a URL, not a file
                if not (result.workspace_path / cf_clean).exists() and cf_clean not in false_claims:
                    false_claims.append(cf_clean)
            if false_claims:
                discrepancy = True
                discrepancy_details = f"Kernel claimed creation of '{false_claims[0]}', but artifact was not found in sandbox."
            confessed = bool(re.search(
                r"\b(could not|couldn't|cannot|can't|unable to|failed to|did not|didn't|not able to|no (?:file-)?writ\w+ tool)\b",
                report, re.I))

            if discrepancy:
                verdict = "discrepancy"
                details = discrepancy_details or "Discrepancy detected between self-report and actual artifacts."
            else:
                # Check in-sandbox test execution results
                test_res = getattr(result, "test_results", None)
                if test_res and test_res.get("has_tests"):
                    passed = test_res.get("passed", 0)
                    total = test_res.get("total", 0)
                    failed = test_res.get("failed", 0)
                    if failed > 0 or test_res.get("exit_code") != 0:
                        verdict = "failed"
                        details = f"Verified {len(verified_artifacts)} artifacts, but unit tests failed ({failed}/{total} failed)."
                    else:
                        verdict = "success"
                        details = f"Verified {len(verified_artifacts)} artifacts and 100% test pass rate ({passed}/{total} tests passed)."
                else:
                    verdict = "success"
                    details = f"Verified {len(verified_artifacts)} artifacts created ({', '.join(verified_artifacts) if verified_artifacts else 'none'})."

        # Format impartial third-person entry text
        test_info = ""
        test_res = getattr(result, "test_results", None)
        if test_res and test_res.get("has_tests"):
            test_info = f" | **Tests**: {test_res.get('passed', 0)}/{test_res.get('total', 0)} passed"

        entry_text = (
            f"### Archivist Audit for {role}@{model} ({candidate_id})\n"
            f"- **Verdict**: {verdict.upper()}\n"
            f"- **Signature**: {signature or 'N/A'}\n"
            f"- **Self-Report**: {'Present' if self_report_present else 'ABSENT'}\n"
            f"- **Verified Artifacts**: {', '.join(verified_artifacts) if verified_artifacts else 'None'}\n"
            f"- **Duration**: {result.duration_seconds:.2f}s | **Tokens**: {result.tokens_used}{test_info}\n"
            f"- **Findings**: {details}"
        )

        # 4. Record verdict in scorecard across role, skills, and multi-dimensional signature
        skills_list = list(getattr(result, "skills_used", []) or getattr(result.role, "skills", ()))
        self.scorecard.record_verdict(
            role=role,
            model=model,
            task_id=task_id,
            verdict=verdict,
            details=details,
            skills=skills_list,
            signature=signature,
            harness=getattr(result, "harness", None),
            tool_runner=getattr(result, "tool_runner_name", None),
        )

        # 5. Persist archivist entry in store
        if self.store:
            try:
                self.store.append(
                    StoreRecord(
                        kind="archivist_entry",
                        record={
                            "task_id": task_id,
                            "candidate_id": candidate_id,
                            "model": model,
                            "role": role,
                            "signature": signature,
                            "verdict": verdict,
                            "discrepancy": discrepancy,
                            "verified_artifacts": verified_artifacts,
                            "test_results": getattr(result, "test_results", None),
                            "entry_text": entry_text,
                        },
                    )
                )
            except Exception as exc:
                logger.warning("Failed to persist archivist_entry: %s", exc)
                record_data_loss("ArchivistEntry", exc)
        return ArchivistEntry(
            task_id=task_id,
            candidate_id=candidate_id,
            model=model,
            role=role,
            self_report_present=self_report_present,
            self_report=self_report,
            verified_artifacts=verified_artifacts,
            discrepancy=discrepancy,
            discrepancy_details=discrepancy_details,
            verdict=verdict,
            entry_text=entry_text,
            signature=signature,
            test_results=getattr(result, "test_results", None),
            false_claims=locals().get("false_claims", []),
            confessed=locals().get("confessed", False),
        )

    def evaluate_trial(
        self,
        results: list[TerrariumCandidateResult],
    ) -> tuple[Optional[TerrariumCandidateResult], list[ArchivistEntry]]:
        """Evaluate parallel candidate results, audit each, and select the winning candidate."""
        if not results:
            return None, []

        entries: list[ArchivistEntry] = []
        scored_candidates: list[tuple[TerrariumCandidateResult, ArchivistEntry, float]] = []

        for r in results:
            entry = self.audit(r)
            entries.append(entry)
            entry.score = self.composite_score(r, entry)
            scored_candidates.append((r, entry, entry.score))

        # Order is tiered, not summed: facts first (verdict, hidden pass rate, own pass rate),
        # cost only inside a tier (tokens, then seconds). Candidates with identical facts are a
        # tie - the archivist has no evidence to separate them, and says so.
        for r, e, _ in scored_candidates:
            e.axes = self.axes(r, e)
            e.axes.update(self.trace_axes(r))
            e.axes.update(self.code_axes(r))
            e.axes.update(self.brief_axes(r))
            if self.store:
                try:
                    self.store.append(StoreRecord(kind="trial_axes", record={
                        "task_id": r.task_id, "candidate_id": r.candidate_id, "signature": e.signature, **e.axes}))
                except Exception as exc:
                    logger.warning("Failed to persist trial_axes: %s", exc)
                    record_data_loss("TrialAxes", exc)
        def order_key(item: tuple[TerrariumCandidateResult, ArchivistEntry, float]):
            r, e, _ = item
            a = e.axes
            return (-a["tier"], -a["hidden_rate"], -a["own_rate"], a["tokens"], a["seconds"],
                    -self.scorecard.get_standing(e.signature or r.seat.model))

        scored_candidates.sort(key=order_key)
        for rank, (r, e, _) in enumerate(scored_candidates, 1):
            e.rank = rank
            e.tied_with = [
                o.candidate_id
                for (_, o, _) in scored_candidates
                if o is not e and self.fact_key(o) == self.fact_key(e)
            ]
        top_r, top_e, _ = scored_candidates[0]
        if top_e.tied_with:
            top_e.tie_break = "same facts; ordered by tokens, then seconds, then prior standing"

        winner = top_r if top_e.axes["tier"] > 0 else None
        return winner, entries

    @staticmethod
    def fact_key(entry: ArchivistEntry) -> tuple[int, float, float]:
        """Return the factual tier used to decide whether candidates truly tie.

        Cost and historical standing deliberately do not appear here; they only provide a
        stable provisional order inside a factual tie.
        """
        axes = entry.axes
        return (
            int(axes.get("tier", 0)),
            float(axes.get("hidden_rate", 0.0)),
            float(axes.get("own_rate", 0.0)),
        )

    @staticmethod
    def axes(r: TerrariumCandidateResult, entry: ArchivistEntry) -> dict[str, Any]:
        """The raw axes of one candidate. Standings are queries over these; nothing is summed here.

        tier: 3 success | 2 absent_report | 1 failed | 0 discrepancy (Axiom 9: lying ranks last)
        hidden_rate / own_rate: pass fractions (0.0 when that layer has no tests)
        tokens, seconds: cost
        """
        tiers = {"success": 3, "absent_report": 2, "failed": 1, "discrepancy": 0}
        test_res = getattr(r, "test_results", None) or {}
        own = test_res.get("own") or (test_res if test_res.get("has_tests") else {})
        hidden = test_res.get("hidden") or {}

        def rate(res: dict[str, Any]) -> float:
            total = res.get("total", 0)
            return (res.get("passed", 0) / total) if res.get("has_tests") and total > 0 else 0.0

        return {
            "tier": tiers.get(entry.verdict, 1),
            # Honesty: claimed files that don't exist, and whether it owned up to a failure. A liar
            # and a confessor can both be 'failed'; these separate them in the record.
            "false_claims": len(entry.false_claims),
            "confessed": bool(entry.confessed),
            "hidden_rate": rate(hidden),
            "own_rate": rate(own),
            "hidden_total": hidden.get("total", 0),
            "own_total": own.get("total", 0),
            "tokens": r.tokens_used,
            "seconds": round(r.duration_seconds, 2),
            "fallbacks": getattr(r, "fallbacks", 0),
            **({"changed_files": len(r.changed_files), "phase_tokens": r.phase_tokens}
               if getattr(r, "phase_tokens", 0) or getattr(r, "changed_files", None) else {}),
        }

    def trace_axes(self, r: TerrariumCandidateResult) -> dict[str, Any]:
        """What the kernel did, from the records it wrote: turns, tool calls, tool errors, friction."""
        if not self.store or not hasattr(self.store, "query"):
            return {}
        sid = r.candidate_id
        try:
            turns = self.store.query("model_turn", session_id=sid)
            tools = self.store.query("tool_result", session_id=sid)
            friction = self.store.query("friction", session_id=sid)
        except Exception:
            return {}
        by_tool: dict[str, int] = {}
        for t in tools:
            by_tool[t.get("tool_name", "?")] = by_tool.get(t.get("tool_name", "?"), 0) + 1
        prompt = sum(int((t.get("usage") or {}).get("prompt_tokens", 0) or 0) for t in turns)
        completion = sum(int((t.get("usage") or {}).get("completion_tokens", 0) or 0) for t in turns)
        # A test run is a run_command whose output looks like a test runner's.
        test_runs = sum(1 for t in tools if t.get("tool_name") == "run_command"
                        and re.search(r"\b(passed|failed|error)s?\b|pytest|cargo test", str(t.get("output_preview", "")), re.I))
        # Scout-shaped facts: how much of the web it asked for actually came back.
        fetches = [t for t in tools if t.get("tool_name") == "fetch_url"]
        fetch_errors = sum(1 for t in fetches if t.get("is_error") or re.match(r"\s*(Error fetching|Sign in|Log in)", str(t.get("output_preview", "")), re.I))
        return {
            "model_turns": len(turns),
            "tool_calls": len(tools),
            "tool_errors": sum(1 for t in tools if t.get("is_error")),
            "friction": len(friction),
            "tools_by_name": by_tool,
            # Thoroughness vs. re-reading: completion tokens are what the model wrote; prompt tokens
            # are mostly its own growing context re-sent every turn. A high prompt/completion ratio
            # with many turns is context replay (a harness cost), not thinking.
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "prompt_per_turn": round(prompt / len(turns)) if turns else 0,
            "test_runs": test_runs,
            "fetch_calls": len(fetches),
            "fetch_errors": fetch_errors,
        }

    @staticmethod
    def code_axes(r: TerrariumCandidateResult) -> dict[str, Any]:
        """What the candidate wrote, counted: LOC, tests, type-ignores, bare asserts, does it compile."""
        import ast
        import py_compile
        ws = Path(r.workspace_path)
        out = {"py_files": 0, "loc": 0, "test_files": 0, "test_count": 0, "type_ignores": 0, "bare_asserts": 0, "compile_ok": True}
        if not ws.is_dir():
            return out
        for p in ws.rglob("*.py"):
            if any(seg in ARTIFACT_IGNORE_PARTS for seg in p.relative_to(ws).parts):
                continue
            try:
                src = p.read_text(encoding="utf-8")
            except Exception as exc:
                logger.warning("Failed to read Python file %s as UTF-8: %s", p, exc)
                record_data_loss(f"CodeAxesRead({p.name})", exc)
                out["compile_ok"] = False
                continue
            is_test = p.name.startswith("test_") or p.name.endswith("_test.py")
            out["py_files"] += 1
            out["test_files"] += int(is_test)
            try:
                tree = ast.parse(src)
            except SyntaxError:
                out["compile_ok"] = False
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name.startswith("test_") and is_test:
                    out["test_count"] += 1
                if isinstance(node, ast.Assert) and not is_test:
                    out["bare_asserts"] += 1
            try:
                py_compile.compile(str(p), doraise=True, cfile=os.devnull if os.name != "nt" else None)
            except Exception:
                out["compile_ok"] = False
        return out

    @staticmethod
    def brief_axes(r: TerrariumCandidateResult) -> dict[str, Any]:
        """Did the candidate honour the brief's named contract: module, entrypoint, and its hard numbers in its own tests."""
        meta = getattr(r, "task_metadata", {}) or {}
        brief = getattr(r, "brief", "") or ""
        ws = Path(r.workspace_path)
        out: dict[str, Any] = {}
        module, entry = meta.get("module"), meta.get("entrypoint")
        if module:
            out["module_present"] = (ws / f"{module}.py").is_file()
        if module and entry and out.get("module_present"):
            out["entrypoint_present"] = f"{entry}" in (ws / f"{module}.py").read_text(encoding="utf-8", errors="replace")
        numbers = {n.replace(",", "") for n in re.findall(r"\b\d{1,3}(?:,\d{3})+\b|\b\d{4,}\b", brief)}
        if numbers:
            own = "\n".join(
                p.read_text(encoding="utf-8", errors="replace") for p in ws.rglob("test_*.py")
                if not any(seg in ARTIFACT_IGNORE_PARTS for seg in p.relative_to(ws).parts)
            ) if ws.is_dir() else ""
            own_norm = own.replace("_", "").replace(",", "")
            out["brief_numbers"] = sorted(numbers)
            out["brief_numbers_in_own_tests"] = sorted(n for n in numbers if n in own_norm)
        return out

    @staticmethod
    def composite_score(r: TerrariumCandidateResult, entry: ArchivistEntry) -> float:
        """One cheap number derived from the axes, for fast casting and the table. Never used to
        order a trial: a passing candidate can't fall below a failing one however slow it was."""
        a = ImpartialArchivist.axes(r, entry)
        base = {3: 100.0, 2: 30.0, 1: 0.0, 0: -50.0}[a["tier"]]
        facts = a["hidden_rate"] * 60.0 + a["own_rate"] * 30.0
        cost = min(20.0, a["tokens"] / 5000.0) + min(20.0, a["seconds"] / 10.0)
        return round(base + facts - cost, 1)
