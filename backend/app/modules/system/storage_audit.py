from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.sub2api.account_history import build_history_change, chunk_history_changes


async def collect_storage_audit(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    names = await db.list_collection_names()
    raw_stats = await asyncio.gather(*(db.command("collStats", name) for name in names))
    collection_stats = [
        {
            "name": str(item.get("ns") or "").split(".", 1)[-1],
            "count": int(item.get("count") or 0),
            "size": int(item.get("size") or 0),
            "storage_size": int(item.get("storageSize") or 0),
            "index_size": int(item.get("totalIndexSize") or 0),
        }
        for item in raw_stats
    ]
    summary = summarize_storage_stats(collection_stats)
    summary["duplication_signals"] = await _duplication_signals(db)
    return summary


def summarize_storage_stats(collection_stats: list[dict[str, Any]]) -> dict[str, Any]:
    logical_size = sum(int(item.get("size") or 0) for item in collection_stats)
    storage_size = sum(int(item.get("storage_size") or 0) for item in collection_stats)
    index_size = sum(int(item.get("index_size") or 0) for item in collection_stats)
    collections = []
    for item in collection_stats:
        count = int(item.get("count") or 0)
        size = int(item.get("size") or 0)
        collections.append(
            {
                **item,
                "logical_share_percent": round(size / logical_size * 100, 2) if logical_size else 0.0,
                "average_document_bytes": round(size / count, 2) if count else 0.0,
            }
        )
    collections.sort(key=lambda item: int(item.get("size") or 0), reverse=True)
    return {
        "logical_size": logical_size,
        "storage_size": storage_size,
        "index_size": index_size,
        "collections": collections,
    }


def summarize_history_storage_estimate(
    *,
    old_document_count: int,
    old_bson_bytes: int,
    new_document_count: int,
    new_bson_bytes: int,
    elapsed_hours: float,
    checkpoint_document_count: int,
    checkpoint_bson_bytes: int,
    old_retention_days: int = 14,
    change_retention_days: int = 30,
    checkpoint_retention_days: int = 365,
) -> dict[str, Any]:
    elapsed_hours = max(float(elapsed_hours), 1 / 60)
    projected_hours = 30 * 24
    projected_old = round(max(0, old_bson_bytes) / elapsed_hours * projected_hours)
    projected_changes = round(max(0, new_bson_bytes) / elapsed_hours * projected_hours)
    projected_checkpoints = max(0, checkpoint_bson_bytes) * 30
    projected_new = projected_changes + projected_checkpoints
    projected_old_retained = round(max(0, old_bson_bytes) / elapsed_hours * max(1, old_retention_days) * 24)
    projected_change_retained = round(max(0, new_bson_bytes) / elapsed_hours * max(1, change_retention_days) * 24)
    projected_checkpoint_retained = max(0, checkpoint_bson_bytes) * max(1, checkpoint_retention_days)
    projected_new_retained = projected_change_retained + projected_checkpoint_retained
    return {
        "elapsed_hours": elapsed_hours,
        "old_document_count": max(0, old_document_count),
        "old_bson_bytes": max(0, old_bson_bytes),
        "new_document_count": max(0, new_document_count),
        "new_bson_bytes": max(0, new_bson_bytes),
        "checkpoint_document_count": max(0, checkpoint_document_count),
        "checkpoint_bson_bytes": max(0, checkpoint_bson_bytes),
        "observed_document_reduction_percent": _reduction_percent(old_document_count, new_document_count),
        "observed_byte_reduction_percent": _reduction_percent(old_bson_bytes, new_bson_bytes),
        "projected_old_30d_bytes": projected_old,
        "projected_change_30d_bytes": projected_changes,
        "projected_checkpoint_30d_bytes": projected_checkpoints,
        "projected_new_30d_bytes": projected_new,
        "projected_30d_byte_reduction_percent": _reduction_percent(projected_old, projected_new),
        "old_retention_days": max(1, old_retention_days),
        "change_retention_days": max(1, change_retention_days),
        "checkpoint_retention_days": max(1, checkpoint_retention_days),
        "projected_old_retained_bytes": projected_old_retained,
        "projected_change_retained_bytes": projected_change_retained,
        "projected_checkpoint_retained_bytes": projected_checkpoint_retained,
        "projected_new_retained_bytes": projected_new_retained,
        "projected_retained_byte_reduction_percent": _reduction_percent(projected_old_retained, projected_new_retained),
    }


def simulate_account_change_batches(
    samples: list[dict[str, Any]],
    *,
    cutoff: datetime | None = None,
) -> dict[str, Any]:
    cutoff_utc = _audit_datetime(cutoff) if cutoff is not None else None
    runs: dict[tuple[str, str], dict[str, Any]] = {}
    for index, sample in enumerate(samples):
        site_id = str(sample.get("site_id") or "")
        observed_at = _audit_datetime(sample.get("sampled_at") or sample.get("created_at"))
        run_id = str(sample.get("probe_run_id") or f"legacy-{observed_at.isoformat()}-{index}")
        run = runs.setdefault(
            (site_id, run_id),
            {"site_id": site_id, "run_id": run_id, "observed_at": observed_at, "samples": {}},
        )
        if observed_at > run["observed_at"]:
            run["observed_at"] = observed_at
        identity_id = str(sample.get("identity_id") or "")
        if identity_id:
            run["samples"][identity_id] = sample

    baselines: dict[str, dict[str, Any]] = {}
    documents: list[dict[str, Any]] = []
    changed_accounts = 0
    changed_fields = 0
    for run in sorted(runs.values(), key=lambda item: (item["observed_at"], item["site_id"], item["run_id"])):
        changes: list[dict[str, Any]] = []
        for identity_id, sample in sorted(run["samples"].items()):
            current = {
                "usage": dict(sample.get("usage_snapshot") if isinstance(sample.get("usage_snapshot"), dict) else {}),
                "subscription": dict(
                    sample.get("subscription_snapshot")
                    if isinstance(sample.get("subscription_snapshot"), dict)
                    else {}
                ),
            }
            previous = baselines.get(identity_id)
            baselines[identity_id] = current
            if previous is None:
                continue
            change = build_history_change(
                identity_id=identity_id,
                remote_account_id=sample.get("remote_account_id"),
                previous=previous,
                current=current,
                occurrence_id=f"{run['site_id']}:{run['run_id']}",
            )
            if change is not None and (cutoff_utc is None or run["observed_at"] >= cutoff_utc):
                changes.append(change)
        if not changes:
            continue
        batches = chunk_history_changes(
            changes,
            site_id=run["site_id"],
            run_id=run["run_id"],
            observed_at=run["observed_at"],
        )
        documents.extend(batches)
        changed_accounts += len(changes)
        changed_fields += sum(len(change.get("changes") or {}) + len(change.get("unset") or []) for change in changes)
    return {
        "documents": documents,
        "changed_accounts": changed_accounts,
        "changed_fields": changed_fields,
        "source_documents": len(samples),
        "runs": len(runs),
    }


def _reduction_percent(old_value: int | float, new_value: int | float) -> float:
    if old_value <= 0:
        return 0.0
    return round((1 - max(0, new_value) / old_value) * 100, 2)


def _audit_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return datetime.min.replace(tzinfo=UTC)


async def _duplication_signals(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    (
        dashboard_trends_raw,
        dashboard_models_raw,
        dashboard_snapshots_raw,
        duplicated_group_summaries,
        legacy_capacity_samples,
        legacy_probe_samples,
        change_batches,
        daily_checkpoints,
    ) = await asyncio.gather(
        db.sub2api_dashboard_trends.count_documents({"raw": {"$exists": True}}),
        db.sub2api_dashboard_models.count_documents({"raw": {"$exists": True}}),
        db.sub2api_dashboard_snapshots.count_documents({"raw": {"$exists": True}}),
        db.sub2api_groups_cache.count_documents({"group.capacity_summary": {"$exists": True}}),
        db.sub2api_capacity_samples.count_documents({"schema_version": {"$ne": 2}}),
        db.remote_account_probe_samples.count_documents({}),
        db.remote_account_change_batches.count_documents({}),
        db.remote_account_daily_checkpoints.count_documents({}),
    )
    latest_probe, latest_change, latest_checkpoint = await asyncio.gather(
        db.remote_account_probe_samples.find_one(
            {},
            {"schema_version": 1, "sampled_at": 1, "bucket_at": 1},
            sort=[("$natural", -1)],
        ),
        db.remote_account_change_batches.find_one(
            {},
            {"schema_version": 1, "observed_at": 1},
            sort=[("$natural", -1)],
        ),
        db.remote_account_daily_checkpoints.find_one(
            {},
            {"schema_version": 1, "checkpoint_at": 1, "document_type": 1},
            sort=[("$natural", -1)],
        ),
    )
    return {
        "dashboard_trends_with_raw": dashboard_trends_raw,
        "dashboard_models_with_raw": dashboard_models_raw,
        "dashboard_snapshots_with_raw": dashboard_snapshots_raw,
        "groups_with_duplicated_capacity_summary": duplicated_group_summaries,
        "legacy_capacity_samples": legacy_capacity_samples,
        "legacy_probe_samples": legacy_probe_samples,
        "account_change_batches": change_batches,
        "account_daily_checkpoints": daily_checkpoints,
        "latest_probe_sample_schema_version": (latest_probe or {}).get("schema_version", 1 if latest_probe else None),
        "latest_probe_sampled_at": (latest_probe or {}).get("sampled_at"),
        "latest_account_change_schema_version": (latest_change or {}).get("schema_version"),
        "latest_account_change_at": (latest_change or {}).get("observed_at"),
        "latest_daily_checkpoint_schema_version": (latest_checkpoint or {}).get("schema_version"),
        "latest_daily_checkpoint_at": (latest_checkpoint or {}).get("checkpoint_at"),
    }
