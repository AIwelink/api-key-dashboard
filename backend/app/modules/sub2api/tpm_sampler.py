from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.sub2api.cache import _fetch_all_accounts, get_site, is_sub2api_site, list_sites
from app.modules.sub2api.client import Sub2ApiClient, account_in_group
from app.modules.sub2api.dashboard import parse_remote_datetime
from app.utils import now_utc


logger = logging.getLogger("app.sub2api_tpm_sampler")

TPM_SAMPLE_RETENTION_DAYS = 14
TPM_SAMPLE_INTERVAL_SECONDS = 60
TPM_SAMPLE_TIMEZONE = "Asia/Shanghai"
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
        include_trend=False,
        include_model_stats=False,
        include_group_stats=False,
        include_users_trend=False,
        group_id=group_id,
    )
    stats = snapshot.get("stats") if isinstance(snapshot.get("stats"), dict) else {}
    previous = await db.sub2api_tpm_samples.find_one(
        {"site_id": site_id, "group_id": group_id, "bucket_at": {"$lt": bucket_at}},
        sort=[("bucket_at", -1)],
    )

    reported_tpm = _nonnegative_number(stats.get("tpm"))
    total_tokens = _nonnegative_integer(stats.get("total_tokens"))
    calculated_tpm, token_delta, elapsed_seconds = _calculate_tpm_from_previous(
        previous=previous,
        current_total_tokens=total_tokens,
        sampled_at=sampled_at,
    )
    final_tpm = reported_tpm if reported_tpm is not None else calculated_tpm
    sample_id = f"{site_id}:{group_id}:{bucket_at.isoformat().replace('+00:00', 'Z')}"
    document = {
        "_id": sample_id,
        "site_id": site_id,
        "group_id": group_id,
        "bucket_at": bucket_at,
        "sampled_at": sampled_at,
        "stats_updated_at": parse_remote_datetime(stats.get("stats_updated_at")),
        "tpm": final_tpm,
        "reported_tpm": reported_tpm,
        "calculated_tpm": calculated_tpm,
        "rpm": _nonnegative_number(stats.get("rpm")),
        "average_duration_ms": _nonnegative_number(stats.get("average_duration_ms")),
        "current_concurrency": _nonnegative_number(current_concurrency),
        "total_tokens": total_tokens,
        "input_tokens": _nonnegative_integer(stats.get("total_input_tokens")),
        "output_tokens": _nonnegative_integer(stats.get("total_output_tokens")),
        "cache_creation_tokens": _nonnegative_integer(stats.get("total_cache_creation_tokens")),
        "cache_read_tokens": _nonnegative_integer(stats.get("total_cache_read_tokens")),
        "token_delta": token_delta,
        "elapsed_seconds": elapsed_seconds,
        "source": "reported" if reported_tpm is not None else "calculated" if calculated_tpm is not None else "unavailable",
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
    if not previous:
        return None, None, None
    previous_sampled_at = _datetime_value(previous.get("sampled_at"))
    if previous_sampled_at is None:
        return None, None, None
    elapsed_seconds = (sampled_at - previous_sampled_at).total_seconds()
    if elapsed_seconds <= 0:
        return None, None, elapsed_seconds
    previous_total_tokens = _nonnegative_integer(previous.get("total_tokens"))
    if previous_total_tokens is None or current_total_tokens is None:
        return None, None, elapsed_seconds
    token_delta = current_total_tokens - previous_total_tokens
    if token_delta < 0:
        return None, None, elapsed_seconds
    calculated_tpm = token_delta / (elapsed_seconds / 60)
    return calculated_tpm, token_delta, elapsed_seconds


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
