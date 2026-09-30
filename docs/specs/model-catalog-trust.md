# Model catalog: trust boundary

## Why this exists

The model catalog is refreshable over the network (`POST /models/refresh-catalog` pulls
`ANTHILL_MODEL_CATALOG_URL`, default `https://anthill.run/model-catalog.json`). It is easy to read it as
metadata - names, sizes, scores. It is not. Two of its fields are **instructions this box executes**:

| field | consumed by | if an attacker controls it |
|---|---|---|
| `ollama_tag` | `/models/pull` -> `ollama pull <tag>` | the org runs the attacker's **weights**: a backdoored model answering every question, with the org's wiki as context |
| `hf_id`, `quant_hf_id` | `source.servable_id` -> `MODEL_NAME` -> RunPod **vLLM** worker | vLLM fetches and **loads** an arbitrary HF repo on the org's GPU. A repo can carry pickled weights, so this is **code execution**, not merely a bad model |

One origin serves every install, so tampering with it is a supply-chain attack on all clients at once. It
is made worse by the ranking: entries carry `intelligence`, ranking is intelligence x fit, so a poisoned
row with a high score is **preselected as "recommended"** - the UI vouches for the attacker's model, and
the admin only has to click.

## The boundary

**A tag an admin types into the `/models` "Advanced" field is a human decision - trusted, unrestricted.
A row that arrived over the wire is untrusted input.** Everything below applies only to the latter.

## Controls (this change)

1. **Channel.** The URL must be `https://`. Redirects are **not followed** - a redirect at the origin would
   silently source the catalog from another host, defeating the point of pinning one. The body is capped
   (`_CATALOG_MAX_BYTES`).
2. **Identity shape.** `is_safe_ollama_tag` accepts only a plain library ref (`name[:version]` or
   `namespace/name[:version]`) and rejects anything that could redirect the pull at a host the publisher
   does not control: a scheme, a registry/host segment (`hf.co/user/repo`, `evil.com/model`), traversal,
   whitespace. `is_safe_hf_id` accepts only `org/repo`.
3. **Fail closed, wholesale.** A source containing ANY unsafe row is rejected **entirely** - not
   row-filtered. A tampered catalog is evidence about the *source*; keeping its "good" rows would be
   trusting an attacker's leftovers. The current catalog stays in place and the refusal is audit-logged
   (`model.catalog_refresh_refused`).
4. **Persist only what was validated.** The route writes a normalised document (`schema`, `note`,
   `generated`, validated `models`), never the raw payload, so unvetted keys cannot reach the loader.
5. **At rest too.** `load_catalog()` applies the same gate to the override file, because that file is
   written from the network. A poisoned override falls back to the vetted bundled seed.

## Authenticity: keyless signing (implemented)

The shape gate above says *what* a catalog may contain; it cannot say *who* published it. That is
`hosting/catalog_trust.py`.

**Sigstore keyless, so no key exists to guard.** The publishing workflow proves its *identity* through
GitHub's OIDC; Fulcio issues it a short-lived certificate; the signing event is recorded in **Rekor**, a
public append-only transparency log. Verification asks "was this signed by our workflow?", not "does this
match a key we hold" - there is no long-lived private key to steal, rotate, or leak, and a forged signature
is publicly detectable after the fact.

- **Publish**: `.github/workflows/refresh-model-catalog.yml` runs `python -m sigstore sign` with
  `id-token: write`, emitting `www/model-catalog.json.sigstore.json`. Signed only when the catalog actually
  changed - the bundle carries a signed timestamp + inclusion proof, so an existing signature stays valid
  and re-signing an unchanged file would just churn a daily commit (#618). Catalog and signature are
  committed together or not at all.
- **Verify**: the refresh route fetches the bundle beside the catalog and calls
  `catalog_trust.verify_catalog` over the **exact bytes served, before parsing**. Identity is pinned to the
  workflow at `refs/heads/main`, so a signature from a fork, a branch, or another workflow does not satisfy
  it. Any failure - unreadable bundle, wrong signer, missing dependency, verifier error - returns a reason
  and the refresh is refused (fail closed), leaving the current catalog in place. Audit:
  `model.catalog_refresh_refused`.
- **Self-hosted mirrors** pin their own signer via `ANTHILL_MODEL_CATALOG_IDENTITY` / `_ISSUER`. There is
  deliberately **no "skip verification" switch** - that is the switch that gets turned on during an incident
  and left on. Unset or blank both fall back to the pinned default.
- **Packaging**: `sigstore` is collected whole in `Anthill.spec` / `Anthill-sidecar.spec`. It ships its TUF
  trust root as package data (`_store/.../trusted_root.json`), which PyInstaller would otherwise drop - the
  import would succeed and verification would fail *only in the dmg*, breaking Refresh for desktop users.

## Publishing without weakening `main`

The signature can only be made by the workflow (keyless: its identity *is* the signer), and it must be
pinned to `refs/heads/main` so a fork or branch cannot mint one. But `main` is a protected branch, so the
bot cannot push a daily commit to it, and Cloudflare's GitHub App cannot see this private monorepo to deploy
from it at all. So the workflow **signs here** and pushes only the built site to a small dedicated repo,
**`OneHillAI/anthill.run`**, which Cloudflare Pages is git-connected to and deploys on push. This repo is
never written by automation (`contents: read`); the signing run still happens on the default branch
(schedule / dispatch / push to main), so the certificate identity is
`...refresh-model-catalog.yml@refs/heads/main` in *this* repo - exactly what the client verifies. Where the
file is *served from* (which repo, which CDN) is irrelevant to verification, which is over the bytes and the
signer identity, not the URL.

**The deploy repo is outside the trust boundary, and that is fine.** The workflow pushes to `anthill.run`
with a deploy token (a repo-scoped PAT or deploy key). A compromise of that token, or of the `anthill.run`
repo, lets an attacker serve *different bytes* at `anthill.run/model-catalog.json` - but not a *valid*
catalog: the signature is over the bytes and can only be minted by the `refs/heads/main` workflow identity
in `OneHillAI/Anthill`, which the deploy token cannot assume. A client fetching tampered bytes fails
`verify_catalog` and keeps its current signed catalog (fail closed). So the deploy token is a
publish-availability credential, not a trust credential: losing it can cause a stale or missing catalog,
never a poisoned accepted one. The load-bearing secret remains what it was - who can run the signing
workflow on `main` - which is why branch protection there, not the deploy token, is the control that
matters.

## Alignment with the endpoint-transport trust model

This spec and `docs/specs/llm-endpoint-secure-transport.md` are the two halves of Anthill's trust path and
share one doctrine: **pinning is strict verification, not `verify=False`** (that spec pins a self-signed
cert; this one pins the publisher identity); **fail closed** (that spec refuses to serve a cleartext
endpoint; this one refuses an unsigned or unexpected catalog and keeps the current one); and **no silent
"skip verification" switch**.

They diverge on one axis, honestly: the endpoint spec can keep *no third party in the trust path* (SSH
tunnel / pinned cert) because it secures a **point-to-point** channel between two parties Anthill controls,
so a shared secret is possible. The catalog is a **broadcast** artifact fetched by unknown clients, where
no shared secret exists. That leaves two options: a self-managed signing key (the key custody the owner
explicitly ruled out) or keyless PKI. We chose keyless, which deliberately admits GitHub OIDC + Fulcio +
Rekor as trust anchors - the price of having no key to guard. Rekor's public transparency log makes any
misuse of those anchors detectable, and identity pinning bounds who can sign. If the project ever wants the
same zero-third-party posture the endpoint spec has, the only route is a project-held signing key, and the
custody question comes back with it.

## Freshness: rollback (implemented)

A signature proves WHO published a catalog, not WHICH one. Whoever controls the host can replay an older,
still-validly-signed catalog to re-introduce a model that has since been dropped. `catalog_trust.is_rollback`
refuses a catalog whose `generated` is older than the one already trusted; a *missing* date counts as a
rollback, since stripping the field is how you would dodge the check.

## Trust bottoms out at

GitHub's OIDC + Fulcio + Rekor, and **whoever can run that workflow on `main`**. Branch protection on `main`
is therefore load-bearing, not cosmetic: without it, anyone with write access could mint a genuinely valid
signature. (Enforced since the org moved to a GitHub Team plan - free on public repos.)

## What is still NOT covered (be honest)

- **A shape-valid attacker model on the official Ollama library** (`attacker/backdoor:7b`) passes the shape
  gate. Signing means it can only get in front of users if *our workflow* publishes it - i.e. via the
  curated seed, which is a reviewed code change.
- **The pull allowlist is circular.** Onboarding does `valid = {m.ollama_tag for m in LOCAL_CATALOG} |
  installed` with the comment "never pull arbitrary user input". That was sound when `LOCAL_CATALOG` was a
  hardcoded tuple; since the catalog became refreshable it validates network data against network data.
  Signing restores most of the property (the data is now provably ours), but the shape is still worth
  revisiting.
- **Freeze attacks.** An attacker who controls the host can serve the current signed catalog forever,
  keeping a client unaware of a newer one. Detecting that needs a periodically re-signed freshness marker
  (TUF's `timestamp` role); it conflicts with #618's "don't churn a daily commit", so it is deliberately
  deferred rather than half-built.
- **Full TUF** (`python-tuf`) would cover freeze + key-compromise recovery with role separation and
  threshold keys, at the cost of an offline root key and a key ceremony - i.e. the key custody this design
  deliberately avoids.
