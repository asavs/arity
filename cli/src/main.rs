//! `arity`, the command-line front end. Everything it does goes through
//! `arity_core::Arity`, the same object the Mac app uses.

use std::io::Write;
use std::sync::atomic::{AtomicBool, Ordering};

use arity_core::{Access, Arity, Choice, Delivery, Event, Figures, HookConfig, HookEvent, KeyCheck, Message, MessageKind, Priority, RunState, TaskFields, TaskState, Thread, ThreadGroup, Usage};
use serde_json::{Value, json};

const USAGE: &str = "usage:
  arity ls                                   every thread: chats and tasks, by what needs you
  arity board                                the same list, tasks only
  arity log    <thread>                      read a thread
  arity chat   <conv> <model> <message...>   send a message; <model> may be - for a chat's last model or a
                                             task's worker (kept hidden until rated), or --smart, --fast,
                                             --free or --private to let Arity pick for a chat;
                                             if a turn is running there, the message steers it (cutting off its
                                             reply), or with --after-turn joins when it would end;
                                             --inbox leaves it for the next turn and starts nothing;
                                             --dir <path> first attaches a folder (see folder)
  arity resume <conv> [model]                carry on a conversation that stopped
  arity continue <conv> [--smart|--fast|--free|--private|--model <m>]
                                             carry on where it stopped short, writing nothing to ask: a reply
                                             cut off by stop, an unanswered message or report, a stopped run
  arity stop   <conv> [--all | --subtree]    stop the turn running in a conversation, from anywhere; --all also
                                             stops every task it handed out, at any depth, until continued
  arity fork   <entry> <new-conv>            branch a conversation at an entry
  arity usage  <conv>                        tokens used, how full the context is, and how much was cached
  arity compact <conv> [instructions...]     replace the model's context with a summary it writes;
                                             the thread keeps every message
  arity reset  <conv> [note...]              start the model afresh from here; the thread keeps every message
  arity folder <conv> [<path> | --clear]     the folder a conversation's file and shell tools work in;
                                             tasks filed there take it with them
  arity task   <title...> [fields] [-- brief] add a task; --agent hands it to a blindly picked model,
                                             drawn among the top three its choice allows
  arity edit   <task> [fields] [-- brief]    change a task; --agent hands it off
  arity done   <task>                        mark a task done
  arity papercut [--about <t>] [--in <conv>] <note...>
                                             note that something got in the way; it goes on your list
  arity papercuts                            open papercuts
  arity draft  <conv>                        the task card open in a conversation
  arity draft  <conv> --new                  open an empty card; the next model turn helps fill it
  arity draft  <conv> [fields] [-- brief]    edit the card
  arity draft  <conv> --add | --dismiss      put the card on the board, or close it
  arity work   [--once]                      run tasks for agents and deliver their results
  arity runs                                 runs, models hidden until rated
  arity rate   <run> <1-5> <note...>         rate a run yourself
  arity scores                               average rating per model
  arity leaderboard                          per model: quality and speed, public and Arity's own
  arity reveal <run>                         which model did a run
  arity models [--refresh]                   models the configured providers offer
  arity providers                            accounts, their plans, and which have a key
  arity provider add <name> <url> [--key]    add an OpenAI-compatible endpoint; --key if it needs one
  arity provider rm <name>                   remove one you added, and its key
  arity key    set <provider>                store an API key, from a hidden prompt or stdin
  arity key    check <provider>              whether the provider takes its key; runs no model
  arity key    rm <provider>                 forget it
  arity workers [add|rm <model>]             the models tasks for agents are picked from
  arity tools  [conv]                        tools available in a conversation (or a chat without a folder)
  arity hooks                                configured pre/post hooks, plus built-in hooks
  arity hook   add <name> --event <e> [--tool <t>] -- <command...>
                                             add or replace a hook; event is before_tool, after_tool,
                                             on_yield or after_turn; --tool filters by tool name(s)
  arity hook   rm | enable | disable <name>  remove, enable or disable a hook
  arity login | logout                       sign in to ChatGPT, or out

fields: --title <t>  --agent | --me  --smart | --fast | --free | --private  --priority low|normal|high|urgent
        --due YYYY-MM-DD  --blocked-by <id,id>  --tag <t> (repeatable)  --no-tags
        --dir <path> | --no-dir  the folder its run may inspect, edit and run commands in; left out, the chat's

data: $ARITY_HOME, or ~/Library/Application Support/Arity";

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if let Err(e) = run(&args) {
        eprintln!("arity: {e}");
        std::process::exit(1);
    }
}

/// The longest title a listing prints; the full one is in `log` and the app.
const LISTED_TITLE: usize = 80;

fn clip(s: &str) -> String {
    match s.char_indices().nth(LISTED_TITLE) {
        Some((i, _)) => format!("{}…", &s[..i]),
        None => s.to_string(),
    }
}

fn run(args: &[String]) -> Result<(), Box<dyn std::error::Error>> {
    let arity = Arity::new(None);
    let arg = |i: usize| args.get(i).cloned().unwrap_or_default();
    let is_worker = |conv: &str| -> Result<bool, Box<dyn std::error::Error>> {
        Ok(arity.threads()?.iter().any(|t| t.name == conv && t.task.as_ref().and_then(|k| k.run).is_some()))
    };
    let who_for = |conv: &str, model: &str| -> Result<String, Box<dyn std::error::Error>> {
        if model.is_empty() || model == "-" {
            Ok(arity.last_model(conv.into())?.unwrap_or_else(|| "model".into()))
        } else {
            Ok(model.into())
        }
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
        ("log", 2) => {
            let messages = arity.messages(arg(1))?;
            if messages.is_empty() && !arity.threads()?.iter().any(|t| t.name == arg(1)) {
                return Err(format!("no thread {}; `arity ls` lists them", arg(1)).into());
            }
            messages.iter().for_each(print)
        }
        ("chat", n) if n >= 4 => {
            let conv = arg(1);
            let model = match (is_worker(&conv)?, choice(&arg(2))) {
                (true, _) => "-".into(),
                (false, Some(c)) => {
                    let pick = arity.pick(c)?;
                    println!("      {}: {}", pick.model, pick.reason);
                    pick.model
                }
                (false, None) => arg(2),
            };
            let mut rest = &args[3..];
            if rest.first().is_some_and(|a| a == "--dir") {
                let dir = rest.get(1).ok_or("--dir needs a path")?;
                arity.set_folder(conv.clone(), Some(absolute(dir)?))?;
                rest = &rest[2..];
            }
            let (how, words) = match rest.first().map(String::as_str) {
                Some("--after-turn") => (Delivery::AfterTurn, &rest[1..]),
                Some("--inbox") => (Delivery::Inbox, &rest[1..]),
                _ => (Delivery::Steer, rest),
            };
            if words.is_empty() {
                return Err("nothing to send".into());
            }
            let who = who_for(&conv, &model)?;
            stop_on_ctrl_c(&conv);
            if !arity.send_as_with(&conv, &model, &words.join(" "), how, &mut streamer(&who))? {
                println!("a turn is running in {conv}; the message joins it");
            } else if how == Delivery::Inbox {
                println!("left in {conv} for its next turn");
            }
        }
        ("resume", 2 | 3) => {
            let conv = arg(1);
            let model = if is_worker(&conv)? || args.len() == 2 { "-".into() } else { arg(2) };
            let who = who_for(&conv, &model)?;
            stop_on_ctrl_c(&conv);
            if !arity.resume_with(&conv, &model, &mut streamer(&who))? {
                println!("a turn is already running in {conv}");
            }
        }
        ("continue", 2..=4) => {
            let conv = arg(1);
            let model = match (is_worker(&conv)?, arg(2).as_str()) {
                (true, _) | (false, "") => None,
                (false, "--model") if args.len() == 4 => Some(arg(3)),
                (false, flag) => match choice(flag) {
                    Some(c) if args.len() == 3 => {
                        let pick = arity.pick(c)?;
                        println!("      {}: {}", pick.model, pick.reason);
                        Some(pick.model)
                    }
                    _ => return Err(format!("unexpected {:?}; see `arity help`", args[2..].join(" ")).into()),
                },
            };
            let who = who_for(&conv, model.as_deref().unwrap_or("-"))?;
            stop_on_ctrl_c(&conv);
            let ended = arity.continue_with(&conv, model.as_deref(), &mut streamer(&who))?;
            println!("{conv}: {ended}");
        }
        ("stop", 2) => match arity.stop(arg(1))? {
            true => println!("stopping {}", arg(1)),
            false => println!("nothing is running in {}", arg(1)),
        },
        ("stop", 3) if arg(2) == "--all" || arg(2) == "--subtree" => match arity.stop_subtree(arg(1))? {
            0 => println!("nothing is running in {} or below it", arg(1)),
            n => println!("stopped {n} conversation{} in and below {}; `arity continue` carries any of them on", if n == 1 { "" } else { "s" }, arg(1)),
        },
        ("fork", 3) => {
            arity.fork(arg(1).parse()?, arg(2))?;
            println!("{} now branches from entry #{}", arg(2), arg(1));
        }
        ("usage", 2) => {
            let u = arity.usage(arg(1))?;
            if u.replies_with_usage == 0 {
                println!("no usage reported in {} yet; some local servers never report it", arg(1));
            } else {
                println!(
                    "context    {} tokens in the last prompt, {} cached",
                    tokens(u.last_input_tokens),
                    percent(u.last_cached_tokens, u.last_input_tokens)
                );
                println!(
                    "all told   {} in ({} cached) · {} out · over {} replies",
                    tokens(u.input_tokens),
                    percent(u.cached_tokens, u.input_tokens),
                    tokens(u.output_tokens),
                    u.replies_with_usage
                );
            }
            if u.compacted_entries > 0 {
                println!("compacted  {} earlier entries are no longer in the model's context", u.compacted_entries);
            }
        }
        ("compact", n) if n >= 2 => {
            let instructions = (n > 2).then(|| args[2..].join(" "));
            let summary = arity.compact(arg(1), instructions)?;
            println!("compacted {}; the model now reads this summary:\n\n{summary}", arg(1));
        }
        ("reset", n) if n >= 2 => {
            let note = (n > 2).then(|| args[2..].join(" "));
            arity.reset_context(arg(1), note)?;
            println!("{}'s model starts fresh from here; `arity log {}` still shows everything", arg(1), arg(1));
        }
        ("folder", 2) => match arity.folder(arg(1))? {
            Some(dir) => println!("{dir}"),
            None => println!("{} has no folder", arg(1)),
        },
        ("folder", 3) => {
            if arg(2) == "--clear" {
                arity.set_folder(arg(1), None)?;
                println!("{} has no folder now", arg(1));
            } else {
                arity.set_folder(arg(1), Some(absolute(&arg(2))?))?;
                println!("{} reads {}", arg(1), arity.folder(arg(1))?.unwrap_or_default());
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
            let mut f = arity.threads()?.into_iter().filter_map(|t| t.task).find(|t| t.id == id).ok_or(format!("no task {id}"))?.fields;
            f.merge(&fields)?;
            arity.update_task(id, f)?;
            println!("updated task {id}");
        }
        ("done", 2) => {
            arity.set_task_done(arg(1).parse()?, true)?;
            println!("task {} is done", arg(1));
        }
        ("papercut", n) if n >= 2 => {
            let (mut about, mut conv, mut words) = (None, None, vec![]);
            let mut rest = args[1..].iter();
            while let Some(a) = rest.next() {
                match a.as_str() {
                    "--about" => about = Some(rest.next().ok_or("--about needs what got in the way")?.clone()),
                    "--in" => conv = Some(rest.next().ok_or("--in needs a conversation")?.clone()),
                    _ => words.push(a.as_str()),
                }
            }
            let id = arity.file_papercut(conv, words.join(" "), about)?;
            println!("filed papercut {id}");
        }
        ("papercuts", 1) => {
            let me = arity.me();
            for t in arity.threads()?.iter().filter(|t| {
                t.task.as_ref().is_some_and(|k| k.state != TaskState::Done && k.fields.tags.iter().any(|g| g == "papercut"))
            }) {
                println!("{}", thread_line(t, &me));
            }
        }
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
                println!("run {:<3} task {:<3} {:<8} {:<40} {}", r.id, r.task, score, clip(&r.title), model);
            }
        }
        ("rate", n) if n >= 4 => {
            let before = arity.rate(arg(1).parse()?, arg(2).parse()?, args[3..].join(" "))?;
            let changed = before.filter(|b| b.to_string() != arg(2)).map(|b| format!(", changed from {b} to {}", arg(2))).unwrap_or_default();
            println!("rated run {}{changed}; it was {}", arg(1), arity.reveal(arg(1).parse()?)?);
        }
        ("scores", 1) => {
            for s in arity.scores()? {
                println!("{:>5.2}  {:>3} rated  {}", s.average, s.rated, s.model);
            }
        }
        ("leaderboard", 1) => {
            // Each figure: the one the pick uses, then where it came from.
            println!("{:<44} {:<30} {:<26} {}", "model", "quality (0-100)", "first token", "speed");
            for s in arity.leaderboard()? {
                let fig = |get: fn(&Figures) -> Option<f64>, n: i64, unit: &str, digits: usize| -> String {
                    let Some(used) = get(&s.combined) else { return "-".into() };
                    let mut from = vec![];
                    if get(&s.public).is_some() { from.push("public".to_string()) }
                    if get(&s.own).is_some() { from.push(format!("own {n}")) }
                    format!("{used:.digits$}{unit} ({})", from.join(" + "))
                };
                let reach = if s.reachable { "" } else { "  (not reachable)" };
                let quality = match (s.public.quality, s.shift) {
                    (Some(_), Some(shift)) => format!("{:.0} (public {shift:+.1} from {} rated)", s.combined.quality.unwrap_or_default(), s.rated),
                    (Some(q), None) => format!("{q:.0} (public)"),
                    (None, Some(shift)) => format!("{shift:+.1} from {} rated", s.rated),
                    (None, None) => "-".into(),
                };
                println!(
                    "{:<44} {:<30} {:<26} {}{reach}",
                    s.model,
                    quality,
                    fig(|f| f.ttft_ms, s.timed, " ms", 0),
                    fig(|f| f.tokens_per_sec, s.timed, " tok/s", 0),
                );
            }
            println!("\npublic figures and their sources: core/data/leaderboard.json; ratings shift quality by up to 10 points a star from the average rating; own n = timed replies");
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
                    match p.usage {
                        Usage::Limits { limits, age_secs } => {
                            let parts: Vec<String> = limits.iter().map(|l| format!("{} of {} {}", l.remaining, l.limit, l.name)).collect();
                            println!("  {:<12} {:<10} left: {} (as of {age_secs}s ago)", "", "", parts.join(", "));
                        }
                        Usage::NoneYet => println!("  {:<12} {:<10} usage shows after the first reply", "", ""),
                        Usage::NotReported => println!("  {:<12} {:<10} usage not reported", "", ""),
                        Usage::Unlimited => {}
                    }
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
        ("tools", 1 | 2) => {
            let conv = (args.len() == 2).then(|| arg(1));
            let tools = arity.tools(conv)?;
            let name_w = tools.iter().map(|t| t.name.len()).max().unwrap_or(12).max(12);
            for t in &tools {
                let status = if t.available { "[on]" } else { "[off]" };
                println!(
                    "  {:<name_w$}  {:<5}  {:<15}  {}",
                    t.name,
                    status,
                    t.execution,
                    first_sentence(&t.description),
                );
            }
        }
        ("hooks", 1) => {
            println!("Built-in hooks");
            for h in arity.builtin_hooks() {
                let label = format!("{} [{}]", h.name, h.event.as_str());
                println!("  {:<28}  {}", label, h.description);
            }
            println!();
            println!("User hooks");
            let hooks = arity.hooks()?;
            if hooks.is_empty() {
                println!("  (none; add one with `arity hook add <name> --event <e> [--tool <t>] -- <command...>`)");
            } else {
                let name_w = hooks.iter().map(|h| h.name.len()).max().unwrap_or(8).max(8);
                let tool_w = hooks
                    .iter()
                    .map(|h| h.tool.as_deref().unwrap_or("*").len())
                    .max()
                    .unwrap_or(1)
                    .max(1);
                for h in &hooks {
                    let status = if h.enabled { "[on]" } else { "[off]" };
                    let tool = h.tool.as_deref().unwrap_or("*");
                    println!(
                        "  {:<5}  {:<name_w$}  {:<11}  {:<tool_w$}  {}",
                        status,
                        h.name,
                        h.event.as_str(),
                        tool,
                        h.command,
                    );
                }
            }
        }
        ("hook", n) if n >= 2 && arg(1) == "add" => {
            let hook = parse_hook_add(&args[2..])?;
            let name = hook.name.clone();
            arity.save_hook(hook)?;
            println!("saved hook {name}");
        }
        ("hook", 3) if arg(1) == "rm" => {
            let name = arg(2);
            if !arity.remove_hook(name.clone())? {
                return Err(format!("no hook named {name}").into());
            }
            println!("removed hook {name}");
        }
        ("hook", 3) if arg(1) == "enable" || arg(1) == "disable" => {
            let enabled = arg(1) == "enable";
            let name = arg(2);
            arity.set_hook_enabled(name.clone(), enabled)?;
            println!("{} hook {name}", if enabled { "enabled" } else { "disabled" });
        }
        ("login", 1) => println!("Signed in to ChatGPT as {}.", arity.sign_in_chatgpt()?),
        ("" | "help" | "--help" | "-h", _) => println!("{USAGE}"),
        (cmd, _) => {
            eprintln!("{USAGE}");
            return Err(format!("`{cmd}` needs other arguments; see above").into());
        }
    }
    Ok(())
}

fn choice(flag: &str) -> Option<Choice> {
    Some(match flag {
        "--smart" => Choice::Smart,
        "--fast" => Choice::Fast,
        "--free" => Choice::Free,
        "--private" => Choice::Private,
        _ => return None,
    })
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
        MessageKind::Rating => println!("#{:<4} rating: {}", m.entry, m.text),
        MessageKind::TaskCard => println!("#{:<4} card: {}", m.entry, m.text),
        MessageKind::ToolResult => println!("#{:<4}   -> {}", m.entry, m.text.replace('\n', "\n          ")),
        MessageKind::Compaction => println!("#{:<4} context: {}", m.entry, m.text.replace('\n', "\n          ")),
    }
}

/// 18400 -> "18.4k"
fn tokens(n: u64) -> String {
    match n {
        0..1_000 => n.to_string(),
        1_000..1_000_000 => format!("{:.1}k", n as f64 / 1e3),
        _ => format!("{:.2}M", n as f64 / 1e6),
    }
}

fn percent(part: u64, whole: u64) -> String {
    if whole == 0 { "0%".into() } else { format!("{:.0}%", part as f64 * 100.0 / whole as f64) }
}

/// A folder named on the command line, as the absolute path a run can use from anywhere.
fn absolute(dir: &str) -> Result<String, Box<dyn std::error::Error>> {
    let path = std::fs::canonicalize(dir).map_err(|e| format!("no folder {dir}: {e}"))?;
    if !path.is_dir() {
        return Err(format!("{dir} is not a folder").into());
    }
    Ok(path.to_string_lossy().into_owned())
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
            "--smart" | "--fast" | "--free" | "--private" => fields["choice"] = json!(&a[2..]),
            "--local" => fields["choice"] = json!("private"),
            "--any" => fields["choice"] = json!("smart"),
            "--priority" => fields["priority"] = json!(value(a)?),
            "--due" => fields["due"] = json!(value(a)?),
            "--no-due" => fields["due"] = Value::Null,
            "--dir" => fields["dir"] = json!(absolute(&value(a)?)?),
            "--no-dir" => fields["dir"] = Value::Null,
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
    let mut flow = String::new();
    if thread.can_continue {
        flow.push_str("  · stopped short; arity continue");
    }
    if thread.active_children > 0 {
        flow.push_str(&format!("  · {} sub-task{} going", thread.active_children, if thread.active_children == 1 { "" } else { "s" }));
    }
    let Some(t) = &thread.task else {
        let card = if thread.card.is_some() { "  · card open" } else { "" };
        return format!("  {:<12} {:<24} {}{card}{flow}", thread.name, "", clip(&thread.title));
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
        Some(RunState::Waiting) => extra.push("waiting on sub-tasks".into()),
        Some(RunState::Failing) => extra.push("retrying".into()),
        Some(RunState::Paused) => extra.push("stopped; arity continue".into()),
        Some(RunState::NeedsRating) => extra.push(format!("needs rating (run {})", t.run.unwrap_or_default())),
        Some(RunState::Rated) => extra.push(format!("rated {}/5", t.score.unwrap_or_default())),
        None => {}
    }
    if f.priority != Priority::Normal {
        extra.push(format!("{:?}", f.priority).to_lowercase());
    }
    if f.choice != Choice::Smart {
        extra.push(format!("{:?}", f.choice).to_lowercase());
    }
    if let Some(d) = &f.due {
        extra.push(format!("due {d}"));
    }
    if !f.blocked_by.is_empty() {
        let ids: Vec<String> = f.blocked_by.iter().map(|i| i.to_string()).collect();
        extra.push(format!("blocked by {}", ids.join(",")));
    }
    extra.extend(f.tags.iter().map(|t| format!("#{t}")));
    if let Some(d) = &f.dir {
        extra.push(format!("in {d}"));
    }
    if let Some(origin) = &t.origin {
        extra.push(format!("from {origin}"));
    }
    let extra = if extra.is_empty() { String::new() } else { format!("  · {}", extra.join(" · ")) };
    format!("  {:<12} {:>3} {:<9} {:<10} {}{extra}{flow}", thread.name, t.id, state, who, clip(&f.title))
}

fn first_sentence(s: &str) -> String {
    let first_line = s.lines().next().unwrap_or("").trim();
    let sentence = match first_line.find(". ") {
        Some(i) => &first_line[..=i],
        None => first_line,
    };
    clip(sentence)
}

/// Parse arguments after `arity hook add`:
/// `<name> --event <e> [--tool <t>] [--disabled] (--command <cmd> | -- <command...>)`
fn parse_hook_add(args: &[String]) -> Result<HookConfig, Box<dyn std::error::Error>> {
    let mut name: Option<String> = None;
    let mut event: Option<HookEvent> = None;
    let mut tool: Option<String> = None;
    let mut command: Option<String> = None;
    let mut enabled = true;

    let mut it = args.iter();
    while let Some(a) = it.next() {
        let mut value = |flag: &str| it.next().cloned().ok_or_else(|| format!("{flag} needs a value"));
        match a.as_str() {
            "--" => {
                if command.is_some() {
                    return Err("specify either --command or -- <command...>, not both".into());
                }
                let joined = it.by_ref().cloned().collect::<Vec<_>>().join(" ");
                command = Some(joined);
            }
            "--event" => {
                let raw = value(a)?;
                let parsed = HookEvent::parse(&raw).ok_or_else(|| {
                    format!("unknown hook event {raw:?}; expected before_tool, after_tool, on_yield, or after_turn")
                })?;
                event = Some(parsed);
            }
            "--tool" => {
                let raw = value(a)?;
                let trimmed = raw.trim();
                tool = if trimmed.is_empty() { None } else { Some(trimmed.to_string()) };
            }
            "--command" => {
                if command.is_some() {
                    return Err("specify either --command or -- <command...>, not both".into());
                }
                command = Some(value(a)?);
            }
            "--disabled" => enabled = false,
            flag if flag.starts_with("--") => return Err(format!("unknown flag {flag}").into()),
            word => {
                if name.is_none() {
                    name = Some(word.to_string());
                } else {
                    return Err(format!("unexpected argument {word:?} (put the command after -- or --command)").into());
                }
            }
        }
    }

    let name = name
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .ok_or("hook add needs a name")?;
    let event = event.ok_or("--event is required (before_tool, after_tool, on_yield, or after_turn)")?;
    let command = command
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .ok_or("hook add needs a command via --command <cmd> or -- <command...>")?;

    Ok(HookConfig { name, event, tool, command, enabled })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(items: &[&str]) -> Vec<String> {
        items.iter().map(|x| (*x).to_string()).collect()
    }

    #[test]
    fn parse_hook_add_with_dash_dash_command() {
        let cfg = parse_hook_add(&s(&[
            "fmt_check",
            "--event",
            "after_tool",
            "--tool",
            "edit_file,write_file",
            "--",
            "cargo",
            "fmt",
            "--check",
        ]))
        .unwrap();
        assert_eq!(
            cfg,
            HookConfig {
                name: "fmt_check".into(),
                event: HookEvent::AfterTool,
                tool: Some("edit_file,write_file".into()),
                command: "cargo fmt --check".into(),
                enabled: true,
            }
        );
    }

    #[test]
    fn parse_hook_add_with_command_flag_and_disabled() {
        let cfg = parse_hook_add(&s(&[
            "test-gate",
            "--event",
            "on_yield",
            "--disabled",
            "--command",
            "cargo test --quiet",
        ]))
        .unwrap();
        assert_eq!(
            cfg,
            HookConfig {
                name: "test-gate".into(),
                event: HookEvent::OnYield,
                tool: None,
                command: "cargo test --quiet".into(),
                enabled: false,
            }
        );
    }

    #[test]
    fn parse_hook_add_rejects_invalid_and_missing_args() {
        // Invalid event
        let err = parse_hook_add(&s(&["h", "--event", "bogus", "--", "true"]))
            .unwrap_err()
            .to_string();
        assert!(err.contains("unknown hook event"), "{err}");

        // Missing event
        let err = parse_hook_add(&s(&["h", "--", "true"]))
            .unwrap_err()
            .to_string();
        assert!(err.contains("--event"), "{err}");

        // Missing name
        let err = parse_hook_add(&s(&["--event", "before_tool", "--", "true"]))
            .unwrap_err()
            .to_string();
        assert!(err.contains("name"), "{err}");

        // Missing command
        let err = parse_hook_add(&s(&["h", "--event", "after_turn"]))
            .unwrap_err()
            .to_string();
        assert!(err.contains("command"), "{err}");

        // Empty command after --
        let err = parse_hook_add(&s(&["h", "--event", "after_turn", "--"]))
            .unwrap_err()
            .to_string();
        assert!(err.contains("command"), "{err}");

        // Both --command and --
        let err = parse_hook_add(&s(&["h", "--event", "before_tool", "--command", "echo a", "--", "echo", "b"]))
            .unwrap_err()
            .to_string();
        assert!(err.contains("not both"), "{err}");

        // Unknown flag
        let err = parse_hook_add(&s(&["h", "--event", "before_tool", "--nope", "--", "true"]))
            .unwrap_err()
            .to_string();
        assert!(err.contains("unknown flag"), "{err}");
    }

    #[test]
    fn token_counts_read_short() {
        assert_eq!((tokens(950), tokens(18_400), tokens(2_500_000)), ("950".into(), "18.4k".into(), "2.50M".into()));
        assert_eq!((percent(13_248, 18_400), percent(1, 0)), ("72%".into(), "0%".into()));
    }

    #[test]
    fn first_sentence_extracts_and_clips() {
        assert_eq!(
            first_sentence("Read a file in the attached folder. Second sentence here."),
            "Read a file in the attached folder."
        );
        assert_eq!(
            first_sentence("Single sentence without trailing text"),
            "Single sentence without trailing text"
        );
        let long = "a".repeat(120);
        let clipped = first_sentence(&long);
        assert!(clipped.ends_with('…'));
        assert_eq!(clipped.chars().count(), LISTED_TITLE + 1);
    }
}
