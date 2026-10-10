"""Text patterns that read member-supplied text stay linear (docs/specs/bounded-text-patterns.md).

A chat message, a wiki page, a skill or an uploaded document is text a member controls. A pattern whose
cost grows with the square of its length lets one long request keep the server busy for seconds, so each
one is bounded. Each test feeds very long hostile input and requires an answer in well under a second; on
the unbounded patterns the same inputs take many seconds. The behaviour on ordinary input is unchanged.
"""

import time
from pathlib import Path

import pytest

from anthill.agent import intent
from anthill.agent.skills import Skill, parse_skill_md, validate_skill
from anthill.common.text import WIKILINK_RE
from anthill.wiki import okf, review

LIMIT_SECONDS = 1.0


def fast(fn, *args):
    start = time.perf_counter()
    out = fn(*args)
    assert time.perf_counter() - start < LIMIT_SECONDS
    return out


# ── chat intent patterns ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "hostile",
    [
        "note," + " " * 60_000,  # a lead-in and then only whitespace
        "remember this" + " " * 60_000,
        "please " * 8_000 + "note",
        "  " * 30_000 + "remember: x",
    ],
)
def test_remember_detection_is_fast_on_long_whitespace(hostile):
    fast(intent.looks_like_remember, hostile)
    fast(intent.parse_remember, hostile)


def test_remember_detection_still_reads_ordinary_messages():
    assert intent.looks_like_remember("remember this: the build is green")
    assert intent.looks_like_remember("  Please note that the office closes at five")
    assert not intent.looks_like_remember("do you remember when we met?")
    assert intent.parse_remember("note: ship it on Friday") == "ship it on Friday"
    assert intent.parse_remember("remember, we use UTC") == "we use UTC"


@pytest.mark.parametrize(
    "hostile",
    [
        "how do" + " " * 60_000,
        "how do " * 10_000,
        "how do we " + "x " * 30_000,
        "how can " + "relatively " * 6_000,
    ],
)
def test_deep_question_detection_is_fast_on_long_input(hostile):
    fast(intent.looks_deep, hostile)


def test_deep_question_gap_is_limited_to_200_characters():
    """The gap between the opening and the verb is bounded; a longer one no longer counts as one question."""
    assert intent.looks_deep("how do a" + "x" * 199 + " relate")  # 200 characters between
    assert not intent.looks_deep("how do a" + "x" * 200 + " relate")  # 201
    assert not intent.looks_deep("how do relate")  # still needs something between, as before


def test_deep_question_detection_still_recognises_a_relationship_question():
    assert intent.looks_deep("how does the billing service relate to the ledger?")
    assert intent.looks_deep("How would a price change affect our margins")
    assert intent.looks_deep("compare plan A and plan B")
    assert not intent.looks_deep("what time is the meeting")


# ── wiki links ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "hostile",
    [
        "[[" * 40_000,
        "[[" + "a" * 100_000,
        "[[a" * 30_000,
        "[[" + "[" * 100_000 + "]]",
        "[[x\n" * 20_000,
    ],
)
def test_the_wiki_link_pattern_is_fast_on_long_input(hostile):
    fast(WIKILINK_RE.findall, hostile)


def test_the_wiki_link_pattern_still_finds_ordinary_links():
    text = "See [[Billing model]] and [[invoices]], not [[ ]] nor [[a\nb]] nor [[x [y]]."
    assert WIKILINK_RE.findall(text) == [
        "Billing model",
        "invoices",
        " ",
    ]  # no nested brackets, one line
    assert WIKILINK_RE.findall("[[" + "a" * 200 + "]]") == ["a" * 200]
    assert WIKILINK_RE.findall("[[" + "a" * 201 + "]]") == []  # longer than any page title


def test_every_reader_of_wiki_links_uses_the_bounded_pattern():
    """The page review, the OKF bundle writer and the skill validator share the one bounded pattern."""
    assert okf._WIKILINK is WIKILINK_RE
    assert not _regexes_for_wiki_links_outside_text_py()


def _regexes_for_wiki_links_outside_text_py():
    """Every pattern in the package that mentions "[[", other than the shared one in common/text.py."""
    package = Path(intent.__file__).parent.parent
    return _wiki_link_patterns(package, skip=package / "common" / "text.py")


def _wiki_link_patterns(package, skip=None):
    import ast

    found = []
    for path in sorted(package.rglob("*.py")):
        if path == skip:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "re"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
                and ("[[" in node.args[0].value or "\\[\\[" in node.args[0].value)
            ):
                found.append(f"{path.name}:{node.lineno}")
    return found


def test_the_guard_for_stray_wiki_link_patterns_reports_one(tmp_path):
    (tmp_path / "bad.py").write_text('import re\nX = re.compile(r"\\[\\[([^\\]]+)\\]\\]")\n')
    (tmp_path / "fine.py").write_text('import re\nY = re.compile(r"[a-z]+")\n')
    assert _wiki_link_patterns(tmp_path) == ["bad.py:2"]


def test_page_review_is_fast_on_a_page_of_open_brackets(tmp_path):
    from anthill.wiki.workspace import Workspace

    ws = Workspace(tmp_path / "ws", scope="personal")
    ws.init()

    class _NoModel:
        def chat(self, *a, **k):
            raise RuntimeError("no model")

    out = fast(review.outline_change, _NoModel(), ws, "hostile", "[[" * 40_000)
    assert out is not None


def test_skill_validation_is_fast_on_hostile_instructions():
    sk = Skill(
        name="hostile",
        slug="hostile",
        description="d",
        when_to_use="w",
        instructions="[[" * 40_000,
    )
    result = fast(validate_skill, sk)
    assert isinstance(result, dict) and "errors" in result


def test_skill_validation_still_flags_a_reference_to_a_missing_asset():
    sk = Skill(
        name="ok",
        slug="ok",
        description="A skill.",
        when_to_use="when asked",
        instructions="Use [[checklist.md]] and [[other.md]].",
        assets=["checklist.md"],
    )
    warnings = validate_skill(sk)["warnings"]
    assert any("other.md" in w and "checklist.md" not in w.split(":")[1] for w in warnings)


@pytest.mark.parametrize(
    "hostile",
    [
        "[" * 80_000,
        "[a](" * 20_000,
        "[](" + "x" * 80_000,
        "[a]" * 30_000,
        "[a](b" * 20_000,
        "[" + "a" * 80_000,
        "](" * 40_000,
    ],
)
def test_the_markdown_link_pattern_is_fast_on_long_input(hostile):
    from anthill.common.text import outbound_links

    fast(outbound_links, hostile)


def test_outbound_links_still_find_wiki_pages_through_markdown_links():
    from anthill.common.text import outbound_links

    text = "See [the billing page](billing.md), [docs](sub/ledger.md), [web](https://x.example/a.md), [[Invoices]]."
    assert outbound_links(text) == {"billing", "ledger", "Invoices"}


# ── skill front matter ──────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "hostile",
    [
        "---" + "\n" * 80_000,
        "---" + " \n" * 40_000,
        "---" + "\r\n" * 40_000,
        "---\n" + "a: b\n" * 40_000,
    ],
)
def test_skill_front_matter_parsing_is_fast_on_long_input(hostile):
    fast(parse_skill_md, hostile)


def test_skill_front_matter_still_parses_ordinary_skills():
    text = "---\nname: demo\ndescription: A demo\n---\nBody text\n"
    sk = parse_skill_md(text, slug="demo")
    assert sk.description == "A demo" and "Body text" in sk.instructions
    crlf = "---\r\nname: demo\r\ndescription: A demo\r\n---\r\nBody\r\n"
    assert parse_skill_md(crlf, slug="demo").description == "A demo"
    trailing = "---  \nname: demo\ndescription: A demo\n---\nBody\n"
    assert parse_skill_md(trailing, slug="demo").description == "A demo"


# ── through the member-facing route ─────────────────────────────────────────────


def test_the_skill_validation_route_answers_fast_to_hostile_text(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    from anthill.web import db as db_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    user = User(org_id=org.id, email="m@a.com", role="member", active=True)
    s.add(user)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(user.id, org.id, "member"))
    start = time.perf_counter()
    r = c.post(
        "/skills/validate",
        data={"name": "x", "description": "d", "when_to_use": "w", "instructions": "[[" * 40_000},
    )
    assert r.status_code == 200
    assert time.perf_counter() - start < 3 * LIMIT_SECONDS
