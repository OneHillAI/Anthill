#!/usr/bin/env python3
"""Release gate: run one real chat end to end against the FROZEN sidecar binary.

    python scripts/smoke_frozen_chat.py dist/anthill-server
    python scripts/smoke_frozen_chat.py dist/anthill-server --real-ollama qwen2.5:0.5b

Why this exists: the packaged app once shipped with a sidecar that was killed by SIGSEGV on every
chat turn (Arrow's mimalloc allocator crashing on a worker thread inside the PyInstaller build). The
same code ran fine from source and no import-level or main-thread probe reproduced it, only a real
request through the real server did. So this script boots the actual frozen binary on a throwaway
database, points it at a small fake Ollama (so it needs no model, network, or real Ollama), signs up,
sends a chat, and requires a real streamed answer and a server that is still alive afterwards. The fake
reports an embedding model, so the semantic cache is active and the native lancedb/pyarrow path runs,
exactly as it does for a user.

With --real-ollama MODEL the fake is left out. The backend then fetches and starts its own Ollama exactly as
it does on a user's first run, the script pulls the small MODEL with it, and the chat is answered by that
real model (the Windows job runs this; the macOS release build never passes the flag).

Exit status: 0 = the chat completed, 1 = it did not (a native crash shows up as a signal name).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EMBED_DIM = 1024
CHAT_MODEL = "qwen2.5:3b"
ANSWER = (
    "A non-disclosure agreement in Germany is governed by the German Civil Code and the Trade Secrets "
    "Act, while in Delaware it is governed by contract law and the Uniform Trade Secrets Act. The "
    "practical differences are how damages, injunctions, and the reasonableness of the restrictions "
    "are assessed, so an agreement usually needs to be drafted for the jurisdiction that will enforce it."
)


def _unit_vector(text: str) -> list[float]:
    """A deterministic 1024-d unit vector per input, so the same prompt always embeds identically."""
    out: list[float] = []
    counter = 0
    while len(out) < EMBED_DIM:
        digest = hashlib.sha256(f"{counter}:{text}".encode()).digest()
        out.extend(b / 255.0 - 0.5 for b in digest)
        counter += 1
    vec = out[:EMBED_DIM]
    norm = sum(v * v for v in vec) ** 0.5
    return [v / norm for v in vec]


class _FakeOllama(BaseHTTPRequestHandler):
    """The handful of Ollama routes the app uses: tags, show, ps, embed, chat (streaming and not)."""

    protocol_version = "HTTP/1.1"

    def log_message(self, *args: object) -> None:
        pass

    def _json(self, obj: object, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/api/tags":
            models = [
                {"name": CHAT_MODEL, "model": CHAT_MODEL, "size": 1_900_000_000, "details": {}},
                {
                    "name": "bge-m3:latest",
                    "model": "bge-m3:latest",
                    "size": 1_200_000_000,
                    "details": {},
                },
            ]
            self._json({"models": models})
        elif path == "/api/ps":
            self._json({"models": []})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        req = self._read()
        if path == "/api/embed":
            time.sleep(float(os.environ.get("FAKE_OLLAMA_EMBED_DELAY", "0")))
            inputs = req.get("input") or ""
            batch = inputs if isinstance(inputs, list) else [inputs]
            self._json(
                {"model": req.get("model"), "embeddings": [_unit_vector(str(t)) for t in batch]}
            )
        elif path == "/api/show":
            self._json(
                {
                    "details": {"family": "qwen2"},
                    "model_info": {"qwen2.context_length": 32768},
                    "capabilities": ["completion"],
                }
            )
        elif path == "/api/chat":
            self._chat(req)
        else:
            self._json({"error": "not found"}, 404)

    def _chat(self, req: dict) -> None:
        model = req.get("model") or CHAT_MODEL
        done = {
            "model": model,
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "done_reason": "stop",
            "total_duration": 1_000_000,
            "prompt_eval_count": 32,
            "eval_count": 48,
        }
        if req.get("stream") is False:
            self._json({**done, "message": {"role": "assistant", "content": ANSWER}})
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        words = ANSWER.split(" ")
        lines = [
            {"model": model, "message": {"role": "assistant", "content": w + " "}, "done": False}
            for w in words
        ]
        for obj in [*lines, done]:
            chunk = (json.dumps(obj) + "\n").encode()
            self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
        self.wfile.write(b"0\r\n\r\n")
        self.wfile.flush()


def start_fake_ollama() -> tuple[ThreadingHTTPServer, int]:
    """Start the fake Ollama on a free port; returns (server, port). Caller shuts it down."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), _FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True, name="fake-ollama").start()
    return server, server.server_address[1]


def _signal_name(returncode: int) -> str:
    if returncode >= 0:
        return f"exit status {returncode}"
    import signal

    try:
        return f"killed by {signal.Signals(-returncode).name}"
    except ValueError:
        return f"killed by signal {-returncode}"


def _read_log(path: Path) -> str:
    """The sidecar's output, tolerant of the console code page (on Windows it is not UTF-8)."""
    return path.read_text(encoding="utf-8", errors="replace")


def _backend_outlives_launcher(proc: subprocess.Popen, base: str, *, wait: float = 20.0) -> bool:
    """Kill the launcher process the way a quit does, then report whether the backend still answers.

    A one-file PyInstaller build is a bootloader process that runs the real backend as its child.
    Killing the bootloader must stop the backend too (anthill/desktop.py _exit_when_orphaned); on
    Windows nothing else does, so a backend that keeps serving here would pile up across relaunches."""
    import httpx

    proc.terminate()
    proc.wait(timeout=10)
    deadline = time.time() + wait
    while time.time() < deadline:
        try:
            httpx.get(f"{base}/login", timeout=1)
        except httpx.HTTPError:
            return False
        time.sleep(0.5)
    return True


def _prepare_real_ollama(
    data: Path, model: str, proc: subprocess.Popen, *, engine_wait: float = 1800.0
) -> str | None:
    """Wait for the backend to fetch and start its own Ollama, then pull ``model``. Returns None when the
    engine is serving and the model is installed, else why not."""
    import httpx

    exe = data / "ollama-runtime" / ("ollama.exe" if sys.platform == "win32" else "ollama")
    print(f"smoke-frozen-chat: waiting for the backend to download Ollama ({exe.parent}) ...", flush=True)
    deadline = time.time() + engine_wait
    while not exe.exists():
        if proc.poll() is not None:
            return "the backend exited while it should have been downloading Ollama"
        if time.time() > deadline:
            return "the backend never finished downloading Ollama"
        time.sleep(2)
    print("smoke-frozen-chat: Ollama is in place; waiting for it to answer ...", flush=True)
    deadline = time.time() + 180
    while True:
        try:
            if httpx.get("http://127.0.0.1:11434/api/tags", timeout=2).status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.time() > deadline:
            return "the downloaded Ollama never started answering"
        time.sleep(1)
    for attempt in (1, 2):  # the model registry is a network service: one retry
        print(f"smoke-frozen-chat: pulling {model} (attempt {attempt}) ...", flush=True)
        pulled = subprocess.run([str(exe), "pull", model], timeout=1800)
        if pulled.returncode == 0:
            return None
    return f"could not pull {model}"


def run_smoke(binary: str, *, timeout: float = 120.0, real_model: str | None = None) -> int:
    import httpx

    # The sidecar runs with its own throwaway working directory, so a relative path (the build script
    # passes dist/anthill-server) would no longer resolve for the child. Resolve it up front and fail
    # with a clear message, not a traceback, if it is missing.
    binary_path = Path(binary).resolve()
    if not (binary_path.is_file() and os.access(binary_path, os.X_OK)):
        print(
            f"smoke-frozen-chat: FAIL - sidecar binary not found or not executable: {binary_path}"
        )
        return 1
    binary = str(binary_path)

    if real_model:
        fake, fake_url = None, "http://localhost:11434"
    else:
        fake, fake_port = start_fake_ollama()
        fake_url = f"http://127.0.0.1:{fake_port}"
    data = Path(tempfile.mkdtemp(prefix="anthill-smoke-"))
    log_path = data / "server.log"
    env = dict(os.environ)
    env.update(
        {
            "ANTHILL_DB": str(data / "anthill.db"),
            "ANTHILL_HOME": str(data),
            "ANTHILL_WIKI_ROOT": str(data / "wikis"),
            "ANTHILL_WORKSPACE": str(data / "workspace"),
            "ANTHILL_ORG_WIKI": str(data / "org-wiki"),
            "ANTHILL_ENCRYPTION_KEY": base64.b64encode(secrets.token_bytes(32)).decode(),
            "ANTHILL_JWT_SECRET": secrets.token_urlsafe(32),
            "ANTHILL_BACKEND": "ollama",
            "ANTHILL_MODEL": real_model or CHAT_MODEL,
            "ANTHILL_NO_BROWSER": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    if not real_model:
        env["ANTHILL_BASE_URL"] = fake_url
        env["OLLAMA_HOST"] = fake_url  # the embedder reads this one
    for name in list(env):  # never let a developer's mail settings turn setup into a real send
        if name.startswith(("ANTHILL_SMTP", "RESEND")):
            env.pop(name)
    log = log_path.open("w")
    proc = subprocess.Popen([binary], cwd=str(data), env=env, stdout=log, stderr=subprocess.STDOUT)
    ok, detail = False, ""
    try:
        base = None
        deadline = time.time() + timeout
        while time.time() < deadline and proc.poll() is None:
            m = re.search(r"PORT=(\d+)", _read_log(log_path))
            if m:
                base = f"http://127.0.0.1:{m.group(1)}"
                try:
                    if httpx.get(f"{base}/login", timeout=2).status_code in (200, 302, 303):
                        break
                except httpx.HTTPError:
                    pass
            time.sleep(0.4)
        else:
            base = None
        if base is None:
            detail = f"server never became ready ({_signal_name(proc.poll()) if proc.poll() is not None else 'timeout'})"
            return _report(ok, detail, log_path, proc)
        if real_model:
            detail = _prepare_real_ollama(data, real_model, proc) or ""
            if detail:
                return _report(ok, detail, log_path, proc)
        client = httpx.Client(follow_redirects=False, timeout=timeout)
        password = secrets.token_urlsafe(18)
        email = "smoke@localhost.test"
        client.post(
            f"{base}/setup",
            data={
                "admin_email": email,
                "admin_password": password,
                "admin_name": "Smoke",
                "topology": "solo",
            },
        )
        client.post(f"{base}/login", data={"email": email, "password": password})
        # Point the org's model endpoint at the fake (it defaults to http://localhost:11434).
        client.post(f"{base}/settings", data={"ollama_url": fake_url})
        location = client.post(f"{base}/chat/new", data={"plane": "solo"}).headers.get(
            "location", ""
        )
        m = re.search(r"/chat/(\d+)", location)
        if not m:
            detail = "could not open a chat (sign-up or login failed)"
            return _report(ok, detail, log_path, proc)
        chunks: list[str] = []
        error = None
        try:
            with client.stream(
                "GET",
                f"{base}/chat/{m.group(1)}/stream",
                params={
                    "message": (
                        "Reply with the single word: ready"
                        if real_model
                        else "how do NDAs differ between Germany and Delaware?"
                    ),
                    "web": "false",
                },
            ) as resp:
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        event = json.loads(payload)
                    except ValueError:
                        continue
                    if isinstance(event, dict):
                        if "token" in event:
                            chunks.append(event["token"])
                        if event.get("error"):
                            error = event["error"]
        except httpx.HTTPError as e:
            error = f"stream dropped ({type(e).__name__})"
        answer = "".join(chunks)
        time.sleep(1.0)  # a crash while storing the answer in the cache lands just after the stream
        alive = proc.poll() is None
        if answer and not error and alive:
            ok, detail = True, f"answered {len(answer)} characters, sidecar still running"
            # Windows only for now: the macOS release build is left exactly as it was.
            if sys.platform == "win32" and _backend_outlives_launcher(proc, base):
                ok, detail = False, "the backend kept serving after its launcher was killed"
        else:
            why = error or "no answer"
            state = "sidecar still running" if alive else f"sidecar {_signal_name(proc.returncode)}"
            detail = f"chat failed: {why}; {state}"
        return _report(ok, detail, log_path, proc)
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=10)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            proc.kill()
        log.close()
        if fake:
            fake.shutdown()
        shutil.rmtree(data, ignore_errors=True)


def _report(ok: bool, detail: str, log_path: Path, proc: subprocess.Popen) -> int:
    print(f"smoke-frozen-chat: {'PASS' if ok else 'FAIL'} - {detail}")
    if not ok:
        try:
            tail = [ln for ln in _read_log(log_path).splitlines() if "SMTP" not in ln][-25:]
        except OSError:
            tail = []
        if tail:
            print("--- sidecar log tail ---")
            print("\n".join(tail))
    return 0 if ok else 1


if __name__ == "__main__":
    args = sys.argv[1:]
    real = None
    if len(args) == 3 and args[1] == "--real-ollama":
        real = args[2]
        args = args[:1]
    if len(args) != 1:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(run_smoke(args[0], timeout=300.0 if real else 120.0, real_model=real))
