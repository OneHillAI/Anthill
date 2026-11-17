# Privacy: a written threat model for a single Anthill node

Lane: `pillar:privacy`. Issue #229. Roadmap item 1 of the Privacy pillar.

## Why

The front page of the repo promises that your prompts, documents and embeddings never leave your machines
unless you explicitly and visibly allow it. The code has real controls behind that promise (scrubbing on every
call to a remote model, a per-turn local or remote badge, a signed model catalogue, a Keychain-held key and
protected backups). What the repo does not have is a written statement of **who the promise is defended
against, on which kind of install, and what is out of scope**. There is no threat model file in the repo.

Without it:

- privacy fixes cannot be ranked, because nobody has said which attacker matters on which install;
- "claims versus tests" (roadmap item 2) has nothing to check the sentences against;
- an outside reviewer or a later independent test has no list of boundaries to try;
- the three install shapes behave differently (the README already warns that a proxy or tunnel makes a remote
  request look local on a headless or LAN install), and that difference is written down only in scattered
  sentences.

The independent council review of the privacy pillar (17 November 2026) named a written threat model as the
first thing to do.

## What this change adds

One new document and one small test. No product behaviour changes.

- `docs/THREAT_MODEL.md`: the threat model for **a single node**, for three install shapes: **Solo** (desktop
  app or local browser), **headless or LAN** (`anthill web --host 0.0.0.0`, a container), and **Org** (the shared web server with accounts, org
  admins and per-org wikis). It contains:
  1. **What we protect:** prompts, documents, embeddings, wiki, chats, memories, saved credentials, the audit
     log, model files.
  2. **Who we defend against**, as attacker classes: an author of web content or an uploaded file (injection),
     a poisoned model or a tampered model catalogue, another OS user or process on the machine, another machine
     on the LAN, another user in the same Org, a lost or stolen disk or backup, and the outside services that
     legitimately see data we send (a search provider, a hosted inference endpoint).
  3. **What is out of scope**, stated plainly: an attacker with administrator rights on the machine running the
     node, a compromised operating system, hardware attacks, and **peers and node-to-node traffic** (the orchestrator, node agent and shared index belong to the Node
     pillar's threat model; Privacy takes part and links to it).
  4. **A table per install shape:** attacker x what they can reach x what stops them x where that is tested.
     Every cell either points at a code path and a test, or says **no test yet**. No cell may claim more than a
     test shows.
  5. **A list of exits:** every way data can leave the machine today (model call to a non-loopback endpoint,
     web search, page fetch, connectors, updater, model downloads, catalogue refresh), each with its gate and
     whether the user can switch it off. This is the input for the exit ledger (roadmap item 13).
  6. **What the document does not claim yet,** stated as scope limits. Unfixed security gaps are not listed
     (see Guardrails).
- `docs/specs/privacy-threat-model.md`: the spec (Problem, Requirements, Acceptance) the document is held to.
- `tests/test_threat_model_doc.py`: a model-free test that every code path and test file named in the document
  exists, so the document cannot silently rot.
- A link to the document from `README.md` and `SECURITY.md`.

## Guardrails

- **Public document, private gaps.** This file lives in a public repo. It describes attacker classes,
  boundaries and what is defended. It must not list unfixed vulnerabilities, unauthenticated routes or
  reproduction steps. Those go to security@onehill.org first (`SECURITY.md`) and are added to a later version
  only after a fix ships. Where a boundary has not been independently tested, the table says
  **"intended; not yet verified"** and does not describe the weakness.
- **Checked means read.** The document states the commit it was checked against and that the code was read, not
  run, unless a test cited in the table was run.
- **No product code, and no protected path except one line.** Nothing under `anthill/` (including
  `anthill/hybrid/**`, `anthill/web/crypto.py`, `anthill/wiki/**`, `anthill/node_agent/**`) is touched. The
  change is `docs/`, `tests/`, one line each in `README.md` and `SECURITY.md`, and one line each in
  `.github/workflows/ci.yml` and `pr-validation.yml` (protected: `.github/**`) that lists
  `docs/THREAT_MODEL.md` in `DOCS_ONLY_CARVE_OUT`. The repo's own test
  (`tests/test_ci_docs_only_fast_path.py`) requires that line for any doc a test reads, so a PR that
  changes only the document still runs the drift test.
- **No new promise.** A sentence in the document may only say what a cited test or a cited piece of code shows.
- **One purpose.** It does not fix any gap it finds, and it does not turn invariants into marked tests (that is
  roadmap item 3, issue #178).

## Out of scope

- Fixing anything the document reveals.
- The peer / Mesh threat model (Node pillar).
- Claims-versus-tests for every README sentence (roadmap item 2): this change gives item 2 its list, it does
  not do item 2.
- An independent security test (roadmap item 4).
- Legal duties (EU Article 50, product liability, Cyber Resilience Act; roadmap item 5).

## Spec

`docs/specs/privacy-threat-model.md` (new).
