#!/bin/bash
# Real multi-model commit dogfood using environment API keys
# B1 = openrouter (Inkling), B2 = NVIDIA NIM (ChatGPT), B3 = Gemini (Nemotron)

echo "=== REAL DOGFOOD START ==="
echo "B1 key: ${OPENROUTER_API_KEY:0:10}..."
echo "B2 key: ${NVIDIA_NIM_API_KEY:0:10}..."
echo "B3 key: ${GEMINI_API_KEY:0:10}..."

# Create temporary work directory
mkdir -p harness/real_dogfood

# Prompt for B1 (OpenRouter / Inkling)
cat > harness/real_dogfood/prompt_b1.md << 'PROMPT'
You are B1 (inkling). The harness (arity) now contains Go code: loop (ExecuteRuntimeLoop), agent, synth, memory, grade scoring, minimal tools. Tests cover parallel loom, classifier, synthesis, memory trim, persona evolution, pairing, terminal minimal. Legacy files: committer.md + schools (Torvalds/Carmack/Aaltonen/Karpathy) + heuristics. Grades: B1=0.92, B2=0.85, B3=0.80. Synthesis: cherrypick from 3 commits. Write a commit message for the current Go harness work. Imperative mood, <60 chars subject, concrete non-goals, empirical line (line count / test count), reference 6 gates briefly, author B1-inkling. No defensive language.
PROMPT

# Call B1
curl -s -X POST https://openrouter.ai/api/v1/chat/completions \
  -H "Authorization: Bearer $OPENROUTER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "openai-codex/gpt-5.6-terra",
    "messages": [{"role":"user","content":"Read harness/real_dogfood/prompt_b1.md and harness/ (list files if needed). Write commit message: harness/real_dogfood/commit_b1_real.md"}]
  }' > harness/real_dogfood/b1_raw.json

echo "B1 called (real OpenRouter)"
