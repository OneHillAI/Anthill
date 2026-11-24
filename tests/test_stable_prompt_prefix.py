"""The start of the prompt is the same from turn to turn: system, the guidance block (principles and profile), the earlier turns,
then what was found for THIS question, then the question. Order only: the text of every block is as it was. Model-free."""

import pytest

import anthill.wiki.ask as ask_mod
from anthill.inference.fit import cost
from anthill.wiki import prompts
from anthill.wiki.workspace import Workspace
from tests.test_answers_use_document_text import embeds  # noqa: F401

PRINCIPLES = "Always answer in plain words. Prefer short sentences."
PROFILE = "Name: Sam. Runs a bakery. Likes metric units."
MEMORY = "Sam's oven is a deck oven."
HISTORY = [
    ("user", "what do we sell?"),
    ("assistant", "Bread and cakes."),
    ("user", "and on Sundays?"),
    ("assistant", "Only bread."),
]


def _roles(msgs):
    return [m.role for m in msgs]


def _guidance():
    return ask_mod._guidance_block(profile=PROFILE, principles=PRINCIPLES)


def test_the_order_with_guidance_history_and_a_reference():
    msgs = prompts.answer_question("PAGE TEXT", "what next?", HISTORY, guidance=_guidance())
    assert _roles(msgs) == [
        "system",
        "user",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
        "user",
    ]
    assert (
        "Standing principles" in msgs[1].content and "About the user" in msgs[1].content
    )  # guidance first
    assert [m.content for m in msgs[2:6]] == [c for _, c in HISTORY]  # then the earlier turns
    assert (
        "REFERENCE MATERIAL" in msgs[6].content and "PAGE TEXT" in msgs[6].content
    )  # then this question's reference
    assert msgs[7].content == "QUESTION: what next?"  # the question last


def test_the_order_without_guidance_or_without_history_or_without_a_reference():
    assert _roles(prompts.answer_question("ref", "q", HISTORY[:2])) == [
        "system",
        "user",
        "assistant",
        "user",
        "user",
    ]
    assert _roles(prompts.answer_question("ref", "q", guidance=_guidance())) == [
        "system",
        "user",
        "user",
        "user",
    ]
    assert _roles(prompts.answer_question("", "q", HISTORY[:2], guidance=_guidance())) == [
        "system",
        "user",
        "user",
        "assistant",
        "user",
    ]
    assert _roles(prompts.answer_question("", "q")) == ["system", "user"]


def test_the_guidance_block_is_absent_when_there_is_none_and_its_text_is_unchanged():
    assert ask_mod._guidance_block() == ""
    assert not any("Standing principles" in m.content for m in prompts.answer_question("ref", "q"))
    # the same text and labels, in the same order, as the block it replaces
    old = ask_mod._decorate_context("", profile=PROFILE, principles=PRINCIPLES)
    assert old == _guidance() + "\n\n---\n\n"
    assert ask_mod._guidance_block(
        principles=PRINCIPLES
    ) + "\n\n---\n\n" == ask_mod._decorate_context("", principles=PRINCIPLES)
    assert ask_mod._guidance_block(profile=PROFILE) + "\n\n---\n\n" == ask_mod._decorate_context(
        "", profile=PROFILE
    )


def test_guidance_and_reference_are_fenced_untrusted_user_blocks_never_the_system_message():
    msgs = prompts.answer_question("PAGE TEXT", "q", HISTORY, guidance=_guidance())
    system = msgs[0].content
    assert (
        "Standing principles" not in system and "PAGE TEXT" not in system and PROFILE not in system
    )
    for block in (msgs[1], msgs[6]):
        assert (
            block.role == "user"
            and "untrusted" in block.content
            and "<<<BEGIN_UNTRUSTED_CONTEXT" in block.content
        )
        assert block.content.endswith("END_UNTRUSTED_CONTEXT>>>")


def test_the_hijack_retry_still_carries_the_guidance_and_the_reference_and_ends_with_the_request():
    msgs = prompts.answer_question("PAGE TEXT", "q", HISTORY, reassert=True, guidance=_guidance())
    fenced = [m for m in msgs if "BEGIN_UNTRUSTED_CONTEXT" in m.content]
    assert (
        len(fenced) == 1
        and "Standing principles" in fenced[0].content
        and "PAGE TEXT" in fenced[0].content
    )
    assert msgs[-1].role == "user" and "<<<REQUEST" in msgs[-1].content
    assert not any(
        m.content in [c for _, c in HISTORY] for m in msgs
    )  # history is still dropped on a retry


def test_the_fit_to_the_window_counts_the_guidance_and_shortens_the_reference_and_history_as_before():
    big_guidance = "G" * 3000
    msgs = prompts.answer_question(
        "R" * 20000,
        "q",
        [("user", "u" * 6000), ("assistant", "a" * 6000)],
        window=2048,
        guidance=big_guidance,
    )
    assert big_guidance in msgs[1].content  # the guidance is fixed cost: never shortened
    assert sum(cost(m.content) for m in msgs) < 2048 * 4  # the rest was shortened to fit
    roomy = prompts.answer_question("short ref", "q", HISTORY, window=32768, guidance=_guidance())
    plain = prompts.answer_question("short ref", "q", HISTORY, window=0, guidance=_guidance())
    assert [m.content for m in roomy] == [
        m.content for m in plain
    ]  # a prompt that fits is not shortened


class _Capture:
    """Records the messages of each call and answers with a fixed sentence."""

    model = "fake"

    def __init__(self):
        self.calls = []

    def chat(self, messages, **k):
        self.calls.append(list(messages))
        return "A fixed answer about the bakery."

    def chat_stream(self, messages, **k):
        self.calls.append(list(messages))
        yield "A fixed answer about the bakery."


def _wiki(tmp_path):
    ws = Workspace(tmp_path / "w", scope="personal")
    ws.init()
    ws.write_page(
        "sourdough", "# Sourdough\n\nThe sourdough loaf proves for twelve hours at the bakery.\n"
    )
    ws.write_page(
        "croissant",
        "# Croissant\n\nThe croissant dough is laminated with eighty grams of butter.\n",
    )
    return ws


@pytest.mark.usefixtures("embeds")
@pytest.mark.parametrize("stream", [True, False])
def test_ask_and_ask_stream_give_the_model_the_new_order_and_an_identical_start_across_turns(
    tmp_path, stream
):
    ws = _wiki(tmp_path)
    be = _Capture()

    def turn(question, history):
        kw = {
            "history": history,
            "profile": PROFILE,
            "principles": PRINCIPLES,
            "memory_context": MEMORY,
        }
        if stream:
            list(ask_mod.ask_stream(ws, question, be, **kw))
        else:
            ask_mod.ask(ws, question, be, **kw)
        return be.calls[-1]

    first = turn("How long does the sourdough loaf prove?", HISTORY[:2])
    second = turn(
        "How much butter is in the croissant dough?", HISTORY
    )  # other pages, one more exchange
    for msgs in (first, second):
        assert (
            _roles(msgs)[0] == "system"
            and msgs[1].role == "user"
            and "Standing principles" in msgs[1].content
        )
        assert msgs[-1].content.startswith("QUESTION: ")
        ref = msgs[-2].content
        assert "REFERENCE MATERIAL" in ref and ref.index(MEMORY) < ref.index(
            "dough"
        )  # memory, then the pages
    assert first[1].content == second[1].content  # the guidance block is byte-identical
    shared = first[
        : 2 + len(HISTORY[:2])
    ]  # system, guidance and the earlier turns both prompts start with
    assert [m.content for m in second[: len(shared)]] == [m.content for m in shared]
    assert first[-2].content != second[-2].content  # only what was found for the question differs
    assert "sourdough" in first[-2].content.lower() and "croissant" in second[-2].content.lower()


@pytest.mark.usefixtures("embeds")
def test_a_turn_with_no_guidance_has_no_guidance_block(tmp_path):
    ws = _wiki(tmp_path)
    be = _Capture()
    ask_mod.ask(ws, "How long does the sourdough loaf prove?", be, history=HISTORY[:2])
    msgs = be.calls[-1]
    assert not any(
        "Standing principles" in m.content or "About the user" in m.content for m in msgs
    )
    assert _roles(msgs) == ["system", "user", "assistant", "user", "user"]
