#!/usr/bin/env bash
# anthill local demo - no Docker required.
# Shows the full v1 loop: wiki → cache hit → org index hit → agent → promote.
#
# Prerequisites:
#   1. make install  (creates .venv)
#   2. Ollama running: ~/bin/ollama serve
#   3. Model pulled:   ~/bin/ollama pull qwen2.5:3b
set -euo pipefail

ANTHILL=".venv/bin/anthill"
WIKI_A="/tmp/anthill-demo-a"
WIKI_B="/tmp/anthill-demo-b"
ORG_WIKI="/tmp/anthill-demo-org"
CENTRAL="/tmp/anthill-demo-central"
ORCH_PORT=8083
ORCH_URL="http://127.0.0.1:${ORCH_PORT}"
ORCH_PID=""

cleanup() {
    [ -n "$ORCH_PID" ] && kill "$ORCH_PID" 2>/dev/null || true
    rm -rf "$WIKI_A" "$WIKI_B" "$ORG_WIKI" "$CENTRAL"
}
trap cleanup EXIT

hr() { echo; echo "─────────────────────────────────────────"; echo "  $*"; echo "─────────────────────────────────────────"; }

# ── start orchestrator ────────────────────────────────────────────────────────
hr "Starting orchestrator on :${ORCH_PORT}"
ANTHILL_ORG_WIKI="$ORG_WIKI" ANTHILL_CENTRAL_INDEX="$CENTRAL" \
    .venv/bin/uvicorn anthill.orchestrator.app:app \
    --host 127.0.0.1 --port "$ORCH_PORT" --log-level warning &
ORCH_PID=$!
sleep 2

# Register two nodes
curl -s -X POST "$ORCH_URL/nodes/register" \
    -H "Content-Type: application/json" \
    -d '{"node_id":"alice","base_url":"http://localhost:9001","model":"qwen2.5:3b"}' > /dev/null
curl -s -X POST "$ORCH_URL/nodes/register" \
    -H "Content-Type: application/json" \
    -d '{"node_id":"bob","base_url":"http://localhost:9002","model":"qwen2.5:3b"}' > /dev/null

echo "Orchestrator up. Live nodes: $(curl -s "$ORCH_URL/nodes" | python3 -c "import sys,json; print(', '.join(n['node_id'] for n in json.load(sys.stdin)))")"

# ── alice: init + ingest ──────────────────────────────────────────────────────
hr "Alice: init wiki + ingest architecture decisions"
"$ANTHILL" -w "$WIKI_A" init
"$ANTHILL" -w "$WIKI_A" ingest demo-docs/architecture-decisions.md
echo "(wiki page written)"

# ── alice: ask → generates + publishes to org index ──────────────────────────
hr "Alice: ask (will generate, then publish to org index)"
ANTHILL_NODE_ID=alice "$ANTHILL" -w "$WIKI_A" ask \
    "which database did we choose for billing and why?" \
    --org-url "$ORCH_URL"

echo
echo "Org index now has: $(curl -s "$ORCH_URL/cache/stats" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d['total_entries'], 'entr(ies)')")"

# ── alice: same question again → local cache hit ──────────────────────────────
hr "Alice: same question again (local cache hit - no inference)"
ANTHILL_NODE_ID=alice "$ANTHILL" -w "$WIKI_A" ask \
    "which database did we choose for billing and why?" \
    --org-url "$ORCH_URL"

# ── bob: same question, empty wiki → org index hit ───────────────────────────
hr "Bob: same question on an empty wiki (org index hit - no inference)"
"$ANTHILL" -w "$WIKI_B" init
ANTHILL_NODE_ID=bob "$ANTHILL" -w "$WIKI_B" ask \
    "which database did we choose for billing and why?" \
    --org-url "$ORCH_URL"

# ── alice: drop a file in inbox + run agent ───────────────────────────────────
hr "Alice: drop a file in inbox, run maintenance agent"
cp demo-docs/architecture-decisions.md "$WIKI_A/inbox/extra-notes.md"
ANTHILL_NODE_ID=alice "$ANTHILL" -w "$WIKI_A" agent run --org-url "$ORCH_URL"

# ── alice: promote to org wiki ────────────────────────────────────────────────
hr "Alice: promote architecture-decisions → org wiki"
ANTHILL_NODE_ID=alice "$ANTHILL" -w "$WIKI_A" promote architecture-decisions \
    --org-url "$ORCH_URL" --node-id alice

echo
echo "Org wiki pages: $(curl -s "$ORCH_URL/wiki/pages" | python3 -c "import sys,json; print([p['slug'] for p in json.load(sys.stdin)])")"

# ── inference router ──────────────────────────────────────────────────────────
hr "Inference router: which node to use for qwen2.5:3b?"
curl -s -X POST "$ORCH_URL/route" \
    -H "Content-Type: application/json" \
    -d '{"model":"qwen2.5:3b"}' | python3 -c "import sys,json; d=json.load(sys.stdin); print(f\"  → {d['node_id']} (load={d['load']})\")"

hr "Demo complete ✓"
echo "  Org wiki:   $ORG_WIKI"
echo "  Org index:  $CENTRAL"
echo "  Alice wiki: $WIKI_A"
echo "  Bob wiki:   $WIKI_B"
