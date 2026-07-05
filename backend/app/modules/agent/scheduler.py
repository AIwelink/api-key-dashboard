from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.modules.agent.event_triggers import process_agent_event_spikes
from app.modules.agent.eval_runner import AGENT_EVAL_RUNS_COLLECTION
from app.modules.agent.long_term_memory import process_due_memory_summaries
from app.modules.agent.memory import AGENT_RUNS_COLLECTION
from app.modules.agent.notification_dispatcher import process_agent_alert_drafts, process_agent_decision_notifications
from app.modules.agent.patrol import process_pool_patrols
from app.modules.agent.settings import get_agent_scheduler_runtime_settings
from app.modules.agent.task_scheduler import process_due_observing_tasks, process_due_review_tasks, process_mark_review_due_tasks
from app.modules.agent.tasks import (
    AGENT_TASKS_COLLECTION,
    TASK_STATUS_ALERT_DRAFTED,
    TASK_STATUS_OBSERVING,
    TASK_STATUS_REVIEW_DUE,
    TASK_STATUS_WAITING_HUMAN,
)
from app.modules.agent.triggers import SCHEDULER_TRIGGERS
from app.utils import now_utc, serialize_doc


logger = logging.getLogger("app.agent_scheduler")

AGENT_SCHEDULER_TICKS_COLLECTION = "agent_scheduler_ticks"
AGENT_SCHEDULER_LOCKS_COLLECTION = "agent_scheduler_locks"
AGENT_SCHEDULER_LOCK_ID = "agent_scheduler_loop"
AGENT_SCHEDULER_TICK_SCHEMA_VERSION = "agent_scheduler_tick.v1"
DEFAULT_SCHEDULER_INTERVAL_SECONDS = 300


async def start_agent_scheduler(app: Any) -> None:
    """Start the background Agent scheduler loop for this FastAPI process."""

    existing = getattr(app.state, "agent_scheduler_task", None)
    if existing and not existing.done():
        return
    db = getattr(app.state, "agent_scheduler_db", None)
    if db is None:
        raise RuntimeError("app.state.agent_scheduler_db is required before starting Agent scheduler")
    app.state.agent_scheduler_task = asyncio.create_task(_agent_scheduler_loop(app), name="agent-scheduler-loop")
    logger.info("agent_scheduler_started")


async def stop_agent_scheduler(app: Any) -> None:
    """Stop the background Agent scheduler loop if it is running."""

    task = getattr(app.state, "agent_scheduler_task", None)
    if not task:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    app.state.agent_scheduler_task = None
    logger.info("agent_scheduler_stopped")


async def _agent_scheduler_loop(app: Any) -> None:
    db = getattr(app.state, "agent_scheduler_db", None)
    if db is None:
        logger.warning("agent_scheduler_loop_missing_db")
        return
    while True:
        interval = DEFAULT_SCHEDULER_INTERVAL_SECONDS
        try:
            settings = await get_agent_scheduler_runtime_settings(db)
            interval = _positive_int(getattr(settings, "scheduler_interval_seconds", DEFAULT_SCHEDULER_INTERVAL_SECONDS), default=DEFAULT_SCHEDULER_INTERVAL_SECONDS)
            if bool(getattr(settings, "agent_loop_enabled", False)):
                await asyncio.wait_for(
                    run_agent_scheduler_tick(db, reason="timer"),
                    timeout=_tick_timeout_seconds(settings),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - background loop must not kill the app.
            logger.exception("agent_scheduler_loop_error error=%s", exc)
        await asyncio.sleep(max(30, interval))


async def run_agent_scheduler_tick(
    db: AsyncIOMotorDatabase,
    *,
    reason: str = "timer",
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    started_at = now_utc()
    tick_id = _new_id()
    settings = await get_agent_scheduler_runtime_settings(db)
    settings_view = _settings_view(settings)

    if not bool(settings.agent_loop_enabled):
        return await _save_tick(
            db,
            tick_id=tick_id,
            reason=reason,
            status="skipped",
            started_at=started_at,
            settings=settings_view,
            processed={},
            errors=[],
            skip_reason="agent_loop_disabled",
            actor=actor,
        )

    lock = await acquire_agent_scheduler_lock(db, ttl_seconds=_lock_ttl_seconds(settings), owner=tick_id)
    if not lock.get("acquired"):
        return await _save_tick(
            db,
            tick_id=tick_id,
            reason=reason,
            status="skipped",
            started_at=started_at,
            settings=settings_view,
            processed={},
            errors=[],
            skip_reason="scheduler_lock_busy",
            actor=actor,
        )

    processed: dict[str, Any] = {}
    errors: list[dict[str, Any]] = []
    status = "success"
    try:
        processed["review_due_mark"] = await _capture_processor(
            "review_due_mark",
            errors,
            process_mark_review_due_tasks(db, settings=settings, actor=actor),
        )
        processed["review_due_tasks"] = await _capture_processor(
            "review_due_tasks",
            errors,
            process_due_review_tasks(db, settings=settings, scheduler_tick_id=tick_id, actor=actor),
        )
        processed["due_observing_tasks"] = await _capture_processor(
            "due_observing_tasks",
            errors,
            process_due_observing_tasks(db, settings=settings, scheduler_tick_id=tick_id, actor=actor),
        )
        processed["event_spikes"] = await _capture_processor(
            "event_spikes",
            errors,
            process_event_spikes(db, settings=settings, scheduler_tick_id=tick_id, actor=actor),
        )
        processed["pool_patrols"] = await _capture_processor(
            "pool_patrols",
            errors,
            process_pool_patrols(db, settings=settings, scheduler_tick_id=tick_id, actor=actor),
        )
        processed["memory_summaries"] = await _capture_processor(
            "memory_summaries",
            errors,
            process_memory_summaries(db, settings=settings, scheduler_tick_id=tick_id, actor=actor),
        )
        processed["decision_notifications"] = await _capture_processor(
            "decision_notifications",
            errors,
            process_decision_notifications(db, settings=settings, scheduler_tick_id=tick_id, actor=actor),
        )
        processed["alert_drafts"] = await _capture_processor(
            "alert_drafts",
            errors,
            process_alert_drafts(db, settings=settings, scheduler_tick_id=tick_id, actor=actor),
        )
        if errors:
            status = "partial"
    except Exception as exc:  # noqa: BLE001 - scheduler failures must be recorded and released.
        status = "failed"
        errors.append({"processor": "scheduler_tick", "error": str(exc) or exc.__class__.__name__})
    finally:
        await release_agent_scheduler_lock(db, owner=tick_id)

    return await _save_tick(
        db,
        tick_id=tick_id,
        reason=reason,
        status=status,
        started_at=started_at,
        settings=settings_view,
        processed=processed,
        errors=errors,
        actor=actor,
    )


async def get_agent_scheduler_status(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    settings = await get_agent_scheduler_runtime_settings(db)
    latest_tick = await db[AGENT_SCHEDULER_TICKS_COLLECTION].find_one({}, sort=[("started_at", -1)])
    latest_error_tick = await db[AGENT_SCHEDULER_TICKS_COLLECTION].find_one(
        {
            "$or": [
                {"status": {"$in": ["failed", "partial"]}},
                {"errors.0": {"$exists": True}},
            ]
        },
        sort=[("started_at", -1)],
    )
    lock = await db[AGENT_SCHEDULER_LOCKS_COLLECTION].find_one({"_id": AGENT_SCHEDULER_LOCK_ID})
    now = now_utc()
    lock_active = bool(lock and _datetime_gt(lock.get("expires_at"), now))
    task_summary = await _scheduler_task_summary(db, now=now)
    latest_auto_run = await db[AGENT_RUNS_COLLECTION].find_one(
        {"trigger": {"$in": list(SCHEDULER_TRIGGERS)}},
        sort=[("started_at", -1), ("created_at", -1)],
    )
    latest_review_task = await db[AGENT_TASKS_COLLECTION].find_one(
        {"last_review.reviewed_at": {"$exists": True}},
        sort=[("last_review.reviewed_at", -1), ("updated_at", -1)],
    )
    latest_eval_run = await db[AGENT_EVAL_RUNS_COLLECTION].find_one({}, sort=[("started_at", -1)])
    return {
        "enabled": bool(getattr(settings, "agent_loop_enabled", False)),
        "settings": _settings_view(settings),
        "running": lock_active,
        "lock": serialize_doc(lock) if lock else None,
        "latest_tick": serialize_doc(latest_tick) if latest_tick else None,
        "latest_error_tick": serialize_doc(latest_error_tick) if latest_error_tick else None,
        "task_summary": task_summary,
        "patrol_summary": _latest_patrol_summary_view(latest_tick, settings=settings),
        "latest_auto_trigger": serialize_doc(_latest_auto_trigger_view(latest_auto_run)),
        "latest_review_result": serialize_doc(_latest_review_result_view(latest_review_task)),
        "latest_eval_run": serialize_doc(_latest_eval_run_view(latest_eval_run)),
    }


async def list_agent_scheduler_ticks(
    db: AsyncIOMotorDatabase,
    *,
    status: str | None = None,
    reason: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if _clean_optional_string(status):
        query["status"] = _clean_optional_string(status)
    if _clean_optional_string(reason):
        query["reason"] = _clean_optional_string(reason)
    normalized_limit = max(1, min(int(limit or 50), 200))
    items = [item async for item in db[AGENT_SCHEDULER_TICKS_COLLECTION].find(query).sort("started_at", -1).limit(normalized_limit)]
    total = await db[AGENT_SCHEDULER_TICKS_COLLECTION].count_documents(query)
    return {"items": serialize_doc(items), "total": total}


async def set_agent_scheduler_enabled(
    db: AsyncIOMotorDatabase,
    *,
    enabled: bool,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now_utc()
    actor_id = _actor_id(actor)
    await db.agent_llm_settings.update_one(
        {"_id": "agent_llm"},
        {
            "$set": {
                "agent_loop_enabled": bool(enabled),
                "loop_enabled": bool(enabled),
                "updated_by": actor_id,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now, "created_by": actor_id},
        },
        upsert=True,
    )
    return await get_agent_scheduler_status(db)


async def acquire_agent_scheduler_lock(
    db: AsyncIOMotorDatabase,
    *,
    ttl_seconds: int,
    owner: str | None = None,
) -> dict[str, Any]:
    now = now_utc()
    lock_owner = owner or _new_id()
    expires_at = now + timedelta(seconds=max(30, int(ttl_seconds or 300)))
    try:
        document = await db[AGENT_SCHEDULER_LOCKS_COLLECTION].find_one_and_update(
            {
                "_id": AGENT_SCHEDULER_LOCK_ID,
                "$or": [
                    {"expires_at": {"$lte": now}},
                    {"expires_at": {"$exists": False}},
                    {"owner": lock_owner},
                ],
            },
            {
                "$set": {
                    "owner": lock_owner,
                    "locked_at": now,
                    "expires_at": expires_at,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        document = await db[AGENT_SCHEDULER_LOCKS_COLLECTION].find_one({"_id": AGENT_SCHEDULER_LOCK_ID})
    acquired = bool(document and document.get("owner") == lock_owner)
    return {"acquired": acquired, "owner": lock_owner if acquired else None, "lock": serialize_doc(document) if document else None}


async def release_agent_scheduler_lock(db: AsyncIOMotorDatabase, *, owner: str) -> bool:
    if not owner:
        return False
    result = await db[AGENT_SCHEDULER_LOCKS_COLLECTION].delete_one({"_id": AGENT_SCHEDULER_LOCK_ID, "owner": owner})
    return result.deleted_count > 0


async def process_event_spikes(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await process_agent_event_spikes(db, settings=settings, scheduler_tick_id=scheduler_tick_id, actor=actor)


async def process_memory_summaries(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await process_due_memory_summaries(db, settings=settings, scheduler_tick_id=scheduler_tick_id, actor=actor)


async def process_alert_drafts(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await process_agent_alert_drafts(db, settings=settings, scheduler_tick_id=scheduler_tick_id, actor=actor)


async def process_decision_notifications(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await process_agent_decision_notifications(db, settings=settings, scheduler_tick_id=scheduler_tick_id, actor=actor)


async def _capture_processor(name: str, errors: list[dict[str, Any]], awaitable: Any) -> dict[str, Any]:
    try:
        return await awaitable
    except Exception as exc:  # noqa: BLE001 - collect and continue with later processors.
        errors.append({"processor": name, "error": str(exc) or exc.__class__.__name__})
        return {"ok": False, "error": str(exc) or exc.__class__.__name__}


async def _save_tick(
    db: AsyncIOMotorDatabase,
    *,
    tick_id: str,
    reason: str,
    status: str,
    started_at: Any,
    settings: dict[str, Any],
    processed: dict[str, Any],
    errors: list[dict[str, Any]],
    skip_reason: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    finished_at = now_utc()
    document = {
        "_id": tick_id,
        "schema_version": AGENT_SCHEDULER_TICK_SCHEMA_VERSION,
        "tick_id": tick_id,
        "reason": str(reason or "timer"),
        "status": status,
        "skip_reason": skip_reason,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": _duration_ms(started_at, finished_at),
        "settings": settings,
        "processed": processed,
        "errors": errors,
        "created_by": _actor_id(actor),
        "created_at": started_at,
        "updated_at": finished_at,
    }
    await db[AGENT_SCHEDULER_TICKS_COLLECTION].insert_one(document)
    return serialize_doc(document)


def _settings_view(settings: Any) -> dict[str, Any]:
    return {
        "agent_loop_enabled": bool(getattr(settings, "agent_loop_enabled", False)),
        "scheduler_interval_seconds": _positive_int(getattr(settings, "scheduler_interval_seconds", DEFAULT_SCHEDULER_INTERVAL_SECONDS), default=DEFAULT_SCHEDULER_INTERVAL_SECONDS),
        "max_tasks_per_tick": _positive_int(getattr(settings, "max_tasks_per_tick", 5), default=5),
        "max_pool_patrols_per_tick": _non_negative_int(getattr(settings, "max_pool_patrols_per_tick", 3), default=3),
        "patrol_enabled": bool(getattr(settings, "patrol_enabled", False)),
        "pool_patrol_interval_minutes": _positive_int(getattr(settings, "pool_patrol_interval_minutes", 30), default=30),
        "pool_patrol_cooldown_minutes": _non_negative_int(getattr(settings, "pool_patrol_cooldown_minutes", 30), default=30),
        "required_patrol_pool_ids": _string_list(getattr(settings, "required_patrol_pool_ids", [])),
        "excluded_agent_pool_ids": _string_list(getattr(settings, "excluded_agent_pool_ids", [])),
        "max_event_triggers_per_tick": _non_negative_int(getattr(settings, "max_event_triggers_per_tick", 3), default=3),
        "max_concurrent_runs": _positive_int(getattr(settings, "max_concurrent_runs", 1), default=1),
        "task_cooldown_minutes": _non_negative_int(getattr(settings, "task_cooldown_minutes", 10), default=10),
        "event_trigger_cooldown_minutes": _non_negative_int(getattr(settings, "event_trigger_cooldown_minutes", 15), default=15),
        "daily_memory_enabled": bool(getattr(settings, "daily_memory_enabled", True)),
        "weekly_memory_enabled": bool(getattr(settings, "weekly_memory_enabled", True)),
        "max_memory_summaries_per_tick": _non_negative_int(getattr(settings, "max_memory_summaries_per_tick", 3), default=3),
        "memory_summary_catchup_enabled": bool(getattr(settings, "memory_summary_catchup_enabled", True)),
        "notification_dispatch_enabled": bool(getattr(settings, "notification_dispatch_enabled", False)),
        "decision_notification_enabled": bool(getattr(settings, "decision_notification_enabled", False)),
        "decision_notification_min_severity": _clean_optional_string(getattr(settings, "decision_notification_min_severity", None)) or "warning",
        "decision_notification_triggers": _string_list(getattr(settings, "decision_notification_triggers", [])),
        "decision_notification_cooldown_minutes": _non_negative_int(getattr(settings, "decision_notification_cooldown_minutes", 30), default=30),
    }


async def _scheduler_task_summary(db: AsyncIOMotorDatabase, *, now: Any) -> dict[str, Any]:
    due_observing_query = {
        "owner_scope": "agent",
        "status": TASK_STATUS_OBSERVING,
        "next_check_at": {"$lte": now},
    }
    due_review_query = {
        "owner_scope": "agent",
        "status": {"$in": [TASK_STATUS_OBSERVING, TASK_STATUS_WAITING_HUMAN, TASK_STATUS_ALERT_DRAFTED, TASK_STATUS_REVIEW_DUE]},
        "review_after": {"$lte": now},
    }
    waiting_human_query = {
        "owner_scope": "agent",
        "status": TASK_STATUS_WAITING_HUMAN,
    }
    alert_drafted_query = {
        "owner_scope": "agent",
        "status": TASK_STATUS_ALERT_DRAFTED,
    }
    return {
        "due_observing_count": await db[AGENT_TASKS_COLLECTION].count_documents(due_observing_query),
        "due_review_count": await db[AGENT_TASKS_COLLECTION].count_documents(due_review_query),
        "waiting_human_count": await _count_distinct_task_pools(db, waiting_human_query),
        "alert_drafted_count": await _count_distinct_task_pools(db, alert_drafted_query),
        "waiting_human_task_count": await db[AGENT_TASKS_COLLECTION].count_documents(waiting_human_query),
        "alert_drafted_task_count": await db[AGENT_TASKS_COLLECTION].count_documents(alert_drafted_query),
    }


async def _count_distinct_task_pools(db: AsyncIOMotorDatabase, query: dict[str, Any]) -> int:
    keys: set[str] = set()
    async for item in db[AGENT_TASKS_COLLECTION].find(query, {"pool_id": 1, "site_id": 1, "task_id": 1}):
        pool_key = _clean_optional_string(item.get("pool_id"))
        if not pool_key:
            pool_key = f"site:{_clean_optional_string(item.get('site_id'))}" if _clean_optional_string(item.get("site_id")) else f"task:{item.get('task_id') or item.get('_id')}"
        keys.add(pool_key)
    return len(keys)


def _latest_auto_trigger_view(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    metadata = run.get("trigger_metadata") if isinstance(run.get("trigger_metadata"), dict) else {}
    return {
        "run_id": run.get("run_id") or run.get("_id"),
        "trigger": run.get("trigger"),
        "status": run.get("status"),
        "pool_id": run.get("pool_id"),
        "task_id": metadata.get("task_id"),
        "event_trigger_id": metadata.get("event_trigger_id"),
        "signal": metadata.get("signal"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "summary": run.get("summary"),
    }


def _latest_review_result_view(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if not task:
        return None
    review = task.get("last_review") if isinstance(task.get("last_review"), dict) else {}
    return {
        "task_id": task.get("task_id") or task.get("_id"),
        "pool_id": task.get("pool_id"),
        "review_result": review.get("review_result"),
        "next_status": review.get("next_status"),
        "summary": review.get("summary"),
        "reviewed_at": review.get("reviewed_at"),
        "memory_id": review.get("memory_id"),
    }


def _latest_patrol_summary_view(tick: dict[str, Any] | None, *, settings: Any) -> dict[str, Any]:
    processed = tick.get("processed") if isinstance(tick, dict) and isinstance(tick.get("processed"), dict) else {}
    patrols = processed.get("pool_patrols") if isinstance(processed.get("pool_patrols"), dict) else {}
    return {
        "enabled": bool(getattr(settings, "patrol_enabled", False)),
        "latest_tick_id": tick.get("tick_id") if isinstance(tick, dict) else None,
        "latest_tick_at": (tick.get("finished_at") or tick.get("started_at")) if isinstance(tick, dict) else None,
        "implemented": bool(patrols.get("implemented")) if patrols else False,
        "selected": _non_negative_int(patrols.get("selected"), default=0) if patrols else 0,
        "processed": _non_negative_int(patrols.get("total_processed", patrols.get("processed")), default=0) if patrols else 0,
        "skipped": _non_negative_int(patrols.get("total_skipped"), default=0) if patrols else 0,
        "errors": _non_negative_int(patrols.get("total_errors"), default=0) if patrols else 0,
        "pending": _non_negative_int(patrols.get("pending"), default=0) if patrols else 0,
        "reason": patrols.get("reason") or patrols.get("skip_reason") if patrols else None,
    }


def _latest_eval_run_view(run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not run:
        return None
    summary = run.get("summary") if isinstance(run.get("summary"), dict) else {}
    return {
        "eval_run_id": run.get("eval_run_id") or run.get("_id"),
        "suite": run.get("suite"),
        "mode": run.get("mode"),
        "status": run.get("status"),
        "started_at": run.get("started_at"),
        "finished_at": run.get("finished_at"),
        "total": summary.get("total"),
        "passed": summary.get("passed"),
        "failed": summary.get("failed"),
        "score": summary.get("score"),
    }


def _lock_ttl_seconds(settings: Any) -> int:
    interval = _positive_int(getattr(settings, "scheduler_interval_seconds", DEFAULT_SCHEDULER_INTERVAL_SECONDS), default=DEFAULT_SCHEDULER_INTERVAL_SECONDS)
    return max(60, min(interval * 2, 3600))


def _tick_timeout_seconds(settings: Any) -> int:
    interval = _positive_int(getattr(settings, "scheduler_interval_seconds", DEFAULT_SCHEDULER_INTERVAL_SECONDS), default=DEFAULT_SCHEDULER_INTERVAL_SECONDS)
    return max(60, min(interval, 3600))


def _not_implemented_result(trigger: str, reason: str) -> dict[str, Any]:
    return {"ok": True, "implemented": False, "trigger": trigger, "reason": reason, "processed": 0}


def _duration_ms(started_at: Any, finished_at: Any) -> int | None:
    try:
        return int((finished_at - started_at).total_seconds() * 1000)
    except Exception:
        return None


def _datetime_gt(left: Any, right: Any) -> bool:
    left_dt = _as_aware_utc(left)
    right_dt = _as_aware_utc(right)
    return bool(left_dt and right_dt and left_dt > right_dt)


def _as_aware_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
        return number if number > 0 else default
    except (TypeError, ValueError):
        return default


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
        return number if number >= 0 else default
    except (TypeError, ValueError):
        return default


def _actor_id(actor: dict[str, Any] | None) -> str | None:
    actor_id = actor.get("_id") if actor else None
    return str(actor_id) if actor_id is not None else None


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_optional_string(item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _new_id() -> str:
    return secrets.token_urlsafe(16)
