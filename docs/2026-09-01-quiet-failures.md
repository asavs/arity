# Quiet failures — every silent exception handler in `arity/`

**Date:** 2026-09-01
**Scope:** the `arity/` package only. The root `arity.py` legacy prototype and `impl/` are
excluded by design.
**Companion to:** the 2026-08-31 codebase audit, [the axiom backlog](2026-09-01-axiom-backlog.md).

## The count

**42 silent handlers** — every one of them `except <Type>: pass` or `except <Type>: continue`,
across 16 files.

| Class | Count | What it means |
|---|---:|---|
| **HIDES DATA LOSS** | 12 | A record, credential, or evidence write can vanish with no trace. |
| **HIDES A BUG** | 16 | A `TypeError`, `AttributeError`, or signature mismatch is silently absorbed. |
| **BENIGN** | 14 | The failure genuinely does not matter. |
| **UNCLEAR** | 0 | — |

`UNCLEAR` is empty on purpose. Every one of the 42 was traced to a consequence. That is a
finding, not a gap: none of these handlers is load-bearing in a way nobody understands, so
there is no research blocking the decision below.

Two facts worth having before reading the tables:

- **There are no bare `except:` clauses anywhere in the package.** Every handler names a type.
- **There is no `logging` at all** — the module is never imported. So there is no
  "logs to nowhere" category either: the only handler that reports anything reports it to
  stderr (`auth.py:329`, which prints on token-refresh failure and says why). Silence here
  means literal silence, not a log line nobody reads.

## The two known live examples

**1. Scorecard records dropped at write time (`scorecard.py:162`).** `record_verdict` updates
the in-memory standing via `_apply_delta`, then appends a `scorecard` record to the store inside
a `try: ... except Exception: pass`. If the append fails, the standing has already moved and the
record that would explain the move is gone. The returned `ScorecardRecord` looks identical either
way. The scorecard is therefore not replayable from records — it is only ever as good as the
process that is currently running.

**This one is not a one-off.** `runtime.py:90` is the same swallow around
`self.store.append(effect)` in the effect dispatcher, which is the path *every* `StoreRecord`
takes: `model_turn`, `tool_result`, and `friction`. The scorecard bug is the specific case; the
runtime line is the general one, and it is first in the table below for that reason.

**2. A peer-consult `TypeError` swallowed by the tool runner (`tools.py:172`).** This one is a
different shape and is **not** among the 42 — the handler has a body. It converts:

```python
except Exception as e:
    return ToolCompleted(..., output=f"Execution Error: {str(e)}", is_error=True)
```

`message(to="scout", …)` reaches `message_tool`, which calls `self.message_router(...)`, which
runs a whole peer kernel through `dispatch_single`. Any programming error anywhere down that
call chain comes back as a tool-result string. Confirmed by probe:

```
is_error= True
output= Execution Error: dispatch_single() got an unexpected keyword argument 'role'
```

A signature mismatch in the orchestrator is presented to the model as a normal tool failure,
in the same shape as "the file does not exist". The model apologises and works around it, the
run completes, and nothing in the records distinguishes a broken peer-consult seam from a peer
that had nothing to say. This is worth deciding on alongside the 42, because the fix is a
different one: not "where should the error go" but "which exceptions should this handler
convert at all".

---

## HIDES DATA LOSS — 12

| Site | Wraps | What is lost when it fires |
|---|---|---|
| `runtime.py:90` | `self.store.append(effect)` | **Every** `StoreRecord` effect: `model_turn`, `tool_result`, `friction`. The turn still happens, the state still advances. This is the general case of the scorecard bug. |
| `scorecard.py:162` | `store.append(kind="scorecard")` | The verdict, delta, standing-after, and signature. Standing already moved in memory. **Known live example.** |
| `archivist.py:193` | `store.append(kind="archivist_entry")` | Verdict, discrepancy, verified artifacts, test results for one candidate. The `ArchivistEntry` is still returned, so the trial reads as judged. |
| `archivist.py:243` | `store.append(kind="trial_axes")` | The per-candidate axes — the whole quantitative basis for ranking that candidate. |
| `terrarium.py:679` | `store.append(kind="terrarium_trial")` | The trial record itself: seat, signature, harness, tokens, duration, status, error. |
| `transports.py:94` | `store.append(kind="redphone_message")` | The durable copy of a redphone message. It stays in the in-memory `_channels` list and `drain()` still returns it, so the loss only shows up later, as an absence. |
| `handlers.py:520` | `json.loads(line)` in `JsonlRecordStore.query` | Read-side. A corrupt JSONL line is skipped and `query` returns a **short list with no integrity signal**. Inspection, replay, and the archivist's `trace_axes` all under-report and none of them can tell. |
| `runtime.py:97` | `self.transport.emit(effect)` | The user's reply. `state.output` holds the text, the state says the turn succeeded, and the message is never delivered. Indistinguishable from a model that returned nothing. |
| `auth.py:913` | JWT claims parse for `account_id` | `accountId` is written as `None`. `wire.py:513` gates on `if token and account_id:`, so the credential can never drive the Codex wire provider — a successful login silently produces a seat that falls back to the CLI harness. |
| `auth.py:578` | `GOOGLE_USERINFO_URL` fetch for `email` | The account's identity. `email` stays `""`, so the guarded `store.save_credential(f"google-antigravity:{email}", …)` at line 598 **never runs** and only the bare provider key is written. That is precisely the failure `wire.py:49` documents: "Refreshing the bare provider key returned the first account on file and silently moved every seat onto it." |
| `auth.py:639` | `:onboardUser` POST | The real project id. Onboarding failure is invisible, the reload finds nothing, and `discover_and_onboard_antigravity_project` returns the literal string `"default-antigravity"` (line 647), which is then persisted into the credential and used for every subsequent API call and quota fetch. A placeholder is stored where a real value should be. |
| `archivist.py:368` | `src = p.read_text(encoding="utf-8")` in `code_axes` | Evidence understated rather than absent. A non-UTF-8 `.py` file is skipped, so `loc`, `py_files`, `test_count` and `bare_asserts` under-count — and `compile_ok` stays `True` for a file that was never parsed. These axes feed ranking. |

## HIDES A BUG — 16

| Site | Wraps | What is absorbed, and what it costs |
|---|---|---|
| `ledger.py:278` | the entire seat-mounting block (`TokenStore()` through the Anthropic `register(Seat(...))`, ~60 lines) | One `AttributeError` in any `Seat` construction stops registration **partway**. Some seats mount, the rest silently do not. The roster is smaller than it should be, and which trials can run changes with no message. |
| `handlers.py:283` | `create_wire_model_provider(seat)` | An `ImportError` or `TypeError` in wire construction silently demotes the seat to an API-key or CLI provider. The harness axis changes underneath the experiment, and the trial signature records the fallback harness as though it were chosen. |
| `handlers.py:322` | the same, in `create_default_model_provider` | Same, for the default provider path. |
| `roles.py:239` | `self.register(load_role_from_file(path))` | A role `.md` that fails to parse is silently absent. `resolve()` never raises for a missing role — it **falls back to the Secretary** (line 329/340). Every request for that role is quietly answered by a different one. |
| `roles.py:247` | `self.register_type(parse_type_document(...))` | The same shape for role types. |
| `skills.py:133` | manifest read + `register(Skill(...))` | A skill silently is not loaded, but the candidate's signature still names it. The scorecard then credits or blames a skill that never ran — contaminated evidence, not just a missing feature. |
| `tasks.py:109` | `load_task_dir(tdir)` | A malformed task directory is silently absent from the bank; `TaskBank.get(name)` returns `None` as though the task was never written. |
| `race.py:1014` | the whole review-JSON decode and validation loop | A `TypeError` in the validation code is indistinguishable from "this candidate's JSON did not parse". `parsed` stays `False` and the review verdict is dropped. |
| `runtime.py:68` | `obs.on_event(state, event)` | An observer with a signature mismatch is **silently inert for the entire run**. The Observer seam is the telemetry seam; this is the handler that can turn it off without saying so. |
| `runtime.py:79` | `obs.on_effect(new_state, effect)` | Same. |
| `wire.py:65` | per-seat token auto-refresh | An `AttributeError` here means refresh never happens for this seat and the call proceeds with a stale token. Surfaces downstream as a 401 attributed to the model, not to auth. |
| `wire.py:312` | per-event SSE parse, including the usage-token extraction | If usage parsing raises, `prompt_tokens`/`completion_tokens` stay 0 and metering falls back to `len(text)/4` — the exact bug the comment at lines 304–306 says was already fixed once. The `"estimated": True` flag in the usage dict is the one honest signal, and it does not distinguish "the API sent no usage" from "our parser has a bug". |
| `auth.py:245` | per-row OMP credential parse | `parsed.get("email")` on a non-dict raises `AttributeError` and is absorbed; one credential is silently not imported. |
| `auth.py:248` | the whole OMP SQLite block, **including `conn.close()`** | A schema change or a code error reads as "no OMP credentials". The connection also leaks, because `close()` is inside the `try`. |
| `auth.py:264` | the Codex `auth.json` parse block | A `KeyError`/`TypeError` reads as "no Codex credential on this machine". |
| `tools.py:464` | `json.loads(config.json)` in `get_config_value` | A single typo in `.arity/config.json` silently disables **every** setting in the file; each lookup falls through to `None` and the caller's default. Nothing tells the user their config is not being read. |

## BENIGN — 14

These are correct as written. Listed so the inventory is complete and so a future sweep does not
re-litigate them.

| Site | Wraps | Why it is fine |
|---|---|---|
| `auth.py:155` | `os.close(descriptor)` in `finally` | Cleanup must not mask the write/replace failure. Says so in a comment. |
| `auth.py:161` | `temp_path.unlink()` in `finally` | Same. Worst case is a stray temp file. |
| `auth.py:528` | `webbrowser.open(...)` | The URL is printed immediately above; the user can paste it. |
| `auth.py:738` | `webbrowser.open(...)` | Same (xAI device flow). |
| `auth.py:868` | `webbrowser.open(...)` | Same (Codex). |
| `auth.py:1018` | `webbrowser.open(...)` | Same (Anthropic). |
| `cli.py:21` | `_s.reconfigure(...)` | Best-effort console setup at import; failure leaves the stream usable. |
| `cli.py:42` | fallback `print(*args)` inside the `safe_print` handler | Last resort. If printing fails twice there is nothing further to do. |
| `cli.py:431` | live quota fetch for display | Display-only; the surrounding credential listing still prints. |
| `ledger.py:107` | `fetch_antigravity_quota(...)` | Optional live probe with a documented fallback — the docstring says "`{}` when unreachable (seats then keep defaults)". |
| `ledger.py:177` | `float(expires)` | A narrow `(TypeError, ValueError)` guard around exactly one coercion. A malformed presence lock is treated as no lock. |
| `inspection.py:414` | `TrialEvent.from_dict(record)` | Deliberate forward-compatibility: "Validate every common envelope field even when its schema is from the future." A broad handler two lines later still returns `corrupt` for real damage. |
| `terrarium.py:823` | sandbox `rmtree` + empty-parent `rmdir` | Teardown, already containment-checked and already `ignore_errors=True`. Worst case is a stale sandbox directory. |
| `tools.py:307` | `p.read_text(...)` inside `search_files` | An unreadable or binary file is skipped in a grep whose results are capped at 50 anyway. |

---

## The question

**Which classes are allowed to stay silent, and where does everything else go?**

The 14 BENIGN sites are not the question — leave them. The question is the other 28, plus the
converting handler at `tools.py:172`.

There are three destinations, and they are not interchangeable.

**Option A — the Observer seam.** `Observer.on_event` / `on_effect` already exist in `seams.py`
and are already called on every event and every effect.

- *Cost:* it is the seam that `runtime.py:68` and `runtime.py:79` can silently switch off. Routing
  failures into the thing whose own failures are swallowed needs those two lines fixed first, or
  the destination inherits the problem.
- *Cost:* an Observer is telemetry, not a record. Sending data loss there means the loss is
  observable while it happens and still absent from the store afterward. It answers "did this
  fire" but not "what did we lose".

**Option B — the friction record.** `transition.py:216` already emits `StoreRecord(kind="friction")`
on `ModelFailed`, and the archivist already counts friction per session in `trace_axes`, so the
read path exists.

- *Cost, and it is the sharp one:* **the friction record is itself a `StoreRecord`, delivered
  through `runtime.py:90`** — the swallow at the top of the HIDES DATA LOSS table. Using it as
  the destination for store-write failures is circular: the report of a dropped write is
  delivered by the mechanism that dropped it. This works for everything *except* the store-append
  sites, and those are the largest group.
- *Cost:* friction currently means "the model failed". Widening it to mean "any swallowed
  exception" changes what the archivist's `friction` count measures, and makes historical
  per-session counts non-comparable with new ones.

**Option C — raise.** Delete the handler and let it propagate.

- *Cost:* a failed `store.append` currently cannot abort a trial. Raising means an unwritable disk
  ends a race that would otherwise have produced a usable result. For `runtime.py:90`, on the
  effect-dispatch path, that is every turn of every candidate.
- *Cost:* the discovery loops (`roles.py`, `skills.py`, `tasks.py`) currently tolerate one bad file
  in a directory. Raising means a single malformed `.md` in `~/.arity/roles/` stops Arity from
  starting. That may well be right — a role that silently becomes the Secretary is arguably worse —
  but it is a real change in failure posture and should be chosen, not inherited.
- *Benefit:* it is the only option that cannot itself be swallowed.

The three are not exclusive. The plausible shape of an answer is a split — for instance, raise on
the HIDES A BUG class because a `TypeError` is never a runtime condition, and route HIDES DATA
LOSS to a destination that does not depend on the store. But that split has to be chosen, because
each of the three has a cost that lands somewhere different: Option A costs fidelity, Option B
costs the meaning of an existing record, Option C costs availability.

Two smaller decisions ride along with the main one:

1. **`tools.py:172` specifically.** Should the tool runner convert `TypeError`/`AttributeError`
   at all, or only the exceptions a tool can legitimately raise? Converting everything is what
   makes a broken peer-consult seam look like a tool that failed normally.
2. **Narrow the type, or route the failure?** Several HIDES A BUG sites — `auth.py:245`,
   `auth.py:264`, `tools.py:464` — would stop hiding bugs simply by catching
   `(json.JSONDecodeError, OSError)` instead of `Exception`, with no new plumbing and no new
   destination. That is a cheaper answer than routing, and it is available for roughly half
   that class. It is not available for the store-append sites, which really do need a destination.

---

## Method, and a caveat on line numbers

Found by walking the AST of every `.py` file under `arity/` and selecting `ExceptHandler` nodes
whose body consists only of `pass`, `continue`, `break`, or a bare constant. That catches the
whole category by structure rather than by grep, which is why the count (42) is lower than the
audit's "~38 across ~15 files" estimate in file count and higher in site count — a text search for
`except Exception: pass` misses the multi-line and non-`Exception` spellings.

**Line numbers are from a snapshot of the working tree taken at 2026-09-01 09:36** (`HEAD` =
`cf7508f`, plus the fleet's uncommitted edits). `handlers.py`, `runtime.py`, `terrarium.py` and
`transports.py` all moved while this scan was running — `handlers.py` shifted by ~79 lines mid-pass.
Every row is therefore anchored by the wrapped call as well as the line number; if a number has
drifted, grep for the call. The classification is a property of the code, not of the line.
