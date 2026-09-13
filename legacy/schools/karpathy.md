# The Andrej Karpathy (`karpathy`) School

> Location: `.arity/schools/karpathy.md`
> Tradition: OpenAI, Stanford CS231n, Tesla AI, `nanoGPT` (124M params, GPT-2 reproduction), `llm.c`
> Primary: `karpathy.ai/zero-to-hero.html`, `youtube.com/watch?v=kCc8FmEb1nY` (`ng-video-lecture`), `github.com/karpathy/nanoGPT`
> Axiom: Build from scratch. Measure loss empirically. Scale only after transparency preserved. Prefer understanding over performance until demanded.
> Raw sources: `karpathy_transcript_lecture_raw.md` (structured lecture from video/search), `karpathy_nanoGPT_raw.md` (repo file descriptions), `karpathy_tweet_raw.md` (tweet 2098777547265950015). Note: YouTube transcript endpoint returned 0 bytes (`/tmp/transcript_check.xml`); unavailable. Structured reference preserved instead.

---

## Problem

LLMs are opaque. Framework bloat hides weights. Pre-built libraries prevent understanding the core mechanism (`nn.Embedding`, self-attention, multi-head, position embeddings). No empirical measurement of stability over ticks.

Fix: start minimal (`nn.Embedding` bigram → cross-entropy), build attention iteratively (loops → matrix mult → masked softmax → multi-head), scale only after loss observed to drop (`4.7 → 2.3 → 1.1`). Hardware-aware (`torch.amp`, gradient clipping) because GPU demands it, not for vanity.

---

## Implementation Steps (Imperative)

From `nanoGPT` / lecture (`kCc8FmEb1nY`):
1. Load flat data (`Tiny Shakespeare`, 1.1MB) → sliding window chunks (`block_size`)
2. Define tokenizer (`GrammarToken` → character-level first, subword later)
3. Build baseline bigram (`nn.Embedding`) → measure cross-entropy loss
4. Add self-attention: loops → `@` matrix mult → masked softmax → multi-head
5. Assemble full stack: embeddings → transformer blocks → language head
6. Track validation loss empirically → reproduce 124M (GPT-2)

---

## Evidence-Based Mapping to EGE

Verified claims (grounded in `model/model_stub.md`, `grammar_spec.md`, `dummy_sequence.md`, `delta_cache.md`, `two_heads/ouroboros_stub.md`):
- `TinyWorldModel` (`10k-350k` params) = `nanoGPT` scaled to 0.01%-0.3% of 124M. Same architecture (flat array = token vocabulary, `predict()` = generation loop).
- `GrammarToken` (fixed seats 0-6, variable groups 7+) = tokenizer abstraction: define tokens before sequences.
- `dummy_sequence.md` (ball fall, temp rise) = `Tiny Shakespeare` dataset for dynamics: consistent sequences, no complex scene structure.
- Tree question (`sketch/tree_imagined.md`): cylinder (static, zero delta) vs cone (sway, small delta) stabilization = observe validation loss over ticks (20-50 ticks for static patterns, like ball stops at tick 20). Fix isn't code; add tree sequences to training data.
- `delta_cache.md` (changed tokens written) = `nanoGPT` attention mask/optimization principle: don't compute unchanged tokens.
- `two_heads/ouroboros_stub.md` (`head_ouroboros`) = RLHF/reward model: after pre-training (`predict`), measure behavior and adjust (`rl/archetype_rl_stub.md` = archetype clustering/reward).
- Hardware included (`token[2]` GPU temp, `token[5]` battery) = `torch.amp` awareness: GPU demands efficiency, not vanity.
- Minimal dependencies (`CMake` + `slang`, no stdlib bloat) = `nanoGPT` uses only PyTorch.

---

## Zero-Case / Defaults

- Start minimal: 10k params, flat array (`REALITY_STACK_SIZE` = 256), single sequence format (`dummy_sequence.md`).
- Zero data: no tree sequences yet. Model cannot learn static vs dynamic patterns until `dummy_sequence.md` expanded.
- Zero understanding: if `GrammarToken` undefined before sequences, no prediction possible.
- Zero measurement: without `delta_cache.md`, memory overhead unverified.

---

## Checklist (Applied to EGE)

Based on `karpathy_transcript_lecture_raw.md`, `karpathy_nanoGPT_raw.md`:
- [ ] Model starts minimal (`10k` params, flat array, no hidden layers) before scaling?
- [ ] Training data (`dummy_sequence.md`) consistent, small, observable?
- [ ] Predictions measured empirically (loss/stability over ticks, not "feels right")?
- [ ] Tokenizer (`GrammarToken`) transparent before sequence construction?
- [ ] Hardware reality (`token[2]` GPU, `token[5]` battery) included, not ignored?
- [ ] No framework bloat (`CMake`/`slang`, no stdlib inheritance)?
- [ ] Connection point sought via data observation, not engineered constraint?
- [ ] Raw transcript unavailable (`/tmp/transcript_check.xml` = 0 bytes); structured lecture reference (`karpathy_transcript_lecture_raw.md`) preserved instead.

---

## Raw Source References (Not Compressed, Read Verbatim)

- `karpathy_transcript_lecture_raw.md`: structured lecture (`youtube.com/watch?v=kCc8FmEb1nY`, sections mapped, source repos `ng-video-lecture`/`nanoGPT`)
- `karpathy_nanoGPT_raw.md`: raw repo descriptions (`github.com/karpathy/nanoGPT`, `model.py`/`train.py`/`sample.py` structure)
- `karpathy_tweet_raw.md`: verbatim tweet 2098777547265950015 (`x.com/karpathy/status/2098777547265950015?s=20`, all replies/use-cases preserved via Nitter)
- Note: full YouTube transcript (`youtube-dl` endpoint) unavailable (`/tmp/transcript_check.xml` empty, no `youtube-dl`/`yt-dlp`); structured reference is evidence, not fabrication.
