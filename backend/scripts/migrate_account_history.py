from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.sub2api.account_history_migration import (
    assert_legacy_source_idle,
    clamp_migration_batch_size,
    clamp_migration_progress_interval,
    convert_legacy_account_history,
    delete_verified_legacy_samples,
    inspect_legacy_source,
    migration_id_for_boundary,
    pause_legacy_sample_ttl,
    restore_legacy_sample_ttl,
    verify_migrated_account_history,
)
from app.modules.system.bootstrap import ensure_account_history_indexes
from app.utils import serialize_doc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Convert legacy account probe snapshots to verified change history")
    parser.add_argument("--migration-id", default="", help="existing migration ID for resume or deletion")
    parser.add_argument("--site-id", default="", help="optional site scope")
    parser.add_argument("--idle-minutes", type=int, default=10, help="required time since the latest legacy write")
    parser.add_argument("--batch-size", type=int, default=10_000, help="source cursor and deletion batch size")
    parser.add_argument(
        "--progress-every-runs",
        type=int,
        default=100,
        help="persist conversion counters every N probe runs",
    )
    parser.add_argument("--inspect", action="store_true", help="read source status without writing")
    parser.add_argument("--delete-source", action="store_true", help="delete source snapshots for an already verified migration")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    return parser


async def run(args: argparse.Namespace) -> dict[str, Any]:
    db = get_db()
    site_id = str(args.site_id or "").strip() or None
    idle_minutes = max(1, int(args.idle_minutes))
    batch_size = clamp_migration_batch_size(args.batch_size)
    progress_interval_runs = clamp_migration_progress_interval(args.progress_every_runs)
    if args.inspect:
        return {
            "mode": "inspect",
            "read_only": True,
            "source": await inspect_legacy_source(
                db,
                site_id=site_id,
                idle_minutes=idle_minutes,
            ),
        }
    migration_id = str(args.migration_id or "").strip()
    if args.delete_source:
        if not migration_id:
            raise RuntimeError("--delete-source requires --migration-id from a verified conversion")
        result = await delete_verified_legacy_samples(
            db,
            migration_id,
            batch_size=batch_size,
            idle_minutes=idle_minutes,
        )
        result["restored_ttl_index"] = await restore_legacy_sample_ttl(db)
        return result

    source = await inspect_legacy_source(
        db,
        site_id=site_id,
        idle_minutes=idle_minutes,
    )
    if source["count"] == 0:
        return {"ok": True, "stage": "empty", "source": source}
    await assert_legacy_source_idle(db, idle_minutes=idle_minutes)
    boundary = source.get("latest_sampled_at")
    if boundary is None:
        raise RuntimeError("legacy source has documents but no sampled_at boundary")
    migration_id = migration_id or migration_id_for_boundary(boundary, site_id=site_id)
    await ensure_account_history_indexes(db)
    ttl = await pause_legacy_sample_ttl(db)
    converted = await convert_legacy_account_history(
        db,
        migration_id=migration_id,
        source_max_sampled_at=boundary,
        site_id=site_id,
        cursor_batch_size=batch_size,
        progress_interval_runs=progress_interval_runs,
    )
    verification = await verify_migrated_account_history(db, migration_id)
    await db.remote_account_history_migrations.update_one(
        {"_id": migration_id},
        {"$set": {"legacy_ttl_paused": ttl.get("paused"), "legacy_ttl_index": ttl, "updated_at": verification["verified_at"]}},
    )
    return {
        "ok": verification["ok"],
        "stage": verification["stage"],
        "migration_id": migration_id,
        "source": source,
        "ttl": ttl,
        "conversion": _conversion_summary(converted),
        "verification": verification,
        "next_command": (
            f"python scripts/migrate_account_history.py --migration-id {migration_id} --delete-source --batch-size {batch_size}"
            if verification["ok"]
            else None
        ),
    }


async def main() -> None:
    args = build_parser().parse_args()
    await connect_to_mongo()
    try:
        report = await run(args)
    finally:
        await close_mongo_connection()
    if args.json:
        print(json.dumps(serialize_doc(report), ensure_ascii=False, indent=2))
    else:
        print_report(report)


def print_report(report: dict[str, Any]) -> None:
    print(json.dumps(serialize_doc(report), ensure_ascii=False, indent=2))


def _conversion_summary(value: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "stage",
        "source_max_sampled_at",
        "source_documents_expected",
        "source_documents_processed",
        "probe_runs_processed",
        "changed_accounts",
        "changed_fields",
        "change_batches",
        "checkpoint_documents",
        "converted_at",
    )
    return {key: value.get(key) for key in keys}


if __name__ == "__main__":
    asyncio.run(main())
