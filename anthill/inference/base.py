from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..config import Config


class BackendError(RuntimeError):
    """An inference backend couldn't fulfill a request: unreachable, model missing, or bad response.

    Carries a message written for a human running the CLI, not a stack trace.
    """


@dataclass
class Message:
    role: str  # "system" | "user" | "assistant"
    content: str
    images: list[str] | None = None  # base64-encoded images for vision models

    def as_dict(self) -> dict:
        d: dict = {"role": self.role, "content": self.content}
        if self.images:
            d["images"] = self.images
        return d


@dataclass
class ChatResult:
    """One completed chat turn's text plus an optional confidence signal (#278 stronger-escalation
    work). ``confidence`` is the mean per-token log-probability of the generated response - a
    negative float, closer to 0 meaning more confident, more negative meaning less - when the
    backend/model/response actually provided logprobs; ``None`` when it didn't (an older Ollama
    install, a provider that doesn't support it like Groq, or any parsing failure). ``None`` means
    "no signal from this call", never "confident" or "uncertain" by default - callers must not treat
    it as either."""

    text: str
    confidence: float | None = None


def _mean_logprob(token_logprobs: list) -> float | None:
    """Aggregate a list of {"logprob": float, ...} dicts (the shape both OpenAI-compatible endpoints
    and Ollama's newer logprobs support use) into one confidence figure. None on anything unexpected
    (empty list, wrong shape) rather than raising - this is a bonus signal, never load-bearing."""
    try:
        vals = [float(t["logprob"]) for t in token_logprobs if "logprob" in t]
    except (TypeError, KeyError, ValueError):
        return None
    if not vals:
        return None
    return sum(vals) / len(vals)


@runtime_checkable
class InferenceBackend(Protocol):
    """One model, behind one interface. Ollama for laptops, a standard ``/v1``
    server (vLLM, llama.cpp, LM Studio) for everything else."""

    model: str

    def chat(self, messages: Sequence[Message], *, temperature: float = 0.2) -> str: ...

    def chat_with_confidence(
        self, messages: Sequence[Message], *, temperature: float = 0.2
    ) -> ChatResult:
        """Like ``chat``, but also returns a confidence signal when the backend/call provides one
        (#278 stronger-escalation work) - the same request, no second round trip. Backends that can't
        provide logprobs (or weren't asked to, on a plain ``chat`` call) return ``ChatResult(text,
        confidence=None)``; callers must treat ``None`` as "no signal," not as any particular verdict.
        """
        ...

    def health(self) -> str | None:
        """None if the model is reachable and loaded, else a human-readable reason."""
        ...


def stays_local(backend: str, base_url: str) -> bool:
    """True when a turn answered by this backend/base_url never left this machine: the local Ollama
    backend, or an OpenAI-compatible endpoint bound to loopback (the on-device mlx-lm fine-tune
    server). False for any real network endpoint - an org's own cloud/RunPod/onprem server, or a
    third-party inference provider - used both to gate outbound PII scrubbing and to tell the user,
    per turn, whether their message left the machine."""
    if backend == "ollama":
        return True
    try:
        import httpx

        host = httpx.URL(base_url).host
    except Exception:
        return False
    return host in ("localhost", "127.0.0.1", "::1")


def build_backend(config: Config) -> InferenceBackend:
    from .ollama import OllamaBackend
    from .openai_compat import OpenAICompatBackend

    if config.backend == "ollama":
        return OllamaBackend(config.base_url, config.model, config.timeout)
    if config.backend == "openai":
        return OpenAICompatBackend(
            config.base_url,
            config.model,
            config.api_key,
            config.timeout,
            max_tokens=config.max_tokens,
        )
    raise BackendError(f"unknown backend {config.backend!r} (expected 'ollama' or 'openai')")
