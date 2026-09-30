"""Lightweight SQLite schema migration.

``ensure_columns`` adds missing model columns, while ``run_migrations`` applies the
ordered, versioned backfills and table rebuilds that additive DDL cannot express. See
``docs/specs/versioned-migrations.md`` for the ownership and safety contract.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import inspect, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.types import Boolean, Integer

from .db import Base

log = logging.getLogger("anthill.migrate")

MigrationFn = Callable[[Connection], None]
_MIGRATION_CADENCE_REVIEW = (
    "Migration found an overdue recurring time that may be immediate work or cadence; "
    "verify this task's schedule."
)


def _sql_type(column) -> str:
    t = column.type
    if isinstance(t, Boolean):
        return "BOOLEAN"
    if isinstance(t, Integer):
        return "INTEGER"
    return "VARCHAR"


def _default_clause(column) -> str:
    """Return a ` DEFAULT <literal>` clause for scalar defaults, else ''."""
    d = column.default
    if d is None or getattr(d, "is_callable", False) or not getattr(d, "is_scalar", False):
        return ""
    val = d.arg
    if isinstance(val, bool):
        return f" DEFAULT {1 if val else 0}"
    if isinstance(val, (int, float)):
        return f" DEFAULT {val}"
    return f" DEFAULT '{val}'"


def ensure_columns(engine, *, before_change=None) -> list[str]:
    """Add any model-defined columns missing from existing tables.

    If ``before_change`` is given and at least one column will be added, it is called once with the
    list of pending "table.column" names *before* any ALTER runs - the hook that snapshots the DB so
    a migration stays reversible. A failing hook never blocks the migration.

    Returns a list of "table.column" strings that were added (for logging/tests).
    """
    insp = inspect(engine)
    existing_tables = set(insp.get_table_names())

    pending: list[tuple[str, str]] = []  # (ddl, "table.column")
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue  # create_all handles brand-new tables
        live_cols = {c["name"] for c in insp.get_columns(table.name)}
        for column in table.columns:
            if column.name in live_cols:
                continue
            ddl = (
                f"ALTER TABLE {table.name} "
                f"ADD COLUMN {column.name} {_sql_type(column)}"
                f"{_default_clause(column)}"
            )
            pending.append((ddl, f"{table.name}.{column.name}"))

    if not pending:
        return []

    if before_change is not None:
        try:
            before_change([name for _, name in pending])
        except Exception:
            pass  # a failed snapshot must never block the migration that keeps the app working

    with engine.begin() as conn:
        for ddl, _ in pending:
            conn.execute(text(ddl))

    return [name for _, name in pending]


# ── versioned migrations (for NON-additive changes ensure_columns can't do) ─────────────────────────
#
# `ensure_columns` above covers additive column adds automatically. Everything else - renames, drops,
# type changes, data backfills, table rebuilds (e.g. adding FK ON DELETE rules) - needs an ordered,
# recorded migration. This is that: a lightweight in-code runner keyed to SQLite's built-in
# `PRAGMA user_version`. No external dependency, so it works unchanged inside the frozen desktop app
# (unlike Alembic, whose on-disk `versions/` dir fights the single-file PyInstaller bundle).


def _user_version(conn: Connection) -> int:
    return int(conn.exec_driver_sql("PRAGMA user_version").scalar() or 0)


def _set_user_version(conn: Connection, version: int) -> None:
    # PRAGMA takes a literal, not a bound parameter; version is our own int, so this is safe.
    conn.exec_driver_sql(f"PRAGMA user_version = {int(version)}")


def _mig_0001_org_council_members(conn: Connection) -> None:
    """Backfill the single org_* model block into a one-member ``org_council_members`` list.

    The column itself is added additively (``db.create_tables``'s hardcoded ``_ensure_columns`` call,
    ahead of this migration in the same boot - NOT the generic ``migrate.ensure_columns``, which only
    runs later in ``web.app._db()``); this migration is the one-time DATA backfill, which is why it
    belongs here rather than in ``ensure_columns``. Idempotent: a row whose ``org_council_members`` is
    already non-empty (already migrated, or an admin already saved a real council) is left untouched,
    so a retry after a partial failure never clobbers live data.
    """
    rows = (
        conn.execute(
            text(
                "SELECT id, org_provider, org_model, org_model_params, org_region, org_gpu, "
                "org_model_quantized, org_model_endpoint, org_model_key_enc, org_provision_key_enc, "
                "org_hf_token_enc, org_backend_handle, org_lambda_ssh_keys, org_lambda_instance_type, "
                "org_backend_status, org_backend_detail, org_council_members FROM org_settings"
            )
        )
        .mappings()
        .all()
    )

    for r in rows:
        existing = (r["org_council_members"] or "").strip()
        if existing and existing != "[]":
            continue  # already migrated, or a real council already exists - never clobber

        has_config = any(
            [r["org_provider"], r["org_model"], r["org_model_endpoint"], r["org_backend_handle"]]
        )
        if not has_config:
            # never configured a serving model: backfill to an EMPTY list, not a blank member, so
            # "unconfigured" stays unconfigured.
            conn.execute(
                text("UPDATE org_settings SET org_council_members = :m WHERE id = :i"),
                {"m": "[]", "i": r["id"]},
            )
            continue

        provider_config: dict = {}
        if r["org_lambda_ssh_keys"] or r["org_lambda_instance_type"]:
            provider_config = {
                "ssh_keys": r["org_lambda_ssh_keys"] or "",
                "instance_type": r["org_lambda_instance_type"] or "",
            }

        member = {
            "endpoint": r["org_model_endpoint"] or "",
            "provider": r["org_provider"] or "",
            "model": r["org_model"] or "",
            "params_b": r["org_model_params"] or "",
            "region": r["org_region"] or "",
            "gpu_tier": r["org_gpu"] or "",
            "quantized": bool(r["org_model_quantized"]),
            "model_key_enc": r["org_model_key_enc"] or "",
            "provision_key_enc": r["org_provision_key_enc"] or "",
            "hf_token_enc": r["org_hf_token_enc"] or "",
            "backend_handle": r["org_backend_handle"] or "",
            "backend_status": r["org_backend_status"] or "unconfigured",
            "backend_detail": r["org_backend_detail"] or "",
            "lifecycle": "vpc",
            "provider_config": provider_config,
        }
        conn.execute(
            text("UPDATE org_settings SET org_council_members = :m WHERE id = :i"),
            {"m": json.dumps([member]), "i": r["id"]},
        )


def _mig_0002_drop_wiki_auto_promote(conn: Connection) -> None:
    """Drop the orphaned ``org_settings.wiki_auto_promote`` column.

    #683 removed this dead toggle ("rendered, stored, read by nothing") from the ``OrgSettings``
    model, but ``ensure_columns`` only ever ADDS columns it finds missing - it has no way to react to
    one being removed from the model. Any database created before #683 still physically carries this
    column as ``NOT NULL`` with no server-side default, so every ``INSERT INTO org_settings`` the ORM
    now issues (it no longer lists this column at all) hits a NOT NULL constraint violation - i.e.
    every new signup - first account or an additional one - 500s on any such database, forever, since
    nothing else would ever fix it up. Reported live (a real signup 500) and reproduced against a copy
    of a genuinely pre-#683 database; a fresh database created after #683 never had this column, so
    this is a no-op there. SQLite (3.35+, long since the floor here) supports DROP COLUMN directly -
    the column carries no index/FK/unique constraint, so this is a plain, safe drop.
    """
    cols = {c["name"] for c in inspect(conn).get_columns("org_settings")}
    if "wiki_auto_promote" in cols:
        conn.execute(text("ALTER TABLE org_settings DROP COLUMN wiki_auto_promote"))


def _migration_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _migration_aware(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=timezone.utc)
        if value.tzinfo is None
        else value.astimezone(timezone.utc)
    )


def _migration_stored(value: datetime) -> str:
    return _migration_aware(value).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S.%f")


def _migration_schedule(value: str) -> str:
    value = " ".join((value or "").lower().split())
    if value in {"once", "hourly", "daily", "weekly", "weekdays"}:
        return value
    match = re.fullmatch(r"(\d{1,2}):(\d{2})( weekdays)?", value)
    if not match:
        return ""
    hour, minute = int(match[1]), int(match[2])
    if hour > 23 or minute > 59:
        return ""
    return f"{hour:02d}:{minute:02d}{match[3] or ''}"


def _migration_cadence(
    schedule: str,
    timezone_name: str,
    schedule_anchor,
    anchor_source,
    after: datetime,
) -> tuple[datetime | None, datetime | None]:
    after = _migration_aware(after)
    source = _migration_datetime(anchor_source) or after
    source = _migration_aware(source)
    if schedule == "hourly":
        steps = max(1, (after - source) // timedelta(hours=1) + 1)
        return None, source + steps * timedelta(hours=1)

    try:
        zone = ZoneInfo(timezone_name or "UTC")
    except (ValueError, ZoneInfoNotFoundError):
        zone = ZoneInfo("UTC")
    now = after.astimezone(zone)
    anchor = _migration_datetime(schedule_anchor)
    if anchor is None and schedule in {"daily", "weekly", "weekdays"}:
        anchor = source.astimezone(zone).replace(tzinfo=None)
    elif anchor is not None and anchor.tzinfo is not None:
        anchor = anchor.astimezone(zone).replace(tzinfo=None)

    def anchored(candidate: datetime) -> datetime:
        if anchor is None:
            return candidate
        return candidate.replace(
            hour=anchor.hour,
            minute=anchor.minute,
            second=anchor.second,
            microsecond=anchor.microsecond,
            fold=0,
        )

    def stored(candidate: datetime) -> datetime:
        return candidate.astimezone(timezone.utc)

    if schedule == "daily":
        candidate = anchored(now)
        if stored(candidate) <= after:
            candidate += timedelta(days=1)
    elif schedule == "weekly":
        target_weekday = anchor.weekday() if anchor is not None else now.weekday()
        candidate = anchored(now + timedelta(days=(target_weekday - now.weekday()) % 7))
        if stored(candidate) <= after:
            candidate += timedelta(weeks=1)
    elif schedule == "weekdays":
        candidate = anchored(now)
        if stored(candidate) <= after:
            candidate += timedelta(days=1)
        while candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    elif ":" in schedule:
        hhmm = schedule.split()[0]
        hour, minute = (int(part) for part in hhmm.split(":"))
        candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0, fold=0)
        if stored(candidate) <= after:
            candidate += timedelta(days=1)
        while "weekday" in schedule and candidate.weekday() >= 5:
            candidate += timedelta(days=1)
    else:
        return anchor, None
    return anchor, stored(candidate)


def _insert_migrated_occurrence(
    conn: Connection,
    *,
    task_id: int,
    org_id: int | None,
    kind: str,
    status: str,
    due_at: datetime,
    inputs: list[str],
    run_id: int | None = None,
) -> int:
    result = conn.execute(
        text(
            "INSERT INTO task_occurrences "
            "(task_id,org_id,kind,status,due_at,inputs,claimed_run_id,created_at) "
            "VALUES (:task,:org,:kind,:status,:due,:inputs,:run,CURRENT_TIMESTAMP)"
        ),
        {
            "task": task_id,
            "org": org_id,
            "kind": kind,
            "status": status,
            "due": _migration_stored(due_at),
            "inputs": json.dumps(inputs),
            "run": run_id,
        },
    )
    return int(result.lastrowid)


def _project_migrated_task(
    conn: Connection,
    task_id: int,
    *,
    cancelled: bool,
    current_status: str,
) -> None:
    rows = (
        conn.execute(
            text(
                "SELECT status,due_at,inputs FROM task_occurrences "
                "WHERE task_id=:task AND status IN ('pending','claimed','interrupted','paused') "
                "ORDER BY due_at,id"
            ),
            {"task": task_id},
        )
        .mappings()
        .all()
    )
    interrupted = (
        None if cancelled else next((row for row in rows if row["status"] == "interrupted"), None)
    )
    due = next((row for row in rows if row["status"] in ("pending", "interrupted")), None)
    status = current_status
    if cancelled:
        status = "cancelled"
    elif any(row["status"] == "claimed" for row in rows):
        status = "running"
    elif due is not None:
        status = "pending"
    conn.execute(
        text(
            "UPDATE scheduled_tasks SET status=:status,next_run_at=:next,"
            "interrupted_run_at=:interrupted,interrupted_inputs=:interrupted_inputs,"
            "queued_inputs=:queued WHERE id=:task"
        ),
        {
            "task": task_id,
            "status": status,
            "next": None if cancelled or due is None else due["due_at"],
            "interrupted": interrupted["due_at"] if interrupted else None,
            "interrupted_inputs": interrupted["inputs"] if interrupted else None,
            "queued": json.dumps([item for row in rows for item in _json_list(row["inputs"])]),
        },
    )


def _mig_0003_task_occurrence_ledger(conn: Connection) -> None:
    """Backfill durable occurrence ownership from the legacy task projections."""
    run_columns = {column["name"] for column in inspect(conn).get_columns("task_runs")}
    had_claimed_inputs = "claimed_inputs" in run_columns
    had_cancel_requested = "cancel_requested" in run_columns
    had_trigger = "trigger" in run_columns
    trigger_select = "trigger" if had_trigger else "'scheduled' AS trigger"
    if not had_claimed_inputs:
        conn.execute(
            text("ALTER TABLE task_runs ADD COLUMN claimed_inputs TEXT NOT NULL DEFAULT ''")
        )
    if not had_cancel_requested:
        conn.execute(
            text("ALTER TABLE task_runs ADD COLUMN cancel_requested BOOLEAN NOT NULL DEFAULT 0")
        )

    migration_now = datetime.now(timezone.utc)
    tasks = conn.execute(
        text(
            "SELECT id, org_id, created_by, schedule, timezone, schedule_anchor, status, next_run_at, "
            "interrupted_run_at, interrupted_inputs, queued_inputs, last_run_at, created_at, "
            "occurrences_materialized "
            "FROM scheduled_tasks ORDER BY id"
        )
    ).mappings()
    for task in tasks:
        org_id = task["org_id"]
        if (
            org_id is not None
            and not conn.execute(
                text("SELECT 1 FROM organizations WHERE id=:org"), {"org": org_id}
            ).first()
        ):
            org_id = None
        schedule = _migration_schedule(task["schedule"])
        if schedule and schedule != task["schedule"]:
            conn.execute(
                text("UPDATE scheduled_tasks SET schedule=:schedule WHERE id=:task"),
                {"schedule": schedule, "task": task["id"]},
            )
        recurring = bool(schedule and schedule != "once")
        cancelled = task["status"] == "cancelled"
        queued = _json_list(task["queued_inputs"])
        run = (
            conn.execute(
                text(
                    "SELECT id, scheduled_for, started_at, claimed_inputs, cancel_requested, "
                    f"{trigger_select} FROM task_runs WHERE task_id=:task "
                    "AND status='running' ORDER BY id DESC LIMIT 1"
                ),
                {"task": task["id"]},
            )
            .mappings()
            .first()
        )
        if run and had_cancel_requested and run["cancel_requested"]:
            cancelled = True
        if run and task["status"] == "cancelled":
            conn.execute(
                text("UPDATE task_runs SET cancel_requested=1 WHERE id=:run"), {"run": run["id"]}
            )
        if conn.execute(
            text("SELECT 1 FROM task_occurrences WHERE task_id=:task LIMIT 1"),
            {"task": task["id"]},
        ).first():
            if task["occurrences_materialized"]:
                _project_migrated_task(
                    conn,
                    task["id"],
                    cancelled=cancelled,
                    current_status=task["status"],
                )
                continue
            conn.execute(
                text("UPDATE task_runs SET occurrence_id=NULL WHERE task_id=:task"),
                {"task": task["id"]},
            )
            conn.execute(
                text("DELETE FROM task_occurrences WHERE task_id=:task"),
                {"task": task["id"]},
            )

        owned: list[str] = []
        occupied: datetime | None = None
        if run:
            occupied = (
                _migration_datetime(run["scheduled_for"])
                or _migration_datetime(task["last_run_at"])
                or _migration_datetime(run["started_at"])
                or _migration_datetime(task["created_at"])
                or migration_now
            )
            owned = _json_list(run["claimed_inputs"]) if had_claimed_inputs else []
            occurrence_id = _insert_migrated_occurrence(
                conn,
                task_id=task["id"],
                org_id=org_id,
                kind="manual" if had_trigger and run["trigger"] == "manual" else "scheduled",
                status="cancelled" if cancelled else "claimed",
                due_at=occupied,
                inputs=owned,
                run_id=run["id"],
            )
            conn.execute(
                text("UPDATE task_runs SET occurrence_id=:occ WHERE id=:run"),
                {"occ": occurrence_id, "run": run["id"]},
            )
        elif task["interrupted_run_at"]:
            occupied = _migration_datetime(task["interrupted_run_at"])
            if occupied is not None:
                prior = (
                    conn.execute(
                        text(
                            "SELECT id, claimed_inputs, "
                            f"{trigger_select} FROM task_runs WHERE task_id=:task "
                            "AND scheduled_for=:due ORDER BY id DESC LIMIT 1"
                        ),
                        {"task": task["id"], "due": occupied},
                    )
                    .mappings()
                    .first()
                )
                owned = (
                    _json_list(task["interrupted_inputs"])
                    if task["interrupted_inputs"] is not None
                    else (
                        _json_list(prior["claimed_inputs"]) if prior and had_claimed_inputs else []
                    )
                )
                occurrence_id = _insert_migrated_occurrence(
                    conn,
                    task_id=task["id"],
                    org_id=org_id,
                    kind=(
                        "manual"
                        if prior and had_trigger and prior["trigger"] == "manual"
                        else "scheduled"
                    ),
                    status="cancelled" if cancelled else "interrupted",
                    due_at=occupied,
                    inputs=owned,
                    run_id=prior["id"] if prior else None,
                )
                if prior:
                    conn.execute(
                        text("UPDATE task_runs SET occurrence_id=:occ WHERE id=:run"),
                        {"occ": occurrence_id, "run": prior["id"]},
                    )
        elif task["status"] == "running":
            occupied = (
                _migration_datetime(task["last_run_at"])
                or _migration_datetime(task["created_at"])
                or migration_now
            )
            _insert_migrated_occurrence(
                conn,
                task_id=task["id"],
                org_id=org_id,
                kind=(
                    "manual"
                    if schedule == "once" and task["created_by"] is not None
                    else "scheduled"
                ),
                status="interrupted",
                due_at=occupied,
                inputs=[],
            )

        if had_claimed_inputs and not cancelled and queued[: len(owned)] == owned:
            queued = queued[len(owned) :]
        immediate = _migration_datetime(task["next_run_at"])
        ambiguous_overdue_cadence = bool(
            schedule in {"daily", "weekly", "weekdays"}
            and _migration_datetime(task["schedule_anchor"]) is None
            and immediate is not None
            and _migration_aware(immediate) <= migration_now
        )
        immediate_is_cadence = False
        if immediate is not None:
            if queued:
                immediate_kind = "queued"
            elif recurring and _migration_aware(immediate) > migration_now:
                immediate_kind = "scheduled"
                immediate_is_cadence = True
            elif (
                recurring
                or occupied is not None
                or (schedule == "once" and task["created_by"] is not None)
            ):
                immediate_kind = "manual"
            else:
                immediate_kind = "scheduled"
            _insert_migrated_occurrence(
                conn,
                task_id=task["id"],
                org_id=org_id,
                kind=immediate_kind,
                status="paused" if cancelled else "pending",
                due_at=immediate,
                inputs=queued,
            )
            queued = []
        if queued:
            _insert_migrated_occurrence(
                conn,
                task_id=task["id"],
                org_id=org_id,
                kind="queued",
                status="paused" if cancelled else "pending",
                due_at=(
                    _migration_datetime(task["last_run_at"])
                    or _migration_datetime(task["created_at"])
                    or migration_now
                ),
                inputs=queued,
            )

        if recurring:
            threshold = max(
                [
                    migration_now,
                    *[value for value in (occupied, immediate) if value is not None],
                ],
                key=_migration_aware,
            )
            anchor_source = (
                _migration_datetime(task["schedule_anchor"])
                or (
                    immediate
                    if immediate is not None
                    and (ambiguous_overdue_cadence or _migration_aware(immediate) > migration_now)
                    else None
                )
                or _migration_datetime(task["last_run_at"])
                or _migration_datetime(task["created_at"])
                or migration_now
            )
            anchor, future_due = _migration_cadence(
                schedule,
                task["timezone"],
                task["schedule_anchor"],
                anchor_source,
                threshold,
            )
            if future_due is not None:
                if not immediate_is_cadence:
                    _insert_migrated_occurrence(
                        conn,
                        task_id=task["id"],
                        org_id=org_id,
                        kind="scheduled",
                        status="paused" if cancelled else "pending",
                        due_at=future_due,
                        inputs=[],
                    )
                values = {
                    "task": task["id"],
                    "anchor": _migration_stored(anchor) if anchor is not None else None,
                }
                if task["next_run_at"] is None and not cancelled:
                    conn.execute(
                        text(
                            "UPDATE scheduled_tasks SET schedule_anchor=:anchor, next_run_at=:due "
                            "WHERE id=:task"
                        ),
                        {**values, "due": _migration_stored(future_due)},
                    )
                else:
                    conn.execute(
                        text("UPDATE scheduled_tasks SET schedule_anchor=:anchor WHERE id=:task"),
                        values,
                    )
        if ambiguous_overdue_cadence:
            conn.execute(
                text(
                    "UPDATE scheduled_tasks SET cadence_needs_review=1, "
                    "cadence_review_reason=:reason WHERE id=:task"
                ),
                {"task": task["id"], "reason": _MIGRATION_CADENCE_REVIEW},
            )
        conn.execute(
            text("UPDATE scheduled_tasks SET occurrences_materialized=1 WHERE id=:task"),
            {"task": task["id"]},
        )
        _project_migrated_task(
            conn,
            task["id"],
            cancelled=cancelled,
            current_status=task["status"],
        )


def _mig_0004_task_run_occurrence_fk(conn: Connection) -> None:
    """Rebuild upgraded task history with the same occurrence FK as a fresh database."""
    foreign_keys = inspect(conn).get_foreign_keys("task_runs")
    if any(
        fk["constrained_columns"] == ["occurrence_id"]
        and fk["referred_table"] == "task_occurrences"
        for fk in foreign_keys
    ):
        return

    live = {column["name"] for column in inspect(conn).get_columns("task_runs")}
    columns = [
        "id",
        "org_id",
        "task_id",
        "occurrence_id",
        "trigger",
        "status",
        "result",
        "error",
        "verify_needs_review",
        "verify_reason",
        "verify_confidence",
        "claimed_inputs",
        "cancel_requested",
        "scheduled_for",
        "started_at",
        "finished_at",
        "duration_ms",
    ]
    defaults = {
        "org_id": "NULL",
        "occurrence_id": "NULL",
        "trigger": "'scheduled'",
        "status": "'ok'",
        "result": "''",
        "error": "''",
        "verify_needs_review": "0",
        "verify_reason": "''",
        "verify_confidence": "''",
        "claimed_inputs": "''",
        "cancel_requested": "0",
        "scheduled_for": "NULL",
        "started_at": "NULL",
        "finished_at": "NULL",
        "duration_ms": "NULL",
    }
    selected = []
    for column in columns:
        quoted = f'"{column}"'
        if column == "org_id" and column in live:
            selected.append(
                'CASE WHEN "org_id" IS NULL OR EXISTS '
                '(SELECT 1 FROM organizations WHERE organizations.id=task_runs."org_id") '
                'THEN "org_id" ELSE NULL END'
            )
        elif column == "occurrence_id" and column in live:
            selected.append(
                'CASE WHEN "occurrence_id" IS NULL OR EXISTS '
                "(SELECT 1 FROM task_occurrences WHERE task_occurrences.id="
                'task_runs."occurrence_id") THEN "occurrence_id" ELSE NULL END'
            )
        elif column in live:
            selected.append(
                f"COALESCE({quoted}, {defaults[column]})"
                if column in defaults and defaults[column] != "NULL"
                else quoted
            )
        elif column in defaults:
            selected.append(defaults[column])
        else:
            raise RuntimeError(f"legacy task_runs is missing required column {column}")

    conn.execute(
        text(
            "CREATE TABLE IF NOT EXISTS task_run_archives ("
            "id INTEGER NOT NULL PRIMARY KEY, source_run_id INTEGER NOT NULL UNIQUE, "
            "original_task_id INTEGER, original_org_id INTEGER, original_row TEXT NOT NULL, "
            "reason VARCHAR(80) NOT NULL, archived_at DATETIME)"
        )
    )
    orphans = (
        conn.execute(
            text(
                "SELECT task_runs.* FROM task_runs WHERE NOT EXISTS "
                "(SELECT 1 FROM scheduled_tasks WHERE scheduled_tasks.id=task_runs.task_id) "
                "ORDER BY task_runs.id"
            )
        )
        .mappings()
        .all()
    )
    for row in orphans:
        conn.execute(
            text(
                "INSERT OR IGNORE INTO task_run_archives "
                "(source_run_id,original_task_id,original_org_id,original_row,reason,archived_at) "
                "VALUES (:run,:task,:org,:row,'missing_scheduled_task_parent',CURRENT_TIMESTAMP)"
            ),
            {
                "run": row["id"],
                "task": row.get("task_id"),
                "org": row.get("org_id"),
                "row": json.dumps(dict(row), sort_keys=True, default=str),
            },
        )
        if not conn.execute(
            text("SELECT 1 FROM task_run_archives WHERE source_run_id=:run"),
            {"run": row["id"]},
        ).first():
            raise RuntimeError(f"failed to archive orphan task run {row['id']}")

    conn.execute(text("DROP TABLE IF EXISTS task_runs__migration_4"))
    conn.execute(
        text(
            "CREATE TABLE task_runs__migration_4 ("
            "id INTEGER NOT NULL PRIMARY KEY, org_id INTEGER, task_id INTEGER NOT NULL, "
            "occurrence_id INTEGER, trigger VARCHAR(12) NOT NULL, status VARCHAR(12) NOT NULL, "
            "result TEXT NOT NULL, error TEXT NOT NULL, verify_needs_review BOOLEAN NOT NULL, "
            "verify_reason VARCHAR(400) NOT NULL, verify_confidence VARCHAR(8) NOT NULL, "
            "claimed_inputs TEXT NOT NULL, cancel_requested BOOLEAN NOT NULL, scheduled_for DATETIME, "
            "started_at DATETIME, finished_at DATETIME, duration_ms INTEGER, "
            "FOREIGN KEY(org_id) REFERENCES organizations(id), "
            "FOREIGN KEY(task_id) REFERENCES scheduled_tasks(id), "
            "FOREIGN KEY(occurrence_id) REFERENCES task_occurrences(id))"
        )
    )
    names = ",".join(f'"{column}"' for column in columns)
    conn.execute(
        text(
            f"INSERT INTO task_runs__migration_4 ({names}) "
            f"SELECT {','.join(selected)} FROM task_runs "
            "WHERE EXISTS (SELECT 1 FROM scheduled_tasks "
            "WHERE scheduled_tasks.id=task_runs.task_id)"
        )
    )
    conn.execute(text("DROP TABLE task_runs"))
    conn.execute(text("ALTER TABLE task_runs__migration_4 RENAME TO task_runs"))


def _json_list(value) -> list[str]:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


# APPEND-ONLY, ascending. Each entry is (version, description, fn(conn)). The runner applies every
# entry whose version is above the db's user_version, in order, each in its own transaction, and bumps
# user_version on success. RULES: never renumber, reorder, or edit a migration once it has shipped -
# only append a higher version. Additive column adds go through ensure_columns(), not here.
MIGRATIONS: list[tuple[int, str, MigrationFn]] = [
    (
        1,
        "backfill org_* single-model block into org_council_members one-member list",
        _mig_0001_org_council_members,
    ),
    (
        2,
        "drop the orphaned org_settings.wiki_auto_promote column (dead since #683)",
        _mig_0002_drop_wiki_auto_promote,
    ),
    (3, "backfill the durable scheduled-task occurrence ledger", _mig_0003_task_occurrence_ledger),
    (4, "add the task-run occurrence foreign key", _mig_0004_task_run_occurrence_fk),
]


def run_migrations(
    engine: Engine,
    *,
    fresh: bool,
    migrations: list[tuple[int, str, MigrationFn]] | None = None,
    before_change: Callable[[list[str]], None] | None = None,
) -> list[int]:
    """Apply the versioned migrations an existing database is behind on, and return the versions run.

    A FRESH database (``create_all`` just built the latest schema) is stamped at head WITHOUT running
    anything - each migration's end-state is already present, so re-applying it would fail. An existing
    database runs every migration above its ``user_version``, in ascending order, each in its own
    transaction (so a failure rolls that migration back and leaves ``user_version`` at the last one that
    succeeded, to be retried next launch). Before the first migration runs, a reversible snapshot of the
    db is taken (``before_change`` if given, else ``anthill.backup.snapshot_db``). A failing migration
    re-raises so the caller sees it; ``create_tables`` logs it and boots on the current schema."""
    migs = sorted(migrations if migrations is not None else MIGRATIONS, key=lambda m: m[0])
    head = migs[-1][0] if migs else 0

    with engine.begin() as conn:
        current = _user_version(conn)

    if fresh:
        if head != current:
            with engine.begin() as conn:
                _set_user_version(conn, head)
        return []

    pending = [m for m in migs if m[0] > current]
    if not pending:
        return []

    _snapshot_before_migrations(pending, before_change)

    applied: list[int] = []
    for version, desc, fn in pending:
        with engine.begin() as conn:
            fn(conn)
            _set_user_version(conn, version)
        log.info("applied schema migration %d (%s)", version, desc)
        applied.append(version)
    return applied


def _snapshot_before_migrations(
    pending: list[tuple[int, str, MigrationFn]],
    before_change: Callable[[list[str]], None] | None,
) -> None:
    names = [f"{v}:{d}" for v, d, _ in pending]
    if before_change is not None:
        try:
            before_change(names)
        except Exception:
            log.warning("pre-migration hook failed; proceeding", exc_info=True)
        return
    try:
        from ..backup import snapshot_db

        snapshot_db(reason="pre-migration")  # reversible safety copy before any structural change
    except Exception:
        log.warning("pre-migration db snapshot failed; proceeding", exc_info=True)
