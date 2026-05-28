from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReplaceOne

from app.services.sub2api import Sub2ApiClient
from app.utils import now_utc, serialize_doc


logger = logging.getLogger("app.sub2api_dashboard")

DASHBOARD_TIMEZONE = "Asia/Shanghai"
DASHBOARD_LOCAL_TZ = timezone(timedelta(hours=8))
HOURLY_RANGE_DAYS = 6
DAILY_RANGE_DAYS = 6
DASHBOARD_REFRESH_INTERVAL = timedelta(minutes=30)


async def refresh_dashboard_snapshots(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    client: Sub2ApiClient,
    force: bool = False,
) -> dict[str, Any]:
    if not force:
        meta = await db.sub2api_dashboard_meta.find_one({"_id": site_id})
        refreshed_at = meta.get("refreshed_at") if meta else None
        if not dashboard_refresh_due(refreshed_at):
            return serialize_doc(
                {
                    "ok": True,
                    "site_id": site_id,
                    "status": "skipped",
                    "message": "dashboard usage snapshot is fresh",
                    "refreshed_at": refreshed_at,
                    "next_refresh_at": next_refresh_at(refreshed_at),
                }
            )
    ranges = dashboard_snapshot_ranges()
    results = []
    for range_config in ranges:
        snapshot = await client.get_dashboard_snapshot(**range_config["params"])
        result = await store_dashboard_snapshot(
            db,
            site_id=site_id,
            range_type=range_config["range_type"],
            snapshot=snapshot,
        )
        results.append(result)
    summary = {
        "ok": True,
        "site_id": site_id,
        "ranges": results,
        "trend_points": sum(item.get("trend_points", 0) for item in results),
        "models": sum(item.get("models", 0) for item in results),
        "refreshed_at": now_utc(),
    }
    await db.sub2api_dashboard_meta.update_one(
        {"_id": site_id},
        {"$set": {**summary, "updated_at": summary["refreshed_at"]}},
        upsert=True,
    )
    logger.info(
        "sub2api_dashboard_refresh_finished site_id=%s trend_points=%s models=%s",
        site_id,
        summary["trend_points"],
        summary["models"],
    )
    return serialize_doc(summary)


async def refresh_due_dashboard_snapshots_for_all_sites(db: AsyncIOMotorDatabase, *, force: bool = False) -> dict[str, Any]:
    sites = [
        site
        async for site in db.sub2api_sites.find({"status": "active"})
    ]
    results = []
    for site in sites:
        site_id = str(site.get("_id"))
        client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
        try:
            results.append(await refresh_dashboard_snapshots(db, site_id=site_id, client=client, force=force))
        except Exception as exc:  # noqa: BLE001 - one site should not block other sites.
            logger.warning("sub2api_dashboard_startup_refresh_failed site_id=%s error=%s", site_id, exc)
            results.append({"ok": False, "site_id": site_id, "message": str(exc)})
    return {
        "ok": True,
        "sites": len(sites),
        "refreshed": sum(1 for item in results if item.get("ok") is True and item.get("status") != "skipped"),
        "skipped": sum(1 for item in results if item.get("status") == "skipped"),
        "failed": sum(1 for item in results if item.get("ok") is False),
        "results": results,
    }


def dashboard_refresh_due(refreshed_at: Any) -> bool:
    if not refreshed_at:
        return True
    if isinstance(refreshed_at, datetime):
        parsed = refreshed_at
    else:
        try:
            parsed = datetime.fromisoformat(str(refreshed_at).replace("Z", "+00:00"))
        except ValueError:
            return True
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return now_utc() - parsed.astimezone(UTC) >= DASHBOARD_REFRESH_INTERVAL


def next_refresh_at(refreshed_at: Any) -> datetime | None:
    if not refreshed_at:
        return None
    if isinstance(refreshed_at, datetime):
        parsed = refreshed_at
    else:
        try:
            parsed = datetime.fromisoformat(str(refreshed_at).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC) + DASHBOARD_REFRESH_INTERVAL


async def get_stored_dashboard_snapshots(db: AsyncIOMotorDatabase, *, site_id: str) -> dict[str, Any]:
    hourly_cursor = db.sub2api_dashboard_trends.find({"site_id": site_id, "granularity": "hour"}).sort("bucket_at", -1).limit(24 * 8)
    daily_cursor = db.sub2api_dashboard_trends.find({"site_id": site_id, "granularity": "day"}).sort("bucket_at", -1).limit(30)
    recent_models_cursor = db.sub2api_dashboard_models.find({"site_id": site_id, "range_type": "recent_hours"}).sort("cost", -1)
    weekly_models_cursor = db.sub2api_dashboard_models.find({"site_id": site_id, "range_type": "last_7d"}).sort("cost", -1)
    meta = await db.sub2api_dashboard_meta.find_one({"_id": site_id})
    snapshots = [doc async for doc in db.sub2api_dashboard_snapshots.find({"site_id": site_id}).sort("fetched_at", -1)]
    hourly = [serialize_doc(doc) async for doc in hourly_cursor]
    daily = [serialize_doc(doc) async for doc in daily_cursor]
    return {
        "site_id": site_id,
        "meta": serialize_doc(meta) if meta else None,
        "snapshots": [serialize_doc(doc) for doc in snapshots],
        "hourly_trend": list(reversed(hourly)),
        "daily_trend": list(reversed(daily)),
        "recent_models": [serialize_doc(doc) async for doc in recent_models_cursor],
        "weekly_models": [serialize_doc(doc) async for doc in weekly_models_cursor],
    }


async def store_dashboard_snapshot(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    range_type: str,
    snapshot: dict[str, Any],
) -> dict[str, Any]:
    now = now_utc()
    generated_at = parse_remote_datetime(snapshot.get("generated_at"))
    granularity = str(snapshot.get("granularity") or "")
    start_date = str(snapshot.get("start_date") or "")
    end_date = str(snapshot.get("end_date") or "")
    trend_items = snapshot.get("trend") if isinstance(snapshot.get("trend"), list) else []
    model_items = snapshot.get("models") if isinstance(snapshot.get("models"), list) else []

    trend_ops = [
        ReplaceOne(
            {"_id": f"{site_id}:{granularity}:{item.get('date')}"},
            {
                "_id": f"{site_id}:{granularity}:{item.get('date')}",
                "site_id": site_id,
                "range_type": range_type,
                "granularity": granularity,
                "bucket": item.get("date"),
                "bucket_at": parse_bucket_time(item.get("date"), granularity),
                "start_date": start_date,
                "end_date": end_date,
                "requests": number_value(item.get("requests")),
                "input_tokens": number_value(item.get("input_tokens")),
                "output_tokens": number_value(item.get("output_tokens")),
                "cache_creation_tokens": number_value(item.get("cache_creation_tokens")),
                "cache_read_tokens": number_value(item.get("cache_read_tokens")),
                "total_tokens": number_value(item.get("total_tokens")),
                "cost": float_value(item.get("cost")),
                "actual_cost": float_value(item.get("actual_cost")),
                "raw": item,
                "generated_at": generated_at,
                "fetched_at": now,
            },
            upsert=True,
        )
        for item in trend_items
        if isinstance(item, dict) and item.get("date") is not None
    ]
    model_ops = [
        ReplaceOne(
            {"_id": f"{site_id}:{range_type}:{item.get('model')}"},
            {
                "_id": f"{site_id}:{range_type}:{item.get('model')}",
                "site_id": site_id,
                "range_type": range_type,
                "granularity": granularity,
                "model": item.get("model"),
                "start_date": start_date,
                "end_date": end_date,
                "requests": number_value(item.get("requests")),
                "input_tokens": number_value(item.get("input_tokens")),
                "output_tokens": number_value(item.get("output_tokens")),
                "cache_creation_tokens": number_value(item.get("cache_creation_tokens")),
                "cache_read_tokens": number_value(item.get("cache_read_tokens")),
                "total_tokens": number_value(item.get("total_tokens")),
                "cost": float_value(item.get("cost")),
                "actual_cost": float_value(item.get("actual_cost")),
                "account_cost": float_value(item.get("account_cost")),
                "raw": item,
                "generated_at": generated_at,
                "fetched_at": now,
            },
            upsert=True,
        )
        for item in model_items
        if isinstance(item, dict) and item.get("model") is not None
    ]
    if trend_ops:
        await db.sub2api_dashboard_trends.bulk_write(trend_ops, ordered=False)
    if model_ops:
        await db.sub2api_dashboard_models.bulk_write(model_ops, ordered=False)

    meta = {
        "_id": f"{site_id}:{range_type}",
        "site_id": site_id,
        "range_type": range_type,
        "granularity": granularity,
        "start_date": start_date,
        "end_date": end_date,
        "generated_at": generated_at,
        "trend_points": len(trend_ops),
        "models": len(model_ops),
        "fetched_at": now,
    }
    await db.sub2api_dashboard_snapshots.replace_one({"_id": meta["_id"]}, {**meta, "raw": snapshot}, upsert=True)
    return serialize_doc(meta)


def dashboard_snapshot_ranges(reference: datetime | None = None) -> list[dict[str, Any]]:
    today = (reference or now_utc()).astimezone(DASHBOARD_LOCAL_TZ).date()
    hourly_start = today - timedelta(days=HOURLY_RANGE_DAYS)
    daily_start = today - timedelta(days=DAILY_RANGE_DAYS)
    return [
        {
            "range_type": "recent_hours",
            "params": {
                "start_date": hourly_start.isoformat(),
                "end_date": today.isoformat(),
                "granularity": "hour",
                "timezone": DASHBOARD_TIMEZONE,
            },
        },
        {
            "range_type": "last_7d",
            "params": {
                "start_date": daily_start.isoformat(),
                "end_date": today.isoformat(),
                "granularity": "day",
                "timezone": DASHBOARD_TIMEZONE,
            },
        },
    ]


def parse_bucket_time(value: Any, granularity: str) -> datetime | None:
    if not value:
        return None
    text = str(value)
    try:
        if granularity == "hour":
            return datetime.strptime(text, "%Y-%m-%d %H:%M").replace(tzinfo=DASHBOARD_LOCAL_TZ).astimezone(UTC)
        return datetime.strptime(text, "%Y-%m-%d").replace(tzinfo=DASHBOARD_LOCAL_TZ).astimezone(UTC)
    except ValueError:
        return None


def parse_remote_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def number_value(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def float_value(value: Any) -> float:
    if isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return 0.0
