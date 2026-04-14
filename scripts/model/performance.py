"""Query audit_log for parse_type distribution to measure custom model performance.

Compares the ratio of clean parses (Street Address) vs ambiguous parses over
time. Addresses that previously triggered RepeatedLabelError now parse cleanly
with the custom model — the shift from Ambiguous to Street Address is the
performance signal.

Usage:
    python scripts/model/performance.py summary [--since 7d] [--until now]
    python scripts/model/performance.py report --since 2026-03-28 \
        --out training/batches/.../performance.md
    python scripts/model/performance.py ambiguous --since 7d [--limit 20]

Requires VALIDATION_CACHE_DSN environment variable.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

# Ensure the src/ layout is importable when run directly
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "src"))

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine

from address_validator.db.tables import audit_log

_PARSE_ENDPOINTS = ("/api/v1/parse", "/api/v1/standardize", "/api/v1/validate")


def _parse_since(value: str) -> datetime:
    """Parse a --since value: '7d', '24h', or ISO date."""
    if value.endswith("d"):
        return datetime.now(UTC) - timedelta(days=int(value[:-1]))
    if value.endswith("h"):
        return datetime.now(UTC) - timedelta(hours=int(value[:-1]))
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _parse_until(value: str) -> datetime | None:
    """Parse a --until value: 'now' or ISO date."""
    if value == "now":
        return None
    return datetime.fromisoformat(value).replace(tzinfo=UTC)


def _base_filter(query: sa.Select, since: datetime, until: datetime | None) -> sa.Select:
    """Apply common filters: parse endpoints, date range, 2xx status, non-null parse_type."""
    query = query.where(
        audit_log.c.endpoint.in_(_PARSE_ENDPOINTS),
        audit_log.c.parse_type.isnot(None),
        audit_log.c.status_code < 400,  # noqa: PLR2004
        audit_log.c.timestamp >= since,
    )
    if until:
        query = query.where(audit_log.c.timestamp <= until)
    return query


async def _show_summary(dsn: str, since: datetime, until: datetime | None) -> None:
    """Print parse_type distribution summary."""
    engine = create_async_engine(dsn)
    try:
        query = (
            _base_filter(
                sa.select(
                    audit_log.c.parse_type,
                    sa.func.count().label("count"),
                ),
                since,
                until,
            )
            .group_by(audit_log.c.parse_type)
            .order_by(sa.desc("count"))
        )

        async with engine.begin() as conn:
            rows = (await conn.execute(query)).fetchall()

        if not rows:
            print(f"No parse requests with parse_type since {since.isoformat()}")
            return

        total = sum(r.count for r in rows)
        print(f"\n=== Parse Type Distribution (since {since.date()}) ===\n")
        print(f"{'Parse Type':<25} {'Count':>8} {'Pct':>7}")
        print("-" * 42)
        for row in rows:
            pct = row.count / total * 100
            print(f"{row.parse_type:<25} {row.count:>8} {pct:>6.1f}%")
        print("-" * 42)
        print(f"{'Total':<25} {total:>8}")

        # Daily breakdown
        daily_query = (
            _base_filter(
                sa.select(
                    sa.func.date_trunc("day", audit_log.c.timestamp).label("day"),
                    audit_log.c.parse_type,
                    sa.func.count().label("count"),
                ),
                since,
                until,
            )
            .group_by("day", audit_log.c.parse_type)
            .order_by("day")
        )

        async with engine.begin() as conn:
            daily_rows = (await conn.execute(daily_query)).fetchall()

        if daily_rows:
            hdr = (
                f"{'Date':<12} {'Street Address':>15} {'Ambiguous':>10} {'Other':>8} {'Clean %':>8}"
            )
            print(f"\n{hdr}")
            print("-" * 55)
            days: dict[str, dict[str, int]] = {}
            for row in daily_rows:
                day = row.day.strftime("%Y-%m-%d")
                days.setdefault(day, {})
                days[day][row.parse_type] = row.count
            for day, counts in sorted(days.items()):
                clean = counts.get("Street Address", 0)
                ambig = counts.get("Ambiguous", 0)
                non_primary = ("Street Address", "Ambiguous")
                other = sum(v for k, v in counts.items() if k not in non_primary)
                total_day = clean + ambig + other
                pct = clean / total_day * 100 if total_day else 0
                print(f"{day:<12} {clean:>15} {ambig:>10} {other:>8} {pct:>7.1f}%")
    finally:
        await engine.dispose()


async def _show_ambiguous(dsn: str, since: datetime, until: datetime | None, limit: int) -> None:
    """Show recent Ambiguous parse requests from audit_log + training candidates."""
    engine = create_async_engine(dsn)
    try:
        from address_validator.db.tables import model_training_candidates  # noqa: PLC0415

        # Show training candidates created in the period
        query = (
            sa.select(
                model_training_candidates.c.id,
                model_training_candidates.c.raw_address,
                model_training_candidates.c.failure_type,
                model_training_candidates.c.created_at,
            )
            .where(model_training_candidates.c.created_at >= since)
            .order_by(model_training_candidates.c.created_at.desc())
            .limit(limit)
        )
        if until:
            query = query.where(model_training_candidates.c.created_at <= until)

        async with engine.begin() as conn:
            rows = (await conn.execute(query)).fetchall()

        if not rows:
            print(f"No training candidates since {since.isoformat()}")
            print("(Addresses parsed cleanly by the custom model won't appear here)")
            return

        print(f"\n=== Ambiguous Parses / Training Candidates (since {since.date()}) ===\n")
        for row in rows:
            ts = row.created_at.strftime("%Y-%m-%d %H:%M")
            print(f"  [{row.id}] {ts} [{row.failure_type}] {row.raw_address}")
        print(f"\n{len(rows)} candidates shown (limit {limit})")
    finally:
        await engine.dispose()


async def _generate_report(dsn: str, since: datetime, until: datetime | None, out: str) -> None:  # noqa: PLR0915
    """Generate a performance.md report."""
    engine = create_async_engine(dsn)
    try:
        # Totals
        totals_query = _base_filter(
            sa.select(
                audit_log.c.parse_type,
                sa.func.count().label("count"),
            ),
            since,
            until,
        ).group_by(audit_log.c.parse_type)

        async with engine.begin() as conn:
            totals = {r.parse_type: r.count for r in (await conn.execute(totals_query)).fetchall()}

        clean = totals.get("Street Address", 0)
        ambig = totals.get("Ambiguous", 0)
        total = sum(totals.values())
        clean_pct = clean / total * 100 if total else 0

        # Daily breakdown
        daily_query = (
            _base_filter(
                sa.select(
                    sa.func.date_trunc("day", audit_log.c.timestamp).label("day"),
                    audit_log.c.parse_type,
                    sa.func.count().label("count"),
                ),
                since,
                until,
            )
            .group_by("day", audit_log.c.parse_type)
            .order_by("day")
        )

        async with engine.begin() as conn:
            daily_rows = (await conn.execute(daily_query)).fetchall()

        days: dict[str, dict[str, int]] = {}
        for row in daily_rows:
            day = row.day.strftime("%Y-%m-%d")
            days.setdefault(day, {})
            days[day][row.parse_type] = row.count

        # Ambiguous examples
        from address_validator.db.tables import model_training_candidates  # noqa: PLC0415

        examples_query = (
            sa.select(
                model_training_candidates.c.raw_address,
                model_training_candidates.c.failure_type,
                model_training_candidates.c.created_at,
            )
            .where(model_training_candidates.c.created_at >= since)
            .order_by(model_training_candidates.c.created_at.desc())
            .limit(20)
        )
        if until:
            examples_query = examples_query.where(model_training_candidates.c.created_at <= until)

        async with engine.begin() as conn:
            examples = (await conn.execute(examples_query)).fetchall()

        # Generate markdown
        since_str = since.strftime("%Y-%m-%d")
        until_str = until.strftime("%Y-%m-%d") if until else "now"
        lines = [
            "# Model Performance Report",
            "",
            f"**Period:** {since_str} to {until_str}",
            f"**Generated:** {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "## Summary",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| Total parse requests | {total} |",
            f"| Clean parses (Street Address) | {clean} ({clean_pct:.1f}%) |",
            f"| Ambiguous parses | {ambig} |",
            f"| Other types | {total - clean - ambig} |",
            "",
            "## Daily Breakdown",
            "",
            "| Date | Street Address | Ambiguous | Clean % |",
            "|---|---|---|---|",
        ]
        for day, counts in sorted(days.items()):
            d_clean = counts.get("Street Address", 0)
            d_ambig = counts.get("Ambiguous", 0)
            d_total = sum(counts.values())
            d_pct = d_clean / d_total * 100 if d_total else 0
            lines.append(f"| {day} | {d_clean} | {d_ambig} | {d_pct:.1f}% |")

        if examples:
            lines.extend(
                [
                    "",
                    "## Ambiguous Parse Examples (training candidates)",
                    "",
                    "These addresses triggered recovery heuristics despite the custom model.",
                    "They represent patterns not yet handled by CRF training.",
                    "",
                ]
            )
            for ex in examples:
                ts = ex.created_at.strftime("%Y-%m-%d %H:%M")
                lines.append(f"- `{ex.raw_address}` ({ex.failure_type}, {ts})")
        else:
            lines.extend(
                [
                    "",
                    "## Ambiguous Parse Examples",
                    "",
                    "No training candidates recorded in this period.",
                    "All multi-unit addresses parsed cleanly by the custom model,",
                    "or no multi-unit addresses were submitted.",
                ]
            )

        report = "\n".join(lines) + "\n"
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(report)
        print(f"Wrote performance report to {out}")
        print(f"  {total} requests, {clean_pct:.1f}% clean parse rate")

        # Update manifest if it exists in the same directory
        manifest_path = Path(out).parent / "manifest.json"
        if manifest_path.exists():
            with manifest_path.open() as mf:
                manifest_data = json.load(mf)
            manifest_data["performance_file"] = Path(out).name
            with manifest_path.open("w") as mf:
                json.dump(manifest_data, mf, indent=2)
            print(f"Updated manifest: performance_file={Path(out).name}")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Model performance metrics from audit log")
    sub = parser.add_subparsers(dest="command", required=True)

    # summary
    p_summary = sub.add_parser("summary", help="Show parse_type distribution")
    p_summary.add_argument("--since", default="7d", help="Start date (7d, 24h, or ISO date)")
    p_summary.add_argument("--until", default="now", help="End date (now or ISO date)")

    # report
    p_report = sub.add_parser("report", help="Generate performance.md report")
    p_report.add_argument("--since", required=True, help="Start date (ISO date or 7d/24h)")
    p_report.add_argument("--until", default="now", help="End date")
    p_report.add_argument("--out", required=True, help="Output markdown file path")

    # ambiguous
    p_ambig = sub.add_parser("ambiguous", help="Show recent ambiguous parses")
    p_ambig.add_argument("--since", default="7d", help="Start date")
    p_ambig.add_argument("--until", default="now", help="End date")
    p_ambig.add_argument("--limit", type=int, default=20, help="Max results")

    args = parser.parse_args()

    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        print("Error: VALIDATION_CACHE_DSN not set", file=sys.stderr)
        sys.exit(1)

    since = _parse_since(args.since)
    until = _parse_until(args.until)

    if args.command == "summary":
        asyncio.run(_show_summary(dsn, since, until))
    elif args.command == "report":
        asyncio.run(_generate_report(dsn, since, until, args.out))
    elif args.command == "ambiguous":
        asyncio.run(_show_ambiguous(dsn, since, until, args.limit))


if __name__ == "__main__":
    main()
