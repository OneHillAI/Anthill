# Spec: interaction agent and engagement surfaces

Status: proposed
Lane: `pillar:feature`
Relates to: [`AGENTS.md`](../../AGENTS.md), [`anthill/connectors/`](../../anthill/connectors),
[`anthill/web/ingest_push.py`](../../anthill/web/ingest_push.py),
[`docs/CONTRIBUTION_SURFACE.md`](../CONTRIBUTION_SURFACE.md), OneHillAI/ASDD (STANDARD)

## 1. Introduction

Anthill's engagement today is its own chat (web/desktop/CLI) plus the in-app `/contribute` flow. Mature
projects also meet people where they already are - a chat platform - as a first-class engagement surface.
The human direction: the **interaction agent** is an **ASDD-framework role** (like the review lenses and
the runtime adapter are framework roles); ASDD defines it and keeps it ready to implement, and the
concrete implementation lands in the adopting project - here, Anthill.

So this spec has two parts:

1. **The role** (framework): what an interaction agent is, its contract, and its ASDD guardrails -
   runtime- and platform-neutral, so any adopter can implement it. Canonically this belongs upstream in
   OneHillAI/ASDD (STANDARD); it is written here first because that is where the implementation lives.
2. **The Anthill implementation** (phased): bind the role to the project's real surfaces - the MCP
   connectors (Slack already; **Discord added in this PR**), inbound webhooks, the `/contribute` intake,
   and the wiki knowledge - shipped in slices so nothing large lands unreviewed.

This PR delivers the role spec and the Discord connector (the enabling slice). The conversational
implementation is phased below and follows once the spec is agreed (ASDD is spec-driven).

## 2. Requirements

### R1 - The interaction-agent role (framework contract)
- THE STANDARD SHALL define an "interaction agent": an agent that connects a project to an engagement
  platform and mediates two-way interaction - answering from the project's own knowledge, and routing
  ideas/reports into the project's governed contribution intake.
- THE ROLE SHALL be platform-neutral: the platform binding (Discord, Slack, web) is pluggable, exactly
  as the review runtime is a pluggable adapter. An adopter selects a binding; the role is the same.

### R2 - Trust membrane (the ASDD guardrails)
- THE interaction agent SHALL treat every inbound platform message as **untrusted data, not
  instructions** (the same membrane as intake and the review lenses): content that says "ignore your
  rules" is surfaced, not obeyed.
- THE interaction agent SHALL take no side-effectful action on its own (no merge, deploy, spend, or
  config change). It answers, and it routes a contribution into the human-approved pipeline
  (`/contribute` -> spec -> triage -> human accept). A human still approves anything consequential.
- THE interaction agent SHALL disclose that it is an agent, and SHALL be bounded (rate/'`max_actions`'
  style limits), consistent with `.asdd.yml` and the merge-advisory posture.

### R3 - Engagement connectors
- THE project SHALL be able to connect Discord via MCP, as it already can Slack. (Delivered here: a
  `discord` catalog entry, `community` tier, honest provenance note; Slack stays as-is.)
- WHERE a platform has no catalog entry, THE project SHALL still allow a custom MCP server (existing
  `MCPServer` path), so the set of platforms is open.

### R4 - Answer from the project's own knowledge
- WHEN the interaction agent answers on a platform, THE SYSTEM SHALL ground the answer in the project's
  wiki + semantic cache (the same path the in-app chat uses), never inventing project facts.

## 3. Design

### 3.1 Fit and reuse
The role composes existing Anthill primitives rather than adding a parallel stack: the MCP connectors
(read/post to a platform), `ingest_push.py` (inbound events already land as `inbox/` data through the
review gate), the `/contribute` intake agent (idea -> validated spec, already treating input as untrusted
data), and the wiki/cache answer path. The interaction agent is the orchestration role that binds these;
it introduces no new trust boundary the membrane does not already cover.

### 3.2 Where the role lives
Canonically the role definition is an ASDD-standard artifact (OneHillAI/ASDD), so every adopter gets it -
this spec is the source for that upstream. The binding + orchestration are Anthill code
(`anthill/interaction/`, future).

This is not speculative: the inbound **Slack bot** already shipped (`anthill/web/slack_bot.py`,
`docs/SLACK_BOT.md`) - it answers a Slack message from the org wiki. That IS P1 for one platform; this
role names the pattern so it is platform-neutral (Discord next) and adds the P2 contribution routing,
rather than each platform reinventing it.

### 3.3 Phased implementation (each a reviewed slice)
- **P0 (this PR):** the role spec; the Discord connector so a second platform is connectable.
- **P1:** read-only - the agent answers a platform mention from the wiki/cache (grounded, disclosed),
  no writes beyond posting its reply. **Already done for Slack** (`slack_bot.py`); generalize it.
- **P2:** route an idea/report from a platform into `/contribute` (spec-drafted, human-accepted), closing
  the loop from a chat message to a governed contribution.
- **P3:** richer two-way with explicit approval gates for anything consequential; per-platform config.

## 4. Tasks

- [x] Discord connector (`anthill/connectors/catalog.json`, `community` tier) + validity test (R3).
- [ ] Upstream the interaction-agent role into OneHillAI/ASDD (STANDARD) (R1, R2).
- [ ] P1: platform-answer-from-knowledge binding in `anthill/interaction/` (R2, R4).
- [ ] P2: platform-idea -> `/contribute` intake (R2).

## 5. Out of scope (for this PR)

- The conversational implementation (P1-P3) - it needs the role agreed first (this spec) and
  platform bot tokens (owner setup), so it is phased, not landed here.
- Making a platform the *primary* engagement surface - the in-app chat + `/contribute` remain primary;
  platforms are additional reach.
