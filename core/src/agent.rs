//! The loop: call the model, run the tools it asks for, repeat until it answers
//! without calling a tool. Each step is decided purely from the journal, so a
//! fresh process after a crash makes the same decision the dead one would have.

use std::path::Path;

use anyhow::{Result, bail};

use crate::journal::{Entry, Journal};
use crate::model::{Model, ToolCall, tool_calls_of};
use crate::{Message, messages_of, tools};

const SYSTEM: &str = "You are an assistant inside Arity. You share a board of tasks with the user \
and other agents, and you read and change it only through your tools. Be brief.";

const MAX_STEPS: usize = 16;

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

pub fn run(db: &Journal, data_dir: &Path, conv: &str, spec: &str, on: &mut dyn FnMut(&Message)) -> Result<()> {
    let model = Model::parse(spec, data_dir)?;
    for _ in 0..MAX_STEPS {
        let chain = db.chain(conv)?;
        match next_step(&chain) {
            Step::CallModel => {
                let reply = model.complete(SYSTEM, &chain, &tools::specs())?;
                db.append(conv, "assistant", &reply.to_json(), Some(spec))?;
            }
            Step::RunTool(call) => {
                db.run_tool(conv, &call)?;
            }
            Step::Done => return Ok(()),
        }
        if let Some(e) = db.chain(conv)?.last() {
            messages_of(e).iter().for_each(&mut *on);
        }
    }
    bail!("stopped after {MAX_STEPS} steps")
}
