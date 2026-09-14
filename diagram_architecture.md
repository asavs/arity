# Runtime architecture

```mermaid
flowchart LR
    Input["input"] --> Dispatch{"loop(input)"}

    Dispatch -->|"Runtime → list[Agent]"| Runtime
    Dispatch -->|"list[Runtime]"| Runtimes["list[Runtime]"]
    Runtimes --> Parallel["loop(sub) for each Runtime"]
    Parallel --> Merged["merge(outputs)"]

    Dispatch -->|"ChannelMsg"| Channel["ChannelMsg"]
    Channel --> Synthesis["synthesize_by_cherry_pick"]

    subgraph Runtime["Runtime"]
        Agents["agents: list[Agent]"]
        Grades["grades: MemoryBucket"]
        Sys["sysprompt: string"]
        History["chat_history: list[string]"]
        Bash["bash_handle: BashCall | null"]
        Teammate["teammate_channel: TeammateMsg | null"]
        Results["results: list[ButtonResult]"]
    end

    Sys --> Prompt["prompt"]
    History --> Prompt
    Bash --> BashResult["bash_handle.result"]
    Teammate --> Handoff["response_file"]
    Prompt --> Execution["execute_agent"]
    Agents --> Execution
    Execution --> Results
    Results --> Grades
    Results --> ScoreHistory["agent: score"]
    ScoreHistory --> History
    History --> Trim["latest 10 entries"]
    Trim --> History
    Results --> First["results[0]"]

    Merged --> Output["ButtonResult"]
    Synthesis --> Output
    First --> Output
```

`Runtime` carries the prompt rules, recent chat, optional bash state, optional teammate handoff, agents, accumulated grades, and current results. A Runtime input contains `list[Agent]`, builds its prompt from `sysprompt` and the latest three chat entries, and produces one `ButtonResult` per agent. Scores append to `grades` and chat history, which retains the latest ten entries. A list of runtimes merges their loop outputs. A `ChannelMsg` becomes cherry-picked synthesis.
