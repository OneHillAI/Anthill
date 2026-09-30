# Spec: interaction service, Stage 1 (members-first)

Status: proposed
Lane: `pillar:feature`
Implements: OneHillAI/ASDD PR #45 build brief (D2, the Discord/Slack interaction service, framework to
Anthill), Stage 1.
Relates to: [`anthill/contribute/interaction.py`](../../anthill/contribute/interaction.py),
[`anthill/web/slack_bot.py`](../../anthill/web/slack_bot.py),
[`anthill/contribute/intake.py`](../../anthill/contribute/intake.py),
`agents/interaction.md` (the ASDD interaction-agent contract).

## 1. Introduction

The ASDD interaction agent answers from a project's own knowledge and routes ideas into the governed
contribution intake. It was proven in the dogfood (answered from knowledge with citations, drafted a spec
that passed the definition-of-ready gate, refused an injection). This runs that contract as a standing
service on Anthill's existing chat surface.

Stage 1 is **members-first**: the surface is the Slack bot, which already gates to org members by email,
so the identity is trusted. A trusted member may reach the org model and wiki, so a tool-using answer path
is fine here. (Untrusted public surfaces are Stage 2 and must run execution-free or sandboxed, per the B5
security rule; not in this spec.)

The Slack bot already answers a member's question from the org wiki. The new behaviour is: when a message
is an **idea, bug, or request**, route it into the governed intake as a spec object instead of answering,
and disclose on every reply that the assistant is automated.

## 2. Requirements

- **R1** WHEN a member's message is a feature idea, bug, or request, THE SYSTEM SHALL draft it into a spec
  object via the existing intake (`distil_proposal` / `_make_contribution`, `source="slack"`) and store it
  as a `ContributionProposal` for human review, rather than answering it.
- **R2** WHEN a member's message is a question, THE SYSTEM SHALL answer it from the org wiki as before.
- **R3** THE reply SHALL disclose on every message that the assistant is automated and under human
  direction.
- **R4** THE reply to a routed idea SHALL state whether it is ready for review (the definition-of-ready
  verdict, the proposal's completeness) and SHALL make clear nothing is built without a human approving it.
- **R5** THE idea vs question classifier SHALL be conservative: an ambiguous or clearly-question message
  is answered, never mis-routed into intake.
- **R6** THE routing SHALL be members-only (the Slack bot's existing email-to-member gate is the trust
  boundary); the idea is untrusted DATA to the intake agent, never instructions.

## 3. Design

- `anthill/contribute/interaction.py` holds the pure, channel-agnostic pieces: `looks_like_idea`, the
  `DISCLOSURE` line, `intake_reply`, and `answer_reply`. No web or db imports, so it is unit-testable and
  reused by every surface.
- `_slack_answer` (in `anthill/web/app.py`), after mapping the Slack user to a member, classifies the
  message. An idea goes to `_make_contribution(..., source="slack")` and the reply is `intake_reply`; a
  question goes through the existing wiki answer path, wrapped in `answer_reply`. Both disclose.
- The intake write reuses the one channel-agnostic path (`_make_contribution`), so the untrusted-data
  guardrails, the disclosure that the spec is agent-drafted, and the audit entry are unchanged.

## 4. Tasks

- [ ] `anthill/contribute/interaction.py`: the pure core (R3, R4, R5).
- [ ] `anthill/web/app.py` `_slack_answer`: classify and route (R1, R2, R6); disclose (R3).
- [ ] `tests/test_interaction_service.py` + `tests/test_slack_bot.py`: unit + integration (R1-R5).
- [ ] `CHANGELOG.md`: the user-facing entry.

## 5. Out of scope

- **Stage 1b: Discord inbound.** The shipped Discord piece is an outbound connector (MCP), not an inbound
  event receiver. Discord inbound needs an interactions webhook (with Ed25519 verification) or a gateway;
  it is a focused fast-follow. The core in `interaction.py` is platform-agnostic and ready for it.
- **Stage 2: public / anonymous surfaces.** Anti-bot gated and execution-free per B5; waits on the
  framework's execution-free recipe variant and anti-bot contract.
- **A model-based intent classifier.** Stage 1 uses a deterministic marker classifier; a model classifier
  is a later refinement.
