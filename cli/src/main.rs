//! `arity`, the command-line front end. Everything it does goes through
//! `arity_core::Arity`, the same object the Mac app uses.

use arity_core::{Arity, Message, MessageKind};

const USAGE: &str = "usage:
  arity ls                                   conversations, newest first
  arity log    <conv>                        read a conversation
  arity chat   <conv> <model> <message...>   send a message; <model> may be - for the last one used
  arity resume <conv> [model]                carry on a conversation that stopped
  arity fork   <entry> <new-conv>            branch a conversation at an entry
  arity board                                the shared task board
  arity models                               suggested models (any mlx:<id> or codex:<id> works)
  arity login                                sign in with ChatGPT

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
                println!("{:>3} [{}] {}", t.id, if t.done { "x" } else { " " }, t.title);
            }
        }
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
        MessageKind::ToolResult => println!("#{:<4}   -> {}", m.entry, m.text.replace('\n', "\n          ")),
    }
}
