# Spec: first-run setup and mesh access on servers others can reach

Lane `pillar:privacy`. OpenSpec change: `openspec/changes/first-run-and-mesh-access/`.

## Problem

Several entry points were written for a machine only one person can reach. Once Anthill listens beyond that
machine (a container, an appliance, `--host 0.0.0.0`, or a node agent or orchestrator on a network), they
trust a caller more than they should:

- the first account on a fresh server is created by whoever reaches the sign-up page first;
- with no mail server, the password-reset link is shown on the page on a server that has one account;
- the orchestrator's read routes and the node agent's chat API do not use the mesh token that the other
  mesh routes already use, so a node started with a token still answers callers that do not have it.

## Requirements

**R1. First account.** While no account exists and the server is not known to listen on loopback only, the
first account (password sign-up and first-run single sign-on) needs a setup token. The token is
`ANTHILL_SETUP_TOKEN` when set, otherwise a random value made once per process, printed in the server console
at start. A server that listens on loopback only (desktop, `start.sh`, `anthill web` on its default address)
is unchanged.

**R2. Reset link.** With no mail server, the reset link is shown on the page only to a request from this
machine itself (loopback client, loopback host name, no proxy or tunnel header) on a server that is not
shared and has a single account. Otherwise it is written to the console only. The page is identical for every
address.

**R3. Orchestrator.** Every orchestrator route except `/health` requires the mesh token. The wiki agent sends
it.

**R4. Node agent.** The OpenAI-compatible routes answer a request from this machine with no token. Every other
caller needs the mesh token. `anthill serve` refuses to listen on a non-loopback address with no
`ANTHILL_MESH_TOKEN` and no `ANTHILL_MESH_ALLOW_INSECURE`.

**R5. No other behaviour change.** Desktop, loopback-only servers and signed-in flows are unchanged.

## Acceptance

- A first sign-up on a server listening on `0.0.0.0` is refused without the token and accepted with it; a
  loopback server needs none; single sign-on cannot create the first account on a reachable server.
- The reset link is not in the page for a network caller or a proxied loopback caller, matched or not, and is
  in the page for the machine itself on a one-account local server.
- Every orchestrator GET route except `/health` fails without the token (a test lists the routes, so a new
  unguarded one fails).
- The node agent chat routes answer loopback with no token, refuse other callers without it, treat a
  forwarded loopback request as another caller, and `anthill serve` refuses an unprotected non-loopback bind.
- The full test suite, `ruff` and `mypy` pass.

## What was not verified

- The changes were exercised with the test client only, not with a real container, a second machine or a real
  proxy or tunnel.
- The demo compose file still sets no mesh token, so its nodes still cannot reach the orchestrator.
