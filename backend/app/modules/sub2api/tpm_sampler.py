from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from app.modules.sub2api.cache import (
    _fetch_all_accounts,
    get_site,
    is_sub2api_site,
    list_sites,
    update_cached_account_runtime_fields,
)
from app.modules.sub2api.client import Sub2ApiClient, account_in_group
from app.modules.sub2api.dashboard_postgres_repository import fetch_group_minute_usage
from app.utils import now_utc


logger = logging.getLogger("app.sub2api_tpm_sampler")

TPM_SAMPLE_RETENTION_DAYS = 60
TPM_SAMPLE_INTERVAL_SECONDS = 60
TPM_SAMPLE_SCHEMA_VERSION = 3
TPM_COUNTER_SOURCE = "postgresql_usage_logs_minute"
TPM_RECENT_MINUTES = 20
TPM_BACKFILL_WINDOW = timedelta(hours=1)
TPM_BACKFILL_RANGE = timedelta(days=7)

_site_sample_locks: dict[str, asyncio.Lock] = {}


def _minute_buckets(start_at: datetime, end_at: datetime) -> list[datetime]:
    cursor = _as_utc(start_at).replace(second=0, microsecond=0)
    normalized_end = _as_utc(end_at).replace(second=0, microsecond=0)
    buckets: list[datetime] = []
    while cursor < normalized_end:
        buckets.append(cursor)
        cursor += timedelta(minutes=1)
    return buckets


def _sample_document(
    *,
    site_id: str,
    group_id: int,
    bucket_at: datetime,
    sampled_at: datetime,
    usage: dict[str, Any],
    current_concurrency: float | None,
) -> dict[str, Any]:
    bucket_at = _as_utc(bucket_at).replace(second=0, microsecond=0)
    recorded_at = _as_utc(sampled_at)
    minute_tokens = _nonnegative_integer(usage.get("total_tokens")) or 0
    minute_requests = _nonnegative_integer(usage.get("total_requests")) or 0
    minute_account_cost = _nonnegative_number(usage.get("account_cost")) or 0.0
    sample_id = f"{site_id}:{group_id}:{bucket_at.isoformat().replace('+00:00', 'Z')}"
    return {
        "_id": sample_id,
        "schema_version": TPM_SAMPLE_SCHEMA_VERSION,
        "counter_source": TPM_COUNTER_SOURCE,
        "site_id": site_id,
        "group_id": group_id,
        "bucket_at": bucket_at,
        "sampled_at": bucket_at,
        "recorded_at": recorded_at,
        "stats_updated_at": _datetime_value(usage.get("source_updated_at")),
        "tpm": float(minute_tokens),
        "reported_tpm": None,
        "calculated_tpm": float(minute_tokens),
        "rpm": float(minute_requests),
        "reported_rpm": None,
        "calculated_rpm": float(minute_requests),
        "average_duration_ms": None,
        "current_concurrency": _nonnegative_number(current_concurrency),
        "minute_tokens": minute_tokens,
        "minute_requests": minute_requests,
        "input_tokens": _nonnegative_integer(usage.get("input_tokens")) or 0,
        "output_tokens": _nonnegative_integer(usage.get("output_tokens")) or 0,
        "cache_creation_tokens": _nonnegative_integer(usage.get("cache_creation_tokens")) or 0,
        "cache_read_tokens": _nonnegative_integer(usage.get("cache_read_tokens")) or 0,
        "minute_account_cost": minute_account_cost,
        "account_cost_per_minute": minute_account_cost,
        "account_cost_per_hour": minute_account_cost * 60,
        "total_tokens": minute_tokens,
        "total_requests": minute_requests,
        "token_delta": minute_tokens,
        "request_delta": minute_requests,
        "total_account_cost": minute_account_cost,
        "account_cost_delta": minute_account_cost,
        "elapsed_seconds": 60.0,
        "source": "exact_minute",
        "expires_at": recorded_at + timedelta(days=TPM_SAMPLE_RETENTION_DAYS),
    }


async def _write_minute_samples(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_ids: list[int],
    start_at: datetime,
    end_at: datetime,
    usage_by_key: dict[tuple[int, datetime], dict[str, Any]],
    sampled_at: datetime,
    latest_bucket_at: datetime | None,
    concurrency_by_group: dict[int, float | None] | None,
) -> int:
    normalized_latest = _as_utc(latest_bucket_at).replace(second=0, microsecond=0) if latest_bucket_at else None
    operations: list[UpdateOne] = []
    for group_id in sorted({int(value) for value in group_ids}):
        for bucket_at in _minute_buckets(start_at, end_at):
            is_latest = bucket_at == normalized_latest
            document = _sample_document(
                site_id=site_id,
                group_id=group_id,
                bucket_at=bucket_at,
                sampled_at=sampled_at,
                usage=usage_by_key.get((group_id, bucket_at), {}),
                current_concurrency=(concurrency_by_group or {}).get(group_id) if is_latest else None,
            )
            set_fields = {key: value for key, value in document.items() if key != "_id"}
            update: dict[str, Any] = {"$set": set_fields}
            if not is_latest:
                set_fields.pop("current_concurrency", None)
                update["$setOnInsert"] = {"current_concurrency": None}
            operations.append(UpdateOne({"_id": document["_id"]}, update, upsert=True))
    if not operations:
        return 0
    await db.sub2api_tpm_samples.bulk_write(operations, ordered=False)
    return len(operations)


async def _load_backfill_window(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    closed_end: datetime,
    recent_start: datetime,
) -> tuple[datetime, datetime]:
    state_collection = getattr(db, "sub2api_tpm_backfill_state", None)
    state = await state_collection.find_one({"_id": site_id}) if state_collection is not None else None
    oldest_allowed = closed_end - TPM_BACKFILL_RANGE
    next_window_end = _datetime_value(state.get("next_window_end")) if state else None
    if (
        next_window_end is None
        or next_window_end > recent_start
        or next_window_end - TPM_BACKFILL_WINDOW < oldest_allowed
    ):
        next_window_end = recent_start
    return next_window_end - TPM_BACKFILL_WINDOW, next_window_end


async def _advance_backfill_cursor(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    window_start: datetime,
    window_end: datetime,
    next_window_end: datetime,
    completed_at: datetime,
) -> None:
    state_collection = getattr(db, "sub2api_tpm_backfill_state", None)
    if state_collection is None:
        raise RuntimeError("sub2api TPM backfill state collection is unavailable")
    await state_collection.update_one(
        {"_id": site_id},
        {
            "$set": {
                "site_id": site_id,
                "next_window_end": _as_utc(next_window_end),
                "last_window_start": _as_utc(window_start),
                "last_window_end": _as_utc(window_end),
                "last_completed_at": _as_utc(completed_at),
                "updated_at": _as_utc(completed_at),
            }
        },
        upsert=True,
    )


async def sample_site_tpm(db: AsyncIOMotorDatabase, *, site_id: str) -> dict[str, Any]:
    lock = _site_sample_locks.setdefault(site_id, asyncio.Lock())
    if lock.locked():
        return {"ok": True, "site_id": site_id, "status": "skipped", "message": "TPM sampling already running"}

    async with lock:
        site = await get_site(db, site_id, include_token=True)
        if not site:
            return {"ok": False, "site_id": site_id, "message": "sub2api site not found"}
        if not is_sub2api_site(site):
            return {"ok": False, "site_id": site_id, "message": "site is not a sub2api client"}
        sql_dsn = str(site.get("sql_dsn") or "").strip()
        if not sql_dsn:
            return {
                "ok": False,
                "site_id": site_id,
                "status": "not_configured",
                "error_code": "sql_dsn_not_configured",
                "message": "Sub2API SQL_DSN is required for group TPM sampling",
            }
        group_ids = sorted(
            {
                int(doc["group_id"])
                async for doc in db.sub2api_groups_cache.find({"site_id": site_id}, {"group_id": 1})
                if isinstance(doc.get("group_id"), int)
            }
        )
        if not group_ids:
            return {
                "ok": True,
                "site_id": site_id,
                "status": "completed",
                "groups": 0,
                "sampled": 0,
                "documents_written": 0,
                "failed": 0,
            }

        sampled_at = now_utc()
        closed_end = sampled_at.replace(second=0, microsecond=0)
        recent_start = closed_end - timedelta(minutes=TPM_RECENT_MINUTES)
        latest_bucket = closed_end - timedelta(minutes=1)
        try:
            historical_start, historical_end = await _load_backfill_window(
                db,
                site_id=site_id,
                closed_end=closed_end,
                recent_start=recent_start,
            )
            recent_usage = await fetch_group_minute_usage(
                sql_dsn,
                group_ids=group_ids,
                start_at=recent_start,
                end_at=closed_end,
            )
        except Exception as exc:  # noqa: BLE001 - exact recent usage is required for a valid sample.
            logger.warning("sub2api_tpm_recent_sample_failed site_id=%s error_type=%s", site_id, type(exc).__name__)
            return {
                "ok": False,
                "site_id": site_id,
                "status": "failed",
                "error_code": "database_read_failed",
                "message": type(exc).__name__,
            }

        client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
        try:
            accounts = await _fetch_all_accounts(client)
            try:
                await update_cached_account_runtime_fields(db, site_id, accounts)
            except Exception as exc:  # noqa: BLE001 - runtime cache enrichment must not fail metric sampling.
                logger.warning("sub2api_runtime_cache_update_failed site_id=%s error_type=%s", site_id, type(exc).__name__)
            concurrency_by_group = _group_current_concurrency(accounts, group_ids)
        except Exception as exc:  # noqa: BLE001 - dashboard sampling can continue without concurrency.
            logger.warning("sub2api_concurrency_sample_failed site_id=%s error_type=%s", site_id, type(exc).__name__)
            concurrency_by_group = {group_id: None for group_id in group_ids}

        try:
            recent_sampled = await _write_minute_samples(
                db,
                site_id=site_id,
                group_ids=group_ids,
                start_at=recent_start,
                end_at=closed_end,
                usage_by_key=recent_usage,
                sampled_at=sampled_at,
                latest_bucket_at=latest_bucket,
                concurrency_by_group=concurrency_by_group,
            )
        except Exception as exc:  # noqa: BLE001 - do not report a sample that was not stored.
            logger.warning("sub2api_tpm_recent_write_failed site_id=%s error_type=%s", site_id, type(exc).__name__)
            return {
                "ok": False,
                "site_id": site_id,
                "status": "failed",
                "error_code": "recent_write_failed",
                "message": type(exc).__name__,
            }

        historical_sampled = 0
        try:
            historical_usage = await fetch_group_minute_usage(
                sql_dsn,
                group_ids=group_ids,
                start_at=historical_start,
                end_at=historical_end,
            )
            historical_sampled = await _write_minute_samples(
                db,
                site_id=site_id,
                group_ids=group_ids,
                start_at=historical_start,
                end_at=historical_end,
                usage_by_key=historical_usage,
                sampled_at=sampled_at,
                latest_bucket_at=None,
                concurrency_by_group=None,
            )
            next_window_end = historical_start
            if next_window_end <= closed_end - TPM_BACKFILL_RANGE:
                next_window_end = recent_start
            await _advance_backfill_cursor(
                db,
                site_id=site_id,
                window_start=historical_start,
                window_end=historical_end,
                next_window_end=next_window_end,
                completed_at=sampled_at,
            )
            historical_ok = True
        except Exception as exc:  # noqa: BLE001 - recent samples remain valid when historical repair fails.
            logger.warning("sub2api_tpm_historical_backfill_failed site_id=%s error_type=%s", site_id, type(exc).__name__)
            historical_ok = False

        return {
            "ok": True,
            "site_id": site_id,
            "status": "completed",
            "groups": len(group_ids),
            "sampled": len(group_ids),
            "documents_written": recent_sampled + historical_sampled,
            "failed": 0 if historical_ok else 1,
            "recent_sampled": recent_sampled,
            "historical_sampled": historical_sampled,
            "historical_ok": historical_ok,
        }


async def sample_all_sites_tpm(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    sites = [
        site
        for site in (await list_sites(db, site_type="sub2api")).get("items", [])
        if site and site.get("status") == "active" and is_sub2api_site(site)
    ]

    async def sample_one(site: dict[str, Any]) -> dict[str, Any]:
        site_id = str(site.get("id"))
        try:
            return await sample_site_tpm(db, site_id=site_id)
        except Exception as exc:  # noqa: BLE001 - one site must not block other samples.
            logger.warning("sub2api_tpm_site_sample_failed site_id=%s error=%s", site_id, exc)
            return {"ok": False, "site_id": site_id, "message": str(exc)}

    results = await asyncio.gather(*(sample_one(site) for site in sites))
    return {
        "ok": True,
        "sites": len(sites),
        "site_failures": sum(1 for item in results if item.get("ok") is False),
        "sampled": sum(int(item.get("sampled") or 0) for item in results),
        "failed": sum(int(item.get("failed") or 0) for item in results),
        "results": results,
    }


async def tpm_sampler_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        started = time.monotonic()
        try:
            await sample_all_sites_tpm(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sub2api_tpm_sampler_failed")
        elapsed_seconds = time.monotonic() - started
        await asyncio.sleep(max(0.0, TPM_SAMPLE_INTERVAL_SECONDS - elapsed_seconds))


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _nonnegative_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _nonnegative_integer(value: Any) -> int | None:
    number = _nonnegative_number(value)
    return int(number) if number is not None else None


def _group_current_concurrency(
    accounts: list[dict[str, Any]],
    group_ids: list[int],
) -> dict[int, float]:
    totals = {group_id: 0.0 for group_id in group_ids}
    for account in accounts:
        current = _nonnegative_number(account.get("current_concurrency"))
        if current is None:
            continue
        for group_id in group_ids:
            if account_in_group(account, group_id):
                totals[group_id] += current
    return totals


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
