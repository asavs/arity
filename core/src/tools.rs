//! Tool registry and built-in board tools.
//!
//! Board tools are `Execution::Atomic`: they run inside `Journal::immediate`, so
//! a tool's effect on SQLite and the `tool` entry recording it commit together.
//! External tools are `Execution::External`: they commit a `tool_intent` entry
//! first, execute outside any SQLite transaction so heartbeats and UI stay
//! responsive, and then commit the `tool` result entry. Errors become the tool's
//! output, so the model can see them and recover.

use std::path::PathBuf;

use anyhow::Result;
use rusqlite::Connection;
use serde_json::{Value, json};

use crate::board::{self, AGENT, RunState, TaskFields, TaskState};
use crate::journal::Journal;
use crate::model::ToolCall;
use crate::{draft, turn};

#[derive(Debug, Clone)]
pub struct ToolSpec {
    pub name: &'static str,
    pub description: &'static str,
    pub parameters: Value,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Replay {
    /// Safe to re-run after a crash interrupted execution before the result committed.
    Safe,
    /// Must NOT re-run after a crash interrupted execution; emit an interrupted error result instead.
    Unsafe,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Execution {
    /// Runs inside `Journal::immediate` alongside the `tool` result entry.
    Atomic,
    /// Commits a `tool_intent` entry before running outside any SQLite transaction,
    /// then commits the `tool` result entry.
    External { replay: Replay },
}

#[derive(Debug, Clone)]
pub struct ToolOutput {
    pub text: String,
    pub is_error: bool,
    pub details: Option<Value>,
}

impl ToolOutput {
    pub fn ok(text: impl Into<String>) -> Self {
        Self { text: text.into(), is_error: false, details: None }
    }

    pub fn err(text: impl Into<String>) -> Self {
        Self { text: text.into(), is_error: true, details: None }
    }
}

#[derive(Debug, Clone)]
pub struct ToolVisibility {
    pub caller: Caller,
    pub folder: Option<PathBuf>,
}

#[derive(Debug, Clone)]
pub enum ToolHookDecision {
    Continue,
    RewriteArgs(Value),
    Block(String),
}

pub trait Tool: Send + Sync {
    fn name(&self) -> &'static str;
    fn execution(&self) -> Execution;
    fn spec(&self, vis: &ToolVisibility) -> Option<ToolSpec>;

    fn run_atomic(&self, _db: &Connection, _conv: &str, _model: &str, _args: &Value) -> Result<ToolOutput> {
        anyhow::bail!("{} is not an atomic tool", self.name())
    }

    fn run_external(&self, _vis: &ToolVisibility, _args: &Value) -> Result<ToolOutput> {
        anyhow::bail!("{} is not an external tool", self.name())
    }

    fn refused_message(&self, _vis: &ToolVisibility) -> Option<String> {
        None
    }
}

/// Maximum size of a single tool output committed to the journal.
pub const MAX_TOOL_OUTPUT_BYTES: usize = 64 * 1024;

/// Bound `text` to at most `max_bytes` UTF-8 bytes, truncating cleanly on a
/// char boundary and appending a truncation notice when bytes are omitted.
pub fn bound_output(text: String, max_bytes: usize) -> String {
    if text.len() <= max_bytes {
        return text;
    }
    let mut end = max_bytes;
    while end > 0 && !text.is_char_boundary(end) {
        end -= 1;
    }
    let omitted = text.len() - end;
    format!("{}\n[output truncated: {omitted} bytes omitted]", &text[..end])
}

/// Who is calling the tools, which decides which tools they get.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Caller {
    /// A conversation the user talks in.
    Chat,
    /// A run above the floor: it may hand work to an agent, one level down.
    Run,
    /// A run at the floor (`board::MAX_DEPTH`): delegation is not offered.
    RunAtFloor,
}

pub fn caller(db: &Connection, conv: &str) -> Result<Caller> {
    Ok(match board::run_depth(db, conv)? {
        None => Caller::Chat,
        Some(d) if d >= board::MAX_DEPTH => Caller::RunAtFloor,
        Some(_) => Caller::Run,
    })
}

/// What `finish` says when it accepts a report. The loop ends the run on it.
pub const FINISHED: &str = "finished: your report is handed in";
/// How many times `finish` turns a run back before it lets it through anyway,
/// so a model that cannot clear what is open does not loop forever.
const MAX_REFUSALS: usize = 3;

const WORKER_NO_CHANGE: &str = "you are doing a task: you can read the board and file tasks, not change others";
const HANDED_OFF: &str = "An agent will do it, knowing only its title and brief. Its result will come back here \
                          when it finishes; finish your reply now instead of waiting.";
const HANDED_OFF_BY_A_RUN: &str = "An agent will do it, knowing only its title and brief. Its result comes back \
                                   here. Carry on with the rest meanwhile, or end your turn without calling finish to \
                                   wait: you are woken when it reports. Call finish once you have what you need.";

pub struct ToolRegistry {
    tools: Vec<Box<dyn Tool>>,
}

impl Default for ToolRegistry {
    fn default() -> Self {
        Self::default_registry()
    }
}

impl ToolRegistry {
    pub fn new() -> Self {
        Self { tools: Vec::new() }
    }

    pub fn register(&mut self, tool: Box<dyn Tool>) {
        if let Some(slot) = self.tools.iter_mut().find(|t| t.name() == tool.name()) {
            *slot = tool;
        } else {
            self.tools.push(tool);
        }
    }

    pub fn with(mut self, tool: Box<dyn Tool>) -> Self {
        self.register(tool);
        self
    }

    pub fn default_registry() -> Self {
        let mut reg = Self::new();
        reg.register(Box::new(ProposeTaskTool));
        reg.register(Box::new(AddTaskTool));
        reg.register(Box::new(UpdateTaskTool));
        reg.register(Box::new(ListTasksTool));
        reg.register(Box::new(CompleteTaskTool));
        reg.register(Box::new(FinishTool));
        reg.register(Box::new(MessageTool));
        reg.register(Box::new(PapercutTool));
        for tool in crate::files::tools() {
            reg.register(tool);
        }
        for tool in crate::coding::tools() {
            reg.register(tool);
        }
        reg
    }

    pub fn all(&self) -> &[Box<dyn Tool>] {
        &self.tools
    }

    pub fn specs(&self, vis: &ToolVisibility) -> Vec<ToolSpec> {
        self.tools.iter().filter_map(|t| t.spec(vis)).collect()
    }

    pub fn get(&self, name: &str) -> Option<&dyn Tool> {
        self.tools.iter().find(|t| t.name() == name).map(|t| t.as_ref())
    }

    pub fn execute_call(&self, db: &Journal, conv: &str, model: &str, call: &ToolCall) -> Result<ToolOutput> {
        self.run_with_hooks(db, conv, model, call, |_, _| Ok(ToolHookDecision::Continue), |_, _| Ok(()))
    }

    pub fn run_with_hooks<B, A>(
        &self,
        db: &Journal,
        conv: &str,
        model: &str,
        call: &ToolCall,
        mut before: B,
        mut after: A,
    ) -> Result<ToolOutput>
    where
        B: FnMut(&ToolCall, &Value) -> Result<ToolHookDecision>,
        A: FnMut(&ToolCall, &mut ToolOutput) -> Result<()>,
    {
        let mut args: Value = serde_json::from_str(&call.arguments).unwrap_or_else(|_| json!({}));
        strip_dir(&mut args);

        let vis = ToolVisibility {
            caller: caller(db.conn(), conv)?,
            folder: board::folder_of(db.conn(), conv)?,
        };

        let Some(tool) = self.get(&call.name) else {
            let mut output = ToolOutput::err(format!("error: unknown tool {}", call.name));
            after(call, &mut output)?;
            output.text = bound_output(output.text, MAX_TOOL_OUTPUT_BYTES);
            db.append(conv, "tool", &tool_entry_body(call, &output), None)?;
            return Ok(output);
        };

        if tool.spec(&vis).is_none() {
            let reason = tool
                .refused_message(&vis)
                .unwrap_or_else(|| format!("unknown tool {}", call.name));
            let msg = if reason.starts_with("error:") {
                reason
            } else {
                format!("error: {reason}")
            };
            let mut output = ToolOutput::err(msg);
            after(call, &mut output)?;
            output.text = bound_output(output.text, MAX_TOOL_OUTPUT_BYTES);
            db.append(conv, "tool", &tool_entry_body(call, &output), None)?;
            return Ok(output);
        }

        // If a crash interrupted a non-replay-safe external tool after its intent committed,
        // emit an interrupted error without running `before` or re-executing the tool.
        if let Execution::External { replay: Replay::Unsafe } = tool.execution() {
            if has_tool_intent(db, conv, &call.id)? {
                let mut output = ToolOutput::err(
                    "error: interrupted by a crash before completing; not retried because this tool is not replay-safe",
                );
                after(call, &mut output)?;
                output.text = bound_output(output.text, MAX_TOOL_OUTPUT_BYTES);
                db.append(conv, "tool", &tool_entry_body(call, &output), None)?;
                return Ok(output);
            }
        }

        match before(call, &args)? {
            ToolHookDecision::Continue => {}
            ToolHookDecision::RewriteArgs(new_args) => {
                args = new_args;
                strip_dir(&mut args);
            }
            ToolHookDecision::Block(reason) => {
                let mut output = ToolOutput::err(format!("error: blocked: {reason}"));
                after(call, &mut output)?;
                output.text = bound_output(output.text, MAX_TOOL_OUTPUT_BYTES);
                db.append(conv, "tool", &tool_entry_body(call, &output), None)?;
                return Ok(output);
            }
        }

        match tool.execution() {
            Execution::Atomic => db.immediate(|c| {
                c.execute_batch("SAVEPOINT arity_tool_run")?;
                let mut output = match tool.run_atomic(c, conv, model, &args) {
                    Ok(out) => {
                        c.execute_batch("RELEASE SAVEPOINT arity_tool_run")?;
                        out
                    }
                    Err(e) => {
                        let _ = c.execute_batch("ROLLBACK TO SAVEPOINT arity_tool_run; RELEASE SAVEPOINT arity_tool_run");
                        ToolOutput::err(format!("error: {e:#}"))
                    }
                };
                output.text = bound_output(output.text, MAX_TOOL_OUTPUT_BYTES);
                after(call, &mut output)?;
                output.text = bound_output(output.text, MAX_TOOL_OUTPUT_BYTES);
                crate::journal::insert(c, conv, "tool", &tool_entry_body(call, &output), None)?;
                Ok(output)
            }),
            Execution::External { replay } => {
                if !has_tool_intent(db, conv, &call.id)? {
                    db.append(
                        conv,
                        "tool_intent",
                        &json!({
                            "call_id": call.id,
                            "name": call.name,
                            "replay": match replay {
                                Replay::Safe => "safe",
                                Replay::Unsafe => "unsafe",
                            }
                        }),
                        None,
                    )?;
                }
                let mut output = match tool.run_external(&vis, &args) {
                    Ok(out) => out,
                    Err(e) => ToolOutput::err(format!("error: {e:#}")),
                };
                output.text = bound_output(output.text, MAX_TOOL_OUTPUT_BYTES);
                after(call, &mut output)?;
                output.text = bound_output(output.text, MAX_TOOL_OUTPUT_BYTES);
                db.append(conv, "tool", &tool_entry_body(call, &output), None)?;
                Ok(output)
            }
        }
    }
}

fn strip_dir(args: &mut Value) {
    // A task's folder is set by the user, or passed down; a model never picks one.
    if let Some(obj) = args.as_object_mut() {
        obj.remove("dir");
    }
}

fn has_tool_intent(db: &Journal, conv: &str, call_id: &str) -> Result<bool> {
    Ok(db
        .chain(conv)?
        .iter()
        .any(|e| e.kind == "tool_intent" && e.body["call_id"] == call_id))
}

fn tool_entry_body(call: &ToolCall, output: &ToolOutput) -> Value {
    let mut body = json!({
        "call_id": call.id,
        "name": call.name,
        "output": output.text,
        "is_error": output.is_error,
    });
    if let Some(details) = &output.details {
        body["details"] = details.clone();
    }
    body
}

/// The fields `add_task`, `update_task` and `propose_task` all take.
fn field_schema() -> Value {
    json!({
        "title": {"type": "string", "description": "Short, starts with a verb."},
        "brief": {"type": "string", "description": "Everything whoever does it needs to know to do it well."},
        "assignee": {"type": "string", "description": "\"agent\" hands it to an agent: one is picked to do it, and its \
            result comes back here. \"user\" puts it on the user's own list."},
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

fn when_done_prop() -> Value {
    json!({"type": "string", "enum": ["after_turn", "steer"],
        "description": "When its result reaches you if you are still working: \"after_turn\" (the default) \
        when you next stop, \"steer\" as soon as it arrives, cutting off what you are writing. If you are \
        waiting, \"steer\" wakes you for each result, \"after_turn\" once all your sub-tasks have reported."})
}

/// A run gets the same `add_task` a chat uses, so a subagent is a task one level
/// down. At the floor, delegation is omitted. Runs cannot change other tasks.
/// With a folder attached (`board::folder_of`), every caller also gets the
/// read-only file tools.
#[cfg(test)]
pub fn specs(caller: Caller, folder: bool) -> Vec<ToolSpec> {
    let vis = ToolVisibility {
        caller,
        folder: folder.then(|| PathBuf::from(".")),
    };
    ToolRegistry::default_registry().specs(&vis)
}

/// `conv` is the conversation calling the tool.
#[cfg(test)]
pub fn execute(db: &Connection, conv: &str, model: &str, name: &str, arguments: &str) -> String {
    let mut args: Value = serde_json::from_str(arguments).unwrap_or_else(|_| json!({}));
    strip_dir(&mut args);
    let registry = ToolRegistry::default_registry();
    let result = (|| -> Result<ToolOutput> {
        let vis = ToolVisibility {
            caller: caller(db, conv)?,
            folder: board::folder_of(db, conv)?,
        };
        let Some(tool) = registry.get(name) else {
            anyhow::bail!("unknown tool {name}");
        };
        if tool.spec(&vis).is_none() {
            let reason = tool
                .refused_message(&vis)
                .unwrap_or_else(|| format!("unknown tool {name}"));
            anyhow::bail!("{reason}");
        }
        match tool.execution() {
            Execution::Atomic => tool.run_atomic(db, conv, model, &args),
            Execution::External { .. } => tool.run_external(&vis, &args),
        }
    })();
    let out = match result {
        Ok(o) => o.text,
        Err(e) => format!("error: {e:#}"),
    };
    bound_output(out, MAX_TOOL_OUTPUT_BYTES)
}

struct ProposeTaskTool;

impl Tool for ProposeTaskTool {
    fn name(&self) -> &'static str {
        "propose_task"
    }

    fn execution(&self) -> Execution {
        Execution::Atomic
    }

    fn spec(&self, vis: &ToolVisibility) -> Option<ToolSpec> {
        (vis.caller == Caller::Chat).then(|| ToolSpec {
            name: "propose_task",
            description: "Fill in the task card shown to the user in this conversation, or change the one that is open. \
                          Fields you leave out keep their value. The user reviews the card and adds it to the board \
                          themselves, so do not also call add_task for it.",
            parameters: json!({"type": "object", "properties": field_schema()}),
        })
    }

    fn refused_message(&self, vis: &ToolVisibility) -> Option<String> {
        (vis.caller != Caller::Chat).then(|| WORKER_NO_CHANGE.into())
    }

    fn run_atomic(&self, db: &Connection, conv: &str, _model: &str, args: &Value) -> Result<ToolOutput> {
        let f = draft::propose(db, conv, args)?;
        Ok(ToolOutput::ok(format!(
            "The card now reads:\n{}\nIt is not on the board yet: the user can edit it and add it. \
             Change it again with propose_task.",
            f.describe()
        )))
    }
}

struct AddTaskTool;

impl Tool for AddTaskTool {
    fn name(&self) -> &'static str {
        "add_task"
    }

    fn execution(&self) -> Execution {
        Execution::Atomic
    }

    fn spec(&self, vis: &ToolVisibility) -> Option<ToolSpec> {
        match vis.caller {
            Caller::Chat => {
                let mut chat_add = field_schema();
                chat_add["when_done"] = when_done_prop();
                Some(ToolSpec {
                    name: "add_task",
                    description: "Put a task straight on the shared board, without the card. Use this only when the user asks \
                                  you to add it directly. With assignee \"agent\", an agent does it and its result comes back here.",
                    parameters: json!({"type": "object", "properties": chat_add, "required": ["title"]}),
                })
            }
            Caller::Run => {
                let mut props = field_schema();
                props["assignee"] = json!({"type": "string", "description": "Leave it out to hand the task to an agent. \
                    \"user\" puts it on the user's own list instead."});
                props["when_done"] = when_done_prop();
                Some(ToolSpec {
                    name: "add_task",
                    description: "Hand part of your task to another agent, one level down. It knows nothing but the \
                                  title and brief, so say the goal, what its report should contain, and where to stop. \
                                  Its result comes back to you.",
                    parameters: json!({"type": "object", "properties": props, "required": ["title"]}),
                })
            }
            Caller::RunAtFloor => None,
        }
    }

    fn refused_message(&self, vis: &ToolVisibility) -> Option<String> {
        (vis.caller == Caller::RunAtFloor)
            .then(|| "you are at the maximum delegation depth; do the work directly and call finish".into())
    }

    fn run_atomic(&self, db: &Connection, conv: &str, _model: &str, args: &Value) -> Result<ToolOutput> {
        let worker = caller(db, conv)? != Caller::Chat;
        // A run's add_task hands work on: left out, the assignee is an agent, not the user.
        let mut f = TaskFields {
            assignee: if worker { AGENT.into() } else { TaskFields::default().assignee },
            ..Default::default()
        };
        f.merge(args)?;
        let id = board::add_task(db, &f, Some(conv))?;
        let when = args["when_done"].as_str().and_then(turn::Delivery::parse);
        if let Some(when @ (turn::Delivery::Steer | turn::Delivery::AfterTurn)) = when {
            db.execute("UPDATE tasks SET report_when = ?2 WHERE id = ?1", rusqlite::params![id, when.name()])?;
        }
        Ok(ToolOutput::ok(match (f.assignee == AGENT, worker) {
            (true, false) => format!("added task {id}. {HANDED_OFF}"),
            (true, true) => format!("added task {id}. {HANDED_OFF_BY_A_RUN}"),
            _ => format!("added task {id}"),
        }))
    }
}

struct UpdateTaskTool;

impl Tool for UpdateTaskTool {
    fn name(&self) -> &'static str {
        "update_task"
    }

    fn execution(&self) -> Execution {
        Execution::Atomic
    }

    fn spec(&self, vis: &ToolVisibility) -> Option<ToolSpec> {
        (vis.caller == Caller::Chat).then(|| ToolSpec {
            name: "update_task",
            description: "Change a task that is already on the board, by its id. Fields you leave out keep their \
                          value. Setting assignee to \"agent\" hands a task that has not started to an agent. Not for \
                          the task card: change that with propose_task.",
            parameters: json!({"type": "object", "properties": with_id(field_schema()), "required": ["id"]}),
        })
    }

    fn refused_message(&self, vis: &ToolVisibility) -> Option<String> {
        (vis.caller != Caller::Chat).then(|| WORKER_NO_CHANGE.into())
    }

    fn run_atomic(&self, db: &Connection, _conv: &str, _model: &str, args: &Value) -> Result<ToolOutput> {
        let id = args["id"].as_i64().ok_or_else(|| anyhow::anyhow!("missing id"))?;
        let old = board::task(db, id)?;
        let mut f = old.fields.clone();
        f.merge(args)?;
        board::update_task(db, id, &f)?;
        let handed_off = f.assignee == AGENT && old.fields.assignee != AGENT;
        Ok(ToolOutput::ok(if handed_off {
            format!("updated task {id}. {HANDED_OFF}")
        } else {
            format!("updated task {id}")
        }))
    }
}

struct ListTasksTool;

impl Tool for ListTasksTool {
    fn name(&self) -> &'static str {
        "list_tasks"
    }

    fn execution(&self) -> Execution {
        Execution::Atomic
    }

    fn spec(&self, _vis: &ToolVisibility) -> Option<ToolSpec> {
        Some(ToolSpec {
            name: "list_tasks",
            description: "List the tasks on the shared board that are not done, with their ids, states and fields.",
            parameters: json!({"type": "object", "properties": {
                "include_done": {"type": "boolean", "description": "Also list finished tasks."}
            }}),
        })
    }

    fn run_atomic(&self, db: &Connection, _conv: &str, _model: &str, args: &Value) -> Result<ToolOutput> {
        let all = args["include_done"] == true;
        let rows: Vec<String> = board::tasks(db)?
            .iter()
            .filter(|t| all || t.state != TaskState::Done)
            .map(line)
            .collect();
        Ok(ToolOutput::ok(match (rows.is_empty(), all) {
            (true, true) => "the board is empty".into(),
            (true, false) => "no open tasks; include_done lists finished ones".into(),
            _ => rows.join("\n"),
        }))
    }
}

struct CompleteTaskTool;

impl Tool for CompleteTaskTool {
    fn name(&self) -> &'static str {
        "complete_task"
    }

    fn execution(&self) -> Execution {
        Execution::Atomic
    }

    fn spec(&self, vis: &ToolVisibility) -> Option<ToolSpec> {
        (vis.caller == Caller::Chat).then(|| ToolSpec {
            name: "complete_task",
            description: "Mark a task on the board as done, by id. Do this after doing a task yourself in your reply.",
            parameters: json!({"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]}),
        })
    }

    fn refused_message(&self, vis: &ToolVisibility) -> Option<String> {
        (vis.caller != Caller::Chat).then(|| WORKER_NO_CHANGE.into())
    }

    fn run_atomic(&self, db: &Connection, _conv: &str, _model: &str, args: &Value) -> Result<ToolOutput> {
        let id = args["id"].as_i64().ok_or_else(|| anyhow::anyhow!("missing id"))?;
        board::set_done(db, id, true)?;
        Ok(ToolOutput::ok(format!("completed task {id}")))
    }
}

struct FinishTool;

impl Tool for FinishTool {
    fn name(&self) -> &'static str {
        "finish"
    }

    fn execution(&self) -> Execution {
        Execution::Atomic
    }

    fn spec(&self, vis: &ToolVisibility) -> Option<ToolSpec> {
        (vis.caller != Caller::Chat).then(|| ToolSpec {
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
        })
    }

    fn refused_message(&self, vis: &ToolVisibility) -> Option<String> {
        (vis.caller == Caller::Chat).then(|| "unknown tool finish".into())
    }

    fn run_atomic(&self, db: &Connection, conv: &str, _model: &str, args: &Value) -> Result<ToolOutput> {
        let report = args["report"].as_str().unwrap_or_default().trim();
        if report.is_empty() {
            anyhow::bail!("finish needs your report: the result of the task, in full");
        }
        let outcome = args["outcome"].as_str().unwrap_or("done");
        if !matches!(outcome, "done" | "blocked" | "needs_review") {
            anyhow::bail!("outcome is one of done, blocked and needs_review");
        }
        // Refusals since the last report it was given; a wake starts the count again.
        let refused = crate::journal::chain(db, conv)?
            .iter()
            .rev()
            .take_while(|e| e.kind != "report")
            .filter(|e| e.kind == "tool" && e.body["name"] == "finish")
            .count();
        // Blocked: it cannot go on, so sub-tasks still out are not a reason to keep it.
        if refused < MAX_REFUSALS && outcome != "blocked" {
            let out = board::children_out(db, conv)?;
            if !out.is_empty() {
                let ids: Vec<String> = out.iter().map(|i| i.to_string()).collect();
                anyhow::bail!(
                    "not finished: task {} has not reported yet: end your turn without calling finish to wait \
                     for it, and you are woken with its result. Then call finish again.",
                    ids.join(", ")
                );
            }
        }
        db.execute("UPDATE trials SET outcome = ?2 WHERE conversation = ?1", rusqlite::params![conv, outcome])?;
        Ok(ToolOutput::ok(FINISHED))
    }
}

struct MessageTool;

impl Tool for MessageTool {
    fn name(&self) -> &'static str {
        "message"
    }

    fn execution(&self) -> Execution {
        Execution::Atomic
    }

    fn spec(&self, _vis: &ToolVisibility) -> Option<ToolSpec> {
        Some(ToolSpec {
            name: "message",
            description: "Tell someone something before you finish, without ending your task: whoever handed you \
                          the task, or the agent doing a task you handed out. Use it when they are about to waste \
                          work, or need to know now; the rest goes in your report.",
            parameters: json!({"type": "object", "properties": {
                "to": {"type": ["string", "integer"], "description": "\"parent\" for whoever handed you this task, \
                    or the id of a task you handed out."},
                "text": {"type": "string"},
                "when": {"type": "string", "enum": ["steer", "after_turn", "inbox"], "description": "When it \
                    reaches them if they are working: \"steer\" as soon as it can, cutting off what they are writing, \
                    for when they are about to waste work; \"after_turn\" (the default) when they would stop; \
                    \"inbox\" for something they only need to know, read when they are next ready and never waking them."}
            }, "required": ["to", "text"]}),
        })
    }

    fn run_atomic(&self, db: &Connection, conv: &str, _model: &str, args: &Value) -> Result<ToolOutput> {
        let text = args["text"].as_str().unwrap_or_default().trim();
        if text.is_empty() {
            anyhow::bail!("message needs its text");
        }
        let how = when(args)?;
        let msg = match &args["to"] {
            Value::String(s) if s == "parent" => message_parent(db, conv, text, how)?,
            Value::Number(n) => message_child(db, conv, n.as_i64().unwrap_or_default(), text, how)?,
            Value::String(s) => match s.trim().trim_start_matches("task").trim().parse() {
                Ok(task) => message_child(db, conv, task, text, how)?,
                Err(_) => anyhow::bail!("to is \"parent\" or the id of a task you handed out"),
            },
            _ => anyhow::bail!("to is \"parent\" or the id of a task you handed out"),
        };
        Ok(ToolOutput::ok(msg))
    }
}

struct PapercutTool;

impl Tool for PapercutTool {
    fn name(&self) -> &'static str {
        "papercut"
    }

    fn execution(&self) -> Execution {
        Execution::Atomic
    }

    fn spec(&self, _vis: &ToolVisibility) -> Option<ToolSpec> {
        Some(ToolSpec {
            name: "papercut",
            description: crate::papercut::DESCRIPTION,
            parameters: crate::papercut::parameters(),
        })
    }

    fn run_atomic(&self, db: &Connection, conv: &str, _model: &str, args: &Value) -> Result<ToolOutput> {
        Ok(ToolOutput::ok(crate::papercut::execute(db, conv, args)?))
    }
}

/// The `when` a tool was given, `after_turn` when it is left out.
pub(crate) fn when(args: &Value) -> Result<turn::Delivery> {
    match args["when"].as_str() {
        None => Ok(turn::Delivery::AfterTurn),
        Some(w) => turn::Delivery::parse(w).ok_or_else(|| anyhow::anyhow!("when is one of steer, after_turn and inbox")),
    }
}

/// The task whose run `conv` is, as (id, title).
fn own_task(db: &Connection, conv: &str) -> Result<(i64, String)> {
    Ok(db.query_row(
        "SELECT t.id, t.title FROM trials tr JOIN tasks t ON t.id = tr.task WHERE tr.conversation = ?1",
        [conv],
        |r| Ok((r.get(0)?, r.get(1)?)),
    )?)
}

/// Deliver a message to `to` and wake it if it is waiting, unless it is inbox.
fn send(db: &Connection, to: &str, said: String, from_task: i64, how: turn::Delivery) -> Result<()> {
    turn::deliver(db, to, "message", &json!({ "text": said, "from_task": from_task }), how)?;
    if how != turn::Delivery::Inbox {
        board::wake(db, to)?;
    }
    Ok(())
}

/// What `message` does with `to: "parent"`: the text goes to whoever handed
/// the run in `conv` its task, at `how`. Both a chat and a parent run are
/// reached the same way and woken unless `how` is `inbox`.
pub(crate) fn message_parent(db: &Connection, conv: &str, text: &str, how: turn::Delivery) -> Result<String> {
    let (mine, title) = own_task(db, conv)?;
    let said = format!("Message from task {mine}, \"{title}\":\n\n{text}");
    let to = match board::parent(db, conv)? {
        board::Parent::Run(p) | board::Parent::Chat(p) => p,
        board::Parent::Unreachable => {
            anyhow::bail!("whoever handed you this task cannot be reached now; say it in your report when you finish")
        }
    };
    send(db, &to, said, mine, how)?;
    Ok("sent to whoever handed you this task".into())
}

/// What `message` does with a task id: the text goes to the run doing a task `conv` handed out.
fn message_child(db: &Connection, conv: &str, task: i64, text: &str, how: turn::Delivery) -> Result<String> {
    let (mine, _) = own_task(db, conv)?;
    let Some(run) = board::child_run(db, conv, task)? else {
        anyhow::bail!("nobody is working on task {task} now");
    };
    send(db, &run, format!("Message from whoever handed you this task (task {mine}):\n\n{text}"), mine, how)?;
    Ok(format!("sent to task {task}"))
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
    use std::sync::Arc;
    use std::sync::atomic::{AtomicUsize, Ordering::SeqCst};

    /// A run files a task with the same call a chat uses, one level down. At the
    /// floor delegation is not offered.
    #[test]
    fn a_run_at_the_floor_cannot_delegate() -> Result<()> {
        let path = std::env::temp_dir().join(format!("arity-depth-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path)?;
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
        assert_eq!(names("task-1"), ["add_task", "list_tasks", "finish", "message", "papercut"]);
        assert!(add_props("task-1")["properties"]["assignee"].is_object(), "above the floor it may delegate");
        let said = call("task-1", "add_task", json!({ "title": "sub" }));
        assert!(said.starts_with("added task 2") && said.contains("comes back here"), "{said}");
        assert_eq!(board::task(c, 2)?.created_by, "conversations/task-1");
        // Task 2's run is at the floor: no add_task, no complete_task, no rate.
        run(2)?;
        assert_eq!(names("task-2"), ["list_tasks", "finish", "message", "papercut"]);
        assert!(call("task-2", "add_task", json!({ "title": "deeper" })).contains("maximum delegation depth"));
        assert!(call("task-1", "rate", json!({ "task": 2, "score": 5, "note": "x" })).starts_with("error:"), "rate is a hook, not a tool");
        assert!(call("task-1", "complete_task", json!({ "id": 1 })).starts_with("error:"), "runs change nothing");
        // A private run's tasks stay private, whatever it asks for.
        c.execute("UPDATE tasks SET choice = 'private' WHERE id = 1", [])?;
        assert!(call("task-1", "add_task", json!({ "title": "another", "assignee": "agent", "choice": "smart" })).starts_with("added task 3"));
        assert_eq!(board::task(c, 3)?.fields.choice, board::Choice::Private);
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    /// The file tools are offered only with a folder attached, and refused without one.
    #[test]
    fn file_tools_only_with_a_folder() -> Result<()> {
        let path = std::env::temp_dir().join(format!("arity-filetools-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path)?;
        let c = db.conn();
        let offered = |conv: &str| -> Vec<&'static str> {
            let folder = board::folder_of(c, conv).unwrap().is_some();
            specs(caller(c, conv).unwrap(), folder).iter().map(|t| t.name).collect()
        };
        let call = |conv: &str, tool: &str, args: Value| execute(c, conv, "lab:m", tool, &args.to_string());
        let folder_tools = ["list_files", "read_file", "search_files", "write_file", "edit_file", "bash"];
        for name in folder_tools {
            assert!(!offered("chat").contains(&name));
        }
        assert!(call("chat", "list_files", json!({})).contains("no folder is attached"));
        let repo = std::env::temp_dir().join(format!("arity-filetools-repo-{}", std::process::id()));
        std::fs::create_dir_all(&repo)?;
        std::fs::write(repo.join("notes.txt"), "hello\n")?;
        board::set_folder(c, "chat", repo.to_str())?;
        assert!(offered("chat").ends_with(&folder_tools));
        assert_eq!(call("chat", "list_files", json!({})), "notes.txt");
        // A model cannot point a task somewhere else: the chat's folder passes down.
        call("chat", "add_task", json!({ "title": "look", "assignee": "agent", "dir": "/" }));
        c.execute("INSERT INTO trials (task, conversation, model) VALUES (1, 'task-1', 'lab:m')", [])?;
        assert_eq!(board::folder_of(c, "task-1")?, Some(std::fs::canonicalize(&repo)?));
        assert!(offered("task-1").contains(&"read_file"));
        assert!(call("task-1", "read_file", json!({ "path": "notes.txt" })).contains("1  hello"));
        let _ = std::fs::remove_file(&path);
        let _ = std::fs::remove_dir_all(&repo);
        Ok(())
    }

    /// A run messages whoever handed it its task, or the run of a task it
    /// handed out, at a timing; a waiting parent (run or chat) is woken unless
    /// it is inbox. Blocked finishes even with work open.
    #[test]
    fn runs_message_up_and_down_and_can_finish_blocked() -> Result<()> {
        let path = std::env::temp_dir().join(format!("arity-message-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path)?;
        let c = db.conn();
        let call = |conv: &str, tool: &str, args: Value| execute(c, conv, "lab:m", tool, &args.to_string());
        // A chat files task 1; its run hands task 2 to an agent, and task 2's run starts.
        db.append("chat", "assistant", &json!({ "text": "on it" }), Some("chat:m"))?;
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
        // Up, steer: it is woken.
        call("task-2", "message", json!({ "to": "parent", "text": "the repo does not exist", "when": "steer" }));
        assert!(!waiting("task-1"));
        assert_eq!(db.chain("task-1")?.iter().filter(|e| e.kind == "message").count(), 2);
        // Task 1's parent is the chat: reached the same way and woken when not inbox.
        assert!(call("task-1", "message", json!({ "to": "parent", "text": "halfway" })).starts_with("sent"));
        assert!(db.chain("chat")?.last().unwrap().body["text"].as_str().unwrap().contains("halfway"));
        let wake_at: i64 = c.query_row("SELECT wake_at FROM conversations WHERE name = 'chat'", [], |r| r.get(0))?;
        assert_eq!(wake_at, 1);
        assert!(call("task-1", "message", json!({ "to": "parent", "text": "x", "when": "soon" })).contains("when is one of"));
        // Blocked: let through though task 2 is still out; done is refused for it.
        assert!(call("task-1", "finish", json!({ "report": "r" })).contains("task 2 has not reported"));
        assert_eq!(call("task-1", "finish", json!({ "report": "need a key", "outcome": "blocked" })), FINISHED);
        let outcome: String = c.query_row("SELECT outcome FROM trials WHERE conversation = 'task-1'", [], |r| r.get(0))?;
        assert_eq!(outcome, "blocked");
        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    struct MockExternalTool {
        name: &'static str,
        replay: Replay,
        runs: Arc<AtomicUsize>,
        db_path: Option<PathBuf>,
    }

    impl Tool for MockExternalTool {
        fn name(&self) -> &'static str {
            self.name
        }

        fn execution(&self) -> Execution {
            Execution::External { replay: self.replay }
        }

        fn spec(&self, _vis: &ToolVisibility) -> Option<ToolSpec> {
            Some(ToolSpec {
                name: self.name,
                description: "mock external tool",
                parameters: json!({"type": "object", "properties": {"val": {"type": "string"}}}),
            })
        }

        fn run_external(&self, _vis: &ToolVisibility, args: &Value) -> Result<ToolOutput> {
            // Verify SQLite is not write-locked while an external tool executes.
            if let Some(path) = &self.db_path {
                let other = Journal::open(path)?;
                other.append("heartbeat-probe", "notice", &json!({ "text": "alive" }), None)?;
            }
            let n = self.runs.fetch_add(1, SeqCst) + 1;
            let val = args["val"].as_str().unwrap_or("default");
            Ok(ToolOutput::ok(format!("ran:{}:{val}:{n}", self.name)))
        }
    }

    #[test]
    fn external_tool_commits_intent_and_respects_replay_safe_vs_unsafe() -> Result<()> {
        let path = std::env::temp_dir().join(format!("arity-external-replay-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path)?;

        let safe_runs = Arc::new(AtomicUsize::new(0));
        let unsafe_runs = Arc::new(AtomicUsize::new(0));

        let reg = ToolRegistry::new()
            .with(Box::new(MockExternalTool {
                name: "ext_safe",
                replay: Replay::Safe,
                runs: safe_runs.clone(),
                db_path: Some(path.clone()),
            }))
            .with(Box::new(MockExternalTool {
                name: "ext_unsafe",
                replay: Replay::Unsafe,
                runs: unsafe_runs.clone(),
                db_path: Some(path.clone()),
            }));

        // Fresh external call commits intent, runs outside any SQLite tx, then commits tool result.
        let fresh = ToolCall {
            id: "call-fresh".into(),
            name: "ext_safe".into(),
            arguments: json!({ "val": "one" }).to_string(),
        };
        let out = reg.execute_call(&db, "conv", "m", &fresh)?;
        assert!(!out.is_error);
        assert_eq!(out.text, "ran:ext_safe:one:1");
        assert_eq!(safe_runs.load(SeqCst), 1);
        let chain = db.chain("conv")?;
        assert_eq!(chain.len(), 2);
        assert_eq!(chain[0].kind, "tool_intent");
        assert_eq!(chain[0].body["call_id"], "call-fresh");
        assert_eq!(chain[0].body["replay"], "safe");
        assert_eq!(chain[1].kind, "tool");
        assert_eq!(chain[1].body["call_id"], "call-fresh");
        assert_eq!(chain[1].body["is_error"], false);

        // Simulated crash for Replay::Safe: tool_intent exists without a tool result -> reruns cleanly.
        db.append(
            "conv",
            "tool_intent",
            &json!({ "call_id": "call-crashed-safe", "name": "ext_safe", "replay": "safe" }),
            None,
        )?;
        let crashed_safe = ToolCall {
            id: "call-crashed-safe".into(),
            name: "ext_safe".into(),
            arguments: json!({ "val": "retry" }).to_string(),
        };
        let out = reg.execute_call(&db, "conv", "m", &crashed_safe)?;
        assert!(!out.is_error);
        assert_eq!(out.text, "ran:ext_safe:retry:2");
        assert_eq!(safe_runs.load(SeqCst), 2);
        let intents_for_safe = db
            .chain("conv")?
            .iter()
            .filter(|e| e.kind == "tool_intent" && e.body["call_id"] == "call-crashed-safe")
            .count();
        assert_eq!(intents_for_safe, 1, "should not duplicate existing tool_intent");

        // Simulated crash for Replay::Unsafe: tool_intent exists without a tool result -> refused without rerunning.
        db.append(
            "conv",
            "tool_intent",
            &json!({ "call_id": "call-crashed-unsafe", "name": "ext_unsafe", "replay": "unsafe" }),
            None,
        )?;
        let crashed_unsafe = ToolCall {
            id: "call-crashed-unsafe".into(),
            name: "ext_unsafe".into(),
            arguments: json!({ "val": "nope" }).to_string(),
        };
        let out = reg.execute_call(&db, "conv", "m", &crashed_unsafe)?;
        assert!(out.is_error);
        assert!(out.text.contains("interrupted by a crash") && out.text.contains("not replay-safe"), "{}", out.text);
        assert_eq!(unsafe_runs.load(SeqCst), 0, "unsafe tool must not re-run after crash");
        let last = db.chain("conv")?.pop().unwrap();
        assert_eq!(last.kind, "tool");
        assert_eq!(last.body["call_id"], "call-crashed-unsafe");
        assert_eq!(last.body["is_error"], true);

        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    #[test]
    fn run_with_hooks_supports_rewrite_block_and_after_mutation() -> Result<()> {
        let path = std::env::temp_dir().join(format!("arity-hooks-{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&path);
        let db = Journal::open(&path)?;

        let runs = Arc::new(AtomicUsize::new(0));
        let reg = ToolRegistry::default_registry().with(Box::new(MockExternalTool {
            name: "ext_hook",
            replay: Replay::Unsafe,
            runs: runs.clone(),
            db_path: None,
        }));

        // 1. RewriteArgs + after mutation on an external tool.
        let rewrite_call = ToolCall {
            id: "call-rw".into(),
            name: "ext_hook".into(),
            arguments: json!({ "val": "orig" }).to_string(),
        };
        let out = reg.run_with_hooks(
            &db,
            "chat",
            "m",
            &rewrite_call,
            |_call, args| {
                assert_eq!(args["val"], "orig");
                Ok(ToolHookDecision::RewriteArgs(json!({ "val": "rewritten" })))
            },
            |_call, out| {
                out.text.push_str(" [audited]");
                out.details = Some(json!({ "audited": true }));
                Ok(())
            },
        )?;
        assert!(!out.is_error);
        assert_eq!(out.text, "ran:ext_hook:rewritten:1 [audited]");
        assert_eq!(runs.load(SeqCst), 1);
        let last = db.chain("chat")?.pop().unwrap();
        assert_eq!(last.kind, "tool");
        assert_eq!(last.body["output"], "ran:ext_hook:rewritten:1 [audited]");
        assert_eq!(last.body["details"]["audited"], true);

        // 2. Block prevents tool_intent and execution, commits blocked error, and still runs `after`.
        let block_call = ToolCall {
            id: "call-blk".into(),
            name: "ext_hook".into(),
            arguments: json!({ "val": "danger" }).to_string(),
        };
        let out = reg.run_with_hooks(
            &db,
            "chat",
            "m",
            &block_call,
            |_call, _args| Ok(ToolHookDecision::Block("policy denied".into())),
            |_call, out| {
                assert!(out.is_error);
                assert_eq!(out.text, "error: blocked: policy denied");
                out.text.push_str(" (logged)");
                Ok(())
            },
        )?;
        assert!(out.is_error);
        assert_eq!(out.text, "error: blocked: policy denied (logged)");
        assert_eq!(runs.load(SeqCst), 1, "blocked tool must not execute");
        let chain = db.chain("chat")?;
        assert!(
            !chain.iter().any(|e| e.kind == "tool_intent" && e.body["call_id"] == "call-blk"),
            "blocked tool must not record tool_intent"
        );
        let last = chain.last().unwrap();
        assert_eq!(last.kind, "tool");
        assert_eq!(last.body["call_id"], "call-blk");
        assert_eq!(last.body["output"], "error: blocked: policy denied (logged)");
        assert_eq!(last.body["is_error"], true);

        // 3. RewriteArgs + after mutation also work on Atomic board tools.
        let atomic_call = ToolCall {
            id: "call-atomic".into(),
            name: "add_task".into(),
            arguments: json!({ "title": "old title" }).to_string(),
        };
        let out = reg.run_with_hooks(
            &db,
            "chat",
            "m",
            &atomic_call,
            |_call, _args| Ok(ToolHookDecision::RewriteArgs(json!({ "title": "new title" }))),
            |_call, out| {
                out.text.push_str(" [hooked]");
                Ok(())
            },
        )?;
        assert!(!out.is_error);
        assert!(out.text.starts_with("added task 1") && out.text.ends_with(" [hooked]"), "{}", out.text);
        assert_eq!(board::task(db.conn(), 1)?.fields.title, "new title");
        let last = db.chain("chat")?.pop().unwrap();
        assert_eq!(last.body["output"], out.text);

        let _ = std::fs::remove_file(&path);
        Ok(())
    }

    #[test]
    fn bound_output_truncates_utf8_safely() {
        assert_eq!(bound_output("hello".into(), 5), "hello");
        assert_eq!(bound_output("hello".into(), 10), "hello");

        // Each '🦀' is 4 UTF-8 bytes; truncating at 5 bytes must back up to byte 4 (1 crab).
        let crabs = "🦀🦀🦀".to_string(); // 12 bytes
        let bounded = bound_output(crabs, 5);
        assert_eq!(bounded, "🦀\n[output truncated: 8 bytes omitted]");

        // Truncating inside the first multi-byte char (max_bytes = 2) backs up to 0 cleanly.
        let bounded_zero = bound_output("é".repeat(5), 1); // 10 bytes
        assert_eq!(bounded_zero, "\n[output truncated: 10 bytes omitted]");
    }
}
