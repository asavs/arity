//! Normalized entry-chain serialization and pooled streaming codecs (Chat Completions & Responses).

use std::collections::{BTreeMap, HashMap, HashSet};
use std::io::{BufRead, BufReader};
use std::path::Path;
use std::sync::mpsc;
use std::time::{Duration, Instant};

use anyhow::{Result, anyhow, bail};
use reqwest::blocking::{Client, RequestBuilder, Response};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

use crate::secrets;
use crate::types::{
    Entry, EntryKind, InterruptReason, ModelStreamOutcome, ProviderConfig, RateLimitRecord,
    TokenUsage, ToolCallItem, ToolSpec, WireCodec, now_ms,
};

const USER_AGENT: &str = "Arity/0.0.1";
const CONTINUE_AFTER_INTERRUPT: &str = "[Continue from where you were interrupted.]";

#[derive(Debug, Clone)]
pub struct ResolvedModel {
    pub spec: String,
    pub provider: ProviderConfig,
    pub model_id: String,
    pub bearer_token: Option<String>,
    pub account_id: Option<String>,
}

/// Resolve `<provider>:<model_id>` against `providers` and local secrets before making any HTTP call.
pub fn resolve_model(
    spec: &str,
    data_dir: &Path,
    providers: &[ProviderConfig],
) -> Result<ResolvedModel> {
    let (provider_name, model_id) = spec
        .split_once(':')
        .ok_or_else(|| anyhow!("model spec must be '<provider>:<model_id>', got '{spec}'"))?;
    let provider_name = provider_name.trim();
    let model_id = model_id.trim();
    if provider_name.is_empty() || model_id.is_empty() {
        bail!("model spec must be '<provider>:<model_id>', got '{spec}'");
    }

    let provider = providers
        .iter()
        .find(|p| p.name == provider_name)
        .cloned()
        .ok_or_else(|| {
            let known: Vec<&str> = providers.iter().map(|p| p.name.as_str()).collect();
            anyhow!(
                "unknown provider '{provider_name}'; configured providers: {}",
                known.join(", ")
            )
        })?;

    let (bearer_token, account_id) = if provider.name == "codex" {
        let (token, acc) = secrets::codex_auth::access(data_dir)?;
        let account_id = (!acc.is_empty()).then_some(acc);
        (Some(token), account_id)
    } else if provider.needs_key {
        let key = secrets::keys::get(data_dir, &provider.key_name).ok_or_else(|| {
            anyhow!(
                "provider '{}' requires an API key ('{}'); run `arity key set {}`",
                provider.name,
                provider.key_name,
                provider.key_name
            )
        })?;
        (Some(key), None)
    } else {
        let optional_key = (!provider.key_name.is_empty())
            .then(|| secrets::keys::get(data_dir, &provider.key_name))
            .flatten();
        (optional_key, None)
    };

    Ok(ResolvedModel {
        spec: spec.to_string(),
        provider,
        model_id: model_id.to_string(),
        bearer_token,
        account_id,
    })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum NormalizedTurn {
    User(String),
    Assistant {
        text: String,
        tool_calls: Vec<ToolCallItem>,
    },
    ToolResult {
        call_id: String,
        output: String,
    },
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NormalizedPrompt {
    pub system: String,
    pub turns: Vec<NormalizedTurn>,
}

/// Produce a deterministic cross-provider-valid tool call ID matching `^call_[A-Za-z0-9_]{1,32}$`.
pub fn canonicalize_call_id(raw: &str, seq: usize) -> String {
    if let Some(suffix) = raw.strip_prefix("call_")
        && !suffix.is_empty()
        && suffix.len() <= 32
        && suffix
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'_')
    {
        return raw.to_string();
    }
    let mut hasher = Sha256::new();
    hasher.update((seq as u64).to_le_bytes());
    hasher.update(b":");
    hasher.update(raw.as_bytes());
    let digest = hasher.finalize();
    let hex: String = digest[..12].iter().map(|b| format!("{b:02x}")).collect();
    format!("call_{hex}")
}

fn canonicalize_unique_call_id(
    raw: &str,
    seq: usize,
    used: &mut HashSet<String>,
) -> String {
    let candidate = canonicalize_call_id(raw, seq);
    if used.insert(candidate.clone()) {
        return candidate;
    }
    // Disambiguate colliding raw IDs (e.g. local models reusing "call_1" across turns).
    let mut hasher = Sha256::new();
    hasher.update((seq as u64).to_le_bytes());
    hasher.update(b":dup:");
    hasher.update(raw.as_bytes());
    let digest = hasher.finalize();
    let hex: String = digest[..12].iter().map(|b| format!("{b:02x}")).collect();
    let unique = format!("call_{hex}");
    used.insert(unique.clone());
    unique
}

fn normalize_tool_arguments(val: &Value) -> String {
    match val {
        Value::String(s) => {
            let trimmed = s.trim();
            if trimmed.is_empty() {
                "{}".to_string()
            } else if matches!(serde_json::from_str::<Value>(trimmed), Ok(Value::Object(_))) {
                trimmed.to_string()
            } else {
                "{}".to_string()
            }
        }
        Value::Object(_) => val.to_string(),
        _ => "{}".to_string(),
    }
}

/// Normalize a causal journal chain into a cross-codec-safe prompt representation.
pub fn normalize_chain(causal_chain: &[Entry]) -> NormalizedPrompt {
    let system = causal_chain
        .iter()
        .find(|e| e.kind == EntryKind::ThreadCreated)
        .and_then(|e| e.body["system"].as_str())
        .or_else(|| {
            causal_chain
                .iter()
                .find(|e| e.kind == EntryKind::ThreadForked)
                .and_then(|e| e.body["system"].as_str())
        })
        .unwrap_or_default()
        .to_string();

    let mut turns: Vec<NormalizedTurn> = Vec::new();
    let mut id_map: HashMap<String, String> = HashMap::new();
    let mut used_canon_ids: HashSet<String> = HashSet::new();
    let mut pending_call_ids: HashSet<String> = HashSet::new();
    let mut call_seq: usize = 0;

    for entry in causal_chain {
        match entry.kind {
            EntryKind::DeliveryAccepted => {
                let text = entry.body["text"].as_str().unwrap_or_default();
                if text.is_empty() {
                    continue;
                }
                pending_call_ids.clear();
                if let Some(NormalizedTurn::User(prev)) = turns.last_mut() {
                    prev.push_str("\n\n");
                    prev.push_str(text);
                } else {
                    turns.push(NormalizedTurn::User(text.to_string()));
                }
            }
            EntryKind::ModelCompleted => {
                let text = entry.body["text"].as_str().unwrap_or_default().to_string();
                let mut tool_calls = Vec::new();
                pending_call_ids.clear();

                for c in entry.body["tool_calls"]
                    .as_array()
                    .into_iter()
                    .flatten()
                {
                    call_seq += 1;
                    let orig_id = c["id"]
                        .as_str()
                        .or_else(|| c["call_id"].as_str())
                        .unwrap_or_default();
                    let name = c["name"].as_str().unwrap_or_default().to_string();
                    let arguments = normalize_tool_arguments(&c["arguments"]);
                    let canon_id =
                        canonicalize_unique_call_id(orig_id, call_seq, &mut used_canon_ids);
                    if !orig_id.is_empty() {
                        id_map.insert(orig_id.to_string(), canon_id.clone());
                    }
                    pending_call_ids.insert(canon_id.clone());
                    tool_calls.push(ToolCallItem {
                        id: canon_id,
                        name,
                        arguments,
                    });
                }

                if matches!(turns.last(), Some(NormalizedTurn::Assistant { .. })) {
                    turns.push(NormalizedTurn::User(CONTINUE_AFTER_INTERRUPT.to_string()));
                }
                turns.push(NormalizedTurn::Assistant { text, tool_calls });
            }
            EntryKind::ModelInterrupted => {
                let text = entry.body["text"].as_str().unwrap_or_default();
                if text.trim().is_empty() {
                    continue;
                }
                pending_call_ids.clear();
                if matches!(turns.last(), Some(NormalizedTurn::Assistant { .. })) {
                    turns.push(NormalizedTurn::User(CONTINUE_AFTER_INTERRUPT.to_string()));
                }
                turns.push(NormalizedTurn::Assistant {
                    text: text.to_string(),
                    tool_calls: Vec::new(),
                });
            }
            EntryKind::ToolResult => {
                let Some(orig_id) = entry.body["call_id"].as_str() else {
                    continue;
                };
                let Some(canon_id) = id_map.get(orig_id).cloned() else {
                    continue;
                };
                if !pending_call_ids.remove(&canon_id) {
                    continue;
                }
                let output = match &entry.body["output"] {
                    Value::String(s) => s.clone(),
                    Value::Null => String::new(),
                    other => other.to_string(),
                };
                turns.push(NormalizedTurn::ToolResult {
                    call_id: canon_id,
                    output,
                });
            }
            EntryKind::ThreadCreated
            | EntryKind::ThreadForked
            | EntryKind::DeliveryQueued
            | EntryKind::ThreadStop
            | EntryKind::WorkspaceApplied
            | EntryKind::TurnClaimed
            | EntryKind::ToolCall
            | EntryKind::TurnFinished => {}
        }
    }

    NormalizedPrompt { system, turns }
}

pub trait StreamControl {
    fn on_text_delta(&mut self, delta: &str) -> Option<InterruptReason>;
    fn poll_interrupt(&mut self) -> Option<InterruptReason>;
}

pub struct ModelClient {
    client: Client,
}

impl Default for ModelClient {
    fn default() -> Self {
        Self::new()
    }
}

impl ModelClient {
    pub fn new() -> Self {
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(15))
            .timeout(Duration::from_secs(300))
            .user_agent(USER_AGENT)
            .build()
            .expect("pooled http client");
        Self { client }
    }

    pub fn stream(
        &self,
        resolved: &ResolvedModel,
        session_id: &str,
        causal_chain: &[Entry],
        tools: &[ToolSpec],
        control: &mut dyn StreamControl,
        observed_limits: &mut Vec<RateLimitRecord>,
    ) -> Result<ModelStreamOutcome> {
        if let Some(reason) = control.poll_interrupt() {
            return Ok(ModelStreamOutcome::Interrupted {
                text: String::new(),
                reason,
            });
        }

        let prompt = normalize_chain(causal_chain);
        let result = match resolved.provider.wire {
            WireCodec::ChatCompletions => {
                self.stream_chat(resolved, session_id, &prompt, tools, control, observed_limits)
            }
            WireCodec::Responses => {
                self.stream_responses(resolved, session_id, &prompt, tools, control, observed_limits)
            }
        };

        result.map_err(|err| redact_error(err, resolved.bearer_token.as_deref()))
    }

    fn build_post(
        &self,
        resolved: &ResolvedModel,
        session_id: &str,
        path: &str,
    ) -> RequestBuilder {
        let base = resolved.provider.url.trim_end_matches('/');
        let mut req = self.client.post(format!("{base}{path}"));
        if let Some(token) = &resolved.bearer_token {
            req = req.bearer_auth(token);
        }
        if let Some(header_name) = resolved
            .provider
            .session_header
            .as_deref()
            .filter(|h| !h.is_empty())
            && !session_id.is_empty()
        {
            req = req.header(header_name, format_session_id(session_id));
        }
        if resolved.provider.wire == WireCodec::Responses
            && let Some(account_id) = resolved
                .account_id
                .as_deref()
                .filter(|a| !a.is_empty())
        {
            req = req.header("ChatGPT-Account-Id", account_id);
        }
        req
    }

    fn stream_chat(
        &self,
        resolved: &ResolvedModel,
        session_id: &str,
        prompt: &NormalizedPrompt,
        tools: &[ToolSpec],
        control: &mut dyn StreamControl,
        observed_limits: &mut Vec<RateLimitRecord>,
    ) -> Result<ModelStreamOutcome> {
        let started = Instant::now();
        let mut body = build_chat_request_body(&resolved.model_id, prompt, tools);

        let mut resp = self
            .build_post(resolved, session_id, "/chat/completions")
            .json(&body)
            .send()?;
        observe_rate_limits(&resolved.provider.name, &resp, observed_limits);

        let mut status = resp.status();
        if !status.is_success() {
            let detail = resp.text().unwrap_or_default();
            if matches!(status.as_u16(), 400 | 422) {
                if let Some(obj) = body.as_object_mut() {
                    obj.remove("stream_options");
                }
                resp = self
                    .build_post(resolved, session_id, "/chat/completions")
                    .json(&body)
                    .send()?;
                observe_rate_limits(&resolved.provider.name, &resp, observed_limits);
                status = resp.status();
                if !status.is_success() {
                    bail!("{status}: {}", resp.text().unwrap_or_default());
                }
            } else {
                bail!("{status}: {detail}");
            }
        }

        parse_chat_stream(SseStream::new(resp), started, control)
    }

    fn stream_responses(
        &self,
        resolved: &ResolvedModel,
        session_id: &str,
        prompt: &NormalizedPrompt,
        tools: &[ToolSpec],
        control: &mut dyn StreamControl,
        observed_limits: &mut Vec<RateLimitRecord>,
    ) -> Result<ModelStreamOutcome> {
        let started = Instant::now();
        let body = build_responses_request_body(&resolved.model_id, prompt, tools);

        let resp = self
            .build_post(resolved, session_id, "/responses")
            .json(&body)
            .send()?;
        observe_rate_limits(&resolved.provider.name, &resp, observed_limits);

        let status = resp.status();
        if !status.is_success() {
            bail!("{status}: {}", resp.text().unwrap_or_default());
        }

        parse_responses_stream(SseStream::new(resp), started, control)
    }
}

fn redact_error(err: anyhow::Error, secret: Option<&str>) -> anyhow::Error {
    let raw = format!("{err:#}");
    let clean = secrets::keys::redact(&raw, secret);
    if clean != raw {
        anyhow!("{clean}")
    } else {
        err
    }
}

fn observe_rate_limits(
    provider: &str,
    resp: &Response,
    observed_limits: &mut Vec<RateLimitRecord>,
) {
    let limits = secrets::rate_limits::parse_headers(
        provider,
        resp.headers()
            .iter()
            .filter_map(|(k, v)| Some((k.as_str(), v.to_str().ok()?))),
        now_ms(),
    );
    observed_limits.extend(limits);
}

fn is_hex_session_id(s: &str) -> bool {
    s.len() == 32 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

fn format_session_id(session_id: &str) -> String {
    if is_hex_session_id(session_id) {
        session_id.to_string()
    } else {
        Sha256::digest(session_id.as_bytes())[..16]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect()
    }
}

pub fn build_chat_messages(prompt: &NormalizedPrompt) -> Vec<Value> {
    let mut messages = Vec::new();
    if !prompt.system.is_empty() {
        messages.push(json!({ "role": "system", "content": prompt.system }));
    }
    for turn in &prompt.turns {
        match turn {
            NormalizedTurn::User(text) => {
                messages.push(json!({ "role": "user", "content": text }));
            }
            NormalizedTurn::Assistant { text, tool_calls } => {
                let content = if text.is_empty() && !tool_calls.is_empty() {
                    Value::Null
                } else {
                    json!(text)
                };
                let mut m = json!({ "role": "assistant", "content": content });
                if !tool_calls.is_empty() {
                    m["tool_calls"] = tool_calls
                        .iter()
                        .map(|c| {
                            json!({
                                "id": c.id,
                                "type": "function",
                                "function": {
                                    "name": c.name,
                                    "arguments": c.arguments,
                                }
                            })
                        })
                        .collect();
                }
                messages.push(m);
            }
            NormalizedTurn::ToolResult { call_id, output } => {
                messages.push(json!({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": output,
                }));
            }
        }
    }
    messages
}

fn build_chat_request_body(
    model_id: &str,
    prompt: &NormalizedPrompt,
    tools: &[ToolSpec],
) -> Value {
    let mut body = json!({
        "model": model_id,
        "messages": build_chat_messages(prompt),
        "stream": true,
        "stream_options": { "include_usage": true },
    });
    if !tools.is_empty() {
        body["tools"] = tools
            .iter()
            .map(|t| {
                json!({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    }
                })
            })
            .collect();
    }
    body
}

pub fn build_responses_input(prompt: &NormalizedPrompt) -> Vec<Value> {
    let mut input = Vec::new();
    for turn in &prompt.turns {
        match turn {
            NormalizedTurn::User(text) => {
                input.push(json!({
                    "role": "user",
                    "content": [{ "type": "input_text", "text": text }],
                }));
            }
            NormalizedTurn::Assistant { text, tool_calls } => {
                if !text.is_empty() {
                    input.push(json!({
                        "role": "assistant",
                        "content": [{ "type": "output_text", "text": text }],
                    }));
                }
                for c in tool_calls {
                    input.push(json!({
                        "type": "function_call",
                        "call_id": c.id,
                        "name": c.name,
                        "arguments": c.arguments,
                    }));
                }
            }
            NormalizedTurn::ToolResult { call_id, output } => {
                input.push(json!({
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                }));
            }
        }
    }
    input
}

fn build_responses_request_body(
    model_id: &str,
    prompt: &NormalizedPrompt,
    tools: &[ToolSpec],
) -> Value {
    let mut body = json!({
        "model": model_id,
        "instructions": prompt.system,
        "input": build_responses_input(prompt),
        "stream": true,
        "store": false,
    });
    if !tools.is_empty() {
        body["tools"] = tools
            .iter()
            .map(|t| {
                json!({
                    "type": "function",
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "strict": false,
                })
            })
            .collect();
    }
    body
}

enum SseNext {
    Event(Value),
    Done,
    Eof,
    Interrupted(InterruptReason),
}

struct SseStream {
    rx: mpsc::Receiver<std::io::Result<String>>,
}

impl SseStream {
    fn new(resp: impl std::io::Read + Send + 'static) -> Self {
        let (tx, rx) = mpsc::channel();
        std::thread::spawn(move || {
            for line in BufReader::new(resp).lines() {
                if tx.send(line).is_err() {
                    break;
                }
            }
        });
        Self { rx }
    }

    fn next(&mut self, control: &mut dyn StreamControl) -> Result<SseNext> {
        loop {
            if let Some(reason) = control.poll_interrupt() {
                return Ok(SseNext::Interrupted(reason));
            }
            match self.rx.recv_timeout(Duration::from_millis(50)) {
                Ok(line) => {
                    let line = line?;
                    let Some(data) = line.strip_prefix("data:").map(str::trim) else {
                        continue;
                    };
                    if data.is_empty() {
                        continue;
                    }
                    if data == "[DONE]" {
                        return Ok(SseNext::Done);
                    }
                    match serde_json::from_str::<Value>(data) {
                        Ok(v) => return Ok(SseNext::Event(v)),
                        Err(e) if data.starts_with('{') || data.starts_with('[') => {
                            return Err(anyhow!("malformed SSE JSON chunk: {e}"));
                        }
                        Err(_) => continue,
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
                Err(mpsc::RecvTimeoutError::Disconnected) => return Ok(SseNext::Eof),
            }
        }
    }
}

#[derive(Debug, Default)]
struct PartialToolCall {
    id: String,
    name: String,
    arguments: String,
}

fn parse_chat_usage(u: &Value) -> Option<TokenUsage> {
    let input = u["prompt_tokens"].as_u64()?;
    let output = u["completion_tokens"].as_u64().unwrap_or(0);
    let cached = u["prompt_tokens_details"]["cached_tokens"]
        .as_u64()
        .or_else(|| u["prompt_cache_hit_tokens"].as_u64())
        .unwrap_or(0);
    let total = u["total_tokens"]
        .as_u64()
        .unwrap_or_else(|| input.saturating_add(output));
    Some(TokenUsage {
        input,
        cached,
        output,
        total,
    })
}

fn parse_responses_usage(u: &Value) -> Option<TokenUsage> {
    let input = u["input_tokens"].as_u64()?;
    let output = u["output_tokens"].as_u64().unwrap_or(0);
    let cached = u["input_tokens_details"]["cached_tokens"]
        .as_u64()
        .unwrap_or(0);
    let total = u["total_tokens"]
        .as_u64()
        .unwrap_or_else(|| input.saturating_add(output));
    Some(TokenUsage {
        input,
        cached,
        output,
        total,
    })
}

fn parse_chat_stream(
    mut stream: SseStream,
    started: Instant,
    control: &mut dyn StreamControl,
) -> Result<ModelStreamOutcome> {
    let mut text = String::new();
    let mut partials: BTreeMap<usize, PartialToolCall> = BTreeMap::new();
    let mut usage: Option<TokenUsage> = None;
    let mut finish_reason: Option<String> = None;
    let mut saw_done = false;
    let mut ttft_ms: Option<u64> = None;

    loop {
        match stream.next(control)? {
            SseNext::Interrupted(reason) => {
                drop(stream);
                return Ok(ModelStreamOutcome::Interrupted { text, reason });
            }
            SseNext::Done => {
                saw_done = true;
                break;
            }
            SseNext::Eof => break,
            SseNext::Event(ev) => {
                if !ev["error"].is_null() {
                    bail!("chat completions stream error: {}", ev["error"]);
                }
                if let Some(u) = parse_chat_usage(&ev["usage"]) {
                    usage = Some(u);
                }
                if let Some(choice) = ev["choices"].get(0) {
                    if let Some(fr) = choice["finish_reason"].as_str().filter(|s| !s.is_empty()) {
                        finish_reason = Some(fr.to_string());
                    }
                    let delta = &choice["delta"];
                    if let Some(t) = delta["content"].as_str().filter(|t| !t.is_empty()) {
                        if ttft_ms.is_none() {
                            ttft_ms = Some(started.elapsed().as_millis() as u64);
                        }
                        text.push_str(t);
                        if let Some(reason) = control.on_text_delta(t) {
                            drop(stream);
                            return Ok(ModelStreamOutcome::Interrupted { text, reason });
                        }
                    }

                    for c in delta["tool_calls"].as_array().into_iter().flatten() {
                        if ttft_ms.is_none() {
                            ttft_ms = Some(started.elapsed().as_millis() as u64);
                        }
                        let idx = c["index"]
                            .as_u64()
                            .map(|i| i as usize)
                            .unwrap_or_else(|| {
                                let last_idx = partials.keys().next_back().copied();
                                match last_idx {
                                    None => 0,
                                    Some(last) => {
                                        let has_new_id = c["id"]
                                            .as_str()
                                            .filter(|s| !s.is_empty())
                                            .is_some_and(|id| {
                                                partials
                                                    .get(&last)
                                                    .is_some_and(|p| !p.id.is_empty() && p.id != id)
                                            });
                                        if has_new_id { last + 1 } else { last }
                                    }
                                }
                            });
                        let entry = partials.entry(idx).or_default();
                        if let Some(id) = c["id"].as_str().filter(|s| !s.is_empty()) {
                            entry.id = id.to_string();
                        }
                        if let Some(name) = c["function"]["name"]
                            .as_str()
                            .filter(|s| !s.is_empty() && entry.name.is_empty())
                        {
                            entry.name = name.to_string();
                        }
                        match &c["function"]["arguments"] {
                            Value::String(s) => entry.arguments.push_str(s),
                            Value::Null => {}
                            other => entry.arguments.push_str(&other.to_string()),
                        }
                    }
                }
            }
        }
    }

    // Truncation gate: never return Completed on truncated or malformed streams.
    if !saw_done {
        return Err(anyhow!(
            "truncated or incomplete chat completions stream: missing data: [DONE]"
        ));
    }
    if finish_reason.as_deref() == Some("length") {
        return Err(anyhow!(
            "truncated or incomplete chat completions stream: finish_reason=length"
        ));
    }

    let mut tool_calls = Vec::with_capacity(partials.len());
    for (seq, (_, partial)) in partials.into_iter().enumerate() {
        let name = partial.name.trim().to_string();
        if name.is_empty() {
            return Err(anyhow!(
                "truncated or incomplete chat completions stream: tool call missing function name"
            ));
        }

        let trimmed_args = partial.arguments.trim();
        let arguments = if trimmed_args.is_empty() {
            if matches!(finish_reason.as_deref(), Some("tool_calls") | Some("stop")) {
                "{}".to_string()
            } else {
                return Err(anyhow!(
                    "truncated or incomplete chat completions stream: empty arguments for tool '{name}' with finish_reason={finish_reason:?}"
                ));
            }
        } else {
            let parsed: Value = serde_json::from_str(trimmed_args).map_err(|e| {
                anyhow!(
                    "truncated or incomplete chat completions stream: invalid JSON arguments for tool '{name}': {e}"
                )
            })?;
            if !parsed.is_object() {
                return Err(anyhow!(
                    "truncated or incomplete chat completions stream: arguments for tool '{name}' must be a JSON object"
                ));
            }
            trimmed_args.to_string()
        };

        let id = if partial.id.trim().is_empty() {
            canonicalize_call_id("", seq + 1)
        } else {
            partial.id
        };

        tool_calls.push(ToolCallItem {
            id,
            name,
            arguments,
        });
    }

    Ok(ModelStreamOutcome::Completed {
        text,
        tool_calls,
        usage,
        ttft_ms,
        duration_ms: started.elapsed().as_millis() as u64,
    })
}

fn parse_responses_stream(
    mut stream: SseStream,
    started: Instant,
    control: &mut dyn StreamControl,
) -> Result<ModelStreamOutcome> {
    let mut delta_text = String::new();
    let mut done_text = String::new();
    let mut tool_calls: Vec<ToolCallItem> = Vec::new();
    let mut usage: Option<TokenUsage> = None;
    let mut saw_completed = false;
    let mut ttft_ms: Option<u64> = None;

    loop {
        match stream.next(control)? {
            SseNext::Interrupted(reason) => {
                drop(stream);
                return Ok(ModelStreamOutcome::Interrupted {
                    text: delta_text,
                    reason,
                });
            }
            SseNext::Done | SseNext::Eof => break,
            SseNext::Event(ev) => match ev["type"].as_str().unwrap_or_default() {
                "response.output_text.delta" => {
                    if let Some(t) = ev["delta"].as_str().filter(|t| !t.is_empty()) {
                        if ttft_ms.is_none() {
                            ttft_ms = Some(started.elapsed().as_millis() as u64);
                        }
                        delta_text.push_str(t);
                        if let Some(reason) = control.on_text_delta(t) {
                            drop(stream);
                            return Ok(ModelStreamOutcome::Interrupted {
                                text: delta_text,
                                reason,
                            });
                        }
                    }
                }
                "response.function_call_arguments.delta" => {
                    if ttft_ms.is_none() {
                        ttft_ms = Some(started.elapsed().as_millis() as u64);
                    }
                }
                "response.output_item.done" => {
                    let item = &ev["item"];
                    match item["type"].as_str().unwrap_or_default() {
                        "message" => {
                            for part in item["content"].as_array().into_iter().flatten() {
                                if let Some(t) = part["text"].as_str() {
                                    done_text.push_str(t);
                                }
                            }
                        }
                        "function_call" => {
                            if ttft_ms.is_none() {
                                ttft_ms = Some(started.elapsed().as_millis() as u64);
                            }
                            let name = item["name"].as_str().unwrap_or_default().trim().to_string();
                            if name.is_empty() {
                                bail!("responses stream error: function_call missing name");
                            }
                            let arguments = match &item["arguments"] {
                                Value::String(s) => {
                                    let trimmed = s.trim();
                                    let parsed: Value =
                                        serde_json::from_str(trimmed).map_err(|e| {
                                            anyhow!(
                                                "responses stream error: invalid JSON arguments for function_call '{name}': {e}"
                                            )
                                        })?;
                                    if !parsed.is_object() {
                                        bail!(
                                            "responses stream error: arguments for function_call '{name}' must be a JSON object"
                                        );
                                    }
                                    trimmed.to_string()
                                }
                                Value::Object(_) => item["arguments"].to_string(),
                                _ => bail!(
                                    "responses stream error: arguments for function_call '{name}' must be a JSON object"
                                ),
                            };
                            let raw_id = item["call_id"]
                                .as_str()
                                .or_else(|| item["id"].as_str())
                                .unwrap_or_default()
                                .trim();
                            let id = if raw_id.is_empty() {
                                canonicalize_call_id("", tool_calls.len() + 1)
                            } else {
                                raw_id.to_string()
                            };
                            tool_calls.push(ToolCallItem {
                                id,
                                name,
                                arguments,
                            });
                        }
                        _ => {}
                    }
                }
                "response.failed" | "response.incomplete" | "response.error" | "error" => {
                    let kind = ev["type"].as_str().unwrap_or("error");
                    bail!("responses stream {kind}: {ev}");
                }
                "response.completed" => {
                    if let Some(status) = ev["response"]["status"].as_str()
                        && matches!(status, "incomplete" | "failed")
                    {
                        bail!("responses stream completed with status={status}: {ev}");
                    }
                    saw_completed = true;
                    usage = parse_responses_usage(&ev["response"]["usage"]);
                    break;
                }
                _ => {}
            },
        }
    }

    if !saw_completed {
        return Err(anyhow!(
            "truncated responses stream: missing response.completed"
        ));
    }

    let text = if done_text.is_empty() && !delta_text.is_empty() {
        delta_text
    } else {
        done_text
    };

    Ok(ModelStreamOutcome::Completed {
        text,
        tool_calls,
        usage,
        ttft_ms,
        duration_ms: started.elapsed().as_millis() as u64,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    use std::sync::{Arc, Mutex};

    fn make_entry(id: i64, kind: EntryKind, body: Value) -> Entry {
        Entry {
            id,
            thread_id: "t1".into(),
            parent_id: (id > 1).then_some(id - 1),
            epoch: 1,
            kind,
            request_id: None,
            actor: "test".into(),
            tree_sha: None,
            body,
            created_ms: 1000 + id,
        }
    }

    struct NoopControl {
        deltas: String,
    }

    impl NoopControl {
        fn new() -> Self {
            Self {
                deltas: String::new(),
            }
        }
    }

    impl StreamControl for NoopControl {
        fn on_text_delta(&mut self, delta: &str) -> Option<InterruptReason> {
            self.deltas.push_str(delta);
            None
        }
        fn poll_interrupt(&mut self) -> Option<InterruptReason> {
            None
        }
    }

    struct SteerAfterChunks {
        remaining_deltas: usize,
        deltas: String,
    }

    impl StreamControl for SteerAfterChunks {
        fn on_text_delta(&mut self, delta: &str) -> Option<InterruptReason> {
            self.deltas.push_str(delta);
            if self.remaining_deltas == 0 {
                return Some(InterruptReason::Steer);
            }
            self.remaining_deltas -= 1;
            if self.remaining_deltas == 0 {
                Some(InterruptReason::Steer)
            } else {
                None
            }
        }
        fn poll_interrupt(&mut self) -> Option<InterruptReason> {
            None
        }
    }

    #[derive(Debug, Clone)]
    struct CapturedRequest {
        request_line: String,
        headers: HashMap<String, String>,
        body: Value,
    }

    fn spawn_mock_server(
        handler: impl Fn(CapturedRequest) -> (String, Vec<(String, String)>, String) + Send + 'static,
    ) -> Result<(String, Arc<Mutex<Vec<CapturedRequest>>>)> {
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let port = listener.local_addr()?.port();
        let captured = Arc::new(Mutex::new(Vec::new()));
        let captured_bg = captured.clone();

        std::thread::spawn(move || {
            for mut stream in listener.incoming().flatten() {
                let mut buf = Vec::new();
                let mut chunk = [0u8; 8192];
                let req = loop {
                    let n = stream.read(&mut chunk).unwrap_or(0);
                    if n == 0 {
                        break None;
                    }
                    buf.extend_from_slice(&chunk[..n]);
                    let raw = String::from_utf8_lossy(&buf);
                    let Some(head_end) = raw.find("\r\n\r\n") else {
                        continue;
                    };
                    let head = &raw[..head_end];
                    let mut lines = head.lines();
                    let request_line = lines.next().unwrap_or_default().to_string();
                    let mut headers = HashMap::new();
                    for line in lines {
                        if let Some((k, v)) = line.split_once(':') {
                            headers.insert(k.trim().to_ascii_lowercase(), v.trim().to_string());
                        }
                    }
                    let content_len = headers
                        .get("content-length")
                        .and_then(|v| v.parse::<usize>().ok())
                        .unwrap_or(0);
                    if buf.len() >= head_end + 4 + content_len {
                        let body_slice = &buf[head_end + 4..head_end + 4 + content_len];
                        let body = serde_json::from_slice(body_slice).unwrap_or(Value::Null);
                        break Some(CapturedRequest {
                            request_line,
                            headers,
                            body,
                        });
                    }
                };

                let Some(req) = req else { continue };
                captured_bg.lock().unwrap().push(req.clone());
                let (status, extra_headers, body) = handler(req);
                let mut resp = format!(
                    "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n",
                    body.len()
                );
                for (k, v) in extra_headers {
                    resp.push_str(&format!("{k}: {v}\r\n"));
                }
                resp.push_str("\r\n");
                resp.push_str(&body);
                let _ = stream.write_all(resp.as_bytes());
                let _ = stream.flush();
            }
        });

        Ok((format!("http://127.0.0.1:{port}/v1"), captured))
    }

    fn test_provider(name: &str, url: String, wire: WireCodec) -> ProviderConfig {
        ProviderConfig {
            name: name.into(),
            url,
            wire,
            needs_key: false,
            key_name: String::new(),
            session_header: None,
            models: vec![],
        }
    }

    fn is_valid_canonical_id(id: &str) -> bool {
        let Some(suffix) = id.strip_prefix("call_") else {
            return false;
        };
        !suffix.is_empty()
            && suffix.len() <= 32
            && suffix
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_')
    }

    #[test]
    fn canonicalize_call_id_is_deterministic_and_valid() {
        let responses_id = "fc_6801a2b3-c4d5-6789-abcd-ef0123456789_very_long_id";
        let c1 = canonicalize_call_id(responses_id, 1);
        let c2 = canonicalize_call_id(responses_id, 1);
        assert_eq!(c1, c2);
        assert!(is_valid_canonical_id(&c1), "got {c1}");

        let already_ok = canonicalize_call_id("call_AbC_123", 2);
        assert_eq!(already_ok, "call_AbC_123");
        assert!(is_valid_canonical_id(&already_ok));

        let too_long = format!("call_{}", "a".repeat(40));
        let fixed = canonicalize_call_id(&too_long, 3);
        assert!(is_valid_canonical_id(&fixed), "got {fixed}");
        assert_ne!(fixed, too_long);
    }

    #[test]
    fn normalize_chain_cross_codec_coalescing_bridging_and_null_content() {
        let chain = vec![
            make_entry(
                1,
                EntryKind::ThreadCreated,
                json!({ "system": "You are Arity." }),
            ),
            make_entry(
                2,
                EntryKind::DeliveryAccepted,
                json!({ "text": "First part" }),
            ),
            make_entry(
                3,
                EntryKind::DeliveryAccepted,
                json!({ "text": "Second part" }),
            ),
            // Orphan tool result before any tool call must be dropped.
            make_entry(
                4,
                EntryKind::ToolResult,
                json!({ "call_id": "fc_orphan", "output": "nope" }),
            ),
            // Responses-style call_id with empty assistant text and empty arguments.
            make_entry(
                5,
                EntryKind::ModelCompleted,
                json!({
                    "text": "",
                    "tool_calls": [
                        { "id": "fc_resp-001.abc", "name": "read_file", "arguments": "" }
                    ]
                }),
            ),
            make_entry(
                6,
                EntryKind::ToolCall,
                json!({ "call_id": "fc_resp-001.abc", "name": "read_file" }),
            ),
            make_entry(
                7,
                EntryKind::ToolResult,
                json!({ "call_id": "fc_resp-001.abc", "output": "file contents" }),
            ),
            // Duplicate tool result must also be dropped.
            make_entry(
                8,
                EntryKind::ToolResult,
                json!({ "call_id": "fc_resp-001.abc", "output": "duplicate" }),
            ),
            // Interrupted assistant turn followed directly by another assistant turn (resumed without user text).
            make_entry(
                9,
                EntryKind::ModelInterrupted,
                json!({
                    "text": "Partial answer",
                    "tool_calls": [{ "id": "fc_dropped", "name": "bad", "arguments": "{}" }]
                }),
            ),
            make_entry(
                10,
                EntryKind::ModelCompleted,
                json!({ "text": "Finished answer", "tool_calls": [] }),
            ),
        ];

        let prompt = normalize_chain(&chain);
        assert_eq!(prompt.system, "You are Arity.");
        assert_eq!(prompt.turns.len(), 6);

        // Coalesced user turn.
        assert_eq!(
            prompt.turns[0],
            NormalizedTurn::User("First part\n\nSecond part".into())
        );

        // Canonicalized call ID mapped identically across Assistant and ToolResult.
        let canon_id = match &prompt.turns[1] {
            NormalizedTurn::Assistant { text, tool_calls } => {
                assert_eq!(text, "");
                assert_eq!(tool_calls.len(), 1);
                assert_eq!(tool_calls[0].name, "read_file");
                assert_eq!(tool_calls[0].arguments, "{}");
                assert!(is_valid_canonical_id(&tool_calls[0].id));
                tool_calls[0].id.clone()
            }
            other => panic!("expected assistant tool call turn, got {other:?}"),
        };
        assert_eq!(
            prompt.turns[2],
            NormalizedTurn::ToolResult {
                call_id: canon_id.clone(),
                output: "file contents".into()
            }
        );

        // Interrupted turn had tool calls stripped, bridged by synthetic user turn before next assistant turn.
        assert_eq!(
            prompt.turns[3],
            NormalizedTurn::Assistant {
                text: "Partial answer".into(),
                tool_calls: vec![],
            }
        );
        assert_eq!(
            prompt.turns[4],
            NormalizedTurn::User(CONTINUE_AFTER_INTERRUPT.into())
        );
        assert_eq!(
            prompt.turns[5],
            NormalizedTurn::Assistant {
                text: "Finished answer".into(),
                tool_calls: vec![],
            }
        );

        // Chat Completions wire serialization: empty text + tool_calls => "content": null.
        let chat_msgs = build_chat_messages(&prompt);
        assert_eq!(chat_msgs[0], json!({ "role": "system", "content": "You are Arity." }));
        assert_eq!(chat_msgs[2]["role"], "assistant");
        assert!(chat_msgs[2]["content"].is_null(), "got {}", chat_msgs[2]);
        assert_eq!(chat_msgs[2]["tool_calls"][0]["id"], canon_id);
        assert_eq!(chat_msgs[3]["role"], "tool");
        assert_eq!(chat_msgs[3]["tool_call_id"], canon_id);
    }

    #[test]
    fn resolve_model_requires_key_before_http_and_uses_codex_auth() -> Result<()> {
        let nonce: u64 = rand::random();
        let dir = std::env::temp_dir().join(format!(
            "arity-codec-resolve-{}-{nonce:08x}",
            std::process::id()
        ));
        std::fs::create_dir_all(&dir)?;

        let providers = vec![
            ProviderConfig {
                name: "openai".into(),
                url: "https://api.openai.com/v1".into(),
                wire: WireCodec::ChatCompletions,
                needs_key: true,
                key_name: "openai".into(),
                session_header: None,
                models: vec![],
            },
            ProviderConfig {
                name: "mlx".into(),
                url: "http://127.0.0.1:8080/v1".into(),
                wire: WireCodec::ChatCompletions,
                needs_key: false,
                key_name: String::new(),
                session_header: None,
                models: vec![],
            },
        ];

        // Missing key fails clearly.
        let err = resolve_model("openai:gpt-4.1", &dir, &providers)
            .unwrap_err()
            .to_string();
        assert!(err.contains("arity key set openai"), "{err}");

        // Setting key succeeds.
        secrets::keys::set(&dir, "openai", "sk-openai-test")?;
        let resolved = resolve_model("openai:gpt-4.1", &dir, &providers)?;
        assert_eq!(resolved.model_id, "gpt-4.1");
        assert_eq!(resolved.bearer_token.as_deref(), Some("sk-openai-test"));
        assert_eq!(resolved.account_id, None);

        // Local provider without key succeeds.
        let local = resolve_model("mlx:mlx-community/Qwen3.5-4B-MLX-4bit", &dir, &providers)?;
        assert_eq!(local.model_id, "mlx-community/Qwen3.5-4B-MLX-4bit");
        assert_eq!(local.bearer_token, None);

        // Codex provider loads bearer token and ChatGPT account_id from codex-auth.json.
        let codex_providers = vec![ProviderConfig {
            name: "codex".into(),
            url: "https://api.openai.com/v1".into(),
            wire: WireCodec::Responses,
            needs_key: false,
            key_name: String::new(),
            session_header: None,
            models: vec![],
        }];
        assert!(resolve_model("codex:gpt-6.1-terra", &dir, &codex_providers).is_err());

        use base64::Engine;
        use base64::engine::general_purpose::URL_SAFE_NO_PAD as B64;
        let claims = B64.encode(
            json!({ "https://api.openai.com/auth": { "chatgpt_account_id": "acct_codex_9" } })
                .to_string(),
        );
        let jwt = format!("h.{claims}.s");
        let future = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_secs()
            + 3600;
        std::fs::write(
            dir.join("codex-auth.json"),
            json!({
                "client_id": "oaiapp_1",
                "host_id": "urn:uuid:1",
                "access_token": jwt,
                "refresh_token": "rt_1",
                "expires_at": future
            })
            .to_string(),
        )?;
        let codex = resolve_model("codex:gpt-6.1-terra", &dir, &codex_providers)?;
        assert_eq!(codex.bearer_token.as_deref(), Some(jwt.as_str()));
        assert_eq!(codex.account_id.as_deref(), Some("acct_codex_9"));

        std::fs::remove_dir_all(&dir)?;
        Ok(())
    }

    #[test]
    fn chat_completions_omits_empty_tools_no_max_tokens_retries_stream_options_and_parses_limits()
    -> Result<()> {
        let calls = Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let calls_bg = calls.clone();
        let (url, captured) = spawn_mock_server(move |req| {
            let n = calls_bg.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            if n == 0 {
                assert!(req.body.get("stream_options").is_some());
                assert!(req.body.get("max_tokens").is_none());
                assert!(req.body.get("tools").is_none());
                (
                    "400 Bad Request".into(),
                    vec![("Content-Type".into(), "application/json".into())],
                    json!({ "error": "stream_options not supported" }).to_string(),
                )
            } else {
                assert!(req.body.get("stream_options").is_none());
                let sse = format!(
                    "data: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
                    json!({ "choices": [{ "delta": { "content": "Hello " }, "finish_reason": null }] }),
                    json!({ "choices": [{ "delta": { "content": "world" }, "finish_reason": "stop" }] }),
                );
                (
                    "200 OK".into(),
                    vec![
                        ("Content-Type".into(), "text/event-stream".into()),
                        ("x-ratelimit-limit-requests".into(), "100".into()),
                        ("x-ratelimit-remaining-requests".into(), "99".into()),
                        ("x-ratelimit-reset-requests".into(), "12.4".into()),
                    ],
                    sse,
                )
            }
        })?;

        let resolved = ResolvedModel {
            spec: "local:test".into(),
            provider: test_provider("local", url, WireCodec::ChatCompletions),
            model_id: "test".into(),
            bearer_token: None,
            account_id: None,
        };
        let chain = vec![make_entry(
            1,
            EntryKind::DeliveryAccepted,
            json!({ "text": "hi" }),
        )];
        let client = ModelClient::new();
        let mut ctrl = NoopControl::new();
        let mut limits = Vec::new();

        let outcome = client.stream(&resolved, "sess-1", &chain, &[], &mut ctrl, &mut limits)?;
        assert_eq!(captured.lock().unwrap().len(), 2);
        assert_eq!(ctrl.deltas, "Hello world");
        assert_eq!(limits.len(), 1);
        assert_eq!(limits[0].limit_name, "requests");
        assert_eq!(limits[0].reset_secs, Some(13));
        match outcome {
            ModelStreamOutcome::Completed {
                text,
                tool_calls,
                ttft_ms,
                ..
            } => {
                assert_eq!(text, "Hello world");
                assert!(tool_calls.is_empty());
                assert!(ttft_ms.is_some());
            }
            other => panic!("expected Completed, got {other:?}"),
        }
        Ok(())
    }

    #[test]
    fn chat_completions_truncation_gate_rejects_missing_done_and_finish_reason_length() -> Result<()>
    {
        // Case 1: missing data: [DONE]
        let (url1, _) = spawn_mock_server(|_| {
            let sse = format!(
                "data: {}\n\n",
                json!({ "choices": [{ "delta": { "content": "cut off" }, "finish_reason": "stop" }] })
            );
            (
                "200 OK".into(),
                vec![("Content-Type".into(), "text/event-stream".into())],
                sse,
            )
        })?;
        let r1 = ResolvedModel {
            spec: "p:m".into(),
            provider: test_provider("p", url1, WireCodec::ChatCompletions),
            model_id: "m".into(),
            bearer_token: None,
            account_id: None,
        };
        let client = ModelClient::new();
        let mut ctrl = NoopControl::new();
        let mut limits = Vec::new();
        let err1 = client
            .stream(&r1, "s", &[], &[], &mut ctrl, &mut limits)
            .unwrap_err()
            .to_string();
        assert!(err1.contains("[DONE]"), "{err1}");

        // Case 2: finish_reason == "length" even with [DONE]
        let (url2, _) = spawn_mock_server(|_| {
            let sse = format!(
                "data: {}\n\ndata: [DONE]\n\n",
                json!({ "choices": [{ "delta": { "content": "too long" }, "finish_reason": "length" }] })
            );
            (
                "200 OK".into(),
                vec![("Content-Type".into(), "text/event-stream".into())],
                sse,
            )
        })?;
        let r2 = ResolvedModel {
            spec: "p:m".into(),
            provider: test_provider("p", url2, WireCodec::ChatCompletions),
            model_id: "m".into(),
            bearer_token: None,
            account_id: None,
        };
        let err2 = client
            .stream(&r2, "s", &[], &[], &mut ctrl, &mut limits)
            .unwrap_err()
            .to_string();
        assert!(err2.contains("length"), "{err2}");

        // Case 3: malformed tool call JSON arguments rejected
        let (url3, _) = spawn_mock_server(|_| {
            let sse = format!(
                "data: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
                json!({
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_1",
                                "function": { "name": "read_file", "arguments": "{\"path\":" }
                            }]
                        },
                        "finish_reason": null
                    }]
                }),
                json!({ "choices": [{ "delta": {}, "finish_reason": "tool_calls" }] }),
            );
            (
                "200 OK".into(),
                vec![("Content-Type".into(), "text/event-stream".into())],
                sse,
            )
        })?;
        let r3 = ResolvedModel {
            spec: "p:m".into(),
            provider: test_provider("p", url3, WireCodec::ChatCompletions),
            model_id: "m".into(),
            bearer_token: None,
            account_id: None,
        };
        let err3 = client
            .stream(&r3, "s", &[], &[], &mut ctrl, &mut limits)
            .unwrap_err()
            .to_string();
        assert!(err3.contains("invalid JSON"), "{err3}");

        Ok(())
    }

    #[test]
    fn responses_truncation_gate_rejects_missing_completed_and_incomplete_event() -> Result<()> {
        // Case 1: EOF without response.completed
        let (url1, _) = spawn_mock_server(|_| {
            let sse = format!(
                "data: {}\n\n",
                json!({ "type": "response.output_text.delta", "delta": "partial" })
            );
            (
                "200 OK".into(),
                vec![("Content-Type".into(), "text/event-stream".into())],
                sse,
            )
        })?;
        let r1 = ResolvedModel {
            spec: "codex:gpt".into(),
            provider: test_provider("codex", url1, WireCodec::Responses),
            model_id: "gpt".into(),
            bearer_token: Some("tok".into()),
            account_id: None,
        };
        let client = ModelClient::new();
        let mut ctrl = NoopControl::new();
        let mut limits = Vec::new();
        let err1 = client
            .stream(&r1, "s", &[], &[], &mut ctrl, &mut limits)
            .unwrap_err()
            .to_string();
        assert!(err1.contains("response.completed"), "{err1}");

        // Case 2: response.incomplete errors immediately
        let (url2, _) = spawn_mock_server(|_| {
            let sse = format!(
                "data: {}\n\ndata: {}\n\n",
                json!({ "type": "response.output_text.delta", "delta": "cut" }),
                json!({ "type": "response.incomplete", "response": { "incomplete_details": { "reason": "max_output_tokens" } } }),
            );
            (
                "200 OK".into(),
                vec![("Content-Type".into(), "text/event-stream".into())],
                sse,
            )
        })?;
        let r2 = ResolvedModel {
            spec: "codex:gpt".into(),
            provider: test_provider("codex", url2, WireCodec::Responses),
            model_id: "gpt".into(),
            bearer_token: Some("tok".into()),
            account_id: None,
        };
        let err2 = client
            .stream(&r2, "s", &[], &[], &mut ctrl, &mut limits)
            .unwrap_err()
            .to_string();
        assert!(err2.contains("response.incomplete"), "{err2}");

        Ok(())
    }

    #[test]
    fn stream_control_mid_stream_steer_interrupts_and_drops_half_built_tool_calls() -> Result<()> {
        let (url, _) = spawn_mock_server(|_| {
            let sse = format!(
                "data: {}\n\ndata: {}\n\ndata: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
                json!({
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_half",
                                "function": { "name": "write_file", "arguments": "{\"path\":" }
                            }]
                        }
                    }]
                }),
                json!({ "choices": [{ "delta": { "content": "Part 1 " } }] }),
                json!({ "choices": [{ "delta": { "content": "Part 2" } }] }),
                json!({ "choices": [{ "delta": { "content": "Part 3 (never reached)" }, "finish_reason": "stop" }] }),
            );
            (
                "200 OK".into(),
                vec![("Content-Type".into(), "text/event-stream".into())],
                sse,
            )
        })?;

        let resolved = ResolvedModel {
            spec: "local:m".into(),
            provider: test_provider("local", url, WireCodec::ChatCompletions),
            model_id: "m".into(),
            bearer_token: None,
            account_id: None,
        };
        let client = ModelClient::new();
        let mut ctrl = SteerAfterChunks {
            remaining_deltas: 2,
            deltas: String::new(),
        };
        let mut limits = Vec::new();
        let outcome = client.stream(&resolved, "s", &[], &[], &mut ctrl, &mut limits)?;
        assert_eq!(
            outcome,
            ModelStreamOutcome::Interrupted {
                text: "Part 1 Part 2".into(),
                reason: InterruptReason::Steer,
            }
        );
        Ok(())
    }

    #[test]
    fn responses_sends_chatgpt_account_id_preserves_delta_text_and_redacts_bearer_token_on_error()
    -> Result<()> {
        // 1. Success path: verify ChatGPT-Account-Id header, object arguments, and delta_text fallback.
        let (url_ok, captured) = spawn_mock_server(|_| {
            let sse = format!(
                "data: {}\n\ndata: {}\n\ndata: {}\n\n",
                json!({ "type": "response.output_text.delta", "delta": "Streamed only." }),
                json!({
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "fc_99",
                        "name": "list_files",
                        "arguments": { "dir": "." }
                    }
                }),
                json!({
                    "type": "response.completed",
                    "response": {
                        "usage": {
                            "input_tokens": 120,
                            "output_tokens": 15,
                            "total_tokens": 135,
                            "input_tokens_details": { "cached_tokens": 64 }
                        }
                    }
                }),
            );
            (
                "200 OK".into(),
                vec![("Content-Type".into(), "text/event-stream".into())],
                sse,
            )
        })?;

        let resolved_ok = ResolvedModel {
            spec: "codex:gpt-6.1-terra".into(),
            provider: test_provider("codex", url_ok, WireCodec::Responses),
            model_id: "gpt-6.1-terra".into(),
            bearer_token: Some("sk-secret-bearer+token/123".into()),
            account_id: Some("acct_workspace_42".into()),
        };
        let client = ModelClient::new();
        let mut ctrl = NoopControl::new();
        let mut limits = Vec::new();
        let outcome = client.stream(&resolved_ok, "s", &[], &[], &mut ctrl, &mut limits)?;

        let reqs = captured.lock().unwrap();
        assert_eq!(reqs.len(), 1);
        assert!(reqs[0].request_line.starts_with("POST /v1/responses "));
        assert_eq!(
            reqs[0].headers.get("chatgpt-account-id").map(String::as_str),
            Some("acct_workspace_42")
        );
        assert_eq!(
            reqs[0].headers.get("authorization").map(String::as_str),
            Some("Bearer sk-secret-bearer+token/123")
        );
        assert!(reqs[0].body.get("max_tokens").is_none());
        assert!(reqs[0].body.get("max_output_tokens").is_none());

        match outcome {
            ModelStreamOutcome::Completed {
                text,
                tool_calls,
                usage,
                ..
            } => {
                assert_eq!(text, "Streamed only.");
                assert_eq!(tool_calls.len(), 1);
                assert_eq!(tool_calls[0].id, "fc_99");
                assert_eq!(tool_calls[0].name, "list_files");
                assert_eq!(tool_calls[0].arguments, "{\"dir\":\".\"}");
                assert_eq!(
                    usage,
                    Some(TokenUsage {
                        input: 120,
                        cached: 64,
                        output: 15,
                        total: 135,
                    })
                );
            }
            other => panic!("expected Completed, got {other:?}"),
        }

        // 2. Error path: server echoes raw + URL-encoded bearer token back in 401 body; must be redacted!
        let (url_err, _) = spawn_mock_server(|req| {
            let auth = req
                .headers
                .get("authorization")
                .cloned()
                .unwrap_or_default();
            let body = format!(
                "{{\"error\":\"rejected {auth} and url=sk-secret-bearer%2Btoken%2F123\"}}"
            );
            (
                "401 Unauthorized".into(),
                vec![("Content-Type".into(), "application/json".into())],
                body,
            )
        })?;

        let resolved_err = ResolvedModel {
            spec: "codex:gpt-6.1-terra".into(),
            provider: test_provider("codex", url_err, WireCodec::Responses),
            model_id: "gpt-6.1-terra".into(),
            bearer_token: Some("sk-secret-bearer+token/123".into()),
            account_id: Some("acct_workspace_42".into()),
        };
        let err = client
            .stream(&resolved_err, "s", &[], &[], &mut ctrl, &mut limits)
            .unwrap_err();
        let display = format!("{err:#}");
        let debug = format!("{err:?}");
        assert!(!display.contains("sk-secret-bearer"), "{display}");
        assert!(!debug.contains("sk-secret-bearer"), "{debug}");
        assert!(display.contains("[redacted]"), "{display}");

        Ok(())
    }

    #[test]
    fn chat_completions_reassembles_streamed_tool_calls_and_empty_args_on_done() -> Result<()> {
        let (url, _) = spawn_mock_server(|_| {
            let sse = format!(
                "data: {}\n\ndata: {}\n\ndata: {}\n\ndata: {}\n\ndata: [DONE]\n\n",
                json!({
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 1,
                                "id": "call_b",
                                "function": { "name": "ping", "arguments": "" }
                            }]
                        }
                    }]
                }),
                json!({
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "id": "call_a",
                                "function": { "name": "read_file", "arguments": "{\"path\":" }
                            }]
                        }
                    }]
                }),
                json!({
                    "choices": [{
                        "delta": {
                            "tool_calls": [{
                                "index": 0,
                                "function": { "arguments": "\"Cargo.toml\"}" }
                            }]
                        }
                    }]
                }),
                json!({ "choices": [{ "delta": {}, "finish_reason": "tool_calls" }] }),
            );
            (
                "200 OK".into(),
                vec![("Content-Type".into(), "text/event-stream".into())],
                sse,
            )
        })?;

        let resolved = ResolvedModel {
            spec: "local:m".into(),
            provider: test_provider("local", url, WireCodec::ChatCompletions),
            model_id: "m".into(),
            bearer_token: None,
            account_id: None,
        };
        let tools = vec![ToolSpec {
            name: "read_file".into(),
            description: "Read a file".into(),
            parameters: json!({"type": "object"}),
            mutating: false,
        }];
        let client = ModelClient::new();
        let mut ctrl = NoopControl::new();
        let mut limits = Vec::new();
        let outcome = client.stream(&resolved, "s", &[], &tools, &mut ctrl, &mut limits)?;
        match outcome {
            ModelStreamOutcome::Completed { tool_calls, .. } => {
                assert_eq!(tool_calls.len(), 2);
                assert_eq!(tool_calls[0].id, "call_a");
                assert_eq!(tool_calls[0].name, "read_file");
                assert_eq!(tool_calls[0].arguments, "{\"path\":\"Cargo.toml\"}");
                assert_eq!(tool_calls[1].id, "call_b");
                assert_eq!(tool_calls[1].name, "ping");
                assert_eq!(tool_calls[1].arguments, "{}");
            }
            other => panic!("expected Completed, got {other:?}"),
        }
        Ok(())
    }

    #[test]
    fn responses_rejects_non_object_tool_arguments_and_supports_mid_stream_interrupt() -> Result<()>
    {
        // Invalid non-object arguments must error.
        let (url_bad, _) = spawn_mock_server(|_| {
            let sse = format!(
                "data: {}\n\ndata: {}\n\n",
                json!({
                    "type": "response.output_item.done",
                    "item": {
                        "type": "function_call",
                        "call_id": "fc_bad",
                        "name": "list_files",
                        "arguments": "[1, 2, 3]"
                    }
                }),
                json!({ "type": "response.completed", "response": {} }),
            );
            (
                "200 OK".into(),
                vec![("Content-Type".into(), "text/event-stream".into())],
                sse,
            )
        })?;
        let r_bad = ResolvedModel {
            spec: "codex:gpt".into(),
            provider: test_provider("codex", url_bad, WireCodec::Responses),
            model_id: "gpt".into(),
            bearer_token: None,
            account_id: None,
        };
        let client = ModelClient::new();
        let mut ctrl = NoopControl::new();
        let mut limits = Vec::new();
        let err = client
            .stream(&r_bad, "s", &[], &[], &mut ctrl, &mut limits)
            .unwrap_err()
            .to_string();
        assert!(err.contains("JSON object"), "{err}");

        // Mid-stream Steer on Responses returns Interrupted with partial delta text.
        let (url_steer, _) = spawn_mock_server(|_| {
            let sse = format!(
                "data: {}\n\ndata: {}\n\ndata: {}\n\n",
                json!({ "type": "response.output_text.delta", "delta": "Alpha " }),
                json!({ "type": "response.output_text.delta", "delta": "Beta" }),
                json!({ "type": "response.completed", "response": {} }),
            );
            (
                "200 OK".into(),
                vec![("Content-Type".into(), "text/event-stream".into())],
                sse,
            )
        })?;
        let r_steer = ResolvedModel {
            spec: "codex:gpt".into(),
            provider: test_provider("codex", url_steer, WireCodec::Responses),
            model_id: "gpt".into(),
            bearer_token: None,
            account_id: None,
        };
        let mut steer_ctrl = SteerAfterChunks {
            remaining_deltas: 1,
            deltas: String::new(),
        };
        let outcome = client.stream(&r_steer, "s", &[], &[], &mut steer_ctrl, &mut limits)?;
        assert_eq!(
            outcome,
            ModelStreamOutcome::Interrupted {
                text: "Alpha ".into(),
                reason: InterruptReason::Steer,
            }
        );
        Ok(())
    }
}
