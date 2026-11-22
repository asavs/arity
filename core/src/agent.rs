//! The loop: call the model, run the tools it asks for, repeat until it answers
//! without calling a tool. Each step is decided purely from the journal, so a
//! fresh process after a crash makes the same decision the dead one would have.
//!
//! A chat ends when the model answers without a tool. A run ends only when it
//! calls `finish`: answering without a tool hands nothing in. With sub-tasks
//! still out it waits to be woken by their results; otherwise it is reminded,
//! and after a few reminders it ends unfinished.

use std::path::Path;
use std::time::Instant;

use anyhow::{Result, bail};
use serde_json::json;

use crate::board;
use crate::journal::{Entry, Journal};
use crate::model::{Model, Reply, Sink, ToolCall, tool_calls_of};
use crate::tools::{self, ToolSpec};
use crate::turn::Lease;
use crate::{Event, messages_of};

const SYSTEM: &str = "You are an assistant inside Arity. You share a board of tasks with the user \
and other agents, and you read and change it only through your tools.

When there is a task, you have two ways to get it done:
- Do it yourself: put the finished work itself in your reply. If the task is on the board, call complete_task \
in that same reply, after the work, never before it.
- Hand it to an agent: set its assignee to \"agent\". One is picked to do it, and the user rates its result. \
A note of the result may show up here later; do not wait for it.

When the user wants a task written, draft it with propose_task: that fills in the task card they see under \
this conversation. Write the brief to whoever will do it: they see only the title and \
brief, so say everything they need. Keep changing the card as the \
user asks; they add it to the board themselves. Use add_task only when they ask you to add a task directly.

Be brief.";

const WORKER: &str = "You are an agent inside Arity, doing one task from a shared board. The task was \
handed to you: where it says an agent or someone else should do it, that means you. Do the work, then call \
finish with your report. The report is all the person who wrote the task will see, so make it complete: the \
outcome first, then what they need to use it. Ending your turn without finish hands nothing in. Be brief.";

const MAX_STEPS: usize = 24;
/// How many times a run that stops without calling finish is reminded before it ends unfinished.
const MAX_NUDGES: usize = 2;

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
        let accepted = |c: &&ToolCall| c.name == "finish" && output(&c.id).is_some_and(|o| o.starts_with(tools::FINISHED));
        if let Some(call) = calls.iter().find(accepted) {
            let args: serde_json::Value = serde_json::from_str(&call.arguments).unwrap_or_default();
            return Step::Finished(args["report"].as_str().unwrap_or_default().trim().to_string());
        }
    }
    if last.kind == "assistant" { Step::Done } else { Step::CallModel }
}

/// A model call: system prompt, chain and tools in, the reply streamed into the
/// sink. A real model in `run`; a scripted one in tests.
pub type Complete<'a> = dyn Fn(&str, &[Entry], &[ToolSpec], &mut Sink) -> Result<Reply> + 'a;

/// Run the conversation until its turn ends (see `Outcome`), it is stopped, or
/// it runs out of steps. `on` sees the reply's text as it arrives and every
/// message as it is written.
pub fn run(db: &Journal, data_dir: &Path, conv: &str, spec: &str, on: &mut dyn FnMut(Event)) -> Result<Outcome> {
    let model = Model::parse(spec, data_dir, conv)?;
    run_with(db, conv, spec, &|system, chain, tools, out| model.complete(system, chain, tools, out), on)
}

pub fn run_with(db: &Journal, conv: &str, spec: &str, complete: &Complete, on: &mut dyn FnMut(Event)) -> Result<Outcome> {
    let Some(mut lease) = Lease::take(db, conv, spec)? else { return Ok(Outcome::Busy) };
    let worker = board::trial_of_conversation(db.conn(), conv)?.is_some();
    let today: String = db.conn().query_row("SELECT date('now', 'localtime')", [], |r| r.get(0))?;
    let system = format!("{}\n\nToday is {today}.", if worker { WORKER } else { SYSTEM });
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
            if lease.live.stopped() {
                lease.release(db, true)?;
                show(&mut shown, on)?;
                return Ok(Outcome::Ended);
            }
            if lease.drain(db)? > 0 {
                chain = db.chain(conv)?;
            }
        }
        show(&mut shown, on)?;
        match next_step(&chain) {
            Step::CallModel => {
                let system = match crate::draft::current(&chain) {
                    Some(card) if !worker => format!(
                        "{system}\n\nA task card is open under this conversation. It is not on the board yet and has \
                         no id; change it only with propose_task, never with update_task. It reads:\n{}",
                        card.describe()
                    ),
                    _ => system.clone(),
                };
                let can_rate = !board::unrated_for(db.conn(), conv, spec)?.is_empty();
                let live = lease.live.clone();
                let mut clock = Stopwatch::start();
                let mut out = |text: &str| {
                    if !text.is_empty() {
                        clock.text(text);
                        live.push(text);
                        on(Event::Text(text));
                    }
                    !live.stopped()
                };
                let reply = complete(&system, &crate::draft::collapse_edits(&chain), &tools::specs(tools::caller(db.conn(), conv)?, can_rate), &mut out)?;
                lease.commit(db, &reply.to_json(), spec)?;
                // Chats only: a run's speed would hint at its model before it is rated.
                if let (false, false, Some((ttft, tps))) = (worker, reply.interrupted, clock.finish()) {
                    crate::leaderboard::record_speed(db.conn(), spec, ttft, tps)?;
                }
                if reply.interrupted {
                    lease.release(db, true)?;
                    show(&mut shown, on)?;
                    return Ok(Outcome::Ended);
                }
            }
            Step::RunTool(call) => {
                db.run_tool(conv, spec, &call)?;
            }
            Step::Finished(report) => {
                // Anything still on its way stays in the journal; the run is over.
                lease.release(db, true)?;
                show(&mut shown, on)?;
                return Ok(Outcome::Finished(report));
            }
            Step::Done if worker => {
                // Stopped without finish. Its inbox first: that may be what it needs.
                if lease.drain_all(db)? > 0 {
                    continue;
                }
                let waiting = !board::children_out(db.conn(), conv)?.is_empty();
                let reminded = chain.iter().rev().take_while(|e| !matches!(e.kind.as_str(), "user" | "report")).filter(|e| e.kind == "notice").count();
                if !waiting && reminded < MAX_NUDGES {
                    db.append(conv, "notice", &json!({ "text": unfinished(db, conv)? }), None)?;
                    continue;
                }
                if lease.release(db, false)? {
                    show(&mut shown, on)?;
                    return Ok(if waiting { Outcome::Waiting } else { Outcome::Unfinished });
                }
            }
            Step::Done => {
                if lease.release(db, false)? {
                    show(&mut shown, on)?;
                    return Ok(Outcome::Ended);
                }
            }
        }
    }
    bail!("stopped after {MAX_STEPS} steps")
}

/// The reminder for a run that stopped without calling finish.
fn unfinished(db: &Journal, conv: &str) -> Result<String> {
    let todos: Vec<String> = board::open_todos(db.conn(), conv)?.iter().map(|(id, t)| format!("{id} \"{t}\"")).collect();
    let open = match todos.len() {
        0 => String::new(),
        1 => format!(" Your todo {} is still open: do it in your reply and mark it done with complete_task.", todos[0]),
        _ => format!(" Your todos {} are still open: do them in your reply and mark each done with complete_task.", todos.join(", ")),
    };
    Ok(format!(
        "You stopped without calling finish, so nothing is handed in.{open} Write the work itself, then call finish with your report."
    ))
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
        Reply { text: text.into(), tool_calls: vec![], interrupted: false }
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
        db.chain(conv).unwrap().into_iter().map(|e| e.kind).collect()
    }

    /// A reply that calls one tool.
    pub(crate) fn calls(name: &str, args: Value) -> Reply {
        let call = ToolCall { id: format!("call-{}", rand::random::<u32>()), name: name.into(), arguments: args.to_string() };
        Reply { text: String::new(), tool_calls: vec![call], interrupted: false }
    }

    /// A run at the floor that answers its todo with the todo's id is not let
    /// off: it is reminded, finish turns it back while the todo is open, and
    /// it ends only when it has done the todo and handed in its report.
    #[test]
    fn a_run_finishes_only_with_its_todos_done() -> Result<()> {
        let db = Journal::open(&scratch("finish"))?;
        let c = db.conn();
        let agent = |title: &str| board::TaskFields { title: title.into(), assignee: board::AGENT.into(), ..Default::default() };
        board::add_task(c, &agent("Middle step"), None)?;
        c.execute("INSERT INTO trials (task, conversation, model) VALUES (1, 'task-1', 'a')", [])?;
        board::add_task(c, &agent("Bottom step"), Some("task-1"))?;
        c.execute("INSERT INTO trials (task, conversation, model) VALUES (2, 'task-2', 'b')", [])?;
        db.append("task-2", "user", &json!({ "text": "Bottom step\n\nWrite one haiku about snow." }), None)?;
        let model = |_: &str, chain: &[Entry], _: &[ToolSpec], _: &mut Sink| {
            let replies = chain.iter().filter(|e| e.kind == "assistant").count();
            Ok(match replies {
                0 => calls("add_task", json!({ "title": "Write the haiku" })),
                1 => says("3"),
                2 => calls("finish", json!({ "report": "done" })),
                3 => calls("complete_task", json!({ "id": 3 })),
                _ => calls("finish", json!({ "report": "Snow on the cedar" })),
            })
        };
        assert_eq!(run_with(&db, "task-2", "b", &model, &mut |_| {})?, Outcome::Finished("Snow on the cedar".into()));
        let chain = db.chain("task-2")?;
        let notice = chain.iter().find(|e| e.kind == "notice").expect("reminded");
        assert!(notice.body["text"].as_str().unwrap().contains("3 \"Write the haiku\""), "{}", notice.body);
        let refused = chain.iter().filter(|e| e.kind == "tool" && e.body["name"] == "finish").next().unwrap();
        assert!(refused.body["output"].as_str().unwrap().contains("todo 3"), "{}", refused.body);
        assert_eq!(board::task(c, 3)?.state, board::TaskState::Done);
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
        assert_eq!(asked.load(SeqCst), 1 + MAX_NUDGES);
        // A chat is not a run: it ends when it answers.
        db.append("chat", "user", &json!({ "text": "hi" }), None)?;
        assert_eq!(run_with(&db, "chat", "a", &|_, _, _, _| Ok(says("hello")), &mut |_| {})?, Outcome::Ended);
        Ok(())
    }

    /// While a turn runs, a steer joins between its replies; the inbox only when it would end.
    #[test]
    fn a_steer_joins_at_once_and_the_inbox_when_the_turn_would_end() -> Result<()> {
        let db = Journal::open(&scratch("deliver"))?;
        let lease = Lease::take(&db, "c", "m")?.unwrap();
        let said = |text: &str| json!({ "text": text });
        assert!(!turn::deliver(db.conn(), "c", "report", &said("later"), turn::Delivery::Inbox)?);
        assert!(!turn::deliver(db.conn(), "c", "report", &said("now"), turn::Delivery::Steer)?);
        assert!(turn::queued(db.conn(), "c")?.is_empty(), "reports are not the user's messages");
        assert_eq!(lease.drain(&db)?, 1);
        assert_eq!(db.chain("c")?.iter().map(|e| e.body["text"].as_str().unwrap()).collect::<Vec<_>>(), ["now"]);
        assert_eq!(lease.drain_all(&db)?, 1);
        assert_eq!(kinds(&db, "c"), ["report", "report"]);
        drop(lease);
        assert!(turn::deliver(db.conn(), "c", "report", &said("idle"), turn::Delivery::Inbox)?, "no turn: straight in");
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
        assert_eq!(db.chain("c")?[1].body["text"], "Hello world");
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
                    Ok(Reply { text: "Once upon".into(), tool_calls: vec![], interrupted: true })
                };
                turn_on(&db, "c", "test:m", &model, &mut |_| {})
            }
        });
        until("the reply to start", || turn::partial(db.conn(), "c").unwrap().is_some());
        assert!(turn::stop(db.conn(), "c")?, "a turn was running");
        assert!(turn.join().unwrap()?);
        let chain = db.chain("c")?;
        assert_eq!(kinds(&db, "c"), ["user", "assistant"]);
        assert_eq!(chain[1].body["text"], "Once upon");
        assert_eq!(chain[1].body["interrupted"], true);
        assert!(tool_calls_of(&chain[1].body).is_empty());
        assert!(messages_of(&chain[1]).iter().any(|m| m.text == "Stopped."));
        assert!(!turn::stop(db.conn(), "c")?, "nothing left running");

        // The next message is answered as usual, and both wire formats read the chain cleanly.
        turn::send(&db, "c", "go on")?;
        assert!(turn_on(&db, "c", "test:m", &|_, _, _, _| Ok(says("The end.")), &mut |_| {})?);
        assert_eq!(kinds(&db, "c"), ["user", "assistant", "user", "assistant"]);
        let roles: Vec<Value> = crate::model::chat_messages("s", &db.chain("c")?).iter().map(|m| m["role"].clone()).collect();
        assert_eq!(roles, ["system", "user", "assistant", "user", "assistant"]);
        assert!(crate::model::codex_input(&db.chain("c")?).iter().all(|i| i["type"] != "function_call"));
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
                    let asked = chain.last().unwrap().body["text"].as_str().unwrap().to_string();
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
        let texts: Vec<String> = db.chain("c")?.iter().map(|e| e.body["text"].as_str().unwrap().to_string()).collect();
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
}
