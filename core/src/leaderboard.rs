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
//! **How they combine.** Speeds are weighted averages: the public figure counts
//! as `PUBLIC_WEIGHT` observations and each measured reply as one, so public
//! figures are where a model starts and measurements take over as they pile up.
//!
//! Quality is the public index (Artificial Analysis, 0-100). Ratings do not
//! stand in for it, since stars on easy tasks would beat every benchmark;
//! they shift it: `POINTS_PER_STAR` for each star a model's average rating is
//! above or below the average across all rated runs, scaled by n / (n +
//! `PUBLIC_WEIGHT`) for n rated runs. A model with no public figure starts at
//! the average public figure of the models it is being picked among, so a few
//! ratings move it a little and many move it more. A model with neither has
//! no quality and ranks last.

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
/// How far one star above the average rating moves quality, at full weight.
pub const POINTS_PER_STAR: f64 = 10.0;
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
    /// Measured speeds. Its quality is always None: ratings shift quality instead, see `shift`.
    pub own: Figures,
    /// Average rating of its rated runs, 1-5, and how many there are.
    pub rating: Option<f64>,
    pub rated: i64,
    /// How far its ratings move its quality, in index points, already scaled by how many there are.
    pub shift: Option<f64>,
    /// Timed replies behind the measured speeds.
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

impl Choice {
    pub const ALL: [Choice; 4] = [Choice::Smart, Choice::Fast, Choice::Free, Choice::Private];

    pub fn name(self) -> &'static str {
        match self {
            Choice::Smart => "smart",
            Choice::Fast => "fast",
            Choice::Free => "free",
            Choice::Private => "private",
        }
    }

    pub fn parse(s: &str) -> Option<Choice> {
        Choice::ALL.into_iter().find(|c| c.name() == s.trim().to_lowercase())
    }
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

/// The average rating over every rated run, the zero point for shifts.
fn mean_rating(own: &HashMap<String, Own>) -> Option<f64> {
    let (sum, n) = own.values().filter_map(|o| Some((o.rating? * o.rated as f64, o.rated))).fold((0.0, 0), |a, b| (a.0 + b.0, a.1 + b.1));
    (n > 0).then(|| sum / n as f64)
}

/// Every model that is reachable or that anything is known about.
pub fn standings(public: &[Public], own: &HashMap<String, Own>, reachable: &[String]) -> Vec<Standing> {
    let mut names: Vec<String> = reachable.to_vec();
    let mut known: Vec<&String> = own.keys().filter(|m| !reachable.contains(m)).collect();
    known.sort();
    names.extend(known.into_iter().cloned());
    let mean = mean_rating(own);
    let mut out: Vec<Standing> = names
        .iter()
        .map(|m| {
            let p = public.iter().find(|p| p.matches(m));
            let o = own.get(m).cloned().unwrap_or_default();
            standing(m, p, &o, mean, reachable.contains(m))
        })
        .collect();
    // Public entries no model here matches, so the whole seed can be inspected.
    for p in public {
        if !names.iter().any(|m| p.matches(m)) {
            out.push(standing(&p.model, Some(p), &Own::default(), mean, false));
        }
    }
    out
}

fn standing(model: &str, p: Option<&Public>, o: &Own, mean: Option<f64>, reachable: bool) -> Standing {
    let public = p.map(|p| p.figures.clone()).unwrap_or_default();
    let own = Figures { quality: None, ttft_ms: o.ttft_ms, tokens_per_sec: o.tps };
    let shift = match (o.rating, mean) {
        (Some(r), Some(mean)) if o.rated > 0 => Some(POINTS_PER_STAR * (r - mean) * o.rated as f64 / (o.rated as f64 + PUBLIC_WEIGHT)),
        _ => None,
    };
    let combined = Figures {
        quality: public.quality.map(|q| q + shift.unwrap_or(0.0)),
        ttft_ms: blend(public.ttft_ms, own.ttft_ms, o.timed),
        tokens_per_sec: blend(public.tokens_per_sec, own.tokens_per_sec, o.tps_timed),
    };
    Standing {
        model: model.into(),
        sources: p.map(|p| p.sources.clone()).unwrap_or_default(),
        rating: o.rating,
        rated: o.rated,
        shift,
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

/// The model a choice comes to among the candidates, for a chat: the best one
/// (see `rank`), and why.
pub fn choose(choice: Choice, candidates: &[Candidate], board: &[Standing]) -> Result<Pick> {
    let (ranked, scale) = rank(choice, candidates, board)?;
    let best = ranked[0];
    let f = scale.figures(&best.model);
    let q = scale.quality(&best.model);
    let n = ranked.len();
    let of = match choice {
        Choice::Private => plural(n, "local model"),
        Choice::Free => plural(n, "free model"),
        _ => format!("{} you can reach", plural(n, "model")),
    };
    let reason = if n == 1 {
        format!("the only {}", of.trim_start_matches("1 "))
    } else if wait(f).is_none() && q.is_none() {
        format!("first of {of}; none has figures yet")
    } else {
        match choice {
            Choice::Fast => format!("fastest of {of}"),
            _ if q.is_none() => format!("fastest of {of}; none has a quality figure yet"),
            Choice::Smart => format!("smartest of {of}"),
            _ => format!("best of {of}"),
        }
    };
    Ok(Pick { model: best.model.clone(), reason })
}

/// How likely each of the top three is to be picked for a task: the best half
/// the time, the second a third, the third a sixth.
pub const BLIND_WEIGHTS: [u32; 3] = [3, 2, 1];

/// The pick for a task, which stays a blind test: drawn among the top three
/// models the choice allows, weighted by rank, so weaker ones keep getting runs
/// and their scores keep moving. `roll(n)` returns a number below n.
pub fn blind(choice: Choice, candidates: &[Candidate], board: &[Standing], roll: &mut dyn FnMut(u32) -> u32) -> Result<String> {
    let (ranked, _) = rank(choice, candidates, board)?;
    let top = &ranked[..ranked.len().min(BLIND_WEIGHTS.len())];
    let mut r = roll(BLIND_WEIGHTS[..top.len()].iter().sum());
    for (c, w) in top.iter().zip(BLIND_WEIGHTS) {
        if r < w {
            return Ok(c.model.clone());
        }
        r -= w;
    }
    Ok(top[0].model.clone())
}

/// The figures one ranking reads: a candidate's own, and its quality as the pool sees it.
struct Scale<'a> {
    board: &'a [Standing],
    /// Where a rated model with no public figure starts: the pool's average public figure.
    prior: f64,
    none: Figures,
}

impl Scale<'_> {
    fn standing(&self, m: &str) -> Option<&Standing> {
        self.board.iter().find(|s| s.model == m)
    }
    fn figures(&self, m: &str) -> &Figures {
        self.standing(m).map_or(&self.none, |s| &s.combined)
    }
    fn quality(&self, m: &str) -> Option<f64> {
        self.figures(m).quality.or_else(|| self.standing(m)?.shift.map(|s| self.prior + s))
    }
}

/// The candidates the choice allows, best first. Smart, free and private take
/// the best quality, with speed breaking ties and standing in where no model has
/// a quality figure; fast takes the shortest wait. A model with no figures at
/// all ranks last. Fails, saying what is missing, when none is allowed.
fn rank<'a, 'b>(choice: Choice, candidates: &'a [Candidate], board: &'b [Standing]) -> Result<(Vec<&'a Candidate>, Scale<'b>)> {
    let allowed = |r: Reach| match choice {
        Choice::Smart | Choice::Fast => true,
        Choice::Private => r == Reach::Local,
        Choice::Free => matches!(r, Reach::Local | Reach::Subscription { quota_left: true }),
    };
    let mut pool: Vec<&Candidate> = candidates.iter().filter(|c| allowed(c.reach)).collect();
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
    let mut scale = Scale { board, prior: 0.0, none: Figures::default() };
    let public: Vec<f64> = pool.iter().filter_map(|c| scale.standing(&c.model)?.public.quality).collect();
    if !public.is_empty() {
        scale.prior = public.iter().sum::<f64>() / public.len() as f64;
    }
    // Lower sorts first. f64s become ordered keys; a missing figure goes after every known one.
    let key = |c: &&Candidate| {
        let w = wait(scale.figures(&c.model)).unwrap_or(f64::INFINITY);
        let q = scale.quality(&c.model).map_or(f64::INFINITY, |q| -q);
        match choice {
            Choice::Fast => (w, 0.0),
            _ => (q, w),
        }
    };
    pool.sort_by(|a, b| key(a).partial_cmp(&key(b)).unwrap().then(a.model.cmp(&b.model)));
    Ok((pool, scale))
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
        let rated = |stars: f64, n: i64| Own { rating: Some(stars), rated: n, ..Default::default() };
        // One great run against one poor one does not overturn the public figures (25 vs 10)...
        let mut own = HashMap::from([("mlx:org/local-fast".to_string(), rated(5.0, 1)), ("mlx:org/local-good".to_string(), rated(2.0, 1))]);
        assert_eq!(pick(Choice::Private, &all, &own)?.model, "mlx:org/local-good");
        // ...but ten of each do. The average is 3.5 stars: +1.5 and -1.5 at 10/15 weight, so 20 against 15.
        own.insert("mlx:org/local-fast".into(), rated(5.0, 10));
        own.insert("mlx:org/local-good".into(), rated(2.0, 10));
        assert_eq!(pick(Choice::Private, &all, &own)?.model, "mlx:org/local-fast");
        let b = board(&own);
        let s = b.iter().find(|s| s.model == "mlx:org/local-fast").unwrap();
        assert_eq!((s.public.quality, s.rating, s.rated), (Some(10.0), Some(5.0), 10));
        assert!((s.combined.quality.unwrap() - 20.0).abs() < 1e-9);
        // Measured speeds do the same for fast: the quick public model turns out slow here.
        own.insert("groq:quick".into(), Own { ttft_ms: Some(9000.0), timed: 20, tps: Some(20.0), tps_timed: 20, ..Default::default() });
        assert_ne!(pick(Choice::Fast, &all, &own)?.model, "groq:quick");
        Ok(())
    }

    /// Ratings move a model relative to the others, so stars cannot outgrow the benchmark scale.
    #[test]
    fn one_rating_cannot_lift_a_model_without_figures_over_the_rest() -> Result<()> {
        let all = candidates();
        let rated = |stars: f64, n: i64| Own { rating: Some(stars), rated: n, ..Default::default() };
        // The model with no public figure gets one 5-star run; the best local model one 3-star run.
        let own = HashMap::from([("mlx:org/unknown".to_string(), rated(5.0, 1)), ("mlx:org/local-good".to_string(), rated(3.0, 1))]);
        assert_eq!(pick(Choice::Private, &all, &own)?.model, "mlx:org/local-good");
        // Every run rated 5 lifts nobody: the average is the zero point.
        let all_five: HashMap<String, Own> = ["mlx:org/unknown", "mlx:org/local-good", "openai:big"].iter().map(|m| (m.to_string(), rated(5.0, 30))).collect();
        let b = board(&all_five);
        let big = b.iter().find(|s| s.model == "openai:big").unwrap();
        assert_eq!(big.combined.quality, Some(60.0));
        assert_eq!(pick(Choice::Smart, &all, &all_five)?.model, "openai:big");
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

    /// A task's pick is drawn from the top three, so the third-best still gets runs and the fourth none.
    #[test]
    fn a_tasks_pick_still_gives_lower_ranked_models_runs() -> Result<()> {
        let all = candidates();
        let b = board(&HashMap::new());
        // Smart ranks big (60), sub-model (45), quick (30), then the local ones.
        let mut counts: HashMap<String, u32> = HashMap::new();
        for _ in 0..1200 {
            let m = blind(Choice::Smart, &all, &b, &mut |n| rand::random_range(0..n))?;
            *counts.entry(m).or_default() += 1;
        }
        let n = |m: &str| counts.get(m).copied().unwrap_or(0);
        assert!(n("openai:big") > n("codex:sub-model") && n("codex:sub-model") > n("groq:quick"), "{counts:?}");
        assert!(n("groq:quick") > 100, "the third is picked about a sixth of the time: {counts:?}");
        assert_eq!(n("mlx:org/local-good"), 0, "the fourth never");
        // Every roll lands on one of the top three, in rank order.
        let order: Vec<String> = (0..6).map(|r| blind(Choice::Smart, &all, &b, &mut |_| r).unwrap()).collect();
        assert_eq!(order, ["openai:big", "openai:big", "openai:big", "codex:sub-model", "codex:sub-model", "groq:quick"]);
        // Fewer than three allowed: drawn among those. Private still keeps to local models.
        let local = blind(Choice::Private, &all, &b, &mut |n| n - 1)?;
        assert!(local.starts_with("mlx:"), "{local}");
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
