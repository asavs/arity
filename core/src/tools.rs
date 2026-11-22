//! Board tools. They run inside the journal's transaction, so a tool's effect
//! and the record of it commit together. Errors become the tool's output, so
//! the model can see them and recover.

use rusqlite::Connection;
use serde_json::{Value, json};

use crate::board::{self, AGENT, RunState, TaskFields, TaskState};
use crate::{draft, turn};

pub struct ToolSpec {
    pub name: &'static str,
    pub description: &'static str,
    pub parameters: Value,
}

/// The fields `add_task`, `update_task` and `propose_task` all take.
fn field_schema() -> Value {
    json!({
        "title": {"type": "string", "description": "Short, starts with a verb."},
        "brief": {"type": "string", "description": "Everything whoever does it needs to know to do it well."},
        "assignee": {"type": "string", "description": "\"agent\" hands it to an agent: one is picked to do it, and the \
            user rates its result. \"user\" puts it on the user's own list."},
        "choice": {"type": "string", "enum": ["smart", "fast", "free", "private"], "description": "Which kind of model \
            an agent should be: \"smart\" the best available (the default), \"fast\" the quickest, \"free\" one that costs \
            nothing extra, \"private\" only models on the user's computer, for private work."},
        "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
        "due": {"type": "string", "description": "A date, YYYY-MM-DD."},
        "blocked_by": {"type": "array", "items": {"type": "integer"}, "description": "Ids of tasks that must be done first."},
        "tags": {"type": "array", "items": {"type": "string"}}
    })
}

/// `add_task` for a run at the floor: no assignee and no choice, since what it
/// writes is its own todo.
fn todo_schema() -> Value {
    let mut props = field_schema();
    let obj = props.as_object_mut().unwrap();
    obj.remove("assignee");
    obj.remove("choice");
    obj.insert("brief".into(), json!({"type": "string", "description": "Notes for yourself."}));
    props
}

/// Who is calling the tools, which decides which tools they get.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Caller {
    /// A conversation the user talks in.
    Chat,
    /// A run above the floor: it may hand work to an agent, one level down.
    Run,
    /// A run at the floor (`board::MAX_DEPTH`): what it writes is its own todo.
    RunAtFloor,
}

pub fn caller(db: &Connection, conv: &str) -> anyhow::Result<Caller> {
    Ok(match board::run_depth(db, conv)? {
        None => Caller::Chat,
        Some(d) if d >= board::MAX_DEPTH => Caller::RunAtFloor,
        Some(_) => Caller::Run,
    })
}

/// Which tools a caller is offered. `rate` is offered only to a conversation
/// with results of its own waiting to be rated.
fn offered(caller: Caller, name: &str, can_rate: bool) -> bool {
    match (caller, name) {
        (_, "rate") => can_rate,
        (_, "list_tasks" | "add_task") => true,
        (Caller::Chat, "finish") => false,
        (Caller::Chat, _) => true,
        (_, "finish" | "message") => true,
        (Caller::RunAtFloor, "complete_task") => true,
        _ => false,
    }
}

/// What `finish` says when it accepts a report. The loop ends the run on it.
pub const FINISHED: &str = "finished: your report is handed in";
/// How many times `finish` turns a run back before it lets it through anyway,
/// so a model that cannot clear what is open does not loop forever.
const MAX_REFUSALS: usize = 3;

fn with_id(mut props: Value) -> Value {
    props["id"] = json!({"type": "integer"});
    props
}

/// A run gets the same `add_task` a chat uses, so a subagent is a task one level
/// down. At the floor it gets `add_task` without an assignee or choice: a task it
/// writes is its own todo, which it does and ticks off with `complete_task`.
/// Runs cannot change other tasks.
pub fn specs(caller: Caller, can_rate: bool) -> Vec<ToolSpec> {
    let all = vec![
        ToolSpec {
            name: "propose_task",
            description: "Fill in the task card shown to the user in this conversation, or change the one that is open. \
                          Fields you leave out keep their value. The user reviews the card and adds it to the board \
                          themselves, so do not also call add_task for it.",
            parameters: json!({"type": "object", "properties": field_schema()}),
        },
        ToolSpec {
            name: "add_task",
            description: "Put a task straight on the shared board, without the card. Use this only when the user asks \
                          you to add it directly. With assignee \"agent\", an agent does it and the user rates the result.",
            parameters: json!({"type": "object", "properties": field_schema(), "required": ["title"]}),
        },
        ToolSpec {
            name: "update_task",
            description: "Change a task that is already on the board, by its id. Fields you leave out keep their \
                          value. Setting assignee to \"agent\" hands a task that has not started to an agent. Not for \
                          the task card: change that with propose_task.",
            parameters: json!({"type": "object", "properties": with_id(field_schema()), "required": ["id"]}),
        },
        ToolSpec {
            name: "list_tasks",
            description: "List the tasks on the shared board that are not done, with their ids, states and fields.",
            parameters: json!({"type": "object", "properties": {
                "include_done": {"type": "boolean", "description": "Also list finished tasks."}
            }}),
        },
        ToolSpec {
            name: "complete_task",
            description: "Mark a task on the board as done, by id. Do this after doing a task yourself in your reply.",
            parameters: json!({"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
        },
        ToolSpec {
            name: "rate",
            description: "Rate the result of a task you handed to an agent: a score from 1 (useless) \
                          to 5 (excellent) and a short note saying why.",
            parameters: json!({"type": "object", "properties": {
                "task": {"type": "integer"},
                "score": {"type": "integer", "minimum": 1, "maximum": 5},
                "note": {"type": "string"}
            }, "required": ["task", "score", "note"]}),
        },
        ToolSpec {
            name: "finish",
            description: "End your task and hand in your report. The report is all whoever wrote the task will see: \
                          start with the outcome, then what they need to use it. If you could not do it, say what \
                          you found and what stopped you.",
            parameters: json!({"type": "object", "properties": {
                "report": {"type": "string", "description": "The result of the task, in full."},
                "outcome": {"type": "string", "enum": ["done", "blocked", "needs_review"], "description": "\"done\" \
                    (the default) when the task is done; \"blocked\" when you cannot go on, saying in the report what \
                    you need; \"needs_review\" when it is done but someone should check it before it is used."}
            }, "required": ["report"]}),
        },
        ToolSpec {
            name: "message",
            description: "Tell someone something before you finish, without ending your task: whoever handed you \
                          the task, or the agent doing a task you handed out. Use it when they are about to waste \
                          work, or need to know now; the rest goes in your report.",
            parameters: json!({"type": "object", "properties": {
                "to": {"type": ["string", "integer"], "description": "\"parent\" for whoever handed you this task, \
                    or the id of a task you handed out."},
                "text": {"type": "string"},
                "when": {"type": "string", "enum": ["now", "next_step", "after_turn", "inbox"], "description": "When it \
                    reaches them if they are working: \"now\" cuts off what they are writing, \"next_step\" when \
                    they finish their current step, \"after_turn\" (the default) when they would stop, \"inbox\" \
                    for something they only need to know, read when they are next ready and never waking them."}
            }, "required": ["to", "text"]}),
        },
    ];
    all.into_iter()
        .filter(|t| offered(caller, t.name, can_rate))
        .map(|t| match (caller, t.name) {
            (Caller::Run, "add_task") => {
                let mut props = field_schema();
                props["assignee"] = json!({"type": "string", "description": "Leave it out to hand the task to an agent. \
                    \"user\" puts it on the user's own list instead."});
                props["when_done"] = json!({"type": "string", "enum": ["after_turn", "next_step", "now"],
                    "description": "When its result reaches you if you are still working: \"after_turn\" (the default) when you next \
                    stop, \"next_step\" when you finish the step you are on, \"now\" at once, cutting off what you \
                    are writing. If you have stopped to wait, \"next_step\" and \"now\" wake you for each result, \
                    \"after_turn\" once all your sub-tasks have reported."});
                ToolSpec {
                    name: "add_task",
                    description: "Hand part of your task to another agent, one level down. It knows nothing but the \
                                  title and brief, so say the goal, what its report should contain, and where to stop. \
                                  Its result comes back to you.",
                    parameters: json!({"type": "object", "properties": props, "required": ["title"]}),
                }
            }
            (Caller::RunAtFloor, "add_task") => ToolSpec {
                name: "add_task",
                description: "Write a todo for yourself on the board. It is yours: do it in your reply, then mark it \
                              done with complete_task. Nobody else picks it up.",
                parameters: json!({"type": "object", "properties": todo_schema(), "required": ["title"]}),
            },
            (Caller::RunAtFloor, "complete_task") => ToolSpec {
                name: "complete_task",
                description: "Mark one of your own todos done, by id, after doing it.",
                parameters: t.parameters,
            },
            _ => t,
        })
        .collect()
}

const HANDED_OFF: &str = "An agent will do it and the user will rate its result. \
                          Do not wait or check on it: finish your reply now.";
const HANDED_OFF_BY_A_RUN: &str = "An agent will do it, knowing only its title and brief. Its result comes back \
                                   here. Carry on with the rest meanwhile, or end your turn without calling finish to \
                                   wait: you are woken when it reports. Call finish once you have what you need.";

/// `conv` is the conversation calling the tool, and `model` the model it is running on.
pub fn execute(db: &Connection, conv: &str, model: &str, name: &str, arguments: &str) -> String {
    let args: Value = serde_json::from_str(arguments).unwrap_or(json!({}));
    let result = (|| -> anyhow::Result<String> {
        let caller = caller(db, conv)?;
        let worker = caller != Caller::Chat;
        if !offered(caller, name, true) {
            if !worker {
                anyhow::bail!("unknown tool {name}");
            }
            anyhow::bail!("you are doing a task: you can read the board and file tasks, not change others");
        }
        match name {
            "propose_task" => {
                let f = draft::propose(db, conv, &args)?;
                Ok(format!(
                    "The card now reads:\n{}\nIt is not on the board yet: the user can edit it and add it. \
                     Change it again with propose_task.",
                    f.describe()
                ))
            }
            "add_task" if caller == Caller::RunAtFloor => {
                // Its own todo: whatever it says about who or which model, it is the run's.
                let mut args = args.clone();
                if let Some(obj) = args.as_object_mut() {
                    obj.remove("assignee");
                    obj.remove("choice");
                    obj.remove("clearance");
                }
                let mut f = TaskFields { assignee: board::conversation_page(conv), ..Default::default() };
                f.merge(&args)?;
                let id = board::add_task(db, &f, Some(conv))?;
                Ok(format!(
                    "added todo {id} for yourself. The id is not an answer: write the work itself in your reply, \
                     mark it done with complete_task, then call finish with your report."
                ))
            }
            "complete_task" if caller == Caller::RunAtFloor => {
                let id = args["id"].as_i64().ok_or_else(|| anyhow::anyhow!("missing id"))?;
                if board::task(db, id)?.fields.assignee != board::conversation_page(conv) {
                    anyhow::bail!("task {id} is not one of your todos");
                }
                board::set_done(db, id, true)?;
                Ok(format!("completed todo {id}"))
            }
            "add_task" => {
                // A run's add_task hands work on: left out, the assignee is an agent, not the user.
                let mut f = TaskFields { assignee: if worker { AGENT.into() } else { TaskFields::default().assignee }, ..Default::default() };
                f.merge(&args)?;
                let id = board::add_task(db, &f, Some(conv))?;
                let when = args["when_done"].as_str().and_then(turn::Delivery::parse);
                if let (Caller::Run, Some(when @ (turn::Delivery::Now | turn::Delivery::NextStep | turn::Delivery::AfterTurn))) = (caller, when) {
                    db.execute("UPDATE tasks SET report_when = ?2 WHERE id = ?1", rusqlite::params![id, when.name()])?;
                }
                Ok(match (f.assignee == AGENT, worker) {
                    (true, false) => format!("added task {id}. {HANDED_OFF}"),
                    (true, true) => format!("added task {id}. {HANDED_OFF_BY_A_RUN}"),
                    _ => format!("added task {id}"),
                })
            }
            "update_task" => {
                let id = args["id"].as_i64().ok_or_else(|| anyhow::anyhow!("missing id"))?;
                let old = board::task(db, id)?;
                let mut f = old.fields.clone();
                f.merge(&args)?;
                board::update_task(db, id, &f)?;
                let handed_off = f.assignee == AGENT && old.fields.assignee != AGENT;
                Ok(if handed_off { format!("updated task {id}. {HANDED_OFF}") } else { format!("updated task {id}") })
            }
            "list_tasks" => {
                let all = args["include_done"] == true;
                let rows: Vec<String> = board::tasks(db)?.iter().filter(|t| all || t.state != TaskState::Done).map(line).collect();
                Ok(match (rows.is_empty(), all) {
                    (true, true) => "the board is empty".into(),
                    (true, false) => "no open tasks; include_done lists finished ones".into(),
                    _ => rows.join("\n"),
                })
            }
            "complete_task" => {
                let id = args["id"].as_i64().ok_or_else(|| anyhow::anyhow!("missing id"))?;
                board::set_done(db, id, true)?;
                Ok(format!("completed task {id}"))
            }
            "rate" => {
                let task = args["task"].as_i64().ok_or_else(|| anyhow::anyhow!("missing task"))?;
                let score = args["score"].as_i64().ok_or_else(|| anyhow::anyhow!("missing score"))?;
                let note = args["note"].as_str().unwrap_or_default();
                let (_, trial) = board::unrated_for(db, conv, model)?
                    .into_iter()
                    .find(|(t, _)| *t == task)
                    .ok_or_else(|| anyhow::anyhow!("task {task} has no result of yours waiting for a rating"))?;
                board::rate(db, trial, score, note, &board::conversation_page(conv), Some(model))?;
                Ok(format!("rated task {task}: {score}/5"))
            }
            "message" => {
                let text = args["text"].as_str().unwrap_or_default().trim();
                if text.is_empty() {
                    anyhow::bail!("message needs its text");
                }
                let how = match args["when"].as_str() {
                    None => turn::Delivery::AfterTurn,
                    Some(w) => turn::Delivery::parse(w)
                        .ok_or_else(|| anyhow::anyhow!("when is one of now, next_step, after_turn and inbox"))?,
                };
                let (mine, title): (i64, String) = db.query_row(
                    "SELECT t.id, t.title FROM trials tr JOIN tasks t ON t.id = tr.task WHERE tr.conversation = ?1",
                    [conv],
                    |r| Ok((r.get(0)?, r.get(1)?)),
                )?;
                let child = match &args["to"] {
                    Value::String(s) if s == "parent" => None,
                    Value::Number(n) => n.as_i64(),
                    Value::String(s) => s.trim().trim_start_matches("task").trim().parse().ok(),
                    _ => None,
                };
                let (to, how, said, sent) = match child {
                    Some(task) => {
                        let Some(run) = board::child_run(db, conv, task)? else {
                            anyhow::bail!("nobody is working on task {task} now");
                        };
                        (run, how, format!("Message from whoever handed you this task (task {mine}):\n\n{text}"), format!("task {task}"))
                    }
                    None if args["to"] != "parent" => anyhow::bail!("to is \"parent\" or the id of a task you handed out"),
                    None => match board::parent(db, conv)? {
                        board::Parent::Run(run) => {
                            (run, how, format!("Message from task {mine}, \"{title}\":\n\n{text}"), "whoever handed you this task".into())
                        }
                        // The user reads it in the chat; the chat's model is not asked to answer it.
                        board::Parent::Chat(chat) => (
                            chat,
                            turn::Delivery::Inbox,
                            format!("Message from task {mine}, \"{title}\":\n\n{text}"),
                            "the user, in the chat they filed it in".into(),
                        ),
                        board::Parent::Unreachable => anyhow::bail!(
                            "whoever handed you this task cannot be reached now; say it in your report when you finish"
                        ),
                    },
                };
                turn::deliver(db, &to, "message", &json!({ "text": said, "from_task": mine }), how)?;
                if how != turn::Delivery::Inbox {
                    board::wake(db, &to)?;
                }
                Ok(format!("sent to {sent}"))
            }
            "finish" => {
                let report = args["report"].as_str().unwrap_or_default().trim();
                if report.is_empty() {
                    anyhow::bail!("finish needs your report: the result of the task, in full");
                }
                let outcome = args["outcome"].as_str().unwrap_or("done");
                if !matches!(outcome, "done" | "blocked" | "needs_review") {
                    anyhow::bail!("outcome is one of done, blocked and needs_review");
                }
                let todos = board::open_todos(db, conv)?;
                // Refusals since the last report it was given; a wake starts the count again.
                let refused = crate::journal::chain(db, conv)?
                    .iter()
                    .rev()
                    .take_while(|e| e.kind != "report")
                    .filter(|e| e.kind == "tool" && e.body["name"] == "finish")
                    .count();
                // Blocked: it cannot go on, so what is open is not a reason to keep it.
                if refused < MAX_REFUSALS && outcome != "blocked" {
                    let ids = |v: &[i64]| v.iter().map(|i| i.to_string()).collect::<Vec<_>>().join(", ");
                    let mut open = Vec::new();
                    if !todos.is_empty() {
                        let list: Vec<String> = todos.iter().map(|(id, title)| format!("{id} \"{title}\"")).collect();
                        open.push(format!(
                            "your todo {} is still open: do it in your reply and mark it done with complete_task",
                            list.join(", ")
                        ));
                    }
                    let out = board::children_out(db, conv)?;
                    if !out.is_empty() {
                        open.push(format!(
                            "task {} has not reported yet: end your turn without calling finish to wait for it, \
                             and you are woken with its result",
                            ids(&out)
                        ));
                    }
                    let unrated: Vec<i64> = board::unrated_for(db, conv, model)?.iter().map(|(t, _)| *t).collect();
                    if !unrated.is_empty() {
                        open.push(format!("rate the result of task {} with the rate tool first", ids(&unrated)));
                    }
                    if !open.is_empty() {
                        anyhow::bail!("not finished: {}. Then call finish again.", open.join("; "));
                    }
                }
                // Let through anyway: its todos die with it.
                for (id, _) in &todos {
                    board::set_done(db, *id, true)?;
                }
                db.execute("UPDATE trials SET outcome = ?2 WHERE conversation = ?1 AND ended IS NULL", rusqlite::params![conv, outcome])?;
                Ok(FINISHED.into())
            }
            other => anyhow::bail!("unknown tool {other}"),
        }
    })();
    result.unwrap_or_else(|e| format!("error: {e:#}"))
}

/// One task as a line a model can read.
fn line(t: &board::TaskRow) -> String {
    let f = &t.fields;
    let state = match t.state {
        TaskState::Open => "open, waiting on blockers",
        TaskState::Ready => "ready",
        TaskState::Assigned => "assigned",
        TaskState::InGates => "in gates",
        TaskState::Done => "done",
    };
    let who = if f.assignee == board::me() { "the user" } else { &f.assignee };
    let mut parts = vec![format!("{} {} [{state}] for {who}", t.id, f.title)];
    if let Some(run) = t.run_state {
        parts.push(
            match run {
                RunState::Running => "an agent is on it",
                RunState::Waiting => "an agent is waiting on its sub-tasks",
                RunState::Failing => "an agent is retrying it",
                RunState::NeedsRating => "finished, needs rating",
                RunState::Rated => "finished and rated",
            }
            .into(),
        );
    }
    if f.priority != board::Priority::Normal {
        parts.push(format!("{} priority", f.priority.name()));
    }
    if f.choice != board::Choice::Smart {
        parts.push(format!("for a {} model", f.choice.name()));
    }
    if let Some(d) = &f.due {
        parts.push(format!("due {d}"));
    }
    if !f.blocked_by.is_empty() {
        let ids: Vec<String> = f.blocked_by.iter().map(|i| i.to_string()).collect();
        parts.push(format!("blocked by {}", ids.join(", ")));
    }
    if !f.tags.is_empty() {
        parts.push(format!("tags {}", f.tags.join(", ")));
    }
    let line = parts.join("; ");
    if f.brief.is_empty() { line } else { format!("{line}\n    brief: {}", f.brief.replace('\n', "\n    ")) }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A run files a task with the same call a chat uses, one level down. At the
    /// floor it is not offered delegation: what it writes is its own todo.
    #[test]
    fn a_run_at_the_floor_writes_its_own_todos() -> anyhow::Result<()> {
        let path = std::env::temp_dir().join(format!("arity-depth-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = crate::journal::Journal::open(&path)?;
        let c = db.conn();
        let run = |task: i64| {
            c.execute("INSERT INTO trials (task, conversation, model) VALUES (?1, 'task-' || ?1, 'lab:m')", rusqlite::params![task])
        };
        let call = |conv: &str, tool: &str, args: Value| execute(c, conv, "lab:m", tool, &args.to_string());
        let names = |conv: &str| -> Vec<&'static str> { specs(caller(c, conv).unwrap(), false).iter().map(|t| t.name).collect() };
        let add_props = |conv: &str| specs(caller(c, conv).unwrap(), false).into_iter().find(|t| t.name == "add_task").unwrap().parameters;
        // A chat files task 1; its run (level 1) hands task 2 to an agent with the same call.
        assert!(call("chat", "add_task", json!({ "title": "top", "assignee": "agent" })).starts_with("added task 1"));
        run(1)?;
        assert_eq!(names("task-1"), ["add_task", "list_tasks", "finish", "message"]);
        assert!(add_props("task-1")["properties"]["assignee"].is_object(), "above the floor it may delegate");
        let said = call("task-1", "add_task", json!({ "title": "sub" }));
        assert!(said.starts_with("added task 2") && said.contains("comes back here"), "{said}");
        assert_eq!(board::task(c, 2)?.created_by, "conversations/task-1");
        // Task 2's run is at the floor: add_task has no assignee or choice, and complete_task is offered.
        run(2)?;
        assert_eq!(names("task-2"), ["add_task", "list_tasks", "complete_task", "finish", "message"]);
        let props = add_props("task-2");
        assert!(props["properties"]["assignee"].is_null() && props["properties"]["choice"].is_null(), "{props}");
        // Even asked for an agent, what it writes is its own todo: no pick, no run, nothing woken.
        let said = call("task-2", "add_task", json!({ "title": "check the sums", "assignee": "agent", "choice": "smart" }));
        assert!(said.starts_with("added todo 3"), "{said}");
        let todo = board::task(c, 3)?;
        assert_eq!((todo.fields.assignee.as_str(), todo.created_by.as_str()), ("conversations/task-2", "conversations/task-2"));
        // The dispatcher only takes ready tasks whose assignee is an agent; a todo never is.
        let pickable: i64 = c.query_row("SELECT count(*) FROM tasks WHERE id = 3 AND assignee = 'agent'", [], |r| r.get(0))?;
        assert_eq!(pickable, 0);
        let runs_of_todo: i64 = c.query_row("SELECT count(*) FROM trials WHERE task = 3", [], |r| r.get(0))?;
        assert_eq!(runs_of_todo, 0, "a todo is never a run");
        assert!(board::unrated_for(c, "task-2", "x")?.is_empty());
        // It ticks off its own todo, and only its own.
        assert_eq!(call("task-2", "complete_task", json!({ "id": 3 })), "completed todo 3");
        assert!(call("task-2", "complete_task", json!({ "id": 1 })).contains("not one of your todos"));
        assert!(call("task-1", "complete_task", json!({ "id": 1 })).starts_with("error:"), "above the floor runs change nothing");
        // A private run's tasks stay private, whatever it asks for.
        c.execute("UPDATE tasks SET choice = 'private' WHERE id = 1", [])?;
        assert!(call("task-1", "add_task", json!({ "title": "another", "assignee": "agent", "choice": "smart" })).starts_with("added task 4"));
        assert_eq!(board::task(c, 4)?.fields.choice, board::Choice::Private);
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    /// A run messages whoever handed it its task, or the run of a task it
    /// handed out, at a timing; a waiting run is woken unless it is inbox. The
    /// user is reached in the chat the task was filed in, as a note. Blocked
    /// finishes even with work open.
    #[test]
    fn runs_message_up_and_down_and_can_finish_blocked() -> anyhow::Result<()> {
        let path = std::env::temp_dir().join(format!("arity-message-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = crate::journal::Journal::open(&path)?;
        let c = db.conn();
        let call = |conv: &str, tool: &str, args: Value| execute(c, conv, "lab:m", tool, &args.to_string());
        // The user files task 1 in a chat; its run hands task 2 to an agent, and task 2's run starts.
        call("chat", "add_task", json!({ "title": "top", "assignee": "agent" }));
        c.execute("INSERT INTO trials (task, conversation, model, waiting) VALUES (1, 'task-1', 'a', 1)", [])?;
        assert!(call("task-1", "message", json!({ "to": 2, "text": "hi" })).contains("not one you handed out"));
        call("task-1", "add_task", json!({ "title": "sub" }));
        assert!(call("task-1", "message", json!({ "to": 2, "text": "hi" })).contains("nobody is working on task 2"));
        c.execute("INSERT INTO trials (task, conversation, model) VALUES (2, 'task-2', 'b')", [])?;
        // Down: the parent tells its child something.
        assert_eq!(call("task-1", "message", json!({ "to": "task 2", "text": "use metric" })), "sent to task 2");
        let got = db.chain("task-2")?.last().unwrap().clone();
        assert_eq!(got.kind, "message");
        assert!(got.body["text"].as_str().unwrap().contains("use metric"));
        // Up, inbox: the waiting parent has it, and is not woken.
        assert!(call("task-2", "message", json!({ "to": "parent", "text": "fyi", "when": "inbox" })).starts_with("sent"));
        let waiting = |conv: &str| -> bool { c.query_row("SELECT waiting FROM trials WHERE conversation = ?1", [conv], |r| r.get(0)).unwrap() };
        assert!(waiting("task-1"));
        // Up, next step: it is woken.
        call("task-2", "message", json!({ "to": "parent", "text": "the repo does not exist", "when": "next_step" }));
        assert!(!waiting("task-1"));
        assert_eq!(db.chain("task-1")?.iter().filter(|e| e.kind == "message").count(), 2);
        // Task 1's parent is the user, reached in the chat as a note.
        assert!(call("task-1", "message", json!({ "to": "parent", "text": "halfway" })).contains("the user"));
        assert!(db.chain("chat")?.last().unwrap().body["text"].as_str().unwrap().contains("halfway"));
        assert!(call("task-1", "message", json!({ "to": "parent", "text": "x", "when": "soon" })).contains("when is one of"));
        // Blocked: let through though task 2 is still out; done is refused for it.
        assert!(call("task-1", "finish", json!({ "report": "r" })).contains("task 2 has not reported"));
        assert_eq!(call("task-1", "finish", json!({ "report": "need a key", "outcome": "blocked" })), FINISHED);
        let outcome: String = c.query_row("SELECT outcome FROM trials WHERE conversation = 'task-1'", [], |r| r.get(0))?;
        assert_eq!(outcome, "blocked");
        let _ = std::fs::remove_file(&path);
        Ok(())
    }
}
