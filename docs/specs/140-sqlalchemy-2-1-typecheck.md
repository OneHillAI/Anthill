# Spec: SQLAlchemy 2.1 typing parity and CI typecheck (#140)

Status: implemented. Lane: `pillar:platform`.
Relates to: `anthill/web/db.py`, `anthill/web/audit.py`, `.github/workflows/ci.yml`.

## 1. Problem

A fresh `make install` installs `pip install -e ".[dev,docs]"`, where `sqlalchemy>=2.0,<3` resolves
to SQLAlchemy 2.1.x (2.1.5). Running `make typecheck` locally under SQLAlchemy 2.1 fails with 9 errors
across `anthill/web/metrics.py`, `anthill/web/digest.py`, and `anthill/web/audit.py`:

```
anthill/web/metrics.py:46: error: Argument 2 to "filter" of "Query" has incompatible type "bool"
anthill/web/metrics.py:112: error: Argument 2 to "filter" of "Query" has incompatible type "bool"
anthill/web/metrics.py:117: error: Need type annotation for "fb"
anthill/web/metrics.py:122: error: Argument 2 to "filter" of "Query" has incompatible type "bool"
anthill/web/digest.py:79: error: Argument 2 to "filter" of "Query" has incompatible type "bool"
anthill/web/digest.py:80: error: Argument 3 to "filter" of "Query" has incompatible type "bool"
anthill/web/audit.py:113: error: Argument 3 to "filter" of "Query" has incompatible type "bool"
anthill/web/audit.py:122: error: Need type annotation for "alerts"
anthill/web/audit.py:131: error: Argument 3 to "filter" of "Query" has incompatible type "bool"
```

In CI, the lint job previously installed only `ruff==0.15.16 mypy==2.1.0`. With `ignore_missing_imports = true`
configured in `pyproject.toml`, SQLAlchemy resolved to `Any` in CI and its stubs were never checked. As a
result, upstream typing changes in SQLAlchemy drifted past CI undetected while breaking local development
for contributors.

Under SQLAlchemy 2.1, legacy untyped `Column(...)` attributes accessed on the model class evaluate to `Never`,
causing binary comparisons such as `created_at >= since` to evaluate to `bool` rather than a SQL clause
element. This triggers the `filter()` argument type errors and causes downstream code to be marked unreachable,
prompting variable annotation errors.

## 2. Requirements

1. **Model column annotations:** ORM columns involved in class-level expressions (such as `created_at` time-window
   comparisons and `thumbs_up` presence checks) must be typed using SQLAlchemy 2.0+ `Mapped[...]` and `mapped_column(...)`.
2. **Schema and runtime compatibility:** Column types, constraints, default generators, and nullability must remain
   identical at runtime so existing SQLite databases require no schema migration.
3. **CI typecheck parity:** The CI lint job must install SQLAlchemy within the project's dependency range
   (`sqlalchemy>=2.0,<3`) so `mypy anthill` checks against real SQLAlchemy stubs rather than treating it as `Any`.
4. **Backward compatibility:** Type annotations must remain valid and clean under both SQLAlchemy 2.0.x and 2.1.x.

## 3. Implementation

- `anthill/web/db.py`: Updated `AuditLog.created_at`, `QueryMetric.created_at`, `ChatMessage.created_at`, and
  `ChatMessage.thumbs_up` to use `Mapped[datetime | None]` and `Mapped[bool | None]` with `mapped_column(...)`.
- `anthill/web/audit.py`: Explicitly annotated `alerts: list[str] = []` in `check_anomalies`.
- `.github/workflows/ci.yml`: Added `"sqlalchemy>=2.0,<3"` to the linters installation step in the `lint` job.

## 4. Acceptance Criteria

- [x] `make typecheck` passes cleanly under SQLAlchemy 2.1.x without grandfathering additional web modules.
- [x] `make typecheck` passes cleanly under SQLAlchemy 2.0.x.
- [x] The CI lint job installs SQLAlchemy within the specified range and runs `mypy anthill` against real stubs.
- [x] All existing database tests in `tests/` pass with unchanged schema behavior.
