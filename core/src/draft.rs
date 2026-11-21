//! Task drafts. A draft is a task card bound to a conversation: a model fills
//! it with `propose_task`, the person edits it on the card, and either side can
//! keep changing it until the person adds it to the board or dismisses it.
//!
//! Drafts are journal entries of kind `draft` in the conversation's chain, so
//! they survive restarts, follow forks, and show in the CLI. The current draft
//! is the latest such entry, unless that entry closed it. Entries the person
//! writes carry a `text` the model reads as a user message; the model's own
//! proposals need none, because its tool call already says what it did.

use anyhow::{Result, anyhow};
use rusqlite::Connection;
use serde_json::{Value, json};

use crate::board::{self, AGENT, TaskFields};
use crate::journal::{self, Entry, insert};

/// The draft open in this chain, if any.
pub fn current(chain: &[Entry]) -> Option<TaskFields> {
    let last = chain.iter().rev().find(|e| e.kind == "draft")?;
    match &last.body["draft"] {
        Value::Null => None,
        v => Some(TaskFields::from_json(v)),
    }
}

/// Open an empty card. The model's first job in the conversation is to fill it.
pub fn open(c: &Connection, conv: &str) -> Result<()> {
    let text = "I opened a new task card in this conversation. Help me fill it in: ask what needs doing \
                if that is not clear yet, then fill in the card with propose_task.";
    write(c, conv, "opened", Some(&TaskFields::default()), Some(text))
}

/// The person changed the card. Nothing is written if nothing changed.
pub fn edit(c: &Connection, conv: &str, fields: &TaskFields) -> Result<()> {
    if current(&journal::chain(c, conv)?).as_ref() == Some(fields) {
        return Ok(());
    }
    let text = format!("I edited the task card. It now reads:\n{}", fields.describe());
    write(c, conv, "edited", Some(fields), Some(&text))
}

/// A model proposes a card, or changes the open one. Fields it leaves out keep their value.
pub fn propose(c: &Connection, conv: &str, args: &Value) -> Result<TaskFields> {
    let mut fields = current(&journal::chain(c, conv)?).unwrap_or_default();
    fields.merge(args)?;
    write(c, conv, "proposed", Some(&fields), None)?;
    Ok(fields)
}

/// Put the open card on the board, as a task filed in this conversation.
pub fn add(c: &Connection, conv: &str) -> Result<i64> {
    let fields = current(&journal::chain(c, conv)?).ok_or_else(|| anyhow!("{conv} has no task card open"))?;
    let id = board::add_task(c, &fields, Some(conv))?;
    let text = if fields.assignee == AGENT {
        format!("I added the task card to the board as task {id}, for an agent. One will be picked to do it, and I will rate its result.")
    } else {
        format!("I added the task card to the board as task {id}, assigned to {}.", fields.assignee)
    };
    write_closed(c, conv, "added", json!({ "task": id }), &text)?;
    Ok(id)
}

pub fn dismiss(c: &Connection, conv: &str) -> Result<()> {
    if current(&journal::chain(c, conv)?).is_none() {
        return Ok(());
    }
    write_closed(c, conv, "dismissed", json!({}), "I dismissed the task card.")
}

/// The chain as the model should read it: of several card edits in a row, only
/// the last, since each one restates the whole card.
pub fn collapse_edits(chain: &[Entry]) -> Vec<Entry> {
    let is_edit = |e: &Entry| e.kind == "draft" && e.body["event"] == "edited";
    chain
        .iter()
        .enumerate()
        .filter(|(i, e)| !(is_edit(e) && chain.get(i + 1).is_some_and(is_edit)))
        .map(|(_, e)| e.clone())
        .collect()
}

fn write(c: &Connection, conv: &str, event: &str, fields: Option<&TaskFields>, text: Option<&str>) -> Result<()> {
    let mut body = json!({ "event": event, "draft": fields.map(TaskFields::to_json) });
    if let Some(t) = text {
        body["text"] = json!(t);
    }
    insert(c, conv, "draft", &body, None)?;
    Ok(())
}

fn write_closed(c: &Connection, conv: &str, event: &str, mut body: Value, text: &str) -> Result<()> {
    body["event"] = json!(event);
    body["draft"] = Value::Null;
    body["text"] = json!(text);
    insert(c, conv, "draft", &body, None)?;
    Ok(())
}

/// What a person sees in the conversation for a draft entry, if anything.
/// Edits show on the card itself, not as lines in the conversation.
pub fn summary(body: &Value) -> Option<String> {
    Some(match body["event"].as_str()? {
        "opened" => "Opened a task card".into(),
        "added" => format!("Added the card to the board as task {}", body["task"]),
        "dismissed" => "Dismissed the task card".into(),
        _ => return None,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::Journal;

    #[test]
    fn a_draft_is_proposed_edited_and_added() -> Result<()> {
        let path = std::env::temp_dir().join(format!("arity-draft-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path)?;
        let c = db.conn();
        open(c, "chat")?;
        assert_eq!(current(&db.chain("chat")?), Some(TaskFields::default()));
        propose(c, "chat", &json!({"title": "Summarise the README", "assignee": "agent"}))?;
        propose(c, "chat", &json!({"clearance": "local", "priority": "high"}))?;
        let mut f = current(&db.chain("chat")?).unwrap();
        assert_eq!((f.title.as_str(), f.assignee.as_str()), ("Summarise the README", AGENT), "kept across proposals");
        assert_eq!((f.clearance, f.priority), (board::Clearance::Local, board::Priority::High));
        f.tags = vec!["docs".into()];
        edit(c, "chat", &f)?;
        let n = db.chain("chat")?.len();
        edit(c, "chat", &f)?;
        assert_eq!(db.chain("chat")?.len(), n, "an unchanged card writes nothing");
        assert!(propose(c, "chat", &json!({"due": "next week"})).is_err());
        let id = add(c, "chat")?;
        assert!(current(&db.chain("chat")?).is_none(), "closed once added");
        let t = board::task(c, id)?;
        assert_eq!((t.created_by, t.origin.as_deref(), t.fields.tags.clone()), (board::me(), Some("chat"), vec!["docs".to_string()]));
        assert_eq!(t.state, board::TaskState::Ready);
        let _ = std::fs::remove_file(&path);
        Ok(())
    }
}
