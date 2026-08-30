# Arity

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Architecture](https://img.shields.io/badge/architecture-pure%20statechart-orange.svg)](#core-philosophy)

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

Arity is a small, provider-agnostic trial kernel for agent harnesses. Plug in a computer-use system, compaction strategy, memory layer, model router, tool runner, or evaluator; run candidate stacks against the same work; then keep the verified evidence needed to learn what is good for what.

The name is the control surface. Unary (`--arity 1`) is one voice, binary (`--arity 2`) is an A/B trial, and n-ary (`--arity N`) is a multipolar trial. Arity is deliberately one composable piece of the broader effort to build agent harnesses: useful alone, more useful when its seams let independent work be tested together.

## Core Philosophy

Arity separates pure state transitions from side-effect execution:

$$\text{transition}(\text{state}, \text{event}) \longrightarrow (\text{new\_state}, \text{effects})$$

Because the state machine is pure, you can graft on external infrastructure (model routers, tool harnesses, memory, transports, blind evaluators) without modifying the control loop. Trials add isolated candidate workspaces, hidden verification, blind review on factual ties, conferences, delivery receipts, and empirical standings. The evaluator itself is a replaceable seam.

```
                  ┌────────────────────────┐
   Events ───────►│  transition(s, e)     │───────► Effects
(User, Model,     │  (Pure Statechart)     │    (CallModel, ExecuteTool,
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
5. **`Observer`** (`gorkbot.seams.Observer`): Protocol for telemetry, evaluators, and blind scorecard judges.

## Quickstart

### Run a trial
```bash
python -m pip install .
arity --help
arity run --mock --arity 3 --task lru_cache
```

`--arity` must be a positive integer. Resolution order is explicit `--arity`, then `ARITY`, then the legacy `GORKBOT_CONCURRENCY` setting, then the command default.

### Python API Example
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

### Run Unit Tests
```bash
python -m unittest discover -s tests -v
```

## Compatibility Boundary

The distribution and user-facing command are named **Arity**. The `gorkbot` Python package, `python -m gorkbot`, and the `gorkbot` console-script alias remain supported so existing integrations do not break. Existing `.gorkbot/` state directories and `GORKBOT_*` settings are also read in place; Arity does not rename, copy, or delete that user data. Remaining `gorkbot` names in import paths, compatibility identifiers, state paths, and historical release notes are intentional.

This repository does not yet include a license file. See [RELEASE.md](RELEASE.md) for historically named release notes.
