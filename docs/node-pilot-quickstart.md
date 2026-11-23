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
