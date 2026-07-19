from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.sub2api.cache import (
    _fetch_all_accounts,
    get_site,
    is_sub2api_site,
    list_sites,
    update_cached_account_runtime_fields,
)
from app.modules.sub2api.client import Sub2ApiClient, account_in_group
from app.modules.sub2api.dashboard import parse_bucket_time, parse_remote_datetime
from app.utils import now_utc


logger = logging.getLogger("app.sub2api_tpm_sampler")

TPM_SAMPLE_RETENTION_DAYS = 14
TPM_SAMPLE_INTERVAL_SECONDS = 60
TPM_SAMPLE_TIMEZONE = "Asia/Shanghai"
TPM_SAMPLE_SCHEMA_VERSION = 2
SHANGHAI_TZ = timezone(timedelta(hours=8))

_site_sample_locks: dict[str, asyncio.Lock] = {}


async def sample_group_tpm(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    client: Sub2ApiClient,
    sampled_at: datetime | None = None,
    current_concurrency: float | None = None,
) -> dict[str, Any]:
    sampled_at = _as_utc(sampled_at or now_utc())
    bucket_at = sampled_at.replace(second=0, microsecond=0)
    local_date = sampled_at.astimezone(SHANGHAI_TZ).date().isoformat()
    snapshot = await client.get_dashboard_snapshot(
        start_date=local_date,
        end_date=local_date,
        granularity="hour",
        timezone=TPM_SAMPLE_TIMEZONE,
        include_stats=True,
        include_trend=True,
        include_model_stats=False,
        include_group_stats=True,
        include_users_trend=False,
        group_id=group_id,
    )
    stats = snapshot.get("stats") if isinstance(snapshot.get("stats"), dict) else {}
    previous = await db.sub2api_tpm_samples.find_one(
        {
            "site_id": site_id,
            "group_id": group_id,
            "schema_version": TPM_SAMPLE_SCHEMA_VERSION,
            "bucket_at": {"$lt": bucket_at},
        },
        sort=[("bucket_at", -1)],
    )

    total_tokens, total_requests = _current_group_counters(snapshot, sampled_at=sampled_at)
    calculated_tpm, token_delta, elapsed_seconds = _calculate_tpm_from_previous(
        previous=previous,
        current_total_tokens=total_tokens,
        sampled_at=sampled_at,
    )
    calculated_rpm, request_delta, _ = _calculate_counter_rate(
        previous=previous,
        previous_field="total_requests",
        current_value=total_requests,
        sampled_at=sampled_at,
    )
    sample_id = f"{site_id}:{group_id}:{bucket_at.isoformat().replace('+00:00', 'Z')}"
    document = {
        "_id": sample_id,
        "schema_version": TPM_SAMPLE_SCHEMA_VERSION,
        "site_id": site_id,
        "group_id": group_id,
        "bucket_at": bucket_at,
        "sampled_at": sampled_at,
        "stats_updated_at": parse_remote_datetime(stats.get("stats_updated_at")),
        "tpm": calculated_tpm,
        "reported_tpm": None,
        "calculated_tpm": calculated_tpm,
        "rpm": calculated_rpm,
        "reported_rpm": None,
        "calculated_rpm": calculated_rpm,
        "average_duration_ms": None,
        "current_concurrency": _nonnegative_number(current_concurrency),
        "total_tokens": total_tokens,
        "total_requests": total_requests,
        "token_delta": token_delta,
        "request_delta": request_delta,
        "elapsed_seconds": elapsed_seconds,
        "source": "group_trend_delta" if calculated_tpm is not None else "unavailable",
        "expires_at": sampled_at + timedelta(days=TPM_SAMPLE_RETENTION_DAYS),
    }
    await db.sub2api_tpm_samples.replace_one({"_id": sample_id}, document, upsert=True)
    return {"ok": True, "site_id": site_id, "group_id": group_id, "sample": document}


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
        group_ids = sorted(
            {
                int(doc["group_id"])
                async for doc in db.sub2api_groups_cache.find({"site_id": site_id}, {"group_id": 1})
                if isinstance(doc.get("group_id"), int)
            }
        )
        client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
        try:
            accounts = await _fetch_all_accounts(client)
            try:
                await update_cached_account_runtime_fields(db, site_id, accounts)
            except Exception as exc:  # noqa: BLE001 - runtime cache enrichment must not fail metric sampling.
                logger.warning("sub2api_runtime_cache_update_failed site_id=%s error=%s", site_id, exc)
            concurrency_by_group = _group_current_concurrency(accounts, group_ids)
        except Exception as exc:  # noqa: BLE001 - dashboard sampling can continue without concurrency.
            logger.warning("sub2api_concurrency_sample_failed site_id=%s error=%s", site_id, exc)
            concurrency_by_group = {group_id: None for group_id in group_ids}

        async def sample_one(group_id: int) -> dict[str, Any]:
            try:
                return await sample_group_tpm(
                    db,
                    site_id=site_id,
                    group_id=group_id,
                    client=client,
                    current_concurrency=concurrency_by_group.get(group_id),
                )
            except Exception as exc:  # noqa: BLE001 - one group must not block other samples.
                logger.warning("sub2api_tpm_group_sample_failed site_id=%s group_id=%s error=%s", site_id, group_id, exc)
                return {"ok": False, "site_id": site_id, "group_id": group_id, "message": str(exc)}

        results = await asyncio.gather(*(sample_one(group_id) for group_id in group_ids))
        return {
            "ok": True,
            "site_id": site_id,
            "status": "completed",
            "groups": len(group_ids),
            "sampled": sum(1 for item in results if item.get("ok") is True),
            "failed": sum(1 for item in results if item.get("ok") is False),
            "results": results,
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


def _calculate_tpm_from_previous(
    *,
    previous: dict[str, Any] | None,
    current_total_tokens: int | None,
    sampled_at: datetime,
) -> tuple[float | None, int | None, float | None]:
    return _calculate_counter_rate(
        previous=previous,
        previous_field="total_tokens",
        current_value=current_total_tokens,
        sampled_at=sampled_at,
    )


def _calculate_counter_rate(
    *,
    previous: dict[str, Any] | None,
    previous_field: str,
    current_value: int | None,
    sampled_at: datetime,
) -> tuple[float | None, int | None, float | None]:
    if not previous:
        return None, None, None
    previous_sampled_at = _datetime_value(previous.get("sampled_at"))
    if previous_sampled_at is None:
        return None, None, None
    elapsed_seconds = (sampled_at - previous_sampled_at).total_seconds()
    if elapsed_seconds <= 0:
        return None, None, elapsed_seconds
    previous_value = _nonnegative_integer(previous.get(previous_field))
    if previous_value is None or current_value is None:
        return None, None, elapsed_seconds
    delta = current_value - previous_value
    if delta < 0:
        return None, None, elapsed_seconds
    calculated_rate = delta / (elapsed_seconds / 60)
    return calculated_rate, delta, elapsed_seconds


def _current_group_counters(snapshot: dict[str, Any], *, sampled_at: datetime) -> tuple[int, int]:
    current_hour = sampled_at.replace(minute=0, second=0, microsecond=0)
    trend = snapshot.get("trend") if isinstance(snapshot.get("trend"), list) else []
    for item in reversed(trend):
        if not isinstance(item, dict):
            continue
        bucket_at = parse_bucket_time(item.get("date"), "hour")
        if bucket_at == current_hour:
            return (
                _nonnegative_integer(item.get("total_tokens")) or 0,
                _nonnegative_integer(item.get("requests")) or 0,
            )
    return 0, 0


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
