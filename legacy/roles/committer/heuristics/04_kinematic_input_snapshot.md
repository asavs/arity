# Case Study 04: Kinematic Input Snapshots & 2.5D Feet Anchoring

> **Location**: `.arity/roles/committer/heuristics/04_kinematic_input_snapshot.md`  
> **Topic**: Simulation decoupling, input abstraction, and 2.5D hardware depth invariants  
> **Evolution**: Grade B $\to$ Grade S  

---

## 1. Initial State (Grade B Draft)

```git
Add direct kinematic player movement

Route one Input snapshot into GameWorld::step. Normalize diagonal movement and apply 0.45 crouch, 1.0 walk, and 1.65 sprint speeds. Edge-triggered jump input drives scalar player_z and vertical velocity under 4.5 gravity; grounded crouch and vertical height reshape the player quad.

Keep room collision and candle interaction semantics unchanged. No physics library or engine layer is introduced.

MSVC Debug build: warning-free. Headless: 100,000 ticks in 3.57 ms; final player (0.650, -0.080, 0.120); exit 0.
```

### Flaw Breakdown
1. **Uncalibrated Benchmark Configuration**: Cited timing measured under a non-optimized **Debug build** ($3.57\text{ ms}$) instead of Release mode ($0.75\text{ ms}$, a $4.7\times$ delta). Debug timings hide compiler vectorization and inlining metrics.
2. **Conversational Preamble & Exit Padding**: *"warning-free"*, *"exit 0"* are chat milestones, not enduring systems metrics.
3. **Implicit 2.5D Sorting Invariant**: Failed to name the single most critical graphics invariant in the patch: **separating visual sprite lift from feet depth anchoring**.

---

## 2. The Architectural Lessons

### Lesson 1: The Input Snapshot Invariant (Headless Decoupling)
- **The Anti-Pattern**: Calling platform key queries (`GetAsyncKeyState`, `platform_key_down`) inside `GameWorld::step()`, or expanding the parameter list on every new verb (`step(move_x, move_y, interact, crouch, sprint, jump, dt)`).
- **The Law**: Poll platform events strictly at the frame boundary, latch into a plain 12-byte POD `struct Input`, and pass it by `const&` into `step(input, dt)`.
- **Why It Matters**: Decouples the simulation from the OS window. Headless runners (`--headless`) synthesize input snapshots directly in memory, executing 100,000 deterministic ticks in $0.75\text{ ms}$ without spawning a Win32 message pump.

### Lesson 2: The 2.5D Feet-Anchored Depth Rule
- **The Visual Trap**: In a 2.5D perspective, when a character jumps, the sprite shifts upward in screen space ($y_{\text{render}} = y - z \times 0.45$).
- **The Invariant**: If you emit hardware depth from the visual sprite position ($y_{\text{render}}$), jumping causes the player to magically pop behind north-facing props while mid-air.
- **The Solution**: Split visual position from depth sorting:
  ```cpp
  entity.position = { player_pos.x, player_pos.y - player_z * 0.45f }; // Visual quad lifts
  entity.depth_y  = player_pos.y;                                      // Hardware Z anchored to feet
  ```
  The sprite lifts visually, while the hardware depth attachment preserves correct ground-contact occlusion against the tomb and walls.

### Lesson 3: Diagonal Normalization
- Clamping `move_len_sq > 1.0f` to unit length prevents the classic $\sqrt{2} \approx 1.414\times$ diagonal speed bug without incurring expensive square root penalties on non-diagonal inputs.

---

## 3. Final Distilled Result (Grade S)

```git
Add direct kinematic player movement

Route an Input snapshot struct into GameWorld::step(). Normalize diagonal
movement and apply 0.45 crouch, 1.0 walk, and 1.65 sprint speeds. Drive
scalar player_z and vertical velocity via edge-triggered jump input under
4.5 gravity. Reshape player quad geometry based on grounded crouch and
vertical jump height while preserving room collision and candle toggles.

MSVC Release build: 100,000 headless ticks in 0.75 ms, windowed run verified.
```

### Metrics:
* **Word count**: 55 words.
* **Architectural specifics**: Names `Input` struct, speed multipliers ($0.45, 1.0, 1.65$), gravity coefficient ($4.5$), and quad reshaping.
* **Grounded Proof**: Real Release build timing ($0.75\text{ ms}$ for $100\text{k}$ ticks = $133{,}000{,}000\text{ ticks/sec}$).
