"""#661: every turn answered by a real backend (local or remote) gets an inference.call audit row -
the admin-facing trail of who, when, which model, which endpoint. Never the message content itself
(that's the egress scrubber's job, tested in test_openai_compat.py)."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import anthill.web.db as db_mod
from anthill.web import audit
from anthill.web.db import AuditLog, Organization
from anthill.web.plane_routing import PlaneInference


def _session(tmp_path):
    from fk_seed import seed_org_and_users

    eng = create_engine(f"sqlite:///{tmp_path / 'a.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng, autoflush=False, autocommit=False)()
    seed_org_and_users(s, user_ids=(1,))
    s.commit()
    org = Organization(name="Acme", slug="acme")
    s.add(org)
    s.flush()
    return s, org.id


def test_logs_which_model_and_endpoint_answered(tmp_path):
    s, org_id = _session(tmp_path)
    plane_inf = PlaneInference(
        plane="solo",
        backend="openai",
        base_url="https://api.berget.ai/v1",
        model="gpt-oss-120b",
        api_key="secret",
        wiki_scope="personal",
        use_personal_context=True,
    )
    audit.log_inference_call(s, plane_inf, org_id=org_id, user_id=1, surface="chat")
    rows = s.query(AuditLog).filter(AuditLog.event == "inference.call").all()
    assert len(rows) == 1
    assert rows[0].org_id == org_id and rows[0].user_id == 1
    assert "surface=chat" in rows[0].detail
    assert "model=gpt-oss-120b" in rows[0].detail
    assert "endpoint=https://api.berget.ai/v1" in rows[0].detail
    # the API key must never appear in the audit trail
    assert "secret" not in rows[0].detail


def test_never_raises_even_if_the_db_write_fails(tmp_path, monkeypatch):
    s, org_id = _session(tmp_path)
    plane_inf = PlaneInference(
        plane="solo",
        backend="ollama",
        base_url="http://localhost:11434",
        model="qwen2.5:3b",
        api_key=None,
        wiki_scope="personal",
        use_personal_context=True,
    )

    def _boom(*a, **k):
        raise RuntimeError("db is down")

    monkeypatch.setattr(audit, "log", _boom)
    audit.log_inference_call(
        s, plane_inf, org_id=org_id, user_id=1, surface="task"
    )  # must not raise
