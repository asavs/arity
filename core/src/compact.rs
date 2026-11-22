//! Compaction and reset: ways to give the model a smaller context without
//! rewriting the journal. Both append an immutable head marker (see
//! `journal::model_view`); the transcript, forks and hooks still read every entry.
//!
//! - `"compact"` holds a summary of what the model read so far, written by the
//!   model itself, and the model reads that summary in place of those entries.
//! - `"reset"` drops everything before it from the model's context.
//!
//! The summary is asked for over a plain-text transcript rather than the
//! conversation's own messages, so a context that is already too long can still
//! be summarized: each try clips the transcript harder.

use anyhow::{Result, bail};
use serde_json::{Value, json};

use crate::agent::Complete;
use crate::journal::{Entry, Journal};
use crate::model::{TokenUsage, tool_calls_of};

pub const SUMMARIZER_SYSTEM: &str = "You write handoff summaries of conversations between a user and an AI \
assistant working inside Arity. The assistant will continue the conversation from your summary alone, \
so keep everything it needs and nothing it does not.";

const SUMMARY_REQUEST: &str = "Summarize the conversation below so the assistant can carry on from the summary alone. \
Use these headings, skipping any with nothing under them:
## Goal
## Decisions and facts
## Files and changes
## Current state
## Open issues
## Next steps
Keep exact names, paths, ids, commands, numbers and error messages. Be concise.";

/// How hard each try clips the transcript: (characters kept of a tool result, of any other entry, in all).
const BUDGETS: [(usize, usize, usize); 3] = [(4_000, 12_000, 400_000), (1_000, 4_000, 120_000), (300, 1_500, 40_000)];

/// Whether a failed model call failed because the prompt was longer than the model's context.
pub fn is_context_overflow(e: &anyhow::Error) -> bool {
    let text = format!("{e:#}").to_lowercase();
    [
        "context_length_exceeded",
        "maximum context length",
        "context length",
        "context window",
        "prompt is too long",
        "input is too long",
        "too many tokens",
        "exceeds the context",
    ]
    .iter()
    .any(|p| text.contains(p))
}

/// The prompt size of the model's latest reply in `view`, or 0 if none reported one.
pub fn last_prompt_tokens(view: &[Entry]) -> u64 {
    last_usage(view).map_or(0, |u| u.input)
}

pub fn last_usage(entries: &[Entry]) -> Option<TokenUsage> {
    entries.iter().rev().filter(|e| e.kind == "assistant").find_map(|e| TokenUsage::from_json(&e.body["usage"]))
}

/// The model's replies in `view`: one per step it took.
pub fn replies(view: &[Entry]) -> usize {
    view.iter().filter(|e| e.kind == "assistant").count()
}

/// Summarize what the model reads in `conv` and append a `"compact"` entry holding the summary.
/// `carry_on` marks a compact made part way through a turn, which the turn then continues.
/// `stopped` cuts the summary short, and the compact is then not made.
pub fn compact(
    db: &Journal,
    conv: &str,
    complete: &Complete,
    instructions: Option<&str>,
    carry_on: bool,
    stopped: &dyn Fn() -> bool,
) -> Result<String> {
    let view = db.chain_for_model(conv)?;
    let summarized = view.iter().filter(|e| e.kind != "system").count();
    if summarized == 0 {
        bail!("nothing to compact in {conv}");
    }
    let summary = summarize(complete, &view, instructions, stopped)?;
    let mut body = json!({ "summary": summary, "head_entry": Value::Null, "compacted_count": summarized });
    if carry_on {
        body["continue"] = json!(true);
    }
    if let Some(i) = instructions.map(str::trim).filter(|i| !i.is_empty()) {
        body["instructions"] = json!(i);
    }
    db.append(conv, "compact", &body, None)?;
    Ok(summary)
}

/// Append a `"reset"` entry: the model starts again from here, with only the context sections.
pub fn reset(db: &Journal, conv: &str, note: Option<&str>) -> Result<i64> {
    let note = note.map(str::trim).filter(|n| !n.is_empty());
    db.append(conv, "reset", &json!({ "note": note, "head_entry": Value::Null }), None)
}

/// The model's summary of `view`, retried on a tighter transcript while the summary call itself overflows.
pub fn summarize(complete: &Complete, view: &[Entry], instructions: Option<&str>, stopped: &dyn Fn() -> bool) -> Result<String> {
    let mut last_err = None;
    for budget in BUDGETS {
        let mut request = SUMMARY_REQUEST.to_string();
        if let Some(extra) = instructions.map(str::trim).filter(|i| !i.is_empty()) {
            request.push_str(&format!("\n\nAlso: {extra}"));
        }
        request.push_str(&format!("\n\n<transcript>\n{}\n</transcript>", transcript(view, budget)));
        let ask = [Entry { id: 0, kind: "user".into(), body: json!({ "text": request }), model: None }];
        match complete(SUMMARIZER_SYSTEM, &ask, &[], &mut |_| !stopped()) {
            Ok(reply) if reply.interrupted => bail!("compaction was stopped"),
            Ok(reply) if reply.text.trim().is_empty() => bail!("the model returned an empty summary"),
            Ok(reply) => return Ok(reply.text.trim().to_string()),
            Err(e) if is_context_overflow(&e) => last_err = Some(e),
            Err(e) => return Err(e),
        }
    }
    Err(last_err.expect("at least one try").context("even a clipped transcript was too long to summarize"))
}

/// `view` as plain text, each entry clipped to its share of `budget`. When the whole is still too
/// long, the first entry (usually what was asked) and the newest ones are kept.
fn transcript(view: &[Entry], (tool_cap, text_cap, total_cap): (usize, usize, usize)) -> String {
    let mut pieces: Vec<String> = Vec::new();
    for e in view {
        let text = |key: &str| e.body[key].as_str().unwrap_or_default().trim().to_string();
        match e.kind.as_str() {
            "user" => pieces.push(format!("User: {}", clip(&text("text"), text_cap))),
            "report" | "message" => pieces.push(format!("Report from another agent: {}", clip(&text("text"), text_cap))),
            "notice" => pieces.push(format!("Arity: {}", clip(&text("text"), text_cap))),
            "draft" => pieces.push(format!("Task card edit: {}", clip(&e.body.to_string(), text_cap))),
            "compact" => pieces.push(format!("Summary of what came before:\n{}", clip(&text("summary"), text_cap * 2))),
            "assistant" => {
                let said = text("text");
                if !said.is_empty() {
                    pieces.push(format!("Assistant: {}", clip(&said, text_cap)));
                }
                for c in tool_calls_of(&e.body) {
                    pieces.push(format!("Assistant called {}({})", c.name, clip(&c.arguments, tool_cap)));
                }
            }
            "tool" => pieces.push(format!("Result of {}: {}", text("name"), clip(&text("output"), tool_cap))),
            _ => {}
        }
    }
    let joined = pieces.join("\n\n");
    if joined.len() <= total_cap || pieces.len() < 2 {
        return clip(&joined, total_cap);
    }
    let first = pieces.remove(0);
    let mut room = total_cap.saturating_sub(first.len());
    let mut tail = Vec::new();
    while let Some(p) = pieces.pop() {
        if p.len() + 2 > room {
            pieces.push(p);
            break;
        }
        room -= p.len() + 2;
        tail.push(p);
    }
    tail.reverse();
    let omitted = if pieces.is_empty() { String::new() } else { format!("\n\n[{} entries omitted]", pieces.len()) };
    format!("{first}{omitted}\n\n{}", tail.join("\n\n"))
}

fn clip(s: &str, cap: usize) -> String {
    match s.char_indices().nth(cap) {
        Some((i, _)) => format!("{}… [{} more characters]", &s[..i], s[i..].chars().count()),
        None => s.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Reply;

    fn e(id: i64, kind: &str, body: Value) -> Entry {
        Entry { id, kind: kind.into(), body, model: None }
    }

    #[test]
    fn context_overflow_errors_are_recognized() {
        for msg in [
            "400 Bad Request: {\"error\":{\"code\":\"context_length_exceeded\"}}",
            "This model's maximum context length is 128000 tokens",
            "codex error: {\"type\":\"response.failed\",\"error\":{\"message\":\"Your input exceeds the context window\"}}",
            "prompt is too long: 210000 tokens > 200000 maximum",
        ] {
            assert!(is_context_overflow(&anyhow::anyhow!("{msg}")), "{msg}");
        }
        assert!(!is_context_overflow(&anyhow::anyhow!("401 Unauthorized")));
        assert!(!is_context_overflow(&anyhow::anyhow!("connection refused")));
    }

    #[test]
    fn the_transcript_clips_long_entries_and_keeps_the_first_and_newest() {
        let mut view = vec![e(1, "user", json!({ "text": "the brief" }))];
        for i in 0..50 {
            view.push(e(2 + i, "tool", json!({ "name": "read_file", "output": "x".repeat(5_000) })));
        }
        view.push(e(99, "assistant", json!({ "text": "latest words" })));
        let roomy = transcript(&view, BUDGETS[0]);
        assert!(roomy.contains("[1000 more characters]"), "tool results are clipped to 4000");
        let tight = transcript(&view, (300, 1_500, 5_000));
        assert!(tight.len() < 6_000, "{}", tight.len());
        assert!(tight.starts_with("User: the brief") && tight.ends_with("Assistant: latest words"));
        assert!(tight.contains("entries omitted]"));
        assert_eq!(clip("héllo", 2), "hé… [3 more characters]");
    }

    #[test]
    fn a_summary_that_overflows_is_retried_on_a_tighter_transcript() -> Result<()> {
        let view = vec![
            e(1, "user", json!({ "text": "a".repeat(50_000) })),
            e(2, "assistant", json!({ "text": "ok" })),
        ];
        let tries = std::cell::RefCell::new(vec![]);
        let model = |system: &str, chain: &[Entry], tools: &[crate::tools::ToolSpec], _: &mut crate::model::Sink| -> Result<Reply> {
            assert_eq!((system, tools.len(), chain.len()), (SUMMARIZER_SYSTEM, 0, 1));
            let len = chain[0].body["text"].as_str().unwrap().len();
            tries.borrow_mut().push(len);
            if len > 3_000 {
                bail!("400: This model's maximum context length is 4096 tokens");
            }
            Ok(Reply { text: " ## Goal\nAnswer. ".into(), tool_calls: vec![], interrupted: false, usage: None })
        };
        assert_eq!(summarize(&model, &view, Some("focus on files"), &|| false)?, "## Goal\nAnswer.");
        let tries = tries.borrow();
        assert_eq!(tries.len(), 3);
        assert!(tries[0] > tries[1] && tries[1] > tries[2]);

        let failing = |_: &str, _: &[Entry], _: &[crate::tools::ToolSpec], _: &mut crate::model::Sink| -> Result<Reply> { bail!("401 Unauthorized") };
        assert!(summarize(&failing, &view, None, &|| false).unwrap_err().to_string().contains("401"), "other errors are not retried");
        Ok(())
    }
}
