from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta, timezone
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
    "exhausted": "仅耗尽（实时<1h仍告警）",
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
VALID_HEALTH_STATUSES = frozenset(HEALTH_LABELS) - {"pending"}
TRIGGER_REASON_LABELS = {
    "threshold_crossed": "首次进入通知阈值",
    "realtime_runway_below_one_hour": "实时可用时间低于1小时",
    "status_worsened": "容量状态继续恶化",
    "cooldown_elapsed": "危险状态持续，冷却时间已到",
}
REFILL_ACCOUNT_TYPE_LABELS = {
    "free": "Free",
    "plus": "Plus",
    "team": "Team",
    "k12": "K12",
    "pro": "Pro",
}
REFILL_REFERENCE_NOTE = "仅供参考，请结合实时供货和账号质量判断。"
SHANGHAI_TZ = timezone(timedelta(hours=8))


async def _active_feishu_channel_ids(db: AsyncIOMotorDatabase) -> list[str]:
    cursor = db.notification_channels.find(
        {"status": "active", "channel_type": "feishu"},
        {"_id": 1},
    ).sort("created_at", 1)
    return [str(item["_id"]) async for item in cursor]


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
    status_change = capacity_status_change_decision(setting=setting, summary=summary, meta=meta)
    health_status = decision["health_status"]
    base_updates = {
        "site_id": site_id,
        "group_id": group_id,
        "group_name": group_name,
        "updated_at": now,
    }
    if health_status in VALID_HEALTH_STATUSES:
        base_updates["last_observed_status"] = health_status
        base_updates["last_observed_at"] = now

    if not decision["send"] and not status_change["send"]:
        base_updates["active_alert"] = decision["keep_active_alert"]
        await db.sub2api_capacity_notification_meta.update_one(
            {"_id": meta_id},
            {"$set": base_updates, "$setOnInsert": {"created_at": now}},
            upsert=True,
        )
        return {"ok": True, "sent": False, "site_id": site_id, "group_id": group_id, "reason": decision["reason"]}

    health_label = str(summary.get("health_label") or HEALTH_LABELS.get(health_status) or health_status)
    if decision["send"]:
        threshold = decision["threshold"]
        notification_type = str(decision.get("notification_type") or "alert")
        is_recovery = notification_type == "recovery"
        title = f"账号池容量{'恢复' if is_recovery else '预警'}：{group_name} {health_label}"
        if is_recovery:
            message_text = _capacity_recovery_text(
                site_id=site_id,
                group_id=group_id,
                group_name=group_name,
                recovered_at=now,
                summary=summary,
            )
        else:
            message_text = _capacity_notification_text(
                site_id=site_id,
                group_id=group_id,
                group_name=group_name,
                threshold=threshold,
                summary=summary,
                trigger_reason=decision["reason"],
            )
        payload = {
            "site_id": site_id,
            "group_id": group_id,
            "group_name": group_name,
            "threshold": threshold,
            "health_status": health_status,
            "notification_type": notification_type,
            "capacity_summary": summary,
            "trigger_reason": decision["reason"],
        }
        if status_change["send"]:
            payload["previous_health_status"] = status_change["previous_health_status"]
        delivery = await send_notification_event(
            db,
            event_type="sub2api.capacity.recovered" if is_recovery else "sub2api.capacity.low",
            title=title,
            text=message_text,
            markdown_text=f"### {title}\n\n{message_text}",
            severity="success" if is_recovery else _notification_severity(health_status),
            source="sub2api_capacity",
            resource_type="sub2api_group",
            resource_id=f"{site_id}:{group_id}",
            payload=payload,
            dedupe_key=f"sub2api.capacity.{'recovered' if is_recovery else 'low'}:{site_id}:{group_id}:{health_status}:{int(now.timestamp())}",
        )
        event = delivery.get("event") if isinstance(delivery.get("event"), dict) else {}
        delivery_status = str(event.get("status") or "unknown")
        event_id = event.get("id") or event.get("_id")
        base_updates.update(
            {
                "last_attempt_at": now,
                "last_delivery_status": delivery_status,
                "last_notification_event_id": event_id,
                "last_notification_type": notification_type,
                "last_trigger_reason": decision["reason"],
                "active_alert": not is_recovery,
            }
        )
        if is_recovery:
            base_updates["last_recovered_at"] = now
            base_updates["last_recovered_status"] = health_status
        else:
            base_updates["last_notified_status"] = health_status
        if status_change["send"]:
            base_updates.update(
                {
                    "last_state_change_at": now,
                    "last_state_change_from": status_change["previous_health_status"],
                    "last_state_change_to": health_status,
                    "last_state_change_event_id": event_id,
                    "last_state_change_delivery_status": delivery_status,
                }
            )
    else:
        previous_health_status = str(status_change["previous_health_status"])
        previous_label = HEALTH_LABELS.get(previous_health_status, previous_health_status)
        title = f"账号池容量状态变化：{group_name} {previous_label} -> {health_label}"
        message_text = _capacity_status_change_text(
            site_id=site_id,
            group_id=group_id,
            group_name=group_name,
            previous_health_status=previous_health_status,
            changed_at=now,
            summary=summary,
        )
        channel_ids = await _active_feishu_channel_ids(db)
        delivery = await send_notification_event(
            db,
            event_type="sub2api.capacity.status_changed",
            title=title,
            text=message_text,
            markdown_text=f"### {title}\n\n{message_text}",
            severity=_status_change_severity(health_status),
            source="sub2api_capacity",
            resource_type="sub2api_group",
            resource_id=f"{site_id}:{group_id}",
            payload={
                "site_id": site_id,
                "group_id": group_id,
                "group_name": group_name,
                "previous_health_status": previous_health_status,
                "health_status": health_status,
                "notification_type": "status_change",
                "capacity_summary": summary,
            },
            dedupe_key=f"sub2api.capacity.status_changed:{site_id}:{group_id}:{previous_health_status}:{health_status}:{int(now.timestamp())}",
            channel_ids=channel_ids,
        )
        event = delivery.get("event") if isinstance(delivery.get("event"), dict) else {}
        delivery_status = str(event.get("status") or "unknown")
        event_id = event.get("id") or event.get("_id")
        notification_type = "status_change"
        base_updates.update(
            {
                "active_alert": decision["keep_active_alert"],
                "last_state_change_at": now,
                "last_state_change_from": previous_health_status,
                "last_state_change_to": health_status,
                "last_state_change_event_id": event_id,
                "last_state_change_delivery_status": delivery_status,
            }
        )

    await db.sub2api_capacity_notification_meta.update_one(
        {"_id": meta_id},
        {"$set": base_updates, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return {
        "ok": delivery_status in {"success", "partial"}
        or (notification_type == "status_change" and delivery_status == "skipped"),
        "sent": int(delivery.get("success") or 0) > 0,
        "site_id": site_id,
        "group_id": group_id,
        "health_status": health_status,
        "notification_type": notification_type,
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
    active_alert = meta.get("active_alert") is True
    hard_runway_alert = _realtime_runway_below_one_hour(summary)
    below_threshold = enabled and (
        hard_runway_alert or HEALTH_RANK.get(health_status, -1) >= HEALTH_RANK[threshold]
    )
    if not enabled:
        return _decision_result(False, False, "disabled", health_status, threshold)
    if health_status == "pending":
        return _decision_result(False, False, "waiting_data", health_status, threshold, keep_active_alert=active_alert)
    if not below_threshold:
        if active_alert and health_status in {"healthy", "abundant", "very_abundant"}:
            return _decision_result(True, False, "recovered", health_status, threshold, notification_type="recovery")
        if active_alert:
            return _decision_result(False, False, "recovery_pending", health_status, threshold, keep_active_alert=True)
        return _decision_result(False, False, "above_threshold", health_status, threshold)

    previous_status = str(meta.get("last_notified_status") or meta.get("last_observed_status") or "pending")
    if not active_alert:
        reason = "realtime_runway_below_one_hour" if hard_runway_alert else "threshold_crossed"
        return _decision_result(True, True, reason, health_status, threshold, notification_type="alert", keep_active_alert=True)
    if HEALTH_RANK.get(health_status, -1) > HEALTH_RANK.get(previous_status, -1):
        return _decision_result(True, True, "status_worsened", health_status, threshold, notification_type="alert", keep_active_alert=True)

    cooldown_minutes = _bounded_integer(setting.get("capacity_notification_cooldown_minutes"), default=60, minimum=5, maximum=1440)
    last_attempt_at = _parse_datetime(meta.get("last_attempt_at"))
    cooldown_elapsed = last_attempt_at is None or now - last_attempt_at >= timedelta(minutes=cooldown_minutes)
    if not cooldown_elapsed:
        return _decision_result(False, True, "cooldown_active", health_status, threshold, keep_active_alert=True)
    if health_status == "tight":
        return _decision_result(False, True, "tight_repeat_suppressed", health_status, threshold, keep_active_alert=True)
    return _decision_result(True, True, "cooldown_elapsed", health_status, threshold, notification_type="alert", keep_active_alert=True)


def capacity_status_change_decision(
    *,
    setting: dict[str, Any],
    summary: dict[str, Any],
    meta: dict[str, Any],
) -> dict[str, Any]:
    health_status = str(summary.get("health_status") or "pending")
    previous_health_status = str(meta.get("last_observed_status") or "")
    result = {
        "send": False,
        "reason": "unchanged",
        "previous_health_status": previous_health_status or None,
        "health_status": health_status,
    }
    if setting.get("capacity_notification_enabled") is not True:
        return {**result, "reason": "disabled"}
    if health_status not in VALID_HEALTH_STATUSES:
        return {**result, "reason": "waiting_data"}
    if previous_health_status not in VALID_HEALTH_STATUSES:
        return {**result, "reason": "baseline_created"}
    if previous_health_status == health_status:
        return result
    return {**result, "send": True, "reason": "status_changed"}


def _realtime_runway_below_one_hour(summary: dict[str, Any]) -> bool:
    if summary.get("realtime_risk_ready") is not True:
        return False
    runway_values = (
        _number(summary.get("actual_runway_hours")),
        _number(summary.get("dynamic_runway_hours")),
    )
    return any(value is not None and value < 1.0 for value in runway_values)


def _decision_result(
    send: bool,
    below_threshold: bool,
    reason: str,
    health_status: str,
    threshold: str,
    *,
    notification_type: str | None = None,
    keep_active_alert: bool = False,
) -> dict[str, Any]:
    return {
        "send": send,
        "below_threshold": below_threshold,
        "reason": reason,
        "health_status": health_status,
        "threshold": threshold,
        "notification_type": notification_type,
        "keep_active_alert": keep_active_alert,
    }


def _capacity_notification_text(
    *,
    site_id: str,
    group_id: int,
    group_name: str,
    threshold: str,
    summary: dict[str, Any],
    trigger_reason: str | None = None,
) -> str:
    lines = [
        f"站点：{site_id}",
        f"分组：{group_name}（#{group_id}）",
        f"当前状态：{summary.get('health_label') or HEALTH_LABELS.get(str(summary.get('health_status'))) or '-'}",
        f"通知阈值：{THRESHOLD_LABELS.get(threshold, threshold)}",
    ]
    if trigger_reason:
        lines.append(f"触发方式：{TRIGGER_REASON_LABELS.get(trigger_reason, trigger_reason)}")
    lines.extend(
        [
            f"压力阶段：{summary.get('pressure_stage_label') or '等待数据'}",
            f"预测口径：{_forecast_basis(summary)}",
            f"实际 / 动态可用：{_runway_hours(summary, 'actual_runway_hours', 'forecast_actual_runway_capped')} / {_runway_hours(summary, 'dynamic_runway_hours', 'forecast_dynamic_runway_capped')}",
            f"5h 可用：实际 {_money(summary.get('five_hour_actual_remaining_usd'))} / 动态 {_money(summary.get('dynamic_five_hour_remaining_estimated_usd'))} / 容量 {_money(summary.get('dynamic_five_hour_capacity_usd'))}",
            f"7d 可用：实际 {_money(summary.get('seven_day_actual_remaining_usd'))} / 动态 {_money(summary.get('seven_day_remaining_estimated_usd'))} / 容量 {_money(summary.get('seven_day_capacity_usd'))}",
            f"TPM / RPM：{_metric(summary.get('latest_tpm'))} / {_metric(summary.get('latest_rpm'))}",
            f"并发覆盖：{_multiple(summary.get('concurrency_coverage'))}",
            f"当前账号：{int(summary.get('available_accounts') or 0)} 个，5h 可用 {int(summary.get('available_5h_accounts') or 0)} 个",
            f"建议动作：{_refill_action(summary.get('recommended_refill_accounts'), summary.get('recommended_refill_options'))}",
            f"判断原因：{summary.get('health_reason') or '-'}",
        ]
    )
    return "\n".join(lines)


def _capacity_recovery_text(
    *,
    site_id: str,
    group_id: int,
    group_name: str,
    recovered_at: datetime,
    summary: dict[str, Any],
) -> str:
    recovered_time = recovered_at.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
    return "\n".join(
        [
            f"站点：{site_id}",
            f"分组：{group_name}（#{group_id}）",
            f"恢复状态：{summary.get('health_label') or HEALTH_LABELS.get(str(summary.get('health_status'))) or '-'}",
            f"压力阶段：{summary.get('pressure_stage_label') or '-'}",
            f"预测口径：{_forecast_basis(summary)}",
            f"实际 / 动态可用：{_runway_hours(summary, 'actual_runway_hours', 'forecast_actual_runway_capped')} / {_runway_hours(summary, 'dynamic_runway_hours', 'forecast_dynamic_runway_capped')}",
            f"并发覆盖：{_multiple(summary.get('concurrency_coverage'))}",
            f"可用账号：{int(summary.get('available_accounts') or 0)} 个",
            f"恢复时间：{recovered_time}",
        ]
    )


def _capacity_status_change_text(
    *,
    site_id: str,
    group_id: int,
    group_name: str,
    previous_health_status: str,
    changed_at: datetime,
    summary: dict[str, Any],
) -> str:
    health_status = str(summary.get("health_status") or "pending")
    previous_label = HEALTH_LABELS.get(previous_health_status, previous_health_status)
    current_label = str(summary.get("health_label") or HEALTH_LABELS.get(health_status) or health_status)
    changed_time = changed_at.astimezone(SHANGHAI_TZ).strftime("%Y-%m-%d %H:%M")
    return "\n".join(
        [
            f"站点：{site_id}",
            f"分组：{group_name}（#{group_id}）",
            f"状态变化：{previous_label} -> {current_label}",
            f"压力阶段：{summary.get('pressure_stage_label') or '-'}",
            f"实际 / 动态可用：{_runway_hours(summary, 'actual_runway_hours', 'forecast_actual_runway_capped')} / {_runway_hours(summary, 'dynamic_runway_hours', 'forecast_dynamic_runway_capped')}",
            f"并发覆盖：{_multiple(summary.get('concurrency_coverage'))}",
            f"判断原因：{summary.get('health_reason') or '-'}",
            f"变化时间：{changed_time}",
        ]
    )


def _status_change_severity(health_status: str) -> str:
    return {
        "exhausted": "critical",
        "danger": "danger",
        "tight": "warning",
        "healthy": "success",
        "abundant": "info",
        "very_abundant": "info",
    }.get(health_status, "info")


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


def _runway_hours(summary: dict[str, Any], value_key: str, capped_key: str) -> str:
    formatted = _hours(summary.get(value_key))
    if formatted == "-" or not summary.get(capped_key):
        return formatted
    return f">{formatted}"


def _forecast_basis(summary: dict[str, Any]) -> str:
    if summary.get("forecast_status") != "active":
        return "TPM实时估算（降级）"
    if summary.get("forecast_nowcast_applied"):
        return "未来24小时 P90逐小时 + 当前小时Nowcast"
    return "未来24小时 P90逐小时"


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
        return "-"


def _refill_action(value: Any, options: Any = None) -> str:
    option_parts: list[str] = []
    if isinstance(options, dict):
        for option_key, raw_option in options.items():
            if not isinstance(raw_option, dict):
                continue
            count = _integer(raw_option.get("recommended_refill_accounts")) or 0
            if count <= 0:
                continue
            account_type = str(raw_option.get("account_type") or option_key or "").strip().lower()
            label = REFILL_ACCOUNT_TYPE_LABELS.get(account_type, account_type.upper() or "账号")
            option_parts.append(f"补 {label} {count} 个")
    if option_parts:
        return f"{'，或'.join(option_parts)}。{REFILL_REFERENCE_NOTE}"
    count = _integer(value) or 0
    if count > 0:
        return f"补充 {count} 个账号。{REFILL_REFERENCE_NOTE}"
    return "无需补号，检查并发、异常账号或采样数据"
