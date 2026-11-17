# Spec: a written threat model for a single node

Lane `pillar:privacy`. Issue #229. Roadmap item 1 of the Privacy pillar. OpenSpec change:
`openspec/changes/privacy-threat-model/`. The document this spec governs: [`docs/THREAT_MODEL.md`](../THREAT_MODEL.md).

## Problem

The README promises that your prompts, documents and embeddings never leave your machines unless you
explicitly and visibly allow it. The code has real controls behind that promise, but nothing in the repo says
who the promise is defended against, on which kind of install, and what is out of scope. Without that:

- privacy fixes cannot be ranked, because nobody has said which attacker matters on which install;
- a sentence in the README or the app cannot be checked against a stated boundary;
- an outside reviewer, or a later independent test, has no list of boundaries to try;
- the three install shapes behave differently (a proxy or tunnel on the same machine makes a remote request
  look local on a launcher-started server), and that is written down only in scattered sentences.

## Requirements

**R1. One document.** `docs/THREAT_MODEL.md` describes one node, for three install shapes: Solo, headless or
LAN, and Org.

**R2. Content.** It states: what we protect and how today; the install shapes and how a request is judged
local; the attacker classes; what is out of scope (administrator-level attackers, a compromised operating
system, hardware attacks, peers, and the person who runs an Org server); a table per install shape of
attacker, what they can reach, what stops them and where it is tested; every way data leaves a node, with its
gate, default and switch; and what the document does not claim.

**R3. No cell claims more than the code or a test shows.** Every row in the per-shape tables either names the
test files that pin the control, or says **no test yet**, or says **intended; not yet verified**. A row that
says "intended; not yet verified" does not describe a weakness.

**R4. Checked means read.** The document states the commit the code was read at and that the code was read,
not run, unless a cited test was run.

**R5. Public by design.** The document is published in a public repository. It does not list unfixed security
problems, unauthenticated routes or reproduction steps. Those go to the private reporting route in
`SECURITY.md` first and are described only after a fix ships. What it does not claim is stated as a scope
limit, not as a finding.

**R6. It cannot drift.** `tests/test_threat_model_doc.py` fails if the document names a file or a test that
does not exist, if a row in section 6 has neither a test nor a stated reason, or if a required section is
missing. A pull request that adds an exit, an entry point or an install shape updates the document in the same
pull request.

**R7. No new promise, no behaviour change.** The change adds documents and one test. It adds one link each to
`README.md` and `SECURITY.md`. It touches no product code. Its one protected-path change is a single line in
each of `.github/workflows/ci.yml` and `pr-validation.yml`, listing `docs/THREAT_MODEL.md` in
`DOCS_ONLY_CARVE_OUT` as `tests/test_ci_docs_only_fast_path.py` requires for any doc a test reads.

## Out of scope

- Fixing anything the document leaves unclaimed.
- The threat model for how nodes talk to each other (Node pillar), and the independent test of the access and
  egress boundaries.
- Checking every README sentence against a test (roadmap item 2). This document gives that work its list.
- Turning the charter's rules into marked tests (issue #178).
- Legal duties.

## Acceptance

- `docs/THREAT_MODEL.md` exists and has the sections: what we protect, the install shapes, the attackers, out of
  scope, one table each for Solo, headless or LAN and Org, every way data leaves a node, what is not claimed.
- `tests/test_threat_model_doc.py` passes, and fails when a cited path is removed or a section 6 row loses its
  test and its reason.
- A reader new to the repo can say, for each install shape, who the promise holds against and who it does not,
  from the document alone.
- The README and `SECURITY.md` link to the document and add no new promise.
- No file under `anthill/` changed. The only protected-path change is the `DOCS_ONLY_CARVE_OUT` line in the two
  workflow files.

## What was not verified

- The document was written by reading the code. No app was started and the cited tests were not re-run for it.
- The real macOS Keychain, the signed desktop app and the update path were not exercised (the same limit as
  `docs/specs/encryption-at-rest-phase1.md`).
- The exit table is an inventory from reading the code. An exit reached through a path nobody searched for would
  be missing; the independent test (roadmap item 4) is the check for that.
