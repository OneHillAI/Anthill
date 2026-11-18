"""A small client for a LOCAL Ollama, for measuring (#236).

It reads the engine's own counters (prefill and decode counts and times, load time, cached tokens) through
``anthill.inference.stats``, and times the first word itself by streaming. It never pulls, deletes or changes a
model or a setting: a model that is not installed is reported, not downloaded. The one state it touches is
unloading a model, which the cold-load scenario needs.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from typing import Any

import httpx

from anthill.inference.stats import TurnStats, from_ollama

DEFAULT_URL = "http://localhost:11434"


class EngineError(RuntimeError):
    pass


class ModelMissing(EngineError):
    """A model the run needs is not installed. The harness never pulls one."""


class OllamaEngine:
    def __init__(self, base_url: str = DEFAULT_URL, timeout: float = 900.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> Any:
        try:
            resp = httpx.get(f"{self.base_url}{path}", timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, ValueError) as why:
            raise EngineError(
                f"Could not read {path} from Ollama at {self.base_url}: {why}"
            ) from why

    def version(self) -> str:
        return str(self._get("/api/version").get("version", ""))

    def installed(self) -> dict[str, str]:
        """Installed model tag -> digest."""
        return {m["name"]: m.get("digest", "") for m in self._get("/api/tags").get("models", [])}

    def require(self, tags: Sequence[str]) -> dict[str, str]:
        """The digests of ``tags``, or ModelMissing naming every one that is not installed."""
        have = self.installed()
        missing = [t for t in tags if t not in have and f"{t}:latest" not in have]
        if missing:
            raise ModelMissing(
                f"Not installed: {', '.join(missing)}. Install it yourself (ollama pull); this tool never downloads."
            )
        return {t: have.get(t) or have[f"{t}:latest"] for t in tags}

    def running(self) -> list[dict]:
        return list(self._get("/api/ps").get("models", []))

    def unload(self, model: str) -> None:
        try:
            httpx.post(
                f"{self.base_url}/api/generate", json={"model": model, "keep_alive": 0}, timeout=60
            ).raise_for_status()
        except httpx.HTTPError as why:
            raise EngineError(f"Could not unload {model}: {why}") from why

    def chat(
        self,
        model: str,
        messages: list[dict],
        *,
        num_ctx: int,
        num_predict: int,
        keep_alive: str = "10m",
    ) -> TurnStats:
        """One streamed chat call at temperature 0 with an explicit window; the engine's counters plus the time to
        the first word. Raises EngineError on an HTTP error, an error chunk, or a stream that never finishes."""
        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": keep_alive,
            "options": {
                "temperature": 0,
                "seed": 0,
                "num_ctx": num_ctx,
                "num_predict": num_predict,
            },
        }
        started = time.monotonic()
        first_ms: int | None = None
        final: dict | None = None
        try:
            with httpx.stream(
                "POST", f"{self.base_url}/api/chat", json=payload, timeout=self.timeout
            ) as resp:
                if not resp.is_success:
                    raise EngineError(f"Ollama returned {resp.status_code} for {model}.")
                for line in resp.iter_lines():
                    chunk = json.loads(line) if line else {}
                    if chunk.get("error"):
                        raise EngineError(f"Ollama error: {str(chunk['error'])[:200]}")
                    if first_ms is None and chunk.get("message", {}).get("content"):
                        first_ms = round((time.monotonic() - started) * 1000)
                    if chunk.get("done"):
                        final = chunk
                        break
        except (httpx.HTTPError, ValueError) as why:
            raise EngineError(f"The call to {model} failed: {why}") from why
        if final is None:
            raise EngineError(f"The stream from {model} ended before it finished.")
        return from_ollama(
            final,
            model=model,
            num_ctx=num_ctx,
            first_token_ms=first_ms,
            wall_ms=round((time.monotonic() - started) * 1000),
        )
