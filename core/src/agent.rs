//! The loop: call the model, run the tools it asks for, repeat until it answers
//! without calling a tool. Each step is decided purely from the journal, so a
//! fresh process after a crash makes the same decision the dead one would have.

use std::path::Path;

use anyhow::{Result, bail};
use serde_json::json;

use crate::board;
use crate::journal::{Entry, Journal};
use crate::model::{Model, ToolCall, tool_calls_of};
use crate::{Message, messages_of, tools};

const SYSTEM: &str = "You are an assistant inside Arity. You share a board of tasks with the user \
and other agents, and you read and change it only through your tools.

When there is a task, you have two ways to get it done:
- Do it yourself: put the finished work itself in your reply. If the task is on the board, call complete_task \
in that same reply, after the work, never before it.
- Hand it to an agent: set its assignee to \"agent\". One is picked to do it, and its report comes back to \
you here. Rate the report honestly with the rate tool before you answer.

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

/// `on` sees every message as it is written.
pub fn run(db: &Journal, data_dir: &Path, conv: &str, spec: &str, on: &mut dyn FnMut(&Message)) -> Result<()> {
    let model = Model::parse(spec, data_dir)?;
    let worker = board::trial_of_conversation(db.conn(), conv)?.is_some();
    let today: String = db.conn().query_row("SELECT date('now', 'localtime')", [], |r| r.get(0))?;
    let system = format!("{}\n\nToday is {today}.", if worker { WORKER } else { SYSTEM });
    for _ in 0..MAX_STEPS {
        let chain = db.chain(conv)?;
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
                let reply = model.complete(&system, &crate::draft::collapse_edits(&chain), &tools::specs(worker))?;
                db.append(conv, "assistant", &reply.to_json(), Some(spec))?;
            }
            Step::RunTool(call) => {
                db.run_tool(conv, &call)?;
            }
            Step::Done => match rating_nudge(db, conv, &chain)? {
                Some(text) => {
                    db.append(conv, "notice", &json!({ "text": text }), None)?;
                }
                None => return Ok(()),
            },
        }
        if let Some(e) = db.chain(conv)?.last() {
            messages_of(e).iter().for_each(&mut *on);
        }
    }
    bail!("stopped after {MAX_STEPS} steps")
}

/// The rating gate: a conversation may not finish while a result it asked for
/// sits unrated. After a few reminders it is let through, so a model that will
/// not rate cannot loop forever; the run stays unrated for the user to rate.
fn rating_nudge(db: &Journal, conv: &str, chain: &[Entry]) -> Result<Option<String>> {
    let unrated = board::unrated_for(db.conn(), conv)?;
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

    #[test]
    fn gate_nudges_three_times_then_lets_through() -> Result<()> {
        let path = std::env::temp_dir().join(format!("arity-gate-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path)?;
        db.append("sec", "user", &json!({"text": "delegate"}), None)?;
        let f = board::TaskFields { title: "t".into(), assignee: board::AGENT.into(), ..Default::default() };
        let task = board::add_task(db.conn(), &f, &board::conversation_page("sec"))?;
        db.conn().execute(
            "INSERT INTO trials (task, conversation, model, ended, reported) VALUES (?1, 'task-1', 'm', datetime('now'), 1)",
            rusqlite::params![task],
        )?;
        db.append("sec", "report", &json!({"text": "result"}), None)?;
        for _ in 0..MAX_NUDGES {
            let nudge = rating_nudge(&db, "sec", &db.chain("sec")?)?;
            assert!(nudge.is_some_and(|n| n.contains("task 1")));
            db.append("sec", "notice", &json!({"text": "rate"}), None)?;
        }
        assert!(rating_nudge(&db, "sec", &db.chain("sec")?)?.is_none(), "let through after {MAX_NUDGES}");
        board::rate(db.conn(), 1, 4, "fine", "sec")?;
        db.append("sec", "report", &json!({"text": "another"}), None)?;
        assert!(rating_nudge(&db, "sec", &db.chain("sec")?)?.is_none(), "nothing unrated");
        let _ = std::fs::remove_file(&path);
        Ok(())
    }
}
