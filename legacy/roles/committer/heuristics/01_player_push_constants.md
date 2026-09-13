# Case Study 01: Triangle Entity Push Constants

> **Location**: `.arity/roles/committer/heuristics/01_player_push_constants.md`  
> **Topic**: Feature addition & CPU-to-GPU data flow  
> **Evolution**: Grade B $\to$ Grade S  

---

## 1. Initial State (Grade B)

```git
examples: pass player position to triangle via push constants

Demonstrate zero-allocation CPU-to-GPU data flow using vkCmdPushDataEXT.
Introduce a shared C++/Slang 8-byte PlayerRoot structure in triangle_shared.h
and offset vertex coordinates directly in the vertex shader, scaling the 
triangle from screen-spanning geometry down to an entity scale.

Deliberately omit vertex buffers, uniform buffers, camera projection matrices,
and game state machines to keep the example as the bare-minimum entity loop.
Query input through example_support to prevent platform macro leakage into the
example source. Retitle window to "Sol Eremus 2D".

Validated with MSVC Release build and interactive input at 60 FPS on RTX 3070.
```

### Flaw Breakdown
1. **Cargo-Cult Prefix (`examples:`)**: Conventional Commits prefix burns 10 characters without adding signal.
2. **Resume Buzzwords**: *"Demonstrate zero-allocation..."* sounds like a LinkedIn pitch deck. Code doesn't demonstrate; code executes.
3. **Narrative Fluff**: *"scaling the triangle from screen-spanning geometry down to an entity scale"* narrates an obvious 2-line code diff.
4. **Defensive Lecturing**: *"Deliberately omit vertex buffers... to keep the example as the bare-minimum loop"* argues with an imaginary reviewer instead of stating bounds.
5. **Preachy Explanations**: *"to prevent platform macro leakage into the example source"* lectures the reader on why macro leaks are bad.
6. **Incidental Trivia**: *"Retitle window to 'Sol Eremus 2D'"* has zero architectural relevance to future debuggers.
7. **Preamble Padding**: *"Validated with..."* wraps the benchmark in polite corporate padding.

---

## 2. The Surgical Transformations (The LinkedIn Red Pen)

* Strike out all resume verbs (*"demonstrate"*, *"leverage"*, *"robust"*).
* Strike out the conventional commit prefix; use natural imperative English.
* State boundary constraints as plain facts (*"clamped to normalized clip space [-0.85, 0.85]"*).
* Drop *"Validated with"*; state the benchmark directly.

---

## 3. Final Distilled Result (Grade S)

```git
Pass player position to triangle via push constants

Offset triangle vertices by an 8-byte PlayerRoot struct pushed through 
vkCmdPushDataEXT. Update coordinates from keyboard input in example_support, 
clamped to normalized clip space [-0.85, 0.85].

MSVC Release build and interactive input: 60 FPS on RTX 3070.
```

### Metrics:
* **Word count**: 41 words (47% reduction).
* **LinkedIn buzzwords**: 0.
* **Information density**: 100% load-bearing types, APIs, coordinate bounds, and empirical data.
