from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.client_metrics.models import QUALITY_COMPLETE
from app.utils import serialize_doc


MAX_QUERY_MINUTES = 10_080


async def list_client_minute_metrics(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    start_at: datetime,
    end_at: datetime,
    limit: int = 1_440,
) -> dict[str, Any]:
    start_at = _aware_utc(start_at, "start_at").replace(second=0, microsecond=0)
    end_at = _aware_utc(end_at, "end_at").replace(second=0, microsecond=0)
    if end_at <= start_at:
        raise ValueError("end_at must be after start_at")
    total_minutes = int((end_at - start_at).total_seconds() // 60)
    if total_minutes > MAX_QUERY_MINUTES:
        raise ValueError(f"metric range cannot exceed {MAX_QUERY_MINUTES} minutes")
    if limit < 1 or limit > MAX_QUERY_MINUTES:
        raise ValueError(f"limit must be between 1 and {MAX_QUERY_MINUTES}")
    if total_minutes > limit:
        raise ValueError("limit must cover every minute in the requested range")

    projection = {
        "_id": 0,
        "site_id": 1,
        "client_type": 1,
        "bucket_at": 1,
        "sampled_at": 1,
        "rpm": 1,
        "tpm": 1,
        "quality": 1,
        "source": 1,
        "source_updated_at": 1,
        "elapsed_seconds": 1,
        "error_code": 1,
    }
    cursor = (
        db.client_minute_metrics.find(
            {
                "site_id": site_id,
                "bucket_at": {"$gte": start_at, "$lt": end_at},
            },
            projection,
        )
        .sort("bucket_at", 1)
        .limit(limit)
    )
    documents = [doc async for doc in cursor]
    observed_buckets = {
        _mongo_utc(doc["bucket_at"])
        for doc in documents
        if isinstance(doc.get("bucket_at"), datetime)
    }
    complete_minutes = sum(
        1
        for doc in documents
        if doc.get("quality") == QUALITY_COMPLETE
        and doc.get("rpm") is not None
        and doc.get("tpm") is not None
    )
    gap_minutes = max(0, total_minutes - len(observed_buckets))
    missing_minutes = max(0, total_minutes - complete_minutes)
    return {
        "site_id": site_id,
        "start_at": start_at,
        "end_at": end_at,
        "total_minutes": total_minutes,
        "complete_minutes": complete_minutes,
        "missing_minutes": missing_minutes,
        "gap_minutes": gap_minutes,
        "completeness_ratio": round(complete_minutes / total_minutes, 6) if total_minutes else 0.0,
        "items": [serialize_doc(doc) for doc in documents],
    }


async def get_client_metric_status(db: AsyncIOMotorDatabase, *, site_id: str) -> dict[str, Any]:
    state = await db.client_metric_sampler_state.find_one({"_id": site_id})
    if not state:
        return {
            "site_id": site_id,
            "client_type": None,
            "last_attempt_at": None,
            "last_success_at": None,
            "last_bucket_at": None,
            "last_quality": None,
            "last_rpm": None,
            "last_tpm": None,
            "consecutive_failures": 0,
            "last_error": None,
            "updated_at": None,
        }
    public_fields = {
        "site_id": site_id,
        "client_type": state.get("client_type"),
        "last_attempt_at": state.get("last_attempt_at"),
        "last_success_at": state.get("last_success_at"),
        "last_bucket_at": state.get("last_bucket_at"),
        "last_quality": state.get("last_quality"),
        "last_rpm": state.get("last_rpm"),
        "last_tpm": state.get("last_tpm"),
        "consecutive_failures": int(state.get("consecutive_failures") or 0),
        "last_error": state.get("last_error") or None,
        "source_updated_at": state.get("source_updated_at"),
        "updated_at": state.get("updated_at"),
    }
    return serialize_doc(public_fields)


def _aware_utc(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None:
        raise ValueError(f"{field_name} must include a timezone")
    return value.astimezone(UTC)


def _mongo_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
