from anthill.contribute.interaction import (
    DISCLOSURE,
    answer_reply,
    intake_reply,
    looks_like_idea,
)


def test_ideas_are_detected():
    for msg in [
        "Feature request: let admins export the wiki to PDF",
        "idea: dark mode for the dashboard",
        "It would be great if tasks could run on a cron",
        "please add a Slack digest",
        "Bug: the reset email never arrives",
    ]:
        assert looks_like_idea(msg), msg


def test_questions_are_not_ideas():
    for msg in [
        "How do I connect my own model?",
        "what models does Anthill run?",
        "Does my data leave the org?",
        "where is the audit log?",
        "",
    ]:
        assert not looks_like_idea(msg), msg


def test_a_question_shape_wins_even_with_an_idea_word():
    # ends in a question mark -> answer it, do not draft a spec
    assert not looks_like_idea("could you add a feature to do X?")


def test_intake_reply_ready_discloses_and_says_human_decides():
    r = intake_reply(title="Export the wiki to PDF", ready=True)
    assert r.startswith(DISCLOSURE)
    assert "Export the wiki to PDF" in r
    assert "human" in r.lower()


def test_intake_reply_needs_detail_asks_for_more():
    r = intake_reply(title="Do a thing", ready=False, needs_detail="what problem it solves")
    assert r.startswith(DISCLOSURE)
    assert "what problem it solves" in r


def test_answer_reply_prefixes_disclosure():
    r = answer_reply("The org runs qwen2.5:3b locally.")
    assert r.startswith(DISCLOSURE)
    assert "qwen2.5:3b" in r
