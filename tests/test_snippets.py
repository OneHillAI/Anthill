"""Tests for the snippet service: save → red line → gold training example."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web.db import TrainingExample, create_tables
from anthill.web.snippets import all_tags, save_snippet


def _db(tmp_path):
    from fk_seed import seed_org_and_users

    engine = create_engine(f"sqlite:///{tmp_path / 's.db'}")
    create_tables(engine)
    db = sessionmaker(bind=engine)()
    seed_org_and_users(db)  # org 1 + users 1,2 for the snippets seeded below
    db.commit()
    return db


class _FakeBackend:
    model = "test"

    def __init__(self, reply="This matters because it records the billing decision."):
        self.reply = reply

    def chat(self, messages, **kw):
        return self.reply


def test_single_user_save_is_personal_gold(tmp_path):
    db = _db(tmp_path)
    snip = save_snippet(
        db,
        org_id=1,
        user_id=1,
        content="PostgreSQL was chosen for billing.",
        question="which db for billing?",
        tags="db, billing",
        backend=_FakeBackend(),
    )
    assert snip.id and snip.rationale  # red line derived
    assert snip.tags == "db,billing"  # normalized
    assert snip.scope == "personal"  # one user → personal, NOT org
    ex = db.query(TrainingExample).filter(TrainingExample.id == snip.training_id).first()
    assert ex is not None
    assert ex.quality == "gold"
    assert ex.scope == "personal"  # does not train the shared model yet
    assert ex.source == "snippet"
    assert ex.output == "PostgreSQL was chosen for billing."
    assert ex.context == snip.rationale  # red line rides along as context


def test_two_users_corroborate_to_org(tmp_path):
    db = _db(tmp_path)
    text = "Kafka was chosen for the event pipeline."
    s1 = save_snippet(db, org_id=1, user_id=1, content=text, backend=None)
    assert s1.scope == "personal"  # first saver → personal
    s2 = save_snippet(db, org_id=1, user_id=2, content=text, backend=None)
    assert s2.scope == "org"  # second distinct user → corroborated
    # BOTH users' training examples are now org-scope
    exs = db.query(TrainingExample).filter(TrainingExample.source == "snippet").all()
    assert len(exs) == 2 and all(e.scope == "org" for e in exs)


def test_same_user_twice_does_not_corroborate(tmp_path):
    db = _db(tmp_path)
    text = "Same content saved twice by one user."
    save_snippet(db, org_id=1, user_id=1, content=text, backend=None)
    s2 = save_snippet(db, org_id=1, user_id=1, content=text, backend=None)
    assert s2.scope == "personal"  # one distinct user, no corroboration


def test_save_without_backend_still_gold(tmp_path):
    db = _db(tmp_path)
    snip = save_snippet(db, org_id=1, user_id=1, content="A useful fact.", backend=None)
    assert snip.rationale == ""  # no model → no red line, no crash
    ex = db.query(TrainingExample).filter(TrainingExample.id == snip.training_id).first()
    assert ex.quality == "gold"


def test_backend_failure_is_best_effort(tmp_path):
    db = _db(tmp_path)

    class _Boom:
        model = "x"

        def chat(self, *a, **k):
            raise RuntimeError("model down")

    snip = save_snippet(db, org_id=1, user_id=1, content="X", backend=_Boom())
    assert snip.rationale == ""  # swallowed; snippet still saved
    assert snip.id


def test_all_tags_dedup_and_sorted(tmp_path):
    db = _db(tmp_path)
    save_snippet(db, org_id=1, user_id=1, content="a", tags="z, a", backend=None)
    save_snippet(db, org_id=1, user_id=1, content="b", tags="a, m", backend=None)
    assert all_tags(db, 1) == ["a", "m", "z"]


def test_org_export_excludes_uncorroborated_personal(tmp_path):
    """The shared-model dataset (scope=org) must exclude one-user-only snippets."""
    from anthill.training.export import export_jsonl

    db = _db(tmp_path)
    long = "x" * 60  # clears min_output_len
    # one user → personal only
    save_snippet(db, org_id=1, user_id=1, content="solo " + long, backend=None)
    # two users on the same content → org
    shared = "shared " + long
    save_snippet(db, org_id=1, user_id=1, content=shared, backend=None)
    save_snippet(db, org_id=1, user_id=2, content=shared, backend=None)

    org_out = tmp_path / "org.jsonl"
    n_org = export_jsonl(db, org_out, min_quality="gold", org_id=1, scope="org")
    # both org examples for the shared key (2 rows), none of the personal solo one
    assert n_org == 2
    assert "solo" not in org_out.read_text()
    assert "shared" in org_out.read_text()


def test_snippets_page_frames_itself_as_the_manual_wiki_path(tmp_path, monkeypatch):
    # Founder: Snippets shouldn't read as a 4th disconnected concept - it's the manual way to add to
    # the same wiki that grows automatically as you chat.
    monkeypatch.setenv("ANTHILL_WORKSPACE", str(tmp_path / "ws"))
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import anthill.web.app as app_mod
    from anthill.web import db as db_mod
    from anthill.web.crypto import make_token
    from anthill.web.db import Organization, User

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    s = app_mod._SessionFactory()
    o = Organization(name="A", slug="a")
    s.add(o)
    s.flush()
    u = User(org_id=o.id, email="a@a.com", role="member", active=True)
    s.add(u)
    s.commit()
    c = TestClient(app_mod.app)
    c.cookies.set("session_token", make_token(u.id, o.id, "member"))
    body = c.get("/snippets").text
    assert "how you <b>manually</b> add to your wiki" in body
    assert "Content you mark becomes a page in your" in body
