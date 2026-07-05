from __future__ import annotations

from datetime import timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.modules.agent.controller import run_agent_controller
from app.modules.agent.memory import create_agent_run, fail_agent_run, finish_agent_run
from app.modules.agent.tasks import TASK_STATUS_OBSERVING, TASK_STATUS_REVIEW_DUE, TASK_STATUS_WAITING_HUMAN, mark_due_agent_tasks_review_due, review_agent_task
from app.modules.agent.triggers import TRIGGER_SCHEDULER_REVIEW_DUE, TRIGGER_SCHEDULER_TASK_DUE
from app.utils import now_utc, serialize_doc


MAX_TASK_FOLLOWUP_FAILURES = 3
DEFAULT_TASK_FOLLOWUP_RETRY_MINUTES = 15


async def process_mark_review_due_tasks(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await mark_due_agent_tasks_review_due(
        db,
        limit=_positive_int(getattr(settings, "max_tasks_per_tick", 5), default=5),
        actor=actor,
    )


async def process_due_review_tasks(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    limit = _positive_int(getattr(settings, "max_tasks_per_tick", 5), default=5)
    query = {
        "$or": [{"owner_scope": "agent"}, {"owner_scope": {"$exists": False}}],
        "status": TASK_STATUS_REVIEW_DUE,
    }
    candidates = [item async for item in db.agent_tasks.find(query).sort("review_after", 1).limit(limit)]
    reviewed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in candidates:
        task_id = str(item.get("task_id") or item.get("_id") or "")
        if not task_id:
            continue
        decision_id = _clean_optional_string(item.get("current_decision_id"))
        run: dict[str, Any] | None = None
        try:
            run = await create_agent_run(
                db,
                trigger=TRIGGER_SCHEDULER_REVIEW_DUE,
                actor=actor,
                pool_id=_clean_optional_string(item.get("pool_id")),
                site_id=_clean_optional_string(item.get("site_id")),
                conversation_id=_clean_optional_string(item.get("conversation_id")),
                metadata={
                    "trigger_reason": "task review_after reached",
                    "trigger_source": "agent_scheduler",
                    "scheduler_tick_id": scheduler_tick_id,
                    "task_id": task_id,
                    "current_decision_id": decision_id,
                },
            )
            task = await review_agent_task(db, task_id=task_id, run_id=str(run.get("run_id")), actor=actor)
            task_view = task if isinstance(task, dict) else {}
            report = {
                "run_id": run.get("run_id"),
                "conversation_id": run.get("conversation_id"),
                "trigger": TRIGGER_SCHEDULER_REVIEW_DUE,
                "severity": task_view.get("severity"),
                "headline": "Scheduler reviewed an Agent task.",
                "summary": "Scheduler reviewed an Agent task after review_after reached.",
                "agent": {"task": task_view},
            }
            await finish_agent_run(db, run_id=str(run.get("run_id")), report=report, decision_id=decision_id)
            if task:
                reviewed.append({"task_id": task_id, "run_id": run.get("run_id"), "status": task.get("status"), "last_review": task.get("last_review")})
            else:
                skipped.append({"task_id": task_id, "reason": "task_not_found"})
        except Exception as exc:  # noqa: BLE001 - one task must not stop the whole tick.
            if run and run.get("run_id"):
                await fail_agent_run(db, run_id=str(run.get("run_id")), error=str(exc) or exc.__class__.__name__)
            skipped.append({"task_id": task_id, "reason": str(exc) or exc.__class__.__name__})
    return {"total": len(candidates), "reviewed": reviewed, "skipped": skipped}


async def process_due_observing_tasks(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current_time = now_utc()
    limit = _positive_int(getattr(settings, "max_tasks_per_tick", 5), default=5)
    lock_ttl_seconds = _task_lock_ttl_seconds(settings)
    query = {
        "$or": [{"owner_scope": "agent"}, {"owner_scope": {"$exists": False}}],
        "status": TASK_STATUS_OBSERVING,
        "next_check_at": {"$lte": current_time},
        "$and": [
            {
                "$or": [
                    {"scheduler_lock": {"$exists": False}},
                    {"scheduler_lock.expires_at": {"$lte": current_time}},
                ]
            }
        ],
    }
    candidates = [item async for item in db.agent_tasks.find(query).sort("next_check_at", 1).limit(limit)]
    processed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in candidates:
        result = await run_task_followup(
            db,
            task=item,
            trigger=TRIGGER_SCHEDULER_TASK_DUE,
            scheduler_tick_id=scheduler_tick_id,
            lock_ttl_seconds=lock_ttl_seconds,
            actor=actor,
        )
        if result.get("processed"):
            processed.append(result)
        else:
            skipped.append(result)
    return {"total": len(candidates), "processed": processed, "skipped": skipped}


async def run_task_followup(
    db: AsyncIOMotorDatabase,
    *,
    task: dict[str, Any],
    trigger: str,
    scheduler_tick_id: str | None = None,
    lock_ttl_seconds: int = 300,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    task_id = str(task.get("task_id") or task.get("_id") or "")
    pool_id = _clean_optional_string(task.get("pool_id"))
    site_id = _clean_optional_string(task.get("site_id"))
    if not task_id or not pool_id:
        return {"processed": False, "task_id": task_id or None, "reason": "missing_task_or_pool_id"}
    lock_owner = f"{scheduler_tick_id or 'scheduler'}:{task_id}"
    lock = await acquire_agent_task_scheduler_lock(
        db,
        task_id=task_id,
        owner=lock_owner,
        ttl_seconds=lock_ttl_seconds,
    )
    if not lock.get("acquired"):
        return {"processed": False, "task_id": task_id, "reason": "task_lock_busy"}
    try:
        report = await run_agent_controller(
            db,
            trigger=trigger,
            user_message=None,
            pool_id=pool_id,
            conversation_id=_clean_optional_string(task.get("conversation_id")),
            task_id=task_id,
            metadata={
                "trigger_reason": "observing task next_check_at reached",
                "trigger_source": "agent_scheduler",
                "scheduler_tick_id": scheduler_tick_id,
                "task_id": task_id,
                "site_id": site_id,
                "pool_id": pool_id,
            },
            actor=actor,
        )
        await db.agent_tasks.update_one(
            {"_id": task_id},
            {
                "$set": {"scheduler_failure_count": 0, "last_scheduler_success_at": now_utc(), "updated_at": now_utc()},
                "$unset": {"last_scheduler_error": "", "last_scheduler_failed_at": ""},
            },
        )
        return {
            "processed": True,
            "task_id": task_id,
            "pool_id": pool_id,
            "run_id": report.get("run_id"),
            "decision_id": report.get("decision_id"),
            "severity": report.get("severity"),
            "task": ((report.get("agent") or {}).get("task") if isinstance(report.get("agent"), dict) else None),
        }
    except Exception as exc:  # noqa: BLE001 - one task must not stop scheduler tick.
        error = str(exc) or exc.__class__.__name__
        retry = await _mark_task_followup_failed(
            db,
            task=task,
            task_id=task_id,
            error=error,
            scheduler_tick_id=scheduler_tick_id,
        )
        return {"processed": False, "task_id": task_id, "reason": error, "retry": retry}
    finally:
        await release_agent_task_scheduler_lock(db, task_id=task_id, owner=lock_owner)


async def acquire_agent_task_scheduler_lock(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    owner: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    normalized_task_id = _clean_optional_string(task_id)
    if not normalized_task_id:
        return {"acquired": False, "reason": "task_id_required"}
    now = now_utc()
    expires_at = now + timedelta(seconds=max(30, int(ttl_seconds or 300)))
    document = await db.agent_tasks.find_one_and_update(
        {
            "_id": normalized_task_id,
            "$or": [
                {"scheduler_lock": {"$exists": False}},
                {"scheduler_lock.expires_at": {"$lte": now}},
                {"scheduler_lock.owner": owner},
            ],
        },
        {
            "$set": {
                "scheduler_lock": {
                    "owner": owner,
                    "locked_at": now,
                    "expires_at": expires_at,
                    "reason": "scheduler_task_due",
                },
                "updated_at": now,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    acquired = bool(document and ((document.get("scheduler_lock") or {}).get("owner") == owner))
    return {"acquired": acquired, "task": serialize_doc(document) if document else None}


async def release_agent_task_scheduler_lock(db: AsyncIOMotorDatabase, *, task_id: str, owner: str) -> bool:
    normalized_task_id = _clean_optional_string(task_id)
    if not normalized_task_id or not owner:
        return False
    result = await db.agent_tasks.update_one(
        {"_id": normalized_task_id, "scheduler_lock.owner": owner},
        {"$unset": {"scheduler_lock": ""}, "$set": {"updated_at": now_utc()}},
    )
    return result.modified_count > 0


def _task_lock_ttl_seconds(settings: Any) -> int:
    interval = _positive_int(getattr(settings, "scheduler_interval_seconds", 300), default=300)
    return max(60, min(interval * 2, 3600))


async def _mark_task_followup_failed(
    db: AsyncIOMotorDatabase,
    *,
    task: dict[str, Any],
    task_id: str,
    error: str,
    scheduler_tick_id: str | None,
) -> dict[str, Any]:
    now = now_utc()
    existing = await db.agent_tasks.find_one({"_id": task_id}) or task
    failure_count = _non_negative_int(existing.get("scheduler_failure_count"), default=0) + 1
    retry_allowed = failure_count < MAX_TASK_FOLLOWUP_FAILURES
    next_status = TASK_STATUS_OBSERVING if retry_allowed else TASK_STATUS_WAITING_HUMAN
    next_check_at = now + timedelta(minutes=DEFAULT_TASK_FOLLOWUP_RETRY_MINUTES * failure_count) if retry_allowed else None
    reason = (
        f"Scheduler follow-up failed; retry {failure_count}/{MAX_TASK_FOLLOWUP_FAILURES} scheduled."
        if retry_allowed
        else f"Scheduler follow-up failed {failure_count} times; waiting for human review."
    )
    set_fields: dict[str, Any] = {
        "status": next_status,
        "scheduler_failure_count": failure_count,
        "last_scheduler_error": error,
        "last_scheduler_failed_at": now,
        "updated_at": now,
    }
    unset_fields: dict[str, str] = {}
    if retry_allowed:
        set_fields["next_check_at"] = next_check_at
    else:
        set_fields["requires_human_confirm"] = True
        set_fields["human_confirm_questions"] = ["Agent 自动跟进连续失败，需要人工检查 LLM、数据源或任务状态后再恢复观察。"]
        unset_fields["next_check_at"] = ""

    update_doc: dict[str, Any] = {
        "$set": set_fields,
        "$push": {
            "state_history": {
                "from_status": existing.get("status"),
                "to_status": next_status,
                "reason": reason,
                "run_id": None,
                "decision_id": existing.get("current_decision_id"),
                "scheduler_tick_id": scheduler_tick_id,
                "changed_at": now,
            }
        },
    }
    if unset_fields:
        update_doc["$unset"] = unset_fields
    await db.agent_tasks.update_one({"_id": task_id}, update_doc)
    return {
        "failure_count": failure_count,
        "max_failures": MAX_TASK_FOLLOWUP_FAILURES,
        "retry_allowed": retry_allowed,
        "next_status": next_status,
        "next_check_at": next_check_at,
    }


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


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
