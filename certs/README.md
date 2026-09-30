# Development certificates - NOT for production

These committed `.crt` files (`ca.crt`, `orchestrator.crt`, `node-1.crt`, `node-2.crt`) are a
**throwaway self-signed dev CA + certs** so the node mesh (mTLS between orchestrator and nodes)
works out of the box for **local development and the alpha**. They are intentionally checked in
for zero-config setup.

**Do not use these in production.** The CA is self-signed, the certs are shared across every
checkout, and the matching private keys (`*.key`) are generated locally and git-ignored - anyone
with this repo has the same public certs. They provide transport encryption for local dev, **not**
a trust root you should rely on for a real deployment.

For any real deployment:

- **Regenerate per deployment:** `bash scripts/gen-dev-certs.sh` writes a fresh CA + node/orchestrator
  certs (with unencrypted keys - still dev-grade), or
- **Bring your own:** issue certs from your own CA / your org PKI (or terminate TLS at a reverse
  proxy with real certificates - see *Production hardening* in `docs/setup.md`), and keep the private
  keys off the repo and access-controlled.

See `SECURITY.md` for the operator's security responsibilities.
