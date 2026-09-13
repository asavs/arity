# The Sebastian Aaltonen (`sebbbi`) School: Invariants, Zero-Cases, and Non-Goals

> **Location**: `.arity/schools/aaltonen.md`  
> **Tradition**: Ubisoft RedLynx (*Trials*), Second Order (*Claybook*), Unity, HypeHype, *NoGraphicsAPI*, *OffsetAllocator*, *msl_heap*  
> **Primary Sources**: `NoGraphicsAPI/AGENTS.md`, Git History of `sebbbi/NoGraphicsAPI` and `sebbbi/OffsetAllocator`  
> **Core Axiom**: *"Delete weightless code, refuse needless abstractions, state the architectural invariant, and bound the zero-case."*

---

## 1. The Primary Source: `AGENTS.md`

In `NoGraphicsAPI`, Sebastian Aaltonen bypassed corporate contributing templates and codified his engineering doctrine directly in `AGENTS.md`. These rules define the standard for all code committed to the repository:

### Core Engineering Rules (from `AGENTS.md`)
> * *"Write simple, efficient, minimal C/C++ code."*
> * *"Do not add obvious comments such as 'arguments must be live objects'. C/C++ programmers already understand that using destroyed objects is invalid."*
> * *"Do not add asserts or comments describing 32-bit overflow cases. The existing 32-bit ranges (about 4 billion elements or 4 GB) are sufficient."*
> * *"Do not use C++ standard-library headers or facilities in project code, including utilities, examples, and tests. The sole exception is `<initializer_list>` and `std::initializer_list` for Span construction."*
> * *"Avoid adding named variables for trivial expressions, especially when the value is used only once."*
> * *"Disable C++ exceptions and RTTI across the entire codebase. Do not use either."*
> * *"Treat the library as a low-level, thin wrapper, not as a validation layer. Validate only inputs and state whose misuse could make the wrapper itself crash."*
> * *"Do not check for memory allocation failures. We cannot recover from running out of memory; managing memory usage properly is the user's responsibility."*
> * *"Avoid lambdas and complex templates."*
> * *"Avoid trivial single-line wrapper functions."*
> * *"Avoid trivial single-element wrapper structs."*
> * *"Avoid memory allocations, including short-lived local vectors."*
> * *"Do not use `std::shared_ptr`. Avoid `std::unique_ptr`."*
> * *"Do not use class inheritance or virtual functions."*
> * *"Avoid standard-library algorithms; prefer straightforward loops."*
> * *"Line length is 160 characters. Please don't chop expressions to multiple lines if not needed."*

---

## 2. The Anatomy of a Sebbbi Commit

Sebbbi’s commit messages are dense, declarative architectural specifications. They avoid conversational filler and directly record:
1. **Ownership & Private State**: Where data lives and who owns it.
2. **Defaults & The Zero-Case**: Explicitly stating default capacities and what zero means.
3. **Intentional Omissions**: Stating what was left out or supported as optional.
4. **Empirical Validation**: A clean closing verification line.

---

## 3. Verbatim Commits by Sebastian Aaltonen

### Example 1: `c3fcd55` (*NoGraphicsAPI*)
```
Add GPU-pointer timestamps and depth-only raster pipelines

Keep timestamp query pools private to reusable command contexts and resolve 
results through device-address commands. Configure capacity at device 
initialization, defaulting to 256, with zero disabling allocation. Expose 
timestamp period and sampler precision, support omitted fragment stages, 
and group public APIs logically.
```
*Analysis:* 
- *Ownership:* "Keep timestamp query pools private to reusable command contexts..."
- *Mechanism:* "...resolve results through device-address commands."
- *Defaults & Zero-Case:* "Configure capacity at device initialization, defaulting to 256, with zero disabling allocation."
- *Omissions/Non-goals:* "...support omitted fragment stages..."

---

### Example 2: `e025cc1` (*NoGraphicsAPI*)
```
Simplify Vulkan wrapper implementation and utilities

Remove redundant Vulkan input validation and simplify resource and command lifetimes. 
Clean up allocators, examples, formatting, build configuration, and review documentation.

Validated with MSVC Debug and Release builds and all seven tests in both configurations.
```
*Analysis:* Clean statement of simplification and deletion of redundant validation, followed immediately by empirical proof across compilers and test suites.

---

### Example 3: `a2efb6c` (*NoGraphicsAPI*)
```
Remove C++ standard-library dependencies
```
*Analysis:* A 5-word atomic commit that completely stripped `<vector>`, `<string>`, and `<memory>` out of the codebase in one clean cutover.

---

### Example 4: `4b8e23e` (*OffsetAllocator*)
```
big update: allocationSize API for external validation, support for 16 bit offsets 
(smaller metadata), move constructor, safety checks (double delete, out of allocs), fixed bugs
```

---

## 4. The Sebbbi Committer Checklist

- [ ] Does the commit use simple, efficient, minimal C/C++20 without standard library bloat?
- [ ] Are public struct fields initialized with useful defaults, with call sites naming only non-default fields via designated initializers?
- [ ] Are `Span`, `ByteSpan`, and `GpuRange` function parameters passed strictly **by value** so pointer-and-size fit in CPU registers?
- [ ] Is per-frame memory allocation completely banned in the render loop?
- [ ] Does the commit message explicitly state the zero-case and defaults?
- [ ] Did you avoid defensive over-checking for things Vulkan validation or 32-bit limits already guarantee?
- [ ] Does the commit end with an empirical validation line?
