"""Model sourcing: the curated catalog of open models with their params + license.

``builtin_catalog`` yields a ``CatalogModel`` per seed model (its size for the sizer, its license so an
org does not pick one it cannot use commercially). ``params_from_name`` pulls the size out of a tag /
repo name ("...-32B", "qwen2.5:14b"), and ``servable_id`` maps a friendly display name to the concrete
Hugging Face repo id or Ollama tag its serving stack needs.

The weights you pick are downloaded once and then run on your own backend.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .sizing import load_catalog

# a parameter-count token: "7B", "1.5b", "70B" - not "q4", "fp16", or a bare number
_SIZE_RE = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*b\b", re.I)

# indicative licenses for the built-in seed models (verify against the model card before relying on it).
# Matched as a substring of the display name, first hit wins - keep more specific keys before looser ones.
_LICENSE_HINTS = (
    ("gpt-oss", "Apache-2.0"),
    ("qwen", "Apache-2.0"),
    ("llama", "Llama Community"),
    ("gemma", "Gemma"),
    ("phi", "MIT"),
    ("deepseek", "MIT"),
    ("mistral", "Apache-2.0"),
)


@dataclass
class CatalogModel:
    name: str
    params_b: float | None
    license: str = ""
    source: str = "builtin"  # builtin | ollama | huggingface
    gated: bool = False  # HF-gated model (needs license acceptance + a token for cloud serving)
    has_quant: bool = (
        False  # a verified 4-bit (AWQ) build exists, so this model fits a smaller cloud GPU
    )
    num_attention_heads: int = 0  # 0 = unknown; see sizing.Model.num_attention_heads


def params_from_name(name: str) -> float | None:
    """Pull a parameter count out of a model name/tag: 'qwen2.5:32b' -> 32.0, 'Llama-3-70B' -> 70.0."""
    best: float | None = None
    for m in _SIZE_RE.finditer(name or ""):
        try:
            v = float(m.group(1))
        except ValueError:
            continue
        if 0.1 <= v <= 2000 and (best is None or v > best):
            best = v
    return best


def _license_for(name: str) -> str:
    n = (name or "").lower()
    for key, lic in _LICENSE_HINTS:
        if key in n:
            return lic
    return ""


def builtin_catalog() -> list[CatalogModel]:
    """The curated seed (from the sizer's catalog), with params + an indicative license + gated flag."""
    return [
        CatalogModel(
            m.name,
            m.params_b,
            m.license
            or _license_for(m.name),  # the catalog's own licence, else an indicative guess
            "builtin",
            gated=m.gated,
            has_quant=bool(m.quant_hf_id),
            num_attention_heads=m.num_attention_heads,
        )
        for m in load_catalog()
    ]


def servable_id(name: str, *, prefer_hf: bool, quantized: bool = False) -> str:
    """Resolve a catalog selection to the concrete id its serving stack needs.

    The picker stores a friendly display name ("Llama 3.3 70B"); a GPU cannot serve that - vLLM/cloud
    needs the Hugging Face repo id and on-prem Ollama needs a tag. This maps the name to the right one
    so the admin never has to type an id. ``prefer_hf`` True for vLLM/cloud, False for on-prem Ollama.
    ``quantized`` True serves the catalog's 4-bit AWQ repo (so a big model fits a smaller cloud GPU);
    vLLM auto-detects the quantization from the repo, so nothing else changes. A name that is not in the
    catalog (an advanced user typed a real id) passes through unchanged, and a catalog entry missing the
    preferred id falls back to the other id, then the name.
    """
    key = (name or "").strip()
    if not key:
        return ""
    for m in load_catalog():
        if m.name.lower() == key.lower():
            if quantized and prefer_hf and m.quant_hf_id:
                return m.quant_hf_id
            return (
                (m.hf_id or m.ollama_tag or m.name)
                if prefer_hf
                else (m.ollama_tag or m.hf_id or m.name)
            )
    return key  # already a concrete tag / HF id, or an unknown the operator entered deliberately
