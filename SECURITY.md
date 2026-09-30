# Security Policy

Anthill is built around keeping an organization's data inside its own perimeter, so
we take security reports seriously.

## Reporting a vulnerability

**Please do not open a public issue for security vulnerabilities.**

Instead, report it privately:

- Use GitHub's **[Report a vulnerability](https://github.com/OneHillAI/Anthill/security/advisories/new)**
  (Security → Advisories), **or**
- Email **security@onehill.org** with the details.

Please include:

- A description of the issue and its impact.
- Steps to reproduce (proof-of-concept if you have one).
- Affected version / commit, and your environment (OS, Python version).

We'll acknowledge your report, keep you updated on our assessment, and credit you
when a fix ships (unless you'd prefer to stay anonymous). Please give us reasonable
time to address the issue before any public disclosure.

## Scope

Anthill runs locally and stores data on the operator's own machine. Some risks are
the operator's responsibility:

- The `.env` file holds the JWT secret and the AES-256-GCM encryption key. Losing the
  encryption key makes at-rest data unrecoverable; keep `.env` safe and backed up.
- The dashboard listens on `localhost` by default. Before exposing it, put it behind
  a reverse proxy with TLS (see the "Production hardening" notes in the setup guide).
- The certificates committed under `certs/` are **self-signed dev certs**, shared across every
  checkout - a **scaffold** for local mTLS that you terminate at your own proxy/ingress; the app itself
  speaks plain HTTP and does **not** load them. **Not for production**. Regenerate per deployment with
  `scripts/gen-dev-certs.sh`, or bring your own CA / real certificates (see `certs/README.md`).
- The Mac app is **unsigned / not notarized**, so macOS Gatekeeper blocks it on first launch
  ("Apple could not verify ... is free of malware"). On macOS 15+ (Sequoia / Tahoe) the old
  right-click → Open no longer clears this, and a quarantined unsigned app can even have its
  executable removed by XProtect. To run it before notarization ships: drag it to `/Applications`,
  then **either** run `xattr -dr com.apple.quarantine /Applications/Anthill.app` before the first
  launch, **or** after a blocked launch open **System Settings → Privacy & Security** and click
  **Open Anyway**. Code-signing + notarization (Apple Developer ID) is the real fix: the release
  pipeline signs, notarizes, and staples automatically once the Developer ID secrets are added
  (see [docs/RELEASE_SIGNING.md](docs/RELEASE_SIGNING.md)), after which the download just opens.

Reports about the project's own code, dependencies, auth, or data handling are
always in scope and welcome.
