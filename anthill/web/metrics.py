from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import func
from sqlalchemy.orm import Session

from .db import ChatMessage, Conversation, OrgSettings, QueryMetric, TrainingExample


def _pctl(values: list[int], p: float) -> int:
    """Nearest-rank percentile of an already-sorted list (empty -> 0)."""
    if not values:
        return 0
    i = min(len(values) - 1, round((p / 100) * (len(values) - 1)))
    return int(values[i])


def record(
    db: Session,
    *,
    org_id: int | None,
    node_id: str,
    cache_hit: bool,
    source: str,
    duration_ms: int | None,
    model: str,
) -> None:
    db.add(
        QueryMetric(
            org_id=org_id,
            node_id=node_id,
            cache_hit=cache_hit,
            source=source,
            duration_ms=duration_ms,
            model=model,
        )
    )
    db.commit()


def summary(db: Session, org_id: int, days: int = 30) -> dict:
    since = datetime.now(timezone.utc) - timedelta(days=days)
    rows = (
        db.query(QueryMetric)
        .filter(QueryMetric.org_id == org_id, QueryMetric.created_at >= since)
        .all()
    )

    total = len(rows)
    cache_hits = sum(1 for r in rows if r.cache_hit)
    generated = sum(1 for r in rows if r.source == "generated")
    hit_rate = round(cache_hits / total * 100, 1) if total else 0
    avg_ms = (
        (
            round(
                sum(r.duration_ms for r in rows if r.duration_ms)
                / max(1, sum(1 for r in rows if r.duration_ms))
            )
        )
        if rows
        else 0
    )

    # Queries that escalated to a paid cloud model (tagged source="cloud" at answer
    # time). These are the only queries where anything (a PII-scrubbed, question-only
    # prompt) left the perimeter, and the only ones not answered on local hardware.
    cloud_escalations = sum(1 for r in rows if r.source == "cloud")

    cfg = db.query(OrgSettings).filter(OrgSettings.org_id == org_id).first()
    # Per-query cloud-equivalent estimates (admin-overridable in Settings). Defaults:
    #   cost  ~ $0.004/query  (a small/mid cloud chat completion, order-of-cents)
    #   energy ~ 4 gCO2/query (a few Wh inference on the US grid, ~0.4 kgCO2/kWh - IEA)
    # Savings credit cache hits only (a cache hit runs zero inference, cloud or local),
    # so this is a conservative floor, not an upper bound.
    cost_pp = float(getattr(cfg, "cost_per_query_usd", "0.004") or "0.004")
    energy_pp = float(getattr(cfg, "energy_per_query_gco2", "4.0") or "4.0")

    saved_cost_usd = round(cache_hits * cost_pp, 4)
    saved_energy_gco2 = round(cache_hits * energy_pp, 1)

    # ── value-prop metrics (one number per proposition) ───────────────────────
    cloud_enabled = bool(getattr(cfg, "cloud_enabled", False))
    # Resilience: queries answered on the org's own hardware (everything but escalations).
    served_local = total - cloud_escalations
    # Sovereignty: exact share answered without anything leaving the perimeter. 100%
    # when nothing escalated (always true with the cloud fallback off, the default);
    # otherwise (total - escalations) / total.
    in_perimeter_pct = round((total - cloud_escalations) / total * 100, 1) if total else 100
    # Ownership: the org's own trained model version + how much gold backs it.
    model_version = int(getattr(cfg, "training_model_ver", 0) or 0)
    gold_examples = (
        db.query(func.count(TrainingExample.id))
        .filter(TrainingExample.org_id == org_id, TrainingExample.quality == "gold")
        .scalar()
        or 0
    )

    # ── operational metrics (what an admin uses to run the system) ────────────
    # Latency percentiles beat the mean: p95 is the tail users actually feel, and a
    # single slow generation will not skew it the way it skews the average.
    durations = sorted(int(r.duration_ms) for r in rows if r.duration_ms is not None)
    p50_ms = _pctl(durations, 50)
    p95_ms = _pctl(durations, 95)

    # Adoption: distinct people who actually chatted in the window. node_id is "web"
    # for every browser query, so it is useless for this; the conversation's owner is
    # the real signal.
    active_users = (
        db.query(func.count(func.distinct(Conversation.user_id)))
        .join(ChatMessage, ChatMessage.conversation_id == Conversation.id)
        .filter(Conversation.org_id == org_id, ChatMessage.created_at >= since)
        .scalar()
    ) or 0

    # Answer quality: thumbs ratings users left in the window (None = unrated, skipped).
    fb = (
        db.query(ChatMessage.thumbs_up)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .filter(
            Conversation.org_id == org_id,
            ChatMessage.created_at >= since,
            ChatMessage.thumbs_up.isnot(None),
        )
        .all()
    )
    feedback_up = sum(1 for (t,) in fb if t)
    feedback_down = len(fb) - feedback_up
    feedback_rated = len(fb)
    satisfaction_pct = round(feedback_up / feedback_rated * 100, 1) if feedback_rated else 0

    # Which models served the queries (operational: spot an unexpected model or a
    # stale version still in rotation). Sorted busiest-first.
    model_counts: dict[str, int] = {}
    for r in rows:
        key = str(r.model) if r.model else "unknown"
        model_counts[key] = model_counts.get(key, 0) + 1
    by_model = [
        {"model": k, "count": v}
        for k, v in sorted(model_counts.items(), key=lambda kv: kv[1], reverse=True)
    ]

    # Daily breakdown for sparkline (last 14 days)
    daily: dict[str, int] = {}
    for r in rows:
        day = r.created_at.strftime("%Y-%m-%d") if r.created_at else "unknown"
        daily[day] = daily.get(day, 0) + 1

    return {
        "total": total,
        "cache_hits": cache_hits,
        "generated": generated,
        "hit_rate_pct": hit_rate,
        "avg_latency_ms": avg_ms,
        "p50_ms": p50_ms,
        "p95_ms": p95_ms,
        "active_users": active_users,
        "feedback_up": feedback_up,
        "feedback_down": feedback_down,
        "feedback_rated": feedback_rated,
        "satisfaction_pct": satisfaction_pct,
        "by_model": by_model,
        "saved_cost_usd": saved_cost_usd,
        "saved_energy_gco2": saved_energy_gco2,
        "served_local": served_local,
        "cloud_escalations": cloud_escalations,
        "in_perimeter_pct": in_perimeter_pct,
        "cloud_enabled": cloud_enabled,
        "model_version": model_version,
        "gold_examples": gold_examples,
        "offline_ready": True,
        "daily": daily,
        "days": days,
    }
