//! What a provider says about its limits. Providers that report them do so on
//! every reply, as `x-ratelimit-{limit,remaining,reset}-<what>` headers; the
//! latest are kept in usage.json in the data folder, one entry per provider.

use std::path::Path;
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{Value, json};

/// A plan's usage, as far as Arity can know it.
#[derive(Debug, Clone, PartialEq, uniffi::Enum)]
pub enum Usage {
    /// Runs on this computer; nothing to count.
    Unlimited,
    /// The provider does not tell other apps.
    NotReported,
    /// It reports usage on its replies, and none has come yet.
    NoneYet,
    /// As of `age_secs` ago.
    Limits { limits: Vec<Limit>, age_secs: u64 },
}

#[derive(Debug, Clone, PartialEq, uniffi::Record)]
pub struct Limit {
    /// "requests", "tokens", "requests-1h": the provider's own name for it.
    pub name: String,
    pub remaining: u64,
    pub limit: u64,
    /// Until it is full again; None if the provider did not say.
    pub resets_in_secs: Option<u64>,
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_secs()
}

/// Keep the limits a reply reports. Replies without them change nothing.
pub fn record(dir: &Path, provider: &str, headers: &reqwest::header::HeaderMap) {
    let limits = parse(headers.iter().filter_map(|(k, v)| Some((k.as_str(), v.to_str().ok()?))));
    if limits.is_empty() {
        return;
    }
    let path = dir.join("usage.json");
    let mut all: Value = std::fs::read_to_string(&path).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(json!({}));
    let limits: Vec<Value> = limits
        .iter()
        .map(|l| json!({ "name": l.name, "remaining": l.remaining, "limit": l.limit, "reset": l.resets_in_secs }))
        .collect();
    all[provider] = json!({ "at": now(), "limits": limits });
    let _ = std::fs::write(&path, all.to_string());
}

/// The latest limits for a provider that reports them, aged to now: a limit
/// whose reset has passed counts as full again.
pub fn read(dir: &Path, provider: &str) -> Usage {
    let all: Value = std::fs::read_to_string(dir.join("usage.json")).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default();
    let Some(at) = all[provider]["at"].as_u64() else { return Usage::NoneYet };
    let age = now().saturating_sub(at);
    let limits = all[provider]["limits"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|l| {
            let limit = l["limit"].as_u64().unwrap_or(0);
            let reset = l["reset"].as_u64();
            let passed = reset.is_some_and(|r| age >= r);
            Limit {
                name: l["name"].as_str().unwrap_or_default().into(),
                remaining: if passed { limit } else { l["remaining"].as_u64().unwrap_or(0) },
                limit,
                resets_in_secs: reset.filter(|_| !passed).map(|r| r - age),
            }
        })
        .collect();
    Usage::Limits { limits, age_secs: age }
}

/// `x-ratelimit-limit-requests: 50` with `x-ratelimit-remaining-requests: 49`
/// and `x-ratelimit-reset-requests: 59.9` (or `6m0s`) make one limit.
fn parse<'a>(headers: impl Iterator<Item = (&'a str, &'a str)>) -> Vec<Limit> {
    let headers: Vec<(String, &str)> = headers.map(|(k, v)| (k.to_ascii_lowercase(), v)).collect();
    let find = |kind: &str, name: &str| headers.iter().find(|(k, _)| *k == format!("x-ratelimit-{kind}-{name}")).map(|h| h.1.trim());
    let mut out: Vec<Limit> = headers
        .iter()
        .filter_map(|(k, v)| {
            let name = k.strip_prefix("x-ratelimit-limit-")?;
            Some(Limit {
                name: name.into(),
                limit: v.trim().parse().ok()?,
                remaining: find("remaining", name)?.parse().ok()?,
                resets_in_secs: find("reset", name).and_then(seconds),
            })
        })
        .collect();
    out.sort_by(|a, b| a.name.cmp(&b.name));
    out
}

/// "59.999" or "6m0s" or "1h2m3.5s" as whole seconds, rounded up.
fn seconds(s: &str) -> Option<u64> {
    if let Ok(n) = s.parse::<f64>() {
        return Some(n.ceil() as u64);
    }
    let (mut total, mut num) = (0.0, String::new());
    for c in s.chars() {
        match c {
            '0'..='9' | '.' => num.push(c),
            'h' | 'm' | 's' => {
                let n: f64 = std::mem::take(&mut num).parse().ok()?;
                total += n * match c { 'h' => 3600.0, 'm' => 60.0, _ => 1.0 };
            }
            _ => return None,
        }
    }
    num.is_empty().then_some(total.ceil() as u64)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rate_limit_headers_become_limits() {
        let limits = parse(
            [
                ("X-RateLimit-Limit-Requests", "50"),
                ("x-ratelimit-remaining-requests", "49"),
                ("x-ratelimit-reset-requests", "59.2"),
                ("x-ratelimit-limit-tokens-1h", "6000000"),
                ("x-ratelimit-remaining-tokens-1h", "5999968"),
                ("x-ratelimit-reset-tokens-1h", "26m14.9s"),
                ("x-ratelimit-limit-odd", "7"), // no remaining: not a limit
                ("content-type", "application/json"),
            ]
            .into_iter(),
        );
        assert_eq!(limits, vec![
            Limit { name: "requests".into(), remaining: 49, limit: 50, resets_in_secs: Some(60) },
            Limit { name: "tokens-1h".into(), remaining: 5999968, limit: 6000000, resets_in_secs: Some(1575) },
        ]);
    }

    #[test]
    fn limits_are_kept_and_refill_once_reset() {
        let dir = std::env::temp_dir().join(format!("arity-usage-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        assert_eq!(read(&dir, "nous"), Usage::NoneYet);
        let at = now() - 100;
        std::fs::write(dir.join("usage.json"), json!({ "nous": { "at": at, "limits": [
            { "name": "requests", "remaining": 1, "limit": 50, "reset": 60 },
            { "name": "requests-1h", "remaining": 2000, "limit": 2100, "reset": 1000 },
        ] } }).to_string()).unwrap();
        let Usage::Limits { limits, age_secs } = read(&dir, "nous") else { panic!() };
        assert!(age_secs >= 100);
        assert_eq!((limits[0].remaining, limits[0].resets_in_secs), (50, None), "the minute has passed");
        assert_eq!(limits[1].remaining, 2000);
        assert!(limits[1].resets_in_secs.unwrap() <= 900);
        std::fs::remove_dir_all(&dir).unwrap();
    }
}
