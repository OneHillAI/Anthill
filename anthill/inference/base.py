from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from ..config import Config


class BackendError(RuntimeError):
    """An inference backend couldn't fulfill a request: unreachable, model missing, or bad response.

    Carries a message written for a human running the CLI, not a stack trace.
    """


class ToolsUnsupported(BackendError):
    """The model does not support tool calling (Ollama answers 400 "does not support tools"). The agent
    executor catches it and answers without tools instead of failing the run."""


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


# Names a program on this machine uses for this machine. The last two are the host as seen from inside a Docker
# or Podman container, which is how the repo's compose file reaches an Ollama running on the host.
_THIS_MACHINE = ("localhost", "host.docker.internal", "host.containers.internal")


def _is_this_machine(base_url: str) -> bool:
    """True only when the URL's host is this machine: loopback (127.0.0.0/8, ::1), the unspecified address, a
    ``localhost`` name, or the container-to-host names above. Any other name or address, including a LAN
    address, is another machine. An unreadable URL is not this machine."""
    import httpx

    try:
        host = httpx.URL(base_url).host.lower().rstrip(".")
    except (httpx.InvalidURL, TypeError, ValueError):
        return False
    if host in _THIS_MACHINE or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def stays_local(backend: str, base_url: str) -> bool:
    """True when a turn answered by this backend/base_url never left this machine: an Ollama or an
    OpenAI-compatible server (the on-device mlx-lm fine-tune server) at an address on this machine. The
    address decides, not the backend: an Ollama pointed at another machine, whatever its name, is remote.
    False for any real network endpoint - an org's own cloud/RunPod/onprem server, a LAN box, or a
    third-party inference provider - used both to gate outbound PII scrubbing and to tell the user,
    per turn, whether their message left the machine. An Ollama with no address set is the default
    one on this machine."""
    if backend == "ollama" and not (base_url or "").strip():
        return True
    return _is_this_machine(base_url)


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
