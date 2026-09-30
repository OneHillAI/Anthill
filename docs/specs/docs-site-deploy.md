# Spec: public docs site, publishing to docs.anthill.run

Status: implemented
Lane: `pillar:knowledge`
Relates to: `docs/OKGF.md`, `docs/SYSTEM_IMPACT_LOG.md`'s "Public docs site" entry, the anthill.run
publish pipeline (`www/README.md`, `.github/workflows/refresh-model-catalog.yml`).

## 1. Introduction

Anthill has no engineer-facing docs site: no GitHub wiki (none of the org's repos have wiki content),
no `docs.anthill.run` (no CNAME, no Pages deploy, no code reference to it anywhere in the org), and no
generated site at all - just markdown read directly off GitHub. The sibling `OneHillAI/ASDD` project
already solved this with a Docusaurus site (`docs-site/`) deployed straight to GitHub Pages, because
ASDD is public. `OneHillAI/Anthill` is private (pre-launch), so a plain GitHub Pages deploy from this
repo would not be reachable as a public site.

This spec covers reusing ASDD's docs-site approach for Anthill, published via the same
build-here-push-there pattern `anthill.run` itself already uses to get around the same private-repo
constraint.

## 2. Requirements

### R1 - Publishing is an explicit whitelist, not the whole `docs/` folder
- THE SYSTEM SHALL publish only files listed in `docs-site/scripts/collect-docs.js`'s `FILES` array.
- `docs/` ALSO holds internal, "audience: the engineering agent" implementation specs (e.g. `*_PLAN.md`,
  `SETUP_TOPOLOGY_REVISION.md`, `TEAM_TIER_PLAN.md`), ops runbooks (`CI_COST.md`,
  `RUNPOD_LIVE_RUN_RUNBOOK.md`, `asdd-goose-adoption.md`), and `docs/specs/**` (this file included) -
  THE SYSTEM SHALL NOT publish any of these unless a future change explicitly adds them to the
  whitelist.
- WHEN a whitelisted source file is missing at build time, THE SYSTEM SHALL fail the build (not
  silently skip it), so a rename or deletion upstream is caught immediately rather than producing a
  quietly-incomplete site.

### R2 - The deploy workflow never writes to this repo
- THE SYSTEM SHALL run the build-and-publish job with `permissions: contents: read` on this repo.
- THE SYSTEM SHALL push the built static output only to the separate `OneHillAI/docs.anthill.run` repo,
  authenticated with a token scoped to only that repo, never this repo's own `GITHUB_TOKEN`.
- The scoped token SHALL be minted per-run for the onehill-dev-agent bot App (`ANTHILL_BOT_APP_ID` /
  `ANTHILL_BOT_APP_KEY`, the same secrets `asdd-automerge.yml` already uses), constrained to the deploy
  repo via `create-github-app-token`'s `repositories:` input - not a separately-created, manually-rotated
  PAT, so there is one bot credential to manage instead of two.
- Rationale: identical posture to `refresh-model-catalog.yml` - a compromised or buggy docs build can
  at most corrupt the public docs mirror, never this repo's `main`.

### R3 - The site redeploys when its inputs change
- WHEN a push to `main` touches `docs/**`, `docs-site/**`, `README.md`, `ARCHITECTURE.md`, or
  `CONTRIBUTING.md`, THE SYSTEM SHALL rebuild and republish the site.
- THE SYSTEM SHALL also support an on-demand `workflow_dispatch` run, for the one-time bootstrap publish
  and for manual recovery.

### R4 - URL structure matches the requested reference (goose-docs.ai / ASDD)
- THE SYSTEM SHALL route docs under `/docs/...` (not the site root), so a sidebar category without an
  explicit landing doc gets a generated index at `/docs/category/<slug>`, matching
  `https://goose-docs.ai/docs/category/guides`'s structure and ASDD's own docs-site.

## 3. Design

- **`docs-site/`** - a Docusaurus 3 site (`@docusaurus/preset-classic`), configured like
  `OneHillAI/ASDD/docs-site` but with `markdown.format: 'md'` site-wide: several whitelisted source docs
  mix raw HTML into markdown (fine for GitHub's renderer), which MDX's default JSX parser rejects on
  unclosed/self-closing tags. Plain Markdown mode treats that HTML as a passthrough instead.
- **`docs-site/scripts/collect-docs.js`** - a prebuild step (wired via npm's `prebuild` lifecycle hook)
  that copies the whitelisted root files and `docs/*.md` files into `docs-site/docs/` (gitignored,
  regenerated every build). This is the mechanism behind R1: adding a page means adding a line to the
  `FILES` array, not moving or restructuring the source `docs/` folder.
  - `sidebars.js` hand-lists the published docs into categories (Get started, Concepts, Guides,
    Operations, Reference), each with `link: {type: 'generated-index'}` for R4.
  - `src/pages/index.js` redirects the site root to `/docs/` (Docusaurus maps a `README.md` at the docs
    root to the docs plugin's own index, not to `/docs/README`).
- **`.github/workflows/docs-deploy.yml`** - mints a deploy-repo-scoped bot-App token (R2), then pushes
  `docs-site/build/` into a fresh clone of `OneHillAI/docs.anthill.run` (rsync + commit + push to
  `main`), mirroring `refresh-model-catalog.yml`'s clone/credential-helper/push steps (R2, R3).
  Cloudflare Pages, connected to that deploy repo by the owner, deploys on push (one-time owner setup
  documented in `docs-site/README.md`: create the deploy repo, connect Cloudflare Pages, add the
  `docs.anthill.run` custom domain - no deploy token to create, the bot App already covers it).

## 4. Tasks

- [x] `docs-site/` Docusaurus scaffold + `collect-docs.js` whitelist (R1).
- [x] `docs-deploy.yml`, read-only on this repo, pushing only to the deploy repo (R2, R3).
- [x] Sidebar categories with generated indexes + homepage redirect (R4).
- [x] Verified locally: `npm run build` succeeds, `npm run serve` smoke-tested (homepage redirect,
  `/docs/`, a `/docs/category/*` page, and a guide page).
- [x] `changelog.d/+docs-site.added.md` and a `docs/SYSTEM_IMPACT_LOG.md` entry.
- [ ] Owner completes the one-time Cloudflare Pages + DNS setup (`docs-site/README.md`); until then the
  workflow publishes to the deploy repo but nothing serves `docs.anthill.run` yet.

## 5. Out of scope

- **Content curation beyond the initial whitelist** - deciding whether more `docs/*.md` files should
  become public is an editorial call for a later change, not this one.
- **A public GitHub repo for `OneHillAI/Anthill` itself** - if/when that happens, the simpler ASDD-style
  direct-to-Pages deploy becomes possible; this spec's build-and-push-to-a-deploy-repo approach is the
  private-repo workaround, not the end state.
- **Editing the whitelisted source docs' content** - this change only wires up publishing; it does not
  rewrite any of the published pages.
