"""Metrics summary: operational usage stats (volume, adoption, latency, cache, cloud
escalations, answer satisfaction, models) plus the estimated-savings figures."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from anthill.web import metrics
from anthill.web.db import (
    ChatMessage,
    Conversation,
    Organization,
    OrgSettings,
    QueryMetric,
    TrainingExample,
    User,
    create_tables,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    create_tables(engine)
    s = sessionmaker(bind=engine)()
    s.add(Organization(name="Acme", slug="acme"))
    s.commit()
    from fk_seed import seed_org_and_users

    seed_org_and_users(s, user_ids=(1, 2, 3))
    s.commit()
    s.add(
        OrgSettings(
            org_id=1, cost_per_query_usd="0.004", energy_per_query_gco2="4.0", training_model_ver=2
        )
    )
    # 3 queries: 2 cache hits, 1 generated
    s.add(QueryMetric(org_id=1, node_id="n", cache_hit=True, source="local", duration_ms=5))
    s.add(QueryMetric(org_id=1, node_id="n", cache_hit=True, source="local", duration_ms=7))
    s.add(QueryMetric(org_id=1, node_id="n", cache_hit=False, source="generated", duration_ms=900))
    # one gold training example (ownership)
    s.add(TrainingExample(org_id=1, instruction="q", output="a", quality="gold", scope="org"))
    s.commit()
    return s


def test_summary_core_counts(db):
    m = metrics.summary(db, org_id=1, days=30)
    assert m["total"] == 3
    assert m["cache_hits"] == 2
    assert m["hit_rate_pct"] == round(2 / 3 * 100, 1)
    assert m["saved_cost_usd"] == round(2 * 0.004, 4)
    assert m["saved_energy_gco2"] == round(2 * 4.0, 1)


def test_summary_has_a_number_for_each_value_prop(db):
    m = metrics.summary(db, org_id=1, days=30)
    # Resilience, Cost, Latency, Energy, Sovereignty, Ownership, Offline
    assert m["served_local"] == 3  # resilience
    assert m["saved_cost_usd"] >= 0  # cost
    assert m["avg_latency_ms"] > 0  # latency
    assert m["saved_energy_gco2"] >= 0  # energy
    assert m["in_perimeter_pct"] == 100  # sovereignty (cloud off by default)
    assert m["model_version"] == 2  # ownership
    assert m["gold_examples"] == 1  # ownership backing
    assert m["offline_ready"] is True  # offline
    assert m["cloud_enabled"] is False


def test_summary_empty_org_is_safe(db):
    m = metrics.summary(db, org_id=999, days=30)  # no rows, no settings
    assert m["total"] == 0 and m["hit_rate_pct"] == 0
    assert m["served_local"] == 0 and m["offline_ready"] is True
    assert m["in_perimeter_pct"] == 100 and m["cloud_escalations"] == 0


def _seed(tmp_path, name, *, cloud_enabled, rows):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    create_tables(engine)
    s = sessionmaker(bind=engine)()
    s.add(Organization(name="Acme", slug="acme"))
    s.commit()
    from fk_seed import seed_org_and_users

    seed_org_and_users(s, user_ids=(1, 2, 3))
    s.commit()
    s.add(OrgSettings(org_id=1, cloud_enabled=cloud_enabled))
    for cache_hit, source in rows:
        s.add(QueryMetric(org_id=1, node_id="n", cache_hit=cache_hit, source=source, duration_ms=5))
    s.commit()
    return s


@pytest.mark.privacy_invariant
def test_sovereignty_is_exact_with_cloud_escalations(tmp_path):
    # 4 queries: 1 cache, 2 local-generated, 1 escalated to cloud.
    s = _seed(
        tmp_path,
        "esc.db",
        cloud_enabled=True,
        rows=[(True, "cache"), (False, "generated"), (False, "generated"), (False, "cloud")],
    )
    m = metrics.summary(s, org_id=1, days=30)
    assert m["total"] == 4
    assert m["cloud_escalations"] == 1
    assert m["served_local"] == 3  # everything except the escalation
    assert m["in_perimeter_pct"] == 75.0  # (4 - 1) / 4 - no longer a hardcoded 100
    assert m["cache_hits"] == 1 and m["hit_rate_pct"] == 25.0


@pytest.mark.privacy_invariant
def test_sovereignty_is_100_when_nothing_escalates_even_with_cloud_on(tmp_path):
    s = _seed(
        tmp_path, "noesc.db", cloud_enabled=True, rows=[(True, "cache"), (False, "generated")]
    )
    m = metrics.summary(s, org_id=1, days=30)
    assert m["cloud_enabled"] is True
    assert m["cloud_escalations"] == 0 and m["in_perimeter_pct"] == 100


def test_latency_percentiles_and_models(db):
    # durations 5, 7, 900 -> p50 is the middle value, p95 the slow tail.
    m = metrics.summary(db, org_id=1, days=30)
    assert m["p50_ms"] == 7 and m["p95_ms"] == 900
    # the fixture leaves model unset, so every query is bucketed as "unknown"
    assert m["by_model"] == [{"model": "unknown", "count": 3}]


def _ops_db(tmp_path):
    """An org with two users, three chats, and a few thumbs ratings."""
    engine = create_engine(f"sqlite:///{tmp_path / 'ops.db'}")
    create_tables(engine)
    s = sessionmaker(bind=engine)()
    s.add(Organization(name="Acme", slug="acme"))
    s.commit()
    from fk_seed import seed_org_and_users

    seed_org_and_users(s, user_ids=(1, 2, 3))
    s.commit()
    s.add_all(
        [
            User(org_id=1, email="a@acme.com", role="admin", active=True),
            User(org_id=1, email="b@acme.com", role="member", active=True),
        ]
    )
    # two distinct users chatted (user 1 in two conversations, user 2 in one)
    s.add_all(
        [
            Conversation(id=1, org_id=1, user_id=1, title="c1"),
            Conversation(id=2, org_id=1, user_id=1, title="c2"),
            Conversation(id=3, org_id=1, user_id=2, title="c3"),
        ]
    )
    s.commit()
    # assistant answers with ratings: 2 up, 1 down, 1 unrated (skipped)
    s.add_all(
        [
            ChatMessage(conversation_id=1, role="assistant", content="x", thumbs_up=True),
            ChatMessage(conversation_id=2, role="assistant", content="y", thumbs_up=True),
            ChatMessage(conversation_id=3, role="assistant", content="z", thumbs_up=False),
            ChatMessage(conversation_id=3, role="assistant", content="w", thumbs_up=None),
        ]
    )
    s.commit()
    return s


def test_active_users_counts_distinct_chatters(tmp_path):
    s = _ops_db(tmp_path)
    m = metrics.summary(s, org_id=1, days=30)
    assert m["active_users"] == 2  # two distinct users, not three conversations


def test_answer_satisfaction_from_thumbs(tmp_path):
    s = _ops_db(tmp_path)
    m = metrics.summary(s, org_id=1, days=30)
    assert m["feedback_up"] == 2 and m["feedback_down"] == 1
    assert m["feedback_rated"] == 3  # the unrated answer is not counted
    assert m["satisfaction_pct"] == round(2 / 3 * 100, 1)


def test_satisfaction_zero_when_unrated(db):
    # the core fixture has queries but no rated chat messages
    m = metrics.summary(db, org_id=1, days=30)
    assert m["feedback_rated"] == 0 and m["satisfaction_pct"] == 0
    assert m["active_users"] == 0


# ── latency is measured, or shown as not measured (#157) ─────────────────────────


def _timed(tmp_path, durations):
    s = _seed(tmp_path, "lat.db", cloud_enabled=False, rows=[(False, "generated")] * len(durations))
    for row, ms in zip(s.query(QueryMetric).order_by(QueryMetric.id).all(), durations, strict=True):
        row.duration_ms = ms
    s.commit()
    return s


def test_latency_is_none_when_nothing_was_timed(tmp_path):
    m = metrics.summary(_timed(tmp_path, [None, None]), org_id=1, days=30)
    assert m["total"] == 2
    assert m["p50_ms"] is None and m["p95_ms"] is None and m["avg_latency_ms"] is None


def test_latency_ignores_untimed_rows_and_keeps_zero(tmp_path):
    m = metrics.summary(_timed(tmp_path, [None, 0, 4000, 36000]), org_id=1, days=30)
    assert m["p50_ms"] == 4000 and m["p95_ms"] == 36000
    assert m["avg_latency_ms"] == round((0 + 4000 + 36000) / 3)


def test_record_stores_the_duration_and_none(db):
    for ms in (1200, None):
        metrics.record(
            db,
            org_id=1,
            node_id="web",
            cache_hit=False,
            source="generated",
            duration_ms=ms,
            model="m",
        )
    got = [r.duration_ms for r in db.query(QueryMetric).filter(QueryMetric.node_id == "web")]
    assert sorted(got, key=lambda v: (v is None, v)) == [1200, None]


def _metrics_page(db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import anthill.web.app as app_mod
    from anthill.web.crypto import make_token

    eng = db.get_bind()
    app_mod._engine = eng
    app_mod._SessionFactory = sessionmaker(bind=eng, autoflush=False, autocommit=False)
    client = TestClient(app_mod.app)
    client.cookies.set("session_token", make_token(1, 1, "admin"))
    return client.get("/metrics").text


def test_metrics_page_shows_dash_when_latency_is_unmeasured(tmp_path, monkeypatch):
    s = _timed(tmp_path, [None])
    page = _metrics_page(s, tmp_path, monkeypatch)
    assert "not measured yet" in page
    assert "0ms" not in page


def test_metrics_page_shows_the_measured_latency(tmp_path, monkeypatch):
    s = _timed(tmp_path, [5600, 36900])
    page = _metrics_page(s, tmp_path, monkeypatch)
    assert "5600ms" in page and "p95 36900ms" in page
    assert "not measured yet" not in page


def test_a_chat_turn_records_how_long_it_took(tmp_path, monkeypatch):
    from test_web_turn_stream import _client

    import anthill.web.app as app_mod

    client, cid = _client(tmp_path, monkeypatch)
    monkeypatch.setattr("anthill.wiki.ask.ask_stream", lambda *a, **k: iter(["a", "b"]))
    ticks = iter([100.0, 102.5])
    monkeypatch.setattr(app_mod, "monotonic", lambda: next(ticks, 102.5))
    r = client.get(f"/chat/{cid}/stream", params={"message": "Hello there"})
    assert r.status_code == 200
    rows = app_mod._SessionFactory().query(QueryMetric).all()
    assert len(rows) == 1
    assert rows[0].duration_ms == 2500
