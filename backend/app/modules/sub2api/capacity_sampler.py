from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.sub2api.cache import (
    _get_or_update_group_capacity_summary,
    get_site,
    is_sub2api_site,
    list_sites,
)
from app.utils import now_utc


logger = logging.getLogger("app.sub2api_capacity_sampler")

CAPACITY_SAMPLE_INTERVAL_SECONDS = 5 * 60
CAPACITY_SAMPLE_RETENTION_DAYS = 180
CAPACITY_SAMPLE_SCHEMA_VERSION = 1

_site_sample_locks: dict[str, asyncio.Lock] = {}


async def sample_group_capacity(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_doc: dict[str, Any],
    sampled_at: datetime | None = None,
) -> dict[str, Any]:
    group_id = _group_id(group_doc)
    if group_id is None:
        return {"ok": False, "site_id": site_id, "message": "group id is missing"}

    sampled_at = _as_utc(sampled_at or now_utc())
    bucket_at = _five_minute_bucket(sampled_at)
    summary = await _get_or_update_group_capacity_summary(db, site_id, group_id)
    group = group_doc.get("group") if isinstance(group_doc.get("group"), dict) else {}
    group_snapshot = {key: value for key, value in group.items() if key != "capacity_summary"}
    sample_id = f"{site_id}:{group_id}:{bucket_at.isoformat().replace('+00:00', 'Z')}"
    document = {
        "_id": sample_id,
        "schema_version": CAPACITY_SAMPLE_SCHEMA_VERSION,
        "site_id": site_id,
        "group_id": group_id,
        "bucket_at": bucket_at,
        "sampled_at": sampled_at,
        "account_cache_fetched_at": group_doc.get("fetched_at"),
        "capacity_calculated_at": summary.get("calculated_at"),
        "group": group_snapshot,
        "capacity_summary": summary,
        "expires_at": sampled_at + timedelta(days=CAPACITY_SAMPLE_RETENTION_DAYS),
    }
    await db.sub2api_capacity_samples.replace_one({"_id": sample_id}, document, upsert=True)
    return {"ok": True, "site_id": site_id, "group_id": group_id, "sample": document}


async def sample_site_capacity(db: AsyncIOMotorDatabase, *, site_id: str) -> dict[str, Any]:
    lock = _site_sample_locks.setdefault(site_id, asyncio.Lock())
    if lock.locked():
        return {"ok": True, "site_id": site_id, "status": "skipped", "message": "capacity sampling already running"}

    async with lock:
        site = await get_site(db, site_id)
        if not site:
            return {"ok": False, "site_id": site_id, "message": "sub2api site not found"}
        if not is_sub2api_site(site):
            return {"ok": False, "site_id": site_id, "message": "site is not a sub2api client"}

        group_docs = [doc async for doc in db.sub2api_groups_cache.find({"site_id": site_id})]
        group_docs.sort(key=lambda item: _group_id(item) if _group_id(item) is not None else -1)
        sampled_at = now_utc()

        async def sample_one(group_doc: dict[str, Any]) -> dict[str, Any]:
            group_id = _group_id(group_doc)
            try:
                return await sample_group_capacity(
                    db,
                    site_id=site_id,
                    group_doc=group_doc,
                    sampled_at=sampled_at,
                )
            except Exception as exc:  # noqa: BLE001 - one group must not block the remaining snapshots.
                logger.warning(
                    "sub2api_capacity_group_sample_failed site_id=%s group_id=%s error=%s",
                    site_id,
                    group_id,
                    exc,
                )
                return {"ok": False, "site_id": site_id, "group_id": group_id, "message": str(exc)}

        results = await asyncio.gather(*(sample_one(group_doc) for group_doc in group_docs))
        summary = {
            "ok": True,
            "site_id": site_id,
            "status": "completed",
            "groups": len(group_docs),
            "sampled": sum(1 for item in results if item.get("ok") is True),
            "failed": sum(1 for item in results if item.get("ok") is False),
            "results": results,
        }
        logger.info(
            "sub2api_capacity_sample_finished site_id=%s groups=%s sampled=%s failed=%s",
            site_id,
            summary["groups"],
            summary["sampled"],
            summary["failed"],
        )
        return summary


async def sample_all_sites_capacity(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    sites = [
        site
        for site in (await list_sites(db, site_type="sub2api")).get("items", [])
        if site and site.get("status") == "active" and is_sub2api_site(site)
    ]

    async def sample_one(site: dict[str, Any]) -> dict[str, Any]:
        site_id = str(site.get("id"))
        try:
            return await sample_site_capacity(db, site_id=site_id)
        except Exception as exc:  # noqa: BLE001 - one site must not block the remaining snapshots.
            logger.warning("sub2api_capacity_site_sample_failed site_id=%s error=%s", site_id, exc)
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


async def capacity_sampler_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        started = time.monotonic()
        try:
            await sample_all_sites_capacity(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sub2api_capacity_sampler_failed")
        elapsed_seconds = time.monotonic() - started
        await asyncio.sleep(max(0.0, CAPACITY_SAMPLE_INTERVAL_SECONDS - elapsed_seconds))


def _five_minute_bucket(value: datetime) -> datetime:
    minute = value.minute - value.minute % 5
    return value.replace(minute=minute, second=0, microsecond=0)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _group_id(group_doc: dict[str, Any]) -> int | None:
    value = group_doc.get("group_id")
    if value is None and isinstance(group_doc.get("group"), dict):
        value = group_doc["group"].get("id")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
