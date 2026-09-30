"""Memory v2: semantic dedup, cosine, corroboration promotion, web-search recall."""

import numpy as np
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill import memory as mem
from anthill.web import db
from anthill.web.db import MemoryItem, Organization, Team, TeamMembership
from anthill.web.memory_ops import maybe_corroborate

# ── engine: cosine + semantic dedup ──────────────────────────────────────────


def test_cosine():
    a = np.array([1, 0, 0], dtype=np.float32)
    c = np.array([0, 1, 0], dtype=np.float32)
    assert mem.cosine(a, a) == 1.0
    assert mem.cosine(a, c) == 0.0
    assert mem.cosine(a, None) == 0.0 and mem.cosine(None, None) == 0.0


def test_is_semantically_new():
    v = np.array([1, 0, 0], dtype=np.float32)
    near = np.array([0.99, 0.01, 0.0], dtype=np.float32)
    far = np.array([0, 1, 0], dtype=np.float32)
    assert mem.is_semantically_new(v, [far]) is True
    assert mem.is_semantically_new(v, [near], threshold=0.9) is False
    assert mem.is_semantically_new(None, [v]) is True  # no vector -> defer to caller
    assert mem.is_semantically_new(v, []) is True


# ── corroboration promotion ──────────────────────────────────────────────────


def _sess(tmp_path):
    from fk_seed import seed_org_and_users

    eng = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    db.create_tables(eng)
    s = sessionmaker(bind=eng)()
    seed_org_and_users(s, user_ids=(1, 2, 3))  # multi-user memory-promotion tests
    s.commit()
    return s


def test_corroborate_promotes_to_org_across_users(tmp_path):
    s = _sess(tmp_path)
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    a = MemoryItem(
        org_id=o.id, user_id=1, scope="personal", kind="fact", text="We ship on Fridays."
    )
    b = MemoryItem(
        org_id=o.id, user_id=2, scope="personal", kind="fact", text="We ship on Fridays."
    )
    s.add_all([a, b])
    s.flush()
    assert maybe_corroborate(s, o.id, b) == "org"
    s.commit()
    assert a.scope == "org" and b.scope == "org"
    assert a.user_id is None and b.user_id is None and b.corroborations == 2


def test_corroborate_promotes_to_team_when_shared(tmp_path):
    s = _sess(tmp_path)
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    t = Team(org_id=o.id, name="P", slug="p", owner_id=1)
    s.add(t)
    s.flush()
    s.add_all(
        [
            TeamMembership(team_id=t.id, user_id=1, role="owner", status="active"),
            TeamMembership(team_id=t.id, user_id=2, role="member", status="active"),
        ]
    )
    a = MemoryItem(org_id=o.id, user_id=1, scope="personal", kind="fact", text="Use metric units.")
    b = MemoryItem(org_id=o.id, user_id=2, scope="personal", kind="fact", text="Use metric units.")
    s.add_all([a, b])
    s.flush()
    assert maybe_corroborate(s, o.id, b) == "team"
    s.commit()
    assert a.scope == "team" and a.team_id == t.id and b.team_id == t.id


def test_corroborate_noop_for_single_user(tmp_path):
    s = _sess(tmp_path)
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    a = MemoryItem(org_id=o.id, user_id=1, scope="personal", kind="fact", text="A lonely fact.")
    s.add(a)
    s.flush()
    assert maybe_corroborate(s, o.id, a) is None
    assert a.scope == "personal"


# ── web-search path now receives memory ──────────────────────────────────────


def test_search_and_answer_includes_memory(monkeypatch):
    import anthill.search.web as web

    monkeypatch.setattr(web, "web_search", lambda *a, **k: [])
    seen = {}

    class B:
        def chat(self, messages, **kw):
            seen["user"] = messages[-1].content
            return "ok"

    web.search_and_answer("q", backend=B(), memory_context="- prefers metric units")
    assert "prefers metric units" in seen["user"]
