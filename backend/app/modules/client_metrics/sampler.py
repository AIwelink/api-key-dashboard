from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.client_metrics.adapters.base import ClientMetricAdapter
from app.modules.client_metrics.adapters.newapi import NewApiMetricAdapter
from app.modules.client_metrics.adapters.sub2api import Sub2ApiMetricAdapter
from app.modules.client_metrics.models import AdapterSample, QUALITY_COMPLETE, QUALITY_MISSING
from app.utils import now_utc


logger = logging.getLogger("app.client_metric_sampler")
SAMPLE_OFFSET_SECONDS = 5
DEFAULT_RETENTION_DAYS = 90
MAX_SITE_CONCURRENCY = 12
CURSOR_FIELDS = (
    "source_bucket_at",
    "total_requests",
    "total_tokens",
    "cursor_sampled_at",
    "source_updated_at",
)
CLIENT_SITE_PROJECTION = {
    "_id": 1,
    "client_type": 1,
    "base_url": 1,
    "api_key": 1,
    "admin_user_id": 1,
    "status": 1,
    "data_retention_days": 1,
}

AdapterFactory = Callable[[str], ClientMetricAdapter]
_site_locks: dict[str, asyncio.Lock] = {}


def default_adapter_factory(client_type: str) -> ClientMetricAdapter:
    if client_type == "newapi":
        return NewApiMetricAdapter()
    if client_type == "sub2api":
        return Sub2ApiMetricAdapter()
    raise ValueError(f"unsupported client type: {client_type}")


async def sample_client_site(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    bucket_at: datetime | None = None,
    adapter_factory: AdapterFactory = default_adapter_factory,
    site: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bucket_at = _as_utc(bucket_at or target_bucket_for(now_utc()))
    lock = _site_locks.setdefault(site_id, asyncio.Lock())
    if lock.locked():
        return {
            "ok": False,
            "site_id": site_id,
            "bucket_at": bucket_at,
            "status": "skipped",
            "quality": QUALITY_MISSING,
            "error_code": "sampling_in_progress",
        }

    async with lock:
        if site is None:
            site = await db.client_sites.find_one(
                {"_id": site_id, "status": "active"},
                CLIENT_SITE_PROJECTION,
            )
        if not site:
            return {
                "ok": False,
                "site_id": site_id,
                "bucket_at": bucket_at,
                "status": "not_found",
                "quality": QUALITY_MISSING,
                "error_code": "client_site_not_found",
            }

        client_type = str(site.get("client_type") or "").strip().lower()
        sample_id = _sample_id(site_id, bucket_at)
        find_metric = getattr(db.client_minute_metrics, "find_one", None)
        existing = await find_metric({"_id": sample_id}) if callable(find_metric) else None
        if existing and existing.get("quality") == QUALITY_COMPLETE:
            return {
                "ok": True,
                "site_id": site_id,
                "client_type": client_type,
                "bucket_at": bucket_at,
                "quality": QUALITY_COMPLETE,
                "rpm": existing.get("rpm"),
                "tpm": existing.get("tpm"),
                "error_code": None,
                "status": "already_complete",
            }
        previous_state = await db.client_metric_sampler_state.find_one({"_id": site_id}) or {}
        cursor = {key: previous_state.get(key) for key in CURSOR_FIELDS if previous_state.get(key) is not None}
        try:
            adapter = adapter_factory(client_type)
            sample = await adapter.sample(site=site, bucket_at=bucket_at, cursor=cursor)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one customer site must not stop the minute batch.
            sample = AdapterSample(
                rpm=None,
                tpm=None,
                quality=QUALITY_MISSING,
                source=f"{client_type or 'unknown'}_adapter",
                error_code=type(exc).__name__,
                cursor=cursor,
            )
            logger.warning(
                "client_metric_site_sample_failed site_id=%s client_type=%s error_type=%s",
                site_id,
                client_type or "unknown",
                type(exc).__name__,
            )

        sampled_at = now_utc()
        retention_days = _retention_days(site.get("data_retention_days"))
        document = _sample_document(
            site_id=site_id,
            client_type=client_type,
            bucket_at=bucket_at,
            sampled_at=sampled_at,
            retention_days=retention_days,
            sample=sample,
        )
        await db.client_minute_metrics.replace_one({"_id": document["_id"]}, document, upsert=True)

        failures = 0 if sample.quality == QUALITY_COMPLETE else int(previous_state.get("consecutive_failures") or 0) + 1
        state_updates: dict[str, Any] = {
            "site_id": site_id,
            "client_type": client_type,
            "last_attempt_at": sampled_at,
            "last_bucket_at": bucket_at,
            "last_quality": sample.quality,
            "last_rpm": sample.rpm,
            "last_tpm": sample.tpm,
            "consecutive_failures": failures,
            "last_error": "" if sample.quality == QUALITY_COMPLETE else (sample.error_code or sample.quality),
            "updated_at": sampled_at,
        }
        if sample.quality == QUALITY_COMPLETE:
            state_updates["last_success_at"] = sampled_at
        state_updates.update(sample.cursor)
        await db.client_metric_sampler_state.update_one(
            {"_id": site_id},
            {"$set": state_updates, "$setOnInsert": {"created_at": sampled_at}},
            upsert=True,
        )
        return {
            "ok": sample.quality == QUALITY_COMPLETE,
            "site_id": site_id,
            "client_type": client_type,
            "bucket_at": bucket_at,
            "quality": sample.quality,
            "rpm": sample.rpm,
            "tpm": sample.tpm,
            "error_code": sample.error_code,
            "status": "completed",
        }


async def sample_all_client_sites(
    db: AsyncIOMotorDatabase,
    *,
    bucket_at: datetime | None = None,
    adapter_factory: AdapterFactory = default_adapter_factory,
    max_concurrency: int = MAX_SITE_CONCURRENCY,
) -> dict[str, Any]:
    bucket_at = _as_utc(bucket_at or target_bucket_for(now_utc()))
    query = {
        "status": "active",
        "client_type": {"$in": ["newapi", "sub2api"]},
        "api_key": {"$exists": True, "$nin": ["", None]},
    }
    sites = [site async for site in db.client_sites.find(query, CLIENT_SITE_PROJECTION)]
    semaphore = asyncio.Semaphore(max(1, max_concurrency))

    async def sample_one(site: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await sample_client_site(
                db,
                site_id=str(site.get("_id") or ""),
                site=site,
                bucket_at=bucket_at,
                adapter_factory=adapter_factory,
            )

    results = await asyncio.gather(*(sample_one(site) for site in sites))
    return {
        "ok": True,
        "bucket_at": bucket_at,
        "sites": len(sites),
        "complete": sum(1 for item in results if item.get("quality") == QUALITY_COMPLETE),
        "missing": sum(1 for item in results if item.get("quality") != QUALITY_COMPLETE),
        "results": results,
    }


async def client_metric_sampler_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        try:
            await asyncio.sleep(seconds_until_next_sample(now_utc()))
            await sample_all_client_sites(db, bucket_at=target_bucket_for(now_utc()))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - preserve the minute loop after one batch failure.
            logger.exception("client_metric_sampler_failed")


def seconds_until_next_sample(reference: datetime) -> float:
    reference = _as_utc(reference)
    target = reference.replace(second=SAMPLE_OFFSET_SECONDS, microsecond=0)
    if reference > target:
        target += timedelta(minutes=1)
    return max(0.0, (target - reference).total_seconds())


def target_bucket_for(reference: datetime) -> datetime:
    reference = _as_utc(reference)
    return reference.replace(second=0, microsecond=0) - timedelta(minutes=1)


def _sample_document(
    *,
    site_id: str,
    client_type: str,
    bucket_at: datetime,
    sampled_at: datetime,
    retention_days: int,
    sample: AdapterSample,
) -> dict[str, Any]:
    document: dict[str, Any] = {
        "_id": _sample_id(site_id, bucket_at),
        "site_id": site_id,
        "client_type": client_type,
        "bucket_at": bucket_at,
        "sampled_at": sampled_at,
        "rpm": sample.rpm,
        "tpm": sample.tpm,
        "quality": sample.quality,
        "source": sample.source,
        "expires_at": bucket_at + timedelta(days=retention_days),
    }
    optional_values = {
        "source_updated_at": sample.source_updated_at,
        "total_requests": sample.total_requests,
        "total_tokens": sample.total_tokens,
        "elapsed_seconds": sample.elapsed_seconds,
        "error_code": sample.error_code,
    }
    document.update({key: value for key, value in optional_values.items() if value is not None})
    return document


def _sample_id(site_id: str, bucket_at: datetime) -> str:
    return f"{site_id}:{bucket_at.strftime('%Y-%m-%dT%H:%MZ')}"


def _retention_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_RETENTION_DAYS
    return max(1, min(days, 3650))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
