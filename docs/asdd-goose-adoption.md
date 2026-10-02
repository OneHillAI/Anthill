# ASDD-via-Goose: Anthill adoption runbook (theory)

Status: **planned / theory** - the step-by-step for standing up the ASDD operate layer (Goose) on
Anthill, written before the first real run. It is validated against a live run, then the concrete steps
and gotchas are handed to the ASDD framework session to improve their **generic** setup guide
(`OneHillAI/ASDD`: `recipes/README.md`, `cli/README.md`, the README front-door). This file is Anthill's
real-world adoption record; the framework owns the generic guide.

Relates to: [`docs/specs/asdd-model-roster.md`](specs/asdd-model-roster.md), [`.asdd.yml`](../.asdd.yml),
`OneHillAI/ASDD` (`recipes/`, `cli/init.sh --goose`, `cli/asdd-mcp.py`, `standards/spec-driven.md` §OP).

## 0. Context - what is already done, what this adds

Anthill's **govern** layer (the CI gates in `.github/asdd/`) is built and live. This runbook adds the
**operate** layer - the agents that do the work - via "ASDD with Goose" (the generic Operate-layer
product in `OneHillAI/ASDD`). Goose = operate (the local agent loop); the CI gates = govern (PR
enforcement); they compose, they are not either/or.

Prerequisites already in place:
- The model roster in `.asdd.yml` (developer = Opus 4.8 **BYO**, tester = MiniMax M2.7, reviewer =
  DeepSeek V3.1; see `docs/specs/asdd-model-roster.md`).
- Provider decided: **Infercom** (EU-sovereign, open models).
- The ASDD-with-Goose product is Goose-valid: the recipes run on Goose builtins plus the `asdd-gates`
  MCP extension.

## 1. Prerequisites

- **Goose** installed (`goose` on PATH).
- A checkout of **`OneHillAI/ASDD`** (the installer and recipes live there).
- **Infercom**: an OpenAI-compatible endpoint URL + an API key (for the project-provisioned
  governance/support models).
- The maintainer's own coding agent (Opus) for the **BYO developer** role - not project-provisioned.

## 2. Step 1 - install the operate layer (safe: it skips existing files)

Preview first, then apply **without** `--force`:

```
bash <ASDD>/cli/init.sh --goose --dry-run  ~/Projects/anthill    # preview
bash <ASDD>/cli/init.sh --goose            ~/Projects/anthill    # apply (NO --force)
```

`init` skips files that already exist, so Anthill's customized `.asdd.yml`, `AGENTS.md`, and
`.github/asdd/` are **untouched**. It adds only the operate layer: `recipes/` and `cli/asdd-mcp.py`.
Review the diff before committing; never pass `--force` on Anthill (it would clobber the hardened govern
config).

## 3. Step 2 - configure the provider (Infercom)

`goose configure` -> add Infercom as an OpenAI-compatible provider (its endpoint URL + API key). The
recipes are provider-neutral, so switching later is a one-line change.

## 4. Step 3 - set the per-role models (from the roster)

The recipes take their model per run. Set them from the `.asdd.yml` roster, keeping **developer != tester**:

```
goose run --recipe recipes/tester.yaml        --model minimax:minimax-m2.7   # independent tests
goose run --recipe recipes/documentation.yaml --model <open: gemma-4-31B-it>
goose run --recipe recipes/interaction.yaml   --model <open: gemma-4-31B-it>
# developer.yaml is OPTIONAL (BYO) - the maintainer's own Opus, distinct from the tester
```

## 5. Step 4 - wire the extensions

A minimal run needs only two, no external MCP:
- Goose's builtin **`developer`** extension (shell + file editing: runs tests, reads diffs, drives git/gh).
- **`asdd-gates`** (the `asdd-mcp` server) - the four deterministic gates: `spec-check`, `claim-check`,
  `merge-eligibility`, `audit-check`.

Recommended for Anthill (dogfood):
- **`knowledge`** pointed at Anthill's own wiki - Anthill IS the OKGF brain, so the agent orients from
  the wiki instead of re-reading the codebase.
- **`github`** for reading issues/PRs and opening governed PRs.

## 6. Step 5 - prove it runs (live, not just validate)

Run a recipe on a real Anthill PR to prove execution, beyond `goose recipe validate`:

```
goose run --recipe recipes/tester.yaml --model minimax:minimax-m2.7 --params pr=<n>
```

Confirm it reads the diff, runs the suite on a model distinct from the developer's, and reports.

## 7. Step 6 - integrate into the dev process

- **interaction** agent: connect Anthill's chat / Slack surface - answers from the wiki, routes ideas
  into the governed intake as validated specs.
- **tester** agent: independent tests on MiniMax (the project's tester differs from the BYO developer).
- **documentation** agent: keeps docs / `SYSTEM_IMPACT_LOG` / the wiki in sync as governed PRs.
- **developer**: stays BYO (the maintainer's own agent) - never project-provisioned, never self-merges.

## 8. Step 7 - (govern layer, separate) repoint the CI review gate

Not Goose. Wire Infercom to the CI review gate via `.github/workflows/pr-review.yml`, which reads
`vars.ASDD_MODEL_URL` + `vars.ASDD_MODEL` and `secrets.ASDD_RUNTIME_TOKEN`. The endpoint URL and model
name are non-sensitive, so they are repo **Variables**; only the API key is a **Secret**:

- `ASDD_MODEL_URL` (Variable) - the **full** chat-completions URL,
  `https://api.infercom.ai/v1/chat/completions`. The adapter (`runtime/openai-compat.sh`) appends
  `/chat/completions` to a bare `/v1` base and prints a notice, so either form works. Set the full URL to
  silence the notice.
- `ASDD_MODEL` (Variable) - the model name the endpoint expects, `gemma-4-31B-it` (the raw name, the same
  id the roster in `.asdd.yml` uses for the reviewer). The review gate and the documentation agent both use
  this one model.
- `ASDD_RUNTIME_TOKEN` (Secret) - the Infercom API key, the only sensitive value.

Owner action. This moves the live CI review off the OpenRouter test model onto the roster's reviewer,
EU-sovereign gemma-4-31B-it.

## 9. Feedback loop (to the framework)

After the live run, the concrete steps and gotchas (what `init` and `goose configure` actually needed,
which extensions were required, what broke) go to the ASDD framework session to review and update the
**generic** setup guide. This runbook stays Anthill's record; the framework owns the generic guide.

## Confirmation - is the theory feasible?

- Goose is installed; the recipes are Goose-valid; the roster satisfies `developer != tester`
  (`check-models --strict` passes); Infercom is OpenAI-compatible, so it configures in Goose; a minimal
  run needs no external MCP.
- Owner-gated before it can actually run: Infercom's endpoint + key (as the repo secret for the gate, and
  in `goose configure` for the recipes); the live-run proof in Step 5 needs a `goose configure` with the
  provider.
