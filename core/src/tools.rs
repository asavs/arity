//! Board tools. They run inside the journal's transaction, so a tool's effect
//! and the record of it commit together. Errors become the tool's output, so
//! the model can see them and recover.

use rusqlite::Connection;
use serde_json::{Value, json};

use crate::board::{self, AGENT, RunState, TaskFields, TaskState};
use crate::draft;

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

fn with_id(mut props: Value) -> Value {
    props["id"] = json!({"type": "integer"});
    props
}

/// Workers doing a task may read the board and file tasks of their own, the
/// same call a chat makes one level down, but not change other tasks. `rate`
/// is offered only to a conversation with results of its own waiting to be rated.
pub fn specs(worker: bool, can_rate: bool) -> Vec<ToolSpec> {
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
    ];
    all.into_iter()
        .filter(|t| match t.name {
            "rate" => can_rate,
            "list_tasks" | "add_task" => true,
            _ => !worker,
        })
        .collect()
}

const HANDED_OFF: &str = "An agent will do it and the user will rate its result. \
                          Do not wait or check on it: finish your reply now.";
const HANDED_OFF_BY_A_RUN: &str = "An agent will do it. Its result comes back to this conversation for you to rate, \
                                   after your own reply is in: finish your reply now with what you can do yourself.";

/// `conv` is the conversation calling the tool, and `model` the model it is running on.
pub fn execute(db: &Connection, conv: &str, model: &str, name: &str, arguments: &str) -> String {
    let args: Value = serde_json::from_str(arguments).unwrap_or(json!({}));
    let result = (|| -> anyhow::Result<String> {
        let worker = board::trial_of_conversation(db, conv)?.is_some();
        if !matches!(name, "list_tasks" | "rate" | "add_task") && worker {
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
            "add_task" => {
                let mut f = TaskFields::default();
                f.merge(&args)?;
                let id = board::add_task(db, &f, Some(conv))?;
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

    /// A run files a task with the same call a chat uses, one level down, until the floor.
    #[test]
    fn a_run_files_subtasks_down_to_the_floor() -> anyhow::Result<()> {
        let path = std::env::temp_dir().join(format!("arity-depth-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = crate::journal::Journal::open(&path)?;
        let c = db.conn();
        let run = |task: i64| {
            c.execute("INSERT INTO trials (task, conversation, model) VALUES (?1, 'task-' || ?1, 'lab:m')", rusqlite::params![task])
        };
        let add = |conv: &str, title: &str| execute(c, conv, "lab:m", "add_task", &json!({ "title": title, "assignee": "agent" }).to_string());
        // A chat files task 1; its run files task 2, which is the run's own, with the run's reply text.
        assert!(add("chat", "top").starts_with("added task 1"));
        run(1)?;
        let names: Vec<&str> = specs(true, false).iter().map(|t| t.name).collect();
        assert_eq!(names, ["add_task", "list_tasks"], "a worker gets add_task like a chat");
        let said = add("task-1", "sub");
        assert!(said.starts_with("added task 2") && said.contains("comes back to this conversation"), "{said}");
        let sub = board::task(c, 2)?;
        assert_eq!(sub.created_by, "conversations/task-1");
        // A private run's tasks stay private, whatever it asks for.
        c.execute("UPDATE tasks SET choice = 'private' WHERE id = 1", [])?;
        assert!(add("task-1", "another").starts_with("added task 3"));
        assert_eq!(board::task(c, 3)?.fields.choice, board::Choice::Private);
        // Task 2's run is at the floor: it is told to do the work itself.
        run(2)?;
        let said = add("task-2", "subsub");
        assert!(said.starts_with("error:") && said.contains("do the work yourself"), "{said}");
        assert!(board::task(c, 4).is_err());
        // Workers still cannot change other tasks.
        assert!(execute(c, "task-1", "lab:m", "complete_task", r#"{"id": 1}"#).starts_with("error:"));
        let _ = std::fs::remove_file(&path);
        Ok(())
    }
}
