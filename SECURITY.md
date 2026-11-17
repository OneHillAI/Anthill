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

- Saved credentials are AES-256-GCM encrypted. On a Mac the key (and the session secret) is kept in the login
  Keychain, with a small non-secret marker (`key-location.json`) in the data folder; elsewhere it stays in
  `secrets.env` or `.env`, which is a plain file and not protected. Losing the key makes the saved credentials
  unreadable, and a copy of the data folder on another Mac will not open them.
- Backups and copies of the data folder made before the key moved still open every saved password. Delete older
  backups and old copies of `secrets.env`; to make them useless, change those passwords or API keys at their
  source and enter the new ones. The wiki, chats, files and cache are not encrypted by Anthill: turn on disk
  encryption (FileVault on a Mac) so a lost or stolen computer cannot be read.
- The dashboard listens on `localhost` by default. Before exposing it, put it behind
  a reverse proxy with TLS (see the "Production hardening" notes in the setup guide).
- The certificates committed under `certs/` are **self-signed dev certs**, shared across every
  checkout - a **scaffold** for local mTLS that you terminate at your own proxy/ingress; the app itself
  speaks plain HTTP and does **not** load them. **Not for production**. Regenerate per deployment with
  `scripts/gen-dev-certs.sh`, or bring your own CA / real certificates (see `certs/README.md`).
- The official Mac release is signed with an Apple Developer ID, notarized and stapled (see the changelog and
  [docs/RELEASE_SIGNING.md](docs/RELEASE_SIGNING.md)), so it opens without a Gatekeeper warning. A Mac app you
  build yourself is unsigned: macOS blocks it on first launch, and on macOS 15+ the old right-click → Open no
  longer clears that. Drag it to `/Applications`, then **either** run
  `xattr -dr com.apple.quarantine /Applications/Anthill.app` before the first launch, **or** after a blocked
  launch open **System Settings → Privacy & Security** and click **Open Anyway**.

Reports about the project's own code, dependencies, auth, or data handling are
always in scope and welcome.
