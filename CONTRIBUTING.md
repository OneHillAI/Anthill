# Contributing to Anthill

Anthill is AGPL v3, with a commercial license for distributing it, or running a modified
version as a network service, without releasing your source (see the README's Licensing section). Contributions, forks, and self-hosting are all welcome. This
file holds the developer- and operator-facing material that doesn't belong in the
product [README](README.md): building from source, the CLI, self-hosting the org
backend, and the full configuration reference.

> Most users never need any of this - they install the [Mac app](https://github.com/OneHillAI/Anthill/releases/latest)
> and never see a Terminal. This is for people who want to build, extend, or operate
> Anthill themselves.

---

## Build from source

**Requirements:** macOS or Linux, Python 3.10+, [Ollama](https://ollama.com).

```bash
git clone https://github.com/OneHillAI/Anthill.git
cd Anthill
make install          # create .venv and install deps
bash start.sh         # first run pulls qwen2.5:3b (~2 GB), generates secrets, opens the dashboard
```

The dashboard opens at `http://localhost:8000`; follow the setup screen to create your account.
For user guidance, see [Using Anthill](docs/USING_ANTHILL.md).

**Build the Mac app / installer:**

The canonical Mac app - the one `anthill.run` distributes, with a native window and self-update - is
the **Tauri desktop shell** in [`src-tauri/`](src-tauri/README.md). Build it locally with:

```bash
bash scripts/build-sidecar.sh          # backend sidecar (do this every time; see the warning below)
cargo tauri icon assets/anthill-mark.png   # from src-tauri/ - app icons (gitignored, build fails without them)
cargo tauri build                      # from src-tauri/ - produces the .app/.dmg
```

Full details, file map, and the local-dev loop (`cargo tauri dev`) are in
[`src-tauri/README.md`](src-tauri/README.md). **Always build the sidecar with `scripts/build-sidecar.sh`,
never a hand-rolled `pyinstaller` call or an old/borrowed binary** - skip or botch that step and the
app opens with an invisible window and no error message (nothing for the shell to spawn or point the
window at). *(An unsigned local build still hits Gatekeeper on first launch: `xattr -dr
com.apple.quarantine /Applications/Anthill.app`, or allow it via System Settings → Privacy & Security →
Open Anyway. The official release is signed, notarized, and opens with no warning.)*

`make dmg` (`scripts/build-dmg.sh`) is the **older, retired** standalone PyInstaller build - it opens
its UI in the system browser rather than a native window, and CI no longer publishes it. It still
works for a quick Python-only smoke test with no Rust toolchain, but it is not the Mac app users
download; don't confuse the two.

---

## Development

```bash
make install    # create .venv and install deps
make test       # model-free unit tests
make smoke      # live end-to-end (Ollama required)
make demo       # full walkthrough
```

- `lancedb` is pinned `<0.20`. SQLite auto-migrates new columns on startup
  (see `anthill/web/migrate.py`).
- **Reproducible installs.** `pyproject.toml` keeps flexible version ranges (so normal installs still
  get security updates); [`requirements.lock`](requirements.lock) is a fully-pinned, hashed snapshot of
  the packaged app's runtime closure (base deps + the `docs` and `mcp` extras) for a byte-for-byte
  reproducible, tamper-evident install: `pip install --require-hashes -r requirements.lock`. The release
  build installs from it (`scripts/build-sidecar.sh`). Regenerate it after changing those dependencies:
  `uv pip compile --universal --python-version 3.11 --generate-hashes --extra docs --extra mcp pyproject.toml -o requirements.lock`.
  The lock targets Python 3.11 and newer (some pins, such as `pandas` 3.0, need 3.11); `pyproject.toml` still
  allows 3.10, and a 3.10 install uses `pyproject.toml`, not the lock.
  Keep `--python-version 3.11` (the packaged app's Python). Without it uv uses the machine's Python, and on
  3.13 or newer the lock silently loses the macOS `onnxruntime<1.20` pin and the numpy splits for 3.11 and 3.12.
  After a regeneration, check that every package the app imports is in the lock: the packaged app installs
  nothing beyond it, so a missing entry (as `sigstore` once was) fails only inside the frozen build.
- Local model for tests/dev: `qwen2.5:3b` on Ollama.
- Working protocol and the contribution contract: [`AGENTS.md`](AGENTS.md). Authoritative design:
  [`ARCHITECTURE.md`](ARCHITECTURE.md).
- MCP (connect/expose tools) needs the optional extra and Python 3.10+:
  `pip install -e ".[mcp]"`.

---

## CLI

The dashboard covers everything, but a CLI exists for scripting and power users:

```bash
anthill -w ./wiki init                          # create a wiki workspace
anthill -w ./wiki ingest notes.md              # model writes a wiki page from any file
anthill -w ./wiki ask "what did we decide?"    # answered from wiki + cache
anthill -w ./wiki ask "..." --save             # file the answer back as a page
anthill -w ./wiki lint                          # broken links, orphans, empty pages
anthill export-training --quality gold          # export training set as JSONL
```

`init`, `info`, and `lint` work without a model. For PDF processing limits and the
`--confirm-large-pdf` option, see [Using Anthill](docs/USING_ANTHILL.md).

---

## Self-hosting the org backend

The standard org topology is: every user runs a local node, and the org runs **one GPU
backend** (AWS VPC or on-prem) that hosts coordination (the orchestrator: consolidated
org wiki, central index, governance) and training. A single individual can run a node
standalone to evaluate or for personal use, but any team uses the backend.

```bash
# On the org backend
ANTHILL_ORG_WIKI=/data/org-wiki uvicorn anthill.orchestrator.app:app --host 0.0.0.0 --port 8080

# On each user's machine (the installer does this automatically for invited users)
anthill -w ./wiki serve --port 9001 --org-url http://server:8080 --node-id $(hostname)
```

**Docker:**

```bash
docker compose up --build   # orchestrator + 2 nodes
```

---

## OpenAI-compatible API

`anthill serve` exposes `/v1/chat/completions` and `/v1/models`, so OpenAI-SDK clients
(Cursor, Continue, etc.) can point at a node and get wiki context + caching transparently:

```python
from openai import OpenAI
client = OpenAI(base_url="http://localhost:9001/v1", api_key="unused")
response = client.chat.completions.create(
    model="qwen2.5:3b",
    messages=[{"role": "user", "content": "which database did we pick for billing?"}],
)
```

---

## Configuration reference

| Variable | Default | Meaning |
|---|---|---|
| `ANTHILL_MODEL` | `qwen2.5:3b` | model tag |
| `ANTHILL_BASE_URL` | `http://localhost:11434` | inference server |
| `ANTHILL_BACKEND` | `ollama` | `ollama` or `openai` |
| `ANTHILL_DB` | `data/anthill.db` | dashboard database |
| `ANTHILL_WORKSPACE` | `workspace` | local (personal) wiki root |
| `ANTHILL_ORG_WIKI` | `data/org-wiki` | consolidated org wiki root |
| `ANTHILL_JWT_SECRET` | generated | session signing |
| `ANTHILL_SESSION_HOURS` | 720 | inactivity window before login is required again |
| `ANTHILL_ENCRYPTION_KEY` | generated | at-rest encryption |
| `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` | - | Google login |
| `MICROSOFT_CLIENT_ID` / `MICROSOFT_CLIENT_SECRET` | - | Microsoft (Azure AD) login |
| `MICROSOFT_TENANT` | `common` | Azure AD tenant id, or `common` for multi-tenant |
| `ANTHILL_CLOUD_*` | - | opt-in hybrid cloud fallback |
| `ANTHILL_SMTP_HOST` / `ANTHILL_SMTP_FROM` | - | transactional email; **unset = no email is sent** (see below) |
| `ANTHILL_SMTP_PORT` / `ANTHILL_SMTP_USER` / `ANTHILL_SMTP_PASSWORD` / `ANTHILL_SMTP_TLS` | 587 / - / - / 1 | SMTP auth + transport |

External tool integrations (Slack, Notion, Jira, Google Drive, Microsoft 365, and more) are
added as **MCP servers** in the dashboard (**Connectors** → add server → admin approve), then
granted to an agent identity via the `mcp` scope. There is no separate per-connector env-var path.

Optional search/retrieval upgrades, each active only when set (built-in default
otherwise): `GOOGLE_SEARCH_API_KEY` + `GOOGLE_SEARCH_CX` (Google web search, else
DuckDuckGo) · `FIRECRAWL_API_KEY` / `JINA_API_KEY` (clean page extraction, else a direct
fetch) · `MEILI_URL` (self-hosted Meilisearch wiki retrieval, else embeddings).

**Transactional email.** Anthill sends welcome, password-reset, and password-changed emails over your own
SMTP relay - set `ANTHILL_SMTP_HOST` + `ANTHILL_SMTP_FROM` (and usually `_USER` / `_PASSWORD`). If unset,
no email is sent: a solo install shows the reset link in the browser, a multi-user org shows "ask your
admin." For **Proton Mail** (SMTP submission; a paid plan + custom domain), generate an SMTP token in
Proton **Settings -> IMAP/SMTP** and set `ANTHILL_SMTP_HOST=smtp.protonmail.ch`, `ANTHILL_SMTP_PORT=587`,
`ANTHILL_SMTP_TLS=1`, `ANTHILL_SMTP_USER=<your Proton address>`, `ANTHILL_SMTP_PASSWORD=<the SMTP token>`,
`ANTHILL_SMTP_FROM=<your Proton address>`. For deliverability, add SPF/DKIM/DMARC on the sending domain
(Gmail hard-rejects unauthenticated mail). Any other provider (Postmark, Resend, SES, ...) works via its
SMTP endpoint the same way.

---

## How we build: ASDD

Anthill is built and maintained in the open by **AI agents working under human direction** - a practice
we call **ASDD**: governed, transparent, and secure. It is also how contributions are expected
to work here, whether they come from a person or an agent:

- **Disclose agents.** If you use an AI agent to help with a contribution, say so in the PR. Agents
  identify as agents in commits and comments, so every change stays attributable.
- **Humans own the merges that matter.** Agents review and recommend; a human approves and merges.
  Reviews are advisory first, never an auto-merge on anything that matters.
- **Quality and security are gates, not suggestions.** Keep code lean and human-idiomatic (no AI-slop),
  expect an adversarial review, and never weaken the security posture (see [`SECURITY.md`](SECURITY.md)).

**Use any tool, or none.** ASDD does not mandate a coding tool. Use Claude, Codex, Cursor, a
local open model, or write it by hand - the gates enforce quality and security regardless of how a change
was produced, so contribution stays open. Whatever produced it, every PR needs the same four things, and
that is the whole contract:

1. **Sign-off (DCO)** - `git commit -s` on every commit.
2. **One lane label** - exactly one of `pillar:privacy` `pillar:knowledge` `pillar:model` `pillar:platform`
   `pillar:feature` `chore`.
3. **The authorship disclosure** - tick the human or AI-agent box in the PR template (if an agent helped,
   its commits also carry an `Agent:` trailer).
4. **Tests** for new behavior.

The gates check exactly these; nothing about your toolchain matters to them. The mechanics are in
[Pull requests](#pull-requests) below, and the pipeline itself is documented in
[`.github/asdd/README.md`](.github/asdd/README.md).

We are packaging this as an open standard any project can adopt: see the README's
[How it is built](README.md#how-it-is-built-asdd) section and
[`OneHillAI/ASDD`](https://github.com/OneHillAI/ASDD) (coming soon). If you'd rather build
on or extend Anthill than change its core, start with [Two ways to use Anthill](docs/use-or-build-on.md).

## Before you open a PR

Send each kind of contribution where it belongs, so the PR queue stays reviewable (spec:
[`docs/specs/contribution-policy-refinements.md`](docs/specs/contribution-policy-refinements.md)):

| You have | Do this |
|---|---|
| A bug or a small, obvious fix | Open a PR directly. No ceremony. |
| A feature or an architecture change | Open a **GitHub issue** first (or use the in-app **`/contribute`** flow, which drafts a spec), so the shape is agreed before code. |
| Any non-`chore` change | **Base it on a spec.** ASDD is spec-driven, so intake fails a substantive PR that neither links an existing `docs/specs/*.md` nor adds one it implements. If none exists, add `docs/specs/<name>.md` (problem, requirements, acceptance criteria) in the PR - the live `spec` review agent checks the change against it and comments what to fix. Spec: [`docs/specs/mandatory-spec-gate.md`](docs/specs/mandatory-spec-gate.md). |
| A refactor / test-only / CI-only change | Open it only if a maintainer asked - these are noisy to review unsolicited. |
| A question | A GitHub issue or discussion, not a PR. |
| A security issue | **Privately**, per [`SECURITY.md`](SECURITY.md) - never a public issue or PR. |

To keep any one author from flooding the queue (the pattern ASDD exists to stop), intake fails a PR
whose author already has more than `max_open_prs_per_author` PRs open ([`.asdd.yml`](.asdd.yml); land or
close some first). And every PR carries an **understanding attestation** - you affirm you have read and
stand behind the change, whoever or whatever wrote it. Disclosure is attribution; this is accountability.

## Pull requests

- Run `make test`, `make lint` (ruff), and `make typecheck` (mypy) before opening a PR - CI runs the same and blocks merge. Add tests for new behavior (mirror the style in `tests/`).
- **Sign off every commit in the PR (DCO):** `git commit -s` adds the `Signed-off-by` line the intake gate requires, including commits created by automation. For an existing unsigned commit, use `git commit --amend -s --no-edit` before pushing. The gate checks the entire PR range, not just the latest commit.
- **Tag a lane:** label the PR with exactly one of `pillar:privacy` `pillar:knowledge` `pillar:model` `pillar:platform` `pillar:feature` `chore`. The intake gate also checks the authorship disclosure in the PR template. See [`.github/asdd/README.md`](.github/asdd/README.md).
- Use [Conventional Commits](https://www.conventionalcommits.org) for messages (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`) - it keeps history scannable and lets the changelog be generated.
- Record user-facing changes as a fragment under [`changelog.d/`](changelog.d/README.md) (a file `<id>.<category>.md`), **not** by hand-editing `CHANGELOG.md` - the project follows [Keep a Changelog](https://keepachangelog.com) and [SemVer](https://semver.org), and per-PR fragments keep two PRs from ever conflicting on the changelog. Preview the pending section with `python scripts/build_changelog.py --draft`.
- Keep code lean and human-idiomatic - see the working protocol in [`AGENTS.md`](AGENTS.md).
- What's shipped and what's planned: [`ROADMAP.md`](ROADMAP.md).

## Releasing (maintainers)

A release is cut by pushing a version tag, which triggers `.github/workflows/release.yml` to build
the macOS `Anthill.dmg` and publish the GitHub Release. **Every version must be changelogged** - a
gate (`scripts/check-release.sh`) fails the release if it is not, and the same check runs in the test
suite, so a version bump without a changelog entry fails at PR time. To cut `x.y.z`:

1. Bump the version in `pyproject.toml` (and `anthill/__init__.py`).
2. Assemble the changelog from the PR fragments: `python scripts/build_changelog.py x.y.z <YYYY-MM-DD>`.
   It turns every `changelog.d/*.md` fragment into the `## [x.y.z]` section (grouped in Keep a Changelog
   order), inserts it below `## [Unreleased]`, and clears `changelog.d/`. Preview first with
   `python scripts/build_changelog.py --draft`. Spec:
   [`docs/specs/changelog-fragments.md`](docs/specs/changelog-fragments.md).
3. Add the credits: `python scripts/release_notes.py --since <last-tag> --version x.y.z` and copy its
   **Contributors** section under the new `## [x.y.z]` block - it honors both the human directing each
   change and, disclosed alongside them, the agent that did it (from the `Agent:` / `Co-Authored-By`
   trailers). Optionally have the advisory
   [`release-notes` lens](.github/asdd/agents/release-notes.md) add a short Highlights paragraph; a human
   always edits and approves - no agent publishes. Open the section with a short plain-language Highlights
   paragraph. The Release workflow puts the assembled section (Highlights, changes, Contributors) on the GitHub
   release page (`scripts/release_body.py`); read the page back after the tag is built. Spec:
   [`docs/specs/release-notes-and-credits.md`](docs/specs/release-notes-and-credits.md).
4. Merge that, then tag and push: `git tag vx.y.z && git push origin vx.y.z`. `scripts/check-release.sh`
   gates the tag: the version must match, `CHANGELOG.md` must have a `## [x.y.z]` section, and no
   `changelog.d/` fragment may be left unassembled (SemVer unchanged).

## Security

Please report vulnerabilities **privately** - see [`SECURITY.md`](SECURITY.md), not a
public issue.

## Code of conduct

Participation is governed by our [Code of Conduct](CODE_OF_CONDUCT.md).

## License

By contributing, you agree that your contributions are licensed under the project's
[AGPL v3](LICENSE) license.
