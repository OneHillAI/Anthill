# Knowledge guidance reconciliation

Status: **shipped**. Lane: `pillar:knowledge`. Re-verified against the real code (2026-08-06), not
assumed from this document's own prior status header, which had gone stale: requirement 1 (hub-level
self-explanation) is live in `anthill/web/templates/_knowledge_tabs.html` (`#know-intro`, one card, with
a light one-line hint per tab) and `anthill/web/static/walkthrough.js` (every per-surface walkthrough
registers `{autostart:false}` - `skills.html`, `wiki.html`, `snippets.html`, `memory.html` all confirmed).
Requirement 2 (a unified suggestions inbox) is live at `GET /knowledge/suggestions`
(`anthill/web/app.py`'s `knowledge_suggestions()` / `_suggestions_for()`), aggregating `WikiReview`
(pages, principles, and promotions) and pending `ProposedSkill` rows behind the existing
approve/reject/adjust routes - no new approval engine, matching the requirement's own constraint.
Requirement 4 (first-run knowledge guidance decoupled from a local download, and a download-size line
that matches the chosen model) is confirmed still OPEN: `dashboard.html`'s "Preparing your local AI"
banner (the guidance's actual home) remains gated entirely on `OrgSettings.local_model_pulling`, so a
Frontier/cloud-first account still never sees it, and its download-size text is still the hard-coded
`'a one-time ~2 GB download'` regardless of the model actually downloading. A cloud-first account does
get SOME first-run nudge via the unrelated activation checklist's "Set up your compute" step, but not
the wiki/skills guidance content requirement 4 describes. Tracked as a separate follow-up, not fixed
here. Requirement 3 (framing) was minor/prose and is superseded by the later baseline-vs-advanced work
below.

Reconciles the guidance LAYER of `docs/specs/knowledge-onboarding-and-guidance.md` (#683, fully shipped)
with the 2026-08 IA redesign. It does not touch the engine underneath: the skills template gallery +
guided authoring wizard, the ingestion work (MarkItDown converters, connector→ingest pipeline, the
download-wait guided step), the knowledge-change audit + digest, and the markdown-canonical + DB-ledger
storage all stay exactly as shipped. Only the top-level UX framing changes.

Relates to:
- `docs/specs/knowledge-onboarding-and-guidance.md` (#683, shipped) - this spec supersedes its guidance
  FRAMING (requirements 1 and 2 there) and leaves the rest (3-6) in force.
- `docs/specs/declutter-onboarding-and-org-settings.md` (shipped) - the IA redesign that unified the four
  knowledge surfaces into one "Knowledge" hub and introduced the Dashboard "Living wiki / Model council"
  cores. This spec re-points the guidance at that IA.

## Problem

The #683 knowledge-guidance framing predates the IA redesign, and two of its choices are now wrong for the
product as it presents itself:

1. **Per-surface self-explanation fragments a unified thing.** #683 shipped a separate interactive
   walkthrough per surface (Wiki, Memory, Snippets, Skills each auto-start their own tour). But the IA has
   since folded those four into one "Knowledge" hub, and the dashboard presents knowledge as a single
   "Living wiki" core. Four coequal tours of four separate destinations now teach a structure the product
   no longer has.
2. **Capture-first is the wrong primary motion.** #683's centerpiece was user-initiated capture (the
   scissors gesture: save-to-wiki, turn-into-skill). The new framing - "it suggests elements you can
   adjust", Model council + Living wiki as things you own and curate - inverts the emphasis: the model
   suggests, the user adjusts. Capture is a secondary path, not the headline. And #683 left the model's
   suggestions scattered across `/wiki/review`, `ProposedSkill`, and snippet/memory promotion, with no one
   place to see and adjust them. That unified inbox is the actual missing piece.
3. **Minor: the concept count undershot.** #683 aimed to "collapse four concepts toward three"
   (snippets-as-wiki); the IA went further, to one Knowledge hub / one Living-wiki core. The
   snippets-as-wiki reframe stays consistent - it is just no longer the headline.

## Requirements

### 1. Hub-level self-explanation, light per-tab hints

- THE SYSTEM SHALL present ONE hub-level self-explanation of knowledge ("this is your Living wiki; your AI
  grows it as you work, and you steer and approve") as the primary guidance, in place of four coequal
  auto-starting per-surface tours.
- Each Knowledge tab (Wiki, Snippets, Memory, Skills) SHALL carry a LIGHT one-line hint of what that tab
  is, rather than its own full auto-starting tour.
- The existing `anthillWalkthrough` mechanism and per-surface walkthrough CONTENT SHALL be preserved and
  remain replayable on demand ("Take a tour"); only the auto-start behaviour is re-pointed so the hub-level
  explanation leads and the four per-surface tours no longer each fire on first visit.
- The existing capture flows (snippet→wiki bridge, "turn a chat into a skill") SHALL remain intact and
  reachable; they are demoted in emphasis, not removed.

### 2. A unified "suggestions to review & adjust" inbox

- THE SYSTEM SHALL provide ONE surface that gathers everything the user's AI has suggested for their
  knowledge and lets them adjust it: proposed wiki pages (the existing `WikiReview` queue - research
  drafts, upload/connector summaries awaiting review), distilled/proposed skills (the existing
  `ProposedSkill`), and pending personal→team/org promotions.
- Each item SHALL show what the AI suggests and offer, at minimum, adjust-and-approve and dismiss, routing
  through the EXISTING review/approve/reject mechanisms (no new approval engine).
- The inbox SHALL read from existing data sources only; it is an aggregation + presentation surface, not a
  new store. It SHALL respect the existing scope/role gates (a member sees their own personal suggestions;
  org-scope review stays admin-gated exactly as today).
- The suggestions surface SHALL be discoverable from the Knowledge hub, and its count SHALL be surfaceable
  on the dashboard (the "Living wiki" core / activation), so "your AI suggested things - adjust them" is a
  visible primary motion, not scattered.

### 3. Framing (minor)

- Knowledge SHALL be presented as one Living wiki (with Skills as reusable procedures and Memory as the
  automatic recall layer); the snippets-as-wiki reframe stays but is not the headline.

### 4. First-run knowledge guidance for every account, not only local downloads

Surfaced by the model-catalog work (a Frontier/cloud model as the first choice widened a pre-existing
seam):

- THE SYSTEM SHALL fire the first-run knowledge guidance ("build your wiki / browse skill templates") on
  first run generally, decoupled from `local_model_pulling`. Today that guidance (and the auto-queue of a
  first upload) only appears while a LOCAL model is downloading, so a Frontier/cloud-first account - which
  has no local download - skips the guided knowledge step entirely and sees only the always-present "Add
  knowledge" button. The upload-QUEUE mechanic stays local-download-specific (a cloud model is ready
  immediately, nothing to queue); it is the GUIDANCE that must reach everyone.
- The local-model download banner's stated size SHALL be derived from the chosen model rather than a
  hard-coded "~2 GB", which now understates larger local options (a 30B Nano is ~17 GB at 4-bit, and the
  CPU-offload tier admits bigger MoEs). If a precise size is unavailable, the copy SHALL avoid quoting a
  specific number rather than quote a wrong one.

## Out of scope (stays exactly as #683 shipped it)

- The skills template gallery + guided authoring wizard, local validation, and "does it trigger?" check.
- Ingestion: MarkItDown converters, connector→ingest pipeline, the first-run download-wait guided step +
  upload queue.
- Knowledge-change audit + the periodic digest.
- Storage: markdown canonical on disk + the thin DB registry/ledger.
- Any change to the memory/skill/wiki ENGINES; this spec is guidance-layer presentation only.

## Acceptance criteria

- A new user on the Knowledge hub sees one hub-level explanation of their Living wiki, not four separate
  auto-starting tours; each tab still carries a light hint, and any per-surface walkthrough is still
  replayable on demand.
- There is a single suggestions surface listing proposed wiki pages, proposed skills, and pending
  promotions together; adjusting-and-approving an item routes through the existing review path and removes
  it from the inbox; dismissing routes through the existing reject path.
- A non-admin member sees only the suggestions they are entitled to act on; org-scope review stays
  admin-gated as today.
- The existing per-surface walkthrough content, the snippet→wiki bridge, and "turn a chat into a skill"
  still work unchanged.
- A Frontier/cloud-first account (no local download) still sees the first-run knowledge guidance on the
  dashboard, not only the bare "Add knowledge" button; the download-size line, when shown, matches the
  chosen model instead of a hard-coded "~2 GB".
- No change to the skills gallery/wizard, ingestion, audit/digest, or storage ledger.
