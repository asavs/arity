//! The loop: call the model, run the tools it asks for, repeat until it answers
//! without calling a tool. Each step is decided purely from the journal, so a
//! fresh process after a crash makes the same decision the dead one would have.
//!
//! Tools and lifecycle hooks live behind `Harness` (`ToolRegistry` + `HookRegistry`).
//! The leading system prompt stays immutable for prompt caching; dynamic state
//! (date, attached folder, worker follow-up status, open task card) is appended
//! as `"system"` delta entries only when changed.

use std::path::Path;
use std::time::Instant;

use anyhow::{Result, bail};
use serde_json::json;

use crate::board;
use crate::compact;
use crate::hooks::{BeforeTool, HookCtx, HookRegistry, YieldAction};
use crate::journal::{Entry, Journal};
use crate::model::{Model, Reply, Sink, ToolCall, tool_calls_of};
use crate::prompt::{self, PromptState};
use crate::tools::{self, ToolHookDecision, ToolRegistry, ToolSpec, ToolVisibility};
use crate::turn::Lease;
use crate::{Event, messages_of};

const MAX_STEPS: usize = 24;

/// The runtime harness: registered tools and write-capable lifecycle hooks.
pub struct Harness {
    pub tools: ToolRegistry,
    pub hooks: HookRegistry,
    /// Compact before a model call once the latest prompt reached this many tokens. None: only on overflow.
    pub auto_compact_tokens: Option<u64>,
}

impl Default for Harness {
    fn default() -> Self {
        Self {
            tools: ToolRegistry::default(),
            hooks: HookRegistry::default(),
            auto_compact_tokens: Some(crate::config::DEFAULT_AUTO_COMPACT_TOKENS),
        }
    }
}

impl Harness {
    /// Build the default harness plus any enabled user-configured hooks from `data_dir/config.json`.
    pub fn for_data_dir(data_dir: &Path) -> Self {
        Self {
            tools: ToolRegistry::default(),
            hooks: HookRegistry::for_data_dir(data_dir),
            auto_compact_tokens: crate::config::load(data_dir)
                .map(|c| c.auto_compact_tokens)
                .unwrap_or(Some(crate::config::DEFAULT_AUTO_COMPACT_TOKENS)),
        }
    }
}

/// How a call to `run` ended.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Outcome {
    /// Another loop holds the conversation; nothing was done.
    Busy,
    /// A chat's turn ended, or someone stopped the turn.
    Ended,
    /// A run called finish; this is its report.
    Finished(String),
    /// A run stopped with sub-tasks out, to be woken by their results.
    Waiting,
    /// A run stopped without finishing, even after being reminded.
    Unfinished,
}

enum Step {
    CallModel,
    RunTool(ToolCall),
    Done,
    /// The run called finish and it was accepted; this is the report.
    Finished(String),
}

fn next_step(chain: &[Entry]) -> Step {
    let Some(last) = chain.last() else { return Step::Done };
    if let Some(pos) = chain.iter().rposition(|e| e.kind == "assistant") {
        let results: Vec<&Entry> = chain[pos + 1..].iter().filter(|e| e.kind == "tool").collect();
        let output = |id: &str| results.iter().find(|e| e.body["call_id"] == id).and_then(|e| e.body["output"].as_str());
        let calls = tool_calls_of(&chain[pos].body);
        if let Some(call) = calls.iter().find(|c| output(&c.id).is_none()) {
            return Step::RunTool(call.clone());
        }
        // Only for the reply that just finished; a later message starts a new turn.
        if chain[pos + 1..].iter().all(|e| matches!(e.kind.as_str(), "tool" | "tool_intent" | "system")) {
            let accepted = |c: &&ToolCall| c.name == "finish" && output(&c.id).is_some_and(|o| o.starts_with(tools::FINISHED));
            if let Some(call) = calls.iter().find(accepted) {
                let args: serde_json::Value = serde_json::from_str(&call.arguments).unwrap_or_default();
                return Step::Finished(args["report"].as_str().unwrap_or_default().trim().to_string());
            }
        }
    }
    // A compact made part way through a turn carries on; one asked for between turns, or a reset, waits for the user.
    let marker_waits = last.kind == "reset" || (last.kind == "compact" && last.body["continue"] != true);
    if matches!(last.kind.as_str(), "assistant" | "rating") || marker_waits { Step::Done } else { Step::CallModel }
}

/// A model call: system prompt, chain and tools in, the reply streamed into the
/// sink. A real model in `run`; a scripted one in tests.
pub type Complete<'a> = dyn Fn(&str, &[Entry], &[ToolSpec], &mut Sink) -> Result<Reply> + 'a;

/// Run the conversation until its turn ends (see `Outcome`), it is stopped, or
/// it runs out of steps. `on` sees the reply's text as it arrives and every
/// message as it is written.
pub fn run(db: &Journal, data_dir: &Path, conv: &str, spec: &str, on: &mut dyn FnMut(Event)) -> Result<Outcome> {
    let session_id = crate::journal::session_id(db.conn(), conv)?;
    let model = Model::parse(spec, data_dir, &session_id)?;
    let harness = Harness::for_data_dir(data_dir);
    run_with_harness(
        db,
        conv,
        spec,
        &harness,
        &|system, chain, tools, out| model.complete(system, chain, tools, out),
        on,
    )
}

pub fn run_with(db: &Journal, conv: &str, spec: &str, complete: &Complete, on: &mut dyn FnMut(Event)) -> Result<Outcome> {
    run_with_harness(db, conv, spec, &Harness::default(), complete, on)
}

pub fn run_with_harness(
    db: &Journal,
    conv: &str,
    spec: &str,
    harness: &Harness,
    complete: &Complete,
    on: &mut dyn FnMut(Event),
) -> Result<Outcome> {
    let Some(mut lease) = Lease::take(db, conv, spec)? else { return Ok(Outcome::Busy) };
    let trial = board::trial_state(db.conn(), conv)?;
    let worker = trial.is_some();
    let in_run = trial.as_ref().is_some_and(|(.., ended)| !*ended);
    let system = prompt::leading_system(worker);
    let today: String = db.conn().query_row("SELECT date('now', 'localtime')", [], |r| r.get(0))?;
    // Entries up to here were written before this turn; later ones are shown as they come.
    let mut shown = db.chain(conv)?.last().map_or(0, |e| e.id);
    let show = |shown: &mut i64, on: &mut dyn FnMut(Event)| -> Result<()> {
        let from = *shown;
        for e in db.chain(conv)?.iter().filter(|e| e.id > from) {
            messages_of(e).iter().for_each(|m| on(Event::Message(m)));
            *shown = e.id;
        }
        Ok(())
    };
    for _ in 0..MAX_STEPS {
        let mut chain = db.chain(conv)?;
        // Between replies, never between a reply's tool calls: stop, or take what was sent meanwhile.
        if matches!(next_step(&chain), Step::CallModel | Step::Done) {
            if lease.live.cut_for_steer() {
                lease.go_on(db)?;
            } else if lease.live.stopped() {
                lease.release(db, true)?;
                show(&mut shown, on)?;
                return Ok(Outcome::Ended);
            }
            lease.drain(db)?;
            chain = db.chain(conv)?;
        }
        show(&mut shown, on)?;
        match next_step(&chain) {
            Step::CallModel => {
                let folder = board::folder_of(db.conn(), conv)?;
                let state = PromptState {
                    worker,
                    in_run,
                    today: today.clone(),
                    folder: folder.clone(),
                    draft_card: (!worker).then(|| crate::draft::current(&chain).map(|c| c.describe())).flatten(),
                };
                prompt::sync(db, conv, &state)?;
                let mut view = db.chain_for_model(conv)?;
                let live = lease.live.clone();
                // Only a real stop cuts a summary short; a steer waits for it and joins after.
                let stopped = || live.stopped() && !live.cut_for_steer();
                let compact_now = || -> Result<Vec<Entry>> {
                    compact::compact(db, conv, complete, None, true, &stopped)?;
                    prompt::sync(db, conv, &state)?;
                    db.chain_for_model(conv)
                };
                if harness.auto_compact_tokens.is_some_and(|limit| compact::last_prompt_tokens(&view) >= limit) {
                    // Past the threshold but not yet too long: a failed compact leaves the context as it was.
                    if let Ok(compacted) = compact_now() {
                        view = compacted;
                    }
                }
                let vis = ToolVisibility { caller: tools::caller(db.conn(), conv)?, folder };
                let specs = harness.tools.specs(&vis);
                let mut clock = Stopwatch::start();
                let mut out = |text: &str| {
                    if !text.is_empty() {
                        clock.text(text);
                        live.push(text);
                        on(Event::Text(text));
                    }
                    !live.stopped()
                };
                let reply = match complete(system, &view, &specs, &mut out) {
                    // Too long for the model: compact, then try once more.
                    Err(e) if compact::is_context_overflow(&e) => {
                        view = compact_now().map_err(|ce| e.context(format!("the context was too long, and compacting it failed: {ce:#}")))?;
                        complete(system, &view, &specs, &mut out)?
                    }
                    r => r?,
                };
                let mut body = reply.to_json();
                if reply.interrupted && lease.live.cut_for_steer() {
                    body["cut_for_message"] = json!(true);
                }
                lease.commit(db, &body, spec)?;
                // Chats only: a run's speed would hint at its model before it is rated.
                if let (false, false, Some((ttft, tps))) = (worker, reply.interrupted, clock.finish()) {
                    crate::leaderboard::record_speed(db.conn(), spec, ttft, tps)?;
                }
                // Cut for a steer: the top of the loop takes it in and goes on.
                if reply.interrupted && !lease.live.cut_for_steer() {
                    lease.release(db, true)?;
                    show(&mut shown, on)?;
                    return Ok(Outcome::Ended);
                }
            }
            Step::RunTool(call) => {
                harness.tools.run_with_hooks(
                    db,
                    conv,
                    spec,
                    &call,
                    |call, args| {
                        let mut ctx = HookCtx { db, conv, model: spec, system, complete, live: &lease.live, in_run };
                        Ok(match harness.hooks.run_before_tool(&mut ctx, call, args.clone())? {
                            BeforeTool::Continue => ToolHookDecision::Continue,
                            BeforeTool::RewriteArgs(v) => ToolHookDecision::RewriteArgs(v),
                            BeforeTool::Block(reason) => ToolHookDecision::Block(reason),
                        })
                    },
                    |call, out| {
                        let mut ctx = HookCtx { db, conv, model: spec, system, complete, live: &lease.live, in_run };
                        harness.hooks.run_after_tool(&mut ctx, call, &mut out.text, &mut out.is_error)
                    },
                )?;
            }
            Step::Finished(report) => {
                let mut ctx = HookCtx { db, conv, model: spec, system, complete, live: &lease.live, in_run };
                harness.hooks.run_after_turn(&mut ctx)?;
                // Anything still on its way stays in the journal; the run is over.
                lease.release(db, true)?;
                show(&mut shown, on)?;
                return Ok(Outcome::Finished(report));
            }
            Step::Done => {
                // Drain queued follow-ups before asking on_yield hooks or ending the turn.
                if lease.drain_all(db)? > 0 {
                    continue;
                }
                chain = db.chain(conv)?;
                let mut ctx = HookCtx { db, conv, model: spec, system, complete, live: &lease.live, in_run };
                if let YieldAction::Continue(text) = harness.hooks.run_on_yield(&mut ctx, &chain)? {
                    db.append(conv, "notice", &json!({ "text": text }), None)?;
                    continue;
                }
                if in_run {
                    let waiting = !board::children_out(db.conn(), conv)?.is_empty();
                    if waiting {
                        harness.hooks.run_after_turn(&mut ctx)?;
                    }
                    if lease.release(db, false)? {
                        show(&mut shown, on)?;
                        return Ok(if waiting { Outcome::Waiting } else { Outcome::Unfinished });
                    }
                } else {
                    harness.hooks.run_after_turn(&mut ctx)?;
                    if lease.release(db, false)? {
                        show(&mut shown, on)?;
                        return Ok(Outcome::Ended);
                    }
                }
            }
        }
    }
    bail!("stopped after {MAX_STEPS} steps")
}

/// Times a streamed reply: how long until its first text, and how fast the rest came.
struct Stopwatch {
    start: Instant,
    first: Option<Instant>,
    chars: usize,
}

impl Stopwatch {
    fn start() -> Self {
        Stopwatch { start: Instant::now(), first: None, chars: 0 }
    }

    fn text(&mut self, text: &str) {
        self.first.get_or_insert_with(Instant::now);
        self.chars += text.chars().count();
    }

    /// Time to first token in milliseconds, and output tokens per second. None
    /// for a reply with no text (only tool calls); no speed for one too short to
    /// time. Tokens are estimated at four characters each, since streams do not
    /// count them.
    fn finish(&self) -> Option<(f64, Option<f64>)> {
        let first = self.first?;
        let ttft = first.duration_since(self.start).as_secs_f64() * 1000.0;
        let rest = first.elapsed().as_secs_f64();
        let tps = (rest >= 0.25 && self.chars >= 40).then(|| self.chars as f64 / 4.0 / rest);
        Some((ttft, tps))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::board;
    use serde_json::Value;

    // Turns on a scripted model: no network.

    /// A turn on a scripted model; false if another loop held the conversation.
    fn turn_on(db: &Journal, conv: &str, spec: &str, complete: &Complete, on: &mut dyn FnMut(Event)) -> Result<bool> {
        run_with(db, conv, spec, complete, on).map(|o| o != Outcome::Busy)
    }

    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::SeqCst};
    use std::time::{Duration, Instant};

    use crate::model::ToolCall;
    use crate::turn;

    fn scratch(name: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("arity-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir.join("arity.sqlite")
    }

    fn says(text: &str) -> Reply {
        Reply { text: text.into(), tool_calls: vec![], interrupted: false, usage: None }
    }

    /// Waits until `f` holds, or fails after a few seconds.
    fn until(what: &str, mut f: impl FnMut() -> bool) {
        let start = Instant::now();
        while !f() {
            assert!(start.elapsed() < Duration::from_secs(5), "timed out waiting for {what}");
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    fn kinds(db: &Journal, conv: &str) -> Vec<String> {
        db.chain(conv).unwrap().into_iter().filter(|e| e.kind != "system").map(|e| e.kind).collect()
    }

    fn last_content(chain: &[Entry]) -> &Entry {
        chain.iter().rev().find(|e| e.kind != "system").unwrap()
    }

    /// A reply that calls one tool.
    pub(crate) fn calls(name: &str, args: Value) -> Reply {
        let call = ToolCall { id: format!("call-{}", rand::random::<u32>()), name: name.into(), arguments: args.to_string() };
        Reply { text: String::new(), tool_calls: vec![call], interrupted: false, usage: None }
    }

    /// After a chat integrates a child's report in its reply, the post-use
    /// rating hook fires automatically without `rate` ever entering the toolset.
    #[test]
    fn a_chat_rates_a_child_via_post_use_hook() -> Result<()> {
        let db = Journal::open(&scratch("hook"))?;
        let c = db.conn();
        db.append("chat-1", "user", &json!({ "text": "write a haiku" }), None)?;
        db.append("chat-1", "assistant", &json!({ "text": "Handed off." }), Some("chat:m"))?;
        board::add_task(c, &board::TaskFields { title: "Snow haiku".into(), assignee: board::AGENT.into(), ..Default::default() }, Some("chat-1"))?;
        c.execute("INSERT INTO trials (task, conversation, model, ended, result, reported) VALUES (1, 'task-1', 'worker:w', datetime('now'), 'Snow on the cedar', 1)", [])?;
        board::set_state(c, 1, board::TaskState::InGates)?;
        db.append("chat-1", "report", &json!({ "text": "Task 1 done: Snow on the cedar", "task": 1, "trial": 1 }), None)?;
        let model = |_: &str, chain: &[Entry], tools: &[ToolSpec], _: &mut Sink| {
            let last = last_content(chain);
            if last.body["hook"] == "rate" {
                assert_eq!(tools.iter().map(|t| t.name).collect::<Vec<_>>(), [crate::hooks::SUBMIT_RATING]);
                return Ok(calls(crate::hooks::SUBMIT_RATING, json!({ "score": 5, "note": "crisp imagery" })));
            }
            assert!(!tools.iter().any(|t| t.name == "rate"), "rate is never in the normal toolset");
            Ok(says("Here is your haiku: Snow on the cedar."))
        };
        assert_eq!(run_with(&db, "chat-1", "chat:m", &model, &mut |_| {})?, Outcome::Ended);
        let t = board::task(c, 1)?;
        assert_eq!((t.state, t.score, t.note.as_deref()), (board::TaskState::Done, Some(5), Some("crisp imagery")));
        assert_eq!(kinds(&db, "chat-1"), ["user", "assistant", "report", "assistant", "rating"]);
        Ok(())
    }

    /// A run that never calls finish is reminded twice, then ends unfinished.
    #[test]
    fn a_run_that_never_finishes_ends_unfinished() -> Result<()> {
        let db = Journal::open(&scratch("unfinished"))?;
        let c = db.conn();
        board::add_task(c, &board::TaskFields { title: "t".into(), assignee: board::AGENT.into(), ..Default::default() }, None)?;
        c.execute("INSERT INTO trials (task, conversation, model) VALUES (1, 'task-1', 'a')", [])?;
        db.append("task-1", "user", &json!({ "text": "t" }), None)?;
        let asked = AtomicUsize::new(0);
        let model = |_: &str, _: &[Entry], _: &[ToolSpec], _: &mut Sink| {
            asked.fetch_add(1, SeqCst);
            Ok(says("ok"))
        };
        assert_eq!(run_with(&db, "task-1", "a", &model, &mut |_| {})?, Outcome::Unfinished);
        assert_eq!(asked.load(SeqCst), 1 + crate::hooks::MAX_NUDGES);
        // A chat is not a run: it ends when it answers.
        db.append("chat", "user", &json!({ "text": "hi" }), None)?;
        assert_eq!(run_with(&db, "chat", "a", &|_, _, _, _| Ok(says("hello")), &mut |_| {})?, Outcome::Ended);
        Ok(())
    }

    /// While a turn runs, steer and inbox entries join between its replies,
    /// after-turn ones only when it would end; an inbox entry never keeps a turn going.
    #[test]
    fn each_timing_joins_a_running_turn_where_it_says() -> Result<()> {
        let db = Journal::open(&scratch("timing"))?;
        let lease = Lease::take(&db, "c", "m")?.unwrap();
        let said = |text: &str| json!({ "text": text });
        assert!(!turn::deliver(db.conn(), "c", "report", &said("later"), turn::Delivery::AfterTurn)?);
        assert!(!turn::deliver(db.conn(), "c", "report", &said("now"), turn::Delivery::Steer)?);
        assert!(!turn::deliver(db.conn(), "c", "report", &said("fyi"), turn::Delivery::Inbox)?);
        assert!(turn::queued(db.conn(), "c")?.is_empty(), "reports are not the user's messages");
        // An inbox note to a turn that would end does not keep it going.
        let mut quiet = Lease::take(&db, "q", "m")?.unwrap();
        turn::deliver(db.conn(), "q", "report", &said("note"), turn::Delivery::Inbox)?;
        assert!(quiet.release(&db, false)?, "ended");
        assert_eq!(kinds(&db, "q"), ["report"], "and the note is in the journal");
        assert_eq!(lease.drain(&db)?, 1, "the inbox one joins too, but asks for no answer");
        assert_eq!(db.chain("c")?.iter().map(|e| e.body["text"].as_str().unwrap()).collect::<Vec<_>>(), ["now", "fyi"]);
        assert_eq!(lease.drain_all(&db)?, 1);
        assert_eq!(kinds(&db, "c"), ["report", "report", "report"]);
        drop(lease);
        assert!(turn::deliver(db.conn(), "c", "report", &said("idle"), turn::Delivery::AfterTurn)?, "no turn: straight in");
        Ok(())
    }

    #[test]
    fn a_streamed_reply_shows_as_it_comes_and_ends_as_one_entry() -> Result<()> {
        let path = scratch("stream");
        let db = Journal::open(&path)?;
        turn::send(&db, "c", "hi")?;
        let turn = std::thread::spawn({
            let path = path.clone();
            move || -> Result<Vec<String>> {
                let db = Journal::open(&path)?;
                let mut pieces = vec![];
                let model = |_: &str, _: &[Entry], _: &[ToolSpec], out: &mut Sink| {
                    for piece in ["Hel", "lo ", "world"] {
                        out(piece);
                        std::thread::sleep(Duration::from_millis(400));
                    }
                    Ok(says("Hello world"))
                };
                turn_on(&db, "c", "test:m", &model, &mut |e| {
                    if let Event::Text(t) = e {
                        pieces.push(t.to_string())
                    }
                })?;
                Ok(pieces)
            }
        });
        // Another connection, as another process would, sees the reply grow.
        let mut seen = vec![];
        until("the turn to end", || {
            if let Some((text, _)) = turn::partial(db.conn(), "c").unwrap() {
                if seen.last() != Some(&text) {
                    seen.push(text);
                }
            }
            turn.is_finished()
        });
        assert_eq!(turn.join().unwrap()?, ["Hel", "lo ", "world"]);
        assert!(seen.len() >= 2 && seen.last().unwrap().starts_with("Hello"), "saw {seen:?}");
        assert_eq!(kinds(&db, "c"), ["user", "assistant"]);
        assert_eq!(db.chain("c")?.iter().find(|e| e.kind == "assistant").unwrap().body["text"], "Hello world");
        assert!(turn::partial(db.conn(), "c")?.is_none() && turn::running(db.conn())?.is_empty());
        // The reply was timed: its first text came about immediately, and it was too short for a speed.
        let (ttft, tps): (f64, Option<f64>) = db.conn().query_row("SELECT ttft_ms, tps FROM speeds WHERE model = 'test:m'", [], |r| Ok((r.get(0)?, r.get(1)?)))?;
        assert!(ttft < 400.0 && tps.is_none(), "{ttft} {tps:?}");
        Ok(())
    }

    #[test]
    fn a_stop_mid_reply_keeps_the_text_and_the_conversation_goes_on() -> Result<()> {
        let path = scratch("stop");
        let db = Journal::open(&path)?;
        turn::send(&db, "c", "write a long story")?;
        let turn = std::thread::spawn({
            let path = path.clone();
            move || -> Result<bool> {
                let db = Journal::open(&path)?;
                // Writes a little, then a tool call starts arriving; it never finishes before the stop.
                let model = |_: &str, _: &[Entry], _: &[ToolSpec], out: &mut Sink| {
                    out("Once upon");
                    while out("") {
                        std::thread::sleep(Duration::from_millis(20));
                    }
                    Ok(Reply { text: "Once upon".into(), tool_calls: vec![], interrupted: true, usage: None })
                };
                turn_on(&db, "c", "test:m", &model, &mut |_| {})
            }
        });
        until("the reply to start", || turn::partial(db.conn(), "c").unwrap().is_some());
        assert!(turn::stop(db.conn(), "c")?, "a turn was running");
        assert!(turn.join().unwrap()?);
        let chain = db.chain("c")?;
        let first_reply = chain.iter().find(|e| e.kind == "assistant").unwrap();
        assert_eq!(kinds(&db, "c"), ["user", "assistant"]);
        assert_eq!(first_reply.body["text"], "Once upon");
        assert_eq!(first_reply.body["interrupted"], true);
        assert!(tool_calls_of(&first_reply.body).is_empty());
        assert!(messages_of(first_reply).iter().any(|m| m.text == "Stopped."));
        assert!(!turn::stop(db.conn(), "c")?, "nothing left running");

        // The next message is answered as usual, and both wire formats read the chain cleanly.
        turn::send(&db, "c", "go on")?;
        assert!(turn_on(&db, "c", "test:m", &|_, _, _, _| Ok(says("The end.")), &mut |_| {})?);
        assert_eq!(kinds(&db, "c"), ["user", "assistant", "user", "assistant"]);
        let roles: Vec<Value> = crate::model::chat_messages("s", &db.chain("c")?).iter().map(|m| m["role"].clone()).collect();
        assert_eq!(roles, ["system", "user", "system", "assistant", "user", "assistant"]);
        assert!(crate::model::codex_input(&db.chain("c")?).iter().all(|i| i["type"] != "function_call"));
        Ok(())
    }

    /// A steer cuts the reply being written; the turn keeps its
    /// text, takes the message in, and goes on, holding its lease throughout.
    #[test]
    fn a_steer_cuts_the_reply_and_the_turn_goes_on() -> Result<()> {
        let path = scratch("now");
        let db = Journal::open(&path)?;
        turn::send(&db, "c", "write a long story")?;
        let turn = std::thread::spawn({
            let path = path.clone();
            move || -> Result<Outcome> {
                let db = Journal::open(&path)?;
                let model = |_: &str, chain: &[Entry], _: &[ToolSpec], out: &mut Sink| {
                    if last_content(chain).body["text"] == "make it short" {
                        return Ok(says("Short."));
                    }
                    out("Once upon");
                    while out("") {
                        std::thread::sleep(Duration::from_millis(20));
                    }
                    Ok(Reply { text: "Once upon".into(), tool_calls: vec![], interrupted: true, usage: None })
                };
                run_with(&db, "c", "test:m", &model, &mut |_| {})
            }
        });
        until("the reply to start", || turn::partial(db.conn(), "c").unwrap().is_some());
        turn::send_as(&db, "c", "make it short", turn::Delivery::Steer)?;
        assert_eq!(turn.join().unwrap()?, Outcome::Ended);
        assert_eq!(kinds(&db, "c"), ["user", "assistant", "user", "assistant"]);
        let chain = db.chain("c")?;
        let replies: Vec<&Entry> = chain.iter().filter(|e| e.kind == "assistant").collect();
        assert_eq!((replies[0].body["text"].clone(), replies[0].body["cut_for_message"].clone()), (json!("Once upon"), json!(true)));
        assert_eq!(replies[1].body["text"], "Short.");
        assert!(messages_of(replies[0]).iter().any(|m| m.text == "Cut off by a new message."));
        assert!(turn::running(db.conn())?.is_empty());
        
        Ok(())
    }

    #[test]
    fn a_second_sender_queues_instead_of_starting_a_second_loop() -> Result<()> {
        let path = scratch("queue");
        let db = Journal::open(&path)?;
        let go = Arc::new(AtomicBool::new(false));
        let calls = Arc::new(AtomicUsize::new(0));
        turn::send(&db, "c", "first")?;
        let turn = std::thread::spawn({
            let (path, go, calls) = (path.clone(), go.clone(), calls.clone());
            move || -> Result<bool> {
                let db = Journal::open(&path)?;
                let model = |_: &str, chain: &[Entry], _: &[ToolSpec], _: &mut Sink| {
                    while !go.load(SeqCst) {
                        std::thread::sleep(Duration::from_millis(20));
                    }
                    calls.fetch_add(1, SeqCst);
                    let asked = last_content(chain).body["text"].as_str().unwrap().to_string();
                    Ok(says(&format!("re: {asked}")))
                };
                turn_on(&db, "c", "test:m", &model, &mut |_| {})
            }
        });
        until("the turn to start", || turn::running(db.conn()).unwrap() == ["c"]);
        // Someone else sends meanwhile: the message waits, and no second loop runs.
        let other = Journal::open(&path)?;
        turn::send(&other, "c", "second")?;
        let ran = turn_on(&other, "c", "test:m", &|_, _, _, _| panic!("a second loop called the model"), &mut |_| {})?;
        assert!(!ran);
        assert_eq!(turn::queued(db.conn(), "c")?.len(), 1);
        go.store(true, SeqCst);
        assert!(turn.join().unwrap()?);
        let texts: Vec<String> = db
            .chain("c")?
            .iter()
            .filter(|e| e.kind != "system")
            .map(|e| e.body["text"].as_str().unwrap().to_string())
            .collect();
        assert_eq!(texts, ["first", "re: first", "second", "re: second"]);
        assert_eq!(calls.load(SeqCst), 2);
        assert!(turn::queued(db.conn(), "c")?.is_empty() && turn::running(db.conn())?.is_empty());
        Ok(())
    }

    #[test]
    fn a_lease_whose_process_died_is_taken_over() -> Result<()> {
        let db = Journal::open(&scratch("lease"))?;
        // A turn whose process died: its lease ran out a moment ago, and nothing renews it.
        db.conn().execute(
            "INSERT INTO turns (conversation, owner, lease_until, partial) VALUES ('c', 'dead', ?1, 'half a')",
            [board::now() - 1],
        )?;
        assert!(turn::running(db.conn())?.is_empty());
        turn::send(&db, "c", "hello?")?;
        assert!(turn::queued(db.conn(), "c")?.is_empty(), "goes straight into the journal");
        assert!(turn_on(&db, "c", "test:m", &|_, _, _, _| Ok(says("here")), &mut |_| {})?);
        assert_eq!(kinds(&db, "c"), ["user", "assistant"]);
        assert!(turn::running(db.conn())?.is_empty());
        Ok(())
    }

    fn says_with(text: &str, input: u64, cached: u64) -> Reply {
        let usage = crate::model::TokenUsage { input, cached, output: 7, total: input + 7 };
        Reply { usage: Some(usage), ..says(text) }
    }

    /// A conversation with two answered exchanges whose replies report usage, never compacted.
    fn two_exchanges(db: &Journal, conv: &str, input: u64) -> Result<()> {
        let harness = Harness { auto_compact_tokens: None, ..Harness::default() };
        for (i, text) in ["first", "second"].iter().enumerate() {
            turn::send(db, conv, text)?;
            let reply = format!("re: {text}");
            run_with_harness(db, conv, "test:m", &harness, &|_, _, _, _| Ok(says_with(&reply, input + i as u64, input / 2)), &mut |_| {})?;
        }
        Ok(())
    }

    #[test]
    fn replies_keep_their_token_usage_in_the_journal() -> Result<()> {
        let db = Journal::open(&scratch("usage"))?;
        two_exchanges(&db, "c", 1000)?;
        let replies: Vec<Entry> = db.chain("c")?.into_iter().filter(|e| e.kind == "assistant").collect();
        assert_eq!(replies[1].body["usage"], json!({ "input": 1001, "cached": 500, "output": 7, "total": 1008 }));
        turn::send(&db, "c", "third")?;
        run_with(&db, "c", "test:m", &|_, _, _, _| Ok(says("no usage")), &mut |_| {})?;
        assert!(db.chain("c")?.last().unwrap().body.get("usage").is_none(), "None writes nothing");
        Ok(())
    }

    /// A model whose context fits only once compacted: the turn compacts, retries, and answers.
    #[test]
    fn a_context_length_error_compacts_and_retries_once() -> Result<()> {
        let db = Journal::open(&scratch("overflow"))?;
        two_exchanges(&db, "c", 1000)?;
        turn::send(&db, "c", "third")?;
        let views = std::cell::RefCell::new(Vec::<Vec<String>>::new());
        let model = |system: &str, chain: &[Entry], tools: &[ToolSpec], _: &mut Sink| {
            if system == compact::SUMMARIZER_SYSTEM {
                assert!(tools.is_empty());
                let asked = chain[0].body["text"].as_str().unwrap();
                assert!(asked.contains("User: first") && asked.contains("Assistant: re: second") && asked.contains("User: third"));
                return Ok(says("## Goal\nAnswer three messages."));
            }
            views.borrow_mut().push(chain.iter().map(|e| e.kind.clone()).collect());
            if chain[0].kind != "compact" {
                bail!("400 Bad Request: {{\"error\":{{\"code\":\"context_length_exceeded\",\"message\":\"This model's maximum context length is 8192 tokens\"}}}}");
            }
            Ok(says("re: third"))
        };
        assert_eq!(run_with(&db, "c", "test:m", &model, &mut |_| {})?, Outcome::Ended);
        let views = views.borrow();
        assert_eq!(views.len(), 2, "one failed call, one retry");
        assert_eq!(views[1][..2], ["compact", "system"], "the summary, then the sections sent again");
        assert_eq!(kinds(&db, "c"), ["user", "assistant", "user", "assistant", "user", "compact", "assistant"]);
        let compact = db.chain("c")?.into_iter().find(|e| e.kind == "compact").unwrap();
        assert_eq!((compact.body["continue"].clone(), compact.body["compacted_count"].clone()), (json!(true), json!(5)));
        assert!(messages_of(&compact)[0].text.starts_with("Context compacted automatically"));

        // A model that still overflows after compacting fails the turn with both errors, not a loop.
        turn::send(&db, "c", "fourth")?;
        let always = |system: &str, _: &[Entry], _: &[ToolSpec], _: &mut Sink| {
            if system == compact::SUMMARIZER_SYSTEM {
                return Ok(says("summary"));
            }
            bail!("maximum context length exceeded")
        };
        let err = run_with(&db, "c", "test:m", &always, &mut |_| {}).unwrap_err();
        assert!(format!("{err:#}").contains("maximum context length"), "{err:#}");
        Ok(())
    }

    #[test]
    fn a_prompt_past_the_threshold_is_compacted_before_the_call() -> Result<()> {
        let db = Journal::open(&scratch("threshold"))?;
        two_exchanges(&db, "c", 130_000)?;
        turn::send(&db, "c", "third")?;
        let first_seen = std::cell::RefCell::new(None::<String>);
        let model = |system: &str, chain: &[Entry], _: &[ToolSpec], _: &mut Sink| {
            if system == compact::SUMMARIZER_SYSTEM {
                return Ok(says("## Goal\nKeep going."));
            }
            first_seen.borrow_mut().get_or_insert(chain[0].kind.clone());
            Ok(says_with("re: third", 900, 0))
        };
        assert_eq!(run_with(&db, "c", "test:m", &model, &mut |_| {})?, Outcome::Ended);
        assert_eq!(first_seen.borrow().as_deref(), Some("compact"), "the model never saw the long context");
        assert_eq!(compact::last_prompt_tokens(&db.chain_for_model("c")?), 900);

        // Below the threshold, or with it turned off, nothing is compacted.
        let db = Journal::open(&scratch("threshold-off"))?;
        two_exchanges(&db, "c", 130_000)?;
        turn::send(&db, "c", "third")?;
        let harness = Harness { auto_compact_tokens: None, ..Harness::default() };
        let plain = |system: &str, _: &[Entry], _: &[ToolSpec], _: &mut Sink| {
            assert_ne!(system, compact::SUMMARIZER_SYSTEM);
            Ok(says("re: third"))
        };
        run_with_harness(&db, "c", "test:m", &harness, &plain, &mut |_| {})?;
        assert!(!kinds(&db, "c").contains(&"compact".to_string()));
        Ok(())
    }

    /// After a compact asked for between turns, the turn ends; a reset does too. Neither calls the model.
    #[test]
    fn a_marker_at_the_head_waits_for_the_next_message() -> Result<()> {
        let db = Journal::open(&scratch("marker-waits"))?;
        two_exchanges(&db, "c", 10)?;
        for (kind, body) in [("compact", json!({ "summary": "s", "compacted_count": 4 })), ("reset", json!({ "note": null }))] {
            db.append("c", kind, &body, None)?;
            assert!(matches!(next_step(&db.chain("c")?), Step::Done), "{kind}");
            let outcome = run_with(&db, "c", "test:m", &|_, _, _, _| panic!("nothing to answer"), &mut |_| {})?;
            assert_eq!(outcome, Outcome::Ended);
        }
        db.append("c", "compact", &json!({ "summary": "s", "continue": true }), None)?;
        assert!(matches!(next_step(&db.chain("c")?), Step::CallModel), "a compact made mid-turn carries on");
        Ok(())
    }

    /// End-to-end harness test: custom write-capable hooks (`before_tool`,
    /// `after_tool`, `on_yield`) run inside the real loop, and the leading
    /// system prompt stays byte-identical while a task card opens and changes.
    #[test]
    fn harness_hooks_and_cache_safe_prefix_work_end_to_end() -> Result<()> {
        struct AuditAndVerifyHook;
        impl crate::hooks::Hook for AuditAndVerifyHook {
            fn name(&self) -> &'static str {
                "audit_verify"
            }
            fn before_tool(&self, ctx: &mut HookCtx, call: &ToolCall, args: &Value) -> Result<BeforeTool> {
                if call.name == "propose_task" {
                    ctx.db.append(ctx.conv, "audit", &json!({ "stage": "before", "title": args["title"] }), None)?;
                    let mut rewritten = args.clone();
                    rewritten["priority"] = json!("high");
                    return Ok(BeforeTool::RewriteArgs(rewritten));
                }
                Ok(BeforeTool::Continue)
            }
            fn after_tool(&self, _ctx: &mut HookCtx, call: &ToolCall, output: &mut String, _is_error: &mut bool) -> Result<()> {
                if call.name == "propose_task" {
                    output.push_str("\n[audited]");
                }
                Ok(())
            }
            fn on_yield(&self, _ctx: &mut HookCtx, chain: &[Entry]) -> Result<YieldAction> {
                let verified = chain.iter().any(|e| e.kind == "notice" && e.body["text"] == "Please confirm.");
                if !verified {
                    return Ok(YieldAction::Continue("Please confirm.".into()));
                }
                Ok(YieldAction::Accept)
            }
        }

        let db = Journal::open(&scratch("harness-e2e"))?;
        turn::send(&db, "chat-1", "draft a task")?;
        let mut harness = Harness::default();
        harness.hooks.register(Box::new(AuditAndVerifyHook));

        let seen_systems = std::cell::RefCell::new(Vec::<String>::new());
        let seen_prefix_len = std::cell::RefCell::new(Vec::<usize>::new());
        let model = |system: &str, chain: &[Entry], _: &[ToolSpec], _: &mut Sink| {
            seen_systems.borrow_mut().push(system.to_string());
            seen_prefix_len.borrow_mut().push(chain.len());
            let last = last_content(chain);
            Ok(match last.kind.as_str() {
                "user" => calls("propose_task", json!({ "title": "Ship harness" })),
                "tool" => {
                    assert!(last.body["output"].as_str().unwrap().contains("[audited]"));
                    says("Drafted.")
                }
                "notice" => says("Confirmed."),
                other => panic!("unexpected {other}"),
            })
        };

        assert_eq!(run_with_harness(&db, "chat-1", "test:m", &harness, &model, &mut |_| {})?, Outcome::Ended);

        let systems = seen_systems.borrow();
        assert_eq!(systems.len(), 3);
        assert_eq!(systems[0], prompt::CHAT_SYSTEM);
        assert_eq!(systems[1], systems[0], "leading system prompt stays byte-identical after card opens");
        assert_eq!(systems[2], systems[0], "leading system prompt stays byte-identical on on_yield retry");

        let card = crate::draft::current(&db.chain("chat-1")?).expect("card is open");
        assert_eq!(card.priority, board::Priority::High, "before_tool rewrote args");
        assert!(db.chain("chat-1")?.iter().any(|e| e.kind == "audit" && e.body["title"] == "Ship harness"));
        // Card delta was emitted as a trailing "system" entry rather than mutating the prefix.
        let sys_entries: Vec<Entry> = db.chain("chat-1")?.into_iter().filter(|e| e.kind == "system").collect();
        assert_eq!(sys_entries.len(), 2, "initial date delta + card-opened delta only");
        assert!(sys_entries[1].body["text"].as_str().unwrap().contains("Ship harness"));
        Ok(())
    }
}
