//! Cache-safe system prompt and append-only context section deltas.
//!
//! Provider prompt caches match from the start of the message list, so the
//! leading `system` prompt must stay byte-identical for the life of a
//! conversation. Mutable context (today's date, attached folder, worker
//! post-finish status, open task card) is appended as immutable `"system"`
//! journal entries containing only the sections that changed since the last
//! delta.

use std::collections::BTreeMap;
use std::path::PathBuf;

use anyhow::Result;
use serde_json::{Map, Value, json};

use crate::journal::{Entry, Journal};

pub const CHAT_SYSTEM: &str = "You are an assistant inside Arity. You share a board of tasks with the user \
and other agents, and you read and change it only through your tools.

When there is a task, you have two ways to get it done:
- Do it yourself: put the finished work itself in your reply. If the task is on the board, call complete_task \
in that same reply, after the work, never before it.
- Hand it to an agent: set its assignee to \"agent\". One is picked to do it, and its result comes back here \
when it finishes; finish your reply now instead of waiting.

When the user wants a task written, draft it with propose_task: that fills in the task card they see under \
this conversation. Write the brief to whoever will do it: they see only the title and \
brief, so say everything they need. Keep changing the card as the \
user asks; they add it to the board themselves. Use add_task only when they ask you to add a task directly.

The card holds one task. When the user wants work broken into several tasks, do not keep rewriting the card: \
outline the tasks briefly in your reply, or, once they agree, add them with add_task, setting blocked_by where \
one must wait for another.

Be brief.";

pub const WORKER_SYSTEM: &str = "You are an agent inside Arity, doing one task from a shared board. The task was \
handed to you: where it says an agent or someone else should do it, that means you. Do the work, then call \
finish with your report. The report is all the person who wrote the task will see, so make it complete: the \
outcome first, then what they need to use it. Ending your turn without finish hands nothing in. If you cannot go \
on, finish with outcome blocked and say what you need. To tell whoever handed you the task something sooner, \
use message. Be brief.";

const SECTION_ORDER: [&str; 4] = ["date", "folder", "status", "card"];

/// Immutable leading system prompt for the life of a conversation.
pub fn leading_system(worker: bool) -> &'static str {
    if worker { WORKER_SYSTEM } else { CHAT_SYSTEM }
}

/// Dynamic prompt context tracked as append-only `"system"` deltas.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PromptState {
    pub worker: bool,
    pub in_run: bool,
    pub today: String,
    pub folder: Option<PathBuf>,
    pub draft_card: Option<String>,
}

fn desired_section(key: &str, state: &PromptState) -> Option<String> {
    match key {
        "date" => Some(format!("Today is {}.", state.today)),
        "folder" => state.folder.as_ref().map(|dir| {
            format!(
                "The folder {} is attached{}. Use list_files, read_file, search_files, write_file, edit_file and bash to inspect, edit and test it; paths are relative to it.",
                dir.display(),
                if state.worker { " to your task" } else { " to this conversation" }
            )
        }),
        "status" => (state.worker && !state.in_run).then(|| {
            "You have already handed in your report for this task. Answer follow-up messages directly; call finish again only if you are replacing your report."
                .to_string()
        }),
        "card" => {
            if !state.worker {
                state.draft_card.as_ref().map(|desc| {
                    format!(
                        "A task card is open under this conversation. It is not on the board yet and has no id; change it only with propose_task, never with update_task. It reads:\n{desc}"
                    )
                })
            } else {
                None
            }
        }
        _ => None,
    }
}

fn removal_text(key: &str) -> Option<&'static str> {
    match key {
        "folder" => Some("The attached folder was removed."),
        "card" => Some("No task card is open now."),
        _ => None,
    }
}

/// Replay all `"system"` entries in `chain` to reconstruct the sections currently shown to the model.
pub fn replay_sections(chain: &[Entry]) -> BTreeMap<String, String> {
    let mut shown = BTreeMap::new();
    for e in chain {
        if e.kind != "system" {
            continue;
        }
        if let Some(obj) = e.body.get("sections").and_then(Value::as_object) {
            for (k, v) in obj {
                match v {
                    Value::String(s) => {
                        shown.insert(k.clone(), s.clone());
                    }
                    Value::Null => {
                        shown.remove(k);
                    }
                    _ => {}
                }
            }
        }
    }
    shown
}

/// Compute the minimal `"system"` delta entry body needed to bring `chain` up to `state`,
/// or `None` when nothing changed.
pub fn plan_delta(chain: &[Entry], state: &PromptState) -> Option<Value> {
    let shown = replay_sections(chain);
    let mut patch = Map::new();
    let mut parts = Vec::new();

    for key in SECTION_ORDER {
        let desired = desired_section(key, state);
        let current = shown.get(key).map(String::as_str);
        match (current, desired) {
            (Some(old), Some(new)) if old == new => {}
            (None, None) => {}
            (_, Some(new)) => {
                patch.insert(key.to_string(), Value::String(new.clone()));
                parts.push(new);
            }
            (Some(_), None) => {
                patch.insert(key.to_string(), Value::Null);
                if let Some(msg) = removal_text(key) {
                    parts.push(msg.to_string());
                }
            }
        }
    }

    if patch.is_empty() {
        return None;
    }

    Some(json!({
        "sections": Value::Object(patch),
        "text": parts.join("\n\n"),
    }))
}

/// Append a minimal `"system"` delta entry to `conv` if any dynamic section changed.
/// Returns `Ok(true)` if an entry was appended, `Ok(false)` if the prefix stayed untouched.
/// Sections are replayed from what the model reads, so after a compact or reset every
/// current section is sent once more.
pub fn sync(db: &Journal, conv: &str, state: &PromptState) -> Result<bool> {
    let chain = db.chain_for_model(conv)?;
    if let Some(body) = plan_delta(&chain, state) {
        db.append(conv, "system", &body, None)?;
        Ok(true)
    } else {
        Ok(false)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_db(name: &str) -> (std::path::PathBuf, Journal) {
        let path = std::env::temp_dir().join(format!("arity-prompt-{name}-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path).unwrap();
        (path, db)
    }

    #[test]
    fn leading_system_is_invariant_across_dynamic_state_changes() {
        let chat_initial = leading_system(false);
        let worker_initial = leading_system(true);
        assert_eq!(chat_initial, CHAT_SYSTEM);
        assert_eq!(worker_initial, WORKER_SYSTEM);
        assert_ne!(chat_initial, worker_initial);

        // Leading system never contains mutable date, folder, card, or post-finish follow-up text.
        for prefix in [chat_initial, worker_initial] {
            assert!(!prefix.contains("Today is"));
            assert!(!prefix.contains("The folder"));
            assert!(!prefix.contains("A task card is open"));
            assert!(!prefix.contains("already handed in your report"));
        }

        // Across card edits, folder attaches, date changes, and worker follow-ups, the leading prefix never changes.
        assert_eq!(leading_system(false), chat_initial);
        assert_eq!(leading_system(true), worker_initial);
    }

    #[test]
    fn sync_appends_initial_entry_then_noop_then_minimal_deltas() -> Result<()> {
        let (path, db) = temp_db("sync-deltas");
        let conv = "chat-1";

        let mut state = PromptState {
            worker: false,
            in_run: false,
            today: "2026-11-21".into(),
            folder: None,
            draft_card: None,
        };

        // Initial turn appends one system entry with the date section.
        assert!(sync(&db, conv, &state)?);
        let chain1 = db.chain(conv)?;
        assert_eq!(chain1.len(), 1);
        assert_eq!(chain1[0].kind, "system");
        assert_eq!(chain1[0].body["text"], "Today is 2026-11-21.");
        assert_eq!(chain1[0].body["sections"], json!({ "date": "Today is 2026-11-21." }));

        // Subsequent unchanged turn appends nothing.
        assert!(!sync(&db, conv, &state)?);
        assert_eq!(db.chain(conv)?.len(), 1);

        // Opening a task card appends only the card delta, leaving prior entry untouched.
        state.draft_card = Some("Title: Draft RFC\nAssignee: agent".into());
        assert!(sync(&db, conv, &state)?);
        let chain2 = db.chain(conv)?;
        assert_eq!(chain2.len(), 2);
        assert_eq!(chain2[0].body, chain1[0].body, "prior entry stays untouched");
        assert_eq!(chain2[1].body["sections"].as_object().unwrap().len(), 1);
        assert!(chain2[1].body["sections"].get("card").is_some());
        assert!(chain2[1].body["text"].as_str().unwrap().contains("Title: Draft RFC"));
        assert!(!chain2[1].body["text"].as_str().unwrap().contains("Today is"));

        // Unchanged card appends nothing.
        assert!(!sync(&db, conv, &state)?);
        assert_eq!(db.chain(conv)?.len(), 2);

        // Editing the card appends only the updated card delta.
        state.draft_card = Some("Title: Draft RFC v2\nAssignee: agent".into());
        assert!(sync(&db, conv, &state)?);
        let chain3 = db.chain(conv)?;
        assert_eq!(chain3.len(), 3);
        assert_eq!(chain3[0].body, chain1[0].body);
        assert_eq!(chain3[1].body, chain2[1].body);
        assert!(chain3[2].body["text"].as_str().unwrap().contains("Draft RFC v2"));

        // Closing the card emits null for "card" and the closed notice.
        state.draft_card = None;
        assert!(sync(&db, conv, &state)?);
        let chain4 = db.chain(conv)?;
        assert_eq!(chain4.len(), 4);
        assert_eq!(chain4[3].body["sections"], json!({ "card": Value::Null }));
        assert_eq!(chain4[3].body["text"], "No task card is open now.");
        assert!(!replay_sections(&chain4).contains_key("card"));

        // Staying closed appends nothing.
        assert!(!sync(&db, conv, &state)?);
        assert_eq!(db.chain(conv)?.len(), 4);

        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    #[test]
    fn folder_date_and_worker_status_deltas_render_in_fixed_order() -> Result<()> {
        let (path, db) = temp_db("worker-order");
        let conv = "task-1";

        let mut state = PromptState {
            worker: true,
            in_run: true,
            today: "2026-11-21".into(),
            folder: Some(PathBuf::from("/tmp/repo")),
            draft_card: Some("ignored for workers".into()),
        };

        assert!(sync(&db, conv, &state)?);
        let chain = db.chain(conv)?;
        let text = chain[0].body["text"].as_str().unwrap();
        assert_eq!(
            text,
            "Today is 2026-11-21.\n\nThe folder /tmp/repo is attached to your task. Use list_files, read_file, search_files, write_file, edit_file and bash to inspect, edit and test it; paths are relative to it."
        );
        let shown = replay_sections(&chain);
        assert!(shown.contains_key("date"));
        assert!(shown.contains_key("folder"));
        assert!(!shown.contains_key("status"));
        assert!(!shown.contains_key("card"));

        // After finishing, worker follow-up adds the "status" section; date change + folder removal emit in deterministic order.
        state.in_run = false;
        state.today = "2026-11-22".into();
        state.folder = None;
        assert!(sync(&db, conv, &state)?);
        let chain2 = db.chain(conv)?;
        assert_eq!(chain2.len(), 2);
        assert_eq!(
            chain2[1].body["text"],
            "Today is 2026-11-22.\n\nThe attached folder was removed.\n\nYou have already handed in your report for this task. Answer follow-up messages directly; call finish again only if you are replacing your report."
        );
        assert_eq!(chain2[1].body["sections"]["folder"], Value::Null);
        assert!(chain2[1].body["sections"]["status"].is_string());

        let shown2 = replay_sections(&chain2);
        assert_eq!(shown2.get("date").map(String::as_str), Some("Today is 2026-11-22."));
        assert!(!shown2.contains_key("folder"));
        assert!(shown2.contains_key("status"));

        assert!(!sync(&db, conv, &state)?);

        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    #[test]
    fn sync_sends_every_current_section_once_after_a_compact_or_reset() -> Result<()> {
        let (path, db) = temp_db("markers");
        let conv = "chat-1";
        let state = PromptState {
            worker: false,
            in_run: false,
            today: "2026-11-21".into(),
            folder: Some(PathBuf::from("/tmp/repo")),
            draft_card: Some("Title: Ship M1".into()),
        };
        assert!(sync(&db, conv, &state)?);
        db.append(conv, "user", &json!({ "text": "hi" }), None)?;
        assert!(!sync(&db, conv, &state)?, "unchanged");

        for marker in [json!(["compact", { "summary": "said hi", "compacted_count": 2 }]), json!(["reset", { "note": null, "head_entry": null }])] {
            db.append(conv, marker[0].as_str().unwrap(), &marker[1], None)?;
            assert!(sync(&db, conv, &state)?, "{} re-sends the sections", marker[0]);
            let last = db.chain(conv)?.pop().unwrap();
            assert_eq!(last.kind, "system");
            let sections = last.body["sections"].as_object().unwrap();
            assert_eq!(sections.keys().map(String::as_str).collect::<Vec<_>>(), ["card", "date", "folder"]);
            let text = last.body["text"].as_str().unwrap();
            assert!(text.starts_with("Today is 2026-11-21.") && text.contains("/tmp/repo") && text.contains("Ship M1"));
            assert!(!text.contains("removed") && !text.contains("No task card"), "no removal notices: {text}");
            assert!(!sync(&db, conv, &state)?, "and only once");
            let model_systems = db.chain_for_model(conv)?.iter().filter(|e| e.kind == "system").count();
            assert_eq!(model_systems, 1, "the model reads one copy of each section");
        }
        assert_eq!(db.chain(conv)?.iter().filter(|e| e.kind == "system").count(), 3, "the raw chain keeps all of them");

        let _ = std::fs::remove_file(&path);
        Ok(())
    }
}
