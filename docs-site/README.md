# Anthill docs site (docs-site/)

A [Docusaurus](https://docusaurus.io/) site served at **https://docs.anthill.run**, built the same way
as [OneHillAI/ASDD's docs-site](https://github.com/OneHillAI/ASDD/tree/main/docs-site) (live at
https://onehillai.github.io/ASDD/).

## Why this isn't a plain GitHub Pages deploy like ASDD's

ASDD is a public repo, so its `docs-deploy.yml` builds and pushes straight to GitHub Pages via
`actions/deploy-pages`. `OneHillAI/Anthill` is still private (pre-launch), and a private repo's Pages
site isn't reachable as a public site. So this follows the pattern already proven for **anthill.run**
itself (see `../www/README.md`): build here, push only the built static output to a small dedicated
public repo, and let Cloudflare Pages deploy from there.

```
Anthill CI (this repo, private)              OneHillAI/docs.anthill.run        Cloudflare Pages
  collect-docs.js -> docs-site/docs/
  docusaurus build -> docs-site/build/
  push build/ -> repo root (main)   -------------------------------------->  deploys on push
                                                                              --> docs.anthill.run
```

`.github/workflows/docs-deploy.yml` does the build and push; it never writes to this repo
(`contents: read`), so `main` stays fully protected.

## What's published (a whitelist, not the whole docs/ folder)

`scripts/collect-docs.js` copies an **explicit whitelist** of files into `docs-site/docs/`
(gitignored, regenerated on every build) before Docusaurus runs. `docs/` in the main repo also holds
internal, "audience: the engineering agent" implementation specs, ops runbooks, and `docs/specs/**`
(issue-numbered engineering specs) - none of that is picked up. **To publish a new page, add it to the
`FILES` list in that script**; nothing reaches the public site by just adding a file under `docs/`.

## One-time setup (owner)

Unlike anthill.run's `refresh-model-catalog.yml`, this workflow does **not** need its own deploy PAT:
it mints a short-lived token for the onehill-dev-agent GitHub App (the same `ANTHILL_BOT_APP_ID` /
`ANTHILL_BOT_APP_KEY` secrets `asdd-automerge.yml` already uses), scoped to only the deploy repo via
`create-github-app-token`'s `repositories:` input. Since that App is installed org-wide, a newly
created repo is already covered - no separate token to create or rotate.

1. **Create the deploy repo**: an empty **`OneHillAI/docs.anthill.run`** repo (private is fine -
   Cloudflare Pages can build a private repo it's connected to; only the *deployed* Pages output is
   public). If the bot App is ever switched from "all repositories" to "only select repositories",
   add this repo to its installation - otherwise nothing to do here.
2. **Bootstrap**: run the **Deploy docs** workflow once via `workflow_dispatch` to make the first
   commit to the deploy repo.
3. **Connect Cloudflare Pages** to `OneHillAI/docs.anthill.run` (Create -> Connect to Git; grant the
   Cloudflare Pages GitHub App access to that repo since it's private). Production branch `main`,
   build command none, build output directory `/` (the repo root is already the built site).
4. **Custom domain + DNS**: add `docs.anthill.run` as a custom domain in the Pages project; Cloudflare
   handles the DNS record.

Once live, `https://docs.anthill.run` should serve this site, and pushes to `main` that touch
`docs/**` or `docs-site/**` redeploy it automatically.

## Local development

```
cd docs-site
npm ci
npm start   # collects docs, then serves at localhost:3000 with live reload
```
