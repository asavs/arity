# arity

```
                 .  .  .  .
             .  '  *  *  *  '  .
          .  *  o  o  o  o  o  *  .
        .  *  o  x  x  x  x  o  *  .
       .  *  o  x  +  +  x  o  *  .
       .  *  o  x  + [1] +  x  o  *  .    r_n = c √n
       .  *  o  x  +  +  x  o  *  .      θ_n = n × 137.507764° (Golden Angle)
        .  *  o  x  x  x  x  o  *  .      Fibonacci spirals: 21, 34, 55, 89, 144
          .  *  o  o  o  o  o  *  .
             .  '  *  *  *  '  .
                 '  '  '  '
```

One moment. N kernels. The best of each.

The simplest implementation of the arity kernel that still has every part.
It is written to be read, not run. Nothing here is optimised, defended, or tested.
Each file is one noun from `docs/kernel-outline.md`, and the docstring
at the top of each file is that noun's paragraph from the outline.

One rule carries the whole design:

> everything is a name until cast, and everything is a value after.

## Reading order

Follow one message from the keyboard to the model and back. Each hop is one file.

1. **`types.py`** — the nouns as dataclasses. Spec (names), State (values), the events
   that move a moment forward, the effects a moment asks for. Read this first; every
   other file only moves these around.

2. **`paths.py`** — where things live on disk. The package holds code and seeds;
   `~/.arity` holds everything a person edits (library, staff list, seats) and
   everything the system writes (store, ledger). `ARITY_HOME` overrides it.

3. **`library.py`** — where the human-written text lives: roles, skills, tool
   definitions. A folder of markdown, keyed by name. This is the only store a
   person edits by hand.

4. **`seats.py`** — which provider seats exist and how much quota each has left.
   Keyed by seat id.

5. **`ledger.py`** — what makes a bot outlive a kernel. One append-only file per bot.
   Read at birth, written at death.

6. **`store.py`** — one JSONL file per session. Every message, model turn and tool
   result. The conversation, and the raw evidence.

7. **`scorecard.py`** — the box that turns trial results into a score. Deliberately
   a placeholder: put results in, get a ranking out. Also the tally over the store
   that cast reads.

8. **`cast.py`** and **`bots.json`** — the one function that crosses the line:
   `resolve(spec, bot) -> State`. Looks the bot up in the staff list, picks a spec,
   reads every name in it out of the stores, copies the text into a State. After
   this nothing is looked up by name again. Every kernel also gets the `message`
   tool, because every bot can message every bot.

9. **`moment.py`** — `transition(state, event) -> (state, effects)`. Pure. No I/O.
   The whole kernel is this file.

10. **`seams.py`** — the five Protocols between the owned code and the commodity code:
   Model, Tools, Store, Transport, Observer.

11. **`loop.py`** — pops an event, calls the moment, hands each effect to its seam,
    pushes the results back as events. Also the post office: a `Send` to a person
    goes out the transport, a `Send` to a bot wakes that bot's kernel and runs it
    until it answers. Keeps every woken kernel alive until `retire` performs the
    death rites.

12. **`wire_anthropic.py`, `wire_openai.py`, `wire_mock.py`** — the plugs behind the
    Model seam. One per provider: format the payload, send it, read the reply back.
    The mock answers from a script so the loop can be watched for free.

13. **`harness.py`** — where a kernel runs. Our own loop is one harness. A headless
    CLI (`claude -p`, `codex exec`, `agy`) is another, and from the moment's point
    of view it is just a different plug behind the Model seam.

14. **`trial.py`** — a trial is a fork. N copies of one State, one per spec, the same
    event into each, N results keyed by spec, handed to the scorecard.

15. **`main.py`** — the front door. `arity "text"` sends one message to reception
    and prints the reply; `arity` alone reads lines until you stop. No TUI, no flags.
    You start at reception. Ask reception for something and it delegates with the
    message tool and reports back. Start a line with `@engineer` and you are
    transferred: your lines go to the engineer until you address someone else.
    Start a line with a number, `arity 3 "text"`, and whoever you are talking to
    is forked onto the three best models with quota; the answers print side by
    side, you pick the winner, and the scorecard remembers.

16. **`demo.py`** — one moment, one bot messaging another, one three-way trial,
    all against a mock wire, so the flow can be followed without a key.

## The flow in one paragraph

A person types. The front door wraps the text in a `Message` to the bot called
"reception" and asks `cast` for that bot's State. Cast looks the bot up in the staff list,
asks the scorecard who has been winning that bot's kind of task, asks the seat table
who has quota, picks a `Spec`, and reads every name in that spec out of the library
and the ledger, copying the text into a fresh `State`. The loop hands the State and
the event to `transition`, which keeps a task record, appends the message, and returns
a `CallModel` effect: the payload. The loop gives the payload to the wire, the wire
formats it for the provider and sends it, and the reply comes back as a
`ModelCompleted` event. The loop hands that to `transition` again, which appends it,
asks for a `StoreRecord`, and then either asks for tools, or asks to `Send` a message
to another bot, or `Send`s its answer back to the person. A `Send` to a bot is the
post office's job: wake that bot's kernel, run it until it answers, hand the answer
back as the tool result. Every record lands in the session's JSONL file. When the
conversation ends, each woken kernel is retired: the loop asks it for its own report,
asks the archivist for an impartial one, and appends both to the bot's ledger. Next
time that bot is cast, it wakes with those entries. The scorecard reads the store,
counts who won, and cast reads the scorecard. That is the loop closed.
