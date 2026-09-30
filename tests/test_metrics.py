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
