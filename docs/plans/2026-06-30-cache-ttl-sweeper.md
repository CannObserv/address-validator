# Validation-Cache TTL Sweeper Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development (recommended) or executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a periodic job that deletes `validated_addresses` rows (and their `query_patterns` pointers) older than `VALIDATION_CACHE_TTL_DAYS`, so stale cache rows no longer outlive their TTL.

**Architecture:** A standalone oneshot script (`infra/sweep_cache.py`) driven by a daily systemd timer, cloned from the existing `infra/archive_audit.py` + `audit-archive.{service,timer}` pattern. Deletion is batched and transactional: within each batch the dangling `query_patterns` pointers are deleted **before** the parent `validated_addresses` rows (the FK `fk_query_patterns_canonical_key` has no `ON DELETE` clause, so the parent cannot be removed while a child still references it). TTL semantics match the lookup-time check in `cache_provider._lookup` exactly (expire on `COALESCE(validated_at, created_at)`, `ttl_days == 0` = never expire).

**Tech Stack:** Python 3.12, SQLAlchemy Core (async, asyncpg), PostgreSQL, systemd timer, pytest (`db` fixture against `address_validator_test`).

---

## Context for the implementer (read once)

- **This is GitHub issue #144.** Options 2/3 from that issue (targeted invalidation via a `pipeline_version` stamp) are explicitly **out of scope** here — they are tracked in #145. Do not add a schema column.
- **Precedent to mirror:** read `infra/archive_audit.py`, `infra/audit-archive.service`, `infra/audit-archive.timer`, and `tests/unit/scripts/test_archive_audit.py` before starting. The new code apes their structure (config from env, batched delete loop, VACUUM at the end, `db` fixture in tests).
- **Tables involved** (`src/address_validator/db/tables.py`):
  - `validated_addresses` — PK `id`, unique `canonical_key` (NOT NULL), `created_at`/`last_seen_at`/`validated_at` all `DateTime(timezone=True)` NOT NULL.
  - `query_patterns` — unique `pattern_key` (NOT NULL), `canonical_key` (nullable) FK → `validated_addresses.canonical_key`, **no `ON DELETE`** (defaults to `NO ACTION`). May be NULL for partial/rate-limited registrations. _[Superseded by #150/#151: eager partial registration was removed (#150) and `canonical_key` is now `NOT NULL` (migration 018) — no NULL rows exist.]_
- **TTL source of truth:** the lookup check lives at `src/address_validator/services/validation/cache_provider.py:201-211`. It uses `cutoff = now - timedelta(days=ttl_days)`, compares `validated_at or created_at < cutoff`, and only applies when `ttl_days` is truthy. The sweeper MUST use the same column, same cutoff direction, same zero-sentinel.
- **Env var:** `VALIDATION_CACHE_TTL_DAYS` (default `30`) already exists (see `AGENTS.md` / `docs/VALIDATION-PROVIDERS.md`). Reuse it. Do **not** add a second retention knob.
- **Test plumbing:** `pyproject.toml` sets `pythonpath = ["src", "scripts", "infra"]`, so `from sweep_cache import ...` works in tests. The function-scoped `db: AsyncEngine` fixture (defined in `tests/unit/validation/conftest.py`, re-exported via `tests/unit/conftest.py`) TRUNCATEs all app tables before each test and runs Alembic migrations once per session. `asyncio_mode = "auto"`, so async tests need no `@pytest.mark.asyncio` (the archive tests use it anyway; either is fine — match the file you create).
- **Coverage:** the 80% floor is measured on `src/address_validator` only; `infra/` is excluded (same as `archive_audit.py`). The tests below still exercise the script fully — write them.
- **Run the suite with** `uv run pytest --no-cov -x tests/unit/scripts/test_sweep_cache.py` during the loop; full `uv run pytest` before the final commit.

---

## File Structure

- **Create** `infra/sweep_cache.py` — the oneshot sweeper script. Responsibilities: read config, compute cutoff, batched transactional delete of expired rows + their query_patterns pointers, VACUUM, log counts. Mirrors `infra/archive_audit.py`.
- **Create** `infra/cache-sweep.service` — systemd oneshot unit (clone of `audit-archive.service`).
- **Create** `infra/cache-sweep.timer` — daily timer at `04:00 UTC` (staggered clear of audit-archive at 03:00 and docker-prune at Sun 03:30).
- **Create** `tests/unit/scripts/test_sweep_cache.py` — unit tests against the `db` fixture.
- **Modify** `docs/DEPLOYMENT.md` — add the install/enable command under "Scheduled timers".
- **Modify** `docs/VALIDATION-PROVIDERS.md` — note that `VALIDATION_CACHE_TTL_DAYS` is now also enforced by the sweeper (not only at lookup).

---

## Task 1: Sweep core — `sweep_expired()` deletes expired rows + pointers

**Files:**
- Create: `infra/sweep_cache.py`
- Test: `tests/unit/scripts/test_sweep_cache.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/scripts/test_sweep_cache.py`:

```python
"""Tests for the validation-cache TTL sweeper."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine
from sweep_cache import sweep_expired


async def _insert_validated(
    engine: AsyncEngine,
    *,
    canonical_key: str,
    validated_at: datetime,
) -> None:
    """Insert one validated_addresses row with the given validated_at."""
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO validated_addresses
                    (canonical_key, status, country, validated,
                     created_at, last_seen_at, validated_at)
                VALUES
                    (:ck, 'confirmed', 'US', 'true',
                     :ts, :ts, :ts)
            """),
            {"ck": canonical_key, "ts": validated_at},
        )


async def _insert_pattern(
    engine: AsyncEngine,
    *,
    pattern_key: str,
    canonical_key: str | None,
) -> None:
    """Insert one query_patterns row pointing at canonical_key (may be NULL)."""
    now = datetime.now(UTC)
    async with engine.begin() as conn:
        await conn.execute(
            text("""
                INSERT INTO query_patterns (pattern_key, canonical_key, created_at)
                VALUES (:pk, :ck, :ts)
            """),
            {"pk": pattern_key, "ck": canonical_key, "ts": now},
        )


async def _count(engine: AsyncEngine, table: str) -> int:
    async with engine.connect() as conn:
        return (await conn.execute(text(f"SELECT count(*) FROM {table}"))).scalar_one()


@pytest.mark.asyncio
async def test_sweep_deletes_expired_and_keeps_fresh(db: AsyncEngine) -> None:
    """Expired rows (and their pointers) go; fresh rows stay."""
    old = datetime.now(UTC) - timedelta(days=100)
    fresh = datetime.now(UTC)
    await _insert_validated(db, canonical_key="old-ck", validated_at=old)
    await _insert_pattern(db, pattern_key="old-pk", canonical_key="old-ck")
    await _insert_validated(db, canonical_key="fresh-ck", validated_at=fresh)
    await _insert_pattern(db, pattern_key="fresh-pk", canonical_key="fresh-ck")

    cutoff = datetime.now(UTC) - timedelta(days=30)
    qp_deleted, va_deleted = await sweep_expired(db, cutoff)

    assert (qp_deleted, va_deleted) == (1, 1)
    assert await _count(db, "validated_addresses") == 1
    assert await _count(db, "query_patterns") == 1
    async with db.connect() as conn:
        remaining = (
            await conn.execute(text("SELECT canonical_key FROM validated_addresses"))
        ).scalar_one()
    assert remaining == "fresh-ck"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest --no-cov tests/unit/scripts/test_sweep_cache.py::test_sweep_deletes_expired_and_keeps_fresh -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'sweep_cache'` (or `ImportError: cannot import name 'sweep_expired'`).

- [ ] **Step 3: Write the minimal implementation**

Create `infra/sweep_cache.py`:

```python
#!/usr/bin/env python3
"""Delete validation-cache rows older than the TTL window.

Deletes ``validated_addresses`` rows whose ``COALESCE(validated_at, created_at)``
is older than ``VALIDATION_CACHE_TTL_DAYS``, plus their dangling
``query_patterns`` pointers. TTL semantics match the lookup-time check in
``cache_provider._lookup`` exactly.

Usage:
    uv run python infra/sweep_cache.py             # sweep expired rows
    uv run python infra/sweep_cache.py --dry-run    # report counts, delete nothing

Env vars:
    VALIDATION_CACHE_DSN        PostgreSQL DSN (required)
    VALIDATION_CACHE_TTL_DAYS   TTL window in days (default: 30; 0 = never sweep)
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import query_patterns, validated_addresses

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = logging.getLogger(__name__)

DEFAULT_TTL_DAYS = 30
DEFAULT_BATCH_SIZE = 10_000


def _expiry_column():
    """Match cache_provider._lookup: expire on validated_at, fall back to created_at."""
    return func.coalesce(
        validated_addresses.c.validated_at,
        validated_addresses.c.created_at,
    )


async def sweep_expired(
    engine: AsyncEngine,
    cutoff: datetime,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> tuple[int, int]:
    """Delete expired validated_addresses rows + their query_patterns pointers.

    Each batch runs in a single transaction, deleting the child query_patterns
    rows before the parent validated_addresses rows so the FK
    (fk_query_patterns_canonical_key, ON DELETE NO ACTION) is never violated.

    Returns (query_patterns_deleted, validated_addresses_deleted).
    """
    total_qp = 0
    total_va = 0
    while True:
        async with engine.begin() as conn:
            keys = list(
                (
                    await conn.execute(
                        select(validated_addresses.c.canonical_key)
                        .where(_expiry_column() < cutoff)
                        .limit(batch_size)
                    )
                ).scalars()
            )
            if not keys:
                break

            qp_res = await conn.execute(
                delete(query_patterns).where(query_patterns.c.canonical_key.in_(keys))
            )
            va_res = await conn.execute(
                delete(validated_addresses).where(
                    validated_addresses.c.canonical_key.in_(keys)
                )
            )
            total_qp += qp_res.rowcount
            total_va += va_res.rowcount

        if len(keys) < batch_size:
            break
        logger.info("Swept %d cache rows so far...", total_va)

    return total_qp, total_va
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest --no-cov tests/unit/scripts/test_sweep_cache.py::test_sweep_deletes_expired_and_keeps_fresh -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add infra/sweep_cache.py tests/unit/scripts/test_sweep_cache.py
git commit -m "#144 [feat]: cache TTL sweeper core (sweep_expired)"
```

---

## Task 2: Idempotency + batching + NULL-pointer safety tests

**Files:**
- Modify: `tests/unit/scripts/test_sweep_cache.py` (append tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/scripts/test_sweep_cache.py`:

```python
@pytest.mark.asyncio
async def test_sweep_is_idempotent(db: AsyncEngine) -> None:
    """Second sweep deletes nothing — safe to re-run."""
    old = datetime.now(UTC) - timedelta(days=100)
    await _insert_validated(db, canonical_key="old-ck", validated_at=old)
    await _insert_pattern(db, pattern_key="old-pk", canonical_key="old-ck")
    cutoff = datetime.now(UTC) - timedelta(days=30)

    first = await sweep_expired(db, cutoff)
    assert first == (1, 1)

    second = await sweep_expired(db, cutoff)
    assert second == (0, 0)


@pytest.mark.asyncio
async def test_sweep_batches_across_multiple_rounds(db: AsyncEngine) -> None:
    """batch_size smaller than the expired set still deletes everything."""
    old = datetime.now(UTC) - timedelta(days=100)
    for i in range(5):
        await _insert_validated(db, canonical_key=f"old-{i}", validated_at=old)
        await _insert_pattern(db, pattern_key=f"pk-{i}", canonical_key=f"old-{i}")
    cutoff = datetime.now(UTC) - timedelta(days=30)

    qp_deleted, va_deleted = await sweep_expired(db, cutoff, batch_size=2)

    assert (qp_deleted, va_deleted) == (5, 5)
    assert await _count(db, "validated_addresses") == 0
    assert await _count(db, "query_patterns") == 0


@pytest.mark.asyncio
async def test_sweep_ignores_null_canonical_key_patterns(db: AsyncEngine) -> None:
    """Partial-registration rows (canonical_key NULL) are untouched."""
    await _insert_pattern(db, pattern_key="orphan-pk", canonical_key=None)
    cutoff = datetime.now(UTC) - timedelta(days=30)

    qp_deleted, va_deleted = await sweep_expired(db, cutoff)

    assert (qp_deleted, va_deleted) == (0, 0)
    assert await _count(db, "query_patterns") == 1
```

> **Note:** the plan originally included a `test_sweep_uses_created_at_when_validated_at_null` test to exercise the `COALESCE(validated_at, created_at)` fallback. It was dropped: `validated_at` is `NOT NULL` in the migrated DB (verified against `address_validator_test`), so a NULL cannot be inserted or set via UPDATE to drive that branch. The COALESCE is retained in `_expiry_column()` as defensive parity with `cache_provider._lookup` (which uses `validated_at or created_at`), but it is not unit-testable through the DB. `infra/` is excluded from the coverage floor, so the uncovered branch does not affect the gate.

- [ ] **Step 2: Run the tests to verify they fail (or pass on already-correct behavior)**

Run: `uv run pytest --no-cov tests/unit/scripts/test_sweep_cache.py -v`
Expected: the four new tests run. If `sweep_expired` from Task 1 is correct they may PASS immediately — that is acceptable for behavior-confirming tests. If any FAIL, fix `infra/sweep_cache.py` (do not weaken the test) before continuing.

- [ ] **Step 3: (Only if a test failed) fix the implementation**

No code change is expected — Task 1's `sweep_expired` already covers these cases. If `test_sweep_uses_created_at_when_validated_at_null` fails, confirm `_expiry_column()` uses `func.coalesce(validated_at, created_at)`.

- [ ] **Step 4: Run the full file to verify it passes**

Run: `uv run pytest --no-cov tests/unit/scripts/test_sweep_cache.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/unit/scripts/test_sweep_cache.py
git commit -m "#144 [test]: sweeper idempotency, batching, NULL-pointer safety"
```

---

## Task 3: Config + `main()` entrypoint (env, TTL-zero sentinel, dry-run, VACUUM)

**Files:**
- Modify: `infra/sweep_cache.py` (add `_get_config`, `_parse_args`, `vacuum_cache_tables`, `main`)
- Modify: `tests/unit/scripts/test_sweep_cache.py` (append config tests)

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/scripts/test_sweep_cache.py`:

```python
import sweep_cache


def test_get_config_defaults(monkeypatch) -> None:
    """TTL defaults to 30 when the env var is unset."""
    monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://x/y")
    monkeypatch.delenv("VALIDATION_CACHE_TTL_DAYS", raising=False)
    dsn, ttl_days = sweep_cache._get_config()
    assert dsn == "postgresql+asyncpg://x/y"
    assert ttl_days == 30


def test_get_config_reads_ttl(monkeypatch) -> None:
    """TTL is read from VALIDATION_CACHE_TTL_DAYS."""
    monkeypatch.setenv("VALIDATION_CACHE_DSN", "postgresql+asyncpg://x/y")
    monkeypatch.setenv("VALIDATION_CACHE_TTL_DAYS", "7")
    _, ttl_days = sweep_cache._get_config()
    assert ttl_days == 7


def test_get_config_exits_without_dsn(monkeypatch) -> None:
    """Missing DSN aborts with exit code 1."""
    monkeypatch.delenv("VALIDATION_CACHE_DSN", raising=False)
    with pytest.raises(SystemExit) as exc:
        sweep_cache._get_config()
    assert exc.value.code == 1
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest --no-cov tests/unit/scripts/test_sweep_cache.py -k get_config -v`
Expected: FAIL — `AttributeError: module 'sweep_cache' has no attribute '_get_config'`.

- [ ] **Step 3: Add config, args, VACUUM, and main to `infra/sweep_cache.py`**

Append to `infra/sweep_cache.py` (after `sweep_expired`):

```python
def _get_config() -> tuple[str, int]:
    """Read and validate env vars. Returns (dsn, ttl_days). Exits 1 if DSN missing."""
    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        logger.error("VALIDATION_CACHE_DSN not set")
        sys.exit(1)
    ttl_days = int(os.environ.get("VALIDATION_CACHE_TTL_DAYS", str(DEFAULT_TTL_DAYS)))
    return dsn, ttl_days


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Sweep expired validation-cache rows.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report how many rows would be swept without deleting.",
    )
    return parser.parse_args()


async def count_expired(engine: AsyncEngine, cutoff: datetime) -> int:
    """Count validated_addresses rows that would be swept (for --dry-run)."""
    async with engine.connect() as conn:
        return (
            await conn.execute(
                select(func.count())
                .select_from(validated_addresses)
                .where(_expiry_column() < cutoff)
            )
        ).scalar_one()


async def vacuum_cache_tables(engine: AsyncEngine) -> None:
    """VACUUM ANALYZE the swept tables. Must run outside a transaction."""
    async with engine.execution_options(isolation_level="AUTOCOMMIT").connect() as conn:
        await conn.execute(text("VACUUM ANALYZE validated_addresses"))
        await conn.execute(text("VACUUM ANALYZE query_patterns"))


async def main() -> None:
    args = _parse_args()
    dsn, ttl_days = _get_config()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    if ttl_days <= 0:
        logger.info("VALIDATION_CACHE_TTL_DAYS=%d — sweeping disabled. Done.", ttl_days)
        return

    engine = create_async_engine(dsn)
    cutoff = datetime.now(UTC) - timedelta(days=ttl_days)

    try:
        if args.dry_run:
            expired = await count_expired(engine, cutoff)
            logger.info(
                "Dry run: %d validated_addresses rows older than %s would be swept.",
                expired,
                cutoff.date(),
            )
            return

        logger.info("Sweeping cache rows older than %s...", cutoff.date())
        qp_deleted, va_deleted = await sweep_expired(engine, cutoff)
        logger.info(
            "Swept %d validated_addresses rows and %d query_patterns pointers.",
            va_deleted,
            qp_deleted,
        )

        await vacuum_cache_tables(engine)
        logger.info("VACUUM ANALYZE complete.")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest --no-cov tests/unit/scripts/test_sweep_cache.py -v`
Expected: all tests PASS.

- [ ] **Step 5: Lint and format**

Run: `uv run ruff check infra/sweep_cache.py tests/unit/scripts/test_sweep_cache.py --fix && uv run ruff format infra/sweep_cache.py tests/unit/scripts/test_sweep_cache.py`
Expected: no remaining errors.

- [ ] **Step 6: Commit**

```bash
git add infra/sweep_cache.py tests/unit/scripts/test_sweep_cache.py
git commit -m "#144 [feat]: sweeper config, dry-run, VACUUM, main entrypoint"
```

---

## Task 4: systemd unit + timer

**Files:**
- Create: `infra/cache-sweep.service`
- Create: `infra/cache-sweep.timer`

- [ ] **Step 1: Create the service unit**

Create `infra/cache-sweep.service`:

```ini
[Unit]
Description=Sweep expired validation-cache rows
After=network.target postgresql.service

[Service]
Type=oneshot
User=exedev
WorkingDirectory=/home/exedev/address-validator
EnvironmentFile=/etc/address-validator/.env
Environment=PYTHONPATH=/home/exedev/address-validator/src
ExecStart=/home/exedev/address-validator/.venv/bin/python infra/sweep_cache.py

[Install]
WantedBy=multi-user.target
```

- [ ] **Step 2: Create the timer**

Create `infra/cache-sweep.timer`:

```ini
[Unit]
Description=Daily validation-cache TTL sweep (04:00 UTC)

[Timer]
OnCalendar=04:00 UTC
Persistent=true

[Install]
WantedBy=timers.target
```

- [ ] **Step 3: Verify unit syntax**

Run: `systemd-analyze verify infra/cache-sweep.service infra/cache-sweep.timer`
Expected: no output (valid). If `systemd-analyze` is unavailable in the worktree, skip — the units are byte-for-byte modeled on `audit-archive.{service,timer}`.

- [ ] **Step 4: Commit**

```bash
git add infra/cache-sweep.service infra/cache-sweep.timer
git commit -m "#144 [feat]: cache-sweep systemd service + daily timer (04:00 UTC)"
```

---

## Task 5: Documentation

**Files:**
- Modify: `docs/DEPLOYMENT.md` (Scheduled timers section, after the docker-prune block)
- Modify: `docs/VALIDATION-PROVIDERS.md` (TTL var note)

- [ ] **Step 1: Add the install command to DEPLOYMENT.md**

In `docs/DEPLOYMENT.md`, inside the `## Scheduled timers` fenced block (after the docker-prune lines, currently ending around line 31), add:

```bash
# Validation-cache TTL sweep timer (daily 04:00 UTC)
sudo cp infra/cache-sweep.service infra/cache-sweep.timer /etc/systemd/system/ \
  && sudo systemctl daemon-reload && sudo systemctl enable --now cache-sweep.timer
```

And below that block add a prose line:

```markdown
The cache sweep deletes `validated_addresses` rows (and their `query_patterns` pointers) older than `VALIDATION_CACHE_TTL_DAYS`. Dry-run any time with `uv run python infra/sweep_cache.py --dry-run`. Logs swept counts to the journal:

```bash
journalctl -u cache-sweep -p info
```
```

- [ ] **Step 2: Note sweeper enforcement in VALIDATION-PROVIDERS.md**

In `docs/VALIDATION-PROVIDERS.md`, find the `VALIDATION_CACHE_TTL_DAYS` entry/description and append a sentence:

```markdown
Enforced both at lookup time (expired rows treated as a miss) and by the daily `cache-sweep` timer, which physically deletes rows older than this window (`infra/sweep_cache.py`). Set to `0` to disable expiry and sweeping entirely.
```

- [ ] **Step 3: Verify docs reference real paths**

Run: `ls infra/sweep_cache.py infra/cache-sweep.service infra/cache-sweep.timer`
Expected: all three listed (no "No such file").

- [ ] **Step 4: Commit**

```bash
git add docs/DEPLOYMENT.md docs/VALIDATION-PROVIDERS.md
git commit -m "#144 [docs]: document cache-sweep timer + TTL sweeper enforcement"
```

---

## Task 6: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite with coverage**

Run: `uv run pytest`
Expected: all tests PASS; coverage ≥ 80% (no regression — `infra/` is excluded from the measured source, so the new script does not lower the floor).

- [ ] **Step 2: Lint + format the whole tree**

Run: `uv run ruff check . && uv run ruff format --check .`
Expected: no errors; "would reformat" count is 0.

- [ ] **Step 3: Smoke-test the script against the test DB (dry-run)**

Run:
```bash
VALIDATION_CACHE_DSN="postgresql+asyncpg://address_validator:address_validator_dev@localhost/address_validator_test" \
PYTHONPATH=src \
uv run python infra/sweep_cache.py --dry-run
```
Expected: logs `Dry run: N validated_addresses rows older than <date> would be swept.` and exits 0. (Do **not** point this at the production DSN.)

- [ ] **Step 4: Confirm TTL-zero short-circuit**

Run:
```bash
VALIDATION_CACHE_DSN="postgresql+asyncpg://address_validator:address_validator_dev@localhost/address_validator_test" \
VALIDATION_CACHE_TTL_DAYS=0 PYTHONPATH=src \
uv run python infra/sweep_cache.py
```
Expected: logs `VALIDATION_CACHE_TTL_DAYS=0 — sweeping disabled. Done.` and exits 0 without touching the DB.

- [ ] **Step 5: Open the PR**

```bash
export GH_TOKEN=$(grep GH_TOKEN .env | cut -d= -f2)
git push -u origin cache-ttl-sweeper
gh pr create --title "#144 [feat]: validation-cache TTL sweeper" \
  --body "Closes #144. Adds infra/sweep_cache.py + cache-sweep systemd timer (daily 04:00 UTC) deleting validated_addresses rows (and query_patterns pointers) older than VALIDATION_CACHE_TTL_DAYS. TTL semantics match cache_provider._lookup; deletes are batched, transactional (FK-safe: pointers before parents), idempotent, observable (logged counts), and dry-runnable. Options 2/3 deferred to #145."
```

---

## Self-Review notes

- **Spec coverage (issue #144 acceptance):** "Stale rows do not outlive their TTL" → Tasks 1–4 (sweeper + daily timer). "Observable" → logged swept counts + `--dry-run` + journal command (Tasks 3, 5). "Safe (batched, transactional, idempotent)" → Task 1 batched per-transaction delete + Task 2 idempotency/batching tests.
- **FK gotcha** addressed: pointers deleted before parents within one transaction (Task 1), regression-guarded by `test_sweep_deletes_expired_and_keeps_fresh` and the batching test.
- **TTL-match gotcha** addressed: `_expiry_column()` = `COALESCE(validated_at, created_at)` (parity with `cache_provider._lookup`); zero-sentinel handled in `main` and covered by Task 6 Step 4. The COALESCE fallback branch is not unit-testable (`validated_at` is `NOT NULL` in the DB) — retained as defensive parity only.
- **No new retention knob:** reuses `VALIDATION_CACHE_TTL_DAYS`.
- **Out of scope:** pipeline-version invalidation (Options 2/3) → #145; no schema change, no migration.
- **Type consistency:** `sweep_expired(engine, cutoff, *, batch_size)` returns `(qp_deleted, va_deleted)` everywhere; `_get_config()` returns `(dsn, ttl_days)` everywhere; `_expiry_column()` referenced by both `sweep_expired` and `count_expired`.
