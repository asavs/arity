# gorkbot (0.1.2)

A composable statechart chassis for autonomous AI agents.

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
