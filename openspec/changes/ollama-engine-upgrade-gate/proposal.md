# Ollama engine upgrade gate: a written diff and contract tests

Full spec: `docs/specs/ollama-engine-upgrade-gate.md`. Issue: #227. Lane: `pillar:model`.

## Why

Anthill bundles Ollama 0.30.10; the stable line is 0.40.16. Between them the engine changed how models run on
Apple Silicon (MLX by default), how downloaded models are stored (a background rewrite that keeps backups), what
replies contain (cached-token counts) and what the OpenAI-compatible routes default to. None of it was written
down, and Anthill's parsers were only tested against hand-made shapes. A bump could not be judged.

## What changes

Tests, fixtures and one written spec. No runtime behaviour, no version bump, no engine installed.

- A written diff of 0.30.10 to 0.40.16 with the effect on Anthill, and a list of open questions.
- A contract suite over recorded or documented replies for both versions, every fixture labelled `recorded` or
  `docs`, plus a recorder script for adding a version.
- A restore test for the one place Anthill writes into Ollama's store (an older archive's `ollama/` folder).
- A pin audit so the later bump is one mechanical PR.

## Guardrails (do NOT touch)

- No version bump; `scripts/**` is protected and is only read.
- No engine of another version is installed or run. A `docs` fixture is never the only evidence for an endpoint.
- No network in the tests.
