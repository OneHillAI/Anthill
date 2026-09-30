# Self-hosted / VPC model discovery

## Problem

An admin connecting a model server they run themselves (a vLLM/TGI box, an Ollama on a Mac Mini, a VPC
GPU) could only pick the org model from a curated shortlist or type a raw id into "Other". The box often
already serves several models, but nothing in Anthill showed which ones. The connect flow even fetched the
served list to validate the endpoint, then discarded it, surfacing only a count ("3 model(s) available").
So the model selection looked far more limited than it is.

## Approach

Surface what the endpoint actually serves and let the admin pick from it.

- **`hosting/endpoint.list_models(base_url, api_key="")`** discovers the served model ids. The org endpoint
  is OpenAI-compatible by contract (base includes `/v1`), which vLLM, TGI, LM Studio and Ollama's `/v1` all
  expose, so it tries `{base}/models` first, then `{base}/v1/models` when the pasted URL omitted the
  suffix, then falls back to Ollama's native `/api/tags` for a box that serves only that. Returns a
  sorted, de-duped list; raises the last error only when the server can't be reached at all.
- **`POST /settings/organization/discover-models`** (admin) runs discovery against the URL + key typed into
  the connect form, or the saved ones, reusing the same RunPod provision-key fallback the connect and
  routing paths use (a RunPod `.../openai/v1` authenticates with the provisioning key, not a separate model
  key). Returns `{ok, models, current}` as JSON.
- **UI**: a "Discover models" button in the "Connect a model server you already run" card lists the served
  models as chips (the current one highlighted). Clicking a chip sets the model picker to "Other" and fills
  the custom-id field, so the admin confirms with the existing Save. No new persist path: discovery only
  feeds the field the save route already reads (`org_model == "__custom__"` -> `org_model_custom`).

## Scope

This is discovery, not a change to one-model-per-account: it widens the *selectable set* for the single
active org model, it does not run several at once. It applies to the self-hosted / VPC connect path (the
OpenAI-compatible or Ollama endpoint the admin points Anthill at). The Solo local picker already lists
models installed on the box via `OllamaBackend.installed_models()`.

## Follow-ups (not in P0)

- Surface installed-but-uncatalogued local models as first-class radio picks on the Solo `/models` page
  (today they appear in the management table, and any tag is pullable via the "Advanced: custom tag" field).
- Refresh the curated `DEFAULT_CATALOG` / `LOCAL_CATALOG` with current-generation open models.
- Show each discovered model's size / context where the endpoint reports it.
