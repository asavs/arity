# arity (0.2.0)

A composable statechart chassis for autonomous AI agents.

## Core Philosophy

`arity` separates pure state transitions from side-effect execution:

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

1. **`ModelProvider`** (`arity.seams.ModelProvider`): Protocol for model routing (OpenRouter, LiteLLM, vLLM, direct stdlib `urllib`).
2. **`ToolRunner`** (`arity.seams.ToolRunner`): Protocol for tool execution (MCP, local Python functions, Docker/WSL sandboxes).
3. **`RecordStore`** (`arity.seams.RecordStore`): Protocol for persistence (JSONL, SQLite, Vector DBs, audit logs).
4. **`Transport`** (`arity.seams.Transport`): Protocol for user/channel I/O (CLI, Discord, Slack, SMS).
5. **`Observer`** (`arity.seams.Observer`): Protocol for telemetry, evaluators, and blind scorecard judges.

## Quickstart

### Run the Architectural Demo
```bash
python -m arity demo
```

### Python API Example
```python
from arity import Runtime, LocalToolRunner, OpenAIModelProvider

# Initialize runtime with custom or default seams
runtime = Runtime(
    model_provider=OpenAIModelProvider(model="gpt-4o"),
    tool_runner=LocalToolRunner(),
)

# Run interactive or multi-turn turns
output, state = runtime.chat("Create a hello.txt file with 'Hello from arity!'")
print(output)
```

### Run Unit Tests
```bash
python -m unittest tests/test_arity.py
```
