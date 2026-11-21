//! Board tools. They run inside the journal's transaction, so a tool's effect
//! and the record of it commit together.

use rusqlite::{Connection, params};
use serde_json::{Value, json};

pub struct ToolSpec {
    pub name: &'static str,
    pub description: &'static str,
    pub parameters: Value,
}

pub fn specs() -> Vec<ToolSpec> {
    vec![
        ToolSpec {
            name: "add_task",
            description: "Add a task to the shared board.",
            parameters: json!({"type": "object", "properties": {"title": {"type": "string"}}, "required": ["title"]}),
        },
        ToolSpec {
            name: "list_tasks",
            description: "List every task on the shared board with its id and whether it is done.",
            parameters: json!({"type": "object", "properties": {}}),
        },
        ToolSpec {
            name: "complete_task",
            description: "Mark a task on the shared board as done, by id.",
            parameters: json!({"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
        },
    ]
}

/// Errors become the tool's output, so the model can see and recover from them.
pub fn execute(db: &Connection, name: &str, arguments: &str) -> String {
    let args: Value = serde_json::from_str(arguments).unwrap_or(json!({}));
    let result = match name {
        "add_task" => match args["title"].as_str() {
            Some(title) => db
                .execute("INSERT INTO tasks (title) VALUES (?1)", params![title])
                .map(|_| format!("added task {}", db.last_insert_rowid())),
            None => return "error: missing title".into(),
        },
        "list_tasks" => (|| {
            let mut stmt = db.prepare("SELECT id, title, done FROM tasks ORDER BY id")?;
            let rows: Vec<String> = stmt
                .query_map([], |r| {
                    let done: i64 = r.get(2)?;
                    Ok(format!("{} [{}] {}", r.get::<_, i64>(0)?, if done != 0 { "x" } else { " " }, r.get::<_, String>(1)?))
                })?
                .collect::<Result<_, _>>()?;
            Ok(if rows.is_empty() { "the board is empty".into() } else { rows.join("\n") })
        })(),
        "complete_task" => match args["id"].as_i64() {
            Some(id) => db
                .execute("UPDATE tasks SET done = 1 WHERE id = ?1", params![id])
                .map(|n| if n == 1 { format!("completed task {id}") } else { format!("error: no task {id}") }),
            None => return "error: missing id".into(),
        },
        other => return format!("error: unknown tool {other}"),
    };
    result.unwrap_or_else(|e: rusqlite::Error| format!("error: {e}"))
}
