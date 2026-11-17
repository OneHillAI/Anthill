"""The scenarios. Each is a function that makes ONE run and returns named numbers; the protocol repeats it.

Every prompt starts with a unique token, so the engine's prefix cache cannot skip the work being measured
(a warm prompt is not a prefill measurement: the same prompt run twice reuses nearly all of it). A cold run
that the engine says hit the cache anyway is rejected. Prompt depth is read back from the engine, not assumed.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path

from anthill.inference.stats import TurnStats

from .protocol import Measurement, Metric, Protocol, RunInvalid, measure

NUM_CTX = 8192
DEPTH_TOLERANCE = 0.10  # a prompt more than this far from its target depth does not count
CACHE_TOLERANCE = 0.05  # a cold run with more than this share cached does not count ...
CACHE_ALLOWANCE_TOKENS = (
    8  # ... unless it is only the chat template's fixed header, which every prompt shares
)
MIN_DECODE_TOKENS = 64  # an answer shorter than this says little about decode speed
WIKI_PROMPT = Path(__file__).resolve().parents[1] / "fixtures" / "scoreboard_wiki_prompt.json"
FILLER = (
    "The harbour office keeps a ledger of every crate that crosses the quay, and a second ledger for the crates "
    "that never arrive. "
)


def nonce() -> str:
    return uuid.uuid4().hex[:12]


def _user(text: str) -> list[dict]:
    return [{"role": "user", "content": text}]


def _tagged(text: str, tag: str) -> list[dict]:
    """A system message that starts with a unique token, then the user's text. The unique token comes before
    anything the model's template adds (a default system text is identical on every call and would be cached)."""
    return [{"role": "system", "content": f"[{tag}] You are a helpful assistant."}, *_user(text)]


def prefill_tok_s(stats: TurnStats) -> float | None:
    """Prompt tokens actually processed per second (the cached part excluded)."""
    if not stats.prefill_tokens or not stats.prefill_ms:
        return None
    return stats.prefill_tokens / (stats.prefill_ms / 1000)


def check_cold(stats: TurnStats) -> None:
    allowed = max(CACHE_ALLOWANCE_TOKENS, CACHE_TOLERANCE * (stats.prompt_tokens or 0))
    if (stats.cached_tokens or 0) > allowed:
        raise RunInvalid(
            f"a cold run reused {stats.cached_tokens} of {stats.prompt_tokens} prompt tokens"
        )


def check_depth(stats: TurnStats, target: int) -> None:
    if not stats.prompt_tokens:
        raise RunInvalid("the engine did not report the prompt length")
    if abs(stats.prompt_tokens - target) / target > DEPTH_TOLERANCE:
        raise RunInvalid(f"prompt was {stats.prompt_tokens} tokens, not about {target}")


def check_decode(stats: TurnStats) -> None:
    if (stats.decode_tokens or 0) < MIN_DECODE_TOKENS:
        raise RunInvalid(f"the answer stopped after {stats.decode_tokens} tokens")


def calibrate(engine, model: str) -> float:
    """Characters per prompt token for this model, from two prompts of different length (the template overhead
    cancels out). Used only to build a prompt near a target depth; the real depth is read back."""
    short, long = FILLER * 2, FILLER * 20  # the same prose the depth prompts are made of
    a = engine.chat(model, _user(short), num_ctx=NUM_CTX, num_predict=1)
    b = engine.chat(model, _user(long), num_ctx=NUM_CTX, num_predict=1)
    if not a.prompt_tokens or not b.prompt_tokens or b.prompt_tokens <= a.prompt_tokens:
        raise RuntimeError("could not calibrate the prompt length from the engine's counts")
    return (len(long) - len(short)) / (b.prompt_tokens - a.prompt_tokens)


def depth_messages(target_tokens: int, chars_per_token: float, tag: str) -> list[dict]:
    """About ``target_tokens`` of filler text and an instruction to write a long answer, behind a unique tag."""
    chars = max(0, int(target_tokens * chars_per_token))
    if not chars:
        return _tagged("Write a long story about a lighthouse keeper.", tag)
    filler = (FILLER * (chars // len(FILLER) + 1))[:chars]
    ask = "Now write at least 300 new words continuing the story, with no preamble."
    return _tagged(f"{filler}\n\n{ask}", tag)


def run_depth(engine, model: str, target: int, chars_per_token: float) -> dict[str, float | None]:
    stats = engine.chat(
        model, depth_messages(target, chars_per_token, nonce()), num_ctx=NUM_CTX, num_predict=128
    )
    if target:
        check_depth(stats, target)
    check_cold(stats)
    check_decode(stats)
    return {
        "prompt_tokens": float(stats.prompt_tokens or 0),
        "prefill_tok_s": prefill_tok_s(stats),
        "decode_tok_s": stats.decode_tokens_per_s,
        "first_word_ms": _f(stats.first_token_ms),
    }


def _f(value: int | None) -> float | None:
    return None if value is None else float(value)


def wiki_messages(tag: str | None = None) -> list[dict]:
    """The fixed wiki prompt; with ``tag``, a unique first token is put at the start of the system message."""
    messages = json.loads(WIKI_PROMPT.read_text())["messages"]
    if tag:
        messages = [dict(m) for m in messages]
        messages[0]["content"] = f"[{tag}] " + messages[0]["content"]
    return messages


def _wiki_numbers(stats: TurnStats) -> dict[str, float | None]:
    return {
        "prompt_tokens": float(stats.prompt_tokens or 0),
        "first_word_ms": _f(stats.first_token_ms),
        "prefill_ms": _f(stats.prefill_ms),
        "prefill_tok_s": prefill_tok_s(stats),
        "cached_tokens": _f(stats.cached_tokens),
    }


def run_wiki_cold(engine, model: str) -> dict[str, float | None]:
    stats = engine.chat(model, wiki_messages(nonce()), num_ctx=NUM_CTX, num_predict=48)
    check_cold(stats)
    return _wiki_numbers(stats)


def run_wiki_warm(engine, model: str) -> dict[str, float | None]:
    """The same prompt twice with a fresh tag: the first call fills the cache, the second is measured."""
    messages = wiki_messages(nonce())
    engine.chat(model, messages, num_ctx=NUM_CTX, num_predict=1)
    stats = engine.chat(model, messages, num_ctx=NUM_CTX, num_predict=48)
    return _wiki_numbers(stats)


def run_cold_load(engine, model: str) -> dict[str, float | None]:
    engine.unload(model)
    stats = engine.chat(model, _tagged("Say hi.", nonce()), num_ctx=NUM_CTX, num_predict=8)
    return {"load_ms": _f(stats.load_ms), "first_word_ms": _f(stats.first_token_ms)}


def run_memory(engine, model: str, swap_reader: Callable[[], int | None], turns: int = 20) -> dict:
    """Load the model, run ``turns`` short turns, and report what the engine holds and whether the machine
    swapped. One reading, not repeated: the protocol's spread does not apply."""
    before = swap_reader()
    for _ in range(turns):
        engine.chat(model, _tagged("Name three colours.", nonce()), num_ctx=NUM_CTX, num_predict=32)
    after = swap_reader()
    entry = next((m for m in engine.running() if m.get("name") in (model, f"{model}:latest")), {})
    return {
        "allocated_gb": _gb(entry.get("size")),
        "in_gpu_memory_gb": _gb(entry.get("size_vram")),
        "swap_outs": None if before is None or after is None else float(after - before),
        "turns": float(turns),
    }


def _gb(value: object) -> float | None:
    return round(value / 1e9, 3) if isinstance(value, int) and not isinstance(value, bool) else None


def single(values: dict[str, float | None]) -> Measurement:
    """A one-reading measurement: every present number is ``ok``, an absent one is ``missing``."""
    return Measurement(
        {
            k: Metric((v,) if v is not None else (), v, None, "ok" if v is not None else "missing")
            for k, v in values.items()
        }
    )


def run_all(
    engine,
    model: str,
    protocol: Protocol,
    swap_reader: Callable[[], int | None],
    *,
    only: set[str] | None = None,
    sleep: Callable[[float], None] | None = None,
    log: Callable[[str], None] = lambda _s: None,
) -> dict[str, dict]:
    """Every scenario for one model: name -> {"params": ..., "measurement": Measurement}."""
    kw = {} if sleep is None else {"sleep": sleep}
    cpt = calibrate(engine, model)
    plan: list[tuple[str, dict, Callable[[], dict[str, float | None]]]] = [
        ("depth_0", {"target_tokens": 0}, lambda: run_depth(engine, model, 0, cpt)),
        ("depth_4k", {"target_tokens": 4000}, lambda: run_depth(engine, model, 4000, cpt)),
        ("wiki_cold", {"prompt": WIKI_PROMPT.name}, lambda: run_wiki_cold(engine, model)),
        ("wiki_warm", {"prompt": WIKI_PROMPT.name}, lambda: run_wiki_warm(engine, model)),
        ("cold_load", {}, lambda: run_cold_load(engine, model)),
    ]
    out: dict[str, dict] = {}
    for name, params, run in plan:
        if only is None or name in only:
            log(f"{model}: {name}")
            out[name] = {
                "params": {**params, "num_ctx": NUM_CTX},
                "measurement": measure(run, protocol, **kw),
            }
    if only is None or "memory_swap" in only:
        log(f"{model}: memory_swap")
        values = run_memory(engine, model, swap_reader)
        out["memory_swap"] = {"params": {"num_ctx": NUM_CTX}, "measurement": single(values)}
    return out
