# anthill.run (hosting surface)

This directory is the static site + services surface served at **https://anthill.run** via Cloudflare
Pages. It is deliberately small for now: a placeholder landing page and the model catalog that Anthill's
"Refresh models" button pulls. The real marketing/download page is a follow-up.

## What is here

```
www/
  index.html            placeholder landing page
  _headers              Cloudflare Pages response headers (content-type + short cache for the catalog)
  model-catalog.json    the published frontier catalog (see below); generated, do not hand-edit
  functions/
    api/health.js       example service -> https://anthill.run/api/health
    README.md           how to add a service
```

Two URL shapes are available going forward:

- **Path services**: a file at `www/functions/api/<name>.js` serves at `anthill.run/api/<name>`.
- **Subdomains** (`<name>.anthill.run`): added later at the Cloudflare/DNS level, outside this directory.

## The model catalog

`model-catalog.json` is the live list Anthill fetches. Anthill's refresh route
(`POST /models/refresh-catalog`) defaults to `https://anthill.run/model-catalog.json`
(`ANTHILL_MODEL_CATALOG_URL`), validates it, and writes a local override so the picker keeps up with the
open frontier without an app release. The fetch is a server-side `httpx.get` (not browser JavaScript), so
CORS is not required; the file just needs to serve `200` with `application/json` (a permissive
`Access-Control-Allow-Origin: *` is set anyway, since the data is public and non-sensitive).

**Do not hand-edit `model-catalog.json`.** It is produced by `scripts/gen_model_catalog.py` from the vetted
seed at `anthill/hosting/model_catalog.json` and refreshed daily by
`.github/workflows/refresh-model-catalog.yml`. To change which models appear, edit the seed and open a PR;
the next daily run republishes. See [scripts/gen_model_catalog.py](../scripts/gen_model_catalog.py) for the
sources and the maintenance workflow.

## How publishing works (a dedicated deploy repo)

Cloudflare's GitHub App **cannot see this private monorepo**, so Cloudflare Pages cannot deploy from
`OneHillAI/Anthill` directly. Instead a small dedicated repo, **`OneHillAI/anthill.run`**, is the publish
target that Cloudflare *can* be connected to:

```
Anthill CI (this repo)                         OneHillAI/anthill.run          Cloudflare Pages
  gen_model_catalog.py  --> www/model-catalog.json
  sigstore sign         --> www/...sigstore.json
  rsync www/ + git push  ----------------------> repo root (main) ----------> deploys on push --> anthill.run
```

The refresh workflow **signs in this repo** and pushes only the built site to `anthill.run`. It never
writes to this repo (`contents: read`), so `main` stays fully protected. Cloudflare deploys on every push
to the deploy repo's `main`.

## The signature (unchanged by the deploy repo)

The catalog is **signed** and Anthill **refuses an unsigned or unexpected one** (see
`docs/specs/model-catalog-trust.md`). The signature is a detached Sigstore bundle published beside the
catalog as `model-catalog.json.sigstore.json`. Only the workflow can produce it (keyless signing: its
GitHub identity is the signer), pinned to **`refresh-model-catalog.yml@refs/heads/main` in this repo**.
Signing stays here even though the artifact is served from `anthill.run` - the deploy repo is a dumb
publish target and never signs - so **the identity the client verifies did not change**.

## One-time setup (owner)

Some steps are account-level and this repo cannot perform them. The deploy token is the only new secret.

1. **Deploy token** (so the CI can push to the deploy repo). Create a **fine-grained PAT** scoped to
   **only `OneHillAI/anthill.run`** with **Contents: Read and write** (Settings -> Developer settings ->
   Fine-grained tokens; an org repo may need org approval). Add it to **this** repo as the secret
   **`ANTHILL_RUN_DEPLOY_TOKEN`** (Settings -> Secrets -> Actions). A repo **deploy key** with write access
   works too if you prefer no user-scoped token.
2. **No model API key is needed.** Scores are curated editorial values in the seed. (The generator has an
   optional Artificial Analysis integration behind `AA_API_KEY`, off by default: AA's free Data API forbids
   redistribution and this catalog is public. Only set it with a redistribution-permitted licence.)
3. **Bootstrap**: run the **Refresh model catalog** workflow once with **`force_publish = true`** (Actions
   -> Refresh model catalog -> Run workflow). It signs the catalog and makes the first commit to the
   `anthill.run` repo. (Without `force_publish`, an unchanged catalog is skipped, so the empty repo would
   only populate once the seed next changes.)
4. **Connect Cloudflare Pages to the deploy repo.** In Cloudflare Pages -> Create -> **Connect to Git**,
   pick **`OneHillAI/anthill.run`**. When GitHub asks, **grant the Cloudflare Pages GitHub App access to
   that repo** (it is private; the app must be authorized on it - this is the connection that was failing
   when pointed at the monorepo). Set **Production branch = `main`**, **Build command = (none)**,
   **Build output directory = `/`** (the repo root is already the static site).
5. **Custom domain + DNS.** In the Pages project add **`anthill.run`** and **`www.anthill.run`** as custom
   domains. Cloudflare rewrites the apex/`www` records to the Pages project. **Delete the leftover GoDaddy
   record** first if present: today `anthill.run` still resolves to a GoDaddy "coming soon" page, which is
   why it 404s the catalog.

Once live, verify both files serve: `curl -sI https://anthill.run/model-catalog.json` and
`.../model-catalog.json.sigstore.json` each return `200` (the catalog as `application/json`), then click
**Refresh models** in Anthill - it should now succeed rather than report "not signed".
