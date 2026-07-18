from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReplaceOne

from app.modules.sub2api.capacity_sampler import (
    CAPACITY_SAMPLE_RETENTION_DAYS,
    CAPACITY_SAMPLE_SCHEMA_VERSION,
    _capacity_dimensions,
    _scalar_capacity_metrics,
)


DASHBOARD_RAW_COLLECTIONS = (
    "sub2api_dashboard_trends",
    "sub2api_dashboard_models",
    "sub2api_dashboard_snapshots",
)
LEGACY_CAPACITY_QUERY = {"schema_version": {"$ne": CAPACITY_SAMPLE_SCHEMA_VERSION}}


def compact_legacy_capacity_sample(document: dict[str, Any]) -> dict[str, Any]:
    sampled_at = _required_datetime(document.get("sampled_at") or document.get("bucket_at"), "sampled_at")
    bucket_at = _required_datetime(document.get("bucket_at") or sampled_at, "bucket_at")
    summary = document.get("capacity_summary")
    if not isinstance(summary, dict):
        raise ValueError("capacity_summary is missing")
    sample_id = document.get("_id")
    site_id = str(document.get("site_id") or "").strip()
    group_id = document.get("group_id")
    if sample_id is None:
        raise ValueError("_id is missing")
    if not site_id:
        raise ValueError("site_id is missing")
    if group_id is None:
        raise ValueError("group_id is missing")

    retention_expires_at = sampled_at + timedelta(days=CAPACITY_SAMPLE_RETENTION_DAYS)
    existing_expires_at = _optional_datetime(document.get("expires_at"))
    expires_at = min(existing_expires_at, retention_expires_at) if existing_expires_at else retention_expires_at
    return {
        "_id": sample_id,
        "schema_version": CAPACITY_SAMPLE_SCHEMA_VERSION,
        "site_id": site_id,
        "group_id": group_id,
        "bucket_at": bucket_at,
        "sampled_at": sampled_at,
        "account_cache_fetched_at": document.get("account_cache_fetched_at"),
        "capacity_calculated_at": document.get("capacity_calculated_at") or summary.get("calculated_at"),
        "metrics": _scalar_capacity_metrics(summary),
        "dimensions": _capacity_dimensions(summary),
        "expires_at": expires_at,
    }


async def collect_obsolete_storage_counts(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    raw_counts, duplicated_group_summaries, legacy_capacity_samples = await asyncio.gather(
        asyncio.gather(
            *(getattr(db, name).count_documents({"raw": {"$exists": True}}) for name in DASHBOARD_RAW_COLLECTIONS)
        ),
        db.sub2api_groups_cache.count_documents({"group.capacity_summary": {"$exists": True}}),
        db.sub2api_capacity_samples.count_documents(LEGACY_CAPACITY_QUERY),
    )
    dashboard_raw_by_collection = dict(zip(DASHBOARD_RAW_COLLECTIONS, raw_counts, strict=True))
    return {
        "dashboard_raw_documents": sum(int(value) for value in raw_counts),
        "dashboard_raw_by_collection": dashboard_raw_by_collection,
        "duplicated_group_summaries": int(duplicated_group_summaries),
        "legacy_capacity_samples": int(legacy_capacity_samples),
    }


async def cleanup_obsolete_storage(
    db: AsyncIOMotorDatabase,
    *,
    execute: bool = False,
    batch_size: int = 200,
) -> dict[str, Any]:
    before = await collect_obsolete_storage_counts(db)
    report: dict[str, Any] = {
        "mode": "execute" if execute else "dry-run",
        "before": before,
        "results": {},
    }
    if not execute:
        return report

    raw_results: dict[str, dict[str, int]] = {}
    for name in DASHBOARD_RAW_COLLECTIONS:
        result = await getattr(db, name).update_many(
            {"raw": {"$exists": True}},
            {"$unset": {"raw": ""}},
        )
        raw_results[name] = {
            "matched": int(result.matched_count),
            "modified": int(result.modified_count),
        }

    group_result = await db.sub2api_groups_cache.update_many(
        {"group.capacity_summary": {"$exists": True}},
        {"$unset": {"group.capacity_summary": ""}},
    )
    capacity_result = await _compact_legacy_capacity_samples(db, batch_size=max(1, int(batch_size)))
    report["results"] = {
        "dashboard_raw_documents": raw_results,
        "duplicated_group_summaries": {
            "matched": int(group_result.matched_count),
            "modified": int(group_result.modified_count),
        },
        "legacy_capacity_samples": capacity_result,
    }
    report["after"] = await collect_obsolete_storage_counts(db)
    return report


async def _compact_legacy_capacity_samples(
    db: AsyncIOMotorDatabase,
    *,
    batch_size: int,
) -> dict[str, Any]:
    operations: list[ReplaceOne] = []
    scanned = 0
    matched = 0
    modified = 0
    failed = 0
    failed_ids: list[str] = []

    async for document in db.sub2api_capacity_samples.find(LEGACY_CAPACITY_QUERY):
        scanned += 1
        try:
            compact = compact_legacy_capacity_sample(document)
        except (TypeError, ValueError):
            failed += 1
            if len(failed_ids) < 20:
                failed_ids.append(str(document.get("_id") or ""))
            continue
        operations.append(
            ReplaceOne(
                {"_id": document["_id"], "schema_version": {"$ne": CAPACITY_SAMPLE_SCHEMA_VERSION}},
                compact,
                upsert=False,
            )
        )
        if len(operations) >= batch_size:
            result = await db.sub2api_capacity_samples.bulk_write(operations, ordered=False)
            matched += int(result.matched_count)
            modified += int(result.modified_count)
            operations = []

    if operations:
        result = await db.sub2api_capacity_samples.bulk_write(operations, ordered=False)
        matched += int(result.matched_count)
        modified += int(result.modified_count)
    return {
        "scanned": scanned,
        "matched": matched,
        "modified": modified,
        "failed": failed,
        "failed_ids": failed_ids,
    }


def _required_datetime(value: Any, field: str) -> datetime:
    parsed = _optional_datetime(value)
    if parsed is None:
        raise ValueError(f"{field} is missing")
    return parsed


def _optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
