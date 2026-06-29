from __future__ import annotations

from datetime import UTC, datetime, timedelta
from statistics import median
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.events.records import event_records_summary, list_event_records
from app.modules.sub2api.account_probe import list_duplicate_email_alerts
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
        "duplicate_email_alert_count": duplicate_email_alert_count,
        "median_survival_hours_7d": await _median_survival_hours_before_401(db, site_id=site_id, group_id=group_id, since=seven_day_start),
        "recent_events": recent_events,
        "event_summary_24h": summary_24h,
        "event_summary_7d": summary_7d,
    }


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
