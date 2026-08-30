# arity

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![Tests](https://img.shields.io/badge/tests-40%20passing-brightgreen.svg)](tests/)
[![Architecture](https://img.shields.io/badge/architecture-pure%20statechart-orange.svg)](#core-philosophy)
[![License: MIT](https://img.shields.io/badge/license-MIT-yellow.svg)](LICENSE)

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

A composable pure statechart chassis for autonomous AI agents.

In mathematics and nature, **arity** modulates how elements compose: from a single singular voice (**unary: 1**), to A/B candidate races (**binary: 2**), to dense multi-kernel terrariums (**n-ary: $n$**). Just as nature packs sunflower seeds gaplessly at the Golden Angle ($137.5^\circ$), `arity` packs multipolar models, isolated sandboxes, and quota ledgers into an optimal, gapless coordination spiral.

## Core Philosophy

`gorkbot` separates pure state transitions from side-effect execution:

$$\text{transition}(\text{state}, \text{event}) \longrightarrow (\text{new\_state}, \text{effects})$$

Because the state machine is pure, you can graft on any external infrastructure (model routers, tool harnesses, vector memory, Discord/Slack transports, blind evaluators) without modifying the agent control loop.

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

### Run the Architectural Demo
```bash
python -m gorkbot demo
```

### Python API Example
```python
from gorkbot import Runtime, LocalToolRunner, OpenAIModelProvider

# Initialize runtime with custom or default seams
runtime = Runtime(
    model_provider=OpenAIModelProvider(model="gpt-4o"),
    tool_runner=LocalToolRunner(),
)

# Run interactive or multi-turn turns
output, state = runtime.chat("Create a hello.txt file with 'Hello from gorkbot!'")
print(output)
```

### Run Unit Tests
```bash
python -m unittest tests/test_gorkbot.py
```
