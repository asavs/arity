//! Papercuts: a one-line note that something got in the way, about Arity or
//! about anything outside it. A papercut is an ordinary task on the user's
//! list, tagged `papercut`: it interrupts nobody and no agent picks it up. A
//! worker that asks for help also sends the note to whoever handed it its task,
//! the way `message` does.

use anyhow::{Result, bail};
use rusqlite::Connection;
use serde_json::{Value, json};

use crate::board::{self, Priority, TaskFields};
use crate::{tools, turn};

/// The tag every papercut carries, which is how they are listed.
pub const TAG: &str = "papercut";

pub const DESCRIPTION: &str = "Note in one line that something got in the way: a tool, a brief, missing access, \
                               broken docs, an outside service that misbehaved. It goes on the user's list and \
                               interrupts nobody, so file one whenever it happens and carry on. With ask_help, it \
                               also goes as a message to whoever handed you your task, for when you are stuck.";

pub fn parameters() -> Value {
    json!({"type": "object", "properties": {
        "note": {"type": "string", "description": "What got in the way, in a line."},
        "about": {"type": "string", "description": "What it is about, if not plain from the note: the tool, the \
            brief, the repo, or the outside URL or service."},
        "ask_help": {"type": "boolean", "description": "Also ask whoever handed you your task for help with it."},
        "when": {"type": "string", "enum": ["steer", "after_turn", "inbox"], "description": "With ask_help, when it \
            reaches them if they are working: \"steer\" as soon as it can, cutting off what they are writing; \
            \"after_turn\" (the default) when they would stop; \"inbox\" read when they are next ready, never waking them."}
    }, "required": ["note"]})
}

/// File a papercut from the conversation `conv`, or from the user directly when
/// there is none. The note's first line is the title, cut to `board::MAX_TITLE`;
/// whatever does not fit, and what it is about, go in the brief.
pub fn file(conn: &Connection, conv: Option<&str>, note: &str, about: Option<&str>) -> Result<i64> {
    let note = note.trim();
    if note.is_empty() {
        bail!("a papercut needs a note saying what got in the way");
    }
    let (first, rest) = note.split_once('\n').unwrap_or((note, ""));
    let first = first.trim();
    let (title, overflow) = match first.char_indices().nth(board::MAX_TITLE) {
        Some((i, _)) => (&first[..i], format!("{}\n{rest}", &first[i..])),
        None => (first, rest.to_string()),
    };
    let mut brief = overflow.trim().to_string();
    if let Some(about) = about.map(str::trim).filter(|a| !a.is_empty()) {
        if !brief.is_empty() {
            brief.push_str("\n\n");
        }
        brief.push_str(&format!("About: {about}"));
    }
    let fields = TaskFields {
        title: title.trim().to_string(),
        brief,
        assignee: board::me(),
        priority: Priority::Low,
        tags: vec![TAG.into()],
        ..Default::default()
    };
    board::add_task(conn, &fields, conv)
}

/// The `papercut` tool. The papercut is filed whatever happens to the request
/// for help; if that cannot be delivered, the result says so.
pub fn execute(conn: &Connection, conv: &str, args: &Value) -> Result<String> {
    let note = args["note"].as_str().unwrap_or_default();
    let how = tools::when(args)?;
    let id = file(conn, Some(conv), note, args["about"].as_str())?;
    let filed = format!("filed papercut {id} on the user's list");
    if args["ask_help"] != true || tools::caller(conn, conv)? == tools::Caller::Chat {
        return Ok(format!("{filed}. Carry on."));
    }
    Ok(match ask_help(conn, conv, id, note.trim(), how) {
        Ok(sent) => format!("{filed}, and asked for help: {sent}"),
        Err(e) => format!("{filed}, but could not ask for help: {e:#}"),
    })
}

/// Send a papercut to whoever handed the run in `conv` its task, through the
/// same path as `message` to "parent".
fn ask_help(conn: &Connection, conv: &str, id: i64, note: &str, how: turn::Delivery) -> Result<String> {
    tools::message_parent(conn, conv, &format!("Asking for help (papercut {id}):\n\n{note}"), how)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::journal::Journal;

    fn open(name: &str) -> Result<(Journal, std::path::PathBuf)> {
        let path = std::env::temp_dir().join(format!("arity-papercut-{name}-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        Ok((Journal::open(&path)?, path))
    }

    fn call(c: &Connection, conv: &str, args: Value) -> String {
        tools::execute(c, conv, "secret-model", "papercut", &args.to_string())
    }

    /// The chain the tools tests use: a chat files task 1 for an agent, its run
    /// `task-1` hands task 2 on, and task 2's run `task-2` is at the floor.
    fn chain(c: &Connection) -> Result<()> {
        tools::execute(c, "chat", "m", "add_task", &json!({ "title": "top", "assignee": "agent" }).to_string());
        c.execute("INSERT INTO trials (task, conversation, model, waiting) VALUES (1, 'task-1', 'secret-model', 1)", [])?;
        tools::execute(c, "task-1", "m", "add_task", &json!({ "title": "sub" }).to_string());
        c.execute("INSERT INTO trials (task, conversation, model) VALUES (2, 'task-2', 'secret-model')", [])?;
        assert_eq!(tools::caller(c, "task-2")?, tools::Caller::RunAtFloor);
        Ok(())
    }

    fn id_of(said: &str) -> i64 {
        said.trim_start_matches("filed papercut ").split(' ').next().unwrap().parse().unwrap()
    }

    /// From a chat, a run and a run at the floor, a papercut is a low-priority
    /// task on the user's list, tagged papercut. At the floor it is not the
    /// run's todo and does not hold up finish.
    #[test]
    fn papercuts_land_on_the_users_list_from_anywhere() -> Result<()> {
        let (db, path) = open("callers")?;
        let c = db.conn();
        chain(c)?;
        for (conv, by) in [("chat", "conversations/chat"), ("task-1", "conversations/task-1"), ("task-2", "conversations/task-2")] {
            let said = call(c, conv, json!({ "note": "the docs link is dead", "about": "https://example.com/docs" }));
            assert!(said.starts_with("filed papercut"), "{conv}: {said}");
            let t = board::task(c, id_of(&said))?;
            assert_eq!(t.fields.title, "the docs link is dead");
            assert_eq!(t.fields.brief, "About: https://example.com/docs");
            assert_eq!((t.fields.assignee, t.fields.priority, t.fields.tags), (board::me(), Priority::Low, vec![TAG.to_string()]));
            assert_eq!((t.created_by.as_str(), t.origin.as_deref()), (by, Some(conv)));
            let runs: i64 = c.query_row("SELECT count(*) FROM trials WHERE task = ?1", [t.id], |r| r.get(0))?;
            assert_eq!(runs, 0);
        }
        assert!(board::children_out(c, "task-2")?.is_empty());
        assert_eq!(board::children_out(c, "task-1")?, [2], "only the task it handed out holds it up");
        assert_eq!(tools::execute(c, "task-2", "m", "finish", &json!({ "report": "done" }).to_string()), tools::FINISHED);
        // The user files one from the app or the CLI, in no conversation.
        let id = file(c, None, "  the build is slow  ", None)?;
        assert_eq!((board::task(c, id)?.fields.title.as_str(), board::task(c, id)?.created_by), ("the build is slow", board::me()));
        assert!(call(c, "chat", json!({ "note": "  " })).contains("needs a note"));
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    /// A first line longer than a title can be goes on in the brief, with the
    /// note's other lines and then what it is about.
    #[test]
    fn a_long_note_overflows_into_the_brief() -> Result<()> {
        let (db, path) = open("long")?;
        let c = db.conn();
        let first = format!("{}é tail", "x".repeat(board::MAX_TITLE - 1));
        let id = file(c, None, &format!("{first}\nsecond line"), Some("cargo"))?;
        let t = board::task(c, id)?;
        assert_eq!(t.fields.title, format!("{}é", "x".repeat(board::MAX_TITLE - 1)));
        assert_eq!(t.fields.brief, "tail\nsecond line\n\nAbout: cargo");
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    /// A private run's papercut is private too, links back to the run, and says
    /// nothing about which model the run is on.
    #[test]
    fn a_private_runs_papercut_stays_private_and_blind() -> Result<()> {
        let (db, path) = open("private")?;
        let c = db.conn();
        chain(c)?;
        c.execute("UPDATE tasks SET choice = 'private' WHERE id = 1", [])?;
        let said = call(c, "task-1", json!({ "note": "no access to staging", "ask_help": true }));
        assert!(!said.contains("secret-model"), "{said}");
        let t = board::task(c, id_of(&said))?;
        assert_eq!((t.fields.choice, t.origin.as_deref()), (board::Choice::Private, Some("task-1")));
        assert_eq!(t.model, None);
        assert!(!format!("{:?}", t.fields).contains("secret-model") && !t.created_by.contains("secret-model"));
        let note = db.chain("chat")?.last().unwrap().body.to_string();
        assert!(note.contains("no access to staging") && !note.contains("secret-model"), "{note}");
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    /// Asking for help files the papercut and sends it up like `message`: a
    /// waiting parent run is woken, except by inbox; the user gets a note in the
    /// chat; an unreachable parent still leaves the papercut. A chat just files.
    #[test]
    fn asking_for_help_files_it_and_messages_the_parent() -> Result<()> {
        let (db, path) = open("help")?;
        let c = db.conn();
        chain(c)?;
        let waiting = || -> bool { c.query_row("SELECT waiting FROM trials WHERE conversation = 'task-1'", [], |r| r.get(0)).unwrap() };
        let messages = || db.chain("task-1").unwrap().into_iter().filter(|e| e.kind == "message").collect::<Vec<_>>();
        let said = call(c, "task-2", json!({ "note": "the key is expired", "ask_help": true, "when": "inbox" }));
        assert!(said.contains("asked for help: sent to whoever handed you this task"), "{said}");
        assert!(waiting(), "inbox wakes nobody");
        let got = messages();
        assert_eq!(got.len(), 1);
        let text = got[0].body["text"].as_str().unwrap();
        assert!(text.contains(&format!("papercut {}", id_of(&said))) && text.contains("the key is expired"), "{text}");
        call(c, "task-2", json!({ "note": "still stuck", "ask_help": true }));
        assert!(!waiting(), "after_turn wakes a waiting parent");
        assert_eq!(messages().len(), 2);
        assert!(call(c, "task-2", json!({ "note": "x", "ask_help": true, "when": "soon" })).contains("when is one of"));
        // Task 1's parent is the chat: reached the same way.
        assert!(call(c, "task-1", json!({ "note": "halfway stuck", "ask_help": true })).contains("whoever handed you this task"));
        assert!(db.chain("chat")?.last().unwrap().body["text"].as_str().unwrap().contains("halfway stuck"));
        // A task the user filed outside any chat: nobody to ask, but the papercut stays.
        let lone = board::add_task(c, &TaskFields { title: "lone".into(), assignee: board::AGENT.into(), ..Default::default() }, None)?;
        c.execute("INSERT INTO trials (task, conversation, model) VALUES (?1, 'task-lone', 'm')", [lone])?;
        let said = call(c, "task-lone", json!({ "note": "who do I ask", "ask_help": true }));
        assert!(said.contains("could not ask for help") && said.contains("cannot be reached"), "{said}");
        assert_eq!(board::task(c, id_of(&said))?.fields.title, "who do I ask");
        // From a chat there is no parent: it is just filed.
        let before = db.chain("chat")?.len();
        assert!(call(c, "chat", json!({ "note": "slow", "ask_help": true })).ends_with("Carry on."));
        assert_eq!(db.chain("chat")?.len(), before, "nothing is sent");
        let _ = std::fs::remove_file(&path);
        Ok(())
    }
}
