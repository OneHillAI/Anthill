"""Robust JSON drafting: small local models wrap JSON in ```json fences, return arrays/nested
objects where strings were asked for, or (without a format hint) truncate so the result won't parse.
The shared helpers + the skill/task drafters must produce clean string fields instead of silently
echoing the raw input (the QA finding where "Draft it" just copied the description).
"""

from anthill.agent.skills import draft_skill, seed_example
from anthill.common.jsonchat import coerce_str, extract_json, json_chat


def test_extract_json_handles_fence_and_garbage():
    assert extract_json('```json\n{"name": "X", "instructions": "go"}\n```') == {
        "name": "X",
        "instructions": "go",
    }
    assert extract_json('here you go: {"a": 1} cheers') == {"a": 1}
    assert extract_json("not json at all") == {}
    assert extract_json("") == {}


def test_coerce_str_flattens_list_and_dict():
    assert coerce_str(["a", "b"]) == "a\nb"
    assert coerce_str({"Step 1": "go", "Step 2": "stop"}) == "Step 1: go\nStep 2: stop"
    assert coerce_str("  hi  ") == "hi"
    assert coerce_str(None) == ""


def test_json_chat_prefers_format_then_falls_back():
    class _Fmt:
        def chat(self, messages, *, fmt=""):
            return f"fmt={fmt}"

    assert json_chat(_Fmt(), []) == "fmt=json"

    class _NoFmt:
        def chat(self, messages):
            return "plain"

    assert json_chat(_NoFmt(), []) == "plain"


def test_draft_skill_parses_fenced_nested_json():
    """qwen-style output: fenced, when_to_use as an array, instructions as a nested object."""

    class _Backend:
        def chat(self, messages, *, fmt=""):
            return (
                '```json\n{"name": "Weekly update", '
                '"description": "A weekly investor email.", '
                '"when_to_use": ["each Friday"], '
                '"instructions": {"Intro": "greet", "Body": "metrics"}}\n```'
            )

    out = draft_skill("weekly investor update", _Backend())
    assert out["name"] == "Weekly update"
    assert out["description"] == "A weekly investor email."
    assert out["when_to_use"] == "each Friday"
    assert "Intro: greet" in out["instructions"] and "Body: metrics" in out["instructions"]


def test_draft_skill_falls_back_cleanly_on_garbage():
    class _Backend:
        def chat(self, messages, *, fmt=""):
            return "sorry, I can't do that"

    out = draft_skill("my plain description", _Backend())
    assert out["name"] == "my plain description"
    assert out["instructions"] == "my plain description"


def test_seed_example_is_idempotent(tmp_path):
    d = tmp_path / "skills"
    assert seed_example(str(d)) is True
    assert (d / "effective-wiki-page" / "SKILL.md").exists()
    assert seed_example(str(d)) is False  # already has a skill - don't double-seed
