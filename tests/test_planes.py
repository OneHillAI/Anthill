"""The pure plane-routing rule (two-plane architecture, P2): solo -> local, org -> shared cloud."""

from dataclasses import dataclass

from anthill import planes


def test_normalize_defaults_to_solo():
    assert planes.normalize("solo") == "solo"
    assert planes.normalize("ORG") == "org"  # case-insensitive
    assert planes.normalize(" org ") == "org"  # trimmed
    assert planes.normalize("") == "solo"  # blank
    assert planes.normalize(None) == "solo"  # missing
    assert planes.normalize("nope") == "solo"  # unknown -> safe default
    assert planes.normalize("team") == "team"  # team is a real plane now


def test_route_solo_is_local_and_offline_ok():
    r = planes.route("solo")
    assert r.plane == "solo"
    assert r.model_source == "local" and r.wiki_scope == "personal"
    assert r.requires_connection is False and r.offline_ok is True


def test_route_org_is_shared_cloud_and_needs_connection():
    r = planes.route("org")
    assert r.plane == "org"
    assert r.model_source == "org" and r.wiki_scope == "org"
    assert r.requires_connection is True and r.offline_ok is False


def test_route_unknown_falls_back_to_solo():
    assert planes.route("nonsense").plane == "solo"
    assert planes.route(None).plane == "solo"


# ── org availability (duck-typed cfg) ────────────────────────────────────────────────


@dataclass
class _Cfg:
    org_backend_status: str = ""
    org_model_endpoint: str = ""


def test_org_available_requires_a_ready_backend():
    assert planes.org_available(_Cfg(org_backend_status="validated")) is True
    assert planes.org_available(_Cfg(org_backend_status="provisioned")) is True
    # a merely planned selection is NOT available yet (it cannot answer)
    assert planes.org_available(_Cfg(org_backend_status="planned")) is False
    assert planes.org_available(_Cfg(org_backend_status="error")) is False
    assert planes.org_available(_Cfg(org_backend_status="")) is False
    assert planes.org_available(None) is False


def test_org_available_is_case_insensitive():
    assert planes.org_available(_Cfg(org_backend_status="VALIDATED")) is True


# ── the model columns default to solo ────────────────────────────────────────────────


def test_conversation_and_task_default_to_solo(tmp_path):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from anthill.web import db as db_mod
    from anthill.web.db import Conversation, ScheduledTask

    eng = create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    conv = Conversation(title="c")
    task = ScheduledTask(title="t", goal="g")
    s.add_all([conv, task])
    s.commit()
    assert conv.plane == "solo" and task.plane == "solo"
