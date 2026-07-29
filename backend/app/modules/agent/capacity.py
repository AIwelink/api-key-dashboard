from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.api_pools.status_preferences import get_api_pool_status_preferences
from app.modules.sub2api.cache import get_cache_meta, list_cached_groups
from app.utils import serialize_doc

AGENT_POOL_DEFAULTS = {
    "status": "active",
}


async def list_agent_pools(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """Return live sub2api groups as Agent analyzable pools.

    Agent mirrors the API account pool status page by reading cached sub2api
    groups for the pinned/current site. This does not sync api_pools and does
    not refresh sub2api.
    """
    site_id = await _agent_default_site_id(db)
    items = []
    seen: set[str] = set()
    cache_meta = {}
    if site_id:
        groups_response = await list_cached_groups(db, site_id, page=1, page_size=500)
        cache_meta = groups_response.get("cache_meta") if isinstance(groups_response.get("cache_meta"), dict) else {}
        for group in groups_response.get("items", []):
            pool = _pool_from_cached_group(site_id, group)
            if not pool:
                continue
            key = str(pool["id"])
            if key in seen:
                continue
            seen.add(key)
            items.append(pool)
    else:
        cursor = db.sub2api_groups_cache.find({}).sort([("site_id", 1), ("group_id", 1)])
        async for group_doc in cursor:
            pool = _pool_from_group_doc(group_doc)
            if not pool:
                continue
            key = str(pool["id"])
            if key in seen:
                continue
            seen.add(key)
            items.append(pool)
    return {"items": [serialize_doc(item) for item in items], "total": len(items), "site_id": site_id, "cache_meta": cache_meta}


async def read_pool_capacity(db: AsyncIOMotorDatabase, pool_id: str) -> dict[str, Any]:
    pool, group_doc = await _resolve_agent_pool(db, pool_id)
    site_id = str(pool.get("site_id") or "default")
    group_id = _int_or_none(pool.get("active_group_id"))
    if group_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent pool active_group_id is required")

    if group_doc is None:
        group_doc = await db.sub2api_groups_cache.find_one({"site_id": site_id, "group_id": group_id})
    group = group_doc.get("group") if group_doc and isinstance(group_doc.get("group"), dict) else {}
    capacity_summary = {}
    if group_doc:
        raw_summary = group_doc.get("capacity_summary")
        if isinstance(raw_summary, dict):
            capacity_summary = raw_summary
    if not capacity_summary and isinstance(group.get("capacity_summary"), dict):
        capacity_summary = group["capacity_summary"]

    cache_meta = await get_cache_meta(db, site_id)
    reserve_count = await _count_local_reserve_accounts(db, pool=pool, pool_id=pool_id, site_id=site_id, group_id=group_id)
    active_count = _number_or_none(capacity_summary.get("active_available_accounts"))
    if active_count is None:
        active_count = await _count_cached_group_accounts(db, site_id=site_id, group_id=group_id)

    return serialize_doc(
        {
            "pool": pool,
            "site_id": site_id,
            "group_id": group_id,
            "group": group,
            "cache_meta": cache_meta,
            "capacity_summary": capacity_summary,
            "data_source": "sub2api_groups_cache",
            "refresh_behavior": "read_existing_cache_only",
            "active_account_count": int(active_count or 0),
            "reserve_account_count": int(_number_or_none(capacity_summary.get("reserve_available_accounts")) or reserve_count),
            "local_reserve_account_count": reserve_count,
            "total_account_count": int(_number_or_none(capacity_summary.get("total_accounts")) or _number_or_none(group.get("account_count")) or 0),
            "available_accounts": int(_number_or_none(capacity_summary.get("available_accounts")) or 0),
            "available_5h_accounts": int(_number_or_none(capacity_summary.get("available_5h_accounts")) or 0),
            "current_speed_days": _number_or_none(capacity_summary.get("current_speed_days")),
            "recent_24h_cost": _number_or_none(capacity_summary.get("recent_24h_cost")),
            "seven_day_24h_peak_cost": _number_or_none(capacity_summary.get("seven_day_24h_peak_cost")),
            "estimated_recent_24h_consumed_accounts": _number_or_none(
                capacity_summary.get("estimated_recent_24h_consumed_accounts") or capacity_summary.get("estimated_24h_consumed_accounts")
            ),
            "estimated_seven_day_peak_24h_consumed_accounts": _number_or_none(capacity_summary.get("estimated_seven_day_peak_24h_consumed_accounts")),
            "seven_day_peak_speed_days": _number_or_none(capacity_summary.get("seven_day_peak_speed_days")),
            "recent_day_five_hour_peak_speed_days": _number_or_none(capacity_summary.get("recent_day_five_hour_peak_speed_days")),
            "seven_day_five_hour_peak_speed_days": _number_or_none(capacity_summary.get("seven_day_five_hour_peak_speed_days")),
            "recent_day_five_hour_peak_multiple": _number_or_none(capacity_summary.get("recent_day_five_hour_peak_multiple")),
            "seven_day_five_hour_peak_multiple": _number_or_none(capacity_summary.get("seven_day_five_hour_peak_multiple") or capacity_summary.get("five_hour_peak_multiple")),
            "burst_1h_five_hour_multiple": _number_or_none(capacity_summary.get("burst_1h_five_hour_multiple")),
            "active_burst_1h_five_hour_multiple": _number_or_none(capacity_summary.get("active_burst_1h_five_hour_multiple")),
            "burst_1h_observed_cost": _number_or_none(capacity_summary.get("burst_1h_observed_cost")),
            "burst_1h_elapsed_minutes": _number_or_none(capacity_summary.get("burst_1h_elapsed_minutes")),
            "burst_1h_cost": _number_or_none(capacity_summary.get("burst_1h_cost")),
            "burst_1h_five_hour_estimated_cost": _number_or_none(capacity_summary.get("burst_1h_five_hour_estimated_cost")),
            "burst_1h_trend": capacity_summary.get("burst_1h_trend"),
            "burst_1h_trend_label": capacity_summary.get("burst_1h_trend_label"),
            "burst_1h_trend_strength": capacity_summary.get("burst_1h_trend_strength"),
            "burst_1h_trend_strength_label": capacity_summary.get("burst_1h_trend_strength_label"),
            "burst_1h_trend_change_percent": _number_or_none(capacity_summary.get("burst_1h_trend_change_percent")),
            "burst_1h_trend_recent_avg_cost": _number_or_none(capacity_summary.get("burst_1h_trend_recent_avg_cost")),
            "burst_1h_trend_baseline_avg_cost": _number_or_none(capacity_summary.get("burst_1h_trend_baseline_avg_cost")),
            "burst_1h_trend_recent_hours": _number_or_none(capacity_summary.get("burst_1h_trend_recent_hours")),
            "burst_1h_trend_baseline_hours": _number_or_none(capacity_summary.get("burst_1h_trend_baseline_hours")),
            "five_hour_remaining_usd": _number_or_none(
                capacity_summary.get("dynamic_five_hour_remaining_estimated_usd")
                or capacity_summary.get("five_hour_remaining_estimated_usd")
                or capacity_summary.get("five_hour_actual_remaining_usd")
            ),
            "seven_day_remaining_usd": _number_or_none(
                capacity_summary.get("seven_day_remaining_estimated_usd")
                or capacity_summary.get("seven_day_actual_remaining_usd")
                or capacity_summary.get("seven_day_remaining_usd")
            ),
            "health_status": capacity_summary.get("health_status"),
            "health_label": capacity_summary.get("health_label"),
            "cache_fresh": bool(cache_meta.get("last_refreshed_at")),
            "last_refreshed_at": cache_meta.get("last_refreshed_at"),
            "capacity_calculated_at": group_doc.get("capacity_calculated_at") if group_doc else None,
        }
    )


async def _resolve_agent_pool(db: AsyncIOMotorDatabase, pool_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    parsed = _parse_live_pool_id(pool_id)
    if parsed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent pool not found")

    site_id, group_id = parsed
    group_doc = await db.sub2api_groups_cache.find_one({"site_id": site_id, "group_id": group_id})
    if group_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent pool not found")
    pool = _pool_from_group_doc(group_doc)
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent pool not found")
    return pool, group_doc


def _pool_from_group_doc(group_doc: dict[str, Any]) -> dict[str, Any] | None:
    group_id = _int_or_none(group_doc.get("group_id"))
    if group_id is None:
        return None
    site_id = str(group_doc.get("site_id") or "").strip() or "default"
    group = group_doc.get("group") if isinstance(group_doc.get("group"), dict) else {}
    capacity_summary = group_doc.get("capacity_summary") if isinstance(group_doc.get("capacity_summary"), dict) else {}
    return {
        **AGENT_POOL_DEFAULTS,
        "id": _live_pool_id(site_id, group_id),
        "name": str(group.get("name") or f"{site_id} group #{group_id}"),
        "account_type": _infer_account_type(group, capacity_summary),
        "site_id": site_id,
        "active_group_id": group_id,
        "verification_group_id": None,
        "source": "sub2api_groups_cache",
        "remote_status": group.get("status"),
        "remote_account_count": group.get("account_count"),
        "remote_active_account_count": group.get("active_account_count"),
        "remote_rate_limited_account_count": group.get("rate_limited_account_count"),
        "updated_at": group_doc.get("fetched_at"),
    }


def _pool_from_cached_group(site_id: str, group: dict[str, Any]) -> dict[str, Any] | None:
    group_id = _int_or_none(group.get("id"))
    if group_id is None:
        return None
    capacity_summary = group.get("capacity_summary") if isinstance(group.get("capacity_summary"), dict) else {}
    return {
        **AGENT_POOL_DEFAULTS,
        "id": _live_pool_id(site_id, group_id),
        "name": str(group.get("name") or f"{site_id} group #{group_id}"),
        "account_type": _infer_account_type(group, capacity_summary),
        "site_id": site_id,
        "active_group_id": group_id,
        "verification_group_id": None,
        "source": "sub2api_cached_groups_endpoint",
        "remote_status": group.get("status"),
        "remote_account_count": group.get("account_count"),
        "remote_active_account_count": group.get("active_account_count"),
        "remote_rate_limited_account_count": group.get("rate_limited_account_count"),
        "updated_at": group.get("fetched_at"),
    }


def _live_pool_id(site_id: str, group_id: int) -> str:
    return f"sub2api:{site_id}:{group_id}"


def _parse_live_pool_id(pool_id: str) -> tuple[str, int] | None:
    parts = str(pool_id or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "sub2api":
        return None
    group_id = _int_or_none(parts[2])
    if not parts[1] or group_id is None:
        return None
    return parts[1], group_id


def _infer_account_type(group: dict[str, Any], capacity_summary: dict[str, Any]) -> str:
    candidates = [
        capacity_summary.get("account_type"),
        group.get("account_type"),
        group.get("subscription_type"),
        group.get("plan_type"),
        group.get("name"),
    ]
    joined = " ".join(str(item or "").strip().lower() for item in candidates)
    if "pro" in joined:
        return "pro"
    if "team" in joined:
        return "team"
    if "k12" in joined:
        return "k12"
    if "free" in joined:
        return "free"
    if "plus" in joined:
        return "plus"
    return "plus"


async def _agent_default_site_id(db: AsyncIOMotorDatabase) -> str | None:
    preferences = await get_api_pool_status_preferences(db)
    pinned_site_id = str(preferences.get("pinned_site_id") or "").strip()
    if pinned_site_id and await db.sub2api_groups_cache.find_one({"site_id": pinned_site_id}, {"_id": 1}):
        return pinned_site_id

    latest_meta = await db.sub2api_cache_meta.find_one(
        {"last_refreshed_at": {"$exists": True}},
        sort=[("last_refreshed_at", -1), ("finished_at", -1), ("updated_at", -1)],
    )
    if latest_meta and latest_meta.get("site_id"):
        return str(latest_meta["site_id"])
    if latest_meta and latest_meta.get("_id"):
        return str(latest_meta["_id"])

    latest_group = await db.sub2api_groups_cache.find_one({}, sort=[("fetched_at", -1), ("site_id", 1), ("group_id", 1)])
    if latest_group and latest_group.get("site_id"):
        return str(latest_group["site_id"])
    return None


async def _count_cached_group_accounts(db: AsyncIOMotorDatabase, *, site_id: str, group_id: int) -> int:
    return await db.sub2api_accounts_cache.count_documents({"site_id": site_id, "group_ids": group_id})


async def _count_local_reserve_accounts(
    db: AsyncIOMotorDatabase,
    *,
    pool: dict[str, Any],
    pool_id: str,
    site_id: str,
    group_id: int,
) -> int:
    account_type = str(pool.get("account_type") or "").strip().lower()
    query: dict[str, Any] = {
        "metadata.deleted_at": {"$exists": False},
        "metadata.pool_status": "reserve",
        "$or": [
            {"metadata.pool_id": pool_id},
            {"metadata.api_pool_id": pool_id},
            {"metadata.pool_id": str(group_id)},
            {"metadata.sub2api_group_id": group_id},
        ],
    }
    if site_id:
        query["$and"] = [
            {
                "$or": [
                    {"metadata.sub2api_site_id": site_id},
                    {"metadata.sub2api_site_id": {"$exists": False}},
                    {"metadata.sub2api_site_id": ""},
                    {"metadata.sub2api_site_id": None},
                ]
            }
        ]
    if account_type and account_type != "other":
        query["metadata.account_type"] = account_type
    return await db.accounts.count_documents(query)


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
