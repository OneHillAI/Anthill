"""Runtime verifier hooked into the wiki-write gate (Phase C). propose_wiki_write now runs a best-effort
independent cross-check alongside the agent review: a page the verifier disputes is pushed into the
human review queue even when the agent review is clean, but the verifier never blocks when no
independent model is available. Spec: engineering-plans/RUNTIME_CROSSCHECK_VERIFIER.md."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.app as app_mod
from anthill.verify import Verdict
from anthill.web import db as db_mod
from anthill.web.db import Organization, User, WikiReview


class _FakeBackend:
    def __init__(self, reply):
        self._reply = reply

    def chat(self, messages):
        return self._reply


def _setup(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHILL_WIKI_ROOT", str(tmp_path / "wikis"))
    monkeypatch.setenv("ANTHILL_ORG_WIKI", str(tmp_path / "org"))
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng, autoflush=False)()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    owner = User(org_id=org.id, email="o@acme.com", role="admin", active=True)
    s.add(owner)
    s.commit()
    # a clean agent review, so the ONLY thing that can queue a page is the verifier
    monkeypatch.setattr(
        app_mod, "_backend_from_cfg", lambda cfg: _FakeBackend('{"summary":"ok","flags":[]}')
    )
    return s, org.id, owner.id, tmp_path / "org" / "wiki"


def _propose(app, s, org_id, owner_id, slug, content):
    applied = app.propose_wiki_write(
        s, org_id=org_id, proposed_by=owner_id, slug=slug, content=content, target_scope="org"
    )
    s.commit()
    return applied


def test_verifier_queues_a_page_the_agent_review_passed(tmp_path, monkeypatch):
    s, org_id, owner_id, wiki_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        app_mod,
        "_verify_wiki_write",
        lambda cfg, *, content, source: Verdict(
            ok=False,
            confidence=0.6,
            reason="disputes a figure not in the source",
            kind="wiki_write",
            needs_review=True,
            crosscheck_model="mistral-nemo:12b",
        ),
    )
    applied = _propose(
        app_mod, s, org_id, owner_id, "alpha", "# Alpha\n\nRevenue tripled to $9M.\n"
    )
    assert applied is False
    row = s.query(WikiReview).filter_by(slug="alpha", status="pending").one()
    assert "verifier" in row.flags
    assert "disputes a figure" in row.outline and "mistral-nemo:12b" in row.outline
    assert not (wiki_dir / "alpha.md").exists()  # not written; held for review


def test_clean_review_and_clean_verifier_auto_applies(tmp_path, monkeypatch):
    s, org_id, owner_id, wiki_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(
        app_mod,
        "_verify_wiki_write",
        lambda cfg, *, content, source: Verdict(
            ok=True, confidence=0.85, reason="faithful", kind="wiki_write", needs_review=False
        ),
    )
    applied = _propose(app_mod, s, org_id, owner_id, "beta", "# Beta\n\nA fact.\n")
    assert applied is True
    assert s.query(WikiReview).count() == 0
    assert (wiki_dir / "beta.md").exists()


def test_verifier_never_blocks_when_it_cannot_run(tmp_path, monkeypatch):
    # a verifier that couldn't run returns None -> the write follows the agent-review decision alone
    s, org_id, owner_id, wiki_dir = _setup(tmp_path, monkeypatch)
    monkeypatch.setattr(app_mod, "_verify_wiki_write", lambda cfg, *, content, source: None)
    applied = _propose(app_mod, s, org_id, owner_id, "gamma", "# Gamma\n\nA fact.\n")
    assert applied is True
    assert s.query(WikiReview).count() == 0
    assert (wiki_dir / "gamma.md").exists()


def test_verify_wiki_write_helper_is_best_effort_offline(tmp_path, monkeypatch):
    # no Ollama running (no different-family model) -> deterministic-only, ok=True (never forces a
    # review on its own); must not raise even with cfg=None.
    v = app_mod._verify_wiki_write(
        None, content="# Page\n\nSome grounded content.\n", source="prov"
    )
    assert v is None or (v.ok is True)
