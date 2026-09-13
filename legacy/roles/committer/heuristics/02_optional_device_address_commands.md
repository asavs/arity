# Case Study 02: Vulkan Driver Extension Fallback

> **Location**: `.arity/roles/committer/heuristics/02_optional_device_address_commands.md`  
> **Topic**: Driver compatibility & build system decoupling  
> **Evolution**: Grade B- $\to$ Grade S  

---

## 1. Initial State (Grade B-)

```git
Make VK_KHR_device_address_commands optional and support Slang 2026.13.1
```
*(No body text. Empty message.)*

### Flaw Breakdown
1. **Violates the Singularity Gate**: Uses the conjunction **"and"** to bundle two separate concepts into a single subject line (Vulkan driver extension querying + CMake Slang tool version floor).
2. **Missing Architectural Rationale**: Fails to explain *why* `VK_KHR_device_address_commands` was failing (OEM laptop drivers like NVIDIA 610.62 lack this extension, causing `vkCreateDevice` to reject the candidate physical device).
3. **Missing Data & Ownership Contract**: Does not document which function pointers (`cmd_copy_memory`, `cmd_draw_indirect`, etc.) are omitted when the extension is absent.
4. **Missing Verification Proof**: Does not record whether this change was tested against actual hardware or whether device creation succeeded.

---

## 2. The Surgical Transformations

* **Isolate the Primary Invariant**: Focus the commit entirely on device creation and the extension fallback.
* **Document the Omission**: State that indirect and device-address memory copy procs are omitted when the extension is absent, preserving core descriptor heap and push data functionality.
* **Document Build Floor**: Note the Slang 2026.13.1 floor required for standard Vulkan SDK 1.4.357 installs.
* **Provide the Hardware Proof**: State the exact GPU model and driver version where device creation was verified.

---

## 3. Final Distilled Result (Grade S)

```git
Make VK_KHR_device_address_commands optional in device creation

Allow Vulkan 1.4 device creation on drivers lacking device-address commands
by querying the extension conditionally and omitting indirect/copy function
pointers when unavailable. Adjust Slang version floor to 2026.13.1 for standard
Vulkan SDK 1.4.357 compatibility.

MSVC Release build: device created successfully on RTX 3070 (driver 610.62).
```

### Metrics:
* **Subject line**: 59 characters, imperative mood, zero fluff.
* **Architectural clarity**: Explains the exact failure mode, the conditional query, and the omitted function pointers.
* **Verification**: Concrete hardware baseline and driver version explicitly recorded.
