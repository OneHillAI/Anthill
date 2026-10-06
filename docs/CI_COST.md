# Keeping GitHub Actions spend down

GitHub Actions cost here is driven by two things: **macOS runners bill at 10x** the Linux rate, and
**every pull request fires several workflows that re-run on each push.** This is what is wired to control
that, and what is left to you.

## What is wired in the workflows

- **Cancel superseded runs.** Every workflow that runs on each PR push carries a `concurrency` group with
  `cancel-in-progress: true` - `ci`, `pr-validation`, `asdd-invariants`, `supply-chain`, `security-audit`,
  and the review + intake workflows. A newer push to the same branch cancels the
  in-flight run instead of paying for both. This is the single biggest per-PR saving during active work.
  (`asdd-docsync` runs on pushes to `main` and intentionally serializes rather than cancels, so a doc
  update is never dropped; `pr-review-publish` is triggered by a completed review, not per push.)
- **Do not scan on unrelated changes.** `supply-chain` runs only when dependency files change
  (`pyproject.toml`, `requirements*.txt`, the Tauri `Cargo.*`, lockfiles); `asdd-invariants` (report-only)
  skips docs-only PRs.
- **The intake gate already re-runs on a label change** (`labeled`/`unlabeled` triggers), so there is no
  need to push an empty commit to re-run intake after adding a lane label.
- **`ci` caches the pip download and runs tests in parallel.** The `test` and `browser` jobs set
  `cache: pip` (keyed on `pyproject.toml`) so wheels are reused across runs, and the `test` job runs
  `pytest -n 4 --dist loadscope` (needs `pytest-xdist`). `loadscope` keeps each test module on one
  worker; the count is pinned to 4 (the hosted runner's cores) because over-subscribing this
  import-heavy suite is slower, not faster. Parallelism is only safe because the suite is hermetic:
  `tests/conftest.py` blocks every httpx call to the Ollama port, so a dev's live model backend can't
  make the review-gate tests race (which is what surfaced when `pytest -n` was first tried).

## Move the macOS build to your own Mac (the 10x saving)

`release.yml` and `desktop-release.yml` read a repo variable for their runner:

```yaml
runs-on: ${{ vars.MACOS_RUNNER || 'macos-14' }}
```

By default this is GitHub's hosted `macos-14` (billed at 10x). To build for free on your own always-on
Mac (for example the foundation's Mac Mini):

1. Register it as a self-hosted runner: repo **Settings -> Actions -> Runners -> New self-hosted runner**,
   pick macOS/arm64, and give it a label (for example `mac-mini`).
2. Set the repo **Variable** `MACOS_RUNNER` to that label (Settings -> Secrets and variables -> Actions
   -> Variables). The signing certs and notarization keys stay as they are (the same secrets are read).
3. The next tagged release builds on your Mac at no Actions cost. To go back to hosted, clear the variable.

## One caveat on the path filters

If `supply-chain` or `asdd-invariants` is a **required** status check in branch protection, a PR that
skips it (a docs-only or code-only change) will show that check as never-arriving and cannot merge. If
that happens, either mark the check **not required** in branch protection, or remove the `paths` filter
from that workflow. `ci` was deliberately **not** path-filtered for this reason.

## Left for you (bigger, situational)

- **Batch releases.** The macOS build is the expensive step. Cut a release on a cadence, not once per
  user-facing merge.
- **Self-hosted for Linux CI too** is possible but lower value; Linux is 1x, so the win is small next to
  the macOS move above.
