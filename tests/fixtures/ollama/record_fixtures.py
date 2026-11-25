"""Record real replies from a LOCAL Ollama into tests/fixtures/ollama/<version>/ (#227).

Run by hand, never by the test suite:  [OLLAMA_URL=http://localhost:11436] python tests/fixtures/ollama/record_fixtures.py [chat-model] [vision-model] [embed-model]

It only reads and generates with models that are already installed: no pull, no delete. The embedding is cut to its
first 8 values, tokenizer arrays and the Modelfile, template, built-in instructions and licence text in /api/show are dropped, and home folders in paths become /Users/user. It writes
provenance.json with the version, the date and `recorded` for every file it wrote.
"""

from __future__ import annotations

import base64
import datetime
import json
import os
import platform
import re
import struct
import sys
import urllib.error
import urllib.request
import zlib
from pathlib import Path

BASE = os.environ.get(
    "OLLAMA_URL", "http://localhost:11434"
)  # a second engine on its own port: OLLAMA_URL=http://localhost:11436


def _call(method: str, path: str, body: dict | None = None) -> tuple[int, bytes]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _scrub(value: object) -> object:
    """Replace a user's home folder in any string with /Users/user: the replies hold local paths."""
    if isinstance(value, str):
        return re.sub(r"/(Users|home)/[^/\s\"']+", r"/\1/user", value)
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def _png(size: int = 32) -> str:
    """A small solid red PNG, base64: enough for a vision model to accept an image."""
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * size for _ in range(size))

    def chunk(kind: bytes, payload: bytes) -> bytes:
        body = kind + payload
        return struct.pack(">I", len(payload)) + body + struct.pack(">I", zlib.crc32(body))

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    return base64.b64encode(png).decode()


def main() -> None:
    chat = sys.argv[1] if len(sys.argv) > 1 else "qwen2.5:3b"
    vision = sys.argv[2] if len(sys.argv) > 2 else "granite3.2-vision:2b"
    embed = sys.argv[3] if len(sys.argv) > 3 else "bge-m3"
    _, raw = _call("GET", "/api/version")
    version = json.loads(raw)["version"]
    out = Path(__file__).parent / version
    out.mkdir(exist_ok=True)
    written: dict[str, str] = {}

    def save(name: str, payload: object) -> None:
        payload = _scrub(payload)
        path = out / name
        if name.endswith(".ndjson"):
            path.write_text("\n".join(json.dumps(p) for p in payload) + "\n")  # type: ignore[attr-defined]
        else:
            path.write_text(json.dumps(payload, indent=1) + "\n")
        written[name] = "recorded"

    opts = {"temperature": 0, "num_ctx": 4096, "num_predict": 24}
    msgs = [{"role": "user", "content": "Say hi in three words."}]
    save("version.json", {"version": version})
    _, raw = _call(
        "POST", "/api/chat", {"model": chat, "messages": msgs, "stream": False, "options": opts}
    )
    save("chat.json", json.loads(raw))
    _, raw = _call(
        "POST", "/api/chat", {"model": chat, "messages": msgs, "stream": True, "options": opts}
    )
    save(
        "chat_stream.ndjson",
        [json.loads(line) for line in raw.decode().splitlines() if line.strip()],
    )
    tool = {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the weather for a city",
            "parameters": {
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
        },
    }
    ask = [{"role": "user", "content": "What is the weather in Paris? Use the tool."}]
    _, raw = _call(
        "POST",
        "/api/chat",
        {"model": chat, "messages": ask, "tools": [tool], "stream": False, "options": opts},
    )
    save("chat_tools.json", json.loads(raw))
    see = [{"role": "user", "content": "What colour is this image? One word.", "images": [_png()]}]
    _, raw = _call(
        "POST", "/api/chat", {"model": vision, "messages": see, "stream": False, "options": opts}
    )
    save("chat_vision.json", json.loads(raw))
    _, raw = _call("POST", "/api/embed", {"model": embed, "input": "hello"})
    emb = json.loads(raw)
    emb["embeddings"] = [v[:8] for v in emb.get("embeddings", [])]
    save("embed.json", emb)
    for tag, name in ((chat, "show_chat.json"), (vision, "show_vision.json")):
        _, raw = _call("POST", "/api/show", {"model": tag})
        shown = json.loads(raw)
        info = shown.get("model_info", {})
        shown["model_info"] = {k: v for k, v in info.items() if not k.startswith("tokenizer.")}
        shown.pop("tensors", None)
        for prompt_text in ("license", "modelfile", "template", "system", "parameters", "messages"):
            shown.pop(
                prompt_text, None
            )  # the model's own prompt and licence text: Anthill reads none
        save(name, shown)
    _, raw = _call("GET", "/api/tags")
    save("tags.json", json.loads(raw))
    _, raw = _call("GET", "/api/ps")
    save("ps.json", json.loads(raw))
    status, raw = _call("POST", "/api/show", {"model": "no-such-model:1b"})
    save("show_missing.json", {"status": status, "body": json.loads(raw)})

    kept: dict[str, object] = {}  # docs-labelled files written by hand for this version stay listed
    old = out / "provenance.json"
    if old.exists():
        kept = {
            k: v
            for k, v in json.loads(old.read_text()).get("files", {}).items()
            if isinstance(v, dict)
        }
    written.update(kept)  # type: ignore[arg-type]
    (out / "provenance.json").write_text(
        json.dumps(
            {
                "version": version,
                "recorded_on": datetime.date.today().isoformat(),
                "machine": f"{platform.machine()} {platform.system()} {platform.release()}",
                "models": {"chat": chat, "vision": vision, "embed": embed},
                "files": written,
            },
            indent=1,
        )
        + "\n"
    )
    print("wrote", len(written), "files to", out)


if __name__ == "__main__":
    main()
