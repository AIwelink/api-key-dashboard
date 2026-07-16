from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.notifications.service import send_notification_event
from app.utils import now_utc, serialize_doc


HEALTH_RANK = {
    "pending": -1,
    "very_abundant": 0,
    "abundant": 1,
    "healthy": 2,
    "tight": 3,
    "danger": 4,
    "exhausted": 5,
}
THRESHOLD_LABELS = {
    "tight": "紧张及以下",
    "danger": "危险及以下",
    "exhausted": "仅耗尽",
}
HEALTH_LABELS = {
    "pending": "等待数据",
    "very_abundant": "十分充裕",
    "abundant": "充裕",
    "healthy": "健康",
    "tight": "紧张",
    "danger": "危险",
    "exhausted": "耗尽",
}


async def evaluate_capacity_notifications(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    groups: list[dict[str, Any]],
    capacity_summaries: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    settings = {
        int(doc["group_id"]): doc
        async for doc in db.group_observability_settings.find({"site_id": site_id})
        if isinstance(doc.get("group_id"), int)
    }
    tasks = []
    for group in groups:
        group_id = _integer(group.get("id"))
        if group_id is None or group_id not in settings:
            continue
        summary = capacity_summaries.get(group_id)
        if not isinstance(summary, dict):
            continue
        tasks.append(
            _evaluate_group_capacity_notification(
                db,
                site_id=site_id,
                group_id=group_id,
                group_name=str(group.get("name") or f"#{group_id}"),
                setting=settings[group_id],
                summary=summary,
            )
        )
    if not tasks:
        return {"ok": True, "checked": 0, "sent": 0, "items": []}
    raw_results = await asyncio.gather(*tasks, return_exceptions=True)
    items: list[dict[str, Any]] = []
    for result in raw_results:
        if isinstance(result, Exception):
            items.append({"ok": False, "sent": False, "message": str(result) or result.__class__.__name__})
        else:
            items.append(result)
    return {
        "ok": all(item.get("ok") is not False for item in items),
        "checked": len(items),
        "sent": sum(1 for item in items if item.get("sent") is True),
        "items": serialize_doc(items),
    }


async def _evaluate_group_capacity_notification(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    group_name: str,
    setting: dict[str, Any],
    summary: dict[str, Any],
) -> dict[str, Any]:
    meta_id = f"{site_id}:{group_id}"
    meta = await db.sub2api_capacity_notification_meta.find_one({"_id": meta_id}) or {}
    now = now_utc()
    decision = capacity_notification_decision(setting=setting, summary=summary, meta=meta, now=now)
    base_updates = {
        "site_id": site_id,
        "group_id": group_id,
        "group_name": group_name,
        "last_observed_status": decision["health_status"],
        "last_observed_at": now,
        "updated_at": now,
    }
    if not decision["below_threshold"]:
        if meta.get("active_alert"):
            base_updates["last_recovered_at"] = now
        base_updates["active_alert"] = False
        await db.sub2api_capacity_notification_meta.update_one(
            {"_id": meta_id},
            {"$set": base_updates, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return {"ok": True, "sent": False, "site_id": site_id, "group_id": group_id, "reason": decision["reason"]}

    base_updates["active_alert"] = True
    if not decision["send"]:
        await db.sub2api_capacity_notification_meta.update_one(
            {"_id": meta_id},
            {"$set": base_updates, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return {"ok": True, "sent": False, "site_id": site_id, "group_id": group_id, "reason": decision["reason"]}

    health_status = decision["health_status"]
    health_label = str(summary.get("health_label") or HEALTH_LABELS.get(health_status) or health_status)
    threshold = decision["threshold"]
    title = f"账号池容量预警：{group_name} {health_label}"
    text = _capacity_notification_text(
        site_id=site_id,
        group_id=group_id,
        group_name=group_name,
        threshold=threshold,
        summary=summary,
    )
    delivery = await send_notification_event(
        db,
        event_type="sub2api.capacity.low",
        title=title,
        text=text,
        markdown_text=f"### {title}\n\n{text}",
        severity=_notification_severity(health_status),
        source="sub2api_capacity",
        resource_type="sub2api_group",
        resource_id=f"{site_id}:{group_id}",
        payload={
            "site_id": site_id,
            "group_id": group_id,
            "group_name": group_name,
            "threshold": threshold,
            "health_status": health_status,
            "capacity_summary": summary,
            "trigger_reason": decision["reason"],
        },
        dedupe_key=f"sub2api.capacity.low:{site_id}:{group_id}:{health_status}:{int(now.timestamp())}",
    )
    event = delivery.get("event") if isinstance(delivery.get("event"), dict) else {}
    delivery_status = str(event.get("status") or "unknown")
    base_updates.update(
        {
            "last_attempt_at": now,
            "last_notified_status": health_status,
            "last_delivery_status": delivery_status,
            "last_notification_event_id": event.get("id") or event.get("_id"),
            "last_trigger_reason": decision["reason"],
        }
    )
    await db.sub2api_capacity_notification_meta.update_one(
        {"_id": meta_id},
        {"$set": base_updates, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {
        "ok": delivery_status in {"success", "partial"},
        "sent": int(delivery.get("success") or 0) > 0,
        "site_id": site_id,
        "group_id": group_id,
        "health_status": health_status,
        "delivery_status": delivery_status,
        "delivery": delivery,
    }


def capacity_notification_decision(
    *,
    setting: dict[str, Any],
    summary: dict[str, Any],
    meta: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    health_status = str(summary.get("health_status") or "pending")
    threshold = str(setting.get("capacity_notification_threshold") or "tight")
    if threshold not in THRESHOLD_LABELS:
        threshold = "tight"
    enabled = setting.get("capacity_notification_enabled") is True
    below_threshold = enabled and HEALTH_RANK.get(health_status, -1) >= HEALTH_RANK[threshold]
    if not enabled:
        return {"send": False, "below_threshold": False, "reason": "disabled", "health_status": health_status, "threshold": threshold}
    if not below_threshold:
        return {"send": False, "below_threshold": False, "reason": "above_threshold", "health_status": health_status, "threshold": threshold}

    active_alert = meta.get("active_alert") is True
    previous_status = str(meta.get("last_notified_status") or meta.get("last_observed_status") or "pending")
    if not active_alert:
        return {"send": True, "below_threshold": True, "reason": "threshold_crossed", "health_status": health_status, "threshold": threshold}
    if HEALTH_RANK.get(health_status, -1) > HEALTH_RANK.get(previous_status, -1):
        return {"send": True, "below_threshold": True, "reason": "status_worsened", "health_status": health_status, "threshold": threshold}

    cooldown_minutes = _bounded_integer(setting.get("capacity_notification_cooldown_minutes"), default=60, minimum=5, maximum=1440)
    last_attempt_at = _parse_datetime(meta.get("last_attempt_at"))
    if last_attempt_at is None or now - last_attempt_at >= timedelta(minutes=cooldown_minutes):
        return {"send": True, "below_threshold": True, "reason": "cooldown_elapsed", "health_status": health_status, "threshold": threshold}
    return {"send": False, "below_threshold": True, "reason": "cooldown_active", "health_status": health_status, "threshold": threshold}


def _capacity_notification_text(
    *,
    site_id: str,
    group_id: int,
    group_name: str,
    threshold: str,
    summary: dict[str, Any],
) -> str:
    return "\n".join(
        [
            f"站点：{site_id}",
            f"分组：{group_name}（#{group_id}）",
            f"当前状态：{summary.get('health_label') or HEALTH_LABELS.get(str(summary.get('health_status'))) or '-'}",
            f"通知阈值：{THRESHOLD_LABELS.get(threshold, threshold)}",
            f"压力阶段：{summary.get('pressure_stage_label') or '等待数据'}",
            f"实际 / 动态可用：{_hours(summary.get('actual_runway_hours'))} / {_hours(summary.get('dynamic_runway_hours'))}",
            f"TPM / RPM：{_metric(summary.get('pressure_tpm'))} / {_metric(summary.get('pressure_rpm'))}",
            f"并发覆盖：{_multiple(summary.get('concurrency_coverage'))}",
            f"建议补号：{int(summary.get('recommended_refill_accounts') or 0)} 个",
            f"动态 5h 可用：{_percent(summary.get('available_5h_percent'))}，{_money(summary.get('dynamic_five_hour_remaining_estimated_usd'))} / {_money(summary.get('dynamic_five_hour_capacity_usd'))}",
            f"7d 可用：{_percent(summary.get('available_7d_percent'))}，{_money(summary.get('seven_day_remaining_estimated_usd'))} / {_money(summary.get('seven_day_capacity_usd'))}",
            f"可用账号：{int(summary.get('available_accounts') or 0)}，5h 可用账号：{int(summary.get('available_5h_accounts') or 0)}",
            f"原因：{summary.get('health_reason') or '-'}",
        ]
    )


def _notification_severity(health_status: str) -> str:
    return {"tight": "warning", "danger": "danger", "exhausted": "critical"}.get(health_status, "warning")


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _hours(value: Any) -> str:
    number = _number(value)
    if number is None:
        return "-"
    if number < 1:
        return f"{round(number * 60)}分钟"
    return f"{number:.1f}小时"


def _metric(value: Any) -> str:
    number = _number(value)
    return "-" if number is None else f"{number:,.0f}"


def _multiple(value: Any) -> str:
    number = _number(value)
    return "-" if number is None else f"{number:.2f}x"


def _integer(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _bounded_integer(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    parsed = _integer(value)
    if parsed is None:
        return default
    return max(minimum, min(maximum, parsed))


def _parse_datetime(value: Any) -> datetime | None:
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


def _percent(value: Any) -> str:
    try:
        return f"{float(value):.0f}%"
    except (TypeError, ValueError):
        return "-"


def _money(value: Any) -> str:
    try:
        return f"${float(value):.2f}"
    except (TypeError, ValueError):
        return "$0.00"
