"""glean — read a house's run directory and say how it did, deterministically.

A megaminds seed. Given `impl/<house>/` (modules + run.log + demo.log + README), produce:
  - a row of numbers nobody had to think about (size, structure, adherence, outcome, cost)
  - a blind pack: the same logs with house/model names scrubbed, labeled A/B/C, plus a judge
    brief, so a subagent can read them for the qualitative part without knowing who's who.

    python glean.py measure impl/codex impl/agy impl/claude      -> table + impl/glean.json
    python glean.py blind impl/codex impl/agy impl/claude        -> impl/blind/{A,B,C}/ + JUDGE.md

No model calls. Counting only. The judge brief is what a subagent gets; its answer comes back
as qualitative columns on the same row.
"""
from __future__ import annotations

import ast
import json
import re
import shutil
import sys
from pathlib import Path

# things the BRIEF asked for; presence in the code is a cheap adherence signal
BRIEF_TERMS = {
    "keepalive text":     r"hi luv u",
    "identity tuple":     r"identity",
    "report absent":      r"ABSENT",
    "presence":           r"presence",
    "cold cost":          r"cold[_ ]?cost|penalty",
    "standing":           r"standing",
    "handoff bounds":     r"depth|budget",
    "leak scan":          r"leak|Refus|refuse",
    "cache table":        r"window|read_x|read_mult|cache",
    "stdlib http":        r"urllib",
    "no pip":             r"^(?!.*\bimport (requests|httpx|openai|anthropic)\b)",
}
HOUSE_WORDS = r"codex|openai|gpt[-\w.]*|sol\b|agy|antigravity|gemini[-\w.]*|google|claude[-\w.]*|opus|sonnet|anthropic|nemotron|nim\b|nvidia"

def py_files(d: Path) -> list[Path]:
    return sorted(p for p in d.glob("*.py") if p.name != "glean.py")

def structure(d: Path) -> dict:
    files, lines, funcs, classes, docd, defs, rel_imports, abs_local = 0, 0, 0, 0, 0, 0, 0, 0
    third_party, syntax_errors, long_lines = set(), 0, 0
    names = {p.stem for p in py_files(d)}
    for p in py_files(d):
        src = p.read_text(encoding="utf-8", errors="replace")
        files += 1; ls = src.splitlines(); lines += len(ls); long_lines += sum(1 for l in ls if len(l) > 120)
        try: tree = ast.parse(src)
        except SyntaxError: syntax_errors += 1; continue
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)): funcs += 1; defs += 1; docd += bool(ast.get_docstring(n))
            elif isinstance(n, ast.ClassDef): classes += 1; defs += 1; docd += bool(ast.get_docstring(n))
            elif isinstance(n, ast.ImportFrom):
                if n.level: rel_imports += 1
                elif n.module and n.module.split(".")[0] in names: abs_local += 1
                elif n.module and n.module.split(".")[0] not in sys.stdlib_module_names: third_party.add(n.module.split(".")[0])
            elif isinstance(n, ast.Import):
                for a in n.names:
                    if a.name.split(".")[0] not in sys.stdlib_module_names and a.name.split(".")[0] not in names: third_party.add(a.name.split(".")[0])
    return dict(files=files, lines=lines, functions=funcs, classes=classes,
                docstring_rate=round(docd / defs, 2) if defs else 0.0, long_lines=long_lines,
                syntax_errors=syntax_errors, relative_imports=rel_imports, flat_local_imports=abs_local,
                import_style_mixed=bool(rel_imports and abs_local), third_party=sorted(third_party))

def adherence(d: Path) -> dict:
    src = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in py_files(d))
    hits = {k: bool(re.search(v, src, re.I | re.M)) for k, v in BRIEF_TERMS.items()}
    return dict(brief_terms_hit=sum(hits.values()), brief_terms_total=len(hits), missing=[k for k, v in hits.items() if not v])

def outcome(d: Path) -> dict:
    o: dict = dict(demo_ran=None, model_calls=None, demo_tokens=None, tracebacks=0, tool_results=0, tool_failures=0,
                   demo_attempts=0, wall_s=None, house_tokens=None, saw_ground_truth=None, empty_runs=0)
    logs = list(d.glob("run*.log")) + sorted(d.glob("demo*.log")) + sorted(d.glob("turn*.json")) + sorted(d.glob("round*.json"))
    text = ""
    for p in logs:
        t = p.read_text(encoding="utf-8", errors="replace"); text += "\n" + t
        if p.name.startswith("run") and len(t.strip()) < 50: o["empty_runs"] += 1
    o["tracebacks"] = len(re.findall(r"^Traceback \(most recent", text, re.M))
    o["tool_results"] = len(re.findall(r"^\s*(succeeded|exited \d+|failed) in \d+ms", text, re.M))
    o["tool_failures"] = len(re.findall(r"^\s*(exited [1-9]\d*|failed) in \d+ms", text, re.M))
    # codex logs `exec` on one line and the command on the next; count only real demo invocations
    o["demo_attempts"] = len(re.findall(r"^exec\n[^\n]*demo\.py", text, re.M)) or len(re.findall(r"^python(?:3)?\s+demo\.py", text, re.M))
    o["tool_calls"] = len(re.findall(r"^exec$", text, re.M))
    m = re.search(r"tokens used\s*\n?\s*([\d,]+)", text)
    if m: o["house_tokens"] = int(m.group(1).replace(",", ""))
    for p in list(d.glob("run*.log")) + list(d.glob("turn*.json")) + list(d.glob("round*.json")):   # antigravity: JSON with usage, maybe a trailer
        try:
            j, _ = json.JSONDecoder().raw_decode(p.read_text(encoding="utf-8", errors="replace").lstrip())
            u = j.get("usage") or {}
            tot = u.get("total_tokens") or u.get("total") or (int(u.get("input_tokens") or u.get("input") or 0) + int(u.get("output_tokens") or u.get("output") or 0))
            if tot: o["house_tokens"] = (o["house_tokens"] or 0) + int(tot)
        except Exception: pass
    # the last demo log that printed a totals line wins; formats vary by house
    finals = re.findall(r"model_calls=(\d+)\s+tokens=(\d+)|(\d+) model calls,\s*(\d+) tokens|Total Model Calls:\s*(\d+)[\s\S]{0,120}?Tokens:\s*(\d+)", text)
    if finals:
        g = finals[-1]; o["demo_ran"] = True
        o["model_calls"] = int(g[0] or g[2] or g[4]); o["demo_tokens"] = int(g[1] or g[3] or g[5])
    elif list(d.glob("demo*.log")) or o["demo_attempts"]:
        o["demo_ran"] = False
    o["demo_attempts"] = o["demo_attempts"] or len(list(d.glob("demo-round*.log")))
    # relay-observed wall clock: start/end .ts files are canonical; a RELAY.md wall column is the fallback
    ts = sorted(d.glob("start*.ts")), sorted(d.glob("end*.ts"))
    try:
        if ts[0] and ts[1]:
            s = float(ts[0][0].read_text().strip()); e = float(ts[1][-1].read_text().strip()); o["wall_s"] = round(e - s)
    except Exception: pass
    if o["wall_s"] is None and (d / "RELAY.md").exists():
        cells = re.findall(r"\|\s*([\d.]+)\s*\|\s*$", (d / "RELAY.md").read_text(encoding="utf-8", errors="replace"), re.M)
        if cells: o["wall_s"] = round(sum(float(c) for c in cells)); o["wall_source"] = "RELAY.md"
    o["saw_ground_truth"] = "Ground truth" in text or "<!-- ===== .wiki" in text
    return o

def measure(dirs: list[Path]) -> list[dict]:
    rows = []
    for d in dirs:
        r = dict(house=d.name); r.update(structure(d)); r.update(adherence(d)); r.update(outcome(d)); rows.append(r)
    return rows

def table(rows: list[dict]) -> str:
    cols = ["house", "files", "lines", "functions", "classes", "docstring_rate", "import_style_mixed", "third_party",
            "brief_terms_hit", "syntax_errors", "demo_ran", "model_calls", "demo_tokens", "tracebacks",
            "tool_calls", "tool_failures", "demo_attempts", "wall_s", "house_tokens", "saw_ground_truth"]
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    for r in rows: out.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + " |")
    return "\n".join(out)

def blind(dirs: list[Path], out: Path) -> None:
    """Copy each house's code + logs into A/B/C with house and model names scrubbed."""
    if out.exists(): shutil.rmtree(out)
    labels = "ABCDEF"
    key = {}
    for i, d in enumerate(dirs):
        lab = labels[i]; key[lab] = d.name; dest = out / lab; dest.mkdir(parents=True)
        for p in list(d.glob("*.py")) + list(d.glob("*.md")) + list(d.glob("run*.log")) + list(d.glob("demo.log")):
            t = p.read_text(encoding="utf-8", errors="replace")
            t = re.sub(HOUSE_WORDS, "HOUSE", t, flags=re.I)
            (dest / p.name).write_text(t, encoding="utf-8")
    (out / "KEY.json").write_text(json.dumps(key, indent=2))      # the judge must not open this
    (out / "JUDGE.md").write_text(JUDGE_BRIEF.format(labels=", ".join(labels[: len(dirs)])), encoding="utf-8")

JUDGE_BRIEF = """# Blind judge brief — v0 implementations

Directories {labels} each hold one house's attempt at the same brief (BRIEF.md in impl/), with
house and model names scrubbed to HOUSE. Do NOT open KEY.json. Read each directory's code,
README, run*.log and demo.log. Score 1-5 with one sentence each:

1. **Spec fidelity** — does the code do what the brief's hard rules say (no fakes, real
   /chat/completions loop, identity tuple, report-then-entry with ABSENT, standing goes down when
   caught, handoff records bounded, keepalive rule, presence)? Missing or faked rules cost points.
2. **Modularity** — could a file be lifted out and used alone? Are seams where axiom 12 puts them?
3. **Process quality (from the logs)** — how did it work: did it read before writing, run the demo,
   react to failures sensibly, iterate or thrash? Count is in the table; this is the *shape*.
4. **Honesty** — README limitations true and specific? Does the code claim more than it does?
5. **Readability** — casual, clear, docstrings in the register of the brief's pages?

Then: **strengths / weaknesses** per house in two lines each, **what to cherry-pick** (name the
file and why), and a JSON block `{{"A": {{...}}, "B": {{...}}}}` of the five scores.
If a house's logs show it never saw the ground-truth section, say so and score fidelity on what
it was given. Return only the markdown."""

if __name__ == "__main__":
    cmd, dirs = (sys.argv[1] if len(sys.argv) > 1 else "measure"), [Path(a) for a in sys.argv[2:]]
    if cmd == "measure":
        rows = measure(dirs); print(table(rows))
        Path("impl/glean.json").write_text(json.dumps(rows, indent=2)); print("\nwrote impl/glean.json")
    elif cmd == "blind":
        blind(dirs, Path("impl/blind")); print("wrote impl/blind/{A,B,..}/ + JUDGE.md (KEY.json is for after)")
    else: print(__doc__)
