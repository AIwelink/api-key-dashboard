from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.events.records import event_records_summary, list_event_records
from app.modules.sub2api.account_probe import CONFIRMED_401_RECOVERY_COUNT, list_duplicate_email_alerts
from app.utils import now_utc, serialize_doc


async def read_probe_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    account_type: str | None = None,
) -> dict[str, Any]:
    now = now_utc()
    meta = await db.remote_account_probe_meta.find_one({"_id": site_id}) or {}
    last_probe_at = _coerce_datetime(meta.get("last_probe_at"))
    probe_fresh = bool(last_probe_at and now - last_probe_at <= timedelta(minutes=10))

    one_hour_start = now - timedelta(hours=1)
    day_start = now - timedelta(hours=24)
    seven_day_start = now - timedelta(days=7)
    account_type_filter = _account_type_filter(account_type)
    only_pro = str(account_type or "").lower() == "pro"

    summary_24h = await event_records_summary(
        db,
        site_id=site_id,
        group_id=group_id,
        account_type=account_type_filter,
        range_value="24h",
        only_pro=only_pro,
    )
    summary_7d = await event_records_summary(
        db,
        site_id=site_id,
        group_id=group_id,
        account_type=account_type_filter,
        range_value="7d",
        only_pro=only_pro,
    )
    one_hour_401 = int(summary_24h.get("one_hour_401") or 0)
    detected_401_24h = int(summary_24h.get("detected_401") or 0)
    detected_401_7d = int(summary_7d.get("detected_401") or 0)
    recovered_24h = int(summary_24h.get("recovered_401") or 0)
    detected_401_clusters_24h = await _detected_401_clusters_24h(
        db,
        site_id=site_id,
        group_id=group_id,
        account_type=account_type_filter,
        only_pro=only_pro,
        since=day_start,
    )
    largest_401_cluster_24h = detected_401_clusters_24h[0] if detected_401_clusters_24h else None

    duplicate_alerts = await list_duplicate_email_alerts(db, site_id=site_id, group_id=group_id, include_read=False, limit=50)
    duplicate_email_alert_count = int(duplicate_alerts.get("total") or len(duplicate_alerts.get("items") or []))
    recent_response = await list_event_records(
        db,
        site_id=site_id,
        group_id=group_id,
        account_type=account_type_filter,
        range_value="7d",
        only_pro=only_pro,
        limit=8,
    )
    recent_events = recent_response.get("items") or []
    return {
        "probe_fresh": probe_fresh,
        "last_probe_at": serialize_doc(last_probe_at),
        "probe_status": meta.get("status"),
        "data_source": "event_records",
        "detected_401_1h": one_hour_401,
        "detected_401_24h": detected_401_24h,
        "detected_401_7d": detected_401_7d,
        "pro_401_1h": one_hour_401,
        "pro_401_24h": detected_401_24h,
        "pro_401_7d": detected_401_7d,
        "recovered_24h": recovered_24h,
        "confirmed_401_recoveries_24h": recovered_24h,
        "401_recovery_required_healthy_probes": CONFIRMED_401_RECOVERY_COUNT,
        "official_usage_refreshes_24h": int(summary_24h.get("official_usage_refreshes") or 0),
        "official_usage_refreshes_7d": int(summary_7d.get("official_usage_refreshes") or 0),
        "detected_401_clusters_24h": detected_401_clusters_24h,
        "largest_401_cluster_24h": largest_401_cluster_24h,
        "concentrated_401_burst_24h": _is_concentrated_401_burst(largest_401_cluster_24h, detected_401_24h),
        "duplicate_email_alert_count": duplicate_email_alert_count,
        "median_survival_hours_7d": await _median_survival_hours_before_401(db, site_id=site_id, group_id=group_id, since=seven_day_start),
        "recent_events": recent_events,
        "event_summary_24h": summary_24h,
        "event_summary_7d": summary_7d,
    }


async def _detected_401_clusters_24h(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    account_type: str | None,
    only_pro: bool,
    since: datetime,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "site_id": site_id,
        "event_type": "401_detected",
        "detected_at": {"$gte": since},
        **_group_filter(group_id),
    }
    and_clauses: list[dict[str, Any]] = []
    if account_type:
        and_clauses.append({"$or": [{"plan_type": account_type}, {"details.account_type": account_type}]})
    if only_pro:
        and_clauses.append({"$or": [{"details.is_pro_pool": True}, {"plan_type": "pro"}, {"details.account_type": "pro"}]})
    if and_clauses:
        query["$and"] = and_clauses

    events: list[dict[str, Any]] = []
    cursor = db.remote_account_status_events.find(
        query,
        {"detected_at": 1, "identity_id": 1, "remote_account_id": 1, "email": 1, "normalized_email": 1, "current_group_ids": 1},
    ).sort("detected_at", 1)
    async for event in cursor:
        detected_at = _coerce_datetime(event.get("detected_at"))
        if not detected_at:
            continue
        events.append({**event, "detected_at": detected_at})
    if not events:
        return []

    clusters: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    max_gap = timedelta(minutes=90)
    for event in events:
        if not current:
            current = [event]
            continue
        previous_at = current[-1]["detected_at"]
        if event["detected_at"] - previous_at <= max_gap:
            current.append(event)
        else:
            clusters.append(current)
            current = [event]
    if current:
        clusters.append(current)

    summarized = [_summarize_401_cluster(cluster, total_24h=len(events)) for cluster in clusters]
    summarized.sort(key=lambda item: (int(item.get("count") or 0), item.get("duration_minutes") or 0), reverse=True)
    return serialize_doc(summarized[:5])


def _summarize_401_cluster(cluster: list[dict[str, Any]], *, total_24h: int) -> dict[str, Any]:
    started_at = cluster[0]["detected_at"]
    ended_at = cluster[-1]["detected_at"]
    account_keys = {_event_account_key(item) for item in cluster}
    account_keys.discard("")
    duration_minutes = max(0, int((ended_at - started_at).total_seconds() / 60))
    share = round(len(cluster) / total_24h, 4) if total_24h > 0 else 0
    return {
        "count": len(cluster),
        "distinct_account_count": len(account_keys) or len(cluster),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_minutes": duration_minutes,
        "share_of_24h_401": share,
        "interpretation": _cluster_interpretation(len(cluster), duration_minutes, share),
    }


def _event_account_key(event: dict[str, Any]) -> str:
    for key in ("identity_id", "normalized_email", "email", "remote_account_id"):
        value = event.get(key)
        if value is not None and str(value).strip():
            return str(value).strip().lower()
    return ""


def _cluster_interpretation(count: int, duration_minutes: int, share: float) -> str:
    if count >= 5 and duration_minutes <= 180 and share >= 0.5:
        return "集中批量 401，优先按同一时间段封禁/失效事件判断。"
    if count >= 3 and duration_minutes <= 180:
        return "存在同一时间段 401 聚集。"
    return "401 分布相对分散。"


def _is_concentrated_401_burst(cluster: dict[str, Any] | None, total_24h: int) -> bool:
    if not cluster or total_24h <= 0:
        return False
    count = int(cluster.get("count") or 0)
    duration_minutes = int(cluster.get("duration_minutes") or 0)
    share = float(cluster.get("share_of_24h_401") or 0)
    return count >= 5 and duration_minutes <= 180 and share >= 0.5


async def _median_survival_hours_before_401(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    since: datetime,
) -> float | None:
    values: list[float] = []
    cursor = db.remote_account_status_events.find(
        {
            "site_id": site_id,
            "event_type": "401_detected",
            "detected_at": {"$gte": since},
            **_group_filter(group_id),
        },
        {"session_id": 1, "detected_at": 1},
    ).limit(200)
    async for event in cursor:
        session_id = event.get("session_id")
        detected_at = _coerce_datetime(event.get("detected_at"))
        if not session_id or not detected_at:
            continue
        session = await db.remote_account_sessions.find_one({"_id": session_id}, {"started_at": 1})
        started_at = _coerce_datetime((session or {}).get("started_at"))
        if not started_at or detected_at < started_at:
            continue
        values.append((detected_at - started_at).total_seconds() / 3600)
    if not values:
        return None
    return round(float(median(values)), 2)


def _group_filter(group_id: int) -> dict[str, Any]:
    return {"current_group_ids": group_id}


def _account_type_filter(account_type: str | None) -> str | None:
    value = str(account_type or "").strip().lower()
    return value if value in {"pro", "plus", "free", "team", "k12"} else None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
