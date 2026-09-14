```mermaid
flowchart LR
    A[User A] --> R[loop(Runtime)]
    R --> L[loop([Runtime]) / loom]
    L --> B1[B1-inkling]
    L --> B2[B2-chatgpt]
    L --> B3[B3-nemotron]
    B1 --> C[commits/]
    B2 --> C
    B3 --> C
    C --> J[loop(scorer Runtime) / judge classifier]
    J --> G[grades.md]
    G --> S[loop(ChannelMsg) / synthesis]
    S --> SC[synth_commit.md]
    SC --> K[confirm_to_A.md]
    K --> A
```
