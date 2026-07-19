from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.system.sql_dsn import redact_sql_error
from app.modules.sub2api.client import Sub2ApiClient
from app.modules.sub2api.dashboard_postgres_repository import (
    fetch_group_dashboard_snapshot as fetch_postgres_group_dashboard_snapshot,
    fetch_model_statistics as fetch_postgres_model_statistics,
    fetch_site_dashboard_snapshot as fetch_postgres_dashboard_snapshot,
)
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
    client: Sub2ApiClient | None = None,
    force: bool = False,
    group_ids: list[int] | None = None,
    sql_dsn: str | None = None,
) -> dict[str, Any]:
    del client
    sql_dsn = str(sql_dsn or "").strip()
    if not sql_dsn:
        raise ValueError("Sub2API SQL_DSN is required for dashboard reads")
    if not force:
        meta = await db.sub2api_dashboard_meta.find_one({"_id": site_id})
        refreshed_at = meta.get("refreshed_at") if meta else None
        if not dashboard_refresh_due(refreshed_at):
            return serialize_doc(
                {
                    "ok": True,
                    "site_id": site_id,
                    "status": "skipped",
                    "message": "dashboard database validation is fresh",
                    "refreshed_at": refreshed_at,
                    "next_refresh_at": next_refresh_at(refreshed_at),
                    "site_trend_source": "postgresql",
                }
            )

    async def fetch_range(group_id: int | None, range_config: dict[str, Any]) -> dict[str, Any]:
        params = range_config["params"]
        try:
            if group_id is None:
                snapshot, models = await asyncio.gather(
                    fetch_postgres_dashboard_snapshot(
                        sql_dsn,
                        start_date=str(params["start_date"]),
                        end_date=str(params["end_date"]),
                        granularity=str(params["granularity"]),
                    ),
                    fetch_postgres_model_statistics(
                        sql_dsn,
                        start_date=str(params["start_date"]),
                        end_date=str(params["end_date"]),
                    ),
                )
            else:
                snapshot = await fetch_postgres_group_dashboard_snapshot(
                    sql_dsn,
                    group_id=group_id,
                    start_date=str(params["start_date"]),
                    end_date=str(params["end_date"]),
                    granularity=str(params["granularity"]),
                )
                models = []
            return {
                "ok": True,
                "site_id": site_id,
                "group_id": group_id,
                "range_type": range_config["range_type"],
                "granularity": params["granularity"],
                "trend_points": len(snapshot.get("trend") or []),
                "models": len(models),
                "generated_at": snapshot.get("generated_at"),
                "data_source": "postgresql",
            }
        except Exception as exc:  # noqa: BLE001 - report individual group/range failures.
            reason = redact_sql_error(exc, sql_dsn, "postgresql")
            logger.warning(
                "sub2api_dashboard_database_read_failed site_id=%s group_id=%s range_type=%s error=%s",
                site_id,
                group_id,
                range_config["range_type"],
                reason,
            )
            return {
                "ok": False,
                "site_id": site_id,
                "group_id": group_id,
                "range_type": range_config["range_type"],
                "data_source": "postgresql",
                "message": reason,
            }

    ranges = dashboard_snapshot_ranges()
    normalized_group_ids = sorted({int(group_id) for group_id in group_ids or []})
    results = await asyncio.gather(
        *(
            fetch_range(group_id, range_config)
            for group_id in [None, *normalized_group_ids]
            for range_config in ranges
        )
    )
    failed_ranges = sum(1 for item in results if item.get("ok") is not True)
    completed_at = now_utc()
    summary = {
        "ok": failed_ranges == 0,
        "site_id": site_id,
        "status": "succeeded" if failed_ranges == 0 else "partial",
        "ranges": results,
        "trend_points": sum(int(item.get("trend_points") or 0) for item in results),
        "models": sum(int(item.get("models") or 0) for item in results),
        "groups": len(normalized_group_ids),
        "site_trend_source": "postgresql",
        "failed_ranges": failed_ranges,
        "refreshed_at": completed_at if failed_ranges == 0 else None,
    }
    await db.sub2api_dashboard_meta.update_one(
        {"_id": site_id},
        {
            "$set": {
                **summary,
                "ranges": [
                    {
                        key: value
                        for key, value in item.items()
                        if key in {"ok", "group_id", "range_type", "trend_points", "models", "generated_at", "data_source", "message"}
                    }
                    for item in results
                ],
                "updated_at": completed_at,
            }
        },
        upsert=True,
    )
    logger.info(
        "sub2api_dashboard_database_validation_finished site_id=%s trend_points=%s models=%s groups=%s failed=%s",
        site_id,
        summary["trend_points"],
        summary["models"],
        summary["groups"],
        failed_ranges,
    )
    return serialize_doc(summary)


async def dashboard_group_refresh_needed(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_ids: list[int],
) -> bool:
    del db, site_id, group_ids
    return False


async def refresh_due_dashboard_snapshots_for_all_sites(
    db: AsyncIOMotorDatabase,
    *,
    force: bool = False,
) -> dict[str, Any]:
    sites = [
        site
        async for site in db.sub2api_sites.find(
            {
                "status": "active",
                "$or": [
                    {"site_type": "sub2api"},
                    {"site_type": {"$exists": False}},
                    {"site_type": None},
                    {"site_type": ""},
                ],
            }
        )
    ]

    async def refresh_one(site: dict[str, Any]) -> dict[str, Any]:
        site_id = str(site.get("_id") or "")
        try:
            group_ids = [
                int(doc["group_id"])
                async for doc in db.sub2api_groups_cache.find({"site_id": site_id}, {"group_id": 1})
                if isinstance(doc.get("group_id"), int)
            ]
            return await refresh_dashboard_snapshots(
                db,
                site_id=site_id,
                force=force,
                group_ids=group_ids,
                sql_dsn=str(site.get("sql_dsn") or "") or None,
            )
        except Exception as exc:  # noqa: BLE001 - one site should not block other sites.
            reason = redact_sql_error(exc, site.get("sql_dsn"), "postgresql")
            logger.warning("sub2api_dashboard_startup_refresh_failed site_id=%s error=%s", site_id, reason)
            return {"ok": False, "site_id": site_id, "message": reason}

    results = await asyncio.gather(*(refresh_one(site) for site in sites))
    return {
        "ok": True,
        "sites": len(sites),
        "refreshed": sum(1 for item in results if item.get("ok") is True and item.get("status") != "skipped"),
        "skipped": sum(1 for item in results if item.get("status") == "skipped"),
        "failed": sum(1 for item in results if item.get("ok") is False),
        "results": results,
    }


def dashboard_refresh_due(refreshed_at: Any) -> bool:
    parsed = parse_remote_datetime(refreshed_at)
    return parsed is None or now_utc() - parsed >= DASHBOARD_REFRESH_INTERVAL


def next_refresh_at(refreshed_at: Any) -> datetime | None:
    parsed = parse_remote_datetime(refreshed_at)
    return parsed + DASHBOARD_REFRESH_INTERVAL if parsed is not None else None


async def get_stored_dashboard_snapshots(db: AsyncIOMotorDatabase, *, site_id: str) -> dict[str, Any]:
    site = await db.sub2api_sites.find_one({"_id": site_id})
    sql_dsn = str((site or {}).get("sql_dsn") or "").strip()
    if not sql_dsn:
        raise ValueError("Sub2API SQL_DSN is required for dashboard reads")
    ranges = dashboard_snapshot_ranges()

    async def fetch_range(range_config: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        params = range_config["params"]
        return await asyncio.gather(
            fetch_postgres_dashboard_snapshot(
                sql_dsn,
                start_date=str(params["start_date"]),
                end_date=str(params["end_date"]),
                granularity=str(params["granularity"]),
            ),
            fetch_postgres_model_statistics(
                sql_dsn,
                start_date=str(params["start_date"]),
                end_date=str(params["end_date"]),
            ),
        )

    fetched = await asyncio.gather(*(fetch_range(range_config) for range_config in ranges))
    meta = await db.sub2api_dashboard_meta.find_one({"_id": site_id})
    snapshots: list[dict[str, Any]] = []
    trends: dict[str, list[dict[str, Any]]] = {}
    models: dict[str, list[dict[str, Any]]] = {}
    for range_config, (snapshot, model_items) in zip(ranges, fetched, strict=True):
        range_type = range_config["range_type"]
        granularity = str(snapshot.get("granularity") or range_config["params"]["granularity"])
        trends[granularity] = [
            _dashboard_trend_document(
                site_id=site_id,
                range_type=range_type,
                granularity=granularity,
                item=item,
            )
            for item in snapshot.get("trend") or []
            if isinstance(item, dict)
        ]
        models[range_type] = [
            {
                **item,
                "site_id": site_id,
                "group_id": None,
                "range_type": range_type,
                "granularity": granularity,
                "data_source": "postgresql",
            }
            for item in model_items
        ]
        snapshots.append(
            {
                "_id": f"{site_id}:all:{range_type}",
                "site_id": site_id,
                "group_id": None,
                "range_type": range_type,
                "granularity": granularity,
                "start_date": snapshot.get("start_date"),
                "end_date": snapshot.get("end_date"),
                "generated_at": snapshot.get("generated_at"),
                "trend_points": len(snapshot.get("trend") or []),
                "models": len(model_items),
                "data_source": "postgresql",
            }
        )
    return serialize_doc(
        {
            "site_id": site_id,
            "meta": meta,
            "snapshots": snapshots,
            "hourly_trend": trends.get("hour", []),
            "daily_trend": trends.get("day", []),
            "recent_models": models.get("recent_hours", []),
            "weekly_models": models.get("last_7d", []),
        }
    )


def _dashboard_trend_document(
    *,
    site_id: str,
    range_type: str,
    granularity: str,
    item: dict[str, Any],
) -> dict[str, Any]:
    bucket = item.get("date")
    return {
        **item,
        "_id": f"{site_id}:all:{granularity}:{bucket}",
        "site_id": site_id,
        "group_id": None,
        "range_type": range_type,
        "granularity": granularity,
        "bucket": bucket,
        "bucket_at": parse_bucket_time(bucket, granularity),
        "data_source": "postgresql",
    }


def dashboard_snapshot_ranges(reference: datetime | None = None) -> list[dict[str, Any]]:
    today = (reference or now_utc()).astimezone(DASHBOARD_LOCAL_TZ).date()
    return [
        {
            "range_type": "recent_hours",
            "params": {
                "start_date": (today - timedelta(days=HOURLY_RANGE_DAYS)).isoformat(),
                "end_date": today.isoformat(),
                "granularity": "hour",
                "timezone": DASHBOARD_TIMEZONE,
            },
        },
        {
            "range_type": "last_7d",
            "params": {
                "start_date": (today - timedelta(days=DAILY_RANGE_DAYS)).isoformat(),
                "end_date": today.isoformat(),
                "granularity": "day",
                "timezone": DASHBOARD_TIMEZONE,
            },
        },
    ]


def parse_bucket_time(value: Any, granularity: str) -> datetime | None:
    if not value:
        return None
    try:
        if granularity == "hour":
            return datetime.strptime(str(value), "%Y-%m-%d %H:%M").replace(tzinfo=DASHBOARD_LOCAL_TZ).astimezone(UTC)
        return datetime.strptime(str(value), "%Y-%m-%d").replace(tzinfo=DASHBOARD_LOCAL_TZ).astimezone(UTC)
    except ValueError:
        return None


def parse_remote_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif value:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
