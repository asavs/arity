# The John Carmack School: Hardware Budgets & Empirical Deltas

> **Location**: `.arity/schools/carmack.md`  
> **Tradition**: id Software (*Wolfenstein 3D*, *Doom*, *Quake*, *Doom 3*), Armadillo Aerospace, Oculus VR  
> **Primary Source**: `ESWAT/john-carmack-plan-archive` (1996–2010 `.plan` logs)  
> **Core Axiom**: *"If you cannot measure the cycle delta, cache behavior, or memory footprint, you are just guessing."*

---

## 1. The Primary Source: Carmack's Engineering Logs

Before Git existed, John Carmack shared his daily engineering journal via the UNIX `finger` protocol. Preserved in historical archives (`ESWAT/john-carmack-plan-archive`), these daily updates functioned as the direct philosophical ancestor of modern commit logs.

Carmack's writing was relentlessly grounded in **the physical machine**: CPU clock cycles, cache evictions, memory bus bottlenecks, and hard frame deadlines.

---

## 2. Primary Source Excerpt 1: Memory Streaming & Cache Thrashing

From John Carmack's `.plan` log on memory bus bandwidth and cache writebacks:

> *"The test scene I was using created about 1.5 megs of data to relay all the function calls and vertex data for a frame. That data had to go to main memory from one processor, then back out of main memory to the other...*
>
> *Streaming large amounts of data to and from main memory can be really grim. The next write may force a cache writeback to make room for it, then the read from memory to fill the cacheline (even if you are going to write over the entire thing), then eventually the writeback from the cache to main memory where you wanted it in the first place. You also tend to eat one more read when your program wants to use the original data that got evicted at the start.*
>
> *What is really needed for this type of interface is a streaming read cache protocol that performs similarly to the write combining: three dedicated cachelines that let you read or write from a range without evicting other things from the cache, and automatically prefetching the next cacheline as you read...*
>
> *I could probably trim 30% of my data by going to a byte level encoding of all the function calls, instead of the explicit function pointer / parameter count / all-parms-are-32-bits that I have now, but half of the data is just raw vertex data, which isn't going to shrink unless I did evil things like quantize floats to shorts.*
>
> *Too much effort for what looks like a relatively minor speedup. I'm giving up on this approach, and going back to explicit threading in the renderer so I can make most of the communicated data implicit."*

*Analysis:* Notice the mental model: he tracks the exact megabytes per frame (1.5 MB), analyzes the cacheline eviction penalty, weighs byte-level encoding versus quantized floats, measures the speedup against the code complexity, and kills the approach when the numbers don't justify the architectural debt.

---

## 3. Primary Source Excerpt 2: The Latency Breakdown of the Game Loop

From John Carmack's `.plan` log on QuakeWorld (August 2, 1996):

> *"In a perfect environment, the instant you provided any input (pressed a key, moved a mouse, etc) you would have feedback on the screen (or speaker) from the action.*
>
> *In the real world, even single player games have latency.*
>
> *A typical game loop goes something like: Read user input. Simulate the world. Render a new graphics scene. Repeat.*
>
> *If the game is running 15 frames a second, that is 66 ms each frame. The user input will arrive at a random point in the frame, so it will be an average of 33 ms before the input is even looked at. The input is then read, and 66 more ms pass before the result is actually displayed to the user, for a total of nearly 100 ms of latency, right on your desktop.*
>
> *The best way to address that latency is to just make the game run faster if possible. If the screen was sized down so that the game ran 25 fps, the latency would be down to 60ms. There are a few other things that can be done to shave a bit more off, like short circuiting some late breaking information (like view angles) directly into the refresh stage, bypassing the simulation stage."*

*Analysis:* A mathematical dissection of the game loop. Frame rate is not treated as a cosmetic vanity metric; it is treated as the primary lever to minimize user-perceived input latency.

---

## 4. The Anatomy of a Carmack-Style Commit

```
[Subsystem]: Optimize <mechanism> by <concrete technical change>

[Physical Constraint & Bottleneck]
State the exact hardware bottleneck: L1/L2 cache evictions, PCIe transfer bandwidth,
register pressure, or vertex setup latency.

[Structural Delta]
Detail the exact data layout changes:
- Before: Struct layout, alignment, and total byte footprint.
- After: Compacted layout, packing, bitfields, or flattened arrays.
- Cache line fit: "Reduces struct from 48 to 32 bytes, packing 2 per 64-byte line."

[Algorithmic & Instruction Delta]
Explain why the inner loop executes fewer instructions or eliminates branch mispredictions.

[Empirical Proof / Benchmark]
Provide verifiable numbers measured on actual hardware:
- Benchmark name / timedemo
- Frame time or FPS improvement
- Hardware specification (CPU, GPU, Memory bus)
```

---

## 5. The Carmack Committer Checklist

- [ ] Does the commit name the physical bottleneck (cache misses, bus bandwidth, memory footprint)?
- [ ] Are struct layouts, alignments, and byte counts explicitly stated?
- [ ] Does the change reduce memory indirection or pointer chasing?
- [ ] Are benchmark deltas grounded in reproducible timings rather than vague claims like "faster"?
- [ ] Did you avoid dynamic allocations (`malloc`, `new`) in any path running once per frame?
