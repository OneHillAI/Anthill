"""#278: a Task's escalation decision is made ONCE at creation time (escalate_on_uncertainty), never
live mid-run - no one is present to approve anything on an unattended task. Covers: the eligibility
gate (local + uncertain + connected + under the monthly cap), honest flagging via verify_needs_review
when eligible-but-not-escalated, and that _verify_task_result's own verdict never clobbers a flag
_maybe_escalate_task already set (and vice versa)."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import db as db_mod
from anthill.web import scheduler
from anthill.web.db import Organization, OrgSettings, ScheduledTask
from anthill.web.plane_routing import PlaneInference


def _session(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path / 't.db'}", connect_args={"check_same_thread": False})
    db_mod.create_tables(eng)
    s = sessionmaker(bind=eng)()
    org = Organization(name="A", slug="a")
    s.add(org)
    s.flush()
    return s, org.id


def _cfg(s, org_id, **overrides):
    kw = {
        "org_id": org_id,
        "org_backend_status": "validated",
        "org_model_endpoint": "https://gpu.acme.example/v1",
        "org_model": "qwen2.5:32b",
    }
    kw.update(overrides)
    cfg = OrgSettings(**kw)
    s.add(cfg)
    s.commit()
    return cfg


def _task(s, org_id, **overrides):
    kw = {"org_id": org_id, "title": "job", "goal": "do the thing", "schedule": "once"}
    kw.update(overrides)
    t = ScheduledTask(**kw)
    s.add(t)
    s.commit()
    return t


def _real_decrypt(v):
    return v


_LOCAL_PLANE = PlaneInference(
    plane="solo",
    backend="ollama",
    base_url="http://localhost:11434",
    model="qwen2.5:3b",
    api_key=None,
    wiki_scope="personal",
    use_personal_context=True,
)
_REMOTE_PLANE = PlaneInference(
    plane="solo",
    backend="openai",
    base_url="https://gpu.acme.example/v1",
    model="qwen2.5:32b",
    api_key=None,
    wiki_scope="personal",
    use_personal_context=True,
)
_UNCERTAIN = "I'm not entirely sure, but the answer is probably 42."
_CONFIDENT = "The answer is 42."


def test_skips_a_confident_answer(tmp_path):
    s, org_id = _session(tmp_path)
    cfg = _cfg(s, org_id)
    t = _task(s, org_id, escalate_on_uncertainty=True)
    out = scheduler._maybe_escalate_task(
        t, cfg, _real_decrypt, _LOCAL_PLANE, goal="g", context="c", answer=_CONFIDENT, db=s
    )
    assert out == _CONFIDENT
    assert t.verify_needs_review is False


def test_skips_when_already_answered_remotely(tmp_path):
    s, org_id = _session(tmp_path)
    cfg = _cfg(s, org_id)
    t = _task(s, org_id, escalate_on_uncertainty=True)
    out = scheduler._maybe_escalate_task(
        t, cfg, _real_decrypt, _REMOTE_PLANE, goal="g", context="c", answer=_UNCERTAIN, db=s
    )
    assert out == _UNCERTAIN
    assert t.verify_needs_review is False


def test_flags_when_escalation_is_disabled(tmp_path):
    s, org_id = _session(tmp_path)
    cfg = _cfg(s, org_id)
    t = _task(s, org_id, escalate_on_uncertainty=False)
    out = scheduler._maybe_escalate_task(
        t, cfg, _real_decrypt, _LOCAL_PLANE, goal="g", context="c", answer=_UNCERTAIN, db=s
    )
    assert out == _UNCERTAIN  # the original (weak but real) answer is kept
    assert t.verify_needs_review is True
    assert "off for this task" in t.verify_reason


def test_flags_when_nothing_connected(tmp_path):
    s, org_id = _session(tmp_path)
    cfg = _cfg(s, org_id, org_backend_status="")  # nothing connected
    t = _task(s, org_id, escalate_on_uncertainty=True)
    out = scheduler._maybe_escalate_task(
        t, cfg, _real_decrypt, _LOCAL_PLANE, goal="g", context="c", answer=_UNCERTAIN, db=s
    )
    assert out == _UNCERTAIN
    assert t.verify_needs_review is True
    assert "no backend is connected" in t.verify_reason


def test_flags_when_the_monthly_cap_is_reached(tmp_path):
    s, org_id = _session(tmp_path)
    cfg = _cfg(
        s,
        org_id,
        task_escalation_cap_per_month=5,
        task_escalations_this_month=5,
        task_escalations_reset_at=datetime.now(timezone.utc),
    )
    t = _task(s, org_id, escalate_on_uncertainty=True)
    out = scheduler._maybe_escalate_task(
        t, cfg, _real_decrypt, _LOCAL_PLANE, goal="g", context="c", answer=_UNCERTAIN, db=s
    )
    assert out == _UNCERTAIN
    assert t.verify_needs_review is True
    assert "monthly escalation cap" in t.verify_reason
    assert cfg.task_escalations_this_month == 5  # not incremented past the cap


def test_escalates_and_increments_the_monthly_count(tmp_path, monkeypatch):
    s, org_id = _session(tmp_path)
    cfg = _cfg(
        s,
        org_id,
        task_escalation_cap_per_month=20,
        task_escalations_this_month=3,
        task_escalations_reset_at=datetime.now(timezone.utc),
    )
    t = _task(s, org_id, escalate_on_uncertainty=True)

    class _FakeBackend:
        def chat(self, messages, **_k):
            return "a much stronger answer"

    monkeypatch.setattr(
        "anthill.web.escalation.build_escalation_backend",
        lambda *a, **k: (_FakeBackend(), _REMOTE_PLANE),
    )
    out = scheduler._maybe_escalate_task(
        t, cfg, _real_decrypt, _LOCAL_PLANE, goal="g", context="c", answer=_UNCERTAIN, db=s
    )
    assert out == "[Answered by the connected backend] a much stronger answer"
    assert cfg.task_escalations_this_month == 4
    assert t.verify_needs_review is False  # a successful escalation is not itself flagged


def test_escalation_failure_flags_and_keeps_the_original_answer(tmp_path, monkeypatch):
    s, org_id = _session(tmp_path)
    cfg = _cfg(s, org_id)
    t = _task(s, org_id, escalate_on_uncertainty=True)

    def _boom(*a, **k):
        raise RuntimeError("endpoint unreachable")

    monkeypatch.setattr("anthill.web.escalation.build_escalation_backend", _boom)
    out = scheduler._maybe_escalate_task(
        t, cfg, _real_decrypt, _LOCAL_PLANE, goal="g", context="c", answer=_UNCERTAIN, db=s
    )
    assert out == _UNCERTAIN
    assert t.verify_needs_review is True
    assert "failed" in t.verify_reason


# ── the monthly counter reset ──────────────────────────────────────────────────


def test_reset_counter_when_never_reset(tmp_path):
    s, org_id = _session(tmp_path)
    cfg = _cfg(s, org_id, task_escalations_this_month=7, task_escalations_reset_at=None)
    scheduler._reset_monthly_escalation_count_if_due(cfg)
    assert cfg.task_escalations_this_month == 0


def test_no_reset_when_recently_reset(tmp_path):
    s, org_id = _session(tmp_path)
    cfg = _cfg(
        s,
        org_id,
        task_escalations_this_month=7,
        task_escalations_reset_at=datetime.now(timezone.utc) - timedelta(days=1),
    )
    scheduler._reset_monthly_escalation_count_if_due(cfg)
    assert cfg.task_escalations_this_month == 7


def test_reset_when_over_thirty_days_old(tmp_path):
    s, org_id = _session(tmp_path)
    cfg = _cfg(
        s,
        org_id,
        task_escalations_this_month=7,
        task_escalations_reset_at=datetime.now(timezone.utc) - timedelta(days=31),
    )
    scheduler._reset_monthly_escalation_count_if_due(cfg)
    assert cfg.task_escalations_this_month == 0


# ── _verify_task_result must not clobber a flag _maybe_escalate_task already set ────


def test_verify_task_result_preserves_a_prior_escalation_flag(tmp_path, monkeypatch):
    import anthill.verify as verify_pkg

    s, org_id = _session(tmp_path)
    t = _task(s, org_id)
    t.verify_needs_review = True
    t.verify_reason = "Answer looked uncertain; escalation is off for this task."
    monkeypatch.setattr(
        verify_pkg,
        "verify",
        lambda *a, **k: verify_pkg.Verdict(ok=True, confidence=0.9, reason="", needs_review=False),
    )
    scheduler._verify_task_result(t, "some result", s)
    assert t.verify_needs_review is True  # survives even though the verifier itself found nothing
    assert "escalation is off for this task" in t.verify_reason
