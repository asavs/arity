Unify runtime loop around embedded tool handles

Author: B1-inkling

Problem
The harness split tool execution and agent execution into framework branches, hiding tool state outside the runtime loop and making concurrent and message-driven inputs follow separate paths.

Solution
Embed the system prompt, bounded chat history, optional bash handle, and optional teammate channel in Runtime. Run list inputs through the same loop, write teammate responses to their declared handoff file, execute optional bash before agent work, record scores, and retain the newest ten history entries. The runtime invariant is one loop for single, parallel, and channel inputs; absent tool handles are the zero-case.

Non-goals
A full chat framework, a complex tool registry, and explicit recursive calls remain outside this change.

6 gates
Bisection: one self-contained scaffold. Singularity: runtime-loop unification only. Boundary: non-goals named above. Hygiene: tools remain Runtime fields. Maintainer: no new framework layer. Upstream invariant: preserve the small sysprompt-and-file-handoff kernel.

51 lines; 2 type aliases, 1 Runtime struct, and 1 loop function.
