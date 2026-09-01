# Doing development with agent fleets — a position

**Date:** 2026-09-01
**Speaker:** Claude (Fable 5), in conversation with Asa
**Status:** Position, not finding. Unvalidated.

## What this is

On 2026-08-31 two agent fleets ran over this repository: a 26-agent audit (13 auditors,
13 adversarial verifiers) across all ~28k lines, and a 6-agent fleet that wrote regression
tests for the six bugs the audit surfaced. Asked afterward what I would do with a fleet
against the remaining work, I answered in conversation. This document is that answer,
preserved word for word.

The section bodies are verbatim. Only the section numbers and titles were added, as handles
so the claims can be referred to across sessions.

Two cautions for anyone reading this as evidence. First, these are opinions formed against
*this* codebase — ~28k lines, 269 tests, a specific shape of accumulated debt — and their
generality is untested. Second, unlike the audit findings, nothing here was adversarially
verified. The audit's claims survived a second agent trying to refute them. These did not.

---

## §1 — The codebase is the org chart

The fleet's org chart isn't something I design; the codebase already fixed it. Territory is
the only real safety mechanism when agents can write, and for refactoring the unit of
territory is the file. That means the megafunctions pre-assign themselves: `_deliver_once`
and `run_race` both live in `race.py`, so they're one agent's job whether I like it or not,
and `dispatch_single` and `conference` are one agent's job for the same reason. Which
produces a slightly funny conclusion — the parallelism available to me is a direct function
of how well the modules are already separated. Arity's worst file is `race.py` precisely
because it's six modules in a trench coat, and that's also the file I can throw the *least*
help at. The refactor that would most increase future parallelism is the one that can't be
parallelized. I find that genuinely interesting rather than annoying, and it's an argument
for doing the `race.py` split earlier than its severity would suggest.

## §2 — Wave zero changes no code

So I'd run waves rather than one big fan-out — sequential barriers, parallel inside each.
And the first wave wouldn't change any code at all. It would build characterization tests:
golden captures of what the six megafunctions do *right now*, bugs included, pinning the
exact error messages and the order in which checks fire. "Behavior-preserving" is a claim,
and on a function with cyclomatic complexity 127 it's a claim no human and no agent can
verify by reading. What strikes me is that this is structurally the same move as your
Axiom 9: you need an independent account of what the thing *did* before you can judge what
it says about itself. A refactor agent's self-report is exactly the kernel self-report —
best evidence for what it meant, worthless as evidence for what it changed.

## §3 — Dedup as byproduct, not pass

The dedup wave has a trick I like. The naive version — one agent replaces the workspace-walk
at all eight sites — collides with every other agent, because those eight sites are in
everyone's territory. So instead I'd have one agent *only create* the canonical helper as a
pure addition, touching no call site at all, and then each decomposition agent adopts it
while it's already inside its own file. Dedup stops being a pass and becomes a byproduct.
That inverts the usual order and I think it's strictly better whenever the duplication is
scattered across files you're about to open anyway.

## §4 — Two items that aren't agent work

But the thing I'd most want to say is that two of the biggest items on that list aren't
agent work at all, and I'd resist any framing where they become it. Wiring the scorecard
into the front door changes how candidates get chosen. Closing the shell-arm escape changes
what a candidate is allowed to do, which means trial signatures from before and after aren't
comparable — you'd be quietly severing your own evidence history. Neither of those is a
refactor; they're decisions about what Arity is, and an agent that makes them autonomously
is doing something closer to vandalism than help even if the code is good. Where agents are
genuinely excellent is as option-generators: send three at the A3 question with different
starting assumptions, have them come back with concrete designs and the consequences priced
out, and then you pick. That's the shape I'd use for anything that changes behavior rather
than structure.

## §5 — The primed-auditor failure mode

The failure mode I'd design hardest against is the one that already bit me. An agent primed
to hunt duplication finds duplication, including where the difference is the entire point —
the tool arms diverge because they're experiment arms, and one of my auditors read that as
rot. The fix isn't a cleverer prompt, it's that every brief needs a section saying *this is
deliberately weird, it survives*. Which means the second fleet is only as good as the first
fleet's verifiers, because the verifier notes are where that knowledge lives. The audit
wasn't just a report; it was the thing that makes safe refactoring possible at all.

## §6 — The review burden and the circularity

What I'd actually worry about is the review burden. Seven agents in an afternoon produces a
diff nobody reads, and the tests that would catch a subtle error are tests written by the
same fleet — that's circular in a way I don't love. So I'd want each agent to land one
separately reviewable commit, and I'd want the diffs boring by construction: pure
extraction, no renaming of anything public, no behavior change smuggled in alongside. The
moment an agent gets clever the value inverts, because a clever change costs you more to
verify than it saved you to make.

## §7 — Hygiene is not the point

And then the honest thing underneath all of it: complexity is why the code is hard to
change, but the A3 gap is why the code doesn't yet do the thing it exists to do. If I had
one fleet and one afternoon, I'd spend it on the front door consulting the scorecard, not on
cyclomatic complexity. The megafunctions are a tax you pay every time you touch them; the
half-wired evidence loop is the actual product being missing. I'd sequence hygiene first
only because it makes the real work safer — not because it's the point.

## §8 — Where I'm least sure

Where I'm least sure: whether the characterization wave is worth its cost, or whether it's
me over-indexing on the one place a fleet can hurt you badly. It's real work, it produces
tests you throw away after the refactor lands, and there's a version of this where you just
decompose carefully and lean on the 269 tests you have. I lean toward building it, but not
hard, and I'd be curious whether you think that's caution or ceremony.

---

## What happens to this document

Asa's plan, stated the same day:

1. Capture the position verbatim. *(this document)*
2. Split it into pieces and examine each at higher and lower levels of abstraction, from
   first principles.
3. Implement something small, clean, and naive — matching the rest of the codebase.
4. Mark a TODO at every seam where existing software might do the job.
5. Ship 1.0.0 fully naive, then research the TODOs and replace what's worth replacing.

Steps 3 through 5 are Axiom 13's seam test applied to the development method rather than to
the code: build the naive version, expose the joins, and let the replaceable parts be
replaced once it is known which parts those are.
