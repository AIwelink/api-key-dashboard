import asyncio
import logging
import math
import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReplaceOne, UpdateOne

from app.modules.api_pools.capacity_limits import DEFAULT_CAPACITY_ACCOUNT_LIMITS, get_capacity_account_limits
from app.modules.system.sql_dsn import sql_dsn_endpoint, validate_optional_sql_dsn
from app.modules.sub2api.capacity_risk import calculate_capacity_risk
from app.modules.sub2api.client import Sub2ApiClient
from app.utils import now_utc, serialize_doc


logger = logging.getLogger("app.sub2api_cache")

DEFAULT_SITE_ID = "default"
DEFAULT_SITE_TYPE = "sub2api"
SITE_TYPES = {"sub2api", "newapi"}
DEFAULT_REFRESH_INTERVAL_MINUTES = 30
MIN_REFRESH_INTERVAL_MINUTES = 1
REFRESH_DEBOUNCE_SECONDS = 3
FIVE_HOUR_WINDOW_SECONDS = 5 * 60 * 60
FIVE_HOUR_DYNAMIC_MAX_WAIT_SECONDS = 2 * 60 * 60
SEVEN_DAY_WINDOW_SECONDS = 7 * 24 * 60 * 60
SEVEN_DAY_DYNAMIC_MAX_WAIT_SECONDS = 2 * 24 * 60 * 60
CONCURRENCY_SAFE_FIVE_HOUR_USAGE_PERCENT = 80
CONCURRENCY_SAFE_SEVEN_DAY_USAGE_PERCENT = 80
BUG_TEAM_MIN_WINDOW_MINUTES = 28 * 24 * 60
CAPACITY_ACCOUNT_LIMITS = DEFAULT_CAPACITY_ACCOUNT_LIMITS
REFILL_ACCOUNT_TYPES_BY_POOL = {
    "plus": ("plus", "k12"),
    "pro": ("pro",),
}
CAPACITY_HEALTH_THRESHOLDS = {
    "exhausted_available_accounts": 2,
    "exhausted_recent_day_peak_multiple": 0.2,
    "exhausted_current_speed_days": 0.25,
    "danger_recent_day_peak_multiple": 1.0,
    "danger_current_speed_days": 1.0,
    "auto_refill_recent_day_peak_multiple": 1.75,
    "auto_refill_current_speed_days": 3.5,
    "tight_peak_multiple": 1.5,
    "tight_current_speed_days": 3.0,
    "abundant_recent_day_peak_multiple": 3.0,
    "abundant_current_speed_days": 5.0,
    "very_abundant_peak_multiple": 5.0,
    "very_abundant_seven_day_peak_speed_days": 10.0,
}
ACCOUNT_USAGE_FIELDS = (
    "codex_5h_used_percent",
    "codex_7d_used_percent",
    "codex_5h_reset_after_seconds",
    "codex_7d_reset_after_seconds",
    "codex_5h_request_count",
    "codex_7d_request_count",
    "codex_5h_token_count",
    "codex_7d_token_count",
    "codex_5h_actual_cost",
    "codex_7d_actual_cost",
    "codex_5h_total_cost",
    "codex_7d_total_cost",
    "codex_5h_user_cost",
    "codex_7d_user_cost",
    "codex_5h_reset_at",
    "codex_7d_reset_at",
    "codex_usage_updated_at",
    "codex_usage_synced_at",
    "codex_usage_snapshot",
)

_refresh_tasks: dict[str, asyncio.Task] = {}
_refresh_tasks_lock = asyncio.Lock()
_site_locks: dict[str, asyncio.Lock] = {}


def public_site(site: dict[str, Any]) -> dict[str, Any]:
    result = dict(site)
    result["site_type"] = site_type(result)
    result.setdefault("refresh_interval_minutes", DEFAULT_REFRESH_INTERVAL_MINUTES)
    result.setdefault("auto_remove_abnormal_accounts", False)
    result.setdefault("status", "active")
    result.setdefault("source", "database")
    result["token_configured"] = bool(result.get("token"))
    result["uptime_kuma_api_key_configured"] = bool(result.get("uptime_kuma_api_key"))
    sql_dsn = str(result.get("sql_dsn") or "")
    result["sql_dsn_configured"] = bool(sql_dsn)
    result["database_type"] = "postgresql"
    if sql_dsn:
        result["database_endpoint"] = sql_dsn_endpoint(sql_dsn, "postgresql")
    result.pop("token", None)
    result.pop("uptime_kuma_api_key", None)
    result.pop("sql_dsn", None)
    return result


async def get_site(db: AsyncIOMotorDatabase, site_id: str = DEFAULT_SITE_ID, *, include_token: bool = False) -> dict[str, Any] | None:
    query = sub2api_site_query(status={"$ne": "deleted"}) | {"_id": site_id}
    doc = await db.sub2api_sites.find_one(query)
    if doc is None:
        return None
    site = dict(doc)
    site["site_type"] = site_type(site)
    site.setdefault("refresh_interval_minutes", DEFAULT_REFRESH_INTERVAL_MINUTES)
    site.setdefault("auto_remove_abnormal_accounts", False)
    site.setdefault("status", "active")
    site.setdefault("source", "database")
    site["id"] = site["_id"]
    if not include_token:
        site = public_site(site)
    return serialize_doc(site)


async def list_sites(db: AsyncIOMotorDatabase, *, site_type: str | None = None) -> dict[str, Any]:
    if site_type is not None:
        normalized_type = normalize_site_type(site_type)
        if normalized_type != "sub2api":
            raise ValueError("customer sites must be configured through /api/client-sites")
    query = sub2api_site_query(status={"$ne": "deleted"})
    cursor = db.sub2api_sites.find(query).sort([("created_at", 1), ("_id", 1)])
    items = [public_site(doc | {"id": doc["_id"]}) async for doc in cursor]
    return {"items": items, "total": len(items)}


async def update_site_config(db: AsyncIOMotorDatabase, site_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    site_query = sub2api_site_query(status={"$ne": "deleted"}) | {"_id": site_id}
    current = await db.sub2api_sites.find_one(site_query)
    if current is None:
        return {}
    updates: dict[str, Any] = {"updated_at": now_utc()}
    normalized_type = normalize_site_type(payload.get("site_type", current.get("site_type")))
    if normalized_type != "sub2api":
        raise ValueError("customer sites must be configured through /api/client-sites")
    updates["site_type"] = "sub2api"
    updates["admin_user_id"] = ""
    if "name" in payload:
        updates["name"] = str(payload["name"] or site_id).strip() or site_id
    if "base_url" in payload:
        updates["base_url"] = str(payload["base_url"] or "").strip().rstrip("/")
    if "token" in payload:
        updates["token"] = str(payload["token"] or "").strip()
    if "status" in payload:
        updates["status"] = str(payload["status"] or "active")
    if "refresh_interval_minutes" in payload:
        updates["refresh_interval_minutes"] = _site_refresh_interval_minutes(payload)
    if "auto_remove_abnormal_accounts" in payload:
        updates["auto_remove_abnormal_accounts"] = bool(payload["auto_remove_abnormal_accounts"])
    if "uptime_kuma_url" in payload:
        updates["uptime_kuma_url"] = _optional_http_url(payload.get("uptime_kuma_url"), "uptime_kuma_url")
    if str(payload.get("uptime_kuma_api_key") or "").strip():
        updates["uptime_kuma_api_key"] = str(payload["uptime_kuma_api_key"]).strip()
    incoming_sql_dsn = str(payload.get("sql_dsn") or "").strip()
    current_sql_dsn = str(current.get("sql_dsn") or "").strip()
    selected_sql_dsn = validate_optional_sql_dsn(incoming_sql_dsn or current_sql_dsn, "postgresql")
    if incoming_sql_dsn:
        updates["sql_dsn"] = selected_sql_dsn
    await db.sub2api_sites.update_one(site_query, {"$set": updates})
    return await get_site(db, site_id) or {}


async def create_site_config(db: AsyncIOMotorDatabase, payload: dict[str, Any]) -> dict[str, Any]:
    site_id = str(payload.get("id") or "").strip()
    base_url = str(payload.get("base_url") or "").strip().rstrip("/")
    if not site_id or not base_url:
        return {}
    normalized_type = normalize_site_type(payload.get("site_type"))
    if normalized_type != "sub2api":
        raise ValueError("customer sites must be configured through /api/client-sites")
    now = now_utc()
    sql_dsn = validate_optional_sql_dsn(payload.get("sql_dsn"), "postgresql")
    doc = {
        "_id": site_id,
        "name": str(payload.get("name") or site_id).strip() or site_id,
        "base_url": base_url,
        "site_type": "sub2api",
        "admin_user_id": "",
        "token": str(payload.get("token") or "").strip(),
        "status": str(payload.get("status") or "active"),
        "refresh_interval_minutes": _site_refresh_interval_minutes(payload),
        "auto_remove_abnormal_accounts": bool(payload.get("auto_remove_abnormal_accounts", False)),
        "uptime_kuma_url": _optional_http_url(payload.get("uptime_kuma_url"), "uptime_kuma_url"),
        "uptime_kuma_api_key": str(payload.get("uptime_kuma_api_key") or "").strip(),
        "source": "database",
        "created_at": now,
        "updated_at": now,
    }
    if sql_dsn:
        doc["sql_dsn"] = sql_dsn
    await db.sub2api_sites.replace_one({"_id": site_id}, doc, upsert=True)
    return await get_site(db, site_id) or {}


async def delete_site_config(db: AsyncIOMotorDatabase, site_id: str) -> bool:
    site_query = sub2api_site_query(status={"$ne": "deleted"}) | {"_id": site_id}
    result = await db.sub2api_sites.update_one(
        site_query,
        {"$set": {"status": "deleted", "deleted_at": now_utc(), "updated_at": now_utc()}},
    )
    return result.modified_count > 0


def normalize_site_type(value: Any) -> str:
    normalized = str(value or DEFAULT_SITE_TYPE).strip().lower()
    if normalized not in SITE_TYPES:
        raise ValueError(f"unsupported site_type: {normalized}")
    return normalized


def _optional_http_url(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an http or https URL")
    return normalized


def site_type(site: dict[str, Any]) -> str:
    try:
        return normalize_site_type(site.get("site_type"))
    except ValueError:
        return DEFAULT_SITE_TYPE


def is_sub2api_site(site: dict[str, Any] | None) -> bool:
    return bool(site) and site_type(site) == "sub2api"


def sub2api_site_query(*, status: Any = None) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if status is not None:
        query["status"] = status
    query["$or"] = [
        {"site_type": "sub2api"},
        {"site_type": {"$exists": False}},
        {"site_type": None},
        {"site_type": ""},
    ]
    return query


async def get_cache_meta(db: AsyncIOMotorDatabase, site_id: str) -> dict[str, Any]:
    meta = await db.sub2api_cache_meta.find_one({"_id": site_id}) or {"_id": site_id, "site_id": site_id}
    return serialize_doc(meta)


async def list_cached_groups(
    db: AsyncIOMotorDatabase,
    site_id: str,
    *,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    query = {"site_id": site_id}
    total = await db.sub2api_groups_cache.count_documents(query)
    cursor = db.sub2api_groups_cache.find(query).sort("group_id", 1).skip((page - 1) * page_size).limit(page_size)
    docs = [doc async for doc in cursor]
    return {
        "items": [
            serialize_doc(
                _group_with_capacity_summary(
                    doc.get("group", {}),
                    doc.get("capacity_summary") if isinstance(doc.get("capacity_summary"), dict) else None,
                )
            )
            for doc in docs
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "cache_meta": await get_cache_meta(db, site_id),
    }


async def list_cached_group_accounts(
    db: AsyncIOMotorDatabase,
    site_id: str,
    group_id: int,
    *,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    query: dict[str, Any] = {"site_id": site_id, "group_ids": group_id}
    if status_filter:
        query["status"] = status_filter
    total = await db.sub2api_accounts_cache.count_documents(query)
    cursor = (
        db.sub2api_accounts_cache.find(query)
        .sort([("created_at", -1), ("sub2api_account_id", -1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    docs = [doc async for doc in cursor]
    accounts = [_account_snapshot_with_cache_sync(doc) for doc in docs]
    await _attach_local_account_metadata(db, accounts, site_id=site_id)
    return {
        "items": [serialize_doc(account) for account in accounts],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "cache_meta": await get_cache_meta(db, site_id),
        "capacity_summary": await _get_or_update_group_capacity_summary(db, site_id, group_id),
    }


async def upsert_cached_account_snapshot(
    db: AsyncIOMotorDatabase,
    site_id: str,
    account: dict[str, Any],
    *,
    fetched_at: datetime | None = None,
) -> dict[str, Any]:
    normalized = _normalize_account_snapshot(account)
    remote_id = normalized.get("id")
    if remote_id is None:
        return normalized
    fetched_at = fetched_at or now_utc()
    await db.sub2api_accounts_cache.replace_one(
        {"_id": f"{site_id}:{remote_id}"},
        {
            "_id": f"{site_id}:{remote_id}",
            "site_id": site_id,
            "sub2api_account_id": remote_id,
            "group_ids": _extract_group_ids(normalized),
            "status": normalized.get("status"),
            "schedulable": normalized.get("schedulable"),
            **_account_cache_fields(normalized),
            "account": normalized,
            "fetched_at": fetched_at,
        },
        upsert=True,
    )
    return normalized


async def refresh_site_cache(db: AsyncIOMotorDatabase, site_id: str = DEFAULT_SITE_ID) -> dict[str, Any]:
    site = await get_site(db, site_id, include_token=True)
    if not site:
        logger.warning("sub2api_refresh_skipped site_id=%s reason=site_not_found", site_id)
        return {"ok": False, "message": "sub2api site not found"}

    lock = _site_locks.setdefault(site_id, asyncio.Lock())
    async with lock:
        started_at = now_utc()
        logger.info("sub2api_refresh_start site_id=%s", site_id)
        try:
            await db.sub2api_cache_meta.update_one(
                {"_id": site_id},
                {"$set": {"site_id": site_id, "status": "refreshing", "started_at": started_at, "updated_at": started_at}},
                upsert=True,
            )

            client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
            groups_data, raw_accounts = await asyncio.gather(
                client.list_groups(page=1, page_size=500),
                _fetch_all_accounts(client),
            )
            groups = groups_data.get("items", [])
            group_ids = [_int_group_id(group.get("id")) for group in groups if isinstance(group, dict)]
            group_ids = [group_id for group_id in group_ids if group_id is not None]
            accounts = [_normalize_account_snapshot(account) for account in raw_accounts]
            fetched_at = now_utc()
            dashboard_summary, _ = await asyncio.gather(
                _refresh_dashboard_for_cache(db, site_id=site_id, client=client, group_ids=group_ids),
                _apply_account_usage_windows(db, site_id, client, accounts, fetched_at),
            )
            group_capacity_summaries = await _group_capacity_summaries(db, site_id, accounts)

            group_ops = []
            empty_capacity_summary: dict[str, Any] | None = None
            for group in groups:
                group_id = _int_group_id(group.get("id"))
                if group_id is None:
                    continue
                capacity_summary = group_capacity_summaries.get(group_id)
                if capacity_summary is None:
                    if empty_capacity_summary is None:
                        empty_capacity_summary = await _capacity_summary_for_accounts(db, site_id, [])
                    capacity_summary = empty_capacity_summary
                    group_capacity_summaries[group_id] = capacity_summary
                group_ops.append(
                    ReplaceOne(
                        {"_id": f"{site_id}:{group_id}"},
                        {
                            "_id": f"{site_id}:{group_id}",
                            "site_id": site_id,
                            "group_id": group_id,
                            "group": _group_cache_snapshot(group),
                            "capacity_summary": capacity_summary,
                            "fetched_at": fetched_at,
                        },
                        upsert=True,
                    )
                )
            account_ops = [
                ReplaceOne(
                    {"_id": f"{site_id}:{account.get('id')}"},
                    {
                        "_id": f"{site_id}:{account.get('id')}",
                        "site_id": site_id,
                        "sub2api_account_id": account.get("id"),
                        "group_ids": _extract_group_ids(account),
                        "status": account.get("status"),
                        "schedulable": account.get("schedulable"),
                        **_account_cache_fields(account),
                        "account": account,
                        "fetched_at": fetched_at,
                    },
                    upsert=True,
                )
                for account in accounts
                if account.get("id") is not None
            ]
            cache_writes = []
            if group_ops:
                cache_writes.append(db.sub2api_groups_cache.bulk_write(group_ops, ordered=False))
            if account_ops:
                cache_writes.append(db.sub2api_accounts_cache.bulk_write(account_ops, ordered=False))
            if cache_writes:
                await asyncio.gather(*cache_writes)

            account_ids = [account.get("id") for account in accounts if account.get("id") is not None]
            await asyncio.gather(
                db.sub2api_groups_cache.delete_many({"site_id": site_id, "group_id": {"$nin": group_ids}}),
                db.sub2api_accounts_cache.delete_many({"site_id": site_id, "sub2api_account_id": {"$nin": account_ids}}),
            )
            capacity_notification_summary: dict[str, Any] | None = None
            try:
                from app.modules.sub2api.capacity_notifications import evaluate_capacity_notifications

                capacity_notification_summary = await evaluate_capacity_notifications(
                    db,
                    site_id=site_id,
                    groups=groups,
                    capacity_summaries=group_capacity_summaries,
                )
            except Exception as exc:  # noqa: BLE001 - notification failures must not fail cache refresh.
                capacity_notification_summary = {"ok": False, "message": str(exc)}
                logger.warning("sub2api_capacity_notification_failed site_id=%s error=%s", site_id, exc)
            try:
                from app.modules.api_pools.pools import sync_api_pools_from_sub2api_groups

                await sync_api_pools_from_sub2api_groups(db, site_id=site_id)
            except Exception as exc:  # noqa: BLE001 - local pool sync should not block remote cache refresh.
                logger.warning("api_pool_sync_after_sub2api_refresh_failed site_id=%s error=%s", site_id, exc)

            auto_remove_summary: dict[str, Any] | None = None
            if site.get("auto_remove_abnormal_accounts") is True:
                from app.modules.sub2api.abnormal import auto_remove_abnormal_accounts

                auto_remove_summary = await auto_remove_abnormal_accounts(db, site_id=site_id, accounts=accounts)

            summary = {
                "ok": True,
                "site_id": site_id,
                "status": "succeeded",
                "groups": len(groups),
                "accounts": len(accounts),
                "auto_removed_abnormal_accounts": auto_remove_summary.get("removed", 0) if auto_remove_summary else 0,
                "auto_remove_abnormal_failed": auto_remove_summary.get("failed", 0) if auto_remove_summary else 0,
                "dashboard": dashboard_summary,
                "capacity_notifications": capacity_notification_summary,
                "started_at": started_at,
                "finished_at": now_utc(),
            }
            await db.sub2api_cache_meta.update_one(
                {"_id": site_id},
                {"$set": {**summary, "last_refreshed_at": summary["finished_at"], "updated_at": summary["finished_at"]}},
                upsert=True,
            )
            logger.info(
                "sub2api_refresh_finished site_id=%s groups=%s accounts=%s",
                site_id,
                len(groups),
                len(accounts),
            )
            return serialize_doc(summary)
        except asyncio.CancelledError:
            logger.info("sub2api_refresh_cancelled site_id=%s", site_id)
            raise
        except Exception as exc:
            finished_at = now_utc()
            message = str(exc) or exc.__class__.__name__
            logger.exception("sub2api_refresh_failed site_id=%s error=%s", site_id, message)
            try:
                await db.sub2api_cache_meta.update_one(
                    {"_id": site_id},
                    {
                        "$set": {
                            "site_id": site_id,
                            "status": "failed",
                            "message": message,
                            "error_type": exc.__class__.__name__,
                            "finished_at": finished_at,
                            "updated_at": finished_at,
                        }
                    },
                    upsert=True,
                )
            except Exception as meta_exc:  # noqa: BLE001 - do not hide the original refresh failure.
                logger.warning("sub2api_refresh_failed_meta_write_failed site_id=%s error=%s", site_id, meta_exc)
            if isinstance(exc, HTTPException):
                raise
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"sub2api refresh failed: {message}",
            ) from exc


async def request_debounced_refresh(db: AsyncIOMotorDatabase, site_id: str = DEFAULT_SITE_ID) -> dict[str, Any]:
    async with _refresh_tasks_lock:
        current = _refresh_tasks.get(site_id)
        if current and not current.done():
            task = current
        else:
            task = asyncio.create_task(_delayed_refresh(db, site_id))
            _refresh_tasks[site_id] = task
    return await task


async def refresh_account_caches_for_all_sites(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    sites = (await list_sites(db, site_type="sub2api")).get("items", [])
    results = []
    for site in sites:
        if not site or site.get("status") != "active":
            continue
        site_id = str(site.get("id"))
        try:
            results.append(await request_debounced_refresh(db, site_id))
        except Exception as exc:  # noqa: BLE001 - keep startup sync best-effort per site.
            logger.warning("sub2api_startup_account_refresh_failed site_id=%s error=%s", site_id, exc)
            results.append({"ok": False, "site_id": site_id, "message": str(exc)})
    return {
        "ok": True,
        "sites": len(sites),
        "refreshed": sum(1 for item in results if item.get("ok") is True),
        "failed": sum(1 for item in results if item.get("ok") is False),
        "results": results,
    }


async def refresh_scheduler_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        try:
            for site in (await list_sites(db, site_type="sub2api")).get("items", []):
                if not site or site.get("status") != "active":
                    continue
                site_id = str(site.get("id"))
                interval = _site_refresh_interval_minutes(site)
                meta = await get_cache_meta(db, site_id)
                last_refreshed_at = meta.get("last_refreshed_at")
                if _is_due(last_refreshed_at, interval):
                    await request_debounced_refresh(db, site_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sub2api_refresh_scheduler_failed")
        await asyncio.sleep(30)


def _site_refresh_interval_minutes(site: dict[str, Any]) -> int:
    try:
        interval = int(site.get("refresh_interval_minutes") or DEFAULT_REFRESH_INTERVAL_MINUTES)
    except (TypeError, ValueError):
        interval = DEFAULT_REFRESH_INTERVAL_MINUTES
    return max(MIN_REFRESH_INTERVAL_MINUTES, min(interval, 1440))


async def _delayed_refresh(db: AsyncIOMotorDatabase, site_id: str) -> dict[str, Any]:
    requested_at = now_utc()
    await db.sub2api_cache_meta.update_one(
        {"_id": site_id},
        {"$set": {"site_id": site_id, "status": "scheduled", "requested_at": requested_at, "updated_at": requested_at}},
        upsert=True,
    )
    await asyncio.sleep(REFRESH_DEBOUNCE_SECONDS)
    return await refresh_site_cache(db, site_id)


async def _fetch_all_accounts(client: Sub2ApiClient) -> list[dict[str, Any]]:
    page_size = 200
    request_params = {
        "page_size": page_size,
        "sort_by": "last_used_at",
        "sort_order": "asc",
        "timezone": "Asia/Shanghai",
    }
    first_page = await client.list_accounts(page=1, **request_params)
    first_items = first_page.get("items", [])
    accounts = [item for item in first_items if isinstance(item, dict)]
    if not first_items:
        return accounts

    total_value = first_page.get("total")
    total = int(total_value) if isinstance(total_value, int) else None
    if total is not None:
        page_count = min(100, max(1, (total + page_size - 1) // page_size))
        if page_count <= 1:
            return accounts
        remaining_pages = await asyncio.gather(
            *(client.list_accounts(page=page, **request_params) for page in range(2, page_count + 1))
        )
        for data in remaining_pages:
            accounts.extend(item for item in data.get("items", []) if isinstance(item, dict))
        return accounts

    for page in range(2, 101):
        data = await client.list_accounts(page=page, **request_params)
        items = data.get("items", [])
        accounts.extend(item for item in items if isinstance(item, dict))
        if not items:
            break
    return accounts


async def _refresh_dashboard_for_cache(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    client: Sub2ApiClient,
    group_ids: list[int],
) -> dict[str, Any]:
    try:
        from app.modules.sub2api.dashboard import refresh_dashboard_snapshots

        return await refresh_dashboard_snapshots(db, site_id=site_id, client=client, group_ids=group_ids)
    except Exception as exc:  # noqa: BLE001 - account cache refresh should not fail only because dashboard stats failed.
        logger.warning("sub2api_dashboard_refresh_failed site_id=%s error=%s", site_id, exc)
        return {"ok": False, "message": str(exc)}


async def _apply_account_usage_windows(
    db: AsyncIOMotorDatabase,
    site_id: str,
    client: Sub2ApiClient,
    accounts: list[dict[str, Any]],
    synced_at: datetime,
) -> None:
    await _restore_cached_usage_snapshots(db, site_id, accounts)
    selected_accounts = [account for account in accounts if account.get("id") is not None]
    if not selected_accounts:
        return
    logger.info(
        "sub2api_account_usage_refresh_start site_id=%s selected=%s total=%s throttled=false",
        site_id,
        len(selected_accounts),
        len(accounts),
    )

    async def fetch_and_apply(account: dict[str, Any], http_client: httpx.AsyncClient) -> None:
        account_id = account.get("id")
        if account_id is None:
            return
        try:
            usage = await client.get_account_usage(account_id, timezone="Asia/Shanghai", http_client=http_client)
        except Exception:
            logger.exception("sub2api_account_usage_fetch_failed account_id=%s", account_id)
            return
        _apply_account_usage_snapshot(account, usage, synced_at)

    limits = httpx.Limits(max_connections=None, max_keepalive_connections=200)
    async with httpx.AsyncClient(timeout=15, limits=limits) as http_client:
        await asyncio.gather(*(fetch_and_apply(account, http_client) for account in selected_accounts))


async def _restore_cached_usage_snapshots(db: AsyncIOMotorDatabase, site_id: str, accounts: list[dict[str, Any]]) -> None:
    account_ids = [account.get("id") for account in accounts if account.get("id") is not None]
    if not account_ids:
        return
    cached_by_remote_id: dict[Any, dict[str, Any]] = {}
    cursor = db.sub2api_accounts_cache.find(
        {"site_id": site_id, "sub2api_account_id": {"$in": account_ids}},
        {
            "sub2api_account_id": 1,
            "account": 1,
            "remote_test_status": 1,
            "remote_tested_at": 1,
            "remote_test_model": 1,
            "remote_test_response_preview": 1,
            "remote_test_latency_ms": 1,
            "remote_test_error": 1,
        },
    )
    async for doc in cursor:
        cached_by_remote_id[doc.get("sub2api_account_id")] = doc
    for account in accounts:
        cached = cached_by_remote_id.get(account.get("id"))
        if isinstance(cached, dict):
            cached_account = cached.get("account", {}) if isinstance(cached.get("account"), dict) else {}
            _copy_cached_plan_type(account, cached_account)
            _copy_cached_usage(account, cached_account)
            _copy_cached_remote_test(account, cached)


def _copy_cached_plan_type(account: dict[str, Any], cached: dict[str, Any]) -> None:
    extra = dict(account.get("extra") if isinstance(account.get("extra"), dict) else {})
    source = str(account.get("codex_plan_type_source") or extra.get("codex_plan_type_source") or "")
    if source != "fallback_k12":
        return

    cached_credentials = cached.get("credentials") if isinstance(cached.get("credentials"), dict) else {}
    cached_extra = cached.get("extra") if isinstance(cached.get("extra"), dict) else {}
    cached_plan_type = _first_present(cached, cached_credentials, cached_extra, "plan_type")
    if cached_plan_type is None:
        return
    cached_plan_type = str(cached_plan_type).strip()
    if not cached_plan_type:
        return

    cached_source = str(cached.get("codex_plan_type_source") or cached_extra.get("codex_plan_type_source") or "")
    resolved_source = "fallback_k12" if cached_source == "fallback_k12" else "cached"
    account["plan_type"] = cached_plan_type
    account["codex_plan_type_source"] = resolved_source
    extra["plan_type"] = cached_plan_type
    extra["codex_plan_type_source"] = resolved_source
    account["extra"] = extra


def _copy_cached_usage(account: dict[str, Any], cached: dict[str, Any]) -> None:
    extra = dict(account.get("extra") if isinstance(account.get("extra"), dict) else {})
    cached_extra = cached.get("extra") if isinstance(cached.get("extra"), dict) else {}
    for key in ACCOUNT_USAGE_FIELDS:
        cached_value = cached.get(key) if cached.get(key) is not None else cached_extra.get(key)
        if cached_value is None:
            continue
        if account.get(key) is None:
            account[key] = cached_value
        if extra.get(key) is None:
            extra[key] = cached_value
    account["extra"] = extra


def _copy_cached_remote_test(account: dict[str, Any], cached: dict[str, Any]) -> None:
    cached_account = cached.get("account") if isinstance(cached.get("account"), dict) else {}
    mappings = {
        "codex_remote_test_status": cached.get("remote_test_status") or cached_account.get("codex_remote_test_status"),
        "codex_remote_tested_at": cached.get("remote_tested_at") or cached_account.get("codex_remote_tested_at"),
        "codex_remote_test_model": cached.get("remote_test_model") or cached_account.get("codex_remote_test_model"),
        "codex_remote_test_response_preview": cached.get("remote_test_response_preview") or cached_account.get("codex_remote_test_response_preview"),
        "codex_remote_test_latency_ms": cached.get("remote_test_latency_ms") or cached_account.get("codex_remote_test_latency_ms"),
        "codex_remote_test_error": cached.get("remote_test_error") or cached_account.get("codex_remote_test_error"),
    }
    extra = dict(account.get("extra") if isinstance(account.get("extra"), dict) else {})
    for key, value in mappings.items():
        if value is None:
            continue
        account[key] = value
        extra[key] = value
    account["extra"] = extra


def _apply_account_usage_snapshot(account: dict[str, Any], usage: dict[str, Any], synced_at: datetime) -> None:
    if not isinstance(usage, dict):
        return
    extra = dict(account.get("extra") if isinstance(account.get("extra"), dict) else {})
    account["codex_usage_synced_at"] = synced_at
    extra["codex_usage_synced_at"] = synced_at
    updated_at = usage.get("updated_at")
    if updated_at is not None:
        account["codex_usage_updated_at"] = updated_at
        extra["codex_usage_updated_at"] = updated_at

    window_map = {
        "5h": usage.get("five_hour"),
        "7d": usage.get("seven_day"),
    }
    for window, window_data in window_map.items():
        if not isinstance(window_data, dict):
            continue
        prefix = f"codex_{window}"
        stats = window_data.get("window_stats") if isinstance(window_data.get("window_stats"), dict) else {}
        values = {
            f"{prefix}_used_percent": window_data.get("utilization"),
            f"{prefix}_reset_at": window_data.get("resets_at"),
            f"{prefix}_reset_after_seconds": window_data.get("remaining_seconds"),
            f"{prefix}_request_count": stats.get("requests"),
            f"{prefix}_token_count": stats.get("tokens"),
            f"{prefix}_actual_cost": stats.get("cost"),
            f"{prefix}_total_cost": stats.get("standard_cost", stats.get("cost")),
            f"{prefix}_user_cost": stats.get("user_cost"),
        }
        for key, value in values.items():
            if value is not None:
                account[key] = value
                extra[key] = value

    account["codex_usage_snapshot"] = usage
    extra["codex_usage_snapshot"] = usage
    account["extra"] = extra


async def _get_or_update_group_capacity_summary(db: AsyncIOMotorDatabase, site_id: str, group_id: int) -> dict[str, Any]:
    cursor = db.sub2api_accounts_cache.find({"site_id": site_id, "group_ids": group_id})
    accounts: list[dict[str, Any]] = []
    account_ops: list[UpdateOne] = []
    normalized_at = now_utc()
    async for doc in cursor:
        original_account = doc.get("account", {})
        normalized_account = _normalize_account_snapshot(original_account)
        accounts.append(normalized_account)
        if normalized_account != original_account:
            account_ops.append(
                UpdateOne(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "account": normalized_account,
                            "group_ids": _extract_group_ids(normalized_account),
                            "status": normalized_account.get("status"),
                            "schedulable": normalized_account.get("schedulable"),
                            "normalized_at": normalized_at,
                        }
                    },
                )
            )
    if account_ops:
        await db.sub2api_accounts_cache.bulk_write(account_ops, ordered=False)

    summary = await _capacity_summary_for_accounts(db, site_id, accounts, group_id=group_id)
    await db.sub2api_groups_cache.update_one(
        {"site_id": site_id, "group_id": group_id},
        {
            "$set": {"capacity_summary": summary, "capacity_calculated_at": now_utc()},
            "$unset": {"group.capacity_summary": ""},
        },
    )
    return serialize_doc(summary)


async def _group_capacity_summaries(db: AsyncIOMotorDatabase, site_id: str, accounts: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for account in accounts:
        for group_id in _extract_group_ids(account):
            grouped.setdefault(group_id, []).append(account)
    grouped_items = list(grouped.items())
    summaries = await asyncio.gather(
        *(
            _capacity_summary_for_accounts(db, site_id, group_accounts, group_id=group_id)
            for group_id, group_accounts in grouped_items
        )
    )
    return {group_id: summary for (group_id, _), summary in zip(grouped_items, summaries, strict=True)}


def _group_with_capacity_summary(group: dict[str, Any], capacity_summary: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = _group_cache_snapshot(group)
    if capacity_summary is not None:
        snapshot["capacity_summary"] = capacity_summary
    return snapshot


def _group_cache_snapshot(group: dict[str, Any]) -> dict[str, Any]:
    snapshot = dict(group)
    snapshot.pop("capacity_summary", None)
    return snapshot


def _account_cache_fields(account: dict[str, Any]) -> dict[str, Any]:
    fields = (
        "email",
        "plan_type",
        "privacy_mode",
        "subscription_expires_at",
        "credential_expires_at",
        "created_at",
        "updated_at",
        "last_used_at",
        "rate_limited_at",
        "rate_limit_reset_at",
        "codex_5h_used_percent",
        "codex_7d_used_percent",
        "codex_5h_request_count",
        "codex_7d_request_count",
        "codex_total_request_count",
        "codex_total_token_count",
        "codex_5h_total_cost",
        "codex_7d_total_cost",
        "codex_total_actual_cost",
        "codex_total_cost",
        "codex_usage_updated_at",
        "codex_remote_test_status",
        "codex_remote_tested_at",
        "codex_remote_test_model",
        "codex_remote_test_response_preview",
        "codex_remote_test_latency_ms",
        "codex_remote_test_error",
    )
    return {field: account.get(field) for field in fields if account.get(field) is not None}


async def _capacity_summary_for_accounts(
    db: AsyncIOMotorDatabase,
    site_id: str,
    accounts: list[dict[str, Any]],
    *,
    group_id: int | None = None,
) -> dict[str, Any]:
    pool_status_summary = _pool_account_status_summary(accounts)
    capacity_accounts_all = [account for account in accounts if _is_capacity_account(account)]
    capacity_accounts, duplicate_capacity_accounts = _collapse_capacity_accounts_by_email(capacity_accounts_all)
    concurrency_summary = _concurrency_capacity_summary(capacity_accounts)
    five_hour_capacity_accounts = [account for account in capacity_accounts if not _is_7d_exhausted(account)]
    used_5h = _average_percent(_usage_number(account, "codex_5h_used_percent") for account in capacity_accounts)
    capacity_limits = (await get_capacity_account_limits(db, site_id))["limits"]
    type_summary = _capacity_by_account_type(capacity_accounts, five_hour_capacity_accounts, capacity_limits)
    primary_type = _primary_capacity_type(type_summary)
    reserve_type_summary = _empty_capacity_type_summary(capacity_limits)
    selected = type_summary["total"]
    selected_reserve = reserve_type_summary["total"]
    cost_summary = await _dashboard_cost_summary(db, site_id, group_id=group_id)
    five_hour_peak_cost = cost_summary["five_hour_peak_cost"]
    recent_day_five_hour_peak_cost = cost_summary["recent_day_five_hour_peak_cost"]
    seven_day_24h_peak_cost = cost_summary["seven_day_24h_peak_cost"]
    burst_summary = cost_summary["burst_1h"]
    recent_5h_cost = cost_summary["recent_5h_cost"]
    recent_24h_cost = cost_summary["recent_24h_cost"]
    seven_day_cost = cost_summary["seven_day_cost"]
    active_five_hour_capacity_usd = selected["five_hour_capacity_usd"]
    active_seven_day_capacity_usd = selected["seven_day_capacity_usd"]
    selected_account_count = int(selected["available_accounts"])
    selected_limits = {
        "five_hour_usd": active_five_hour_capacity_usd / selected_account_count if selected_account_count > 0 else 0.0,
        "seven_day_usd": active_seven_day_capacity_usd / selected_account_count if selected_account_count > 0 else 0.0,
    }
    selected_seven_day_limit_usd = float(selected_limits.get("seven_day_usd") or 0)
    estimated_recent_24h_consumed_accounts = _ratio_or_none(recent_24h_cost, selected_seven_day_limit_usd)
    estimated_seven_day_peak_24h_consumed_accounts = _ratio_or_none(seven_day_24h_peak_cost, selected_seven_day_limit_usd)
    five_hour_capacity_usd = active_five_hour_capacity_usd + selected_reserve["five_hour_capacity_usd"]
    seven_day_capacity_usd = active_seven_day_capacity_usd + selected_reserve["seven_day_capacity_usd"]
    twenty_four_hour_capacity_usd = seven_day_capacity_usd / 7 if seven_day_capacity_usd > 0 else 0
    active_dynamic_five_hour_used_usd = selected["five_hour_dynamic_used_usd"]
    active_dynamic_five_hour_remaining_usd = selected["five_hour_dynamic_remaining_usd"]
    active_dynamic_five_hour_capacity_usd = selected["five_hour_dynamic_capacity_usd"]
    reserve_dynamic_five_hour_used_usd = selected_reserve["five_hour_dynamic_used_usd"]
    reserve_dynamic_five_hour_remaining_usd = selected_reserve["five_hour_dynamic_remaining_usd"]
    reserve_dynamic_five_hour_capacity_usd = selected_reserve["five_hour_dynamic_capacity_usd"]
    dynamic_five_hour_used_estimated_usd = active_dynamic_five_hour_used_usd + reserve_dynamic_five_hour_used_usd
    dynamic_five_hour_remaining_estimated_usd = active_dynamic_five_hour_remaining_usd + reserve_dynamic_five_hour_remaining_usd
    dynamic_five_hour_capacity_usd = active_dynamic_five_hour_capacity_usd + reserve_dynamic_five_hour_capacity_usd
    active_five_hour_actual_remaining_usd = selected["five_hour_actual_remaining_usd"]
    reserve_five_hour_actual_remaining_usd = selected_reserve["five_hour_actual_remaining_usd"]
    five_hour_actual_used_usd = selected["five_hour_actual_used_usd"] + selected_reserve["five_hour_actual_used_usd"]
    five_hour_actual_remaining_usd = active_five_hour_actual_remaining_usd + reserve_five_hour_actual_remaining_usd
    active_seven_day_actual_remaining_usd = selected["seven_day_actual_remaining_usd"]
    reserve_seven_day_actual_remaining_usd = selected_reserve["seven_day_actual_remaining_usd"]
    active_seven_day_dynamic_used_usd = selected["seven_day_dynamic_used_usd"]
    reserve_seven_day_dynamic_used_usd = selected_reserve["seven_day_dynamic_used_usd"]
    active_seven_day_dynamic_remaining_usd = selected["seven_day_dynamic_remaining_usd"]
    reserve_seven_day_dynamic_remaining_usd = selected_reserve["seven_day_dynamic_remaining_usd"]
    seven_day_actual_used_usd = selected["seven_day_actual_used_usd"] + selected_reserve["seven_day_actual_used_usd"]
    seven_day_actual_remaining_usd = active_seven_day_actual_remaining_usd + reserve_seven_day_actual_remaining_usd
    seven_day_used_estimated_usd = active_seven_day_dynamic_used_usd + reserve_seven_day_dynamic_used_usd
    seven_day_remaining_estimated_usd = active_seven_day_dynamic_remaining_usd + reserve_seven_day_dynamic_remaining_usd
    active_seven_day_remaining_estimated_usd = active_seven_day_dynamic_remaining_usd
    available_5h_percent = _ratio_percent(dynamic_five_hour_remaining_estimated_usd, dynamic_five_hour_capacity_usd)
    active_available_5h_percent = _ratio_percent(active_dynamic_five_hour_remaining_usd, active_dynamic_five_hour_capacity_usd)
    actual_available_5h_percent = _ratio_percent(five_hour_actual_remaining_usd, dynamic_five_hour_capacity_usd)
    active_actual_available_5h_percent = _ratio_percent(active_five_hour_actual_remaining_usd, active_dynamic_five_hour_capacity_usd)
    effective_used_5h = _clamp_percent(100 - available_5h_percent)
    active_effective_used_5h = _clamp_percent(100 - active_available_5h_percent)
    available_7d_percent = _ratio_percent(seven_day_remaining_estimated_usd, seven_day_capacity_usd)
    actual_available_7d_percent = _ratio_percent(seven_day_actual_remaining_usd, seven_day_capacity_usd)
    effective_used_7d = _clamp_percent(100 - available_7d_percent)
    active_effective_used_7d = _ratio_percent(active_seven_day_dynamic_used_usd, active_seven_day_capacity_usd)
    active_five_hour_peak_multiple = _ratio_or_none(active_five_hour_capacity_usd, five_hour_peak_cost)
    active_recent_day_five_hour_peak_multiple = _ratio_or_none(active_five_hour_capacity_usd, recent_day_five_hour_peak_cost)
    active_current_speed_days = _ratio_or_none(active_seven_day_remaining_estimated_usd, recent_24h_cost)
    active_five_x_speed_days = _ratio_or_none(active_seven_day_remaining_estimated_usd, recent_24h_cost * 5 if recent_24h_cost > 0 else 0)
    five_hour_peak_multiple = _ratio_or_none(five_hour_capacity_usd, five_hour_peak_cost)
    recent_day_five_hour_peak_multiple = _ratio_or_none(five_hour_capacity_usd, recent_day_five_hour_peak_cost)
    burst_1h_five_hour_multiple = _ratio_or_none(five_hour_capacity_usd, burst_summary["five_hour_estimated_cost"])
    active_burst_1h_five_hour_multiple = _ratio_or_none(active_five_hour_capacity_usd, burst_summary["five_hour_estimated_cost"])
    recent_5h_multiple = _ratio_or_none(five_hour_capacity_usd, recent_5h_cost)
    twenty_four_hour_peak_multiple = _ratio_or_none(twenty_four_hour_capacity_usd, seven_day_24h_peak_cost)
    recent_24h_multiple = _ratio_or_none(twenty_four_hour_capacity_usd, recent_24h_cost)
    current_speed_multiple = _ratio_or_none(seven_day_capacity_usd, recent_24h_cost * 7 if recent_24h_cost > 0 else 0)
    current_speed_days = _ratio_or_none(seven_day_remaining_estimated_usd, recent_24h_cost)
    five_x_speed_days = _ratio_or_none(seven_day_remaining_estimated_usd, recent_24h_cost * 5 if recent_24h_cost > 0 else 0)
    recent_day_five_hour_peak_daily_cost = recent_day_five_hour_peak_cost / 5 * 24 if recent_day_five_hour_peak_cost > 0 else 0
    seven_day_five_hour_peak_daily_cost = five_hour_peak_cost / 5 * 24 if five_hour_peak_cost > 0 else 0
    recent_day_five_hour_peak_speed_days = _ratio_or_none(seven_day_remaining_estimated_usd, recent_day_five_hour_peak_daily_cost)
    five_x_recent_day_five_hour_peak_speed_days = _ratio_or_none(
        seven_day_remaining_estimated_usd,
        recent_day_five_hour_peak_daily_cost * 5 if recent_day_five_hour_peak_daily_cost > 0 else 0,
    )
    seven_day_five_hour_peak_speed_days = _ratio_or_none(seven_day_remaining_estimated_usd, seven_day_five_hour_peak_daily_cost)
    five_x_seven_day_five_hour_peak_speed_days = _ratio_or_none(
        seven_day_remaining_estimated_usd,
        seven_day_five_hour_peak_daily_cost * 5 if seven_day_five_hour_peak_daily_cost > 0 else 0,
    )
    seven_day_peak_speed_days = _ratio_or_none(seven_day_remaining_estimated_usd, seven_day_24h_peak_cost)
    five_x_peak_speed_days = _ratio_or_none(seven_day_remaining_estimated_usd, seven_day_24h_peak_cost * 5 if seven_day_24h_peak_cost > 0 else 0)
    active_seven_day_peak_speed_days = _ratio_or_none(active_seven_day_remaining_estimated_usd, seven_day_24h_peak_cost)
    active_five_x_peak_speed_days = _ratio_or_none(active_seven_day_remaining_estimated_usd, seven_day_24h_peak_cost * 5 if seven_day_24h_peak_cost > 0 else 0)
    five_x_peak_multiple = _ratio_or_none(five_hour_capacity_usd, five_hour_peak_cost * 5 if five_hour_peak_cost > 0 else 0)
    five_x_recent_day_peak_multiple = _ratio_or_none(five_hour_capacity_usd, recent_day_five_hour_peak_cost * 5 if recent_day_five_hour_peak_cost > 0 else 0)
    five_x_24h_peak_multiple = _ratio_or_none(twenty_four_hour_capacity_usd, seven_day_24h_peak_cost * 5 if seven_day_24h_peak_cost > 0 else 0)
    recent_5h_remaining_usd = max(0.0, five_hour_capacity_usd - recent_5h_cost)
    recent_24h_remaining_usd = max(0.0, twenty_four_hour_capacity_usd - recent_24h_cost)
    seven_day_remaining_usd = max(0.0, seven_day_capacity_usd - seven_day_cost)
    tpm_samples = await _load_group_tpm_samples(db, site_id=site_id, group_id=group_id)
    concurrency_total = float(concurrency_summary.get("concurrency_total_capacity") or 0)
    concurrency_accounts = int(concurrency_summary.get("concurrency_eligible_accounts") or 0)
    average_account_concurrency = concurrency_total / concurrency_accounts if concurrency_accounts > 0 else 0.0
    realtime_risk = calculate_capacity_risk(
        samples=tpm_samples,
        now=now_utc(),
        cost_per_token=_number_or_none(cost_summary.get("recent_6h_cost_per_token")),
        actual_five_hour_remaining_usd=five_hour_actual_remaining_usd,
        dynamic_five_hour_remaining_usd=dynamic_five_hour_remaining_estimated_usd,
        actual_seven_day_remaining_usd=seven_day_actual_remaining_usd,
        dynamic_seven_day_remaining_usd=seven_day_remaining_estimated_usd,
        available_accounts=selected["available_accounts"],
        safe_concurrency_available=float(concurrency_summary.get("concurrency_safe_available") or 0),
        per_account_five_hour_usd=float(selected_limits.get("five_hour_usd") or 0),
        per_account_seven_day_usd=float(selected_limits.get("seven_day_usd") or 0),
        average_account_concurrency=average_account_concurrency,
        refill_account_options=_refill_account_options(primary_type, capacity_limits),
        primary_refill_account_type=primary_type,
    )
    health = {
        "status": realtime_risk["health_status"],
        "label": realtime_risk["health_label"],
        "tone": realtime_risk["health_tone"],
        "reason": realtime_risk["health_reason"],
    }
    risk_details = {
        key: value
        for key, value in realtime_risk.items()
        if key not in {"health_status", "health_label", "health_tone", "health_reason"}
    }
    return {
        "capacity_model": "single_pool_realtime",
        "available_accounts": selected["available_accounts"] + selected_reserve["available_accounts"],
        "available_5h_accounts": selected["available_5h_accounts"] + selected_reserve["available_5h_accounts"],
        "active_available_accounts": selected["available_accounts"],
        "active_available_5h_accounts": selected["available_5h_accounts"],
        "reserve_available_accounts": selected_reserve["available_accounts"],
        "reserve_available_5h_accounts": selected_reserve["available_5h_accounts"],
        "account_type": primary_type,
        "type_summary": type_summary,
        "reserve_type_summary": reserve_type_summary,
        "capacity_limits": capacity_limits,
        "used_5h_percent": effective_used_5h,
        "available_5h_percent": available_5h_percent,
        "active_available_5h_percent": active_available_5h_percent,
        "actual_available_5h_percent": actual_available_5h_percent,
        "active_actual_available_5h_percent": active_actual_available_5h_percent,
        "used_7d_percent": effective_used_7d,
        "available_7d_percent": available_7d_percent,
        "actual_available_7d_percent": actual_available_7d_percent,
        "active_used_5h_percent": active_effective_used_5h,
        "active_dynamic_used_5h_percent": active_effective_used_5h,
        "active_used_7d_percent": active_effective_used_7d,
        "active_five_hour_capacity_usd": round(active_five_hour_capacity_usd, 4),
        "active_seven_day_capacity_usd": round(active_seven_day_capacity_usd, 4),
        "reserve_five_hour_capacity_usd": round(selected_reserve["five_hour_capacity_usd"], 4),
        "reserve_seven_day_capacity_usd": round(selected_reserve["seven_day_capacity_usd"], 4),
        "five_hour_capacity_usd": round(five_hour_capacity_usd, 4),
        "dynamic_five_hour_capacity_usd": round(dynamic_five_hour_capacity_usd, 4),
        "active_dynamic_five_hour_capacity_usd": round(active_dynamic_five_hour_capacity_usd, 4),
        "reserve_dynamic_five_hour_capacity_usd": round(reserve_dynamic_five_hour_capacity_usd, 4),
        "seven_day_capacity_usd": round(seven_day_capacity_usd, 4),
        "twenty_four_hour_capacity_usd": round(twenty_four_hour_capacity_usd, 4),
        "five_hour_used_estimated_usd": round(dynamic_five_hour_used_estimated_usd, 4),
        "five_hour_remaining_estimated_usd": round(dynamic_five_hour_remaining_estimated_usd, 4),
        "dynamic_five_hour_used_estimated_usd": round(dynamic_five_hour_used_estimated_usd, 4),
        "dynamic_five_hour_remaining_estimated_usd": round(dynamic_five_hour_remaining_estimated_usd, 4),
        "active_dynamic_five_hour_used_estimated_usd": round(active_dynamic_five_hour_used_usd, 4),
        "active_dynamic_five_hour_remaining_estimated_usd": round(active_dynamic_five_hour_remaining_usd, 4),
        "reserve_dynamic_five_hour_remaining_estimated_usd": round(reserve_dynamic_five_hour_remaining_usd, 4),
        "five_hour_actual_used_usd": round(five_hour_actual_used_usd, 4),
        "five_hour_actual_remaining_usd": round(five_hour_actual_remaining_usd, 4),
        "active_five_hour_actual_remaining_usd": round(active_five_hour_actual_remaining_usd, 4),
        "reserve_five_hour_actual_remaining_usd": round(reserve_five_hour_actual_remaining_usd, 4),
        "seven_day_used_estimated_usd": round(seven_day_used_estimated_usd, 4),
        "seven_day_remaining_estimated_usd": round(seven_day_remaining_estimated_usd, 4),
        "active_seven_day_dynamic_used_estimated_usd": round(active_seven_day_dynamic_used_usd, 4),
        "active_seven_day_dynamic_remaining_estimated_usd": round(active_seven_day_dynamic_remaining_usd, 4),
        "reserve_seven_day_dynamic_used_estimated_usd": round(reserve_seven_day_dynamic_used_usd, 4),
        "reserve_seven_day_dynamic_remaining_estimated_usd": round(reserve_seven_day_dynamic_remaining_usd, 4),
        "seven_day_actual_used_usd": round(seven_day_actual_used_usd, 4),
        "seven_day_actual_remaining_usd": round(seven_day_actual_remaining_usd, 4),
        "active_seven_day_actual_remaining_usd": round(active_seven_day_actual_remaining_usd, 4),
        "reserve_seven_day_actual_remaining_usd": round(reserve_seven_day_actual_remaining_usd, 4),
        "five_hour_peak_cost": round(five_hour_peak_cost, 4),
        "seven_day_five_hour_peak_cost": round(five_hour_peak_cost, 4),
        "recent_day_five_hour_peak_cost": round(recent_day_five_hour_peak_cost, 4),
        "burst_1h_observed_cost": round(burst_summary["observed_cost"], 4),
        "burst_1h_elapsed_minutes": burst_summary["elapsed_minutes"],
        "burst_1h_projection_multiplier": round(burst_summary["projection_multiplier"], 4),
        "burst_1h_cost": round(burst_summary["cost"], 4),
        "burst_1h_five_hour_estimated_cost": round(burst_summary["five_hour_estimated_cost"], 4),
        "burst_1h_five_hour_multiple": _round_optional(burst_1h_five_hour_multiple),
        "active_burst_1h_five_hour_multiple": _round_optional(active_burst_1h_five_hour_multiple),
        "burst_1h_source": burst_summary["source"],
        "burst_1h_window_count": burst_summary["window_count"],
        "burst_1h_trend": burst_summary["trend"],
        "burst_1h_trend_label": burst_summary["trend_label"],
        "burst_1h_trend_strength": burst_summary["trend_strength"],
        "burst_1h_trend_strength_label": burst_summary["trend_strength_label"],
        "burst_1h_trend_change_percent": _round_optional(burst_summary["trend_change_percent"]),
        "burst_1h_previous_cost": round(burst_summary["previous_cost"], 4),
        "burst_1h_trend_recent_avg_cost": round(burst_summary["trend_recent_avg_cost"], 4),
        "burst_1h_trend_baseline_avg_cost": round(burst_summary["trend_baseline_avg_cost"], 4),
        "burst_1h_trend_recent_hours": burst_summary["trend_recent_hours"],
        "burst_1h_trend_baseline_hours": burst_summary["trend_baseline_hours"],
        "seven_day_24h_peak_cost": round(seven_day_24h_peak_cost, 4),
        "recent_5h_cost": round(recent_5h_cost, 4),
        "recent_24h_cost": round(recent_24h_cost, 4),
        "estimated_recent_24h_consumed_accounts": _round_optional(estimated_recent_24h_consumed_accounts),
        "estimated_seven_day_peak_24h_consumed_accounts": _round_optional(estimated_seven_day_peak_24h_consumed_accounts),
        "estimated_24h_consumed_accounts": _round_optional(estimated_recent_24h_consumed_accounts),
        "seven_day_cost": round(seven_day_cost, 4),
        "recent_5h_remaining_usd": round(recent_5h_remaining_usd, 4),
        "recent_24h_remaining_usd": round(recent_24h_remaining_usd, 4),
        "seven_day_remaining_usd": round(seven_day_remaining_usd, 4),
        "five_hour_peak_multiple": _round_optional(five_hour_peak_multiple),
        "active_five_hour_peak_multiple": _round_optional(active_five_hour_peak_multiple),
        "recent_day_five_hour_peak_multiple": _round_optional(recent_day_five_hour_peak_multiple),
        "active_recent_day_five_hour_peak_multiple": _round_optional(active_recent_day_five_hour_peak_multiple),
        "recent_5h_multiple": _round_optional(recent_5h_multiple),
        "twenty_four_hour_peak_multiple": _round_optional(twenty_four_hour_peak_multiple),
        "recent_24h_multiple": _round_optional(recent_24h_multiple),
        "five_x_peak_multiple": _round_optional(five_x_peak_multiple),
        "five_x_recent_day_peak_multiple": _round_optional(five_x_recent_day_peak_multiple),
        "five_x_24h_peak_multiple": _round_optional(five_x_24h_peak_multiple),
        "current_speed_multiple": _round_optional(current_speed_multiple),
        "current_speed_days": _round_optional(current_speed_days),
        "active_current_speed_days": _round_optional(active_current_speed_days),
        "five_x_speed_days": _round_optional(five_x_speed_days),
        "active_five_x_speed_days": _round_optional(active_five_x_speed_days),
        "recent_day_five_hour_peak_daily_cost": round(recent_day_five_hour_peak_daily_cost, 4),
        "seven_day_five_hour_peak_daily_cost": round(seven_day_five_hour_peak_daily_cost, 4),
        "recent_day_five_hour_peak_speed_days": _round_optional(recent_day_five_hour_peak_speed_days),
        "five_x_recent_day_five_hour_peak_speed_days": _round_optional(five_x_recent_day_five_hour_peak_speed_days),
        "seven_day_five_hour_peak_speed_days": _round_optional(seven_day_five_hour_peak_speed_days),
        "five_x_seven_day_five_hour_peak_speed_days": _round_optional(five_x_seven_day_five_hour_peak_speed_days),
        "seven_day_peak_speed_days": _round_optional(seven_day_peak_speed_days),
        "five_x_peak_speed_days": _round_optional(five_x_peak_speed_days),
        "active_seven_day_peak_speed_days": _round_optional(active_seven_day_peak_speed_days),
        "active_five_x_peak_speed_days": _round_optional(active_five_x_peak_speed_days),
        "health_status": health["status"],
        "health_label": health["label"],
        "health_tone": health["tone"],
        "health_reason": health["reason"],
        "auto_refill_required": False,
        "realtime_risk_ready": realtime_risk["ready"],
        "replenishment_required": realtime_risk["replenishment_required"] if realtime_risk["ready"] else False,
        "recommended_refill_accounts": realtime_risk["recommended_refill_accounts"] if realtime_risk["ready"] else 0,
        **risk_details,
        **pool_status_summary,
        **concurrency_summary,
        "cost_window": cost_summary,
        "total_accounts": len(accounts),
        "capacity_duplicate_email_accounts": duplicate_capacity_accounts,
        "calculated_at": now_utc(),
    }


def _capacity_by_account_type(
    capacity_accounts: list[dict[str, Any]],
    five_hour_capacity_accounts: list[dict[str, Any]],
    capacity_limits: dict[str, dict[str, float]],
) -> dict[str, dict[str, Any]]:
    result = _empty_capacity_type_summary(capacity_limits)
    five_hour_ids = {str(account.get("id")) for account in five_hour_capacity_accounts}
    for account in capacity_accounts:
        account_type = _capacity_account_type(account)
        _add_capacity_account(result, account_type, five_hour_available=str(account.get("id")) in five_hour_ids, account=account, capacity_limits=capacity_limits)
    return result


def _collapse_capacity_accounts_by_email(accounts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_email: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    duplicates = 0
    for account in accounts:
        email_key = _capacity_email_key(account)
        if not email_key:
            passthrough.append(account)
            continue
        current = by_email.get(email_key)
        if current is None:
            by_email[email_key] = dict(account)
            continue
        duplicates += 1
        by_email[email_key] = _merge_capacity_duplicate_account(current, account)
    return [*by_email.values(), *passthrough], duplicates


def _merge_capacity_duplicate_account(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    merged_extra = dict(merged.get("extra") if isinstance(merged.get("extra"), dict) else {})
    right_extra = right.get("extra") if isinstance(right.get("extra"), dict) else {}
    usage_percent_fields = (
        "codex_5h_used_percent",
        "codex_7d_used_percent",
    )
    usage_sum_fields = (
        "codex_5h_request_count",
        "codex_7d_request_count",
        "codex_total_request_count",
        "codex_5h_token_count",
        "codex_7d_token_count",
        "codex_total_token_count",
        "codex_5h_actual_cost",
        "codex_7d_actual_cost",
        "codex_total_actual_cost",
        "codex_5h_total_cost",
        "codex_7d_total_cost",
        "codex_total_cost",
    )
    for field in usage_percent_fields:
        merged[field] = _clamp_percent(_usage_float(left, field) + _usage_float(right, field))
        merged_extra[field] = merged[field]
    for field in usage_sum_fields:
        merged[field] = _usage_float(left, field) + _usage_float(right, field)
        merged_extra[field] = merged[field]
    for field in ("codex_5h_reset_after_seconds", "codex_7d_reset_after_seconds"):
        values = [_usage_number(left, field), _usage_number(right, field)]
        numeric = [float(value) for value in values if isinstance(value, (int, float))]
        if numeric:
            merged[field] = min(numeric)
            merged_extra[field] = merged[field]
    merged["id"] = left.get("id")
    merged["duplicate_capacity_account_ids"] = [*_capacity_duplicate_ids(left), right.get("id")]
    merged["duplicate_capacity_account_count"] = len([item for item in merged["duplicate_capacity_account_ids"] if item is not None])
    merged["extra"] = merged_extra
    if _is_7d_exhausted(left) or _is_7d_exhausted(right):
        merged["codex_7d_used_percent"] = 100
        merged_extra["codex_7d_used_percent"] = 100
    return merged


def _capacity_duplicate_ids(account: dict[str, Any]) -> list[Any]:
    values = account.get("duplicate_capacity_account_ids")
    if isinstance(values, list) and values:
        return values
    return [account.get("id")]


def _empty_capacity_type_summary(capacity_limits: dict[str, dict[str, float]] | None = None) -> dict[str, dict[str, Any]]:
    limits = capacity_limits or CAPACITY_ACCOUNT_LIMITS
    result = {
        **{
            account_type: {
                "available_accounts": 0,
                "available_5h_accounts": 0,
                "five_hour_capacity_usd": 0.0,
                "seven_day_capacity_usd": 0.0,
                "five_hour_dynamic_capacity_usd": 0.0,
                "five_hour_dynamic_used_usd": 0.0,
                "five_hour_dynamic_remaining_usd": 0.0,
                "five_hour_actual_used_usd": 0.0,
                "five_hour_actual_remaining_usd": 0.0,
                "seven_day_dynamic_used_usd": 0.0,
                "seven_day_dynamic_remaining_usd": 0.0,
                "seven_day_actual_used_usd": 0.0,
                "seven_day_actual_remaining_usd": 0.0,
            }
            for account_type in limits
        },
        "total": {
            "available_accounts": 0,
            "available_5h_accounts": 0,
            "five_hour_capacity_usd": 0.0,
            "seven_day_capacity_usd": 0.0,
            "five_hour_dynamic_capacity_usd": 0.0,
            "five_hour_dynamic_used_usd": 0.0,
            "five_hour_dynamic_remaining_usd": 0.0,
            "five_hour_actual_used_usd": 0.0,
            "five_hour_actual_remaining_usd": 0.0,
            "seven_day_dynamic_used_usd": 0.0,
            "seven_day_dynamic_remaining_usd": 0.0,
            "seven_day_actual_used_usd": 0.0,
            "seven_day_actual_remaining_usd": 0.0,
        },
    }
    return result


async def _reserve_capacity_by_account_type(
    db: AsyncIOMotorDatabase,
    site_id: str,
    group_id: int | None,
    capacity_limits: dict[str, dict[str, float]],
) -> dict[str, dict[str, Any]]:
    result = _empty_capacity_type_summary(capacity_limits)
    if group_id is None:
        return result
    query = {
        "metadata.deleted_at": {"$exists": False},
        "metadata.pool_status": "reserve",
        "metadata.sub2api_site_id": site_id,
        "$or": [
            {"metadata.sub2api_group_id": group_id},
            {"metadata.pool_id": str(group_id)},
        ],
    }
    seen_emails: set[str] = set()
    projection = {
        "metadata.email": 1,
        "metadata.account_type": 1,
        "account_json.credentials.email": 1,
        "account_json.credentials.plan_type": 1,
        "account_json.extra.email": 1,
        "account_json.extra.account_type": 1,
        "account_json.extra.plan_type": 1,
        "account_json.extra.codex_5h_window_minutes": 1,
        "account_json.extra.codex_7d_window_minutes": 1,
        "account_json.codex_5h_window_minutes": 1,
        "account_json.codex_7d_window_minutes": 1,
    }
    async for account in db.accounts.find(query, projection):
        email_key = _local_capacity_email_key(account)
        if email_key:
            if email_key in seen_emails:
                continue
            seen_emails.add(email_key)
        _add_capacity_account(result, _local_capacity_account_type(account), five_hour_available=True, account=None, capacity_limits=capacity_limits)
    return result


def _add_capacity_account(
    result: dict[str, dict[str, Any]],
    account_type: str,
    *,
    five_hour_available: bool,
    account: dict[str, Any] | None = None,
    capacity_limits: dict[str, dict[str, float]] | None = None,
) -> None:
    limits_by_type = capacity_limits or CAPACITY_ACCOUNT_LIMITS
    if account_type == "bug_team" or account_type not in limits_by_type:
        return
    limits = limits_by_type[account_type]
    dynamic_five_hour = _dynamic_five_hour_usage(account, limits["five_hour_usd"], limits["seven_day_usd"], five_hour_available=five_hour_available)
    result[account_type]["available_accounts"] += 1
    result[account_type]["seven_day_capacity_usd"] += limits["seven_day_usd"]
    result[account_type]["five_hour_capacity_usd"] += limits["five_hour_usd"]
    result[account_type]["five_hour_dynamic_capacity_usd"] += dynamic_five_hour["capacity_usd"]
    result[account_type]["five_hour_dynamic_used_usd"] += dynamic_five_hour["used_usd"]
    result[account_type]["five_hour_dynamic_remaining_usd"] += dynamic_five_hour["remaining_usd"]
    result[account_type]["five_hour_actual_used_usd"] += dynamic_five_hour["actual_used_usd"]
    result[account_type]["five_hour_actual_remaining_usd"] += dynamic_five_hour["actual_remaining_usd"]
    result[account_type]["seven_day_dynamic_used_usd"] += dynamic_five_hour["seven_day_dynamic_used_usd"]
    result[account_type]["seven_day_dynamic_remaining_usd"] += dynamic_five_hour["seven_day_dynamic_remaining_usd"]
    result[account_type]["seven_day_actual_used_usd"] += dynamic_five_hour["seven_day_actual_used_usd"]
    result[account_type]["seven_day_actual_remaining_usd"] += dynamic_five_hour["seven_day_actual_remaining_usd"]
    result["total"]["available_accounts"] += 1
    result["total"]["seven_day_capacity_usd"] += limits["seven_day_usd"]
    result["total"]["five_hour_capacity_usd"] += limits["five_hour_usd"]
    result["total"]["five_hour_dynamic_capacity_usd"] += dynamic_five_hour["capacity_usd"]
    result["total"]["five_hour_dynamic_used_usd"] += dynamic_five_hour["used_usd"]
    result["total"]["five_hour_dynamic_remaining_usd"] += dynamic_five_hour["remaining_usd"]
    result["total"]["five_hour_actual_used_usd"] += dynamic_five_hour["actual_used_usd"]
    result["total"]["five_hour_actual_remaining_usd"] += dynamic_five_hour["actual_remaining_usd"]
    result["total"]["seven_day_dynamic_used_usd"] += dynamic_five_hour["seven_day_dynamic_used_usd"]
    result["total"]["seven_day_dynamic_remaining_usd"] += dynamic_five_hour["seven_day_dynamic_remaining_usd"]
    result["total"]["seven_day_actual_used_usd"] += dynamic_five_hour["seven_day_actual_used_usd"]
    result["total"]["seven_day_actual_remaining_usd"] += dynamic_five_hour["seven_day_actual_remaining_usd"]
    if five_hour_available:
        result[account_type]["available_5h_accounts"] += 1
        result["total"]["available_5h_accounts"] += 1


def _dynamic_five_hour_usage(account: dict[str, Any] | None, five_hour_limit_usd: float, seven_day_limit_usd: float, *, five_hour_available: bool) -> dict[str, float]:
    if not five_hour_available:
        return {
            "capacity_usd": 0.0,
            "used_usd": 0.0,
            "remaining_usd": 0.0,
            "actual_used_usd": 0.0,
            "actual_remaining_usd": 0.0,
            "seven_day_dynamic_used_usd": 0.0,
            "seven_day_dynamic_remaining_usd": 0.0,
            "seven_day_actual_used_usd": 0.0,
            "seven_day_actual_remaining_usd": 0.0,
        }
    if account is None:
        return {
            "capacity_usd": five_hour_limit_usd,
            "used_usd": 0.0,
            "remaining_usd": five_hour_limit_usd,
            "actual_used_usd": 0.0,
            "actual_remaining_usd": five_hour_limit_usd,
            "seven_day_dynamic_used_usd": 0.0,
            "seven_day_dynamic_remaining_usd": seven_day_limit_usd,
            "seven_day_actual_used_usd": 0.0,
            "seven_day_actual_remaining_usd": seven_day_limit_usd,
        }

    use_seven_day_percent = math.isclose(five_hour_limit_usd, seven_day_limit_usd, rel_tol=1e-9, abs_tol=1e-9)
    five_hour_prefix = "codex_7d" if use_seven_day_percent else "codex_5h"
    used_percent = _usage_number(account, f"{five_hour_prefix}_used_percent")
    used_percent = _clamp_percent(used_percent if isinstance(used_percent, (int, float)) else 0)
    seven_day_used_percent = _usage_number(account, "codex_7d_used_percent")
    seven_day_used_percent = _clamp_percent(seven_day_used_percent if isinstance(seven_day_used_percent, (int, float)) else 0)
    reset_after_seconds = _usage_number(account, f"{five_hour_prefix}_reset_after_seconds")
    if not isinstance(reset_after_seconds, (int, float)):
        reset_at = _parse_datetime(_first_present(account, account.get("extra") if isinstance(account.get("extra"), dict) else {}, f"{five_hour_prefix}_reset_at", "7d_reset_at" if use_seven_day_percent else "5h_reset_at"))
        reset_after_seconds = max(0, (reset_at - now_utc()).total_seconds()) if reset_at is not None else (SEVEN_DAY_WINDOW_SECONDS if use_seven_day_percent else FIVE_HOUR_WINDOW_SECONDS)
    window_minutes = _usage_number(account, f"{five_hour_prefix}_window_minutes")
    window_seconds = max(1.0, float(window_minutes) * 60) if isinstance(window_minutes, (int, float)) and window_minutes > 0 else (SEVEN_DAY_WINDOW_SECONDS if use_seven_day_percent else FIVE_HOUR_WINDOW_SECONDS)
    reset_factor = max(0.0, min(1.0, float(reset_after_seconds) / window_seconds))
    actual_used_usd = max(0.0, min(five_hour_limit_usd, five_hour_limit_usd * used_percent / 100))
    seven_day_actual_used_usd = max(0.0, min(seven_day_limit_usd, seven_day_limit_usd * seven_day_used_percent / 100))
    seven_day_reset_after_seconds = _usage_number(account, "codex_7d_reset_after_seconds")
    if not isinstance(seven_day_reset_after_seconds, (int, float)):
        seven_day_reset_at = _parse_datetime(_first_present(account, account.get("extra") if isinstance(account.get("extra"), dict) else {}, "codex_7d_reset_at", "7d_reset_at"))
        seven_day_reset_after_seconds = max(0, (seven_day_reset_at - now_utc()).total_seconds()) if seven_day_reset_at is not None else SEVEN_DAY_WINDOW_SECONDS
    seven_day_window_minutes = _usage_number(account, "codex_7d_window_minutes")
    seven_day_window_seconds = max(1.0, float(seven_day_window_minutes) * 60) if isinstance(seven_day_window_minutes, (int, float)) and seven_day_window_minutes > 0 else SEVEN_DAY_WINDOW_SECONDS
    seven_day_reset_factor = max(0.0, min(1.0, float(seven_day_reset_after_seconds) / seven_day_window_seconds))
    if float(seven_day_reset_after_seconds) > SEVEN_DAY_DYNAMIC_MAX_WAIT_SECONDS:
        seven_day_dynamic_used_usd = seven_day_actual_used_usd
    else:
        seven_day_dynamic_used_usd = seven_day_limit_usd * seven_day_used_percent / 100 * seven_day_reset_factor
        seven_day_dynamic_used_usd = max(0.0, min(seven_day_limit_usd, seven_day_dynamic_used_usd))
    if float(reset_after_seconds) > FIVE_HOUR_DYNAMIC_MAX_WAIT_SECONDS:
        return {
            "capacity_usd": five_hour_limit_usd,
            "used_usd": actual_used_usd,
            "remaining_usd": max(0.0, five_hour_limit_usd - actual_used_usd),
            "actual_used_usd": actual_used_usd,
            "actual_remaining_usd": max(0.0, five_hour_limit_usd - actual_used_usd),
            "seven_day_dynamic_used_usd": seven_day_dynamic_used_usd,
            "seven_day_dynamic_remaining_usd": max(0.0, seven_day_limit_usd - seven_day_dynamic_used_usd),
            "seven_day_actual_used_usd": seven_day_actual_used_usd,
            "seven_day_actual_remaining_usd": max(0.0, seven_day_limit_usd - seven_day_actual_used_usd),
        }
    dynamic_used_usd = five_hour_limit_usd * used_percent / 100 * reset_factor
    dynamic_used_usd = max(0.0, min(five_hour_limit_usd, dynamic_used_usd))
    return {
        "capacity_usd": five_hour_limit_usd,
        "used_usd": dynamic_used_usd,
        "remaining_usd": max(0.0, five_hour_limit_usd - dynamic_used_usd),
        "actual_used_usd": actual_used_usd,
        "actual_remaining_usd": max(0.0, five_hour_limit_usd - actual_used_usd),
        "seven_day_dynamic_used_usd": seven_day_dynamic_used_usd,
        "seven_day_dynamic_remaining_usd": max(0.0, seven_day_limit_usd - seven_day_dynamic_used_usd),
        "seven_day_actual_used_usd": seven_day_actual_used_usd,
        "seven_day_actual_remaining_usd": max(0.0, seven_day_limit_usd - seven_day_actual_used_usd),
    }


def _primary_capacity_type(type_summary: dict[str, dict[str, Any]]) -> str:
    candidates = [
        (account_type, int(summary["available_accounts"]))
        for account_type, summary in type_summary.items()
        if account_type != "total"
    ]
    if not any(count > 0 for _, count in candidates):
        return "total"
    priority = {"pro": 6, "bug_team": 5, "plus": 4, "k12": 3, "team": 2, "free": 1}
    return max(candidates, key=lambda item: (item[1], priority.get(item[0], 0)))[0]


def _refill_account_options(
    primary_type: str,
    capacity_limits: dict[str, dict[str, float]],
) -> dict[str, dict[str, float]]:
    normalized_type = str(primary_type or "").strip().lower()
    if normalized_type in {"", "total", "bug_team"}:
        return {}
    account_types = REFILL_ACCOUNT_TYPES_BY_POOL.get(normalized_type, (normalized_type,))
    return {
        account_type: dict(capacity_limits[account_type])
        for account_type in account_types
        if account_type in capacity_limits
    }


def _capacity_account_type(account: dict[str, Any]) -> str:
    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    value = account.get("plan_type") or credentials.get("plan_type") or extra.get("account_type") or extra.get("plan_type")
    normalized = _normalize_capacity_account_type(value)
    if is_bug_team_account(account):
        return "bug_team"
    if normalized in {"team", "team_sub", "team-sub", "team_child", "team_child_account", "team子号", "team 子号"}:
        return "team"
    if normalized == "k12":
        return "k12"
    if normalized == "pro":
        return "pro"
    if normalized == "plus":
        return "plus"
    if normalized == "free":
        return "free"
    text_values: list[str] = [
        str(account.get("name") or ""),
        str(account.get("notes") or ""),
        str(extra.get("name") or ""),
        str(extra.get("notes") or ""),
    ]
    groups = account.get("groups") if isinstance(account.get("groups"), list) else []
    account_groups = account.get("account_groups") if isinstance(account.get("account_groups"), list) else []
    text_values.extend(str(group.get("name") or "") for group in groups if isinstance(group, dict))
    for account_group in account_groups:
        if isinstance(account_group, dict) and isinstance(account_group.get("group"), dict):
            text_values.append(str(account_group["group"].get("name") or ""))
    combined = " ".join(text_values).lower()
    if any(marker in combined for marker in ("team子号", "team 子号", "team-sub", "team_sub", "team child", "team member", "子号")):
        return "team"
    if "k12" in combined:
        return "k12"
    if any(marker in combined for marker in ("pro", "20x")):
        return "pro"
    if any(marker in combined for marker in ("plus", "付费", "购买plus")):
        return "plus"
    if any(marker in combined for marker in ("free", "免费")):
        return "free"
    if credentials.get("subscription_expires_at") or extra.get("subscription_expires_at"):
        return "plus"
    if str(account.get("platform") or "").lower() == "openai":
        return "free"
    return "other"


def is_bug_team_account(account: dict[str, Any]) -> bool:
    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    plan_type = _normalize_capacity_account_type(account.get("plan_type") or credentials.get("plan_type") or extra.get("plan_type"))
    if plan_type not in {"team", "bug_team"}:
        return False
    five_hour_window = _usage_number(account, "codex_5h_window_minutes")
    seven_day_window = _usage_number(account, "codex_7d_window_minutes")
    return five_hour_window == 0 and isinstance(seven_day_window, (int, float)) and seven_day_window >= BUG_TEAM_MIN_WINDOW_MINUTES


def _local_capacity_account_type(account: dict[str, Any]) -> str:
    metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
    account_json = account.get("account_json") if isinstance(account.get("account_json"), dict) else {}
    credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else {}
    extra = account_json.get("extra") if isinstance(account_json.get("extra"), dict) else {}
    normalized = _normalize_capacity_account_type(metadata.get("account_type") or extra.get("account_type") or credentials.get("plan_type"))
    five_hour_window = _number_or_none(_first_present(account_json, extra, "codex_5h_window_minutes"))
    seven_day_window = _number_or_none(_first_present(account_json, extra, "codex_7d_window_minutes"))
    if normalized == "bug_team" or (
        normalized == "team"
        and five_hour_window == 0
        and isinstance(seven_day_window, (int, float))
        and seven_day_window >= BUG_TEAM_MIN_WINDOW_MINUTES
    ):
        return "bug_team"
    if normalized == "team":
        return "team"
    if normalized == "k12":
        return "k12"
    if normalized == "pro":
        return "pro"
    if normalized == "plus":
        return "plus"
    if normalized == "free":
        return "free"
    return "other"


def _normalize_capacity_account_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"team", "team_sub", "team-sub", "team_child", "team_child_account", "team子号", "team 子号", "team瀛愬彿", "team 瀛愬彿"}:
        return "team"
    if "k12" in normalized:
        return "k12"
    if "pro" in normalized or "20x" in normalized:
        return "pro"
    if "plus" in normalized:
        return "plus"
    return normalized


async def _dashboard_cost_summary(db: AsyncIOMotorDatabase, site_id: str, *, group_id: int | None = None) -> dict[str, Any]:
    query: dict[str, Any] = {"site_id": site_id, "group_id": group_id}
    if group_id is None:
        query = {"site_id": site_id, "$or": [{"group_id": None}, {"group_id": {"$exists": False}}]}
    hourly_docs = [
        doc
        async for doc in db.sub2api_dashboard_trends.find({**query, "granularity": "hour"}).sort("bucket_at", -1).limit(24 * 8)
    ]
    daily_docs = [
        doc
        async for doc in db.sub2api_dashboard_trends.find({**query, "granularity": "day"}).sort("bucket_at", -1).limit(14)
    ]
    hourly = list(reversed(hourly_docs))
    current_time = now_utc()
    hourly_7d = _dashboard_docs_since(hourly, current_time - timedelta(days=7))
    hourly_24h = _dashboard_docs_since(hourly, current_time - timedelta(hours=24))
    hourly_8h = _dashboard_docs_since(hourly, current_time - timedelta(hours=8))
    hourly_6h = _dashboard_docs_since(hourly, current_time - timedelta(hours=6))
    hourly_5h = _dashboard_docs_since(hourly, current_time - timedelta(hours=5))
    daily_7d = _dashboard_docs_since(daily_docs, current_time - timedelta(days=7))
    five_hour_peak_cost = _five_hour_daily_peak_cost(hourly_7d)
    recent_day_five_hour_peak_cost = _rolling_peak_cost(hourly_24h, 5)
    burst_1h = _burst_1h_summary(hourly_8h)
    daily_costs = [_float_or_zero(doc.get("cost")) for doc in daily_7d]
    seven_day_24h_peak_cost = round(max(daily_costs) if daily_costs else _rolling_peak_cost(hourly_7d, 24), 6)
    recent_24h_cost = round(sum(_float_or_zero(doc.get("cost")) for doc in hourly_24h), 6)
    recent_5h_cost = round(sum(_float_or_zero(doc.get("cost")) for doc in hourly_5h), 6)
    recent_6h_docs = hourly_6h
    recent_6h_cost = sum(_float_or_zero(doc.get("actual_cost") if doc.get("actual_cost") is not None else doc.get("cost")) for doc in recent_6h_docs)
    recent_6h_tokens = sum(_float_or_zero(doc.get("total_tokens")) for doc in recent_6h_docs)
    recent_6h_cost_per_token = recent_6h_cost / recent_6h_tokens if recent_6h_tokens > 0 else None
    seven_day_cost = round(sum(daily_costs), 6)
    return {
        "five_hour_peak_cost": five_hour_peak_cost,
        "seven_day_five_hour_peak_cost": five_hour_peak_cost,
        "recent_day_five_hour_peak_cost": recent_day_five_hour_peak_cost,
        "burst_1h": burst_1h,
        "seven_day_24h_peak_cost": seven_day_24h_peak_cost,
        "recent_24h_cost": recent_24h_cost,
        "recent_5h_cost": recent_5h_cost,
        "recent_6h_cost": round(recent_6h_cost, 6),
        "recent_6h_tokens": round(recent_6h_tokens),
        "recent_6h_cost_per_token": recent_6h_cost_per_token,
        "seven_day_cost": seven_day_cost,
        "hourly_points": len(hourly_docs),
        "daily_points": len(daily_docs),
        "group_id": group_id,
        "calculated_at": now_utc(),
    }


async def _load_group_tpm_samples(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int | None,
) -> list[dict[str, Any]]:
    collection = getattr(db, "sub2api_tpm_samples", None)
    if group_id is None or collection is None:
        return []
    cutoff = now_utc() - timedelta(hours=6)
    cursor = collection.find(
        {
            "site_id": site_id,
            "group_id": group_id,
            "schema_version": 2,
            "sampled_at": {"$gte": cutoff},
        },
        {
            "sampled_at": 1,
            "tpm": 1,
            "rpm": 1,
            "average_duration_ms": 1,
            "current_concurrency": 1,
        },
    ).sort("sampled_at", 1).limit(400)
    return [doc async for doc in cursor]


def _dashboard_docs_since(items: list[dict[str, Any]], cutoff: datetime) -> list[dict[str, Any]]:
    cutoff = cutoff.astimezone(UTC) if cutoff.tzinfo else cutoff.replace(tzinfo=UTC)
    return [
        item
        for item in items
        if (_parse_datetime(item.get("bucket_at")) or datetime.min.replace(tzinfo=UTC)) >= cutoff
    ]


def _five_hour_daily_peak_cost(hourly: list[dict[str, Any]]) -> float:
    by_day: dict[str, list[dict[str, Any]]] = {}
    for doc in hourly:
        bucket = str(doc.get("bucket") or "")
        day = bucket[:10]
        if day:
            by_day.setdefault(day, []).append(doc)
    peak = 0.0
    for docs in by_day.values():
        docs.sort(key=lambda item: str(item.get("bucket") or ""))
        costs = [_float_or_zero(doc.get("cost")) for doc in docs]
        if len(costs) >= 5:
            peak = max(peak, max(sum(costs[index:index + 5]) for index in range(0, len(costs) - 4)))
        elif costs:
            peak = max(peak, sum(costs))
    return round(peak, 6)


def _rolling_peak_cost(items: list[dict[str, Any]], window_size: int) -> float:
    costs = [_float_or_zero(item.get("cost")) for item in items]
    if not costs:
        return 0.0
    if len(costs) <= window_size:
        return round(sum(costs), 6)
    return round(max(sum(costs[index:index + window_size]) for index in range(0, len(costs) - window_size + 1)), 6)


def _burst_1h_summary(hourly: list[dict[str, Any]]) -> dict[str, Any]:
    recent_docs = hourly[-8:]
    costs = [_float_or_zero(doc.get("cost")) for doc in recent_docs]
    latest_doc = recent_docs[-1] if recent_docs else None
    elapsed_minutes = _latest_hour_elapsed_minutes(latest_doc)
    projection_multiplier = 60 / max(5, elapsed_minutes)
    if not costs:
        return {
            "observed_cost": 0.0,
            "cost": 0.0,
            "previous_cost": 0.0,
            "trend_recent_avg_cost": 0.0,
            "trend_baseline_avg_cost": 0.0,
            "trend_recent_hours": 0,
            "trend_baseline_hours": 0,
            "five_hour_estimated_cost": 0.0,
            "elapsed_minutes": elapsed_minutes,
            "projection_multiplier": projection_multiplier,
            "trend": "unknown",
            "trend_label": "等待数据",
            "trend_strength": "unknown",
            "trend_strength_label": "等待数据",
            "trend_change_percent": None,
            "source": "hourly",
            "window_count": 0,
        }
    observed_current = costs[-1]
    current = observed_current * projection_multiplier
    completed = costs[:-1]
    recent_values = ([current] + completed[-2:]) if completed else [current]
    baseline_values = completed[-5:-2] if len(completed) >= 5 else completed[:-2]
    recent_average = sum(recent_values) / len(recent_values) if recent_values else 0.0
    baseline_average = sum(baseline_values) / len(baseline_values) if baseline_values else 0.0
    previous = baseline_average
    change_percent = None
    if baseline_average > 0:
        change_percent = (recent_average - baseline_average) / baseline_average * 100
    elif recent_average > 0:
        change_percent = 100.0
    trend, trend_label = _burst_trend_label(change_percent)
    strength, strength_label = _burst_trend_strength(change_percent)
    return {
        "observed_cost": round(observed_current, 6),
        "cost": round(current, 6),
        "previous_cost": round(previous, 6),
        "trend_recent_avg_cost": round(recent_average, 6),
        "trend_baseline_avg_cost": round(baseline_average, 6),
        "trend_recent_hours": len(recent_values),
        "trend_baseline_hours": len(baseline_values),
        "five_hour_estimated_cost": round(current * 5, 6),
        "elapsed_minutes": elapsed_minutes,
        "projection_multiplier": projection_multiplier,
        "trend": trend,
        "trend_label": trend_label,
        "trend_strength": strength,
        "trend_strength_label": strength_label,
        "trend_change_percent": change_percent,
        "source": "hourly",
        "window_count": len(costs),
    }


def _current_hour_elapsed_minutes() -> float:
    local_now = now_utc().astimezone(timezone(timedelta(hours=8)))
    return max(1, min(60, local_now.minute + local_now.second / 60))


def _latest_hour_elapsed_minutes(latest_doc: dict[str, Any] | None) -> float:
    if not latest_doc:
        return _current_hour_elapsed_minutes()
    local_now = now_utc().astimezone(timezone(timedelta(hours=8)))
    current_hour = local_now.replace(minute=0, second=0, microsecond=0)
    bucket_at = latest_doc.get("bucket_at")
    if isinstance(bucket_at, datetime):
        latest_hour = bucket_at
        if latest_hour.tzinfo is None:
            latest_hour = latest_hour.replace(tzinfo=UTC)
        latest_hour = latest_hour.astimezone(timezone(timedelta(hours=8))).replace(minute=0, second=0, microsecond=0)
    else:
        bucket = str(latest_doc.get("bucket") or "")
        try:
            latest_hour = datetime.strptime(bucket, "%Y-%m-%d %H:%M").replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            return 60.0
    if latest_hour == current_hour:
        return _current_hour_elapsed_minutes()
    return 60.0


def _burst_trend_label(change_percent: float | None) -> tuple[str, str]:
    if change_percent is None:
        return "unknown", "等待数据"
    if change_percent >= 10:
        return "rising", "上涨"
    if change_percent <= -10:
        return "falling", "下降"
    return "flat", "平稳"


def _burst_trend_strength(change_percent: float | None) -> tuple[str, str]:
    if change_percent is None:
        return "unknown", "等待数据"
    absolute = abs(change_percent)
    if absolute >= 80:
        return "extreme", "极强"
    if absolute >= 40:
        return "strong", "强"
    if absolute >= 15:
        return "medium", "中"
    return "weak", "弱"


def _capacity_health(
    *,
    available_accounts: int,
    reserve_accounts: int,
    five_hour_capacity_usd: float,
    seven_day_capacity_usd: float,
    five_hour_peak_multiple: float | None,
    active_five_hour_peak_multiple: float | None,
    recent_day_five_hour_peak_multiple: float | None,
    active_recent_day_five_hour_peak_multiple: float | None,
    twenty_four_hour_peak_multiple: float | None,
    current_speed_multiple: float | None,
    current_speed_days: float | None,
    active_current_speed_days: float | None,
    seven_day_peak_speed_days: float | None,
    five_x_speed_days: float | None,
) -> dict[str, Any]:
    thresholds = CAPACITY_HEALTH_THRESHOLDS
    auto_refill_required = _lt(active_recent_day_five_hour_peak_multiple, thresholds["auto_refill_recent_day_peak_multiple"]) or _lt(
        active_current_speed_days,
        thresholds["auto_refill_current_speed_days"],
    )
    base = {"auto_refill_required": auto_refill_required, "reserve_accounts": reserve_accounts}
    if five_hour_peak_multiple is None and current_speed_multiple is None:
        return {**base, "status": "pending", "label": "等待数据", "tone": "muted", "reason": "7天最高5h峰值倍数和当前速度倍数还没有数据"}
    if available_accounts <= thresholds["exhausted_available_accounts"] or five_hour_capacity_usd <= 0 or seven_day_capacity_usd <= 0:
        return {**base, "status": "exhausted", "label": "耗尽", "tone": "danger", "reason": "可用账号 <= 2，或理论容量为 0"}
    if _lt(recent_day_five_hour_peak_multiple, thresholds["exhausted_recent_day_peak_multiple"]) or _lt(
        current_speed_days,
        thresholds["exhausted_current_speed_days"],
    ):
        return {**base, "status": "exhausted", "label": "耗尽", "tone": "danger", "reason": "最近一天5h峰值低于 0.2x，或当前速度可用不足 6 小时"}
    if _lt(recent_day_five_hour_peak_multiple, thresholds["danger_recent_day_peak_multiple"]) or _lt(
        current_speed_days,
        thresholds["danger_current_speed_days"],
    ):
        return {**base, "status": "danger", "label": "危险", "tone": "danger", "reason": "最近一天5h峰值或当前速度已压到危险线"}
    if _lt(recent_day_five_hour_peak_multiple, thresholds["tight_peak_multiple"]) or _lt(current_speed_days, thresholds["tight_current_speed_days"]):
        reason = "含备用池后，最近一天5h峰值或当前速度仍偏紧"
        if auto_refill_required:
            reason = "已触发自动补号阈值，含备用池后仍按黄色维护"
        return {**base, "status": "tight", "label": "紧张", "tone": "warning", "reason": reason}
    if _gte(five_hour_peak_multiple, thresholds["very_abundant_peak_multiple"]) and _gte(
        seven_day_peak_speed_days,
        thresholds["very_abundant_seven_day_peak_speed_days"],
    ):
        return {**base, "status": "very_abundant", "label": "十分充裕", "tone": "excellent", "reason": "含备用池后，7天最高5h峰值 >= 5x 且 7天最高24h可用 >= 10天"}
    if _gte(recent_day_five_hour_peak_multiple, thresholds["abundant_recent_day_peak_multiple"]) and _gte(
        current_speed_days,
        thresholds["abundant_current_speed_days"],
    ):
        return {**base, "status": "abundant", "label": "充裕", "tone": "info", "reason": "含备用池后，最近一天5h峰值 >= 3x 且当前速度可用 >= 5天"}
    return {**base, "status": "healthy", "label": "健康", "tone": "success", "reason": "含备用池后容量处于健康范围"}

def _ratio_or_none(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return numerator / denominator


def _ratio_percent(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return _clamp_percent(numerator / denominator * 100)


def _round_optional(value: float | None) -> float | None:
    return None if value is None else round(value, 2)


def _lt(value: float | None, threshold: float) -> bool:
    return value is not None and value < threshold


def _gte(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _float_or_zero(value: Any) -> float:
    number = _number_or_none(value)
    return float(number) if isinstance(number, (int, float)) else 0.0


def _pool_account_status_summary(accounts: list[dict[str, Any]]) -> dict[str, int]:
    normal_accounts = 0
    active_normal_accounts = 0
    five_hour_rate_limited_accounts = 0
    seven_day_rate_limited_accounts = 0
    abnormal_accounts = 0
    excluded_bug_team_accounts = 0

    for account in accounts:
        if is_bug_team_account(account):
            excluded_bug_team_accounts += 1
            continue
        if _is_abnormal_account(account):
            abnormal_accounts += 1
            continue
        normal_accounts += 1
        is_seven_day_rate_limited = _is_7d_exhausted(account)
        is_five_hour_rate_limited = not is_seven_day_rate_limited and _is_five_hour_rate_limited(account)
        if is_seven_day_rate_limited:
            seven_day_rate_limited_accounts += 1
        elif is_five_hour_rate_limited:
            five_hour_rate_limited_accounts += 1
        status = str(account.get("status") or "").lower()
        if status == "active" and account.get("schedulable") is not False and not is_seven_day_rate_limited and not is_five_hour_rate_limited:
            active_normal_accounts += 1

    return {
        "pool_normal_accounts": normal_accounts,
        "pool_active_normal_accounts": active_normal_accounts,
        "pool_five_hour_rate_limited_accounts": five_hour_rate_limited_accounts,
        "pool_seven_day_rate_limited_accounts": seven_day_rate_limited_accounts,
        "pool_abnormal_accounts": abnormal_accounts,
        "pool_excluded_bug_team_accounts": excluded_bug_team_accounts,
    }


def _is_abnormal_account(account: dict[str, Any]) -> bool:
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    error_values = [
        account.get("error_message"),
        extra.get("error_message"),
        extra.get("last_error"),
    ]
    error_text = " ".join(str(value).lower() for value in error_values if value not in (None, ""))
    authentication_text = " ".join(
        str(value).lower()
        for value in [
            *error_values,
            account.get("temp_unschedulable_reason"),
            account.get("credentials_status"),
            extra.get("credentials_status"),
        ]
        if value not in (None, "")
    )
    authentication_markers = (
        "401",
        "unauthorized",
        "authentication failed",
        "token revoked",
        "token_invalidated",
        "token invalidated",
        "invalid oauth",
        "invalid token",
        "oauth token",
        "凭证失效",
        "认证失败",
    )
    if any(marker in authentication_text for marker in authentication_markers):
        return True
    status = str(account.get("status") or "").lower()
    if status in {"error", "disabled", "paused", "banned", "invalid", "failed"} and not _is_temporary_rate_limit(account):
        return True
    explicit_error_markers = (
        "403",
        "forbidden",
        "account banned",
        "account disabled",
        "account deactivated",
        "account suspended",
        "账号封禁",
        "账号停用",
    )
    return any(marker in error_text for marker in explicit_error_markers)


def _is_five_hour_rate_limited(account: dict[str, Any]) -> bool:
    used_5h = _usage_number(account, "codex_5h_used_percent")
    if isinstance(used_5h, (int, float)) and used_5h >= 100:
        return True
    return _is_temporary_rate_limit(account) and not _is_7d_exhausted(account)


def _concurrency_capacity_summary(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    actual_in_use = 0.0
    actual_available = 0.0
    safe_available = 0.0
    total_capacity = 0.0
    eligible_accounts = 0
    available_accounts = 0
    safe_accounts = 0
    near_limit_accounts = 0
    five_hour_limited_accounts = 0
    short_seven_day_limited_accounts = 0
    other_unavailable_accounts = 0
    long_seven_day_limited_accounts = 0

    for account in accounts:
        maximum = _number_or_none(account.get("concurrency"))
        if not isinstance(maximum, (int, float)) or maximum <= 0:
            continue
        maximum = float(maximum)
        current = _number_or_none(account.get("current_concurrency"))
        current = max(0.0, min(maximum, float(current))) if isinstance(current, (int, float)) else 0.0

        if _is_long_seven_day_concurrency_limit(account):
            long_seven_day_limited_accounts += 1
            continue

        eligible_accounts += 1
        total_capacity += maximum
        actual_in_use += current

        unavailable_kind = _current_concurrency_unavailable_kind(account)
        if unavailable_kind:
            if unavailable_kind == "five_hour":
                five_hour_limited_accounts += 1
            elif unavailable_kind == "short_seven_day":
                short_seven_day_limited_accounts += 1
            else:
                other_unavailable_accounts += 1
            continue

        available_accounts += 1
        remaining = max(0.0, maximum - current)
        actual_available += remaining
        if _is_safe_concurrency_account(account):
            safe_accounts += 1
            safe_available += remaining
        else:
            near_limit_accounts += 1

    temporarily_unavailable = max(0.0, total_capacity - actual_in_use - actual_available)
    temporarily_unavailable_accounts = five_hour_limited_accounts + short_seven_day_limited_accounts + other_unavailable_accounts
    near_limit_available = max(0.0, actual_available - safe_available)
    used_percent = actual_in_use / total_capacity * 100 if total_capacity > 0 else 0.0
    available_percent = actual_available / total_capacity * 100 if total_capacity > 0 else 0.0
    return {
        "concurrency_actual_in_use": _concurrency_number(actual_in_use),
        "concurrency_actual_available": _concurrency_number(actual_available),
        "concurrency_safe_available": _concurrency_number(safe_available),
        "concurrency_near_limit_available": _concurrency_number(near_limit_available),
        "concurrency_total_capacity": _concurrency_number(total_capacity),
        "concurrency_temporarily_unavailable": _concurrency_number(temporarily_unavailable),
        "concurrency_temporarily_unavailable_accounts": temporarily_unavailable_accounts,
        "concurrency_used_percent": round(used_percent, 2),
        "concurrency_available_percent": round(available_percent, 2),
        "concurrency_eligible_accounts": eligible_accounts,
        "concurrency_available_accounts": available_accounts,
        "concurrency_safe_accounts": safe_accounts,
        "concurrency_near_limit_accounts": near_limit_accounts,
        "concurrency_five_hour_limited_accounts": five_hour_limited_accounts,
        "concurrency_short_seven_day_limited_accounts": short_seven_day_limited_accounts,
        "concurrency_other_unavailable_accounts": other_unavailable_accounts,
        "concurrency_long_seven_day_limited_accounts": long_seven_day_limited_accounts,
    }


def _concurrency_number(value: float) -> int | float:
    rounded = round(value, 2)
    return int(rounded) if rounded.is_integer() else rounded


def _current_concurrency_unavailable_kind(account: dict[str, Any]) -> str | None:
    used_5h = _usage_number(account, "codex_5h_used_percent")
    used_7d = _usage_number(account, "codex_7d_used_percent")
    if isinstance(used_7d, (int, float)) and used_7d >= 100:
        return "short_seven_day"
    if isinstance(used_5h, (int, float)) and used_5h >= 100:
        return "five_hour"
    if _is_temporary_rate_limit(account):
        return "five_hour"
    status = str(account.get("status") or "").lower()
    if status != "active" or account.get("schedulable") is False:
        return "other"
    return None


def _is_safe_concurrency_account(account: dict[str, Any]) -> bool:
    used_5h = _usage_number(account, "codex_5h_used_percent")
    used_7d = _usage_number(account, "codex_7d_used_percent")
    if not isinstance(used_5h, (int, float)) or not isinstance(used_7d, (int, float)):
        return False
    return used_5h < CONCURRENCY_SAFE_FIVE_HOUR_USAGE_PERCENT and used_7d < CONCURRENCY_SAFE_SEVEN_DAY_USAGE_PERCENT


def _is_long_seven_day_concurrency_limit(account: dict[str, Any]) -> bool:
    used_7d = _usage_number(account, "codex_7d_used_percent")
    reset_after = _usage_number(account, "codex_7d_reset_after_seconds")
    if not isinstance(reset_after, (int, float)):
        extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
        reset_at = _parse_datetime(_first_present(account, extra, "codex_7d_reset_at", "7d_reset_at"))
        reset_after = max(0.0, (reset_at - now_utc()).total_seconds()) if reset_at is not None else None
    has_long_reset = isinstance(reset_after, (int, float)) and reset_after > 24 * 60 * 60
    return isinstance(used_7d, (int, float)) and used_7d >= 100 and has_long_reset


def _is_7d_exhausted(account: dict[str, Any]) -> bool:
    used_7d = _usage_number(account, "codex_7d_used_percent")
    return isinstance(used_7d, (int, float)) and used_7d >= 100


def _is_capacity_account(account: dict[str, Any]) -> bool:
    if is_bug_team_account(account) or _is_abnormal_account(account):
        return False
    status = str(account.get("status") or "").lower()
    if _is_7d_exhausted(account) or _is_five_hour_rate_limited(account):
        return True
    return status == "active" and account.get("schedulable") is not False


def _is_temporary_rate_limit(account: dict[str, Any]) -> bool:
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    rate_limit_reset_at = _parse_datetime(account.get("rate_limit_reset_at") or extra.get("rate_limit_reset_at"))
    temp_unschedulable_until = _parse_datetime(account.get("temp_unschedulable_until") or extra.get("temp_unschedulable_until"))
    has_active_until = any(
        value is not None and value > now_utc()
        for value in (rate_limit_reset_at, temp_unschedulable_until)
    )
    values = [
        account.get("status"),
        account.get("error_message"),
        account.get("temp_unschedulable_reason"),
        extra.get("error_message"),
        extra.get("last_error"),
    ]
    combined = " ".join(str(value).lower() for value in values if value is not None)
    return has_active_until or "429" in combined or "529" in combined


def _usage_number(account: dict[str, Any], key: str) -> int | float | None:
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    return _number_or_none(account.get(key) if account.get(key) is not None else extra.get(key))


def _usage_float(account: dict[str, Any], key: str) -> float:
    value = _usage_number(account, key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def _average_percent(values: Any) -> int:
    numeric = [value for value in values if isinstance(value, (int, float))]
    if not numeric:
        return 0
    return _clamp_percent(round(sum(numeric) / len(numeric)))


def _clamp_percent(value: int | float) -> int:
    return max(0, min(100, round(value)))


def _extract_group_ids(account: dict[str, Any]) -> list[int]:
    ids: set[int] = set()
    group_ids = account.get("group_ids")
    if isinstance(group_ids, list):
        ids.update(item for item in group_ids if isinstance(item, int))
    groups = account.get("groups")
    if isinstance(groups, list):
        ids.update(group.get("id") for group in groups if isinstance(group, dict) and isinstance(group.get("id"), int))
    account_groups = account.get("account_groups")
    if isinstance(account_groups, list):
        ids.update(item.get("group_id") for item in account_groups if isinstance(item, dict) and isinstance(item.get("group_id"), int))
    return sorted(ids)


def _int_group_id(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _account_snapshot_with_cache_sync(doc: dict[str, Any]) -> dict[str, Any]:
    account = _normalize_account_snapshot(doc.get("account", {}))
    _copy_cached_remote_test(account, doc)
    return account


async def _attach_local_account_metadata(db: AsyncIOMotorDatabase, accounts: list[dict[str, Any]], *, site_id: str | None = None) -> None:
    email_by_account: dict[int, str] = {}
    remote_id_by_account: dict[int, str] = {}
    emails: set[str] = set()
    remote_ids: set[Any] = set()
    for index, account in enumerate(accounts):
        email = _account_email_key(account)
        if email:
            email_by_account[index] = email
            emails.add(email)
        remote_id = account.get("id")
        if remote_id is not None:
            remote_id_key = str(remote_id)
            remote_id_by_account[index] = remote_id_key
            remote_ids.add(remote_id)
            remote_ids.add(remote_id_key)
    if not emails and not remote_ids:
        return

    matchers: list[dict[str, Any]] = []
    if remote_ids:
        remote_match: dict[str, Any] = {"metadata.sub2api_account_id": {"$in": list(remote_ids)}}
        if site_id:
            remote_match["metadata.sub2api_site_id"] = site_id
        matchers.append(remote_match)
    if emails:
        email_matchers = [re.compile(f"^{re.escape(email)}$", re.IGNORECASE) for email in emails]
        matchers.extend(
            [
                {"metadata.email": {"$in": email_matchers}},
                {"account_json.credentials.email": {"$in": email_matchers}},
                {"account_json.extra.email": {"$in": email_matchers}},
            ]
        )

    local_by_email: dict[str, dict[str, Any]] = {}
    local_by_remote_id: dict[str, dict[str, Any]] = {}
    cursor = db.accounts.find(
        {
            "metadata.deleted_at": {"$exists": False},
            "$or": matchers,
        },
        {
            "_id": 1,
            "metadata.email": 1,
            "metadata.sub2api_site_id": 1,
            "metadata.sub2api_account_id": 1,
            "account_json.credentials.email": 1,
            "account_json.extra.email": 1,
            "metadata.uploaded_by_user_id": 1,
            "metadata.uploader_name": 1,
            "metadata.email_session": 1,
            "metadata.2FA": 1,
            "metadata.phone_number": 1,
            "metadata.phone_bound": 1,
            "metadata.last_operation_at": 1,
            "metadata.last_operation_by": 1,
            "metadata.last_operation_by_name": 1,
        },
    )
    async for local in cursor:
        metadata = local.get("metadata") if isinstance(local.get("metadata"), dict) else {}
        account_json = local.get("account_json") if isinstance(local.get("account_json"), dict) else {}
        credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else {}
        extra = account_json.get("extra") if isinstance(account_json.get("extra"), dict) else {}
        email = _normalize_email(metadata.get("email") or credentials.get("email") or extra.get("email"))
        if email and email not in local_by_email:
            local_by_email[email] = local
        remote_id = metadata.get("sub2api_account_id")
        remote_site_id = str(metadata.get("sub2api_site_id") or "")
        if remote_id is not None and (not site_id or remote_site_id == site_id):
            local_by_remote_id.setdefault(str(remote_id), local)

    uploader_ids = {
        metadata.get("uploaded_by_user_id")
        for local in [*local_by_remote_id.values(), *local_by_email.values()]
        if isinstance((metadata := local.get("metadata") if isinstance(local.get("metadata"), dict) else {}), dict)
        and metadata.get("uploaded_by_user_id")
        and not metadata.get("uploader_name")
    }
    users_by_id: dict[str, dict[str, Any]] = {}
    if uploader_ids:
        async for user in db.users.find({"_id": {"$in": list(uploader_ids)}}, {"name": 1, "email": 1}):
            users_by_id[str(user.get("_id"))] = user

    for index, account in enumerate(accounts):
        local = local_by_remote_id.get(remote_id_by_account.get(index, "")) or local_by_email.get(email_by_account.get(index, ""))
        if not local:
            continue
        metadata = local.get("metadata") if isinstance(local.get("metadata"), dict) else {}
        uploader_id = metadata.get("uploaded_by_user_id")
        uploader_user = users_by_id.get(str(uploader_id)) if uploader_id else None
        accounts[index]["local_account_id"] = str(local.get("_id"))
        accounts[index]["uploaded_by_user_id"] = uploader_id
        accounts[index]["uploader_name"] = metadata.get("uploader_name") or (uploader_user or {}).get("name") or (uploader_user or {}).get("email") or uploader_id
        accounts[index]["local_email_session"] = metadata.get("email_session")
        accounts[index]["local_two_fa"] = metadata.get("2FA")
        accounts[index]["local_phone_number"] = metadata.get("phone_number")
        accounts[index]["local_phone_bound"] = metadata.get("phone_bound")
        accounts[index]["last_operation_at"] = metadata.get("last_operation_at")
        accounts[index]["last_operation_by"] = metadata.get("last_operation_by")
        accounts[index]["last_operation_by_name"] = metadata.get("last_operation_by_name")


def _account_email_key(account: dict[str, Any]) -> str:
    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    return _normalize_email(account.get("email") or credentials.get("email") or extra.get("email") or account.get("name"))


def _capacity_email_key(account: dict[str, Any]) -> str:
    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    return _normalize_email(account.get("email") or credentials.get("email") or extra.get("email"))


def _local_capacity_email_key(account: dict[str, Any]) -> str:
    metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
    account_json = account.get("account_json") if isinstance(account.get("account_json"), dict) else {}
    credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else {}
    extra = account_json.get("extra") if isinstance(account_json.get("extra"), dict) else {}
    return _normalize_email(metadata.get("email") or credentials.get("email") or extra.get("email"))


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


def _normalize_account_snapshot(account: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(account)
    credentials = dict(normalized.get("credentials") if isinstance(normalized.get("credentials"), dict) else {})
    extra = dict(normalized.get("extra") if isinstance(normalized.get("extra"), dict) else {})

    normalized["group_ids"] = _extract_group_ids(normalized)
    for field in (
        "email",
        "plan_type",
        "privacy_mode",
        "organization_id",
        "chatgpt_account_id",
        "chatgpt_user_id",
    ):
        value = _first_present(normalized, credentials, extra, field)
        if value is not None:
            normalized[field] = value

    plan_type = str(_first_present(normalized, credentials, extra, "plan_type") or "").strip()
    if plan_type:
        normalized["plan_type"] = plan_type
    else:
        normalized["plan_type"] = "k12"
        normalized["codex_plan_type_source"] = "fallback_k12"
        extra["plan_type"] = "k12"
        extra["codex_plan_type_source"] = "fallback_k12"

    credential_expires_at = _first_present(credentials, extra, "expires_at", "credential_expires_at")
    if credential_expires_at is not None:
        normalized["credential_expires_at"] = credential_expires_at

    subscription_expires_at = _first_present(
        normalized,
        credentials,
        extra,
        "subscription_expires_at",
        "chatgpt_subscription_active_until",
        "subscription_active_until",
    )
    if subscription_expires_at is not None:
        normalized["subscription_expires_at"] = subscription_expires_at

    credentials_status = normalized.get("credentials_status")
    if isinstance(credentials_status, dict):
        normalized["credentials_status"] = dict(credentials_status)

    normalized["status"] = _first_present(
        normalized,
        extra,
        "status",
        "account_status",
        "state",
        "sub2api_status",
    )
    normalized["schedulable"] = _bool_or_none(
        _first_present(
            normalized,
            extra,
            "schedulable",
            "is_schedulable",
            "sub2api_schedulable",
        )
    )
    error_message = _first_present(
        normalized,
        extra,
        "error_message",
        "last_error",
        "error",
    )
    if error_message is None and str(normalized.get("status") or "").lower() not in {"active", "ok", "healthy"}:
        error_message = _first_present(normalized, extra, "message")
    normalized["error_message"] = error_message
    normalized["current_concurrency"] = _number_or_none(_first_present(normalized, extra, "current_concurrency", "used_concurrency"))
    normalized["concurrency"] = _number_or_none(_first_present(normalized, extra, "concurrency", "max_concurrency"))
    normalized["load_factor"] = _number_or_none(_first_present(normalized, extra, "load_factor"))
    normalized["priority"] = _number_or_none(_first_present(normalized, extra, "priority"))
    normalized["rate_multiplier"] = _number_or_none(_first_present(normalized, extra, "rate_multiplier"))
    auto_pause_on_expired = _bool_or_none(_first_present(normalized, extra, "auto_pause_on_expired"))
    if auto_pause_on_expired is not None:
        normalized["auto_pause_on_expired"] = auto_pause_on_expired

    for field in (
        "notes",
        "created_at",
        "updated_at",
        "last_used_at",
        "rate_limited_at",
        "rate_limit_reset_at",
        "overload_until",
        "temp_unschedulable_until",
        "temp_unschedulable_reason",
        "session_window_start",
        "session_window_end",
        "session_window_status",
        "expires_at",
        "proxy_id",
    ):
        value = _first_present(normalized, extra, field)
        if value is not None:
            normalized[field] = value

    usage_values = {
        "codex_5h_used_percent": _usage_value(normalized, extra, "codex_5h_used_percent", "5h", "used_percent"),
        "codex_7d_used_percent": _usage_value(normalized, extra, "codex_7d_used_percent", "7d", "used_percent"),
        "codex_5h_reset_after_seconds": _usage_value(normalized, extra, "codex_5h_reset_after_seconds", "5h", "reset_after_seconds"),
        "codex_7d_reset_after_seconds": _usage_value(normalized, extra, "codex_7d_reset_after_seconds", "7d", "reset_after_seconds"),
        "codex_5h_request_count": _usage_value(normalized, extra, "codex_5h_request_count", "5h", "request_count"),
        "codex_7d_request_count": _usage_value(normalized, extra, "codex_7d_request_count", "7d", "request_count"),
        "codex_5h_token_count": _usage_value(normalized, extra, "codex_5h_token_count", "5h", "token_count"),
        "codex_7d_token_count": _usage_value(normalized, extra, "codex_7d_token_count", "7d", "token_count"),
        "codex_5h_actual_cost": _usage_value(normalized, extra, "codex_5h_actual_cost", "5h", "actual_cost"),
        "codex_7d_actual_cost": _usage_value(normalized, extra, "codex_7d_actual_cost", "7d", "actual_cost"),
        "codex_5h_total_cost": _usage_value(normalized, extra, "codex_5h_total_cost", "5h", "total_cost"),
        "codex_7d_total_cost": _usage_value(normalized, extra, "codex_7d_total_cost", "7d", "total_cost"),
        "codex_total_request_count": _total_usage_value(normalized, extra, "codex_total_request_count", "request_count"),
        "codex_total_token_count": _total_usage_value(normalized, extra, "codex_total_token_count", "token_count"),
        "codex_total_actual_cost": _total_usage_value(normalized, extra, "codex_total_actual_cost", "actual_cost"),
        "codex_total_cost": _total_usage_value(normalized, extra, "codex_total_cost", "total_cost"),
        "codex_primary_used_percent": _first_present(normalized, extra, "codex_primary_used_percent"),
        "codex_primary_reset_after_seconds": _first_present(normalized, extra, "codex_primary_reset_after_seconds"),
        "codex_primary_window_minutes": _first_present(normalized, extra, "codex_primary_window_minutes"),
        "codex_secondary_used_percent": _first_present(normalized, extra, "codex_secondary_used_percent"),
        "codex_secondary_reset_after_seconds": _first_present(normalized, extra, "codex_secondary_reset_after_seconds"),
        "codex_secondary_window_minutes": _first_present(normalized, extra, "codex_secondary_window_minutes"),
        "codex_primary_over_secondary_percent": _first_present(normalized, extra, "codex_primary_over_secondary_percent"),
        "codex_5h_reset_at": _first_present(normalized, extra, "codex_5h_reset_at", "5h_reset_at"),
        "codex_7d_reset_at": _first_present(normalized, extra, "codex_7d_reset_at", "7d_reset_at"),
        "codex_5h_window_minutes": _first_present(normalized, extra, "codex_5h_window_minutes"),
        "codex_7d_window_minutes": _first_present(normalized, extra, "codex_7d_window_minutes"),
        "codex_usage_updated_at": _first_present(normalized, extra, "codex_usage_updated_at", "usage_updated_at"),
        "codex_usage_synced_at": _first_present(normalized, extra, "codex_usage_synced_at", "usage_synced_at"),
    }
    for key, value in usage_values.items():
        if value is not None:
            normalized[key] = value
            extra[key] = value

    _refresh_window_from_reset_at(normalized, extra, "5h")
    _refresh_window_from_reset_at(normalized, extra, "7d")
    _clear_expired_transient_limits(normalized, extra)

    normalized["extra"] = extra
    return normalized


def _clear_expired_transient_limits(account: dict[str, Any], extra: dict[str, Any]) -> None:
    rate_limit_reset_at = _parse_datetime(account.get("rate_limit_reset_at") or extra.get("rate_limit_reset_at"))
    temp_unschedulable_until = _parse_datetime(account.get("temp_unschedulable_until") or extra.get("temp_unschedulable_until"))
    now = now_utc()

    if rate_limit_reset_at is not None and rate_limit_reset_at <= now:
        for key in ("rate_limited_at", "rate_limit_reset_at"):
            account.pop(key, None)
            extra.pop(key, None)

    if temp_unschedulable_until is not None and temp_unschedulable_until <= now:
        for key in ("temp_unschedulable_until", "temp_unschedulable_reason"):
            account.pop(key, None)
            extra.pop(key, None)


def _first_present(*items: Any) -> Any:
    containers = [item for item in items if isinstance(item, dict)]
    keys = [item for item in items if isinstance(item, str)]
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value is not None and value != "":
                return value
    return None


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return None


def _usage_value(account: dict[str, Any], extra: dict[str, Any], canonical_key: str, window: str, metric: str) -> Any:
    direct = _first_present(account, extra, canonical_key)
    if direct is not None:
        return direct

    compact_key = f"{window}_{metric}"
    codex_compact_key = f"codex_{compact_key}"
    for container in (account, extra):
        for key in (compact_key, codex_compact_key):
            value = container.get(key)
            if value is not None:
                return value

    for container in (account, extra):
        usage = container.get("usage") or container.get("usage_windows") or container.get("windows")
        if isinstance(usage, dict):
            window_data = usage.get(window) or usage.get(window.lower()) or usage.get(window.upper())
            if isinstance(window_data, dict):
                for key in (metric, metric.replace("_seconds", "")):
                    value = window_data.get(key)
                    if value is not None:
                        return value
    return None


def _total_usage_value(account: dict[str, Any], extra: dict[str, Any], canonical_key: str, metric: str) -> Any:
    direct = _first_present(
        account,
        extra,
        canonical_key,
        f"total_{metric}",
        f"{metric}_total",
        f"all_time_{metric}",
        f"lifetime_{metric}",
    )
    if direct is not None:
        return direct

    for container in (account, extra):
        usage = container.get("usage") or container.get("usage_summary") or container.get("usage_totals")
        if isinstance(usage, dict):
            for key in ("total", "all_time", "lifetime"):
                usage_data = usage.get(key)
                if isinstance(usage_data, dict):
                    value = usage_data.get(metric)
                    if value is not None:
                        return value
            value = usage.get(metric)
            if value is not None:
                return value
    return None


def _refresh_window_from_reset_at(account: dict[str, Any], extra: dict[str, Any], window: str) -> None:
    reset_at_key = f"codex_{window}_reset_at"
    reset_after_key = f"codex_{window}_reset_after_seconds"
    used_key = f"codex_{window}_used_percent"
    reset_at = _parse_datetime(account.get(reset_at_key) or extra.get(reset_at_key))
    if reset_at is None:
        return
    remaining_seconds = max(0, int((reset_at - now_utc()).total_seconds()))
    account[reset_after_key] = remaining_seconds
    extra[reset_after_key] = remaining_seconds
    if remaining_seconds == 0:
        account[used_key] = 0
        extra[used_key] = 0


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_due(last_refreshed_at: Any, interval_minutes: int) -> bool:
    if not last_refreshed_at:
        return True
    if isinstance(last_refreshed_at, str):
        try:
            last_refreshed_at = datetime.fromisoformat(last_refreshed_at)
        except ValueError:
            return True
    if isinstance(last_refreshed_at, datetime) and last_refreshed_at.tzinfo is None:
        last_refreshed_at = last_refreshed_at.replace(tzinfo=UTC)
    return now_utc() - last_refreshed_at >= timedelta(minutes=interval_minutes)
