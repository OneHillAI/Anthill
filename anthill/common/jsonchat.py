"""Helpers for asking a model for a JSON object and parsing it robustly.

Small local models often wrap JSON in a ```json fence, return nested objects/arrays where a
string was asked for, or (without a format hint) truncate mid-object so the result won't parse.
These helpers (1) ask Ollama for `format=json` when the backend supports it, (2) extract the JSON
even when fenced, and (3) coerce field values to strings - so a draft never silently degrades to
echoing the raw input.
"""

from __future__ import annotations

import json
import re
from typing import Any

# A JSON helper reply (intent decision, task draft, verifier verdict) is small by construction, so a
# generous token cap can't truncate a legitimate result - but it DOES bound a runaway. Reasoning
# models under a forced-JSON grammar can otherwise generate until the context fills (the server-side
# chat hang seen in qa/chat-eval/DEV_FINDINGS.md); this cap turns that into a bounded result.
JSON_MAX_TOKENS = 1024


def json_chat(backend, messages, *, think: bool | None = None) -> str:
    """Call backend.chat asking for bounded JSON output. Uses Ollama's ``fmt="json"`` and a token cap
    when the backend supports them, degrading gracefully for backends that accept fewer kwargs.

    ``think=False`` asks a reasoning model to skip its hidden thinking: a small structured decision does
    not need it, and under the token cap it can eat the whole budget and leave no JSON. A backend that
    does not accept ``think`` is called without it."""
    ladder: list[dict[str, Any]] = [
        {"fmt": "json", "num_predict": JSON_MAX_TOKENS},
        {"fmt": "json"},
        {},
    ]
    if think is not None:
        ladder = [{**k, "think": think} for k in ladder] + ladder
    for kwargs in ladder:
        try:
            return backend.chat(messages, **kwargs)
        except TypeError:
            continue
    return backend.chat(messages)


def extract_json(raw: str) -> dict:
    """Parse a JSON object out of a model response, tolerating ```json fences and surrounding
    prose. Returns {} if nothing parses."""
    if not raw:
        return {}
    s = raw.strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*\n?", "", s)
        s = re.sub(r"\n?```$", "", s).strip()
    candidates = [s]
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if m:
        candidates.append(m.group(0))
    for cand in candidates:
        try:
            out = json.loads(cand)
        except Exception:
            continue
        if isinstance(out, dict):
            return out
    return {}


def coerce_str(value) -> str:
    """Flatten a model field to a string. Small models sometimes return a list (e.g. steps) or a
    nested object where a string was asked for; join those readably instead of str(dict)."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        return "\n".join(coerce_str(x) for x in value if x is not None).strip()
    if isinstance(value, dict):
        return "\n".join(f"{k}: {coerce_str(v)}" for k, v in value.items()).strip()
    return str(value).strip()
