# Tasks

- [x] Read the code and existing specs for the three install shapes (Solo, headless or LAN, Org) and list the
      trust boundaries and every exit, with the file that implements each. Record the `main` commit read.
- [x] Write `docs/specs/privacy-threat-model.md` with a Problem, Requirements and Acceptance section
      (the intake gate needs the Problem and Acceptance headings).
- [x] Write `docs/THREAT_MODEL.md`: assets, attacker classes, out of scope, one table per install shape
      (attacker x reach x control x test), the list of exits, and the public-only list of what is not covered yet.
- [x] Every table cell cites a code path and a test, or says **no test yet**, or says **intended; not yet
      verified**. No cell describes an unfixed weakness.
- [x] Add `tests/test_threat_model_doc.py`: parse the document, assert every cited file path exists and every
      cited test name is found. Model-free, no network.
- [x] Link the document from `README.md` and `SECURITY.md` (one line each); confirm neither line adds a new
      promise.
- [x] No `docs/SYSTEM_IMPACT_LOG.md` line (that log is written per merged PR) and no changelog fragment (no
      user-facing behaviour changes).
- [ ] Local validation: `ruff check`, `ruff format --check`, the new test, the full `pytest`, and the repo's
      intake dry run (spec headings, lane label, disclosure).
- [ ] Read the finished document once as an outsider: nothing in it names an unfixed gap; every sentence points
      at code or a test.
- [ ] State in the PR what was **not verified** (the code was read, not run, except the cited tests).
- [ ] Open the PR only after the maintainer's plain yes for that PR: label `pillar:privacy`, `## Spec` section
      linking `docs/specs/privacy-threat-model.md`, `## Disclosure` ticked for agent work, `Fixes #N`, commits
      signed off with the `Agent:` trailer, pushed as the bot.

## Acceptance

- `docs/THREAT_MODEL.md` exists and covers Solo, headless or LAN, and Org, each with its table.
- The out-of-scope list names administrator-level attackers, a compromised OS, hardware attacks and peers.
- The test passes and fails if a cited path is removed.
- A reviewer who has never seen the repo can say, for each install shape, who the promise holds against and
  who it does not, from the document alone.
- No product code changed. The only protected-path change is the one-line `DOCS_ONLY_CARVE_OUT` entry in
  `.github/workflows/ci.yml` and `pr-validation.yml`.
