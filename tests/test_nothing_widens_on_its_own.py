"""Nothing moves to a wider space on its own: a memory, a training example and a snippet stay where their owner made them,
however many other people hold or reuse something similar. Model-free."""

from fk_seed import seed_org_and_users
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.training.collect import promote_to_silver, record_example
from anthill.training.export import export_jsonl
from anthill.web import db as db_mod
from anthill.web import scheduler
from anthill.web.db import MemoryItem, Organization, ScheduledTask, TrainingExample
from anthill.web.snippets import promote_snippet, save_snippet

LINE = "We ship the billing service on Fridays after the freeze."


def _db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'w.db'}")
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    seed_org_and_users(s, user_ids=(1, 2, 3))
    s.add(Organization(id=1, name="Acme", slug="acme")) if not s.get(Organization, 1) else None
    s.commit()
    return s


def test_a_memory_stays_private_however_many_people_hold_a_similar_one(tmp_path, monkeypatch):
    db = _db(tmp_path)
    monkeypatch.setattr("anthill.memory.embed_text", lambda text: [0.1, 0.2])
    monkeypatch.setattr("anthill.memory.is_semantically_new", lambda *a, **k: True)
    for uid in (1, 2, 3):
        task = ScheduledTask(
            org_id=1,
            created_by=uid,
            title="Ship",
            goal="ship",
            schedule="once",
            status="pending",
            plane="solo",
        )
        db.add(task)
        db.flush()
        scheduler._remember_task_outcome(db, task, LINE)
        db.commit()
    rows = db.query(MemoryItem).all()
    assert len(rows) == 3 and {r.scope for r in rows} == {"personal"}
    assert {r.user_id for r in rows} == {1, 2, 3}  # still owned by each of them, none made org-wide
    assert all(LINE in r.text for r in rows)  # each person still has their own memory
    org_wide = db.query(MemoryItem).filter(MemoryItem.scope.in_(["org", "team"])).count()
    assert org_wide == 0


def test_a_training_example_keeps_its_scope_when_another_person_reuses_the_answer(tmp_path):
    db = _db(tmp_path)
    ex = record_example(
        db, instruction="How do we ship?", output=LINE, org_id=1, user_id=1, scope="personal"
    )
    db.commit()
    promote_to_silver(db, "How do we ship?", 1)  # another person was given this answer
    db.commit()
    got = db.get(TrainingExample, ex.id)
    assert (
        got.quality == "silver" and got.scope == "personal"
    )  # quality can rise; the scope does not
    out = tmp_path / "org.jsonl"
    assert export_jsonl(db, out, min_quality="bronze", org_id=1, scope="org") == 0


def test_a_snippet_and_its_example_stay_personal_when_others_save_the_same_text(tmp_path):
    db = _db(tmp_path)
    saved = [save_snippet(db, org_id=1, user_id=u, content=LINE, backend=None) for u in (1, 2, 3)]
    assert {s.scope for s in saved} == {"personal"}
    assert {
        e.scope for e in db.query(TrainingExample).filter(TrainingExample.source == "snippet")
    } == {"personal"}


def test_moving_something_wider_is_still_possible_as_a_persons_own_action(tmp_path):
    db = _db(tmp_path)
    snip = save_snippet(db, org_id=1, user_id=1, content=LINE, backend=None)
    promote_snippet(db, snip, "org")  # an admin approves it into the wiki
    db.refresh(snip)
    assert snip.scope == "org"
    assert {
        e.scope for e in db.query(TrainingExample).filter(TrainingExample.source == "snippet")
    } == {"org"}
