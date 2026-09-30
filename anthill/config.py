from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OPENAI_URL = "http://localhost:8000/v1"
DEFAULT_MODEL = "qwen2.5:3b"


@dataclass
class Config:
    """How to reach the local model. Read from the environment so a node can be
    pointed at Ollama (default) or any standard ``/v1`` model server without code changes."""

    backend: str = "ollama"  # "ollama" | "openai"
    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_OLLAMA_URL
    api_key: str | None = None
    timeout: float = 300.0
    max_tokens: int | None = None  # cap generated output; None = provider default (unbounded)

    @classmethod
    def from_env(cls) -> Config:
        backend = os.environ.get("ANTHILL_BACKEND", "ollama").lower()
        base_url = os.environ.get("ANTHILL_BASE_URL") or (
            DEFAULT_OLLAMA_URL if backend == "ollama" else DEFAULT_OPENAI_URL
        )
        return cls(
            backend=backend,
            model=os.environ.get("ANTHILL_MODEL", DEFAULT_MODEL),
            base_url=base_url,
            api_key=os.environ.get("ANTHILL_API_KEY"),
            timeout=float(os.environ.get("ANTHILL_TIMEOUT", "300")),
        )


def hybrid_policy_from_env():
    """Build a hybrid.HybridPolicy from environment variables.

    ANTHILL_CLOUD_ENABLED   "1"/"true" to turn on cloud escalation
    ANTHILL_CLOUD_PROVIDER  openrouter | anthropic | moonshot | deepseek | together | huggingface
    ANTHILL_CLOUD_MODEL     override the provider's default model
    ANTHILL_CLOUD_THRESHOLD escalate when local confidence < this (default 0.5)
    ANTHILL_CLOUD_CONTEXT   "1" to allow sending wiki context (default off)
    ANTHILL_CLOUD_SCRUB     "0" to disable PII scrubbing (default on)
    ANTHILL_CLOUD_BUDGET    monthly USD cap (0 = none)
    """
    from .hybrid import HybridPolicy

    def _truthy(v: str) -> bool:
        return v.strip().lower() in {"1", "true", "yes", "on"}

    return HybridPolicy(
        enabled=_truthy(os.environ.get("ANTHILL_CLOUD_ENABLED", "")),
        provider=os.environ.get("ANTHILL_CLOUD_PROVIDER", "openrouter"),
        model=os.environ.get("ANTHILL_CLOUD_MODEL", ""),
        threshold=float(os.environ.get("ANTHILL_CLOUD_THRESHOLD", "0.5")),
        send_context=_truthy(os.environ.get("ANTHILL_CLOUD_CONTEXT", "")),
        scrub_pii=os.environ.get("ANTHILL_CLOUD_SCRUB", "1") != "0",
        monthly_budget_usd=float(os.environ.get("ANTHILL_CLOUD_BUDGET", "0")),
    )
