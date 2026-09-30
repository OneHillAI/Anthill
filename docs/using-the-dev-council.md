# Using the developer council

The developer council is an optional produce-loop developer: instead of one model implementing a change,
two to five diverse models propose, cross-critique, a lead synthesises one result, and the test agents
verify it. Opus 4.8 and Gemini 3.1 Pro propose; GPT-5.6 leads (it is the independent arbiter, and it is
deliberately not Opus, because Opus is also the Claude Code interface you talk through). It always returns
one draft plus a full transcript.

It is opt-in. Reach for it when a change benefits from multi-model diversity: a tricky design, a
security-sensitive path, a change where one model's blind spot matters. Do not run it on a trivial edit,
each run is real runware cost across several frontier calls. For most changes, the bring-your-own
developer (Claude Code) implements directly.

## When to use it, in one line

Non-trivial change where more than one strong model's perspective earns its keep. Otherwise, just build it.

## How to run it

1. **Write the change bridge.** The council reads an OpenSpec change. Create two files:
   - `openspec/changes/<id>/proposal.md`  the why and what-changes (reference the `docs/specs/<x>.md` it implements).
   - `openspec/changes/<id>/tasks.md`  the acceptance criteria, the concrete rules the result must satisfy.
2. **Run the council.**
   ```
   scripts/council.sh <id>
   ```
   It loads the local keys, runs the loop (a few minutes), and prints a draft path and a transcript path.
3. **Review the draft.** It is a proposal, not applied code. Read it against the acceptance criteria,
   apply it, and fix the integration details the council could not see (imports, existing objects, how it
   fits the surrounding file).
4. **Capture the transcript.** The transcript is full-fidelity (every proposal, critique, the synthesis,
   the verify result). It is the distillation corpus, so route it to the private operate repo:
   `anthill-run/training/`. It is private only, it contains real code, and it never reaches a public or
   governed sink.
5. **Open a governed PR.** Turn the reviewed draft into a normal PR so it flows through intake, the
   reviewer, and the tests. The council produced it; the gates govern it.

## The keys

Local and private, never committed:

- `~/anthill-keys/runware.env`  the council (runware; per-member `ASDD_..._COUNCIL_i`).
- `~/anthill-keys/berget.env`   the Berget governance and verify models (shared `ASDD_MODEL_URL` /
  `ASDD_RUNTIME_TOKEN`).

Before trusting a run, `python3 cli/connect-check.py` must report every agent LIVE. A dry or unwired
agent does no real work and must never look like it did.

## Why frontier, and why it is temporary

The council runs on frontier proprietary models (runware) because open models still trail on the produce
task. That is a deliberate, bounded compromise: the governance agents stay sovereign and non-Chinese on
Berget, and every council run is captured as training fuel. The frontier models are a teacher we intend to
distil from, not a standing dependency.
