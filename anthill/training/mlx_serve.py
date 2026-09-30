"""Serve a locally-trained LoRA adapter via mlx-lm's OpenAI-compatible HTTP server.

On Apple Silicon, once a fine-tuned adapter wins the eval-gate, the Solo/local plane stops
serving the plain base model through Ollama and instead serves **base + adapter** through
``mlx_lm server`` (which loads the adapter at startup via ``--adapter-path`` - no fuse step
needed) and exposes an OpenAI ``/v1`` endpoint on localhost. The Solo plane then routes its
inference at that endpoint. Everything stays on the device; nothing leaves.

The subprocess boundary (``spawn``) and the readiness probe are injectable so the command
building and lifecycle are unit-testable without launching a real server or loading a model.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading


def serve_available() -> bool:
    """True when this host can serve an MLX fine-tune (Apple Silicon with mlx-lm installed)."""
    from .trainer import detect_toolchain

    return detect_toolchain() == "mlx"


def default_port() -> int:
    """The localhost port for the MLX server (next to Ollama's 11434); override with env."""
    try:
        return int(os.environ.get("ANTHILL_MLX_SERVE_PORT", "11435"))
    except ValueError:
        return 11435


_TOOL_EXAMPLE = r"{{\"name\": <function-name>, \"arguments\": <args-json-object>}}"
_TOOL_EXAMPLE_FIXED = r"{\"name\": <function-name>, \"arguments\": <args-json-object>}"


def _tool_compatible_chat_template(base_repo: str) -> str | None:
    """Fix MLX-LM's Qwen tool example so the model emits parseable JSON."""
    try:
        from transformers import AutoTokenizer

        template = AutoTokenizer.from_pretrained(base_repo).chat_template
    except Exception:
        return None
    if not isinstance(template, str) or _TOOL_EXAMPLE not in template:
        return None
    return template.replace(_TOOL_EXAMPLE, _TOOL_EXAMPLE_FIXED, 1)


def build_server_cmd(
    base_repo: str, adapter_path: str, host: str, port: int, *, chat_template: str | None = None
) -> list[str]:
    """The argv for ``mlx_lm server`` serving ``base_repo`` with ``adapter_path`` applied."""
    cmd = [
        sys.executable,
        "-m",
        "mlx_lm",
        "server",
        "--model",
        base_repo,
        "--adapter-path",
        adapter_path,
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "WARNING",
    ]
    if chat_template:
        cmd.extend(["--chat-template", chat_template])
    return cmd


def _default_spawn(cmd: list[str]) -> subprocess.Popen:
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)


def _default_probe(base_url: str) -> bool:
    """Ready when the server answers ``GET /v1/models`` with 200."""
    import httpx

    try:
        return httpx.get(f"{base_url}/models", timeout=2.0).status_code == 200
    except Exception:
        return False


class MlxServeManager:
    """Owns the single local ``mlx_lm server`` process for this instance.

    ``start`` resolves the served model to an mlx-loadable repo, spawns the server, and blocks
    (briefly) until the readiness probe passes. On success ``status()`` reports the ``/v1`` URL
    and the served model id; the caller persists those so the plane router can find the endpoint.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._url: str = ""
        self._model: str = ""
        self._adapter: str = ""
        self._detail: str = ""
        self._lock = threading.Lock()

    def status(self) -> dict:
        running = self._proc is not None and self._proc.poll() is None
        return {
            "running": running,
            "url": self._url if running else "",
            "model": self._model if running else "",
            "adapter": self._adapter if running else "",
            "detail": self._detail,
        }

    def start(
        self,
        base_model: str,
        adapter_path: str,
        *,
        port: int | None = None,
        host: str = "127.0.0.1",
        spawn=None,
        probe=None,
        attempts: int = 30,
        sleep=None,
    ) -> dict:
        """Spawn ``mlx_lm server`` for ``base_model`` + ``adapter_path`` and wait until it answers.

        ``base_model`` may be an Ollama tag - it is resolved to an mlx-community repo. Returns the
        status dict; ``running`` is False with a human-readable ``detail`` if it could not start.
        """
        from .trainer import _mlx_base_model

        self.stop()
        if spawn is None and not serve_available():
            with self._lock:
                self._detail = "mlx-lm is not installed (pip install 'anthill[train-mac]')"
            return self.status()

        try:
            base_repo = _mlx_base_model(base_model)
        except Exception as exc:
            with self._lock:
                self._detail = f"cannot resolve base model: {exc}"
            return self.status()

        port = port or default_port()
        base_url = f"http://{host}:{port}/v1"
        spawn = spawn or _default_spawn
        probe = probe or _default_probe
        sleeper = sleep or _sleep_default

        try:
            proc = spawn(
                build_server_cmd(
                    base_repo,
                    adapter_path,
                    host,
                    port,
                    chat_template=_tool_compatible_chat_template(base_repo),
                )
            )
        except Exception as exc:
            with self._lock:
                self._detail = f"could not start mlx-lm server: {exc}"
            return self.status()

        with self._lock:
            self._proc = proc
            self._model = base_repo
            self._adapter = adapter_path
            self._detail = "starting mlx-lm server..."

        for _ in range(max(1, attempts)):
            if proc.poll() is not None:  # died before becoming ready
                with self._lock:
                    self._detail = "mlx-lm server exited before it was ready"
                    self._url = ""
                return self.status()
            if probe(base_url):
                with self._lock:
                    if self._proc is proc:  # not superseded
                        self._url = base_url
                        self._detail = "serving local fine-tune"
                return self.status()
            sleeper(1.0)

        with self._lock:
            self._detail = "mlx-lm server did not become ready in time"
            self._url = ""
        return self.status()

    def stop(self) -> None:
        with self._lock:
            proc, self._proc = self._proc, None
            self._url = ""
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass


def _sleep_default(seconds: float) -> None:
    import time

    time.sleep(seconds)


# Process-wide singleton (the local server is a property of this running instance).
manager = MlxServeManager()
