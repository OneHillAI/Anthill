"""Source configuration for scripts/gen_model_catalog.py.

The frontier list itself lives in ``anthill/hosting/model_catalog.json`` - the vetted seed, and the single
source of truth for *which* models appear and their base metadata. This module holds only the bits the
generator needs that are NOT in the seed:

  * where to fetch live data (the intelligence leaderboard, HuggingFace, the Ollama registry), and
  * how to reconcile a catalog row with its record on the Artificial Analysis leaderboard when the names
    do not line up automatically.

Editing this file is safe and reviewed through a normal PR. To change which models appear, edit the seed
(not this file); the daily generator republishes the hosted copy at anthill.run.
"""

from __future__ import annotations

# --- Live sources -------------------------------------------------------------------------------------

# Artificial Analysis Intelligence Index (the ranking). Free tier: create an account, generate a key, pass
# it in the `x-api-key` header. Rate limit is ~10 requests / 24h, so the daily job uses exactly one.
# The free tier returns the headline `artificial_analysis_intelligence_index` per model (but NOT params or
# license - those come from HuggingFace below).
AA_MODELS_URL = "https://artificialanalysis.ai/api/v2/data/llms/models"

# HuggingFace model metadata: total parameter count (safetensors), the `license:*` tag, and the gated flag.
HF_MODEL_URL = "https://huggingface.co/api/models/{hf_id}"
# Trending open-weight text-generation models, used only to FLAG candidates a human might add to the seed.
HF_TRENDING_URL = "https://huggingface.co/api/models?sort=trending&filter=text-generation&limit=40"

# Ollama registry (OCI). A 200 here means the tag is actually pullable with `ollama pull <tag>`.
OLLAMA_MANIFEST_URL = "https://registry.ollama.ai/v2/library/{repo}/manifests/{tag}"
OLLAMA_MANIFEST_ACCEPT = "application/vnd.docker.distribution.manifest.v2+json"

HTTP_TIMEOUT = 30.0
USER_AGENT = "anthill-model-catalog-generator (+https://anthill.run)"

# --- Reconciliation overrides -------------------------------------------------------------------------

# The generator matches a catalog row to an Artificial Analysis record automatically, first by `hf_id`
# and then by a normalised name. When a lab's naming makes that fail, pin it here: catalog `name` -> the
# exact AA record name (or slug). An absent entry just means "keep the seed's intelligence value" - a miss
# is reported, never fatal. Fill these in as the labs' names settle.
AA_NAME_OVERRIDES: dict[str, str] = {
    # "Kimi K2.7 Code": "Kimi K2.7 (Code)",
}

# Known HuggingFace orgs behind frontier open-weight labs, used to keep the "new model" discovery report
# signal-heavy instead of listing every trending fine-tune. Extend as new labs appear.
FRONTIER_HF_ORGS: frozenset[str] = frozenset(
    {
        "deepseek-ai",
        "Qwen",
        "zai-org",
        "moonshotai",
        "MiniMaxAI",
        "openai",
        "meta-llama",
        "google",
        "mistralai",
        "microsoft",
        "xai-org",
    }
)
