"""Query model_training_candidates and export selected candidates to CSV.

Usage:
    python scripts/model/identify.py summary
    python scripts/model/identify.py export [--status new] [--type repeated_label_error]
        [--limit 100] [--out candidates.csv]
    python scripts/model/identify.py mark ID [ID ...] --status rejected

Requires VALIDATION_CACHE_DSN environment variable.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# Ensure the src/ layout is importable when run directly
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import sqlalchemy as sa  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from address_validator.db.tables import model_training_candidates  # noqa: E402


async def _show_summary(dsn: str) -> None:
    """Print a summary of candidates grouped by failure_type and status."""
    engine = create_async_engine(dsn)
    try:
        query = (
            sa.select(
                model_training_candidates.c.failure_type,
                model_training_candidates.c.status,
                sa.func.count().label("count"),
            )
            .group_by(
                model_training_candidates.c.failure_type,
                model_training_candidates.c.status,
            )
            .order_by(sa.text("count DESC"))
        )
        async with engine.begin() as conn:
            result = await conn.execute(query)
            rows = result.all()

        print("\n=== Training Candidate Summary ===\n")
        print(f"{'Failure Type':<30} {'Status':<12} {'Count':>6}")
        print("-" * 50)
        for row in rows:
            print(f"{row.failure_type:<30} {row.status:<12} {row.count:>6}")
        if not rows:
            print("  (no candidates)")
        print()
    finally:
        await engine.dispose()


async def _export_csv(
    dsn: str,
    outfile: str,
    *,
    status: str = "new",
    failure_type: str | None = None,
    limit: int = 100,
) -> list[str]:
    """Export candidates to CSV. Returns list of raw_address_hash values exported."""
    engine = create_async_engine(dsn)
    try:
        query = (
            sa.select(model_training_candidates)
            .where(model_training_candidates.c.status == status)
            .order_by(model_training_candidates.c.created_at.desc())
            .limit(limit)
        )
        if failure_type:
            query = query.where(model_training_candidates.c.failure_type == failure_type)

        async with engine.begin() as conn:
            result = await conn.execute(query)
            rows = result.mappings().all()

        if not rows:
            print("No candidates found matching criteria.")
            return []

        with Path(outfile).open("w", newline="") as f:
            writer = csv.writer(f)
            cols = ["id", "raw_address", "failure_type", "parsed_tokens", "recovered_components"]
            writer.writerow(cols)
            for row in rows:
                writer.writerow(
                    [
                        row["id"],
                        row["raw_address"],
                        row["failure_type"],
                        json.dumps(row["parsed_tokens"]),
                        json.dumps(row["recovered_components"])
                        if row["recovered_components"]
                        else "",
                    ]
                )

        print(f"Exported {len(rows)} candidates to {outfile}")
        return [row["raw_address_hash"] for row in rows]
    finally:
        await engine.dispose()


async def _assign_to_batch(
    dsn: str,
    raw_address_hashes: list[str],
    *,
    batch_slug: str | None = None,
    create_slug: str | None = None,
    description: str | None = None,
) -> None:
    """Assign exported candidates to a batch (existing or newly created)."""
    from address_validator.services.training_batches import (  # noqa: PLC0415
        advance_step,
        assign_candidates,
        create_batch,
        get_batch_id_by_slug,
    )

    engine = create_async_engine(dsn)
    try:
        if create_slug:
            batch_id = await create_batch(
                engine,
                slug=create_slug,
                description=description or "",
            )
            print(f"Created batch '{create_slug}' ({batch_id})")
        else:
            batch_id = await get_batch_id_by_slug(engine, slug=batch_slug)  # type: ignore[arg-type]
            if batch_id is None:
                print(f"Error: unknown batch slug: {batch_slug}", file=sys.stderr)
                sys.exit(1)

        # identify.py IS the identifying step — mark it so the admin UI reflects reality.
        await advance_step(engine, batch_id=batch_id, step="identifying")

        n = await assign_candidates(
            engine,
            batch_id=batch_id,
            raw_address_hashes=raw_address_hashes,
            assigned_by="scripts/model/identify.py",
        )
        print(f"Assigned {n} candidate group(s) to batch '{create_slug or batch_slug}'")
    finally:
        await engine.dispose()


async def _update_status(dsn: str, ids: list[int], new_status: str) -> None:
    """Update the status of candidates by ID."""
    engine = create_async_engine(dsn)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                model_training_candidates.update()
                .where(model_training_candidates.c.id.in_(ids))
                .values(status=new_status)
            )
        print(f"Updated {len(ids)} candidates to status='{new_status}'")
    finally:
        await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="Identify training candidates")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("summary", help="Show candidate summary")

    export_cmd = sub.add_parser("export", help="Export candidates to CSV")
    export_cmd.add_argument("--status", default="new")
    export_cmd.add_argument("--type", dest="failure_type", default=None)
    export_cmd.add_argument("--limit", type=int, default=100)
    export_cmd.add_argument("--out", default="training/candidates.csv")
    export_cmd.add_argument(
        "--batch",
        help="slug of an existing batch to assign exported candidates to",
    )
    export_cmd.add_argument(
        "--create-batch",
        metavar="SLUG",
        help="create a new planned batch with this slug and assign exported candidates to it",
    )
    export_cmd.add_argument(
        "--batch-description",
        help="description for --create-batch (required when --create-batch is used)",
    )

    mark_cmd = sub.add_parser("mark", help="Update candidate status")
    mark_cmd.add_argument("ids", nargs="+", type=int)
    mark_cmd.add_argument("--status", required=True, choices=["new", "labeled", "rejected"])

    args = parser.parse_args()

    # Validate --create-batch requires --batch-description
    if args.command == "export" and args.create_batch and not args.batch_description:
        parser.error("--create-batch requires --batch-description")

    dsn = os.environ.get("VALIDATION_CACHE_DSN", "").strip()
    if not dsn:
        print("Error: VALIDATION_CACHE_DSN not set", file=sys.stderr)
        sys.exit(1)

    if args.command == "summary":
        asyncio.run(_show_summary(dsn))
    elif args.command == "export":
        raw_address_hashes = asyncio.run(
            _export_csv(
                dsn,
                args.out,
                status=args.status,
                failure_type=args.failure_type,
                limit=args.limit,
            )
        )
        batch_slug = getattr(args, "batch", None)
        create_slug = getattr(args, "create_batch", None)
        if raw_address_hashes and (batch_slug or create_slug):
            asyncio.run(
                _assign_to_batch(
                    dsn,
                    raw_address_hashes,
                    batch_slug=batch_slug,
                    create_slug=create_slug,
                    description=getattr(args, "batch_description", None),
                )
            )
    elif args.command == "mark":
        asyncio.run(_update_status(dsn, args.ids, args.status))


if __name__ == "__main__":
    main()
