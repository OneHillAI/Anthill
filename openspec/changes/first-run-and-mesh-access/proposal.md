# First-run setup and mesh access on servers others can reach

Lane: `pillar:privacy`. Spec: `docs/specs/first-run-and-mesh-access.md`.

## Why

Some entry points assumed a machine only one person can reach. When Anthill listens on a network they should
ask for proof: a setup token for the first account, the mesh token on mesh routes, and no reset link on the
page for a caller who is not at the machine.

## What changes

- A setup token for the first account on a server not known to listen on loopback only.
- The orchestrator's read routes use the mesh token. The node agent's chat API uses it from every caller when the
  node listens beyond loopback; a loopback-only node is unchanged.
- The reset link is on the page only for the machine itself on a one-account local server.
- `anthill serve` refuses an unprotected non-loopback bind. Docs, a changelog fragment and tests.

## Guardrails

- Desktop and loopback-only servers are unchanged. No new dependency. No change to stored data.
