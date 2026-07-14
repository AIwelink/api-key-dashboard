from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.memory import AGENT_RUNS_COLLECTION
from app.modules.agent.tasks import TASK_STATUS_ALERT_DRAFTED
from app.modules.notifications.service import send_notification_event
from app.utils import now_utc, serialize_doc


AGENT_ALERT_EVENT_TYPE = "agent_alert_draft"
AGENT_DECISION_EVENT_TYPE = "agent_loop_decision"
AGENT_ALERT_DISPATCH_SOURCE = "agent"
DEFAULT_ALERT_RETRY_MINUTES = 30
DEFAULT_DECISION_NOTIFICATION_TRIGGERS = {
    "scheduler_patrol",
    "scheduler_task_due",
    "scheduler_review_due",
    "event_spike",
}
SEVERITY_RANK = {
    "healthy": 0,
    "info": 0,
    "watch": 1,
    "warning": 2,
    "danger": 3,
    "critical": 4,
}


async def process_agent_alert_drafts(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Dispatch eligible Agent alert drafts according to notification policy.

    Scheduler is only a dispatcher here. It does not create refill decisions,
    change account-pool business data, or bypass notification policy.
    """

    if not bool(getattr(settings, "notification_dispatch_enabled", False)):
        return {"ok": True, "implemented": True, "enabled": False, "processed": [], "skipped": [{"reason": "notification_dispatch_disabled"}]}

    limit = _positive_int(getattr(settings, "max_tasks_per_tick", 5), default=5)
    query = {
        "$or": [{"owner_scope": "agent"}, {"owner_scope": {"$exists": False}}],
        "status": TASK_STATUS_ALERT_DRAFTED,
        "alert_status": "drafted",
        "alert_draft": {"$type": "object"},
    }
    candidates = [item async for item in db.agent_tasks.find(query).sort("updated_at", 1).limit(limit)]
    processed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for task in candidates:
        task_id = str(task.get("task_id") or task.get("_id") or "")
        if not task_id:
            skipped.append({"reason": "missing_task_id"})
            continue
        result = await dispatch_agent_alert_draft(
            db,
            task_id=task_id,
            settings=settings,
            scheduler_tick_id=scheduler_tick_id,
            actor=actor,
            manual_confirmed=False,
        )
        if result.get("sent"):
            processed.append(result)
        else:
            skipped.append(result)
    return {"ok": True, "implemented": True, "enabled": True, "total": len(candidates), "processed": processed, "skipped": skipped}


async def process_agent_decision_notifications(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Send DingTalk summaries for meaningful scheduler-created Agent decisions.

    This is separate from alert draft dispatch. It never creates decisions,
    changes account-pool data, or bypasses the notification channel module.
    """

    if not bool(getattr(settings, "decision_notification_enabled", False)):
        return {"ok": True, "implemented": True, "enabled": False, "processed": [], "skipped": [{"reason": "decision_notification_disabled"}]}
    if not scheduler_tick_id:
        return {"ok": True, "implemented": True, "enabled": True, "processed": [], "skipped": [{"reason": "scheduler_tick_id_missing"}]}

    dingtalk_channels = await _active_dingtalk_channel_ids(db)
    if not dingtalk_channels:
        return {"ok": True, "implemented": True, "enabled": True, "processed": [], "skipped": [{"reason": "active_dingtalk_channel_missing"}]}

    allowed_triggers = _decision_notification_triggers(settings)
    limit = _positive_int(getattr(settings, "max_tasks_per_tick", 5), default=5) + _positive_int(getattr(settings, "max_pool_patrols_per_tick", 3), default=3) + _positive_int(getattr(settings, "max_event_triggers_per_tick", 3), default=3)
    query = {
        "status": "success",
        "decision_id": {"$exists": True, "$ne": None},
        "trigger": {"$in": sorted(allowed_triggers)},
        "$or": [
            {"trigger_metadata.scheduler_tick_id": scheduler_tick_id},
            {"metadata.scheduler_tick_id": scheduler_tick_id},
        ],
    }
    runs = [item async for item in db[AGENT_RUNS_COLLECTION].find(query).sort("started_at", 1).limit(max(1, min(limit, 100)))]
    processed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for run in runs:
        result = await dispatch_agent_decision_notification(
            db,
            run=run,
            settings=settings,
            scheduler_tick_id=scheduler_tick_id,
            channel_ids=dingtalk_channels,
            actor=actor,
        )
        if result.get("sent"):
            processed.append(result)
        elif result.get("ok") is False:
            errors.append(result)
        else:
            skipped.append(result)
    return {
        "ok": not errors,
        "implemented": True,
        "enabled": True,
        "total": len(runs),
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
    }


async def dispatch_agent_decision_notification(
    db: AsyncIOMotorDatabase,
    *,
    run: dict[str, Any],
    settings: Any,
    scheduler_tick_id: str | None,
    channel_ids: list[str] | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_id = _clean_optional_string(run.get("run_id") or run.get("_id"))
    decision_id = _clean_optional_string(run.get("decision_id"))
    trigger = _clean_optional_string(run.get("trigger"))
    if not run_id or not decision_id or not trigger:
        return {"ok": True, "sent": False, "reason": "run_identity_incomplete", "run_id": run_id, "decision_id": decision_id}

    decision = await _load_decision(db, decision_id)
    task = _task_from_run_or_decision(run=run, decision=decision)
    policy = await evaluate_agent_decision_notification_policy(
        db,
        run=run,
        decision=decision,
        task=task,
        settings=settings,
    )
    if not policy.get("allowed"):
        return {"ok": True, "sent": False, "run_id": run_id, "decision_id": decision_id, **policy}

    title = _decision_notification_title(run=run, decision=decision)
    text = _decision_notification_text(run=run, decision=decision, task=task)
    markdown_text = _markdown_decision_text(title=title, run=run, decision=decision, task=task)
    dedupe_key = _decision_notification_dedupe_key(run=run, decision_id=decision_id)
    try:
        delivery = await send_notification_event(
            db,
            event_type=AGENT_DECISION_EVENT_TYPE,
            title=title,
            text=text,
            markdown_text=markdown_text,
            severity=policy.get("severity") or "warning",
            source=AGENT_ALERT_DISPATCH_SOURCE,
            resource_type="agent_run",
            resource_id=run_id,
            payload={
                "run_id": run_id,
                "decision_id": decision_id,
                "task_id": task.get("task_id") or task.get("_id") if isinstance(task, dict) else None,
                "site_id": run.get("site_id") or decision.get("site_id"),
                "pool_id": run.get("pool_id") or decision.get("pool_id"),
                "trigger": trigger,
                "scheduler_tick_id": scheduler_tick_id,
                "requires_human_confirm": _requires_human_confirm(run=run, decision=decision, task=task),
                "policy": policy,
                "actor_id": _actor_id(actor),
            },
            dedupe_key=dedupe_key,
            channel_ids=channel_ids or policy.get("channel_ids"),
        )
        event = delivery.get("event") if isinstance(delivery.get("event"), dict) else {}
        sent = int(delivery.get("success") or 0) > 0
        await _mark_run_decision_notification(
            db,
            run_id=run_id,
            notification_event_id=_clean_optional_string(event.get("_id") or event.get("id")),
            delivery=delivery,
            sent=sent,
        )
        if not sent:
            return {"ok": False, "sent": False, "run_id": run_id, "decision_id": decision_id, "reason": "notification_delivery_failed_or_skipped", "delivery": delivery}
        return {
            "ok": True,
            "sent": True,
            "run_id": run_id,
            "decision_id": decision_id,
            "notification_event_id": event.get("_id") or event.get("id"),
            "delivery": delivery,
        }
    except Exception as exc:  # noqa: BLE001 - notification failures must not crash scheduler ticks.
        return {"ok": False, "sent": False, "run_id": run_id, "decision_id": decision_id, "reason": str(exc) or exc.__class__.__name__}


async def evaluate_agent_decision_notification_policy(
    db: AsyncIOMotorDatabase,
    *,
    run: dict[str, Any],
    decision: dict[str, Any],
    task: dict[str, Any],
    settings: Any,
) -> dict[str, Any]:
    trigger = _clean_optional_string(run.get("trigger"))
    if trigger not in _decision_notification_triggers(settings):
        return _policy_denied("trigger_not_enabled_for_decision_notification")
    if not decision:
        return _policy_denied("decision_not_found")

    severity = _clean_optional_string(run.get("severity") or decision.get("severity")) or "info"
    min_severity = _clean_optional_string(getattr(settings, "decision_notification_min_severity", None)) or "warning"
    important_task_status = _clean_optional_string(task.get("status")) in {"waiting_human", "alert_drafted", "review_due"} if isinstance(task, dict) else False
    if _severity_rank(severity) < _severity_rank(min_severity) and not important_task_status:
        return _policy_denied("severity_below_notification_threshold")

    cooldown_minutes = _positive_int(getattr(settings, "decision_notification_cooldown_minutes", 30), default=30)
    if await _recent_decision_notification_exists(db, run=run, cooldown_minutes=cooldown_minutes):
        return _policy_denied("decision_notification_dedupe_cooldown_active")

    dingtalk_channels = await _active_dingtalk_channel_ids(db)
    if not dingtalk_channels:
        return _policy_denied("active_dingtalk_channel_missing")
    return {
        "allowed": True,
        "reason": "policy_allowed",
        "severity": severity,
        "min_severity": min_severity,
        "channel_ids": dingtalk_channels,
    }


async def list_agent_notifications(
    db: AsyncIOMotorDatabase,
    *,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "$or": [
            {"owner_scope": "agent"},
            {"owner_scope": {"$exists": False}},
        ],
        "$and": [
            {
                "$or": [
                    {"status": TASK_STATUS_ALERT_DRAFTED},
                    {"alert_draft": {"$type": "object"}},
                    {"alert_status": {"$exists": True, "$ne": None}},
                    {"alert_notification_event_id": {"$exists": True, "$ne": None}},
                ]
            }
        ],
    }
    normalized_status = _clean_optional_string(status)
    if normalized_status:
        query["alert_status"] = normalized_status
    normalized_limit = max(1, min(int(limit or 50), 200))
    tasks = [item async for item in db.agent_tasks.find(query).sort("updated_at", -1).limit(normalized_limit)]
    items = [await _notification_view(db, task) for task in tasks]
    if not normalized_status or normalized_status in {"sent", "success", "failed", "partial", "skipped"}:
        decision_events = await _list_decision_notification_views(db, status=normalized_status, limit=normalized_limit)
        items.extend(decision_events)
    items.sort(key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""), reverse=True)
    items = items[:normalized_limit]
    total = await db.agent_tasks.count_documents(query)
    return {"items": serialize_doc(items), "total": total + await _count_decision_notifications(db, status=normalized_status)}


async def dispatch_agent_alert_draft(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    settings: Any | None = None,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
    manual_confirmed: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    task = await db.agent_tasks.find_one({"$or": [{"_id": task_id}, {"task_id": task_id}]})
    if not task:
        return {"ok": False, "sent": False, "task_id": task_id, "reason": "task_not_found"}

    policy = await evaluate_agent_alert_dispatch_policy(
        db,
        task=task,
        settings=settings,
        manual_confirmed=manual_confirmed,
        force=force,
    )
    if not policy.get("allowed"):
        return {"ok": True, "sent": False, "task_id": str(task.get("task_id") or task.get("_id")), **policy}

    now = now_utc()
    draft = task.get("alert_draft") if isinstance(task.get("alert_draft"), dict) else {}
    title = _clean_optional_string(draft.get("title")) or _clean_optional_string(task.get("title")) or "Agent alert"
    content = _clean_optional_string(draft.get("content")) or _clean_optional_string(task.get("summary")) or title
    severity = _clean_optional_string(draft.get("severity") or task.get("severity")) or "warning"
    source_decision_id = _clean_optional_string(draft.get("source_decision_id") or task.get("current_decision_id"))
    dedupe_key = _alert_dedupe_key(task=task, source_decision_id=source_decision_id)
    try:
        delivery = await send_notification_event(
            db,
            event_type=AGENT_ALERT_EVENT_TYPE,
            title=title,
            text=content,
            markdown_text=_markdown_alert_text(title=title, content=content, task=task, draft=draft),
            severity=severity,
            source=AGENT_ALERT_DISPATCH_SOURCE,
            resource_type="agent_task",
            resource_id=str(task.get("task_id") or task.get("_id")),
            payload={
                "task_id": task.get("task_id") or task.get("_id"),
                "site_id": task.get("site_id"),
                "pool_id": task.get("pool_id"),
                "source_decision_id": source_decision_id,
                "scheduler_tick_id": scheduler_tick_id,
                "manual_confirmed": manual_confirmed,
                "policy": policy,
            },
            dedupe_key=dedupe_key,
            channel_ids=policy.get("channel_ids"),
        )
        event = delivery.get("event") if isinstance(delivery.get("event"), dict) else {}
        sent = int(delivery.get("success") or 0) > 0
        if not sent:
            error = "notification_delivery_failed_or_skipped"
            await _mark_alert_failed(
                db,
                task=task,
                reason=error,
                delivery=delivery,
                scheduler_tick_id=scheduler_tick_id,
                now=now,
            )
            return {"ok": False, "sent": False, "task_id": task.get("task_id") or task.get("_id"), "reason": error, "delivery": delivery}
        updated = await _mark_alert_sent(
            db,
            task=task,
            notification_event_id=_clean_optional_string(event.get("_id") or event.get("id")),
            delivery=delivery,
            scheduler_tick_id=scheduler_tick_id,
            now=now,
        )
        return {
            "ok": True,
            "sent": True,
            "task_id": task.get("task_id") or task.get("_id"),
            "notification_event_id": event.get("_id") or event.get("id"),
            "delivery": delivery,
            "task": updated,
        }
    except Exception as exc:  # noqa: BLE001 - notification failures must not crash Agent runs or scheduler ticks.
        error = str(exc) or exc.__class__.__name__
        updated = await _mark_alert_failed(
            db,
            task=task,
            reason=error,
            delivery={},
            scheduler_tick_id=scheduler_tick_id,
            now=now,
        )
        return {"ok": False, "sent": False, "task_id": task.get("task_id") or task.get("_id"), "reason": error, "task": updated}


async def evaluate_agent_alert_dispatch_policy(
    db: AsyncIOMotorDatabase,
    *,
    task: dict[str, Any],
    settings: Any | None = None,
    manual_confirmed: bool = False,
    force: bool = False,
) -> dict[str, Any]:
    task_id = str(task.get("task_id") or task.get("_id") or "")
    draft = task.get("alert_draft") if isinstance(task.get("alert_draft"), dict) else {}
    severity = _clean_optional_string(draft.get("severity") or task.get("severity")) or "warning"
    source_decision_id = _clean_optional_string(draft.get("source_decision_id") or task.get("current_decision_id"))
    if task.get("status") != TASK_STATUS_ALERT_DRAFTED:
        return _policy_denied("task_not_alert_drafted")
    if task.get("alert_status") != "drafted":
        return _policy_denied("alert_status_not_drafted")
    if not draft:
        return _policy_denied("alert_draft_missing")
    if not source_decision_id:
        return _policy_denied("source_decision_id_missing")

    decision = await _load_decision(db, source_decision_id)
    decision_payload = decision.get("decision") if isinstance(decision.get("decision"), dict) else {}
    if not decision:
        return _policy_denied("source_decision_not_found")
    if not bool(decision_payload.get("should_alert") or decision.get("should_alert") or draft.get("should_alert")):
        return _policy_denied("decision_should_alert_not_true")

    dingtalk_channels = await _active_dingtalk_channel_ids(db)
    if not dingtalk_channels:
        return _policy_denied("active_dingtalk_channel_missing")

    if not manual_confirmed:
        if not bool(getattr(settings, "notification_dispatch_enabled", False)):
            return _policy_denied("notification_dispatch_disabled")
        if severity != "critical" and not _draft_policy_allows_noncritical_auto_send(draft):
            return _policy_denied("severity_not_auto_sendable")
        if bool(task.get("requires_human_confirm") or draft.get("requires_human_confirm")) and not _allows_night_critical_before_human(draft=draft, severity=severity):
            return _policy_denied("human_confirmation_required")
        if not _is_night_window(now_utc()) and not _draft_policy_allows_daytime_auto_send(draft):
            return _policy_denied("daytime_policy_draft_only")

    cooldown_minutes = _positive_int(getattr(settings, "task_cooldown_minutes", 10), default=10) if settings is not None else 10
    if not force and await _recent_alert_dedupe_exists(db, task=task, source_decision_id=source_decision_id, cooldown_minutes=cooldown_minutes):
        return _policy_denied("dedupe_cooldown_active")

    return {
        "allowed": True,
        "reason": "manual_confirmed" if manual_confirmed else "policy_allowed",
        "task_id": task_id,
        "severity": severity,
        "source_decision_id": source_decision_id,
        "channel_ids": dingtalk_channels,
        "manual_confirmed": manual_confirmed,
    }


async def _mark_alert_sent(
    db: AsyncIOMotorDatabase,
    *,
    task: dict[str, Any],
    notification_event_id: str | None,
    delivery: dict[str, Any],
    scheduler_tick_id: str | None,
    now: datetime,
) -> dict[str, Any] | None:
    task_id = str(task.get("_id") or task.get("task_id"))
    source_decision_id = _clean_optional_string((task.get("alert_draft") or {}).get("source_decision_id") if isinstance(task.get("alert_draft"), dict) else task.get("current_decision_id"))
    await db.agent_tasks.update_one(
        {"_id": task_id},
        {
            "$set": {
                "alert_status": "sent",
                "alert_sent_at": now,
                "alert_notification_event_id": notification_event_id,
                "alert_notification_delivery": delivery,
                "alert_error": None,
                "updated_at": now,
            },
            "$push": {
                "state_history": _state_history_entry(
                    from_status=task.get("status"),
                    to_status=task.get("status"),
                    reason="Agent alert draft dispatched to DingTalk notification channel.",
                    run_id=None,
                    decision_id=source_decision_id,
                    scheduler_tick_id=scheduler_tick_id,
                    notification_event_id=notification_event_id,
                )
            },
        },
    )
    refreshed = await db.agent_tasks.find_one({"_id": task_id})
    return serialize_doc(refreshed) if refreshed else None


async def _mark_alert_failed(
    db: AsyncIOMotorDatabase,
    *,
    task: dict[str, Any],
    reason: str,
    delivery: dict[str, Any],
    scheduler_tick_id: str | None,
    now: datetime,
) -> dict[str, Any] | None:
    task_id = str(task.get("_id") or task.get("task_id"))
    source_decision_id = _clean_optional_string((task.get("alert_draft") or {}).get("source_decision_id") if isinstance(task.get("alert_draft"), dict) else task.get("current_decision_id"))
    await db.agent_tasks.update_one(
        {"_id": task_id},
        {
            "$set": {
                "alert_status": "failed",
                "alert_error": reason,
                "alert_notification_delivery": delivery,
                "next_check_at": now + timedelta(minutes=DEFAULT_ALERT_RETRY_MINUTES),
                "updated_at": now,
            },
            "$push": {
                "state_history": _state_history_entry(
                    from_status=task.get("status"),
                    to_status=task.get("status"),
                    reason=f"Agent alert dispatch failed: {reason}",
                    run_id=None,
                    decision_id=source_decision_id,
                    scheduler_tick_id=scheduler_tick_id,
                    notification_event_id=None,
                )
            },
        },
    )
    refreshed = await db.agent_tasks.find_one({"_id": task_id})
    return serialize_doc(refreshed) if refreshed else None


async def _load_decision(db: AsyncIOMotorDatabase, decision_id: str) -> dict[str, Any]:
    if not decision_id:
        return {}
    return await db.agent_decisions.find_one({"$or": [{"_id": decision_id}, {"decision_id": decision_id}]}) or {}


async def _active_dingtalk_channel_ids(db: AsyncIOMotorDatabase) -> list[str]:
    cursor = db.notification_channels.find({"status": "active", "channel_type": "dingtalk"}, {"_id": 1}).sort("created_at", 1)
    return [str(item["_id"]) async for item in cursor if item.get("_id")]


async def _recent_alert_dedupe_exists(
    db: AsyncIOMotorDatabase,
    *,
    task: dict[str, Any],
    source_decision_id: str | None,
    cooldown_minutes: int,
) -> bool:
    since = now_utc() - timedelta(minutes=max(1, cooldown_minutes))
    dedupe_key = _alert_dedupe_key(task=task, source_decision_id=source_decision_id)
    query = {"dedupe_key": dedupe_key, "created_at": {"$gte": since}, "status": {"$in": ["success", "partial", "pending"]}}
    return bool(await db.notification_events.find_one(query, {"_id": 1}))


def _alert_dedupe_key(*, task: dict[str, Any], source_decision_id: str | None) -> str:
    return f"agent_alert_dispatch:{task.get('task_id') or task.get('_id')}:{source_decision_id or '-'}"


async def _recent_decision_notification_exists(
    db: AsyncIOMotorDatabase,
    *,
    run: dict[str, Any],
    cooldown_minutes: int,
) -> bool:
    run_dedupe = _decision_notification_dedupe_key(run=run, decision_id=_clean_optional_string(run.get("decision_id")) or "-")
    if await db.notification_events.find_one({"dedupe_key": run_dedupe}, {"_id": 1}):
        return True
    since = now_utc() - timedelta(minutes=max(1, cooldown_minutes))
    query = {
        "event_type": AGENT_DECISION_EVENT_TYPE,
        "source": AGENT_ALERT_DISPATCH_SOURCE,
        "created_at": {"$gte": since},
        "status": {"$in": ["success", "partial", "pending"]},
        "payload.pool_id": run.get("pool_id"),
        "payload.trigger": run.get("trigger"),
        "severity": run.get("severity"),
    }
    return bool(await db.notification_events.find_one(query, {"_id": 1}))


def _decision_notification_dedupe_key(*, run: dict[str, Any], decision_id: str) -> str:
    return f"agent_decision_notification:{run.get('run_id') or run.get('_id')}:{decision_id or '-'}"


def _decision_notification_title(*, run: dict[str, Any], decision: dict[str, Any]) -> str:
    severity = _clean_optional_string(run.get("severity") or decision.get("severity")) or "info"
    trigger = _clean_optional_string(run.get("trigger")) or "scheduler"
    pool_id = _clean_optional_string(run.get("pool_id") or decision.get("pool_id")) or "unknown"
    return f"Agent 自动决策 {severity} - {pool_id} ({trigger})"


def _decision_notification_text(*, run: dict[str, Any], decision: dict[str, Any], task: dict[str, Any]) -> str:
    lines = _decision_summary_lines(run=run, decision=decision, task=task, max_reasons=4)
    return "\n".join(lines)


def _markdown_decision_text(*, title: str, run: dict[str, Any], decision: dict[str, Any], task: dict[str, Any]) -> str:
    lines = [f"### {title}", "", *_decision_summary_lines(run=run, decision=decision, task=task, max_reasons=6)]
    return "\n".join(lines)


def _decision_summary_lines(*, run: dict[str, Any], decision: dict[str, Any], task: dict[str, Any], max_reasons: int) -> list[str]:
    decision_payload = decision.get("decision") if isinstance(decision.get("decision"), dict) else {}
    metadata = run.get("trigger_metadata") if isinstance(run.get("trigger_metadata"), dict) else run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    task_id = task.get("task_id") or task.get("_id") if isinstance(task, dict) else None
    lines = [
        f"- 触发来源：{run.get('trigger') or '-'}",
        f"- 账号池：{run.get('pool_id') or decision.get('pool_id') or '-'}",
        f"- 风险等级：{run.get('severity') or decision.get('severity') or '-'}",
        f"- run_id：{run.get('run_id') or run.get('_id') or '-'}",
        f"- decision_id：{run.get('decision_id') or decision.get('decision_id') or decision.get('_id') or '-'}",
    ]
    if metadata.get("signal"):
        lines.append(f"- 事件信号：{metadata.get('signal')}")
    if task_id:
        lines.append(f"- task：{task_id} / {task.get('status') or '-'}")
    add_accounts = decision_payload.get("suggest_add_accounts") or decision_payload.get("add_accounts") or decision_payload.get("recommended_add_accounts")
    if add_accounts is not None:
        lines.append(f"- 建议补号：{add_accounts}")
    if decision_payload.get("should_alert") is not None:
        lines.append(f"- 是否建议告警：{bool(decision_payload.get('should_alert'))}")
    lines.append(f"- 是否需要人工确认：{_requires_human_confirm(run=run, decision=decision, task=task)}")
    summary = _clean_optional_string(run.get("summary") or decision.get("summary") or decision.get("headline"))
    if summary:
        lines.extend(["", f"摘要：{_truncate(summary, 500)}"])
    reasons = decision.get("reasons") if isinstance(decision.get("reasons"), list) else decision_payload.get("reasons") if isinstance(decision_payload.get("reasons"), list) else []
    if reasons:
        lines.append("")
        lines.append("核心依据：")
        for reason in reasons[:max_reasons]:
            lines.append(f"- {_truncate(str(reason), 240)}")
    actions = decision.get("suggested_actions") if isinstance(decision.get("suggested_actions"), list) else decision_payload.get("suggested_actions") if isinstance(decision_payload.get("suggested_actions"), list) else []
    if actions:
        lines.append("")
        lines.append("建议动作：")
        for action in actions[:max_reasons]:
            lines.append(f"- {_truncate(str(action), 240)}")
    return lines


def _task_from_run_or_decision(*, run: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    agent = run.get("agent") if isinstance(run.get("agent"), dict) else decision.get("agent") if isinstance(decision.get("agent"), dict) else {}
    task = agent.get("task") if isinstance(agent.get("task"), dict) else {}
    return task if isinstance(task, dict) else {}


def _requires_human_confirm(*, run: dict[str, Any], decision: dict[str, Any], task: dict[str, Any]) -> bool:
    decision_payload = decision.get("decision") if isinstance(decision.get("decision"), dict) else {}
    return bool(
        run.get("requires_human_confirm")
        or decision.get("requires_human_confirm")
        or decision_payload.get("manual_review_required")
        or (task.get("requires_human_confirm") if isinstance(task, dict) else False)
    )


async def _mark_run_decision_notification(
    db: AsyncIOMotorDatabase,
    *,
    run_id: str,
    notification_event_id: str | None,
    delivery: dict[str, Any],
    sent: bool,
) -> None:
    await db[AGENT_RUNS_COLLECTION].update_one(
        {"_id": run_id},
        {
            "$set": {
                "decision_notification": {
                    "status": "sent" if sent else "failed",
                    "notification_event_id": notification_event_id,
                    "delivery": delivery,
                    "updated_at": now_utc(),
                },
                "updated_at": now_utc(),
            }
        },
    )


async def _list_decision_notification_views(
    db: AsyncIOMotorDatabase,
    *,
    status: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"event_type": AGENT_DECISION_EVENT_TYPE, "source": AGENT_ALERT_DISPATCH_SOURCE}
    normalized_status = _clean_optional_string(status)
    if normalized_status and normalized_status not in {"sent", "success"}:
        query["status"] = normalized_status
    elif normalized_status in {"sent", "success"}:
        query["status"] = {"$in": ["success", "partial"]}
    events = [item async for item in db.notification_events.find(query).sort("created_at", -1).limit(limit)]
    return [await _decision_notification_view(db, event) for event in events]


async def _count_decision_notifications(db: AsyncIOMotorDatabase, *, status: str | None) -> int:
    query: dict[str, Any] = {"event_type": AGENT_DECISION_EVENT_TYPE, "source": AGENT_ALERT_DISPATCH_SOURCE}
    normalized_status = _clean_optional_string(status)
    if normalized_status and normalized_status not in {"sent", "success"}:
        query["status"] = normalized_status
    elif normalized_status in {"sent", "success"}:
        query["status"] = {"$in": ["success", "partial"]}
    return await db.notification_events.count_documents(query)


async def _decision_notification_view(db: AsyncIOMotorDatabase, event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
    event_id = _clean_optional_string(event.get("_id") or event.get("id"))
    deliveries = await _load_notification_deliveries(db, event_id)
    return {
        "notification_kind": "agent_loop_decision",
        "task_id": payload.get("task_id"),
        "pool_id": payload.get("pool_id"),
        "site_id": payload.get("site_id"),
        "task_status": None,
        "severity": event.get("severity"),
        "alert_status": "sent" if event.get("status") in {"success", "partial"} else event.get("status"),
        "alert_title": event.get("title"),
        "alert_content": event.get("text"),
        "alert_draft": {},
        "source_decision_id": payload.get("decision_id"),
        "notification_event_id": event_id,
        "notification_event": serialize_doc(event),
        "deliveries": deliveries,
        "delivery_status": event.get("status"),
        "error": event.get("error"),
        "created_at": event.get("created_at"),
        "updated_at": event.get("updated_at"),
        "alert_sent_at": event.get("finished_at") or event.get("created_at"),
        "run_id": payload.get("run_id") or event.get("resource_id"),
        "trigger": payload.get("trigger"),
    }


def _markdown_alert_text(*, title: str, content: str, task: dict[str, Any], draft: dict[str, Any]) -> str:
    severity = _clean_optional_string(draft.get("severity") or task.get("severity")) or "warning"
    draft_pool = draft.get("pool") if isinstance(draft.get("pool"), dict) else {}
    pool_id = _clean_optional_string(task.get("pool_id") or draft_pool.get("pool_id")) or "unknown"
    lines = [
        f"### {title}",
        "",
        f"- Risk: {severity}",
        f"- Pool: {pool_id}",
        f"- Task: {task.get('task_id') or task.get('_id')}",
        "",
        content,
    ]
    return "\n".join(lines)


def _state_history_entry(
    *,
    from_status: Any,
    to_status: Any,
    reason: str,
    run_id: str | None,
    decision_id: str | None,
    scheduler_tick_id: str | None,
    notification_event_id: str | None,
) -> dict[str, Any]:
    return {
        "from_status": _clean_optional_string(from_status),
        "to_status": _clean_optional_string(to_status),
        "reason": reason,
        "run_id": run_id,
        "decision_id": decision_id,
        "scheduler_tick_id": scheduler_tick_id,
        "notification_event_id": notification_event_id,
        "changed_at": now_utc(),
    }


def _policy_denied(reason: str) -> dict[str, Any]:
    return {"allowed": False, "reason": reason}


async def _notification_view(db: AsyncIOMotorDatabase, task: dict[str, Any]) -> dict[str, Any]:
    draft = task.get("alert_draft") if isinstance(task.get("alert_draft"), dict) else {}
    draft_pool = draft.get("pool") if isinstance(draft.get("pool"), dict) else {}
    notification_event_id = _clean_optional_string(task.get("alert_notification_event_id"))
    event = await _load_notification_event(db, notification_event_id)
    deliveries = await _load_notification_deliveries(db, notification_event_id)
    delivery_status = _delivery_status(event=event, deliveries=deliveries, task=task)
    return {
        "task_id": task.get("task_id") or task.get("_id"),
        "pool_id": task.get("pool_id") or draft_pool.get("pool_id"),
        "site_id": task.get("site_id") or draft_pool.get("site_id"),
        "task_status": task.get("status"),
        "severity": draft.get("severity") or task.get("severity"),
        "alert_status": task.get("alert_status") or draft.get("status"),
        "alert_title": draft.get("title"),
        "alert_content": draft.get("content"),
        "alert_draft": draft,
        "source_decision_id": draft.get("source_decision_id") or task.get("current_decision_id"),
        "notification_event_id": notification_event_id,
        "notification_event": event,
        "deliveries": deliveries,
        "delivery_status": delivery_status,
        "error": task.get("alert_error") or (event or {}).get("error"),
        "created_at": draft.get("created_at") or task.get("created_at"),
        "updated_at": task.get("updated_at"),
        "alert_sent_at": task.get("alert_sent_at"),
    }


async def _load_notification_event(db: AsyncIOMotorDatabase, event_id: str | None) -> dict[str, Any] | None:
    if not event_id:
        return None
    document = await db.notification_events.find_one({"_id": event_id})
    return serialize_doc(document) if document else None


async def _load_notification_deliveries(db: AsyncIOMotorDatabase, event_id: str | None) -> list[dict[str, Any]]:
    if not event_id:
        return []
    items = [item async for item in db.notification_deliveries.find({"notification_event_id": event_id}).sort("created_at", -1).limit(20)]
    return serialize_doc(items)


def _delivery_status(*, event: dict[str, Any] | None, deliveries: list[dict[str, Any]], task: dict[str, Any]) -> str:
    if task.get("alert_status") == "drafted":
        return "drafted"
    if task.get("alert_status") == "failed":
        return "failed"
    if event and event.get("status"):
        return str(event.get("status"))
    if deliveries:
        if any(item.get("status") == "success" for item in deliveries):
            return "success"
        if all(item.get("status") == "failed" for item in deliveries):
            return "failed"
    return str(task.get("alert_status") or "unknown")


def _draft_policy_allows_noncritical_auto_send(draft: dict[str, Any]) -> bool:
    policy = draft.get("notification_policy") if isinstance(draft.get("notification_policy"), dict) else {}
    return bool(policy.get("allow_noncritical_auto_send") or draft.get("auto_send"))


def _draft_policy_allows_daytime_auto_send(draft: dict[str, Any]) -> bool:
    policy = draft.get("notification_policy") if isinstance(draft.get("notification_policy"), dict) else {}
    return str(policy.get("daytime") or policy.get("daytime_policy") or "").strip() in {"auto_send", "critical_auto_send"}


def _allows_night_critical_before_human(*, draft: dict[str, Any], severity: str) -> bool:
    if severity != "critical" or not _is_night_window(now_utc()):
        return False
    policy = draft.get("notification_policy") if isinstance(draft.get("notification_policy"), dict) else {}
    value = str(policy.get("night") or policy.get("night_policy") or policy.get("night_critical_policy") or "").strip()
    return value in {"critical_auto_send", "critical_auto_send_with_audit", "auto_send"}


def _is_night_window(value: datetime) -> bool:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    # The product is currently operated in China time; keep this explicit until
    # a per-site timezone setting exists.
    china_time = value.astimezone(UTC) + timedelta(hours=8)
    return china_time.hour >= 22 or china_time.hour < 8


def _positive_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
        return number if number > 0 else default
    except (TypeError, ValueError):
        return default


def _severity_rank(value: Any) -> int:
    return SEVERITY_RANK.get(str(value or "").strip().lower(), 0)


def _decision_notification_triggers(settings: Any) -> set[str]:
    configured = getattr(settings, "decision_notification_triggers", None)
    if not isinstance(configured, list):
        return set(DEFAULT_DECISION_NOTIFICATION_TRIGGERS)
    values = {_clean_optional_string(item) for item in configured}
    return {item for item in values if item} or set(DEFAULT_DECISION_NOTIFICATION_TRIGGERS)


def _truncate(value: str, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)]}..."


def _actor_id(actor: dict[str, Any] | None) -> str | None:
    actor_id = actor.get("_id") if actor else None
    return str(actor_id) if actor_id is not None else None


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _new_id() -> str:
    return secrets.token_hex(12)
