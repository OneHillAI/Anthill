# Speed/usability benchmark against real LLM usage patterns

## Problem

There was no way to test Anthill's real-world speed and usability against anything grounded in how
people actually use an AI assistant - no representative prompt set, and no harness to time Anthill's
own chat pipeline end to end. Asked directly (research question, answered with web search before any
code): are there existing studies or datasets of the ~100-1,000 most common LLM tasks that could be
used for this? Yes, in two tiers:

- **Real-usage taxonomies** (what people actually ask, with real proportions): Anthropic's Economic
  Index/Clio (analyzed ~1M real Claude.ai conversations; "computer and mathematical" tasks - mostly
  coding - are 37.2% of all queries) and OpenAI's "How People Use ChatGPT" (NBER working paper, 1M+
  conversations; "Practical Guidance"/"Seeking Information"/"Writing" cover ~80% of usage, writing
  alone 42%, two-thirds of that editing not generating).
- **Real-conversation datasets sampleable directly**: WildChat-1M (~838K real ChatGPT conversations,
  open) and LMSYS-Chat-1M (~1M Chatbot Arena conversations, access-gated on HuggingFace).
- **Curated benchmark prompt sets built from that same real distribution**: Arena-Hard-Auto (500
  prompts, current best-practice pick, 98.6% correlation with human preference rankings) superseding
  the earlier MT-Bench/AlpacaEval generation.

None of these measure latency by themselves - they're prompt sources / quality benchmarks, not timing
tools. The gap this closes: sample real prompts from them, then actually drive Anthill's chat API and
time it.

## Design

### Prompt sources (`tools/bench/fixtures/`)

- `real_usage_sample.jsonl` (300 prompts): the first user turn from 300 real WildChat-1M conversations,
  sampled at spread-out offsets across the ~838K-row dataset (not just the first N, avoiding time/topic
  clustering), filtered to English, non-toxic (dataset-provided flags), and a plausible single-message
  length (10-800 chars), and scrubbed of anything credential-shaped before it is stored (`redact_secrets` in
  `fetch_prompts.py`): one prompt held a pasted Telegram bot token that GitHub's secret scanning flagged in the
  stored sample. LMSYS-Chat-1M is gated on HuggingFace (needs a token with its terms accepted);
  `fetch_prompts.py --hf-token`/`$HF_TOKEN` can pull from it too once someone has accepted the gate -
  not required for the checked-in sample, which tops up from WildChat alone to stay in range.
- `arena_hard_v0.1_questions.jsonl` (500 prompts): the full, unmodified Arena-Hard-Auto v0.1 set,
  fetched from its GitHub repo. Kept verbatim (own `uid`/`category`/`cluster`/`prompt` schema) rather
  than reshaped into this tool's own format, since it may be useful standalone for an actual
  Arena-Hard-style quality score later (out of scope here - see below).

Combined: 800 prompts, within the 100-1,000 target range asked for.

### Timing harness (`tools/bench/run_timing.py`)

Drives the SAME HTTP API a real Chat user hits - `anthill.web_client.OrgClient`, the client `anthill
chat --org` already uses (unit-tested against a mock transport) - rather than reimplementing HTTP/SSE
parsing. `OrgClient.stream()` gained one new parameter, `escalate_org: bool = False`, mirroring the
server's existing per-turn "redo with the connected org/cloud/provider backend" parameter (#278's
escalation engine) - lets a script force a turn onto the stronger backend without a natural-language
redo phrase.

For each prompt: log in once, open a FRESH conversation (not one growing thread across the whole run),
and time the stream from request start to first token (TTFT) and to stream completion. A fresh
conversation per prompt keeps every prompt's number independently comparable - a later prompt is never
penalized by an earlier one's growing context, matching how most real usage is a fresh question (per
the OpenAI/Anthropic research above, not a single ever-growing thread).

This measures Anthill's real end-to-end latency for whichever compute tier the target account is
already configured for - it does not provision anything or switch tiers itself. Comparing tiers means
either running against separate accounts each configured differently, or using `--escalate` on one
account that has a local default AND a connected endpoint (exactly the #278 escalation path), to
compare "local" vs "escalated to the connected backend" on the same account.

### Comparison (`tools/bench/compare_runs.py`)

Reads two or more `run_timing.py --output` result files and prints a side-by-side table (n, errors,
TTFT/total mean and p95) - closes the loop from "run three tiers separately" to "see them compared."

## Out of scope

- Actually running this against a live Anthill instance - needs a real running server with a model
  pulled/configured (local, self-provisioned cloud, or a connected inference provider), which this
  session's environment doesn't have. Handed off to a session that can run it against a real instance.
- An Arena-Hard-Auto-style automated quality score (LLM-as-judge comparing Anthill's answers against a
  reference model) - the 500-prompt file is fetched and available for this, but scoring is a separate,
  larger piece of work (needs a judge model + reference answers) not built here.
- Any change to the CLI's interactive `anthill chat --org` command beyond the new, backward-compatible
  `escalate_org` parameter on `OrgClient.stream()` - not wired into the interactive REPL's `/` commands
  here, since that's a UX decision for the CLI, not this benchmark tool.
- Stratifying the real-usage sample by Clio's/OpenAI's exact task-category proportions - infeasible
  without their (private) classification tooling; documented as a limitation, not attempted as a fake
  weighting scheme. Plain random sampling from WildChat is already a reasonable proxy, since the
  dataset itself is real-world usage.
