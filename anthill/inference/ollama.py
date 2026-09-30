from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.parse
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import httpx

from .base import BackendError, ChatResult, Message, _mean_logprob

_DEFAULT_OLLAMA_URL = "http://localhost:11434"

# (base_url, model) -> context window in tokens (0 = unknown). A model's context length is fixed, so
# probe /api/show once and cache it across backend instances (they're created per request). (#277)
_CTX_WINDOW_CACHE: dict[tuple[str, str], int] = {}


def _confidence_from_ollama_response(data: dict) -> float | None:
    """Best-effort parse of a #278 logprobs response. Ollama's native /api/chat added logprobs
    support after this was written without full public documentation of the exact response shape
    (as of writing, only its OpenAI-compatibility layer's shape - message.logprobs.content, a list of
    {"token", "logprob", ...} - is confirmed); this tries that shape and returns None on anything
    else, so an older Ollama install (which just ignores the `logprobs` request field) or an
    unexpected shape both degrade to "no signal" rather than raising. Verify/adjust against a real
    recent Ollama install once one is available to test against."""
    try:
        content = data.get("message", {}).get("logprobs", {}).get("content")
    except AttributeError:
        return None
    if not isinstance(content, list):
        return None
    return _mean_logprob(content)


class OllamaBackend:
    """Talks to a local Ollama server (https://ollama.com).

    Supports:
      - Standard text chat
      - Vision (images passed in Message.images as base64)
      - Streaming (chat_stream yields tokens as they arrive)
      - Dynamic model override (for task-based routing)
    """

    def __init__(
        self, base_url: str, model: str, timeout: float = 120.0, keep_alive: str | None = None
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        # Keep the model resident between turns so a follow-up doesn't pay the cold-start *reload*
        # (the biggest single local latency spike). Ollama's default is 5m; a longer window means a
        # user who pauses mid-conversation still hits a warm model. ``keep_alive`` is a duration
        # ("30m", "1h"), a number of seconds, or "-1" to stay loaded; empty = Ollama's default.
        self.keep_alive = (
            keep_alive if keep_alive is not None else os.environ.get("ANTHILL_KEEP_ALIVE", "30m")
        )
        # Last non-streaming call's Ollama timing/eval counts (prompt_eval_count, eval_count, durations).
        # A follow-up turn with a stable prefix shows a much lower prompt_eval_DURATION - the token count
        # is unchanged, but the cached prefix is re-processed near-instantly (measured: ~1263ms -> ~56ms
        # for an identical prefix on qwen3:8b). Read by a probe / metrics; never affects behaviour.
        self.last_stats: dict = {}

    # ── non-streaming ─────────────────────────────────────────────────────────

    def chat(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        model: str | None = None,  # override for task routing
        fmt: str = "",  # Ollama structured-output hint, e.g. "json"
        num_predict: int | None = None,  # cap generated tokens (bounds a runaway; None = unbounded)
        think: bool | None = None,  # False disables a reasoning model's <think> block
        trust_env: bool = True,
    ) -> str:
        return self._chat_raw(
            messages,
            temperature=temperature,
            model=model,
            fmt=fmt,
            num_predict=num_predict,
            think=think,
            trust_env=trust_env,
            logprobs=False,
        )[0]

    def chat_with_confidence(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        model: str | None = None,
        fmt: str = "",
        num_predict: int | None = None,
        think: bool | None = None,
        trust_env: bool = True,
    ) -> ChatResult:
        """Like ``chat``, plus a confidence signal (#278) when this Ollama install's ``/api/chat``
        actually returns per-token logprobs for the ``logprobs: true`` request field (recent Ollama
        versions only - unavailable in older installs, which silently ignore the field and return
        no logprobs; parsed defensively, never raises on a missing/unexpected shape)."""
        content, data = self._chat_raw(
            messages,
            temperature=temperature,
            model=model,
            fmt=fmt,
            num_predict=num_predict,
            think=think,
            trust_env=trust_env,
            logprobs=True,
        )
        return ChatResult(text=content, confidence=_confidence_from_ollama_response(data))

    def _chat_raw(
        self,
        messages: Sequence[Message],
        *,
        temperature: float,
        model: str | None,
        fmt: str,
        num_predict: int | None,
        think: bool | None,
        trust_env: bool,
        logprobs: bool,
    ) -> tuple[str, dict]:
        payload = self._payload(
            messages,
            temperature=temperature,
            model=model,
            stream=False,
            fmt=fmt,
            num_predict=num_predict,
            think=think,
            logprobs=logprobs,
        )
        try:
            if trust_env:
                resp = httpx.post(
                    f"{self.base_url}/api/chat",
                    json=payload,
                    timeout=self.timeout,
                )
            else:
                with httpx.Client(trust_env=False) as client:
                    resp = client.post(
                        f"{self.base_url}/api/chat",
                        json=payload,
                        timeout=self.timeout,
                    )
            resp.raise_for_status()
        except httpx.ConnectError as e:
            raise BackendError(
                f"Can't reach Ollama at {self.base_url}. "
                "Install it from https://ollama.com, then run `ollama serve`."
            ) from e
        except httpx.HTTPStatusError as e:
            raise BackendError(
                f"Ollama returned {e.response.status_code}: {e.response.text[:300]}"
            ) from e

        data = resp.json()
        self.last_stats = {
            k: data[k]
            for k in (
                "prompt_eval_count",
                "eval_count",
                "prompt_eval_duration",
                "eval_duration",
                "total_duration",
            )
            if k in data
        }
        content = data.get("message", {}).get("content")
        if not content:
            raise BackendError("Ollama returned an empty response.")
        return content, data

    def chat_with_tools(
        self,
        messages: Sequence[Message],
        tools: list[dict],
        *,
        temperature: float = 0.1,
        model: str | None = None,
        num_predict: int | None = None,  # cap generated tokens per agent step (bounds a runaway)
    ) -> dict:
        """Call ``/api/chat`` with tool specs and return the raw Ollama response dict
        (``{"message": {"content", "tool_calls"}}``). Connect/HTTP failures raise ``BackendError``
        with a human-readable message (the agent loop surfaces it instead of a raw errno)."""
        options: dict = {"temperature": temperature}
        if num_predict is not None:
            options["num_predict"] = num_predict
        payload = {
            "model": model or self.model,
            "messages": [m.as_dict() for m in messages],
            "tools": tools,
            "stream": False,
            "options": options,
        }
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive  # keep the model warm between agent steps
        try:
            resp = httpx.post(f"{self.base_url}/api/chat", json=payload, timeout=self.timeout)
            resp.raise_for_status()
        except httpx.ConnectError as e:
            raise BackendError(
                f"Can't reach Ollama at {self.base_url}. "
                "Install it from https://ollama.com, then run `ollama serve`."
            ) from e
        except httpx.HTTPStatusError as e:
            raise BackendError(
                f"Ollama returned {e.response.status_code}: {e.response.text[:300]}"
            ) from e
        return resp.json()

    # ── streaming ─────────────────────────────────────────────────────────────

    def chat_stream(
        self,
        messages: Sequence[Message],
        *,
        temperature: float = 0.2,
        model: str | None = None,
        num_predict: int | None = None,  # cap generated tokens (bounds a runaway mid-stream)
        think: bool | None = None,  # False disables a reasoning model's <think> block
    ) -> Iterator[str]:
        """Yield tokens as they arrive from Ollama.

        Usage:
            for token in backend.chat_stream(messages):
                print(token, end="", flush=True)
        """
        payload = self._payload(
            messages,
            temperature=temperature,
            model=model,
            stream=True,
            num_predict=num_predict,
            think=think,
        )
        try:
            with httpx.stream(
                "POST",
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout,
            ) as resp:
                if not resp.is_success:
                    body = bytearray()
                    for chunk in resp.iter_bytes(chunk_size=300):
                        body.extend(chunk[: 300 - len(body)])
                        if len(body) >= 300:
                            break
                    detail = body.decode("utf-8", errors="replace")
                    raise BackendError(f"Ollama returned {resp.status_code}: {detail}")
                completed = False
                emitted_text = False
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if chunk.get("error"):
                        raise BackendError(f"Ollama stream error: {str(chunk['error'])[:300]}")
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        emitted_text = emitted_text or bool(token.strip())
                        yield token
                    if chunk.get("done"):
                        completed = True
                        break
                if not completed:
                    raise BackendError("The Ollama stream ended before completion.")
                if not emitted_text:
                    raise BackendError("Ollama returned an empty response.")
        except httpx.TimeoutException as e:
            raise BackendError(
                "Ollama did not respond in time. The model may still be loading - try again shortly."
            ) from e
        except httpx.ConnectError as e:
            raise BackendError(f"Can't reach Ollama at {self.base_url}.") from e
        except httpx.TransportError as e:
            raise BackendError(
                "The connection to Ollama failed while streaming. Check that Ollama is running and "
                "try again."
            ) from e

    # ── health ────────────────────────────────────────────────────────────────

    def health(self) -> str | None:
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=10)
            resp.raise_for_status()
        except httpx.HTTPError:
            return f"Ollama not reachable at {self.base_url}. Is `ollama serve` running?"

        tags = [m.get("name", "") for m in resp.json().get("models", [])]
        check = self.model
        base = check.split(":")[0]
        if check not in tags and not any(t.split(":")[0] == base for t in tags):
            available = ", ".join(tags) or "none pulled yet"
            return f"Model {check!r} not found. Run `ollama pull {check}`. Available: {available}."
        return None

    def installed_models(self) -> list[str]:
        """Return list of locally installed model tags. Never raises: a non-JSON response from
        whatever is on ``base_url`` (e.g. mid-restart, or a different service on that port) is
        treated the same as unreachable - callers on a request-handling path (first-run setup,
        Settings) must not 500 on a malformed probe (mirrors ``context_window``'s own guarantee)."""
        try:
            resp = httpx.get(f"{self.base_url}/api/tags", timeout=10)
            resp.raise_for_status()
            return [m.get("name", "") for m in resp.json().get("models", [])]
        except (httpx.HTTPError, ValueError):
            return []

    def resident_models(self) -> set[str]:
        """Tags currently loaded in memory (``ollama ps``). Empty set if the engine can't be reached
        or returns something unparseable - callers treat 'unknown' as 'not resident'."""
        try:
            resp = httpx.get(f"{self.base_url}/api/ps", timeout=5)
            resp.raise_for_status()
            return {m.get("name", "") for m in resp.json().get("models", [])}
        except (httpx.HTTPError, ValueError):
            return set()

    def context_window(self, model: str | None = None) -> int:
        """The model's context window in tokens, read from Ollama's ``/api/show`` ``model_info`` and
        cached per (base_url, model). Returns 0 when it can't be read, so callers fall back to their own
        default (issue #277). Never raises."""
        m = model or self.model or ""
        key = (self.base_url, m)
        if key in _CTX_WINDOW_CACHE:
            return _CTX_WINDOW_CACHE[key]
        ctx = 0
        try:
            resp = httpx.post(f"{self.base_url}/api/show", json={"model": m}, timeout=5)
            resp.raise_for_status()
            info = resp.json().get("model_info", {}) or {}
            for k, v in info.items():  # e.g. "qwen2.context_length", "llama.context_length"
                if k.endswith(".context_length") and isinstance(v, int) and v > 0:
                    ctx = v
                    break
        except (httpx.HTTPError, ValueError):
            ctx = 0
        _CTX_WINDOW_CACHE[key] = ctx
        return ctx

    def delete_model(self, tag: str) -> bool:
        """Uninstall a locally installed model (``ollama rm``), reclaiming its disk. Returns True on
        success, False if the engine is unreachable or the tag isn't installed."""
        try:
            resp = httpx.request(
                "DELETE", f"{self.base_url}/api/delete", json={"name": tag}, timeout=30
            )
            resp.raise_for_status()
            return True
        except httpx.HTTPError:
            return False

    # ── internal ──────────────────────────────────────────────────────────────

    def _payload(
        self,
        messages: Sequence[Message],
        *,
        temperature: float,
        model: str | None,
        stream: bool,
        fmt: str = "",
        num_predict: int | None = None,
        think: bool | None = None,
        logprobs: bool = False,
    ) -> dict:
        options: dict = {"temperature": temperature}
        if num_predict is not None:
            # Bound generation. Ollama's default (-1) is unbounded, so a reasoning model that runs
            # away (esp. under a forced-JSON grammar) can generate until the context fills - the
            # server-side chat hang. A cap turns that into a bounded, graceful result.
            options["num_predict"] = num_predict
        payload: dict = {
            "model": model or self.model,
            "messages": [m.as_dict() for m in messages],
            "stream": stream,
            "options": options,
        }
        if self.keep_alive:
            payload["keep_alive"] = self.keep_alive  # keep the model warm between turns
        if fmt:
            payload["format"] = fmt  # Ollama: force valid structured output (e.g. "json")
        if think is not None:
            # Reasoning models (qwen3, deepseek-r1) emit a <think> block by default. For a formatting
            # task (e.g. research synthesis) that just burns the token budget and latency; think=False
            # turns it off so the whole budget goes to the answer. Ignored by non-reasoning models.
            payload["think"] = think
        if logprobs:
            # #278 stronger-escalation work: only recent Ollama versions actually return logprobs for
            # this - an older install silently ignores the field (never errors), so the response is
            # parsed defensively (_confidence_from_ollama_response) rather than assumed present.
            payload["logprobs"] = True
        return payload


# ── local server lifecycle ──────────────────────────────────────────────────────
# A bare-binary Ollama install (just ~/bin/ollama, no Ollama.app) has nothing to start the
# server on login, so the local model has nothing to talk to until `ollama serve` is run by
# hand. These helpers let the desktop launcher start it automatically.


def bundled_ollama_bin() -> str | None:
    """The Ollama binary bundled inside the packaged app, or None when not frozen/not bundled.
    scripts/build-app.sh stages it and Anthill.spec ships it under ``ollama-runtime/`` (unpacked
    next to the frozen app at ``sys._MEIPASS``), so the local model runs with nothing installed."""
    meipass = getattr(sys, "_MEIPASS", "") if getattr(sys, "frozen", False) else ""
    if not meipass:
        return None
    cand = os.path.join(meipass, "ollama-runtime", "ollama")
    return cand if os.path.exists(cand) and os.access(cand, os.X_OK) else None


# Pinned Ollama runtime (matches scripts/build-app.sh) for the FIRST-RUN download path. The Tauri
# desktop app ships WITHOUT Ollama so the dmg and every auto-update stay small; it fetches the engine
# once into the data dir on first launch instead. macOS only here - the cross-platform builds add the
# per-OS urls/checksums.
_OLLAMA_VERSION = "0.30.10"
_OLLAMA_DARWIN_SHA256 = "ad8a4d2918ed09480b8160419570602b4f49e48c9e3792efb601c0f54619e48e"
_OLLAMA_DARWIN_URL = (
    f"https://github.com/ollama/ollama/releases/download/v{_OLLAMA_VERSION}/ollama-darwin.tgz"
)


def _managed_ollama_dir() -> Path:
    """Where a first-run-downloaded Ollama lives: under the app data dir (``ANTHILL_HOME``), so it
    persists across launches AND app auto-updates (it is not inside the .app bundle, which the
    updater replaces wholesale)."""
    base = os.environ.get("ANTHILL_HOME", "")
    root = Path(base) if base else (Path.home() / "Library" / "Application Support" / "Anthill")
    return root / "ollama-runtime"


def managed_ollama_bin() -> str | None:
    """The first-run-downloaded Ollama binary in the data dir, or None if it isn't there yet."""
    cand = _managed_ollama_dir() / "ollama"
    return str(cand) if cand.exists() and os.access(cand, os.X_OK) else None


def _http_get_bytes(url: str) -> bytes:
    return httpx.get(url, follow_redirects=True, timeout=300).content


def download_ollama(
    *, fetch: Callable[[str], bytes] | None = None, verify: bool = True
) -> str | None:
    """Download + extract the pinned Ollama runtime into the data dir; return the binary path or None.

    The desktop app calls this on first run when no Ollama is found, so the local model works without
    the user installing anything - while keeping the dmg/auto-update small (the engine is fetched once
    here, not bundled). macOS only for now; best-effort and silent - any failure returns None and the
    chat falls back to its "install Ollama" message. The checksum is verified so an unexpected binary
    is never run. ``fetch``/``verify`` are injectable for tests."""
    if sys.platform != "darwin":
        return None  # other OSes get their own url/checksum when the cross-platform builds land
    if managed_ollama_bin():
        return managed_ollama_bin()
    try:
        data = (fetch or _http_get_bytes)(_OLLAMA_DARWIN_URL)
        if verify and hashlib.sha256(data).hexdigest() != _OLLAMA_DARWIN_SHA256:
            return None
        dest = _managed_ollama_dir()
        dest.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tf:
            # filter="data" (PEP 706) rejects absolute paths + `..` traversal, so a member can't write
            # outside `dest`. The tgz is the ollama binary + its sibling runner/gpu libs (#488).
            tf.extractall(dest, filter="data")
        binpath = dest / "ollama"
        if binpath.exists():
            os.chmod(binpath, 0o755)
            return str(binpath)
    except Exception:
        pass
    return None


def find_ollama_bin() -> str | None:
    """Locate the ollama binary: the runtime **bundled in the app** first, then a **first-run
    download** in the data dir, then ``~/bin/ollama`` (bare-binary install), then PATH. Returns the
    absolute path, or None if ollama is not available anywhere. PATH alone is unreliable - a macOS GUI
    app inherits a minimal PATH that often excludes ``~/bin``."""
    bundled = bundled_ollama_bin()
    if bundled:
        return bundled
    managed = managed_ollama_bin()
    if managed:
        return managed
    home_bin = os.path.expanduser("~/bin/ollama")
    if os.path.exists(home_bin) and os.access(home_bin, os.X_OK):
        return home_bin
    return shutil.which("ollama")


def _server_reachable(base_url: str, *, timeout: float = 1.5) -> bool:
    try:
        return httpx.get(f"{base_url.rstrip('/')}/api/tags", timeout=timeout).status_code == 200
    except Exception:
        return False


def _spawn_ollama_serve(ollama_bin: str) -> None:
    # Detached (new session) so the server outlives the launcher that started it.
    subprocess.Popen(
        [ollama_bin, "serve"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def ensure_serving(
    base_url: str = _DEFAULT_OLLAMA_URL,
    *,
    wait_s: float = 12.0,
    find_bin: Callable[[], str | None] = find_ollama_bin,
    reachable: Callable[[str], bool] = _server_reachable,
    spawn: Callable[[str], None] = _spawn_ollama_serve,
    sleep: Callable[[float], None] = time.sleep,
) -> bool:
    """Ensure a LOCAL Ollama server is answering at ``base_url``, starting it if needed.

    If it is already up, returns True without doing anything. Otherwise, when ollama is
    installed AND ``base_url`` is local, start ``ollama serve`` detached and poll up to
    ``wait_s`` seconds for it to answer. Returns whether it is reachable at the end. Never
    raises - a missing binary, a remote URL, or a failed launch just returns False, and the
    caller falls back to its normal "Can't reach Ollama" message. Boundaries are injectable
    so this unit-tests without a real server.
    """
    if reachable(base_url):
        return True
    host = (urllib.parse.urlparse(base_url).hostname or "").lower()
    if host not in {"localhost", "127.0.0.1", "::1"}:
        return False  # never auto-start a server for a remote host - that is not ours to manage
    ollama_bin = find_bin()
    if not ollama_bin:
        return False
    spawn(ollama_bin)
    waited = 0.0
    while waited < wait_s:
        sleep(0.5)
        waited += 0.5
        if reachable(base_url):
            return True
    return False
