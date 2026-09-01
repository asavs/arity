# Arity

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Architecture](https://img.shields.io/badge/architecture-event%2Feffect%20runtime-orange.svg)](#core-philosophy)

```
                 .  .  .  .
             .  '  *  *  *  '  .
          .  *  o  o  o  o  o  *  .
        .  *  o  x  x  x  x  o  *  .
       .  *  o  x  +  +  x  o  *  .
       .  *  o  x  + [1] +  x  o  *  .    r_n = c √n
       .  *  o  x  +  +  x  o  *  .      θ_n = n × 137.507764° (Golden Angle)
        .  *  o  x  x  x  x  o  *  .      Fibonacci spirals: 21, 34, 55, 89, 144
          .  *  o  o  o  o  o  *  .
             .  '  *  *  *  '  .
                 '  '  '  '
```

**One task. N agents. Facts first.**

Arity is a small, provider-agnostic trial kernel for agent harnesses. Today it compares models, harnesses, tool runners, skills, roles, and context modes against the same work, then records verified evidence. Computer-use integrations fit behind tool and harness seams; compaction and pluggable memory policies are future trial axes.

The name is the control surface. Unary (`--arity 1`) requests one voice, binary (`--arity 2`) requests an A/B trial, and n-ary (`--arity N`) requests a multipolar trial of up to N distinct candidates. A trial can resolve fewer seats than requested when fewer unique candidates are available. Arity is deliberately one composable piece of the broader effort to build agent harnesses: useful alone, more useful when its seams let independent work be tested together.

## Core Philosophy

Arity separates transition decisions from effect execution:

$$\text{transition}(\text{state}, \text{event}) \longrightarrow (\text{new\_state}, \text{effects})$$

`transition` updates the supplied state while deciding which effects are required; `Runtime` performs the I/O. This boundary lets callers supply model providers, tool runners, record stores, transports, and event/effect observers. Trials layer on built-in verification and factual archival, then freeze content-addressed evidence before an injected `TrialEvaluator` may express a preference. An explicit, attributable `Resolution` controls frozen-byte delivery. Versioned trial events make that lifecycle replayable without live workspaces or providers.

```
                  ┌────────────────────────┐
   Events ───────►│  transition(s, e)     │───────► Effects
(User, Model,     │  (Transition Logic)    │    (CallModel, ExecuteTool,
 Tool, Pulse)     └────────────────────────┘     EmitMessage, StoreRecord)
                             │
                             ▼
                  ┌────────────────────────┐
                  │    Runtime Chassis     │
                  └────────────────────────┘
                    │    │    │    │    │
                    ▼    ▼    ▼    ▼    ▼
                   [Seam Graft Points / Protocols]
                 Model  Tool Store Transport Observer
```

## The Seams (Graft Points)

1. **`ModelProvider`** (`arity.seams.ModelProvider`): Protocol for model routing (OpenRouter, LiteLLM, vLLM, direct stdlib `urllib`).
2. **`ToolRunner`** (`arity.seams.ToolRunner`): Protocol for tool execution (MCP, local Python functions, Docker/WSL sandboxes).
3. **`RecordReader`** (`arity.seams.RecordReader`): Protocol for query-only record access; the declared reader type of `inspect_trial`/`inspect_trials` and the seam behind inspection front-ends (TUIs, GUIs, dashboards).
4. **`RecordStore`** (`arity.seams.RecordStore`): `RecordReader` plus append — Protocol for persistence (JSONL, SQLite, Vector DBs, audit logs).
5. **`Transport`** (`arity.seams.Transport`): Protocol for user/channel I/O (CLI, Discord, Slack, SMS).
6. **`Observer`** (`arity.seams.Observer`): Protocol for event/effect telemetry and evaluation monitoring.
7. **`ContextAdapter`** (`arity.terrarium.ContextAdapter`): A named, testable transformation applied at the context boundary before a candidate runtime starts.
8. **`TrialEvaluator`** (`arity.evidence.TrialEvaluator`): Evaluates an immutable `EvidenceBundle`; alternate evaluators can run later without rerunning candidate harnesses.
9. **`TrialJournal`** (`arity.trial_events.TrialJournal`): Not a Protocol — a concrete class that composes the `RecordStore` seam, persisting ordered lifecycle events through any store; `replay_trial` validates the declared arms, evidence, reviews, resolutions, and delivery.

## Quickstart

### Run a trial
```bash
python -m pip install .
arity --help
arity run --mock --arity 3 --task lru_cache
```

`--arity` is a positive requested maximum, not a promise to duplicate candidates until N seats exist. Resolution order is explicit `--arity`, then `ARITY`, then the command default. Reports expose both the requested maximum and the number of unique candidates actually resolved.
### Inspect persisted trials

```bash
arity trials
arity trial show <trial-id>
arity trial replay <trial-id> --json
```

These commands are read-only: they do not run agents, consult providers, repair records, or create a missing store. `show` returns graph-ready metadata without candidate output or artifact bodies; `replay --json` is the explicit full local journal view and can include task briefs, candidate output, test results, and frozen artifact contents. Treat replay output as sensitive. Add `--json` to any inspection command for a versioned envelope. Exit codes are `0` for valid/empty, `1` for an operational read failure, `2` for command syntax, `3` for a missing trial, `4` for a safe partial projection containing a newer schema/event, and `5` for corruption.

Inspection follows the active store selection (`ARITY_STORE=sqlite` or JSONL by default) and `.arity/` paths.
### Authenticate provider harnesses

Prefer credentials managed by an official installed harness when one is available. Arity can
discover supported local sessions with `arity auth import` and report what it found with
`arity auth status`.

The direct OAuth adapters are experimental and are not endorsed by their providers. Arity does
not bundle another application's OAuth identity. Native login therefore requires caller-supplied
configuration at invocation time:

- Google Antigravity: `ARITY_GOOGLE_ANTIGRAVITY_CLIENT_ID` and
  `ARITY_GOOGLE_ANTIGRAVITY_CLIENT_SECRET`.
- OpenAI Codex: `ARITY_OPENAI_CLIENT_ID`.
- xAI Grok: `ARITY_XAI_CLIENT_ID`.
- Anthropic Claude: `ARITY_ANTHROPIC_CLIENT_ID`.

A successful native login stores the resolved client configuration with the resulting tokens.
Google, OpenAI, and xAI reuse that configuration during automatic refresh; Anthropic automatic
refresh is not currently implemented. That makes `~/.arity/auth.json` especially sensitive: it
is plaintext, owner-only (`0600`) on POSIX when Arity writes it, and protected only by the
destination directory's ACLs on Windows. See [SECURITY.md](SECURITY.md) before using these adapters.
### Python API (`arity` namespace)
```python
from arity import Runtime, LocalToolRunner, OpenAIModelProvider

# Initialize runtime with custom or default seams
runtime = Runtime(
    model_provider=OpenAIModelProvider(model="gpt-4o"),
    tool_runner=LocalToolRunner(),
)

# Run interactive or multi-turn turns
output, state = runtime.chat("Create a hello.txt file with 'Hello from Arity!'")
print(output)
```

The same read-only projection is the API intended for TUIs, GUIs, and other observers:

```python
from arity import inspect_trial, inspect_trials, open_record_reader

with open_record_reader() as reader:
    catalog = inspect_trials(reader)
    selected = inspect_trial(reader, catalog.summaries[0].trial_id)

print(selected.status, selected.to_dict()["projection"])
```

### Run Tests
```bash
python -m pytest -q tests
```

The clean installed-wheel acceptance gate is separate from the source suite:

```bash
python acceptance/verify_installed.py
```

## Configuration & State

Arity stores credentials, records, configuration, and local definition overrides in `~/.arity/` and local `.arity/` directories.
## Security

Arity is designed for a trusted single-user workstation. It is not an OS sandbox or a
multi-tenant security boundary: model-directed tools, `LocalToolRunner`, and CLI harnesses may
execute with the current user's permissions. Credentials and full trial replay data also
require careful handling. Read [SECURITY.md](SECURITY.md) before using Arity with credentials,
untrusted tasks, or external content.

## License

Arity is available under the [MIT License](LICENSE). See [RELEASE.md](RELEASE.md) for
historically named release notes.
