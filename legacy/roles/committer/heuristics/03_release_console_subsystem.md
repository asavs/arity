# Case Study 03: Windows Release Console Suppression

> **Location**: `.arity/roles/committer/heuristics/03_release_console_subsystem.md`  
> **Topic**: Linker subsystem configuration & build flags  
> **Reference Commit**: `3319611` (*NoGraphicsAPI*, merged in `b34d3c2`)  
> **Evolution**: Grade D $\to$ Grade S  

---

## 1. Initial State (Grade D)

```git
commit 33196112c3feea6b14aa7fc714aa4d41e737ba76
Author: Tarmo <tarmo888@...>

fix: no console on release build
```
*(No body text. Empty message.)*

### Flaw Breakdown
1. **Cargo-Cult Prefix (`fix:`)**: Uses the lowercase Conventional Commits prefix without adding architectural context.
2. **Grammar & Voice**: Passive lowercase fragment (*"no console on release build"*) instead of an imperative order to the codebase.
3. **Missing Mechanism & Subsystem Context**: Does not explain *how* the console is suppressed. On Windows, hiding a console requires switching the PE subsystem from `CONSOLE` to `WINDOWS` while keeping the standard `main()` entry point alive via the MSVC linker flag `/ENTRY:mainCRTStartup`.
4. **Missing Invariants & Scope**: Fails to state that console output (`printf`) is deliberately preserved in `Debug` builds for diagnostics and logging.
5. **Missing Verification Proof**: Does not record whether the fix was tested across both Debug and Release configurations.

---

## 2. The Surgical Transformations

* **Drop the Prefix & Capitalize**: Replace `fix: no console...` with a strong imperative verb: `Suppress console window in Release builds on Windows`.
* **Document the Linker Mechanism**: Name the CMake properties and linker options (`WIN32_EXECUTABLE`, `/ENTRY:mainCRTStartup`).
* **Document the Invariant**: Explicitly state that Debug builds are untouched so `printf` diagnostics remain visible during development.
* **Provide Empirical Proof**: Verify that Release builds suppress the console and Debug builds preserve it.

---

## 3. Final Distilled Result (Grade S)

```git
Suppress console window in Release builds on Windows

Set WIN32_EXECUTABLE and /ENTRY:mainCRTStartup on example executables in 
Release configurations, switching the PE subsystem to a native window 
while preserving console stdout logging in Debug.

MSVC Debug and Release builds: verified console suppression on Release.
```

### Metrics:
* **Subject line**: 52 characters, imperative mood, zero fluff.
* **Technical precision**: Explains the exact PE subsystem switch and MSVC entry point forwarding.
* **Invariants**: Clarifies the Debug vs. Release boundary.
* **Proof**: Explicitly validated across both build configurations.
