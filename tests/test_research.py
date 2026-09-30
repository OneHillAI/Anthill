"""Deep Research -> Wiki: the research engine (search -> synthesize -> verify -> cited markdown) and
the /research route that files the draft into the review queue. Web + model are stubbed."""

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.research import ResearchError, research_topic, verify_report
from anthill.web import db
from anthill.web.db import Organization, User


class _FakeBackend:
    def __init__(self):
        self.last_messages = None

    def chat(self, messages, **kw):
        self.last_messages = list(messages)
        return (
            "One-line summary.\n\n- key point one\n- key point two\n\n## Details\nMore detail here."
        )


def _fake_search(query, max_results):
    return [
        SimpleNamespace(
            title="Source A", url="https://a.example/x", snippet="a", body="body about the topic"
        ),
        SimpleNamespace(title="Source B", url="https://b.example/y", snippet="b", body="more body"),
    ]


def test_research_topic_builds_a_cited_page():
    backend = _FakeBackend()
    r = research_topic("return policy", backend=backend, search_fn=_fake_search)
    assert any("Source A" in m.content for m in backend.last_messages)  # sources reach the prompt
    assert r.title == "return policy"
    assert r.markdown.startswith("# return policy")
    assert "key point one" in r.markdown  # the model's synthesis is included
    assert "## Sources" in r.markdown  # citations appended
    assert "https://a.example/x" in r.markdown and "https://b.example/y" in r.markdown
    assert r.sources == ["https://a.example/x", "https://b.example/y"]


def test_research_topic_dedupes_sources_across_queries():
    one = [SimpleNamespace(title="A", url="https://a/x", snippet="", body="b")]
    r = research_topic("t", backend=_FakeBackend(), search_fn=lambda q, n: list(one))
    assert r.sources == ["https://a/x"]  # same URL across angle-queries -> a single source


class _VerifyBackend:
    """Routes by system prompt: synthesis -> body, claim extraction -> two claims, verification ->
    a JSON object. The first claim is backed by both sources, the second by only one (so it flags)."""

    def chat(self, messages, **kw):
        system = messages[0].content
        if "extract the most important factual claims" in system:
            return "The return window is 30 days.\nRefunds are issued within 5 business days."
        if "strict fact-checker" in system:
            user = messages[1].content
            return '{"supporting": [1, 2]}' if "30 days" in user else '{"supporting": [1]}'
        return "Returns overview.\n\n- point one\n\n## Details\nMore detail."


def test_verify_report_marks_claims_by_corroboration():
    sources = [
        SimpleNamespace(title="A", url="https://a/x", snippet="", body="..."),
        SimpleNamespace(title="B", url="https://b/y", snippet="", body="..."),
    ]
    v = verify_report(_VerifyBackend(), "body text", sources)
    assert [c.status for c in v.claims] == ["verified", "weak"]
    assert len(v.flagged) == 1  # the 1-source claim is flagged for review
    assert v.flagged[0].text.startswith("Refunds are issued")
    assert v.summary.startswith("1 of 2")


def test_research_topic_appends_verification_section_when_enabled():
    backend = _VerifyBackend()
    r = research_topic("return policy", backend=backend, search_fn=_fake_search, verify=True)
    assert r.verification is not None
    assert "## Verification" in r.markdown
    assert "1 of 2 key claims corroborated" in r.markdown
    assert "Needs review" in r.markdown
    assert "Refunds are issued within 5 business days" in r.markdown  # the flagged claim is named
    # the Verification section sits between the body and the Sources list
    assert r.markdown.index("## Verification") < r.markdown.index("## Sources")


def test_research_topic_has_no_verification_by_default():
    r = research_topic("return policy", backend=_FakeBackend(), search_fn=_fake_search)
    assert r.verification is None and "## Verification" not in r.markdown


def test_verify_claim_ignores_out_of_range_source_numbers():
    from anthill.research import _verify_claim

    class _OutOfRange:
        def chat(self, messages, **kw):
            return '{"supporting": [1, 9, "x", 2]}'  # 9 and "x" are invalid for 2 sources

    sources = [
        SimpleNamespace(title="A", url="https://a", body=""),
        SimpleNamespace(url="https://b", body=""),
    ]
    assert _verify_claim(_OutOfRange(), "some claim", sources) == [1, 2]


def test_research_topic_requires_a_topic():
    with pytest.raises(ResearchError):
        research_topic("   ", backend=_FakeBackend(), search_fn=_fake_search)


def test_research_topic_raises_when_no_sources():
    with pytest.raises(ResearchError):
        research_topic("t", backend=_FakeBackend(), search_fn=lambda q, n: [])


def _admin_client(tmp_path):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="admin@a.com", role="admin", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "admin"))
    return c, app_mod


def test_research_route_redirects(tmp_path, monkeypatch):
    c, app_mod = _admin_client(tmp_path)
    # the route fires research off-thread; stub it so the test does no real web/model work
    monkeypatch.setattr(app_mod, "_research_and_file", lambda *a, **kw: None)

    r = c.post("/research", data={"topic": "", "target_scope": "personal"}, follow_redirects=False)
    assert r.status_code == 302 and "error=no_topic" in r.headers["location"]

    r = c.post(
        "/research",
        data={"topic": "vendor onboarding", "target_scope": "personal"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "research=started" in r.headers["location"]


def test_parse_topics_cleans_dedupes_and_caps():
    from anthill.web.app import MAX_SEED_TOPICS, _parse_topics

    raw = "Refund policy\n- Refund policy\n  2. Onboarding  \n\nProduct facts, specs\nproduct facts, specs\n"
    assert _parse_topics(raw) == ["Refund policy", "Onboarding", "Product facts, specs"]
    # newline-delimited, so a comma inside a topic is preserved (not a separator)
    assert _parse_topics("a\n" * 50) == ["a"]  # dedup collapses identical lines
    assert len(_parse_topics("\n".join(f"topic {i}" for i in range(50)))) == MAX_SEED_TOPICS
    # a leading number that is part of the topic (not a list marker) is preserved
    assert _parse_topics("30-day returns\n401k enrollment") == ["30-day returns", "401k enrollment"]


def test_research_batch_route_redirects(tmp_path, monkeypatch):
    c, app_mod = _admin_client(tmp_path)
    # the route seeds research off-thread; stub it so the test does no real web/model work
    monkeypatch.setattr(app_mod, "_research_batch_and_file", lambda *a, **kw: None)

    r = c.post(
        "/research/batch",
        data={"topics": "   ", "target_scope": "personal"},
        follow_redirects=False,
    )
    assert r.status_code == 302 and "error=no_topic" in r.headers["location"]

    r = c.post(
        "/research/batch",
        data={"topics": "Refund policy\nOnboarding\nOnboarding", "target_scope": "personal"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert "research=batch" in loc and "n=2" in loc  # 3 lines -> 2 after dedupe
