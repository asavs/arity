# End-to-End Description — Multi-Model Commit & Grading Flow (Test Pseudocode)
# Written in plain language for the harness design. No compiled code yet.

## What the user (A) does
A sends one message: "commit that work you just did" (referring to kernel3.pseudo).
In the harness, this translates to calling loop() with a flat Runtime that carries:
  - sysprompt: the committer rules (committer.md content)
  - chat_history: previous grades / context
  - agents: the user A (or a proxy representing the request)
  - no bash_handle (unless A asks for bash); no teammate_channel yet.

## What happens inside the loop (step 1: spawn B agents)
The loop receives a list input: [Runtime_for_B1, Runtime_for_B2, Runtime_for_B3].
This is the loom (#1 superparallel). Each Runtime is identical in shape but differs in:
  - agent persona: B1-inkling, B2-chatgpt, B3-nemotron
  - sysprompt: same base (committer.md + schools) but each agent may emphasize different school

The loop runs in parallel (goroutine/model invocation in eventual Go; here just the concept).
No special branch knows it's "scoring" or "designing" — it's just loop(sub) for each item in the list.

## What B agents are supposed to do (step 2: commit writing)
Each B agent reads:
  - kernel3.pseudo (the diff to commit)
  - legacy/committer.md (rules)
  - legacy/schools/*.md (Torvalds, Carmack, Aaltonen, Karpathy references)
  - contract (imperative, <60 chars subject, concrete non-goals, empirical line, 6 gates reference)

Each B produces one file: harness/commits/b{N}_commit.md.
The output is concrete text, not a score or judgment. The agent does not know it's being graded.
It does not get defensive language. It just writes the commit.

## What the code does (step 3: file-based handoff)
The teammate tool (TeammateMsg) is a Runtime field, not a framework branch.
When the synthesis phase starts, the loop receives a ChannelMsg (open floor).
The channel message contains the 3 commit files (or references to them).
The synthesis writes to a file: harness/synth_commit.md (cherrypick best parts).
This matches our subagent experience: subsub wrote file -> sub read file.
No hidden state; file system is the single source of truth.

## Grading / scoring (step 4: classifier runs in parallel)
After B1/B2/B3 write commits, another loop (nested Runtime) acts as the classifier.
This is the recursive part: the scorer is not a special function; it's loop() called with a Runtime whose agents have persona="scorer".
The scorer reads the 3 commit files (or the synthesized file) and writes grades to harness/grades.md.
Each grade is a float with a concrete verdict (PASS/FAIL/UNPROVEN) per gate.
No anxiety: the scorer does not express emotion; it just produces structured output.

## Memory / calibration (step 5: grades feed future pairing)
Grades are stored in MemoryBucket (grades.md).
Over multiple turns, grades accumulate. Average grades calibrate pairing (#6):
  - high grade (0.92) -> preferred for commitBench tasks
  - medium grade (0.85) -> preferred for metric-bound tasks
  - lower grade (0.80) -> still eligible, paired for minimal-boundary tasks
No agent is blacklisted unless explicitly set (none are in this run).
Chat history is trimmed to last 10 entries (#4 minimal memory).

## Collaboration with user (step 6: open floor synthesis)
The open floor synthesis (loop(ChannelMsg)) allows either:
  - cherrypick best parts from B1/B2/B3 (our chosen method)
  - or letting B agents talk to each other via teammate_channel and compromise
In our actual execution, we used cherrypick (B1 lead + B2 metric + B3 zero-case).
If we used the talk method, the teammate_channel field would carry messages between agents, and loop would process those messages as ChannelMsg inputs.

## Confirmed to A (step 7: minimal terminal output)
From A's perspective, nothing complex is visible.
Next turn, A receives:
  - harness/confirm_to_A.md (confirmation message)
  - harness/synth_commit.md (commit ID / content)
  - harness/grades.md (report card for B1/B2/B3)
This is the minimal terminal UI (#7): concrete files, no hidden framework state.

## Information flow (end to end summary)
A message -> loop(Runtime) [spawn] -> loop([B1,B2,B3]) [loom parallel] -> files b1,b2,b3 [commit outputs]
-> loop(scorer Runtime) [classifier] -> grades [memory] -> loop(ChannelMsg) [synthesis]
-> synth_commit.md [cherrypick] -> confirm_to_A.md [UI] -> grades.md [calibration]
Every step uses the same loop() function. Every data object is either Runtime, list[Runtime], or ChannelMsg.
No hidden branches. File system is truth. Contracts are concrete.
