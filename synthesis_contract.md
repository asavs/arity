# Synthesis Authority Contract (fix for ambiguous output contract)

## Decision Maker
Nested Runtime with persona="judge_classifier" evaluates grades from B1, B2, B3.
User A retains veto authority over final synthesis.

## Conflict Resolution
1. Cherry-pick parts scoring >0.85 (high-grade threshold from grades.md).
2. If score divergence >0.15 (e.g., B1=0.92 vs B3=0.60), apply median filter; discard outliers.
3. If still ambiguous: user A selects from presented alternatives.

## Presentation
Synthesis agent writes harness/synth_commit.md with:
- Explicit author attribution: "B1-inkling (thesis) + B2-chatgpt (metric) + B3-nemotron (boundary)"
- Judge verdict line: "Settled by classifier; no anxiety; concrete gates met"
- No meta headers; no defensive language; empirical verification included.

## Output Format
Single coherent imperative commit message (<60 chars subject), followed by Problem, Solution, Non-goals, Empirical, Zero-case, Gates (PASS/FAIL/UNPROVEN per gate), Author attribution.
