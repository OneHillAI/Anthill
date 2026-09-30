# anthill - Architecture (v2)

> An organization-scoped GPT that runs on a fleet of local machines, coordinated by a private orchestrator. Open-weight LLMs stay on-device. The organization's knowledge accumulates into a **wiki layer** the model maintains itself, and improvements consolidate into a private cloud the org controls end-to-end. **Nothing leaves the perimeter.**

This is the source-of-truth design doc. It is grounded in the mid-2026 state of the art and reflects the decisions made with the project owner (a non-engineer driving the product vision).

**Key decisions locked for v1:**
- License: **AGPL v3** (network copyleft keeps forks and hosted services open; a commercial license covers closed distribution and closed modified services; commercial use under the AGPL terms needs none).
- Package name: **`anthill`**.
- Build order: **wiki layer + semantic cache first.** Federated LoRA is demoted to a later, optional, research-grade phase - not the spine.

---

## 1. Why this exists

Centralized hosted LLMs concentrate cost, energy, GPU demand, internet traffic, and - most importantly - the organization's data inside a vendor's perimeter. The status quo is **expensive** (API spend scales with use), **wasteful** (every query is recomputed even if the org already answered it), **centralized** (power and outages concentrate in a few data centers), **leaky** (data crosses the org boundary on every call), and **generic** (the model never gets better at *your* work without paying to fine-tune and shipping your data out).

**The seven value propositions** (the canonical pitch - it is not *only* privacy):

1. **Resilience** - own-hardware inference means no rate limits, API deprecations, or vendor outages can throttle or cut access; works offline.
2. **Cost** - repeat work is served from cache/wiki at ~zero marginal cost; savings are dramatic at scale.
3. **Latency** - no network round-trip or vendor queueing; cache hits are instant; generation latency is bounded by hardware you control.
4. **Energy** - the win is *avoided* inference (cache hits) plus running on idle hardware.
5. **Sovereignty** - data stays inside the org perimeter unless an explicit, policy-gated action promotes it.
6. **Ownership** - the org trains and keeps its **own** open-weight model on its own gold data (Phase 4); a managed cloud assistant personalizes but never hands you the model.
7. **Offline-capable** - after a one-time model + embeddings download, the entire core (chat, wiki, cache, org-LAN sharing, automation, training) runs with no internet; every internet feature (web search, paid cloud, SaaS connectors) is an opt-in extra whose absence disables only itself.

| Concern | Status quo | anthill |
|---|---|---|
| Compute location | Vendor cloud | Org's own machines + private cloud |
| Data movement | Out of the org | Never leaves the org perimeter |
| Accumulated knowledge | None - re-retrieved each time | A wiki the model curates and grows |
| Redundant work | Re-billed every time | Semantic cache + wiki reuse prior answers |
| Personalization | Generic model | Org wiki + (later) org-specific LoRA adapters |
| GPU usage | Centralized, peaky | Distributed across idle org hardware |
| Model updates | Vendor-dictated | Org pulls latest open weights on its own schedule |

This is feasible in 2026 because three things converged: (1) open-weight models are now frontier-competitive (DeepSeek V4 Pro, Qwen 3.6, GLM-5, Kimi K2.6, Llama 4, Gemma 4); (2) the **LLM-maintained wiki** pattern (Karpathy, Apr 2026) makes "knowledge that compounds" a buildable thing today, with zero ML risk; and (3) federated LoRA fine-tuning has matured *in research* enough to be a credible later phase.

---

## 2. The three ways the org gets smarter

The product goal - "don't start from scratch every time; optimize and personalize from already-built answers" - is served by **three mechanisms, ordered here by difficulty, not by glamour.** v1 ships the cheap, high-value ones first.

| # | Mechanism | What it delivers | Difficulty | Phase |
|---|---|---|---|---|
| 1 | **Wiki layer** - curated, LLM-maintained markdown knowledge that compounds | Most of "nothing leaves, don't redo work, org learns its own tasks" | **Low - buildable now** | 1-2 |
| 2 | **Semantic cache** - reuse a prior answer when a new prompt is close enough | Cuts recompute, cost, energy, GPU load | Low-medium | 2-3 |
| 3 | **Federated LoRA** - the model literally re-weights on org data, privately | Deep personalization beyond what text retrieval can do | **High, research-grade** | 4+ (optional) |

The previous draft of this architecture led with mechanism 3. That is backwards for a team that wants something real soon: mechanisms 1 and 2 deliver ~80% of the stated goals with ~20% of the complexity, and they run on today's tooling. Federated LoRA is treated as a **prove-it-later spike**, not a committed dependency.

---

## 3. Design principles

1. **Wiki-first.** The default way the org accumulates knowledge is a markdown wiki the model curates. It is plain text, diffable, inspectable, and immune to model/version churn.
2. **Data locality is the default.** Prompts, completions, documents, embeddings, and per-user wikis stay on the node that produced them unless an explicit, policy-gated action promotes them.
3. **Cache before compute.** Every prompt is a semantic-cache lookup (and a wiki lookup) before it is an inference call.
4. **Adapters move, not data** *(when federation is enabled)*. Federated improvements travel as LoRA weight deltas and aggregated embeddings - never raw text.
5. **The base model is replaceable.** Everything is tied to a base-model fingerprint; when a new open-weight release lands, we re-base rather than lock in.
6. **Encrypted in transit, encrypted at rest, attested when it matters.** mTLS + libsodium for transport; AES-GCM at rest; optional TEE attestation for sensitive aggregation.
7. **Heterogeneous fleet is the norm.** A Mac mini, a workstation with one GPU, and a small GPU server must coexist.
8. **Fork-friendly.** Single-binary node agent, `docker compose up` for the demo, no vendor lock-in.

---

## 4. System overview

```
                                  ┌──────────────────────────────────────┐
                                  │      Private Cloud (Org-owned)       │
                                  │                                      │
                                  │   ┌────────────────────────────────┐ │
                                  │   │       Orchestrator             │ │
                                  │   │  - Node registry & health      │ │
                                  │   │  - Inference router            │ │
                                  │   │  - ORG WIKI (consolidated)     │ │
                                  │   │  - Central semantic index      │ │
                                  │   │  - Policy & audit              │ │
                                  │   │  - Federation coordinator [P4] │ │
                                  │   │  - Adapter registry       [P4] │ │
                                  │   └────────────────────────────────┘ │
                                  │             ▲      ▲                 │
                                  └─────────────┼──────┼─────────────────┘
                                   mTLS + Noise │      │ mTLS + Noise
                       ┌───────────────────────┘       └──────────────────────┐
                       │                                                       │
        ┌──────────────▼──────────────┐                       ┌────────────────▼────────────┐
        │      Node Agent (org PC)     │       ...             │     Node Agent (server)     │
        │                              │                       │                             │
        │  - Local inference (Ollama)  │                       │  - Local inference (vLLM)   │
        │  - PER-USER WIKI (private)   │                       │  - PER-USER WIKI (private)  │
        │  - Local semantic cache      │                       │  - Local semantic cache     │
        │  - Local vector store (RAG)  │                       │  - Local vector store (RAG) │
        │  - Proactive maint. agent    │                       │  - Proactive maint. agent   │
        │  - Local LoRA trainer   [P4] │                       │  - Local LoRA trainer  [P4] │
        └──────────────────────────────┘                       └─────────────────────────────┘
                       ▲
                       │ localhost only
        ┌──────────────┴──────────────┐
        │     User (org employee)     │
        │  - Chat UI / IDE / app SDK  │
        └─────────────────────────────┘

[P4] = Phase 4, optional / research-grade. Not in v1.
```

Three planes:
- **Control plane** (gRPC + mTLS): registration, heartbeats, routing, wiki-promotion approvals, (later) federation rounds.
- **Data plane** (HTTPS + mTLS, streaming): prompt → completion; cross-node cache fetch; wiki sync.
- **Storage plane** (encrypted at rest, per-node): per-user wiki, vector store, semantic cache, optional logs, (later) adapters keyed by base-model fingerprint.

---

## 5. The wiki layer (the spine)

Adapted from [Karpathy's "LLM Wiki"](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f) (gist, Apr 4 2026). Instead of re-retrieving raw fragments on every question (classic RAG), the model **incrementally compiles** sources into a persistent, interlinked markdown knowledge base. Knowledge *accumulates* instead of being rediscovered each time. This is the core answer to "don't start from scratch."

### 5.1 Structure

Three layers, on disk, per scope:

```
wiki-root/
  raw/         # immutable source documents (articles, PDFs, transcripts, data)
  inbox/       # newly dropped sources awaiting ingestion
  wiki/        # LLM-OWNED markdown: summaries, entity pages, concept pages
  index.md     # catalog: every page + one-line summary + metadata
  log.md       # append-only timeline, e.g. "## [2026-04-02] ingest | <title>"
  SCHEMA.md    # the rules: structure, naming conventions, workflows
```

- **The model owns `wiki/` entirely.** It creates pages, updates them when sources arrive, maintains cross-references, keeps things consistent.
- **Ingest** a source → the model reads it, writes a summary page, updates `index.md`, revises the ~10-15 related entity/concept pages, appends to `log.md`.
- **Answers become pages.** A good answer to a question is filed back as a wiki page, so explorations compound exactly like ingested sources.
- **Lint** (periodic) → finds contradictions between pages, stale claims superseded by newer sources, orphan pages with no inbound links, and missing cross-references.

### 5.2 Scope: org vs. per-user (selectable)

The owner's explicit requirement - *"a wiki layer by default, for an org and per user, to be selected."* This maps onto a trust boundary:

- **Per-user wiki** - lives on the user's node, private, encrypted at rest, **never leaves unless the user opts in.** The user's second brain.
- **Org wiki** - consolidated in the private cloud, built **only from contributions that pass policy.** The shared org brain.
- **Team wikis** (optional middle tier) - scoped to a group.

**Scope is selectable at query time and at file time** (`personal / team / org`). When a user asks a question, they choose which wikis to read. When a good answer is produced, they choose where to file it.

**Default: personal-first, promote to org on review.** Promotion (`personal → team → org`) is an explicit, policy-gated, auditable merge - like opening a pull request - not an automatic copy. This is where encryption + access control + the audit log do their work. Safer privacy default, and it matches how a wiki naturally grows.

### 5.3 Why this is immune to the hard problems

The wiki is plain markdown. It does not care which base model is loaded, what an adapter's rank is, or whether a node is three versions behind. It survives model upgrades untouched. That is precisely why it is the spine and federated LoRA is not.

---

## 6. Components

### 6.1 Node Agent
A long-running daemon on each participating machine.
- **Local inference** behind one `InferenceBackend` interface: **Ollama** (laptops/Macs/dev) and **vLLM** (GPU servers/prod, ~6-16× throughput at concurrency). Interchangeable; the fleet can mix them.
- **Per-user wiki** (§5) - the local knowledge base, private by default.
- **Local semantic cache** - embeds the prompt with **BGE-M3 (1024-dim dense)** and checks a local LanceDB cache for a sufficiently-similar prior answer (cosine ≥ τ, default 0.93) **plus a guard step** (§7.3). Hit → return; miss → generate, then write `(prompt, embedding, response, citations)`. Eviction by LRU + age.
- **Local vector store (RAG)** - per-node encrypted document store (LanceDB) for retrieval that doesn't warrant a wiki page.
- **Proactive maintenance agent** (§6.3).
- **Capability registry** - reports GPU/VRAM, supported base models, loaded adapters, load, max context, quantization, TEE availability, bandwidth class.
- **Local LoRA trainer** *(Phase 4, optional)* - PEFT fine-tuning on opted-in data, DP-SGD-wrapped, runs only when idle/plugged in.

### 6.2 Orchestrator
Single logical service (HA-replicable later), in the org's private cloud.
- **Node registry** - authoritative node list + health (5s heartbeat, 3-strike timeout).
- **Inference router** - picks the best live node for a prompt given constraints (model, context, latency budget, data-residency tag). v1 heuristic: filter by capability → sort by load → tiebreak by p99 latency. Pluggable.
- **Org wiki** - the consolidated knowledge base; receives promoted pages, runs org-level lint.
- **Central semantic index** (policy-gated) - holds `(embedding, response_ref, ttl)`, **never prompt text.** Lets a node ask "has anyone in the org seen something like this?" then fetch the cached answer directly from the producing node over the data plane. (Treat embeddings as **sensitive metadata**, not a privacy guarantee - see §7.2.)
- **Policy & audit** - per-tenant policies (who may promote to org, what may be cached centrally, who may read which wiki/adapter); append-only audit log shipped to external syslog.
- **Federation coordinator + adapter registry** *(Phase 4, optional)* - runs federated LoRA rounds and serves versioned adapters keyed by `(base_model_fingerprint, task_tag, version)`.

### 6.3 Proactive maintenance agent
Inspired by Claude Code **Routines** ([session](https://claude.com/code-with-claude/session/ldn-build-a-proactive-agent-workflow-with-claude-code)). The wiki shouldn't only update when a human pushes a button. On idle (same window training would use), a scheduled agent: drains `inbox/` into the wiki, runs the lint, proposes promotions to the org wiki for review, and flags structurally-similar past tasks so work is reused, not redone. This is what makes the system a teammate rather than a filing cabinet.

### 6.4 User-facing surfaces (post-MVP shape)
- Web chat UI (FastAPI + minimal frontend) talking to the local node agent.
- OpenAI-compatible `/v1/chat/completions` so existing tooling drops in.
- Admin CLI: `anthill nodes`, `anthill wiki`, `anthill rounds`, `anthill adapters`.

---

## 7. Protocols

### 7.1 Transport security
- **mTLS** everywhere on the control plane; org runs an internal CA issuing short-lived (24h) node certs after enrollment.
- **Noise (XX)** layered over mTLS on the data plane between nodes - defense in depth against a compromised TLS terminator.
- **Signed manifests** (Ed25519) for every wiki promotion, adapter, and base-model download; nodes refuse unsigned or wrong-fingerprint artifacts.

### 7.2 Semantic cache lookup
```
user → node
  embed(prompt)=e ; local_cache.search(e, τ=0.93)
   ├─ hit → GUARD check (§7.3) → return cached answer
   └─ miss → if policy allows central: ask_central(e, τ) → maybe peer fetch (mTLS)
             else generate locally → write cache → (optionally) publish (embedding only)
```
**Invariant: the central index only ever sees embeddings, not prompts.** But embeddings are **not** a confidentiality guarantee - embedding-inversion attacks (e.g. vec2text) can reconstruct meaningful text. So embeddings are protected with the same mTLS + access control + at-rest encryption as text. The privacy story rests on access control, not on "embeddings are opaque."

### 7.3 Cache-correctness guard
A 0.93 cosine match can still be the **wrong** answer - *"EU refund policy"* and *"US refund policy"* are near-identical vectors with different correct answers. So a cache hit is not returned blindly: a fast guard step (lightweight check that the cached answer's key entities/constraints match the new prompt, or a cheap model-side confirmation) gates the return. On guard failure → fall through to generate. Cache correctness is treated as a first-class hazard, not a tuning knob.

### 7.4 Wiki promotion (personal → org)
```
user proposes page(s) for promotion
  → policy check (may this user promote? does content violate residency/secrecy tags?)
  → diff is signed + audited
  → reviewer (human or policy-auto for low-risk) approves
  → org-wiki ingest: merge, update org index.md, run org lint, append org log.md
```

### 7.5 Local model training (Phase 4) - **locked design**

Goal: the open-weight model itself improves on the org's real usage (the *weights*,
not only the wiki/cache). Decided structure (**Option C**): a private training server
the org owns, trained on human-approved examples, eval-gated, rolled out to all nodes.

**Topology - central aggregation, never a vendor.**
```
nodes (per user)                    private training server (per org/team)
  collect gold examples  ──mTLS──▶   accumulate gold (encrypted at rest)
  keep inferring locally             every 24h: new gold? ─no─▶ skip (no spend)
        ▲                                          └─yes─▶ LoRA fine-tune (GPU)
        │                                                   eval new vs current
        │                                                   on held-out gold
        └────── pull improved adapter ◀── promote ONLY if better ── register w/ Ollama
```

- **Training backend is admin-selectable per org and per team:** either a **VPC GPU**
  (ephemeral - spun up for the run, then released; most cost/energy efficient) or an
  **on-prem GPU box** (org-owned hardware; max control, zero cloud egress). Same
  pipeline, different `training_backend` setting.
- **Perimeter:** everything stays inside the team/org. A VPC backend runs in the org's
  own cloud tenancy. Nothing reaches a model vendor.
- **What travels:** only **gold-tier** (human-approved 👍 / admin-approved) examples,
  **PII-scrubbed** (§ hybrid.scrub) before they leave a node. Never bronze, never raw
  chat logs, never the wiki body unless separately promoted.
- **Security (enterprise-grade, not military):** mTLS in transit, AES-256 at rest,
  authenticated + access-controlled. Differential privacy / TEE attestation are
  explicitly **out of scope** for this design - they belong to the stricter
  "data never leaves the device" federated variant (deferred, see below).
- **When:** a 24h tick checks whether gold examples accumulated since the last trained
  watermark. If none → no run (no cost, no energy). If yes → one batched run.
- **Safety gate:** the candidate adapter is scored against the current model on a
  held-out slice of gold (`lifecycle.evaluate_models`) - split from the training rows
  by instruction, so the gate never scores examples the candidate trained on. It is
  promoted **only if it wins**; a regression is discarded. With too little gold to hold
  out a trustworthy slice, a **first** model is promoted unvalidated, but a live model
  is never replaced by an un-evaluated candidate (that run is rejected before any GPU
  spend). Protects the org from a bad run.
- **Rollout:** the winning LoRA adapter is converted to GGUF and registered with Ollama
  (`FROM <base>` + `ADAPTER <adapter.gguf>` → `ollama create <org>-model-vN`). Nodes
  hot-swap; because training happened off-device, **user latency is unaffected**.

**Honest scope.** Fine-tuning teaches the model the org's *style, format, and domain
vocabulary* - not durable facts (the wiki/cache owns facts). Training uses **gold only**
to avoid a self-training feedback loop (model collapse). Per-run cost on an ephemeral
VPC GPU is roughly one GPU-hour; on Apple-Silicon on-prem, MLX LoRA fine-tunes an ~8B
model in ~1h within 16 GB unified memory.

**Deployment topology (decided).** Coordination (orchestrator: org wiki, central
index, governance) and training **co-locate on one admin-selected org GPU backend**
(VPC GPU *or* on-prem box). Day-to-day inference runs **locally per user**; the system
**auto-detects weak local inference and offloads to the org GPU backend**. A small team
with capable machines runs **fully local** with **no separate orchestrator** - the
orchestrator appears only to coordinate multiple nodes, share the org wiki, or train.
The GPU backend is a **setup prerequisite** for the offload/training mode. **Cloud
tie-in (decided): automated AWS provisioning** - the VPC backend launches an
**ephemeral EC2 GPU via boto3** per run, trains, and **guarantees teardown** (try/finally
+ a startup reaper for orphaned anthill-tagged instances; an abandoned GPU instance is
expensive). Mandatory guardrails: instance-type/runtime caps, per-instance tags + cost
surfacing, least-privilege IAM (launch/terminate the tagged type only), gold examples
shipped over an encrypted channel into the org's VPC. On-prem box stays contact-us
(not automated). Everything stays inside the org perimeter.

**Deferred (not this design):** fully-federated training where weights never leave the
device (FlexLoRA aggregation of per-node LoRA deltas + DP-SGD + optional TEE). That is
the answer only if the org's bar is "data must never leave an individual machine"; it
trades cost/energy/latency and operational simplicity for that stronger boundary.

---

## 8. Threat model

We assume and defend against:
- **Adversary inside the network** - mTLS + Noise + signed manifests defeat passive eavesdropping and most MITM.
- **Compromised single node** - can poison its own data/inference and (in Phase 4) a training round. Mitigations: signed promotions/deltas, eval-set canary, DP limits memorization, audit log. It **cannot** read other nodes' data or impersonate the orchestrator (mTLS).
- **Compromised orchestrator (insider)** - worst case. Sees embeddings (not prompts), routing metadata, promoted org-wiki pages, aggregated adapters. Mitigations: (Phase 4) TEE-based aggregation so individual deltas aren't admin-visible; append-only audit shipped externally; multi-party approval for sensitive ops (org-wiki bulk export, model promotion, policy change).
- **External attacker breaching the perimeter** - same defenses plus short-lived certs, segmentation, no internet egress from nodes by default.

We do **not** fully defend against: a malicious admin colluding with a TEE break (no 2026 system does); a user with legitimate node access leaking their own data (policy/training territory). And we explicitly do **not** treat embeddings as confidential (§7.2).

---

## 9. Technology choices

| Concern | Choice | Why |
|---|---|---|
| Language | Python 3.11+ | ML + federation + web libs; fastest path to working code. |
| Wiki engine | Plain markdown + git-style diff; model-driven ingest/lint | Inspectable, diffable, version-churn-proof. The spine. |
| Local inference (dev) | **Ollama** | Trivial cross-platform install; great on Macs/laptops. |
| Local inference (prod) | **vLLM** | High throughput, multi-GPU tensor parallel, real concurrency. |
| Base model - demo default | **Gemma 4 26B A4B** or small **Qwen 3.6** (configurable) | Laptop-friendly so the demo runs widely. |
| Base model - prod default | **DeepSeek V4 Pro** (MIT, 1M ctx) | Frontier-competitive, license-clean. Alts: Qwen 3.6 (Apache 2.0), GLM-5 (MIT), Mistral Large 3 (Apache 2.0). |
| Embeddings | **BGE-M3** (1024-dim dense) | Strong recall/latency, multilingual. (Earlier "512-dim" was wrong.) |
| Vector store | **LanceDB** (local), Postgres + pgvector (central index) | On-disk, encrypted at rest, fast; boring central infra. |
| Transport | gRPC + mTLS; **PyNaCl** (libsodium) Noise overlay | Standard, well-audited primitives. |
| Packaging | Docker + docker-compose (demo); PyPI wheel (node agent) | Forkable. |
| Fine-tuning *(P4)* | PEFT (LoRA/QLoRA) + **FlexLoRA** aggregation | ~70× bandwidth vs full weights; handles heterogeneous ranks. |
| Federation *(P4)* | **Flower 1.x** + **Salvia** secure-agg | Mature framework; secure-agg available. *Highest-risk component - treat as research spike.* |
| Privacy *(P4)* | **Opacus** DP-SGD; per-round `(ε,δ)` with cross-round composition tracked | ε must be designed *with* round count - `ε=4/round` is not "conservative" over many rounds. |
| Confidential compute *(P4)* | Intel TDX / AMD SEV-SNP for the aggregation worker | Optional; degrades gracefully. CPU TEEs add modest overhead for small aggregation workloads. |

---

## 10. Roadmap (re-ordered: value-first)

### Phase 0 - Research + architecture - **done**
This document. Tech choices locked. Models, dims, DP framing corrected from v1; wiki layer added as spine; federated LoRA demoted.

### Phase 1 - Wiki + local chat (first usable thing)
- Node agent with Ollama backend; FastAPI `/v1/chat/completions` on localhost.
- **Per-user wiki**: `raw/ inbox/ wiki/ index.md log.md SCHEMA.md`; ingest + answer-as-page + lint, driven by the model.
- Minimal chat UI that reads the wiki and files good answers back.
- Repo scaffold: `pyproject.toml`, `Makefile`, `docker-compose.yml`, AGPL-3.0 `LICENSE`, README, module layout `anthill/{node_agent,orchestrator,wiki,protocol,crypto,common}`.

### Phase 2 - Org wiki + consolidation + cache
- Orchestrator with node registry + **org wiki** + promotion protocol (§7.4) + policy/audit.
- Semantic cache (BGE-M3 + LanceDB) with the **cache-correctness guard** (§7.3).
- mTLS bootstrap with a generated dev CA.
- `docker compose up` → 1 orchestrator + 2 nodes; ask on either node; sometimes a cache/wiki hit, sometimes generate; promote a page personal→org.

### Phase 3 - Routing + central index + proactive agent
- Inference router (load-based) so a weak node can borrow a strong node's GPU.
- Central semantic index (embeddings only) + cross-node fetch over the data plane.
- Proactive maintenance agent on a schedule (drain inbox, lint, propose promotions, flag reusable past tasks).

### Phase 4 - Local model training (locked design, §7.5)
- **Central training on an org-owned server** (VPC GPU *or* on-prem box, admin-selectable
  per org/team). Gold-only, PII-scrubbed examples; 24h gold-delta trigger; eval-gated
  promotion; Ollama adapter rollout. Enterprise-grade security (mTLS + AES-256), data
  stays in the org perimeter.
- Build order: (a) settings backend-choice + 24h gold-delta scheduler [done first,
  hardware-free], (b) trainer execution (MLX on-prem / remote GPU runner) + eval-gate +
  adapter registration.
- Fully-federated deltas-only variant (FlexLoRA + DP-SGD + TEE) remains deferred - only
  for the "data never leaves the device" bar.

### Phase 5 - Demo + GitHub polish
- One-command `make demo` of the full v1 (wiki → cache hit → routed inference → proactive consolidation → org promotion).
- `docs/walkthrough.md`, CONTRIBUTING, CI (tests + lint), tag v0.1.0.

### Beyond v0.1
TEE-attested aggregation; HA orchestrator; web admin console; data-residency policy DSL; cross-org federation (the "everybody connected" mode - much larger security surface); feedback-driven training-data curation.

---

## 11. Open questions (parked)
1. **Wiki conflict resolution** when two users promote contradictory pages - last-write, reviewer-decides, or keep-both-with-provenance?
2. **Cache guard cost** - how cheap can the §7.3 guard be while still catching the "EU vs US" class of error?
3. **Promotion automation** - which categories of page are safe to auto-promote vs. always human-reviewed?
4. **Stale base model on a node** - catch-up path when a node is offline through a base-model migration.
5. *(P4)* Adapter routing, catastrophic-forgetting checks, DP ε/round-count budgeting, voluntary vs. mandatory participation.

---

## 12. References

**LLM-maintained wiki / knowledge layer**
- [Karpathy - LLM Wiki (gist, Apr 2026)](https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f)
- [Beyond RAG: the LLM Wiki pattern](https://levelup.gitconnected.com/beyond-rag-how-andrej-karpathys-llm-wiki-pattern-builds-knowledge-that-actually-compounds-31a08528665e)

**Proactive agents**
- [Build a proactive agent workflow with Claude Code (Routines)](https://claude.com/code-with-claude/session/ldn-build-a-proactive-agent-workflow-with-claude-code)

**Open-weight models (2026)**
- [Best Open-Source LLM, May 2026 (Codersera)](https://codersera.com/blog/best-open-source-llm-2026-llama-4-qwen-3-5-deepseek-v4-gemma-4-mistral/)
- [Best Open-Source LLMs 2026 (Hugging Face)](https://huggingface.co/blog/daya-shankar/open-source-llms)

**Federated fine-tuning / secure aggregation**
- [FlexLoRA - Federated Fine-tuning under Heterogeneous Tasks & Resources (arXiv 2402.11505)](https://arxiv.org/abs/2402.11505)
- [FLoRA - Heterogeneous Low-Rank Adaptations (arXiv 2409.05976)](https://arxiv.org/abs/2409.05976)
- [DP-FedLoRA (arXiv 2509.09097)](https://arxiv.org/abs/2509.09097)
- [Salvia - Secure Aggregation in Flower (arXiv 2205.06117)](https://arxiv.org/abs/2205.06117)

**Decentralized inference**
- [Petals (Yandex Research)](https://research.yandex.com/blog/petals-decentralized-inference-and-finetuning-of-large-language-models)
- [Parallax (arXiv 2509.26182)](https://arxiv.org/html/2509.26182v1)

**Inference engines / caching / confidential compute**
- [Ollama vs vLLM (Spheron, 2026)](https://www.spheron.network/blog/ollama-vs-vllm/)
- [GPT Semantic Cache (arXiv 2411.05276)](https://arxiv.org/abs/2411.05276)
- [Confidential LLM Inference across CPU/GPU TEEs (arXiv 2509.18886)](https://arxiv.org/abs/2509.18886)
