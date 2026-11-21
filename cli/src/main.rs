//! `arity`, the command-line front end. Everything it does goes through
//! `arity_core::Arity`, the same object the Mac app uses.

use arity_core::{Arity, BoardTask, Clearance, Message, MessageKind, Priority, RunState, TaskFields, TaskState};
use serde_json::{Value, json};

const USAGE: &str = "usage:
  arity ls                                   conversations, newest first
  arity log    <conv>                        read a conversation
  arity chat   <conv> <model> <message...>   send a message; <model> may be - for the last one used
  arity resume <conv> [model]                carry on a conversation that stopped
  arity fork   <entry> <new-conv>            branch a conversation at an entry
  arity board                                the shared task board
  arity task   <title...> [fields] [-- brief] add a task; --agent hands it to a blindly picked model
  arity edit   <task> [fields] [-- brief]    change a task; --agent hands it off
  arity done   <task>                        mark a task done
  arity draft  <conv>                        the task card open in a conversation
  arity draft  <conv> --new                  open an empty card; the next model turn helps fill it
  arity draft  <conv> [fields] [-- brief]    edit the card
  arity draft  <conv> --add | --dismiss      put the card on the board, or close it
  arity work   [--once]                      run tasks for agents and deliver their results
  arity runs                                 runs, models hidden until rated
  arity rate   <run> <1-5> <note...>         rate a run yourself
  arity scores                               average rating per model
  arity reveal <run>                         which model did a run
  arity models                               suggested models (any mlx:<id> or codex:<id> works)
  arity login                                sign in with ChatGPT

fields: --title <t>  --agent | --me  --local | --any  --priority low|normal|high|urgent
        --due YYYY-MM-DD  --blocked-by <id,id>  --tag <t> (repeatable)  --no-tags

data: $ARITY_HOME, or ~/Library/Application Support/Arity";

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Err(e) = run(&args) {
        eprintln!("arity: {e}");
        std::process::exit(1);
    }
}

fn run(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let arity = Arity::new(None);
    let arg = |i: usize| args.get(i).cloned().unwrap_or_default();
    let model_for = |conv: &str, given: String| -> Result<String, Box<dyn std::error::Error>> {
        if !given.is_empty() && given != "-" {
            return Ok(given);
        }
        arity.last_model(conv.into())?.ok_or_else(|| format!("{conv} has no model yet; name one").into())
    };
    match (arg(0).as_str(), args.len()) {
        ("ls", 1) => {
            for c in arity.conversations()? {
                println!("{:<24} {}  (head #{})", c.name, c.updated, c.head);
            }
        }
        ("log", 2) => arity.messages(arg(1))?.iter().for_each(print),
        ("chat", n) if n >= 4 => {
            let model = model_for(&arg(1), arg(2))?;
            arity.send_with(&arg(1), &model, &args[3..].join(" "), &mut |m| print(m))?;
        }
        ("resume", 2 | 3) => {
            let model = model_for(&arg(1), arg(2))?;
            arity.resume_with(&arg(1), &model, &mut |m| print(m))?;
        }
        ("fork", 3) => {
            arity.fork(arg(1).parse()?, arg(2))?;
            println!("{} now branches from entry #{}", arg(2), arg(1));
        }
        ("board", 1) => {
            for t in arity.tasks()? {
                println!("{}", board_line(&t, &arity.me()));
                if let Some(r) = t.result.as_ref().filter(|_| t.run_state == Some(RunState::NeedsRating)) {
                    println!("{:>8}{}", "", r.replace('\n', "\n        "));
                }
            }
        }
        ("task", n) if n >= 2 => {
            let (words, fields) = parse_fields(&args[1..])?;
            let mut f = TaskFields { title: words.join(" "), ..Default::default() };
            f.merge(&fields)?;
            let agent = f.assignee == "agent";
            let id = arity.add_task(f)?;
            println!("added task {id}{}", if agent { " for an agent; `arity work` or the app will run it" } else { "" });
        }
        ("edit", n) if n >= 3 => {
            let id: i64 = arg(1).parse()?;
            let (words, fields) = parse_fields(&args[2..])?;
            if !words.is_empty() {
                return Err(format!("unexpected {:?}; use --title to rename", words.join(" ")).into());
            }
            let mut f = arity.tasks()?.into_iter().find(|t| t.id == id).ok_or(format!("no task {id}"))?.fields;
            f.merge(&fields)?;
            arity.update_task(id, f)?;
            println!("updated task {id}");
        }
        ("done", 2) => arity.set_task_done(arg(1).parse()?, true)?,
        ("draft", n) if n >= 2 => {
            let conv = arg(1);
            let rest = &args[2..];
            if rest.iter().any(|a| a == "--new") {
                arity.open_draft(conv.clone())?;
            } else if rest.iter().any(|a| a == "--add") {
                let id = arity.add_draft(conv.clone())?;
                println!("added task {id} from {conv}'s card");
                return Ok(());
            } else if rest.iter().any(|a| a == "--dismiss") {
                arity.dismiss_draft(conv.clone())?;
                println!("dismissed {conv}'s card");
                return Ok(());
            } else if !rest.is_empty() {
                let (words, fields) = parse_fields(rest)?;
                if !words.is_empty() {
                    return Err(format!("unexpected {:?}; use --title to rename", words.join(" ")).into());
                }
                let mut f = arity.draft(conv.clone())?.ok_or(format!("{conv} has no task card open; --new opens one"))?;
                f.merge(&fields)?;
                arity.edit_draft(conv.clone(), f)?;
            }
            match arity.draft(conv.clone())? {
                Some(f) => println!("{}", f.describe()),
                None => println!("{conv} has no task card open"),
            }
        }
        ("work", 1 | 2) => {
            let once = arg(1) == "--once";
            loop {
                match arity.work_once()? {
                    Some(line) => println!("{line}"),
                    None if once => break,
                    None => std::thread::sleep(std::time::Duration::from_secs(1)),
                }
            }
        }
        ("runs", 1) => {
            for r in arity.runs()? {
                let score = r.score.map(|s| format!("{s}/5")).unwrap_or_else(|| if r.ended.is_some() { "unrated".into() } else { "running".into() });
                let model = r.model.as_deref().unwrap_or("(hidden)");
                println!("run {:<3} task {:<3} {:<8} {:<40} {}", r.id, r.task, score, r.title, model);
            }
        }
        ("rate", n) if n >= 4 => {
            arity.rate(arg(1).parse()?, arg(2).parse()?, args[3..].join(" "))?;
            println!("rated run {}; it was {}", arg(1), arity.reveal(arg(1).parse()?)?);
        }
        ("scores", 1) => {
            for s in arity.scores()? {
                println!("{:>5.2}  {:>3} rated  {}", s.average, s.rated, s.model);
            }
        }
        ("reveal", 2) => println!("{}", arity.reveal(arg(1).parse()?)?),
        ("models", 1) => arity.suggested_models().iter().for_each(|m| println!("{m}")),
        ("login", 1) => println!("Signed in. ChatGPT account {}.", arity.sign_in_chatgpt()?),
        _ => println!("{USAGE}"),
    }
    Ok(())
}

fn print(m: &Message) {
    let who = m.model.as_deref().unwrap_or("model");
    match m.kind {
        MessageKind::User => println!("#{:<4} you: {}", m.entry, m.text),
        MessageKind::Assistant => println!("#{:<4} {who}: {}", m.entry, m.text),
        MessageKind::ToolCall => println!("#{:<4} {who} calls {}", m.entry, m.text),
        MessageKind::Report => println!("#{:<4} report: {}", m.entry, m.text.replace('\n', "\n          ")),
        MessageKind::Notice => println!("#{:<4} arity: {}", m.entry, m.text),
        MessageKind::TaskCard => println!("#{:<4} card: {}", m.entry, m.text),
        MessageKind::ToolResult => println!("#{:<4}   -> {}", m.entry, m.text.replace('\n', "\n          ")),
    }
}

/// Split arguments into plain words (a title) and field flags, as the JSON the
/// core's tools take. Everything after `--` is the brief.
fn parse_fields(args: &[String]) -> Result<(Vec<String>, Value), Box<dyn std::error::Error>> {
    let mut words = vec![];
    let mut fields = json!({});
    let mut tags: Option<Vec<String>> = None;
    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut value = |flag: &str| it.next().cloned().ok_or_else(|| format!("{flag} needs a value"));
        match a.as_str() {
            "--" => {
                fields["brief"] = json!(it.by_ref().cloned().collect::<Vec<_>>().join(" "));
            }
            "--title" => fields["title"] = json!(value(a)?),
            "--agent" => fields["assignee"] = json!("agent"),
            "--me" => fields["assignee"] = json!("user"),
            "--for" => fields["assignee"] = json!(value(a)?),
            "--local" => fields["clearance"] = json!("local"),
            "--any" => fields["clearance"] = json!("any"),
            "--priority" => fields["priority"] = json!(value(a)?),
            "--due" => fields["due"] = json!(value(a)?),
            "--no-due" => fields["due"] = Value::Null,
            "--blocked-by" => {
                let ids: Vec<Value> = value(a)?.split(',').filter(|s| !s.is_empty()).map(|s| json!(s.trim())).collect();
                fields["blocked_by"] = json!(ids);
            }
            "--tag" => tags.get_or_insert_with(Vec::new).push(value(a)?),
            "--no-tags" => tags = Some(vec![]),
            flag if flag.starts_with("--") => return Err(format!("unknown flag {flag}").into()),
            word => words.push(word.to_string()),
        }
    }
    if let Some(tags) = tags {
        fields["tags"] = json!(tags);
    }
    Ok((words, fields))
}

/// `  3 ready     for agent   write a poem  · high · due 2026-12-01 · blocked by 2 · #fun`
fn board_line(t: &BoardTask, me: &str) -> String {
    let f = &t.fields;
    let state = match t.state {
        TaskState::Open => "open",
        TaskState::Ready => "ready",
        TaskState::Assigned => "assigned",
        TaskState::InGates => "in gates",
        TaskState::Done => "done",
    };
    let who = if f.assignee == me { "for me".to_string() } else { format!("for {}", f.assignee) };
    let mut extra = vec![];
    match t.run_state {
        Some(RunState::Running) => extra.push("running".to_string()),
        Some(RunState::Failing) => extra.push("retrying".into()),
        Some(RunState::NeedsRating) => extra.push(format!("needs rating (run {})", t.run.unwrap_or_default())),
        Some(RunState::Rated) => extra.push(format!("rated {}/5", t.score.unwrap_or_default())),
        None => {}
    }
    if f.priority != Priority::Normal {
        extra.push(format!("{:?}", f.priority).to_lowercase());
    }
    if f.clearance == Clearance::Local {
        extra.push("local only".into());
    }
    if let Some(d) = &f.due {
        extra.push(format!("due {d}"));
    }
    if !f.blocked_by.is_empty() {
        let ids: Vec<String> = f.blocked_by.iter().map(|i| i.to_string()).collect();
        extra.push(format!("blocked by {}", ids.join(",")));
    }
    extra.extend(f.tags.iter().map(|t| format!("#{t}")));
    let extra = if extra.is_empty() { String::new() } else { format!("  · {}", extra.join(" · ")) };
    format!("{:>3} {:<9} {:<10} {}{extra}", t.id, state, who, f.title)
}
