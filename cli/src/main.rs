//! `arity`, the command-line front end. Everything it does goes through
//! `arity_core::Arity`, the same object the Mac app uses.

use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};

use arity_core::{Access, Arity, KeyCheck, Clearance, Event, Message, MessageKind, Priority, RunState, TaskFields, TaskState, Thread, ThreadGroup};
use serde_json::{Value, json};

const USAGE: &str = "usage:
  arity ls                                   every thread: chats and tasks, by what needs you
  arity board                                the same list, tasks only
  arity log    <thread>                      read a thread
  arity chat   <conv> <model> <message...>   send a message; <model> may be - for the last one used
  arity resume <conv> [model]                carry on a conversation that stopped
  arity stop   <conv>                        stop the turn running in a conversation, from anywhere
  arity fork   <entry> <new-conv>            branch a conversation at an entry
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
  arity models [--refresh]                   models the configured providers offer
  arity providers                            accounts, their plans, and which have a key
  arity provider add <name> <url> [--key]    add an OpenAI-compatible endpoint; --key if it needs one
  arity provider rm <name>                   remove one you added, and its key
  arity key    set <provider>                store an API key, from a hidden prompt or stdin
  arity key    check <provider>              whether the provider takes its key; runs no model
  arity key    rm <provider>                 forget it
  arity workers [add|rm <model>]             the models tasks for agents are picked from
  arity login | logout                       sign in to ChatGPT, or out

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
        ("ls" | "board", 1) => {
            let me = arity.me();
            let mut group = None;
            for t in arity.threads()?.iter().filter(|t| arg(0) == "ls" || t.task.is_some()) {
                if group != Some(t.group) {
                    group = Some(t.group);
                    println!("{}", match t.group {
                        ThreadGroup::NeedsYou => "Needs you",
                        ThreadGroup::Running => "Running",
                        ThreadGroup::Open => "Open",
                        ThreadGroup::Chat => "Chats",
                        ThreadGroup::Done => "Done",
                    });
                }
                println!("{}", thread_line(t, &me));
                let waiting = t.task.as_ref().filter(|k| arg(0) == "board" && k.run_state == Some(RunState::NeedsRating));
                if let Some(r) = waiting.and_then(|k| k.result.as_ref()) {
                    println!("{:>10}{}", "", r.replace('\n', "\n          "));
                }
            }
        }
        ("log", 2) => arity.messages(arg(1))?.iter().for_each(print),
        ("chat", n) if n >= 4 => {
            let model = model_for(&arg(1), arg(2))?;
            stop_on_ctrl_c(&arg(1));
            if !arity.send_with(&arg(1), &model, &args[3..].join(" "), &mut streamer(&model))? {
                println!("a turn is running in {}; it answers this next", arg(1));
            }
        }
        ("resume", 2 | 3) => {
            let model = model_for(&arg(1), arg(2))?;
            stop_on_ctrl_c(&arg(1));
            if !arity.resume_with(&arg(1), &model, &mut streamer(&model))? {
                println!("a turn is already running in {}", arg(1));
            }
        }
        ("stop", 2) => match arity.stop(arg(1))? {
            true => println!("stopping {}", arg(1)),
            false => println!("nothing is running in {}", arg(1)),
        },
        ("fork", 3) => {
            arity.fork(arg(1).parse()?, arg(2))?;
            println!("{} now branches from entry #{}", arg(2), arg(1));
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
            let mut f = arity.threads()?.into_iter().filter_map(|t| t.task).find(|t| t.id == id).ok_or(format!("no task {id}"))?.fields;
            f.merge(&fields)?;
            arity.update_task(id, f)?;
            println!("updated task {id}");
        }
        ("done", 2) => arity.set_task_done(arg(1).parse()?, true)?,
        ("draft", n) if n >= 2 => {
            let conv = arg(1);
            let rest = &args[2..];
            let card = || arity.threads().map(|ts| ts.into_iter().find(|t| t.name == conv).and_then(|t| t.card));
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
                let mut f = card()?.ok_or(format!("{conv} has no task card open; --new opens one"))?;
                f.merge(&fields)?;
                arity.edit_draft(conv.clone(), f)?;
            }
            match card()? {
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
        ("models", 1 | 2) => arity.models(arg(1) == "--refresh")?.iter().for_each(|m| println!("{m}")),
        ("providers", 1) => {
            for a in arity.accounts()? {
                let key = match (&a.key_for, a.has_key) {
                    (None, _) => String::new(),
                    (Some(_), true) => " · key set".into(),
                    (Some(k), false) => format!(" · needs a key: arity key set {k}"),
                };
                println!("{}{key}", a.title);
                for p in a.plans {
                    let how = match p.access {
                        Access::SignIn { account: Some(who) } => format!(" · signed in as {who}"),
                        Access::SignIn { account: None } => " · arity login".into(),
                        _ => String::new(),
                    };
                    println!("  {:<12} {:<10} {}{how}", p.provider, p.title, p.note);
                }
            }
        }
        ("key", 3) if arg(1) == "set" => {
            arity.set_key(arg(2), read_secret(&format!("API key for {}: ", arg(2)))?)?;
            println!("saved the key for {}; {}", arg(2), describe(arity.check_provider(arg(2))?));
        }
        ("key", 3) if arg(1) == "check" => println!("{}: {}", arg(2), describe(arity.check_provider(arg(2))?)),
        ("provider", 4 | 5) if arg(1) == "add" => {
            arity.add_provider(arg(2), arg(3), arg(4) == "--key")?;
            println!("added {}{}", arg(2), if arg(4) == "--key" { format!("; now run `arity key set {}`", arg(2)) } else { String::new() });
        }
        ("provider", 3) if arg(1) == "rm" => arity.remove_provider(arg(2))?,
        ("logout", 1) => arity.sign_out_chatgpt()?,
        ("key", 3) if arg(1) == "rm" => arity.set_key(arg(2), String::new())?,
        ("workers", 1) => arity.worker_models()?.iter().for_each(|m| println!("{m}")),
        ("workers", 3) if arg(1) == "add" || arg(1) == "rm" => arity.set_worker(arg(2), arg(1) == "add")?,
        ("login", 1) => println!("Signed in to ChatGPT as {}.", arity.sign_in_chatgpt()?),
        _ => println!("{USAGE}"),
    }
    Ok(())
}

fn describe(check: KeyCheck) -> String {
    match check {
        KeyCheck::Works => "the key works".into(),
        KeyCheck::Rejected { reason } => format!("the key was rejected: {reason}"),
        KeyCheck::Unreachable { reason } => format!("could not reach it: {reason}"),
        KeyCheck::NoKey => "no key set".into(),
    }
}

/// One line from stdin, not echoed when it is a terminal.
fn read_secret(prompt: &str) -> std::io::Result<String> {
    use std::io::IsTerminal;
    let tty = std::io::stdin().is_terminal();
    let stty = |arg: &str| std::process::Command::new("stty").arg(arg).stdin(std::process::Stdio::inherit()).status();
    if tty {
        eprint!("{prompt}");
        stty("-echo")?;
    }
    let mut line = String::new();
    let read = std::io::stdin().read_line(&mut line);
    if tty {
        stty("echo")?;
        eprintln!();
    }
    read?;
    Ok(line.trim().to_string())
}

/// Prints the reply as it arrives, then each message as it is written. The
/// finished reply's text is already on screen, so only its line is ended.
fn streamer(model: &str) -> impl FnMut(Event) {
    let who = model.to_string();
    let mut streaming = false;
    move |e| match e {
        Event::Text(t) => {
            if !streaming {
                print!("      {who}: ");
                streaming = true;
            }
            print!("{t}");
            let _ = std::io::stdout().flush();
        }
        Event::Message(m) if streaming && m.kind == MessageKind::Assistant => {
            println!();
            streaming = false;
        }
        Event::Message(m) => {
            if std::mem::take(&mut streaming) {
                println!();
            }
            print(m)
        }
    }
}

static INTERRUPTED: AtomicBool = AtomicBool::new(false);

/// Ctrl-C stops the turn the way `arity stop` would, keeping the reply so far.
/// A second Ctrl-C quits at once.
fn stop_on_ctrl_c(conv: &str) {
    extern "C" fn on_sigint(_: libc::c_int) {
        if INTERRUPTED.swap(true, Ordering::SeqCst) {
            unsafe { libc::_exit(130) };
        }
    }
    unsafe { libc::signal(libc::SIGINT, on_sigint as *const () as libc::sighandler_t) };
    let conv = conv.to_string();
    std::thread::spawn(move || {
        while !INTERRUPTED.load(Ordering::SeqCst) {
            std::thread::sleep(std::time::Duration::from_millis(50));
        }
        let _ = Arity::new(None).stop(conv);
    });
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

/// `  task-3    3 ready     for agent   write a poem  · high · due 2026-12-01 · blocked by 2 · #fun`
/// `  chat-1                              how do I ...  · card open`
fn thread_line(thread: &Thread, me: &str) -> String {
    let Some(t) = &thread.task else {
        let card = if thread.card.is_some() { "  · card open" } else { "" };
        return format!("  {:<12} {:<24} {}{card}", thread.name, "", thread.title);
    };
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
    if let Some(origin) = &t.origin {
        extra.push(format!("from {origin}"));
    }
    let extra = if extra.is_empty() { String::new() } else { format!("  · {}", extra.join(" · ")) };
    format!("  {:<12} {:>3} {:<9} {:<10} {}{extra}", thread.name, t.id, state, who, f.title)
}
