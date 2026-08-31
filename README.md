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

1. **`ModelProvider`** (`gorkbot.seams.ModelProvider`): Protocol for model routing (OpenRouter, LiteLLM, vLLM, direct stdlib `urllib`).
2. **`ToolRunner`** (`gorkbot.seams.ToolRunner`): Protocol for tool execution (MCP, local Python functions, Docker/WSL sandboxes).
3. **`RecordStore`** (`gorkbot.seams.RecordStore`): Protocol for persistence (JSONL, SQLite, Vector DBs, audit logs).
4. **`Transport`** (`gorkbot.seams.Transport`): Protocol for user/channel I/O (CLI, Discord, Slack, SMS).
5. **`Observer`** (`gorkbot.seams.Observer`): Protocol for event/effect telemetry and evaluation monitoring.
6. **`ContextAdapter`** (`gorkbot.terrarium.ContextAdapter`): A named, testable transformation applied at the context boundary before a candidate runtime starts.
7. **`TrialEvaluator`** (`gorkbot.evidence.TrialEvaluator`): Evaluates an immutable `EvidenceBundle`; alternate evaluators can run later without rerunning candidate harnesses.
8. **`TrialJournal`** (`gorkbot.trial_events.TrialJournal`): Persists ordered lifecycle events through any `RecordStore`; `replay_trial` validates the declared arms, evidence, reviews, resolutions, and delivery.

## Quickstart

### Run a trial
```bash
python -m pip install .
arity --help
arity run --mock --arity 3 --task lru_cache
```

`--arity` is a positive requested maximum, not a promise to duplicate candidates until N seats exist. Resolution order is explicit `--arity`, then `ARITY`, then the compatibility fallback `GORKBOT_CONCURRENCY`, then the command default. Reports expose both the requested maximum and the number of unique candidates actually resolved.

### Python API (`gorkbot` namespace)
```python
from gorkbot import Runtime, LocalToolRunner, OpenAIModelProvider

# Initialize runtime with custom or default seams
runtime = Runtime(
    model_provider=OpenAIModelProvider(model="gpt-4o"),
    tool_runner=LocalToolRunner(),
)

# Run interactive or multi-turn turns
output, state = runtime.chat("Create a hello.txt file with 'Hello from Arity!'")
print(output)
```

### Run Tests
```bash
python -m pytest -q tests
```

The clean installed-wheel acceptance gate is separate from the source suite:

```bash
python acceptance/verify_installed.py
```

## Compatibility Boundary

The distribution and primary command are named **Arity**. The Python API remains available only from the `gorkbot` package (`import arity` is not provided); `python -m gorkbot` and the `gorkbot` console command remain supported entry points. `.gorkbot/` remains Arity's active state/config location: credentials, records, configuration, and local definition overrides are read or written there. `GORKBOT_*` settings remain compatibility fallbacks where `ARITY_*` counterparts exist; no state migration is performed. Historical release notes keep the names used when they were published.

This repository does not yet include a license file. See [RELEASE.md](RELEASE.md) for historically named release notes.
