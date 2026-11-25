# Mozilla & 3-Node P2P Travel Demo Runbook

Run a repeatable 2-node (Mozilla demo: Raul + Christoph) or 3-node (Raul + Christoph + Peter De Vreede) peer-to-peer Anthill Desktop demo using realistic, non-sensitive Rome travel knowledge.

## 1. One-Command Launch (Each Participant)

Each participant runs a single command on their own machine (macOS or Linux) with their persona's travel dummy data pre-seeded into an isolated `~/.anthill-p2p-pilot` directory:

- **Node 1 (Raul - Trip Planner / Host):**
  ```bash
  bash scripts/run-p2p-pilot.sh --seed-demo raul
  ```
- **Node 2 (Christoph - Rome Local & Museum Guide):**
  ```bash
  bash scripts/run-p2p-pilot.sh --seed-demo christoph
  ```
- **Node 3 (Peter De Vreede - Italy Rail & Day-Trip Specialist):**
  ```bash
  bash scripts/run-p2p-pilot.sh --seed-demo peter
  ```

*(If running two nodes on one laptop for a solo Mozilla rehearsal, pass distinct `--port` and `ANTHILL_PILOT_DATA`, e.g. `ANTHILL_PILOT_DATA=/tmp/demo-a bash scripts/run-p2p-pilot.sh --port 8001 --no-tunnel --seed-demo raul` and `ANTHILL_PILOT_DATA=/tmp/demo-b bash scripts/run-p2p-pilot.sh --port 8002 --no-tunnel --seed-demo christoph`.)*

## 2. Pair the Nodes (2 Minutes)

1. Each node completes `/setup`, opens **Peers & Hills**, and switches **Node peer-to-peer** ON.
2. Each node pastes its printed `https://*.trycloudflare.com` URL into **This desktop's peer URL** and clicks **Save URL**.
3. **Node 1 (Raul)** clicks **Create pairing invite** and sends the `anthill-pair://v1/...` token to **Node 2 (Christoph)**.
4. **Christoph** pastes it into **Join with an invite** and clicks **Verify invite & compute SAS**.
5. Raul's screen **auto-updates** within ~2.5 seconds to show the matching 6-digit SAS code. Both confirm the code verbally and click **Codes match - Trust & Connect**.
6. *(For the 3-node test)* Repeat steps 3-5 with **Peter** on the same Hill (`Anthill Core Pilot`).

## 3. Lend Travel Knowledge (Keep Private Data Locked)

1. On **Christoph** and **Peter**, open **Peers & Hills -> Knowledge lending**.
2. Toggle **Travel** (`area:travel`) to **Lent** for `Anthill Core Pilot`.
3. Point out **Personal / Sensitive**: locked as **Never lent** in Python outside the model. (Raul's seeded `private-passport-and-health.md` contains `CANARY-PASSPORT-7749-NEVER-LEAK` to demonstrate zero-leak isolation.)

## 4. Live Demo Flow (Counter-Check + Two-Way + Review Gate)

1. **Counter-check from Raul -> Christoph (+ Peter):**
   - Click **Consult** on Christoph's peer row (or **Consult peers & Open threads** - the paired Hill is pre-selected with its peer count).
   - Ask:
     > *I am planning a 4-day trip to Rome in May - where should I stay, how do I get from Fiumicino airport, and what needs advance booking?*
   - Click **Draft locally & preview exit** -> show the live drafting indicator and the **Visible exit preview (Rule 2)** proving only the brief (never chat history or private wiki) leaves the machine.
   - Click **Send brief to selected peers** -> show the loading progress bar while Christoph's (and Peter's) 0-tool endpoints counter-check against their lent `Travel` notes:
     - Christoph's AI adds **FL1 regional train (EUR 8 to Trastevere)** vs Leonardo Express, **30-day Colosseum / 45-60 day Vatican booking windows**, and **Monday museum closures (Galleria Borghese)**.
     - Peter's AI adds **60-day Italo/Frecciarossa savings** and **Ostia Antica / Orvieto day trips**.
   - Click **Accept** on one suggestion and **Reject** on another (rejected suggestions stay visible; accepted suggestions update the draft and sign an `Acceptance` record).
2. **Prove two-way communication (Christoph -> Raul):**
   - On Christoph's desktop, click **Consult** on Raul's peer row and ask a follow-up question. Verify Raul's desktop responds live and shows up in both nodes' **Identity & egress** usage ledgers with zero stored prompt content.
3. **Open Thread & Knowledge Review Gate:**
   - Click **Open to people** to switch into a human-only Open Thread (Rule 11), then click **Propose to my wiki** and inspect **Knowledge -> Needs review** to show inbound/proposed pages never overwrite the wiki directly (Rule 4).
