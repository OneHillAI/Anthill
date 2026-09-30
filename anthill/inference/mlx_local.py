"""An inference backend that runs an MLX model (optionally with a LoRA adapter) on Apple Silicon.

Used by the local eval-gate: ``evaluate_models`` calls ``chat(msgs, model=...)`` for both the
current model and the candidate. Here the ``model`` argument is interpreted as a LoRA adapter
path - empty means the bare base model, a path means base + that adapter - so the gate can score
"base" against "base + new adapter" entirely within MLX, without standing up a server.

mlx-lm is a heavy, platform-specific dependency (the ``train-mac`` extra), so it is imported
lazily. The ``_load`` / ``_generate`` wrappers are module-level to keep the backend unit-testable
by monkeypatching them.
"""

from __future__ import annotations

from collections.abc import Sequence

from .base import BackendError, ChatResult, Message


def _load(base_repo: str, adapter_path: str | None):
    from mlx_lm import load

    return load(base_repo, adapter_path=adapter_path or None)


def _generate(model, tokenizer, prompt, max_tokens: int) -> str:
    from mlx_lm import generate

    return generate(model, tokenizer, prompt=prompt, max_tokens=max_tokens, verbose=False)


class MlxBackend:
    """Generate with an mlx-community base model, applying a LoRA adapter when one is given.

    ``model`` on ``chat`` is an adapter path (or ""/None for the base). Loaded (base, adapter)
    pairs are cached so an eval sweep does not reload weights on every example.
    """

    def __init__(self, base_repo: str, *, max_tokens: int = 256) -> None:
        self.base_repo = base_repo
        self.model = base_repo  # satisfies the InferenceBackend Protocol
        self.max_tokens = max_tokens
        self._cache: dict[str, tuple] = {}

    def _loaded(self, adapter_path: str):
        key = adapter_path or ""
        if key not in self._cache:
            try:
                self._cache[key] = _load(self.base_repo, adapter_path or None)
            except Exception as e:  # missing mlx-lm, bad repo/adapter
                raise BackendError(f"could not load MLX model {self.base_repo!r}: {e}") from e
        return self._cache[key]

    def chat(
        self, messages: Sequence[Message], *, temperature: float = 0.2, model: str | None = None
    ) -> str:
        mdl, tok = self._loaded(model or "")
        msgs = [{"role": m.role, "content": m.content} for m in messages]
        try:
            prompt = tok.apply_chat_template(msgs, add_generation_prompt=True)
        except Exception:
            prompt = "\n\n".join(m["content"] for m in msgs)
        return _generate(mdl, tok, prompt, self.max_tokens)

    def chat_with_confidence(
        self, messages: Sequence[Message], *, temperature: float = 0.2, model: str | None = None
    ) -> ChatResult:
        """Satisfies the InferenceBackend Protocol; never used by the escalation trigger (#278) -
        this class is the eval-gate only, never a live chat-serving backend (see module docstring),
        so there is no meaningful confidence signal to compute here."""
        return ChatResult(text=self.chat(messages, temperature=temperature, model=model))

    def health(self) -> str | None:
        import importlib.util

        if importlib.util.find_spec("mlx_lm") is None:
            return "mlx-lm is not installed (pip install 'anthill[train-mac]')"
        return None
