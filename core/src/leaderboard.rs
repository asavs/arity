//! The leaderboard: per model, how good it is and how quick, and the pick that
//! turns one of four choices (smart, fast, free, private) into a model.
//!
//! Figures come from two sources, kept apart so either can be inspected:
//! - **Public:** benchmark results and published speeds, from `data/leaderboard.json`,
//!   which says where each figure came from. Matched to a model by its id
//!   without the provider or organisation: `mlx:mlx-community/Qwen3.5-4B-MLX-4bit`
//!   matches `Qwen3.5-4B-MLX-4bit`.
//! - **Arity's own:** the average rating of a model's rated runs, and the time to
//!   first token and output speed of each streamed chat reply (`speeds` table).
//!
//! **How they combine.** Each figure is a weighted average: the public figure
//! counts as `PUBLIC_WEIGHT` observations and each of Arity's own counts as one,
//! so public figures are where a model starts and Arity's measurements take over
//! as they pile up. Quality is on a 0-100 scale: the public figure is the
//! Artificial Analysis Intelligence Index, and a 1-5 rating maps to 0-100.
//! A figure with only one source is that source's.

use std::collections::HashMap;

use anyhow::{Result, bail};
use rusqlite::{Connection, params};
use serde_json::Value;

pub const TABLES: &str = "
    CREATE TABLE IF NOT EXISTS speeds (
        id      INTEGER PRIMARY KEY,
        model   TEXT NOT NULL,
        ttft_ms REAL NOT NULL,
        tps     REAL,
        at      INTEGER NOT NULL
    );";

/// A public figure counts as this many of Arity's own observations.
pub const PUBLIC_WEIGHT: f64 = 5.0;
/// The length of a typical reply, for turning speeds into one waiting time.
const REPLY_TOKENS: f64 = 300.0;
/// Stand-ins when only one of a model's two speed figures is known.
const TYPICAL_TTFT_MS: f64 = 1000.0;
const TYPICAL_TPS: f64 = 50.0;

const SEED: &str = include_str!("../data/leaderboard.json");

/// What the pick needs to know about a model. Each figure may be missing.
#[derive(Debug, Clone, Default, PartialEq, uniffi::Record)]
pub struct Figures {
    /// 0-100.
    pub quality: Option<f64>,
    /// Time to first token, in milliseconds.
    pub ttft_ms: Option<f64>,
    /// Output tokens per second.
    pub tokens_per_sec: Option<f64>,
}

/// One row of the leaderboard.
#[derive(Debug, Clone, PartialEq, uniffi::Record)]
pub struct Standing {
    /// `<provider>:<id>`, or just the id for a public entry no reachable model matches.
    pub model: String,
    pub public: Figures,
    /// Where each public figure came from: "quality: …".
    pub sources: Vec<String>,
    pub own: Figures,
    /// Rated runs behind `own.quality`, and timed replies behind the speeds.
    pub rated: i64,
    pub timed: i64,
    /// What the pick uses.
    pub combined: Figures,
    /// The user can reach it now.
    pub reachable: bool,
}

/// One of four ways to ask for a model, instead of naming one.
#[derive(Debug, Clone, Copy, PartialEq, Eq, uniffi::Enum)]
pub enum Choice {
    /// The best model you can reach.
    Smart,
    /// The quickest model you can reach.
    Fast,
    /// Local models, and subscriptions with quota left. Never a pay-per-token key.
    Free,
    /// Local models only.
    Private,
}

/// The model a choice came to, and why, in a few words.
#[derive(Debug, Clone, PartialEq, uniffi::Record)]
pub struct Pick {
    pub model: String,
    pub reason: String,
}

/// How the user reaches a model, which decides what it counts as.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Reach {
    /// A server on this computer.
    Local,
    /// A plan with a flat fee; `quota_left` unless its provider reports a limit used up.
    Subscription { quota_left: bool },
    /// Every call costs money.
    Pay,
}

#[derive(Debug, Clone)]
pub struct Candidate {
    pub model: String,
    pub reach: Reach,
}

// Arity's own data

/// Note how quickly a streamed reply came. `tps` is None when the reply was too
/// short to time its speed.
pub fn record_speed(c: &Connection, model: &str, ttft_ms: f64, tps: Option<f64>) -> Result<()> {
    c.execute("INSERT INTO speeds (model, ttft_ms, tps, at) VALUES (?1, ?2, ?3, unixepoch())", params![model, ttft_ms, tps])?;
    Ok(())
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Own {
    /// Average rating, 1-5, and how many rated runs.
    pub rating: Option<f64>,
    pub rated: i64,
    pub ttft_ms: Option<f64>,
    pub timed: i64,
    pub tps: Option<f64>,
    pub tps_timed: i64,
}

/// Arity's own figures per model: ratings from the board, speeds from `speeds`.
pub fn own(c: &Connection) -> Result<HashMap<String, Own>> {
    let mut out: HashMap<String, Own> = HashMap::new();
    for (model, rated, average) in crate::board::scores(c)? {
        let o = out.entry(model).or_default();
        (o.rating, o.rated) = (Some(average), rated);
    }
    let mut stmt = c.prepare("SELECT model, count(*), avg(ttft_ms), count(tps), avg(tps) FROM speeds GROUP BY model")?;
    let rows = stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?)))?;
    for row in rows {
        let (model, timed, ttft, tps_timed, tps) = row?;
        let o = out.entry(model).or_default();
        (o.timed, o.ttft_ms, o.tps_timed, o.tps) = (timed, ttft, tps_timed, tps);
    }
    Ok(out)
}

// Public data

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Public {
    /// The model's id, without provider or organisation.
    pub model: String,
    /// Other ids the same model goes by on other providers.
    pub aliases: Vec<String>,
    pub figures: Figures,
    pub sources: Vec<String>,
}

/// The checked-in seed. A live fetcher would produce the same list.
pub fn public() -> Vec<Public> {
    parse_public(&serde_json::from_str(SEED).expect("data/leaderboard.json is valid JSON"))
}

fn parse_public(v: &Value) -> Vec<Public> {
    v["models"]
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|m| {
            Some(Public {
                model: m["model"].as_str()?.into(),
                aliases: m["aliases"].as_array().into_iter().flatten().filter_map(|a| a.as_str().map(String::from)).collect(),
                figures: Figures { quality: m["quality"].as_f64(), ttft_ms: m["ttft_ms"].as_f64(), tokens_per_sec: m["tps"].as_f64() },
                sources: m["sources"].as_object().into_iter().flatten().map(|(k, s)| format!("{k}: {}", s.as_str().unwrap_or_default())).collect(),
            })
        })
        .collect()
}

impl Public {
    /// Whether this entry is about the model `spec` names.
    fn matches(&self, spec: &str) -> bool {
        let id = bare(spec);
        self.model.to_lowercase() == id || self.aliases.iter().any(|a| a.to_lowercase() == id)
    }
}

/// "mlx:mlx-community/Qwen3.5-4B-MLX-4bit" -> "qwen3.5-4b-mlx-4bit"
fn bare(spec: &str) -> String {
    let id = spec.split_once(':').map_or(spec, |(_, id)| id);
    id.rsplit('/').next().unwrap_or(id).to_lowercase()
}

// Combining

/// Public counts as `PUBLIC_WEIGHT` observations, each own one as one.
pub fn blend(public: Option<f64>, own: Option<f64>, n: i64) -> Option<f64> {
    match (public, own) {
        (Some(p), Some(o)) if n > 0 => Some((p * PUBLIC_WEIGHT + o * n as f64) / (PUBLIC_WEIGHT + n as f64)),
        (Some(p), _) => Some(p),
        (None, o) => o,
    }
}

/// A 1-5 rating on the 0-100 quality scale.
fn rating_to_quality(r: f64) -> f64 {
    (r - 1.0) * 25.0
}

/// Every model that is reachable or that anything is known about.
pub fn standings(public: &[Public], own: &HashMap<String, Own>, reachable: &[String]) -> Vec<Standing> {
    let mut names: Vec<String> = reachable.to_vec();
    let mut known: Vec<&String> = own.keys().filter(|m| !reachable.contains(m)).collect();
    known.sort();
    names.extend(known.into_iter().cloned());
    let mut out: Vec<Standing> = names
        .iter()
        .map(|m| {
            let p = public.iter().find(|p| p.matches(m));
            let o = own.get(m).cloned().unwrap_or_default();
            standing(m, p, &o, reachable.contains(m))
        })
        .collect();
    // Public entries no model here matches, so the whole seed can be inspected.
    for p in public {
        if !names.iter().any(|m| p.matches(m)) {
            out.push(standing(&p.model, Some(p), &Own::default(), false));
        }
    }
    out
}

fn standing(model: &str, p: Option<&Public>, o: &Own, reachable: bool) -> Standing {
    let public = p.map(|p| p.figures.clone()).unwrap_or_default();
    let own = Figures { quality: o.rating.map(rating_to_quality), ttft_ms: o.ttft_ms, tokens_per_sec: o.tps };
    let combined = Figures {
        quality: blend(public.quality, own.quality, o.rated),
        ttft_ms: blend(public.ttft_ms, own.ttft_ms, o.timed),
        tokens_per_sec: blend(public.tokens_per_sec, own.tokens_per_sec, o.tps_timed),
    };
    Standing {
        model: model.into(),
        sources: p.map(|p| p.sources.clone()).unwrap_or_default(),
        rated: o.rated,
        timed: o.timed,
        public,
        own,
        combined,
        reachable,
    }
}

/// Seconds to a typical reply. A missing speed figure counts as a typical one;
/// a model with neither has no time.
fn wait(f: &Figures) -> Option<f64> {
    if f.ttft_ms.is_none() && f.tokens_per_sec.is_none() {
        return None;
    }
    Some(f.ttft_ms.unwrap_or(TYPICAL_TTFT_MS) / 1000.0 + REPLY_TOKENS / f.tokens_per_sec.unwrap_or(TYPICAL_TPS))
}

// The pick

/// The model a choice comes to among the candidates. Smart, free and private take
/// the best quality, with speed breaking ties and standing in where no model has
/// a quality figure; fast takes the shortest wait. A model with no figures at all
/// ranks last.
pub fn choose(choice: Choice, candidates: &[Candidate], board: &[Standing]) -> Result<Pick> {
    let allowed = |r: Reach| match choice {
        Choice::Smart | Choice::Fast => true,
        Choice::Private => r == Reach::Local,
        Choice::Free => matches!(r, Reach::Local | Reach::Subscription { quota_left: true }),
    };
    let pool: Vec<&Candidate> = candidates.iter().filter(|c| allowed(c.reach)).collect();
    if pool.is_empty() {
        bail!("{}", match choice {
            Choice::Private => "No local model is running. Start mlx_vlm.server on port 8080 (This Mac in Settings), or another local server.",
            Choice::Free => {
                if candidates.iter().any(|c| c.reach == Reach::Subscription { quota_left: false }) {
                    "No free model: no local server is running, and your subscriptions have used up their quota for now."
                } else {
                    "No free model: no local server is running, and no subscription is signed in."
                }
            }
            Choice::Smart | Choice::Fast => "No model is reachable. Sign in to ChatGPT, add an API key, or start a local server.",
        });
    }
    let none = Figures::default();
    let figures = |m: &str| board.iter().find(|s| s.model == m).map_or(&none, |s| &s.combined);
    // Lower sorts first. f64s become ordered keys; a missing figure goes after every known one.
    let key = |c: &&Candidate| {
        let f = figures(&c.model);
        let w = wait(f).unwrap_or(f64::INFINITY);
        let q = f.quality.map_or(f64::INFINITY, |q| -q);
        match choice {
            Choice::Fast => (w, 0.0),
            _ => (q, w),
        }
    };
    let mut ranked = pool.clone();
    ranked.sort_by(|a, b| key(a).partial_cmp(&key(b)).unwrap().then(a.model.cmp(&b.model)));
    let best = ranked[0];
    let f = figures(&best.model);
    let n = pool.len();
    let of = match choice {
        Choice::Private => plural(n, "local model"),
        Choice::Free => plural(n, "free model"),
        _ => format!("{} you can reach", plural(n, "model")),
    };
    let reason = if n == 1 {
        format!("the only {}", of.trim_start_matches("1 "))
    } else if wait(f).is_none() && f.quality.is_none() {
        format!("first of {of}; none has figures yet")
    } else {
        match choice {
            Choice::Fast => format!("fastest of {of}"),
            _ if f.quality.is_none() => format!("fastest of {of}; none has a quality figure yet"),
            Choice::Smart => format!("smartest of {of}"),
            _ => format!("best of {of}"),
        }
    };
    Ok(Pick { model: best.model.clone(), reason })
}

fn plural(n: usize, what: &str) -> String {
    if n == 1 { format!("1 {what}") } else { format!("{n} {what}s") }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn public_seed() -> Vec<Public> {
        parse_public(&json!({ "models": [
            { "model": "big", "quality": 60.0, "ttft_ms": 3000.0, "tps": 40.0, "sources": { "quality": "test" } },
            { "model": "quick", "quality": 30.0, "ttft_ms": 200.0, "tps": 300.0 },
            { "model": "Local-Good", "quality": 25.0, "tps": 80.0 },
            { "model": "local-fast", "quality": 10.0, "tps": 200.0 },
            { "model": "sub-model", "quality": 45.0, "ttft_ms": 800.0, "tps": 60.0 },
        ] }))
    }

    fn candidates() -> Vec<Candidate> {
        let c = |m: &str, reach| Candidate { model: m.into(), reach };
        vec![
            c("openai:big", Reach::Pay),
            c("groq:quick", Reach::Pay),
            c("mlx:org/local-good", Reach::Local),
            c("mlx:org/local-fast", Reach::Local),
            c("mlx:org/unknown", Reach::Local),
            c("codex:sub-model", Reach::Subscription { quota_left: true }),
        ]
    }

    fn board(own: &HashMap<String, Own>) -> Vec<Standing> {
        let names: Vec<String> = candidates().into_iter().map(|c| c.model).collect();
        standings(&public_seed(), own, &names)
    }

    fn pick(choice: Choice, cands: &[Candidate], own: &HashMap<String, Own>) -> Result<Pick> {
        choose(choice, cands, &board(own))
    }

    #[test]
    fn each_choice_picks_from_what_it_allows() -> Result<()> {
        let own = HashMap::new();
        let all = candidates();
        assert_eq!(pick(Choice::Smart, &all, &own)?, Pick { model: "openai:big".into(), reason: "smartest of 6 models you can reach".into() });
        assert_eq!(pick(Choice::Fast, &all, &own)?.model, "groq:quick");
        assert_eq!(pick(Choice::Fast, &all, &own)?.reason, "fastest of 6 models you can reach");
        assert_eq!(pick(Choice::Private, &all, &own)?, Pick { model: "mlx:org/local-good".into(), reason: "best of 3 local models".into() });
        // Free: local models and the subscription, never the pay-per-token keys.
        assert_eq!(pick(Choice::Free, &all, &own)?, Pick { model: "codex:sub-model".into(), reason: "best of 4 free models".into() });
        Ok(())
    }

    #[test]
    fn private_with_nothing_local_says_what_is_missing() {
        let cloud: Vec<Candidate> = candidates().into_iter().filter(|c| c.reach != Reach::Local).collect();
        let err = pick(Choice::Private, &cloud, &HashMap::new()).unwrap_err().to_string();
        assert!(err.contains("No local model is running"), "{err}");
        assert_eq!(pick(Choice::Smart, &[], &HashMap::new()).unwrap_err().to_string().contains("No model is reachable"), true);
    }

    #[test]
    fn free_skips_a_subscription_out_of_quota() -> Result<()> {
        let mut all = candidates();
        all.iter_mut().filter(|c| c.model == "codex:sub-model").for_each(|c| c.reach = Reach::Subscription { quota_left: false });
        assert_eq!(pick(Choice::Free, &all, &HashMap::new())?, Pick { model: "mlx:org/local-good".into(), reason: "best of 3 free models".into() });
        // With nothing local either, the error says the quota is the reason.
        let cloud: Vec<Candidate> = all.into_iter().filter(|c| c.reach != Reach::Local).collect();
        let err = pick(Choice::Free, &cloud, &HashMap::new()).unwrap_err().to_string();
        assert!(err.contains("used up their quota"), "{err}");
        Ok(())
    }

    #[test]
    fn own_data_overtakes_public_data() -> Result<()> {
        let all = candidates();
        // One good rating of the weaker local model is not enough against the public figures...
        let rated = |n: i64| Own { rating: Some(4.0), rated: n, ..Default::default() };
        let mut own = HashMap::from([("mlx:org/local-fast".to_string(), rated(1))]);
        assert_eq!(pick(Choice::Private, &all, &own)?.model, "mlx:org/local-good");
        // ...but a run of them is. 4/5 is 75; ten of those against public 10 weighted 5: (50 + 750) / 15.
        own.insert("mlx:org/local-fast".into(), rated(10));
        assert_eq!(pick(Choice::Private, &all, &own)?.model, "mlx:org/local-fast");
        let b = board(&own);
        let s = b.iter().find(|s| s.model == "mlx:org/local-fast").unwrap();
        assert_eq!((s.public.quality, s.own.quality, s.rated), (Some(10.0), Some(75.0), 10));
        assert!((s.combined.quality.unwrap() - 800.0 / 15.0).abs() < 1e-9);
        // Measured speeds do the same for fast: the quick public model turns out slow here.
        own.insert("groq:quick".into(), Own { ttft_ms: Some(9000.0), timed: 20, tps: Some(20.0), tps_timed: 20, ..Default::default() });
        assert_ne!(pick(Choice::Fast, &all, &own)?.model, "groq:quick");
        Ok(())
    }

    #[test]
    fn a_model_with_no_data_ranks_last() -> Result<()> {
        let only = |names: &[&str]| -> Vec<Candidate> { candidates().into_iter().filter(|c| names.contains(&c.model.as_str())).collect() };
        let two = only(&["mlx:org/unknown", "mlx:org/local-fast"]);
        assert_eq!(pick(Choice::Private, &two, &HashMap::new())?.model, "mlx:org/local-fast");
        assert_eq!(pick(Choice::Fast, &two, &HashMap::new())?.model, "mlx:org/local-fast");
        let lone = only(&["mlx:org/unknown"]);
        assert_eq!(pick(Choice::Private, &lone, &HashMap::new())?.reason, "the only local model");
        Ok(())
    }

    #[test]
    fn the_seed_parses_and_names_its_sources() {
        let seed = public();
        assert!(seed.len() >= 4);
        for p in &seed {
            let figures = [p.figures.quality, p.figures.ttft_ms, p.figures.tokens_per_sec].iter().filter(|f| f.is_some()).count();
            assert_eq!(figures, p.sources.len(), "{}: one source per figure", p.model);
        }
        assert_eq!(bare("mlx:mlx-community/Qwen3.5-4B-MLX-4bit"), "qwen3.5-4b-mlx-4bit");
        let p = Public { model: "Kimi-K3".into(), aliases: vec!["kimi-k3-instruct".into()], ..Default::default() };
        assert!(p.matches("opencode:kimi-k3") && p.matches("together:moonshotai/Kimi-K3-Instruct") && !p.matches("mlx:kimi"));
    }
}
