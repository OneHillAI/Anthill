# Spec: clear the dependency advisories that have a fix

Status: implemented
Lane: `pillar:platform`
Relates to: `requirements.lock`, `src-tauri/Cargo.lock`, `docs-site/package.json`, `docs-site/package-lock.json`,
`.github/workflows/pr-validation.yml`, `.github/workflows/security-audit.yml`,
`docs/specs/regenerate-requirements-lock.md`, `docs/specs/replace-python-jose-with-pyjwt.md`

## 1. Problem

GitHub's dependency alerts and `pip-audit` report known advisories in libraries we use. Two gaps made them pile up.

- The packaged app ships the versions pinned in `requirements.lock`, but the CI audit installs the newest versions
  allowed by `pyproject.toml`. The audit passed while the shipped pins held 38 distinct advisories in seven packages
  (42 entries in `pip-audit`'s raw output, which lists an advisory twice when it has two IDs): `pypdf` (14, it reads
  uploaded PDFs), `pyjwt` (14, login sessions), `anyio` (3), `urllib3` (3), `soupsieve` (2), `cryptography` (1) and
  `h2` (1). Every one has a fixed version.
- The desktop shell (`src-tauri/Cargo.lock`) pins `rustls` 0.23.41; the advisory GHSA-2mjx-qc3c-rqvc is fixed in
  0.23.45. The docs site (`docs-site`, built on GitHub's servers, not shipped in the app) pins four libraries with
  fixed versions: `tinypool`, `source-map-js`, `postcss-selector-parser` and `http-cache-semantics`.

Dependabot opened pull requests for some of these, but they cannot pass intake (no disclosure, no lane label), and
one of them (`rustls` 0.23.43) did not reach the fixed version.

## 2. Requirements

R1. THE shipped lock (`requirements.lock`) SHALL pin no package that has a known advisory with a fixed version.
`pip-audit` of the lock SHALL report no known vulnerability. Only the seven affected packages change version.

R2. THE desktop shell lock SHALL pin `rustls` at 0.23.45 or later (and `rustls-webpki` as Cargo requires).

R3. THE docs site SHALL resolve `tinypool` 2.1.2 or later, `source-map-js` 1.2.2 or later,
`postcss-selector-parser` 7.1.6 or later and `http-cache-semantics` 4.3.0 or later, and SHALL still build.

R4. THE two audit workflows SHALL carry no `--ignore-vuln` exception. The one exception they had
(`PYSEC-2026-1325`, in `ecdsa`, which only python-jose pulled in) is removed because `ecdsa` is not installed
any more.

## 3. Not in this change

- `braces` (docs site) has no patched version, and `glib` (desktop shell) needs the Linux GTK stack, which is not a
  supported target. Both stay open; dismissing an alert is the owner's decision.
- The audit's blind spot itself. CI installs from `pyproject.toml`, not from the lock, so a pinned package that has
  since gained an advisory is not seen by CI. Auditing the lock in CI is a separate decision, because it turns a
  PR red when someone publishes a new advisory.

## 4. Acceptance

- `pip-audit --require-hashes -r requirements.lock` reports no known vulnerability.
- A fresh Python 3.11 environment installs the lock with `--require-hashes`, `uv pip check` passes, and the
  packaged sidecar builds and passes its own checks (office, semantic cache, a real chat, a catalog refresh).
- `cargo metadata --locked` accepts `src-tauri/Cargo.lock`; the Windows CI job builds the shell.
- `npm run build` in `docs-site` succeeds with the new versions.
- `pip-audit` with no exceptions passes on the CI environment.
