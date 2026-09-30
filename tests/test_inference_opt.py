"""Inference optimization P0: prefix-stable prompt assembly + Ollama keep-warm + a measurement hook.

The reference (RAG) block is placed up front as a stable, user-role, fenced block and the question is
restated last, so [system + reference] is a byte-stable prefix the serving engine's KV/prefix cache
reuses turn-to-turn; the model stays warm between turns via keep_alive. See
engineering-plans/INFERENCE_OPTIMIZATION.md. Model-free.
"""

from anthill.inference.base import Message
from anthill.inference.ollama import OllamaBackend
from anthill.wiki import prompts

# ── prefix-stable prompt assembly ──────────────────────────────────────────────


def test_reference_block_is_up_front_and_question_is_last():
    history = [("user", "earlier q"), ("assistant", "earlier a")]
    msgs = prompts.answer_question(
        "Refund window is 45 days.", "what is the refund window?", history
    )
    roles = [m.role for m in msgs]
    # system, THEN the reference block, THEN the conversation, THEN the question - a stable growing prefix
    assert roles == ["system", "user", "user", "assistant", "user"]
    assert "REFERENCE MATERIAL" in msgs[1].content and "45 days" in msgs[1].content
    assert msgs[2].content == "earlier q" and msgs[3].content == "earlier a"
    assert msgs[-1].content == "QUESTION: what is the refund window?"


def test_no_reference_block_without_context():
    msgs = prompts.answer_question("", "hi there")
    assert [m.role for m in msgs] == ["system", "user"]
    assert "REFERENCE MATERIAL" not in msgs[-1].content
    assert msgs[-1].content == "QUESTION: hi there"


def test_reassert_reference_up_front_hardened_request_last():
    # #541: the hardened re-run keeps the reference block up front and puts the fenced request (the
    # injection "sandwich") as the final turn.
    msgs = prompts.answer_question("some untrusted text", "summarise it", reassert=True)
    assert "REFERENCE MATERIAL" in msgs[1].content  # reference up front
    assert msgs[-1].role == "user" and "<<<REQUEST" in msgs[-1].content  # fenced request last


def test_reference_block_is_user_role_not_system():
    # untrusted retrieved data must never sit in the system/instruction position (injection defence)
    msgs = prompts.answer_question("untrusted wiki text", "q")
    assert msgs[0].role == "system" and "REFERENCE MATERIAL" not in msgs[0].content
    assert msgs[1].role == "user" and "REFERENCE MATERIAL" in msgs[1].content


# ── keep-warm (keep_alive) ─────────────────────────────────────────────────────


def test_keep_alive_defaults_and_reaches_the_payload():
    be = OllamaBackend("http://x", "m")  # default from ANTHILL_KEEP_ALIVE (30m)
    assert be.keep_alive == "30m"
    payload = be._payload([Message("user", "hi")], temperature=0.2, model=None, stream=False)
    assert payload["keep_alive"] == "30m"


def test_keep_alive_explicit_override():
    be = OllamaBackend("http://x", "m", keep_alive="1h")
    payload = be._payload([Message("user", "hi")], temperature=0.2, model=None, stream=False)
    assert payload["keep_alive"] == "1h"


def test_keep_alive_omitted_when_empty(monkeypatch):
    monkeypatch.setenv("ANTHILL_KEEP_ALIVE", "")
    be = OllamaBackend("http://x", "m")
    payload = be._payload([Message("user", "hi")], temperature=0.2, model=None, stream=False)
    assert "keep_alive" not in payload  # empty -> use Ollama's own default, send nothing


# ── measurement hook ───────────────────────────────────────────────────────────


def test_last_stats_starts_empty():
    be = OllamaBackend("http://x", "m")
    assert (
        be.last_stats == {}
    )  # populated after a real non-streaming chat with Ollama's eval counts
