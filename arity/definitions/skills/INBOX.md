# Skill inbox

Candidates. Not live. Not attached to a role.

Dump sources here as they arrive. Assignment happens later, once the pile
shows which existing roles they belong to and whether a new role is needed.

Live skills today are the five built-ins in `arity/skills.py` plus anything
on disk at `.arity/skills/` or `~/.arity/skills/`. This folder is the
packaged library that roles and types already have; it is not yet on the
discovery path, so an entry here cannot leak into a brief.

## How skills actually attach

A skill is a named prompt (+ optional tool grants). A role opts in by listing
the name under `skills:` in its markdown frontmatter
(`arity/definitions/roles/*.md`). A type pack can add more
(`definitions/types/python.md` adds `python-development` and `pytest-tdd`).
`BriefCompiler` injects the compiled block. `arity skills` lists the registry;
`arity roles` shows which names each role currently claims.

`SkillRegistry.install(name, description, instructions)` writes
`.arity/skills/<name>/SKILL.md` and registers it. `.remove(name)` deletes
that directory. There is no CLI for install/remove yet — only `arity skills`
(list). Built-ins cannot be removed from disk because they are Python
constants; `remove` only drops the in-memory copy for that process.

## How tools actually attach (different)

Tools are not a library you add and remove. The sandbox publishes a fixed
set. A role may *deny* some (`denied_tools:`) or, rarely, *allow only* some
(`allowed_tools:` empty means “all except denials”). That is axiom 2:
capability is a denial set, not a grant list. Skills can *name* extra tools
on the `Skill.tools` field; nothing currently grants them. Do not model
skills as tools.

## Entry template

One heading per candidate. Fill what you have; leave the rest blank.

```
### <short-name>
- source: url or path
- kind: skill | repo | prompt | article | war-story
- does:
- pin-then-extract: yes / no / unknown
- suggested role: (blank until the pile is in)
- keep out of 1.0: yes / no
- notes:
```

## Entries

<!-- drop below this line -->

### mattpocock/skills
- source: https://github.com/mattpocock/skills
- kind: repo
- does: Composable engineering skills against four failure modes (misalignment, jargon, no feedback, ball of mud). Explicitly anti-vibe-coding; small skills you own rather than a process that owns you (GSD/BMAD/Spec-Kit). User-invoked vs model-invoked split.
- pin-then-extract: no (survey + grill, not characterize-then-split)
- suggested role:
- keep out of 1.0: no for the vocabulary; yes for the HTML-report / issue-tracker / CONTEXT.md machinery
- notes: 244k stars, MIT. Install via `npx skills add mattpocock/skills` or Claude plugin `mattpocock-skills`. Setup skill asks for issue tracker — skip that for arity 1.0.

  Deslop-shaped internals:
  - `improve-codebase-architecture` — scan for *deepening* (shallow → deep modules), HTML report, then grill. Self-describes as "a survey, not a rescue." Matches fleet-dev: don't let an agent untangle the mud autonomously.
  - `codebase-design` — Ousterhout vocabulary: module, interface, depth, seam (Feathers), adapter, leverage, locality. Deletion test. "One adapter = hypothetical seam, two = real." Maps onto axioms 12/13 almost one-to-one; their "seam" is our seam.
  - `tdd` — red-green-refactor. We already have `pytest-tdd`.
  - `code-review` — two axes in parallel: Standards vs Spec.
  - `implement` — drive tdd at pre-agreed seams, close with code-review.
  - `grill-with-docs` / `grilling` — interview until the design tree is resolved; writes CONTEXT.md + ADRs. Tension with our finding #3 (AskUserQuestion as hedge). Keep as a *user-invoked* skill, never model-invoked on arity [DECIDE] items.

  Also in the bag, not deslop: diagnosing-bugs, research, domain-modeling, to-spec, to-tickets, wayfinder, triage, prototype, wizard, handoff, wait-what, writing-for-agents.


### dmmulroy/anti-slop
- source: https://github.com/dmmulroy/anti-slop
- kind: repo
- does: Opinionated Oxlint rules that reject low-evidence / low-signal TS/JS. Vendored, not an npm package. One install skill copies the plugin in.
- pin-then-extract: no (lint, not refactor)
- suggested role:
- keep out of 1.0: yes as a plugin (arity is Python, stdlib-first). Keep the *stance*.
- notes: 4k stars, MIT. Author: Dillon Mulroy. Taste, not a universal standard.

  Stance that transfers:
  - Reject patterns that *fabricate evidence* (`as object as User`, widen-then-assert).
  - Prefer real dependency seams over module mocks (`no-module-mocking`).
  - Type assertion needs a nearby invariant comment (`SAFETY:`).
  - `unknown` is a boundary type, not a pass-through.

  Not a god-file splitter. Complementary: mechanical gate on the *kind* of slop agents emit while coding, not on file shape. A Python analogue would be a ruff/ast rule set — post-1.0.1 [SEAM], not a 1.0 skill.


### DietrichGebert/ponytail
- source: https://github.com/DietrichGebert/ponytail
- kind: repo
- does: Always-on "lazy senior" ladder that stops at the first rung that holds. Measured ~54% less LOC vs bare agent on real FastAPI+React tickets, without dropping safety. Companion skills review/audit/debt for *deletion*, not addition.
- pin-then-extract: no (prevents new slop; does not split an existing god file)
- suggested role:
- keep out of 1.0: no for the ladder and review tags; yes for the 20-agent plugin/hooks machinery
- notes: 121k stars, MIT. Author: Dietrich Gebert. Closest thing in the pile to arity's naive-1.0 taste. The date-picker example (`<input type="date">` vs flatpickr+wrapper) is the vibe-coding failure mode in one picture.

  Ladder (runs *after* reading the code, not instead of it):
  1. Need to exist? skip (YAGNI)
  2. Already in this codebase? reuse
  3. Stdlib? use it
  4. Native platform? use it
  5. Installed dep? use it
  6. One line? one line
  7. Only then: the minimum that works
  Never cut: trust-boundary validation, data-loss handling, security, a11y.

  Companion skills:
  - `ponytail-review` — diff for over-engineering. Tags: `delete:` `stdlib:` `native:` `yagni:` `shrink:`. One line per finding. Ends `net: -N lines possible.` Does not apply fixes. Explicitly out of scope: bugs, security, perf.
  - `ponytail-audit` — same tags, repo-wide, ranked biggest cut first.
  - `ponytail-debt` — harvest `ponytail:` comments (ceiling + upgrade trigger) into a ledger. A `[SEAM]` marker with a forcing function. `no-trigger` = the ones that rot.

  Maps onto: axiom 5 (mine/stdlib over rented), A0 (fewer things in Asa's head), fleet-dev "diffs boring." `yagni: AbstractRepository with one implementation. Inline it until a second one exists` is Pocock's "one adapter = hypothetical seam" in review-tag form.


### j3ssie/metabigor (osp.fyi/metabigor)
- source: https://osp.fyi/metabigor → https://github.com/j3ssie/metabigor
- kind: repo
- does: OSINT recon CLI. Maps ASN/domain/IP footprint from keyless public sources (crt.sh, Shodan InternetDB, grep.app). Pipe-friendly, one UX across commands.
- pin-then-extract: no
- suggested role:
- keep out of 1.0: yes for the engineering team. Maybe a scout *tool* later, not a skill.
- notes: Off-axis for deslop. Logged anyway. If this was a miss (wrong URL / different metabigor), say so and I'll retarget.


### emilkowalski/skills
- source: https://github.com/emilkowalski/skills
- kind: repo
- does: Transfer UI *taste* to an agent so it doesn't emit generic slop. Duration tables, easing (ease-out on enter, not ease-in), shadows not solid borders, named motion primitives. "A shortcut to stand out in a sea of slop."
- pin-then-extract: no (prevents visual slop on new UI; not a god-file split)
- suggested role: (first pile item that implies a role we don't have — designer / design-eng. Do not hang these on engineer or developer.)
- keep out of 1.0 kernel: yes. Keep for any future UI surface (inspection TUI, redphone, phone door).
- notes: 35k stars, MIT. `npx skills add emilkowalski/skills`. Asa's usage: pull *every* skill in this set *before* building a website or mobile app. That's a role-gating pattern, not always-on.

  Skills:
  - `emil-design-eng` — flagship doctrine (animation + some design)
  - `animate` / `animate-expo` — build motion from scratch with the right curve/duration/thread
  - `review-animations` — adversarial craft review (ponytail-review, for motion)
  - `improve-animations` / `find-animation-opportunities` — audit + "what not to animate"
  - `animation-vocabulary` — named primitives instead of "swooshy"
  - `apple-design` — HIG distilled for web
  - `pick-ui-library` — don't hand-roll a toast or install an abandoned package. Ponytail rungs 4–5, for UI.
  - `prototype` — multiple versions behind a switcher
  - `ask-sonner` / `write-swift` — library- and language-specific

  Essay behind it: https://emilkowal.ski/ui/agents-with-taste


### nathanmarz 2094459117922578655
- source: https://x.com/nathanmarz/status/2094459117922578655
- kind: war-story
- does: Catalog of LLM mistakes (mostly Fable, one week) that compound if uncorrected. Not a skill — the *do-not* list a review skill has to catch.
- pin-then-extract: no (argues for correcting *as you go*, which is why god files exist if you don't)
- suggested role:
- keep out of 1.0: no — this is the failure mode 1.0 is trying not to ship
- notes: Nathan Marz, 2026-08-31. Quote:

  > Had I not corrected the mistakes, every future iteration would keep compounding the structural and semantic problems. If you don't do this, it "works" until you realize the code is full of bugs, performs terribly, and can only be fixed with a rewrite.

  The list splits across two review axes (Pocock's Standards vs Spec; ponytail-review vs a correctness pass):

  Over-build (ponytail `yagni:` / `delete:`):
  - Complex unnecessary DI instead of calling functions
  - Unnecessary state
  - Overly complex locking, full of bugs
  - Unnecessary configs introduced prematurely

  Under-build / skip the ladder the wrong way (`native:` / library):
  - Hand-rolling encryption instead of using a library

  Correctness / perf (explicitly *out* of ponytail-review scope — needs a second pass):
  - Memory leaks in tracked state on failure
  - Incomplete tests, especially failure modes
  - Grossly inefficient file handling because "dev scale"
  - Missed race conditions

  Implication for arity: one "deslop" skill cannot cover this list. Complexity review and correctness review pollute each other if combined (Pocock runs them as parallel sub-agents for that reason).


### kunchenguid/no-mistakes
- source: https://github.com/kunchenguid/no-mistakes
- kind: repo
- does: Local git-proxy gate. Push to `no-mistakes` instead of origin; disposable worktree runs review → test → docs → lint → PR. Safe mechanical fixes auto-apply; anything that touches intent parks for a human. Author's 1.60.x change: adversarial review was *creating* over-engineering (scope creep, usually Sol).
- pin-then-extract: no (gate on the way out, not a splitter)
- suggested role:
- keep out of 1.0: yes as a product (git proxy, daemon, TUI). Steal the two review rules.
- notes: 8k stars, MIT. Author: Kun Chen (@kunchenguid). Tweet: adversarial review loops over-engineered, no-mistakes runs got slow and grew scope, "usually caused by cough... sol.. cough.."

  The two rules (now in `.agents/skills/pipeline-review-and-agents`, "Remedy-Scope Discipline"):
  1. Classify findings by *remedy*, not topic. If the smallest honest fix would add durable state, a schema, retry/persistence, a new subsystem, or otherwise EXTEND rather than CORRECT → `ask-user`. Do not auto-fix scope expansion.
  2. Auto-fix reaches depth by *simplifying the architectural reason*, not bolting machinery onto symptoms. A prior fix-round that grew past the original finding gets an exit ramp: `ask-user` recommending revert to the minimal fix, not another layer.

  Meta-rule they refused to add: a growth detector / second scope-reviewer. Quote: "a growth detector or scope verifier is itself the machinery being prevented."

  Maps onto: Marz's compounding (correct as you go, but the *correction* can itself compound — that's the Sol failure). Ponytail on the fixer. Finding #3 (don't hand judgment back unless it's actually a [DECIDE]). Casting note: Sol named as the over-engineer in review loops — aptitude data for the reviewer role, not a ban.


### ryanvogel 2094580233378607233 + opt-in test files
- source: https://x.com/ryanvogel/status/2094580233378607233
- kind: prompt
- does: Default-deny new test files. Run existing tests and live checks instead. When tests *are* in scope, assert observable behavior, not source strings or that tests exist.
- pin-then-extract: no
- suggested role:
- keep out of 1.0: no as a *developer* default; yes as a ban on the *tester* role (tester's job is hidden tests)
- notes: Tweet: "I'm about to start banning agent's from creating tests and see how it goes / Maybe the time saved from writing tests can be used to actually develop the product." Policy text pasted with it (opt-in, assume no, ask only when creating them has a concrete benefit).

  Tension in the pile:
  - Marz: incomplete tests, especially failure modes, was a compounding LLM mistake.
  - Pocock `/tdd` and our `pytest-tdd` / tester role exist to write tests.
  - Ponytail: one smoke/`assert` is the minimum, not bloat; don't flag it for deletion.
  - This policy: *new files* are the slop, not testing. "implement / fix / test / verify" does not authorize a new file. Prefer run-what-exists + browser/runtime.

  Resolution that doesn't collapse the tension: developer defaults to no new test files; tester (and an explicit "write tests" brief) is the opt-in. Characterization tests for a god-file split are a third case — throwaway, asked for, then deleted (fleet-dev §8).


### Vuk97/forward-implementation-first
- source: https://github.com/Vuk97/forward-implementation-first
- kind: skill
- does: Before every action, classify as implementation / focused validation / administrative bookkeeping. Do 1 and 2. Skip 3 unless asked, or the artifact *is* the product. Stale metadata is not a reason to redo work. ~150 lines. Named Sol as the paperwork over-engineer.
- pin-then-extract: no (keeps a long run from stalling; does not split a file)
- suggested role:
- keep out of 1.0: no — hang on developer/engineer for long runs. Do not hang on archivist (the journal *is* the product).
- notes: 161 stars, MIT. Author: Vuk97. Author's claim: GPT/Sol experience 3→8. "Redoing work needs a real reason, and stale metadata is not one. Tests and benchmarks stay mandatory."

  Forward cursor — replay only if: input meaning changed, target/revision changed, output is actually wrong, an observed run disproves the static result, or the producer's cone requires it. Missing receipt/hash/lock is not on that list. If the orchestrator refuses a stage for a marker: run it by hand, validate, publish, *delete the gate*.

  Preserved (not paperwork): product integrity hashes, input/revision identity, tests that assert results, coverage≠consequence. "Rigor vs superstition."

  Parallelism: skill must not be a scheduler. Invariants only — one writer owns publication/cursor/conclusions; verify a worker before consuming; don't invent busywork for idle slots. Maps onto fleet-dev territory + the VC governor.

  Arity-specific trap: EvidenceBundle hashes, trial receipts, and the journal look exactly like the paperwork this skill deletes. They are the output contract. The skill's own exception covers them. An agent implementing *arity* without that carve-out would "deslop" the product.

  Third Sol datapoint in the pile (with no-mistakes review loops). Aptitude: Sol over-builds process.


### JinjingLiang 2095539062484398520 elegance-review
- source: https://x.com/JinjingLiang/status/2095539062484398520
- kind: prompt
- does: One-click post-branch pass: "Review this branch like your life depends on it. Make it as elegant, simple, and correct as possible. No weird wiring. No needless abstractions. Pure elegance." Claimed 10/10: less code, better architecture, more correct behavior. Ships in @orca_build.
- pin-then-extract: no (rewrites in place; the opposite of characterize-then-split)
- suggested role:
- keep out of 1.0: yes as an auto-apply. The *intent* overlaps ponytail-review; the *mechanism* (apply elegance) is the Sol/no-mistakes failure mode.
- notes: 2026-09-03. Unmeasured vs ponytail's agentic LOC/safety table. Ponytail-review lists cuts and does not apply them. This applies. Fleet-dev: diffs boring, no public rename, no behavior smuggled in. An "elegance" pass on `race.py` is how you silently change `run_race`.

  Keep the prompt as a *user-invoked* reviewer stance ("no weird wiring") after a pin exists. Never model-invoked, never on a god file without characterization.


### jakubkrehel/skills
- source: https://github.com/jakubkrehel/skills
- kind: repo
- does: Interface craft beyond motion: type, color, a11y, layout, product writing. Completes Kowalski (who is mostly animation). Same "transfer taste so the agent doesn't emit generic UI."
- pin-then-extract: no
- suggested role: designer / design-eng, same gate as Kowalski — pull before a website or app, not on engineer/developer, not always-on.
- keep out of 1.0 kernel: yes. Same future-UI bucket as Kowalski.
- notes: 5k stars, MIT. `npx skills add jakubkrehel/skills` or plugin `interfaces@interfaces`. Author: Jakub Krehel (jakub.kr, interfaces.dev).

  Skills:
  - `better-ui` / `better-typography` / `better-colors` / `better-accessibility` / `better-layout` / `better-writing`
  - `better-interface` — all of the above in one review
  - `interface-review` — user-invoked multi-category analysis
  - `explain-interface` — reverse-engineer how a UI/animation was built
  - `break` — render every state on a throwaway page
  - `variant` — multiple versions (Kowalski `prototype`)

  Split with Kowalski: Krehel owns type/color/a11y/layout/copy; Kowalski owns motion curves and UI-library choice. Don't install both as always-on or they fight.

