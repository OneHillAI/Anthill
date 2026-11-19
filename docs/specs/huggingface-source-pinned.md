# Spec: Hugging Face as a second model source, with the file fingerprint checked after the pull

Status: proposal (no code). Lane: `pillar:model`. Issue: #292. **This changes a foundation (the model store and the
tag gate)**, so by the roadmap's rule it needs the maintainer's approval before any implementation issue is filed.
Builds on `docs/specs/model-catalog-trust.md` (signed catalogue, shape gate) and `docs/specs/model-selection.md`.

## Problem

The catalogue can only list models that exist as Ollama library tags. `is_safe_ollama_tag` and `is_safe_row`
(`anthill/hosting/sizing.py`) reject `hf.co/...` references on purpose, as a supply-chain control: a tag taken from
the network is a download instruction for arbitrary weights. Most regional-language and specialised models are
published only on Hugging Face (in the catalogue research, 18 of the first 26 proposed rows). Ollama can already pull
from Hugging Face, and the Advanced custom-tag box in the models page already accepts `hf.co/...` (a deliberate human
choice), but nothing lets the signed catalogue offer such a model with a check that it is the file that was approved.

## Decisions already taken (maintainer, 2026-11-19)

These bound the data model; they were taken for the catalogue and apply here.
1. Hugging Face is added as a second source with safeguards: each model is tested by a real download, pinned to its
   file fingerprint, its file type and licence checked, a person approves it, and the catalogue stays signed.
2. Licences: every model enters with its restrictions shown as badges; nothing is hidden by licence.
3. Builds: the default is a GGUF build. An MLX build may be offered only if it passes the memory test, and only as an
   advanced option.
4. Origin is shown and filtered by verified region (Europe, North America, South America, Africa, MENA, Southeast Asia,
   the rest of Asia; the exact list is still to be confirmed), not by country.
5. Build information (file type, quantisation, minimum Ollama version) is checked by hand or by a test run when a model
   is added, and again when the publisher updates it or the bundled Ollama version changes. No scripted reads of the
   Ollama registry.

## Requirements

1. **A typed source on every catalogue row.** `source` is one of `ollama-library` (today's rows, unchanged) or
   `huggingface`. A row with no `source` is `ollama-library`. Older installs ignore fields they do not know.
2. **A Hugging Face row carries:** `hf_repo` (`owner/repo`, the same shape check as `is_safe_hf_id`), `quant` (the
   label Ollama accepts after the colon, for example `Q4_K_M`), `file_bytes`, `fingerprints` (the SHA-256 of each layer
   Ollama stores for the pull: the model layer, and a vision-projector layer where there is one), `role` (chat,
   embedding, rerank, guard, vision, translation, decision, other), `format: gguf`, `engine: llamacpp`, `restrictions[]`
   (badges), `origin_region` with `region_verified`, `min_ollama_version` or `unknown`, and `build_checked` (date,
   Ollama version, method). Unknown values are written `unknown`, never guessed.
3. **Shape gate.** A Hugging Face reference is accepted only as the exact string `hf.co/<hf_repo>:<quant>` built by the
   app from the row's own fields. The app never takes a Hugging Face reference from the network or from free text in the
   catalogue; no URL, no redirect, no `@digest`, no gated repository (a token is out of scope).
4. **Pull, verify, then admit.** The app pulls the model, reads the fingerprints Ollama stored for it, and compares them
   with the row's `fingerprints`. Only on a full match is the model recorded as installed and offered for use. Until
   then it is not loaded, selectable or served.
5. **Mismatch.** On any difference, or a missing layer, the app deletes the pulled model, records an audit row (model,
   expected and found fingerprints, time) and tells the user in plain words that the downloaded file is not the one that
   was approved, so it was removed. It does not retry by itself.
6. **Catalogue-only, user-triggered.** A Hugging Face model is pulled only when a person chooses it in the picker.
   Nothing is pulled automatically. The Advanced custom-tag box is unchanged and stays the person's own explicit choice
   (no fingerprint is checked there, and the page says so).
7. **Role typing.** The picker offers chat models for chat, embedding models only where an embedder is chosen, and so on.
   The fit logic stays chat-only until non-chat roles are typed; a non-chat row has no chat fit tier.
8. **Build default.** A row names an explicit GGUF build. An MLX build appears only as an advanced option and only if the
   row records a passed memory test.
9. **Re-check triggers.** `build_checked` is refreshed when a model is added, when its publisher updates it (the
   fingerprints change), and when the bundled Ollama version changes. A fingerprint the catalogue publishes replaces the
   old one through the normal signed refresh.
10. **Existing guarantees stay.** Signature verification, rollback protection by `generated` date, the shape checks for
    library tags, and the "refresh fails closed" rule are unchanged.

## Verification flow

1. The person picks a catalogue row with `source: huggingface`.
2. The app builds `hf.co/<hf_repo>:<quant>` from the row and starts the pull through Ollama (the existing pull path).
3. Ollama refuses a repository that is not GGUF or that holds only split (sharded) files, before downloading anything;
   the app shows that message in plain words and the row is reported to the catalogue owners as broken.
4. After the pull the app reads the stored fingerprints and compares them with the row.
5. Match: admit. Mismatch or missing layer: delete, audit, tell the user.
6. The app also checks that the role and family Ollama reports for the model agree with the row (an embedding row must
   report the `embedding` capability); a disagreement is handled like a mismatch.

## Measured (issue #292, 2026-11-19; Ollama 0.40.16, M4 16 GB, scratch engine, throwaway store)

- A Hugging Face pull works (chat model Qwen3-0.6B Q8_0, 639 MB: 30 s; embedding model nomic-embed-text-v1.5 Q4_K_M,
  84 MB: 5.5 s). Ollama writes a manifest at `manifests/hf.co/<owner>/<repo>/<quant>` and records the SHA-256 of the
  model layer.
- The fingerprint was stable: a repeat pull downloaded nothing (0.5 s) and delete plus a fresh pull gave the identical
  value. The window was minutes; a change by the publisher over days was not tested.
- A fingerprint cannot be given in the pull reference: `...:<quant>@sha256:<digest>` and `...@sha256:<digest>` were
  refused with HTTP 400 `invalid model name`. Hence the check after the pull (requirement 4).
- Two files of one model (Q4_K_M and Q4_0) have different fingerprints, so the comparison would detect a different file.
  A real swap by a publisher was not simulated.
- A repository with only split GGUF files and a repository with no GGUF were refused at the manifest stage, before any
  download, with clear messages.
- The embedding model pulled and ran (capability `embedding`, 768-number vectors); the chat model reports completion,
  tools and thinking; both ran on the llama.cpp runner.

## Threat note

An attacker who controls a Hugging Face repository can: replace the file after approval; delete or rename the
repository; publish a look-alike name; upload a file whose behaviour is harmful but whose format is valid.
- A replaced file: the catalogue pins the fingerprint, so a changed file is detected and deleted before it is ever
  loaded. The bytes are on disk briefly; they are not executed. Detection depends on the comparison running (requirement
  4) and on the catalogue's fingerprint being the signed one.
- A look-alike name or a typo: the app only builds references from signed catalogue rows, never from free text.
- A deleted or renamed repository: the pull fails; already installed models are unaffected.
- A valid-format file with harmful behaviour, or backdoored weights: **not stopped.** A fingerprint proves which file it
  is, not that it is safe. The human approval and the automated checks (licence, format, a real test run) reduce this
  risk; they do not remove it.
- Chat templates and other metadata inside a GGUF file are read by the engine: whether that is a risk class here was not
  assessed and is listed as an open point.
- The Advanced custom-tag box bypasses all of this by design; the page must keep saying so.

## What breaks or needs a change (sizes are guesses; each would be its own issue)

| Where | What | Size |
|---|---|---|
| `is_safe_ollama_tag`, `is_safe_row` (`anthill/hosting/sizing.py`) | Accept a typed Hugging Face source built from fixed fields; keep rejecting any free-text `hf.co` tag | M |
| `Model` dataclass and `_model_from_row` | New fields: source, role, restrictions, region, fingerprints, build check | M |
| `catalog_trust.py`, the catalogue generator | Fingerprints and the new fields inside the signed rows; generator checks them (outside this repo) | M |
| Pull paths (`_start_model_pull`, `POST /models/pull`, onboarding) | Verify-then-admit and delete-on-mismatch; an audit row | M |
| Reading the fingerprint | Which field to pin is open (see below); the app may need to read Ollama's stored manifest | S to M |
| Fit logic and picker | Chat-only assumptions; role-aware lists; badges; region filter | L |
| Backup and restore | Models under `hf.co/<owner>/<repo>` instead of the library path | S |
| Delete (`delete_model`) | Worked in the measurement with an `hf.co/...` name | S |
| Router vision list (`anthill/routing/router.py`) | Hand-written list; a Hugging Face vision model needs an entry or a typed row | M |

## Unchanged

Signing and rollback protection; the shape checks for library tags; how local, org and cloud serving are chosen; the
Advanced box; the privacy gate for anything that leaves the machine.

## Open points (not settled by the measurement)

1. Which value is pinned: the model layer's SHA-256 (stable in the measurement) or the manifest digest (also repeated
   identically in the debug log, and it changes if Ollama's metadata layers change), and how the app reads it (Ollama's
   stored manifest file, or an API field; whether `/api/tags` or `/api/show` exposes the model-layer digest was not
   measured).
2. A real upstream change of a file, and a different Ollama version resolving the same reference to a different file.
3. Vision-projector layers and repositories with several GGUF files: pin every layer.
4. Whether a chat template inside a GGUF file is a risk class that needs its own check.
5. Hugging Face availability, rate limits and gated repositories for a multi-user organisation install.

## Acceptance

This PR adds only this spec and the OpenSpec change files. An implementation, when approved, is accepted when:
- a fixture row with a wrong fingerprint is refused, the pulled model deleted and an audit row written;
- a fixture row with a matching fingerprint is admitted;
- a free-text `hf.co` reference in a catalogue row is rejected by the shape gate;
- a row with no `source` behaves exactly as today;
- a split-GGUF repository and a non-GGUF repository produce the plain messages;
- the measurement in this spec is reproduced on a scratch engine with the commands recorded.
