//! The loop: call the model, run the tools it asks for, repeat until it answers
//! without calling a tool. Each step is decided purely from the journal, so a
//! fresh process after a crash makes the same decision the dead one would have.

use std::path::Path;

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
handed to you: where it says an agent or someone else should do it, that means you. Do it yourself and \
reply with the result. Your final message is your report, and it is all the person who \
wrote the task will see, so make it complete. Be brief.";

const MAX_STEPS: usize = 16;
/// How many times a conversation is reminded to rate before it is let through.
const MAX_NUDGES: usize = 3;

enum Step {
    CallModel,
    RunTool(ToolCall),
    Done,
}

fn next_step(chain: &[Entry]) -> Step {
    let Some(last) = chain.last() else { return Step::Done };
    if let Some(pos) = chain.iter().rposition(|e| e.kind == "assistant") {
        let answered: Vec<&str> = chain[pos + 1..]
            .iter()
            .filter(|e| e.kind == "tool")
            .filter_map(|e| e.body["call_id"].as_str())
            .collect();
        if let Some(call) = tool_calls_of(&chain[pos].body).into_iter().find(|c| !answered.contains(&c.id.as_str())) {
            return Step::RunTool(call);
        }
    }
    if last.kind == "assistant" { Step::Done } else { Step::CallModel }
}

/// A model call: system prompt, chain and tools in, the reply streamed into the
/// sink. A real model in `run`; a scripted one in tests.
pub type Complete<'a> = dyn Fn(&str, &[Entry], &[ToolSpec], &mut Sink) -> Result<Reply> + 'a;

/// Run the conversation until the model answers without calling a tool, it is
/// stopped, or it runs out of steps. `on` sees the reply's text as it arrives
/// and every message as it is written. Returns false, doing nothing, when a
/// turn in another loop holds the conversation.
pub fn run(db: &Journal, data_dir: &Path, conv: &str, spec: &str, on: &mut dyn FnMut(Event)) -> Result<bool> {
    let model = Model::parse(spec, data_dir, conv)?;
    run_with(db, conv, spec, &|system, chain, tools, out| model.complete(system, chain, tools, out), on)
}

pub fn run_with(db: &Journal, conv: &str, spec: &str, complete: &Complete, on: &mut dyn FnMut(Event)) -> Result<bool> {
    let Some(mut lease) = Lease::take(db, conv, spec)? else { return Ok(false) };
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
        if !matches!(next_step(&chain), Step::RunTool(_)) {
            if lease.live.stopped() {
                lease.release(db, true)?;
                show(&mut shown, on)?;
                return Ok(true);
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
                let mut out = |text: &str| {
                    if !text.is_empty() {
                        live.push(text);
                        on(Event::Text(text));
                    }
                    !live.stopped()
                };
                let reply = complete(&system, &crate::draft::collapse_edits(&chain), &tools::specs(worker, can_rate), &mut out)?;
                lease.commit(db, &reply.to_json(), spec)?;
                if reply.interrupted {
                    lease.release(db, true)?;
                    show(&mut shown, on)?;
                    return Ok(true);
                }
            }
            Step::RunTool(call) => {
                db.run_tool(conv, spec, &call)?;
            }
            Step::Done => match rating_nudge(db, conv, spec, &chain)? {
                Some(text) => {
                    db.append(conv, "notice", &json!({ "text": text }), None)?;
                }
                None if lease.release(db, false)? => {
                    show(&mut shown, on)?;
                    return Ok(true);
                }
                None => {}
            },
        }
    }
    bail!("stopped after {MAX_STEPS} steps")
}

/// The rating gate, for an agent that delegated on its own: its conversation
/// may not finish while a result it asked for sits unrated. After a few
/// reminders it is let through, so a model that will not rate cannot loop
/// forever; the run stays unrated for the user to rate. The user's own tasks
/// never come through here: the user rates when they like.
fn rating_nudge(db: &Journal, conv: &str, model: &str, chain: &[Entry]) -> Result<Option<String>> {
    let unrated = board::unrated_for(db.conn(), conv, model)?;
    if unrated.is_empty() {
        return Ok(None);
    }
    let since_report = chain.iter().rev().take_while(|e| e.kind != "report").filter(|e| e.kind == "notice").count();
    if since_report >= MAX_NUDGES {
        return Ok(None);
    }
    let tasks: Vec<String> = unrated.iter().map(|(t, _)| t.to_string()).collect();
    Ok(Some(format!(
        "Before you finish, rate the result of task {} with the rate tool: a score from 1 to 5 and a short note.",
        tasks.join(", ")
    )))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::board;
    use serde_json::Value;

    #[test]
    fn gate_nudges_three_times_then_lets_through() -> Result<()> {
        let path = std::env::temp_dir().join(format!("arity-gate-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path)?;
        let c = db.conn();
        let agent = |title: &str| board::TaskFields { title: title.into(), assignee: board::AGENT.into(), ..Default::default() };
        let run = |task: i64, model: &str| {
            c.execute(
                "INSERT INTO trials (task, conversation, model, ended, reported) VALUES (?1, 'task-' || ?1, ?2, datetime('now'), 1)",
                rusqlite::params![task, model],
            )
        };
        // An agent's run, task-1, delegates a task of its own; another model does it.
        run(board::add_task(c, &agent("parent"), None)?, "a")?;
        run(board::add_task(c, &agent("child"), Some("task-1"))?, "b")?;
        db.append("task-1", "report", &json!({"text": "result"}), None)?;
        for _ in 0..MAX_NUDGES {
            let nudge = rating_nudge(&db, "task-1", "a", &db.chain("task-1")?)?;
            assert!(nudge.is_some_and(|n| n.contains("task 2")));
            db.append("task-1", "notice", &json!({"text": "rate"}), None)?;
        }
        assert!(rating_nudge(&db, "task-1", "a", &db.chain("task-1")?)?.is_none(), "let through after {MAX_NUDGES}");
        board::rate(c, 2, 4, "fine", "conversations/task-1", Some("a"))?;
        db.append("task-1", "report", &json!({"text": "another"}), None)?;
        assert!(rating_nudge(&db, "task-1", "a", &db.chain("task-1")?)?.is_none(), "nothing unrated");
        // The user's own task, filed in a chat, is never the chat's to rate.
        run(board::add_task(c, &agent("mine"), Some("chat"))?, "b")?;
        assert!(rating_nudge(&db, "chat", "a", &db.chain("chat")?)?.is_none());
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    // Turns on a scripted model: no network.

    use std::sync::Arc;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering::SeqCst};
    use std::time::{Duration, Instant};

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
                run_with(&db, "c", "test:m", &model, &mut |e| {
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
                run_with(&db, "c", "test:m", &model, &mut |_| {})
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
        assert!(run_with(&db, "c", "test:m", &|_, _, _, _| Ok(says("The end.")), &mut |_| {})?);
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
                run_with(&db, "c", "test:m", &model, &mut |_| {})
            }
        });
        until("the turn to start", || turn::running(db.conn()).unwrap() == ["c"]);
        // Someone else sends meanwhile: the message waits, and no second loop runs.
        let other = Journal::open(&path)?;
        turn::send(&other, "c", "second")?;
        let ran = run_with(&other, "c", "test:m", &|_, _, _, _| panic!("a second loop called the model"), &mut |_| {})?;
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
        assert!(run_with(&db, "c", "test:m", &|_, _, _, _| Ok(says("here")), &mut |_| {})?);
        assert_eq!(kinds(&db, "c"), ["user", "assistant"]);
        assert!(turn::running(db.conn())?.is_empty());
        Ok(())
    }
}
