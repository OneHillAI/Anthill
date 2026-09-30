# Roadmap

What's shipped, what's in progress, and what's planned. This is a direction, not a
promise - priorities shift with feedback. For the deep design behind any item, see
[`ARCHITECTURE.md`](ARCHITECTURE.md).

## Shipped

- **Local-first node** - inference (Ollama / open-weight models), a self-maintaining
  wiki, and a semantic cache, all on the user's own machine.
- **Memory** - distils durable facts from everyday chats and tasks and recalls them
  into future answers.
- **Chat** - answered from the wiki + cache, with per-message **web search** and
  **agent mode** toggles.
- **Snippets** - mark any answer worth keeping; it becomes gold knowledge (org-wide
  once corroborated).
- **Agents, tasks & skills** - a ReAct executor acting under scoped, audited
  identities; scheduled and event-driven tasks; reusable Skills (`SKILL.md`) loaded
  on demand.
- **File creation** - generate PDF, DOCX, PPTX, XLSX, HTML and charts, with inline
  preview and a Canvas pane.
- **Connectors** - Slack, Notion, Jira, Google Drive, Microsoft 365, configured in
  the dashboard with credentials encrypted at rest.
- **Hybrid cloud fallback** - optional, off by default: escalate only a weak answer
  to a paid model, question-only and PII-scrubbed.
- **Metrics dashboard** - a value number for each of the project's value
  propositions (resilience, cost, latency, energy, sovereignty, ownership, offline).
- **Training-data pipeline** - collects human-approved gold answers and exports JSONL
  for fine-tuning.
- **No-Terminal Mac app** - one-click installer, `Anthill.app`, drag-to-Applications
  `.dmg`, optional autostart at login.
- **Web dashboard** - auth (bcrypt + JWT, AES-256-GCM at rest), users & invites,
  roles, audit trail, model management, settings.
- **In-app help bot**, **speech-to-text input**, per-task model routing, and
  in-session context compaction.

## In progress

- **AWS VPC GPU backend** - credential capture + validation today; automated
  ephemeral-GPU provisioning (launch → train → guaranteed teardown) next.
- **Training run** - executing fine-tuning on the gold set end-to-end.
- **Progressive web app** + push notifications.

## Planned

- **Multi-machine networking** - teammates' nodes connecting to a shared org backend
  over the network/VPN; per-user installs that auto-connect via an invite.
- **Mobile app.**
- **Microsoft SSO.**
- **Federated training** across nodes.

## Non-negotiables

These hold across every item above:

- **Everything stays inside the org perimeter** unless an explicit, policy-gated
  action promotes it.
- **Offline-capable core** - every internet feature is an opt-in extra whose absence
  disables only itself.
- **Cloud GPU safety** - guaranteed teardown, cost guardrails, least-privilege IAM.
