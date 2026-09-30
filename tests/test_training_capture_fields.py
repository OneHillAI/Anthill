"""record_example()'s new training_eligible/council_drafts fields (#661 inference-provider work) -
banking the full council reasoning process, and marking captures whose contributing model's own
terms restrict using its outputs to train another model."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.training.collect import record_example
from anthill.web import db as db_mod
from anthill.web.db import TrainingExample


def _session(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    return sessionmaker(bind=eng, autoflush=False, autocommit=False)()


def test_new_fields_default_to_todays_behavior(tmp_path):
    s = _session(tmp_path)
    ex = record_example(s, instruction="q", output="a")
    assert ex.training_eligible is True
    assert ex.council_drafts == ""


def test_council_drafts_round_trip(tmp_path):
    s = _session(tmp_path)
    drafts = '[{"member_index": 0, "text": "draft0"}, {"member_index": 1, "text": "draft1"}]'
    ex = record_example(s, instruction="q", output="a", council_drafts=drafts)
    stored = s.query(TrainingExample).filter(TrainingExample.id == ex.id).first()
    assert stored.council_drafts == drafts


def test_training_eligible_false_persists(tmp_path):
    s = _session(tmp_path)
    ex = record_example(s, instruction="q", output="a", training_eligible=False)
    stored = s.query(TrainingExample).filter(TrainingExample.id == ex.id).first()
    assert stored.training_eligible is False
