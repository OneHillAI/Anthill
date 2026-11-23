# Node Pillar P2P Pilot Quickstart

Run two isolated Anthill Desktop instances locally (or across two Macs on a LAN/Tailscale) to test pairing, encrypted `.anthill-bundle` sync, live authenticated peer HTTP sync, 0-tool counter-check consultations, and the Knowledge Review Gate.

## Prerequisites

- Anthill installed (`make install`)
- Local Ollama running (`ollama serve`) with a local chat model (e.g. `ollama pull qwen2.5:3b`)
- Branch checked out: `git checkout feat/node-p2p-pilot`

## 1. Start Two Local Desktop Instances

Open two terminal windows and start each instance with its own data directory, SQLite DB, encryption key, and port:

### Terminal 1 - Instance A (port 8001)

```bash
mkdir -p /tmp/anthill-pilot-a
ANTHILL_ALLOW_EPHEMERAL_KEY=1 \
ANTHILL_HOME=/tmp/anthill-pilot-a \
ANTHILL_DB=/tmp/anthill-pilot-a/anthill.db \
ANTHILL_ORG_WIKI=/tmp/anthill-pilot-a/org-wiki \
ANTHILL_LOCAL_ONLY=1 \
.venv/bin/anthill web --host 127.0.0.1 --port 8001
```

### Terminal 2 - Instance B (port 8002)

```bash
mkdir -p /tmp/anthill-pilot-b
ANTHILL_ALLOW_EPHEMERAL_KEY=1 \
ANTHILL_HOME=/tmp/anthill-pilot-b \
ANTHILL_DB=/tmp/anthill-pilot-b/anthill.db \
ANTHILL_ORG_WIKI=/tmp/anthill-pilot-b/org-wiki \
ANTHILL_LOCAL_ONLY=1 \
.venv/bin/anthill web --host 127.0.0.1 --port 8002
```

> **Two separate laptops on a LAN or Tailscale:** replace `--host 127.0.0.1` with `--host 0.0.0.0` (explicit opt-in) and use each machine's LAN or Tailscale IP in the peer URL field (for example `http://192.168.1.42:8001`).

Complete the quick first-run setup on both:
- Instance A: `http://127.0.0.1:8001/setup`
- Instance B: `http://127.0.0.1:8002/setup`

## 2. Enable Node Pillar & Pair with SAS Verification

1. On both instances, click **Peers & Hills** in the left sidebar. Notice the Node pillar starts **off by default** (Rule 12).
2. Click **Enable Node peer-to-peer** on both instances.
3. Under **Connections -> Transport layer**, save each desktop's reachable peer URL:
   - Instance A: `http://127.0.0.1:8001`
   - Instance B: `http://127.0.0.1:8002`
4. On Instance A, click **Create pairing invite** and copy the `anthill-pair://v1/...` token.
5. On Instance B, paste the token into **Join with an invite** and click **Verify invite & compute SAS**.
6. Refresh Instance A's **Peers & Hills** page: both desktops now show the **exact same 6-digit SAS code** (e.g. `482 - 917`) and each other's Ed25519 root key fingerprint.
7. Click **Codes match - Trust & Connect** on both desktops to pin keys and sign the Hill's `Membership` record.

## 3. Test Offline Encrypted `.anthill-bundle` Sync

1. On Instance A, add a wiki page under **Knowledge -> Wiki** (or save a snippet).
2. Open **Peers & Hills -> Record log & sync**, select the page under **Propose a local wiki page into a Hill log**, and click **Sign PageChange**.
3. Click **Export .anthill-bundle** to download an X25519-encrypted, Ed25519-signed bundle.
4. On Instance B, go to **Peers & Hills -> Record log & sync**, upload the `.anthill-bundle` file, and click **Verify & Import**.
5. Open **Knowledge -> Needs review** on Instance B: the incoming page sits strictly as **Proposed** (Rule 4) and is never written directly to Instance B's wiki until you click **Approve**.

## 4. Test Live LAN HTTP Sync & Counter-Check Consultation

1. On Instance B, create a wiki page tagged `area:energy` (and optionally a `private-health` page to test the locked `Personal / Sensitive` boundary).
2. Go to **Peers & Hills -> Knowledge lending** on Instance B and toggle **Lent** for the energy area on `Anthill Core Pilot`. Notice **Personal / Sensitive** stays locked (`Never lent`).
3. On Instance A, click **Consult peers & Open threads** (or click the **Consult peers** button on any Chat assistant reply), enter a question + local draft, and click **Draft locally & preview exit**.
4. Review the **Visible exit preview (Rule 2)** showing the scrubbed question and draft brief only, then click **Send brief to selected peers**.
5. Instance B's isolated 0-tool peer endpoint answers strictly from lent areas (capped at 600 tokens, PII-scrubbed, 20 req/hr quota) and returns structured suggestions.
6. On Instance A, click **Accept** or **Reject** on suggestions (rejected items stay visible; accepted items write signed `Acceptance` records and update local karma), optionally click **Open to people** to switch to a human-only Open Thread, and click **Propose to my wiki** to send the result through the Review Gate.
7. Inspect **Peers & Hills -> Identity & egress** on both instances to verify zero-content usage ledger entries, test the 33-word recovery kit, or download an open-format OKGF + signed JSONL export.

## 5. Connecting Two Desktops in Two Different Locations (Over the Internet)

When two testers are on different networks (not the same Wi-Fi/LAN), Anthill keeps its web server bound to loopback (`127.0.0.1`) and connects over HTTPS via a zero-account Cloudflare Quick Tunnel (`cloudflared`) or Tailscale. No router port forwarding is required.

### Option 5A: Both machines open a quick tunnel (full bidirectional sync + consult)

Each person starts Anthill on loopback (`127.0.0.1:8001`) plus a quick tunnel in a second terminal:

```bash
# Install once on macOS if needed: brew install cloudflared
cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8001
```

`cloudflared` prints a temporary public HTTPS URL such as `https://example-words.trycloudflare.com`.

1. Both testers enable **Node peer-to-peer** under **Peers & Hills** and save their own `https://...trycloudflare.com` URL under **This desktop's peer URL**.
2. Person A clicks **Create pairing invite** and sends the `anthill-pair://v1/...` token to Person B (Signal/Slack/WhatsApp).
3. Person B pastes it under **Join with an invite** (with Person B's own `https://...trycloudflare.com` URL filled in) and clicks **Verify invite & compute SAS**.
4. Both verify the **exact same 6-digit SAS code** out-of-band (voice call / Signal) and click **Codes match - Trust & Connect**.
5. Live **Sync**, **Knowledge lending**, and **Consult peers** now run end-to-end across the Internet, with every HTTP request Ed25519-signed and verified against the pinned peer keys.
6. If either machine restarts `cloudflared` and gets a new hostname, open **Update peer URL (LAN / Tailscale / tunnel)** on that peer's row to update the URL without re-pairing.

### Option 5B: One-sided NAT (only one person opens a tunnel)

Only Person A runs `cloudflared`. Person B leaves **This desktop's peer URL** blank:
- Person B joins A's invite live, clicks **Sync** (one HTTPS POST both pushes B's missing records to A and pulls A's missing records to B), and runs live **Consult peers** against A's AI.
- For offline/air-gapped tests with zero tunnels, exchange the offline join-response JSON + verify SAS, then pass `.anthill-bundle` files over any messenger or email.

## 6. Autonomous Coding Agent Prompt

Paste this prompt directly into a coding agent (Claude Code, Antigravity, Codex, etc.) to set up and launch the pilot autonomously:

```text
Set up and launch the Anthill peer-to-peer Node pillar proof-of-concept from branch `feat/node-p2p-pilot` of `https://github.com/OneHillAI/Anthill`.

Do everything autonomously step by step:
1. If not already in the Anthill repo, clone `https://github.com/OneHillAI/Anthill.git` and cd into it. Run `git fetch origin && git checkout feat/node-p2p-pilot && git pull --ff-only origin feat/node-p2p-pilot`.
2. Ensure the Python venv is installed (`make install` if `.venv/bin/anthill` is missing).
3. Verify the pilot works locally by running `.venv/bin/pytest -q tests/test_node_p2p.py` and confirm all tests pass (including the cross-Internet HTTPS tunnel + one-sided NAT test).
4. Ensure Ollama is running locally (`ollama serve` in the background if `http://127.0.0.1:11434/api/tags` is unreachable) and ensure at least one small local chat model (such as `qwen2.5:3b`) is pulled.
5. Start an isolated pilot Anthill Desktop instance in the background on `127.0.0.1:8001` with its own clean data dir `/tmp/anthill-pilot-me` (`ANTHILL_ALLOW_EPHEMERAL_KEY=1`, `ANTHILL_HOME=/tmp/anthill-pilot-me`, `ANTHILL_DB=/tmp/anthill-pilot-me/anthill.db`, `ANTHILL_ORG_WIKI=/tmp/anthill-pilot-me/org-wiki`, `ANTHILL_LOCAL_ONLY=1`), and wait until `http://127.0.0.1:8001/` responds.
6. To allow connecting live with a remote peer in another location over the Internet, check if `cloudflared` is installed (install via `brew install cloudflared` on macOS if missing and brew is available). Start `cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8001` in the background and capture the printed `https://*.trycloudflare.com` URL. (If `cloudflared` is unavailable, report that offline `.anthill-bundle` mode or Tailscale IP can be used instead.)
7. Optionally, if I ask to test solo first, also start a second isolated instance on `127.0.0.1:8002` using `/tmp/anthill-pilot-peer`.
8. Report back clearly with:
   - Test pass summary
   - Local browser URL to open (`http://127.0.0.1:8001/setup` then `/peers`)
   - Public HTTPS tunnel URL to paste into "This desktop's peer URL" for cross-Internet pairing
   - PIDs / stop commands for the background Anthill and cloudflared processes
   - The 4-step checklist to pair with Raul via `anthill-pair://v1/...` + 6-digit SAS verification, sync an encrypted `.anthill-bundle` or live HTTPS delta, lend a wiki area, and run a 0-tool counter-check consultation.
```

