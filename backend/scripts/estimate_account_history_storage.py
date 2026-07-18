from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from bson import BSON

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.sub2api.account_history import (
    CHANGE_RETENTION_DAYS,
    CHECKPOINT_RETENTION_DAYS,
    CHECKPOINT_SCHEMA_VERSION,
    SHANGHAI_TZ,
    build_daily_checkpoint_documents,
)
from app.modules.system.storage_audit import simulate_account_change_batches, summarize_history_storage_estimate
from app.utils import serialize_doc


async def main() -> None:
    parser = argparse.ArgumentParser(description="Read-only legacy account snapshot versus change-history BSON estimator")
    parser.add_argument("--hours", type=float, default=6, help="observed legacy window in hours")
    parser.add_argument("--warmup-hours", type=float, default=1, help="earlier baseline window excluded from totals")
    parser.add_argument("--old-retention-days", type=int, default=14, help="legacy snapshot TTL used for steady-state projection")
    parser.add_argument("--site-id", default="", help="optional site scope")
    parser.add_argument("--json", action="store_true", help="print machine-readable JSON")
    args = parser.parse_args()

    await connect_to_mongo()
    try:
        report = await estimate_account_history_storage(
            get_db(),
            hours=max(args.hours, 1 / 60),
            warmup_hours=max(args.warmup_hours, 0),
            old_retention_days=max(args.old_retention_days, 1),
            site_id=str(args.site_id or "").strip() or None,
        )
    finally:
        await close_mongo_connection()

    if args.json:
        print(json.dumps(serialize_doc(report), ensure_ascii=False, indent=2))
        return
    print_report(report)


async def estimate_account_history_storage(
    db: Any,
    *,
    hours: float,
    warmup_hours: float,
    old_retention_days: int,
    site_id: str | None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    cutoff = now - timedelta(hours=hours)
    replay_cutoff = cutoff - timedelta(hours=warmup_hours)
    observed_query: dict[str, Any] = {"sampled_at": {"$gte": cutoff}}
    replay_query: dict[str, Any] = {"sampled_at": {"$gte": replay_cutoff}}
    identity_query: dict[str, Any] = {"current_presence": "present"}
    if site_id:
        observed_query["site_id"] = site_id
        replay_query["site_id"] = site_id
        identity_query["site_id"] = site_id

    old_stats, old_byte_source = await _full_bson_stats(db, observed_query)
    projection = {
        "site_id": 1,
        "probe_run_id": 1,
        "identity_id": 1,
        "remote_account_id": 1,
        "sampled_at": 1,
        "created_at": 1,
        "usage_snapshot": 1,
        "subscription_snapshot": 1,
    }
    samples = [
        sample
        async for sample in db.remote_account_probe_samples.find(replay_query, projection).sort("sampled_at", 1)
    ]
    simulated = simulate_account_change_batches(samples, cutoff=cutoff)
    change_bson_bytes = sum(len(BSON.encode(document)) for document in simulated["documents"])

    identities = [
        identity
        async for identity in db.remote_account_identities.find(
            identity_query,
            {
                "site_id": 1,
                "last_usage_snapshot": 1,
                "current_subscription_snapshot": 1,
                "cumulative_usage_snapshot": 1,
            },
        )
    ]
    checkpoint_documents = _checkpoint_documents(identities, checkpoint_at=now)
    checkpoint_bson_bytes = sum(len(BSON.encode(document)) for document in checkpoint_documents)

    if old_stats["count"] == 0 and samples:
        observed_samples = [sample for sample in samples if _datetime(sample.get("sampled_at")) >= cutoff]
        old_stats = {
            "count": len(observed_samples),
            "bson_bytes": sum(len(BSON.encode(sample)) for sample in observed_samples),
            "first_at": min((_datetime(sample.get("sampled_at")) for sample in observed_samples), default=None),
            "last_at": max((_datetime(sample.get("sampled_at")) for sample in observed_samples), default=None),
        }
        old_byte_source = "projected_documents_fallback"

    report = summarize_history_storage_estimate(
        old_document_count=old_stats["count"],
        old_bson_bytes=old_stats["bson_bytes"],
        new_document_count=len(simulated["documents"]),
        new_bson_bytes=change_bson_bytes,
        elapsed_hours=hours,
        checkpoint_document_count=len(checkpoint_documents),
        checkpoint_bson_bytes=checkpoint_bson_bytes,
        old_retention_days=old_retention_days,
        change_retention_days=CHANGE_RETENTION_DAYS,
        checkpoint_retention_days=CHECKPOINT_RETENTION_DAYS,
    )
    report.update(
        {
            "read_only": True,
            "site_id": site_id,
            "window_start": cutoff,
            "window_end": now,
            "first_legacy_sample_at": old_stats.get("first_at"),
            "last_legacy_sample_at": old_stats.get("last_at"),
            "old_bson_measurement": old_byte_source,
            "warmup_hours": warmup_hours,
            "replay_source_documents": simulated["source_documents"],
            "probe_runs_replayed": simulated["runs"],
            "changed_accounts": simulated["changed_accounts"],
            "changed_fields": simulated["changed_fields"],
            "checkpoint_accounts": len(identities),
            "projected_retained_bytes_saved": report["projected_old_retained_bytes"] - report["projected_new_retained_bytes"],
        }
    )
    return report


async def _full_bson_stats(db: Any, query: dict[str, Any]) -> tuple[dict[str, Any], str]:
    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "count": {"$sum": 1},
                "bson_bytes": {"$sum": {"$bsonSize": "$$ROOT"}},
                "first_at": {"$min": "$sampled_at"},
                "last_at": {"$max": "$sampled_at"},
            }
        },
    ]
    try:
        values = [item async for item in db.remote_account_probe_samples.aggregate(pipeline, allowDiskUse=True)]
    except Exception:
        return {"count": 0, "bson_bytes": 0, "first_at": None, "last_at": None}, "projection_fallback"
    if not values:
        return {"count": 0, "bson_bytes": 0, "first_at": None, "last_at": None}, "server_bson_size"
    value = values[0]
    return {
        "count": int(value.get("count") or 0),
        "bson_bytes": int(value.get("bson_bytes") or 0),
        "first_at": value.get("first_at"),
        "last_at": value.get("last_at"),
    }, "server_bson_size"


def _checkpoint_documents(identities: list[dict[str, Any]], *, checkpoint_at: datetime) -> list[dict[str, Any]]:
    by_site: dict[str, list[dict[str, Any]]] = {}
    for identity in identities:
        site_id = str(identity.get("site_id") or "")
        if site_id:
            by_site.setdefault(site_id, []).append(identity)
    result: list[dict[str, Any]] = []
    local_date = checkpoint_at.astimezone(SHANGHAI_TZ).date().isoformat()
    for current_site_id, site_identities in sorted(by_site.items()):
        chunks = build_daily_checkpoint_documents(
            site_identities,
            site_id=current_site_id,
            checkpoint_at=checkpoint_at,
        )
        result.extend(chunks)
        result.append(
            {
                "_id": f"{current_site_id}:{local_date}:manifest",
                "schema_version": CHECKPOINT_SCHEMA_VERSION,
                "document_type": "manifest",
                "site_id": current_site_id,
                "local_date": local_date,
                "checkpoint_at": checkpoint_at,
                "chunk_count": len(chunks),
                "entry_count": sum(int(chunk.get("entry_count") or 0) for chunk in chunks),
                "complete": True,
                "expires_at": checkpoint_at + timedelta(days=CHECKPOINT_RETENTION_DAYS),
            }
        )
    return result


def print_report(report: dict[str, Any]) -> None:
    print("Account history storage estimate (read-only)")
    print(f"window={report['elapsed_hours']:.2f}h warmup={report['warmup_hours']:.2f}h site={report.get('site_id') or 'all'}")
    print(
        f"legacy={report['old_document_count']:,} docs / {_format_bytes(report['old_bson_bytes'])} "
        f"({report['old_bson_measurement']})"
    )
    print(
        f"changes={report['new_document_count']:,} batches / {_format_bytes(report['new_bson_bytes'])} "
        f"accounts_changed={report['changed_accounts']:,} fields={report['changed_fields']:,}"
    )
    print(
        f"observed_reduction=documents {report['observed_document_reduction_percent']:.2f}% / "
        f"logical_bson {report['observed_byte_reduction_percent']:.2f}%"
    )
    print(
        f"daily_checkpoint={report['checkpoint_document_count']:,} docs / "
        f"{_format_bytes(report['checkpoint_bson_bytes'])} / {report['checkpoint_accounts']:,} accounts"
    )
    print(
        f"30d_projection=old {_format_bytes(report['projected_old_30d_bytes'])} / "
        f"new {_format_bytes(report['projected_new_30d_bytes'])} / "
        f"saved {report['projected_30d_byte_reduction_percent']:.2f}%"
    )
    print(
        f"ttl_steady_state=old({report['old_retention_days']}d) {_format_bytes(report['projected_old_retained_bytes'])} / "
        f"new(changes {report['change_retention_days']}d + checkpoints {report['checkpoint_retention_days']}d) "
        f"{_format_bytes(report['projected_new_retained_bytes'])} / "
        f"saved {_format_bytes(report['projected_retained_bytes_saved'])} "
        f"({report['projected_retained_byte_reduction_percent']:.2f}%)"
    )


def _datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


def _format_bytes(value: Any) -> str:
    size = float(value or 0)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if size < 1024 or unit == "TiB":
            return f"{size:.2f}{unit}"
        size /= 1024
    return f"{size:.2f}TiB"


if __name__ == "__main__":
    asyncio.run(main())
