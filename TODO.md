# TODO

Next work for Arity (post-1.0.1).

## Safety & Seams

- Fast model safety check on `bash`: small fast model acts as an inspector before shell execution.
- Worktree / scratch directory isolation for parallel tool execution (avoid file collision).
- Real MCP runner behind `ToolSeam`: JSON-RPC over subprocess (`tools/list` -> `schemas`, `tools/call` -> `execute`).
- Tool schema versioning: carry version on the row so outcomes stay comparable after edits.

## Memory & Addressing

- The Bulletin Board: turn `ledger` into a live blackboard tool (`post_note`, `read_notes`) so fresh bots wake knowing project facts without chat bloat.
- Interrupt / Cancellation: `Cancel` event that kills running subprocesses / in-flight HTTP requests and unblocks the next turn boundary immediately.

## Performance & Telemetry

- Cache preservation & keepalive: measure prompt cache hits across warm windows.
- Hybrid Judge metrics: incorporate wall-time, token costs, and exit codes into the deterministic judge filter.
