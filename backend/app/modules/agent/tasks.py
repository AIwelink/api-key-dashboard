from __future__ import annotations

import secrets
from datetime import datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.long_term_memory import MEMORY_TYPE_OPERATOR_FEEDBACK, save_agent_memory_summary
from app.utils import now_utc, serialize_doc


AGENT_TASKS_COLLECTION = "agent_tasks"
AGENT_TASK_SCHEMA_VERSION = "agent_task.v1"
DEFAULT_TASK_TITLE = "Agent continuous operation task"
DEFAULT_TASK_CHANGE_REASON = "Agent updated the task status from this run."

TASK_STATUS_OPEN = "open"
TASK_STATUS_OBSERVING = "observing"
TASK_STATUS_WAITING_HUMAN = "waiting_human"
TASK_STATUS_ALERT_DRAFTED = "alert_drafted"
TASK_STATUS_REVIEW_DUE = "review_due"
TASK_STATUS_CLOSED = "closed"
TASK_STATUS_FAILED = "failed"

ALLOWED_TASK_STATUSES = {
    TASK_STATUS_OPEN,
    TASK_STATUS_OBSERVING,
    TASK_STATUS_WAITING_HUMAN,
    TASK_STATUS_ALERT_DRAFTED,
    TASK_STATUS_REVIEW_DUE,
    TASK_STATUS_CLOSED,
    TASK_STATUS_FAILED,
}

ACTIVE_TASK_STATUSES = {
    TASK_STATUS_OPEN,
    TASK_STATUS_OBSERVING,
    TASK_STATUS_WAITING_HUMAN,
    TASK_STATUS_ALERT_DRAFTED,
    TASK_STATUS_REVIEW_DUE,
}

ALLOWED_TASK_TRANSITIONS = {
    TASK_STATUS_OPEN: {
        TASK_STATUS_OBSERVING,
        TASK_STATUS_WAITING_HUMAN,
        TASK_STATUS_ALERT_DRAFTED,
        TASK_STATUS_CLOSED,
    },
    TASK_STATUS_OBSERVING: {
        TASK_STATUS_OBSERVING,
        TASK_STATUS_WAITING_HUMAN,
        TASK_STATUS_ALERT_DRAFTED,
        TASK_STATUS_REVIEW_DUE,
        TASK_STATUS_CLOSED,
    },
    TASK_STATUS_WAITING_HUMAN: {
        TASK_STATUS_OBSERVING,
        TASK_STATUS_ALERT_DRAFTED,
        TASK_STATUS_REVIEW_DUE,
        TASK_STATUS_CLOSED,
    },
    TASK_STATUS_ALERT_DRAFTED: {
        TASK_STATUS_WAITING_HUMAN,
        TASK_STATUS_REVIEW_DUE,
        TASK_STATUS_CLOSED,
    },
    TASK_STATUS_REVIEW_DUE: {
        TASK_STATUS_OBSERVING,
        TASK_STATUS_WAITING_HUMAN,
        TASK_STATUS_CLOSED,
    },
}


async def resolve_agent_task(
    db: AsyncIOMotorDatabase,
    *,
    intent: dict[str, Any],
    site_id: str | None,
    pool_id: str | None,
    conversation_id: str | None,
) -> dict[str, Any] | None:
    """Find the active Agent-owned task that should receive this turn."""

    normalized_pool_id = _clean_optional_string(pool_id or intent.get("target_pool_id"))
    normalized_site_id = _clean_optional_string(site_id)
    if not normalized_pool_id and not normalized_site_id:
        return None

    query: dict[str, Any] = {
        "status": {"$in": list(ACTIVE_TASK_STATUSES)},
        "$or": [{"owner_scope": "agent"}, {"owner_scope": {"$exists": False}}],
    }
    if normalized_pool_id:
        query["pool_id"] = normalized_pool_id
    elif normalized_site_id:
        query["site_id"] = normalized_site_id

    normalized_conversation_id = _clean_optional_string(conversation_id)
    if normalized_conversation_id:
        query["$and"] = [
            {
                "$or": [
                    {"conversation_id": normalized_conversation_id},
                    {"linked_conversation_ids": normalized_conversation_id},
                    {"conversation_id": None},
                    {"conversation_id": {"$exists": False}},
                ]
            }
        ]

    task = await _tasks(db).find_one(query, sort=[("updated_at", -1), ("created_at", -1)])
    return serialize_doc(task) if task else None


async def create_or_update_agent_task(
    db: AsyncIOMotorDatabase,
    *,
    task: dict[str, Any] | None,
    decision: dict[str, Any] | None,
    step_result: dict[str, Any] | None,
    run_id: str,
    site_id: str | None = None,
    pool_id: str | None = None,
    conversation_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Create or update an Agent-owned task from a decision or controller step.

    A task is a sustained operational issue, not a single run. It may link to
    many runs, decisions, steps, and conversations over time.
    """

    decision = decision if isinstance(decision, dict) else {}
    step_result = step_result if isinstance(step_result, dict) else {}
    should_update = bool(decision or step_result.get("task_update") or step_result.get("requires_human_confirm"))
    if not should_update:
        return task

    now = now_utc()
    normalized_run_id = _clean_optional_string(run_id)
    normalized_conversation_id = _clean_optional_string(conversation_id)
    decision_id = _clean_optional_string(decision.get("decision_id"))
    current_status = _current_task_status(task)
    next_status, task_update_warnings = _resolve_next_status(
        current_status=current_status,
        decision=decision,
        step_result=step_result,
    )
    if not task and next_status == TASK_STATUS_CLOSED:
        return None
    if not task:
        task = await _find_active_task_for_pool(db, site_id=site_id, pool_id=pool_id)
        if task:
            current_status = _current_task_status(task)
            next_status, task_update_warnings = _resolve_next_status(
                current_status=current_status,
                decision=decision,
                step_result=step_result,
            )
    severity = _clean_optional_string(decision.get("severity") or step_result.get("severity")) or "watch"
    summary = _task_summary(decision=decision, step_result=step_result)
    reason = _task_change_reason(decision=decision, step_result=step_result, fallback=summary)
    title = _clean_optional_string(decision.get("headline") or step_result.get("title")) or DEFAULT_TASK_TITLE
    refill_plan_fields = {
        "suggested_account_type": _clean_optional_string(decision.get("suggested_account_type")),
        "suggested_add_count": _int_or_none(decision.get("suggested_add_count")),
        "suggested_refill_options": decision.get("suggested_refill_options")
        if isinstance(decision.get("suggested_refill_options"), list)
        else [],
        "refill_plan_summary": _clean_optional_string(decision.get("refill_plan_summary")),
    }
    status_fields = _status_fields(
        status=next_status,
        decision=decision,
        step_result=step_result,
        severity=severity,
        reason=reason,
        now=now,
        pool_context={
            "site_id": _clean_optional_string(site_id) or (task.get("site_id") if isinstance(task, dict) else None),
            "pool_id": _clean_optional_string(pool_id) or (task.get("pool_id") if isinstance(task, dict) else None),
        },
    )
    state_entry = _state_history_entry(
        from_status=task.get("status") if isinstance(task, dict) else None,
        to_status=next_status,
        reason=reason,
        run_id=normalized_run_id,
        decision_id=decision_id,
    )
    run_entry = _run_history_entry(
        run_id=normalized_run_id,
        decision_id=decision_id,
        severity=severity,
        status=next_status,
        recorded_at=now,
    )

    if task and task.get("task_id"):
        task_id = str(task["task_id"])
        updates = {
            "schema_version": AGENT_TASK_SCHEMA_VERSION,
            "owner_scope": "agent",
            "status": next_status,
            "severity": severity,
            "title": title,
            "summary": summary,
            "current_decision_id": decision_id or task.get("current_decision_id"),
            "current_run_id": normalized_run_id,
            "conversation_id": normalized_conversation_id or task.get("conversation_id"),
            "task_update_warnings": task_update_warnings,
            "updated_at": now,
            **refill_plan_fields,
        }
        updates.update(status_fields)

        push_updates: dict[str, Any] = {
            "state_history": state_entry,
            "run_history": run_entry,
        }
        if decision_id:
            push_updates["decision_history"] = {
                "run_id": normalized_run_id,
                "decision_id": decision_id,
                "severity": severity,
                "recorded_at": now,
            }

        update_doc: dict[str, Any] = {"$set": updates, "$push": push_updates}
        add_to_set = _linked_add_to_set(
            run_id=normalized_run_id,
            decision_id=decision_id,
            conversation_id=normalized_conversation_id,
        )
        if add_to_set:
            update_doc["$addToSet"] = add_to_set

        await _tasks(db).update_one({"_id": task_id}, update_doc)
        document = await _tasks(db).find_one({"_id": task_id})
        return serialize_doc(document) if document else None

    task_id = _new_id()
    document = {
        "_id": task_id,
        "schema_version": AGENT_TASK_SCHEMA_VERSION,
        "task_id": task_id,
        "owner_scope": "agent",
        "source": "agent_task_state_machine",
        "site_id": _clean_optional_string(site_id),
        "pool_id": _clean_optional_string(pool_id),
        "task_type": _clean_optional_string(step_result.get("task_type")) or "pool_risk_monitoring",
        "status": next_status,
        "severity": severity,
        "title": title,
        "summary": summary,
        "current_decision_id": decision_id,
        "current_run_id": normalized_run_id,
        "conversation_id": normalized_conversation_id,
        "opened_at": now,
        "updated_at": now,
        "closed_at": None,
        "next_check_at": None,
        "review_after": None,
        "next_observation_focus": None,
        "requires_human_confirm": False,
        "human_confirm_status": None,
        "human_confirm_questions": [],
        "alert_status": None,
        "alert_reason": None,
        "alert_draft": None,
        "close_reason": None,
        "task_update_warnings": task_update_warnings,
        **refill_plan_fields,
        "linked_run_ids": [normalized_run_id] if normalized_run_id else [],
        "linked_decision_ids": [decision_id] if decision_id else [],
        "linked_conversation_ids": [normalized_conversation_id] if normalized_conversation_id else [],
        "run_history": [run_entry],
        "decision_history": [
            {
                "run_id": normalized_run_id,
                "decision_id": decision_id,
                "severity": severity,
                "recorded_at": now,
            }
        ] if decision_id else [],
        "state_history": [state_entry],
        "created_by": _actor_id(actor),
        "created_at": now,
    }
    document.update(status_fields)
    await _tasks(db).insert_one(document)
    return serialize_doc(document)


async def close_agent_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    reason: str,
    run_id: str | None = None,
    decision_id: str | None = None,
) -> dict[str, Any] | None:
    existing = await _tasks(db).find_one({"_id": task_id})
    if not existing:
        return None
    normalized_reason = _clean_optional_string(reason)
    if not normalized_reason:
        raise ValueError("close reason is required")

    now = now_utc()
    normalized_run_id = _clean_optional_string(run_id)
    normalized_decision_id = _clean_optional_string(decision_id)
    update_doc: dict[str, Any] = {
        "$set": {
            "schema_version": AGENT_TASK_SCHEMA_VERSION,
            "owner_scope": "agent",
            "status": TASK_STATUS_CLOSED,
            "closed_at": now,
            "close_reason": normalized_reason,
            "next_check_at": None,
            "review_after": None,
            "updated_at": now,
        },
        "$push": {
            "state_history": _state_history_entry(
                from_status=existing.get("status"),
                to_status=TASK_STATUS_CLOSED,
                reason=normalized_reason,
                run_id=normalized_run_id,
                decision_id=normalized_decision_id,
            )
        },
    }
    add_to_set = _linked_add_to_set(
        run_id=normalized_run_id,
        decision_id=normalized_decision_id,
        conversation_id=None,
    )
    if add_to_set:
        update_doc["$addToSet"] = add_to_set

    await _tasks(db).update_one({"_id": task_id}, update_doc)
    document = await _tasks(db).find_one({"_id": task_id})
    return serialize_doc(document) if document else None


async def fail_agent_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    error: str,
    run_id: str | None = None,
    decision_id: str | None = None,
) -> dict[str, Any] | None:
    existing = await _tasks(db).find_one({"_id": task_id})
    if not existing:
        return None

    now = now_utc()
    normalized_error = _clean_optional_string(error) or "Agent task failed"
    normalized_run_id = _clean_optional_string(run_id)
    normalized_decision_id = _clean_optional_string(decision_id)
    update_doc: dict[str, Any] = {
        "$set": {
            "schema_version": AGENT_TASK_SCHEMA_VERSION,
            "owner_scope": "agent",
            "status": TASK_STATUS_FAILED,
            "failed_at": now,
            "error": normalized_error,
            "next_check_at": None,
            "review_after": None,
            "updated_at": now,
        },
        "$push": {
            "state_history": _state_history_entry(
                from_status=existing.get("status"),
                to_status=TASK_STATUS_FAILED,
                reason=normalized_error,
                run_id=normalized_run_id,
                decision_id=normalized_decision_id,
            )
        },
    }
    add_to_set = _linked_add_to_set(
        run_id=normalized_run_id,
        decision_id=normalized_decision_id,
        conversation_id=None,
    )
    if add_to_set:
        update_doc["$addToSet"] = add_to_set

    await _tasks(db).update_one({"_id": task_id}, update_doc)
    document = await _tasks(db).find_one({"_id": task_id})
    return serialize_doc(document) if document else None


async def transition_agent_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    target_status: str,
    reason: str,
    run_id: str | None = None,
    decision_id: str | None = None,
    actor: dict[str, Any] | None = None,
    extra_step_result: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    existing = await _tasks(db).find_one({"_id": task_id})
    if not existing:
        return None

    current_status = _current_task_status(existing)
    normalized_target = _clean_optional_string(target_status)
    normalized_reason = _clean_optional_string(reason)
    if normalized_target not in ALLOWED_TASK_STATUSES:
        raise ValueError(f"Invalid task status: {target_status}")
    if not normalized_reason:
        raise ValueError("transition reason is required")
    if not _is_allowed_transition(current_status, normalized_target):
        raise ValueError(f"Task transition {current_status}->{normalized_target} is not allowed")

    now = now_utc()
    normalized_run_id = _clean_optional_string(run_id)
    normalized_decision_id = _clean_optional_string(decision_id)
    step_result = {
        **(extra_step_result if isinstance(extra_step_result, dict) else {}),
        "next_status": normalized_target,
        "reason": normalized_reason,
    }
    updates = {
        "schema_version": AGENT_TASK_SCHEMA_VERSION,
        "owner_scope": "agent",
        "status": normalized_target,
        "current_run_id": normalized_run_id or existing.get("current_run_id"),
        "current_decision_id": normalized_decision_id or existing.get("current_decision_id"),
        "updated_at": now,
    }
    updates.update(
        _status_fields(
            status=normalized_target,
            decision={},
            step_result=step_result,
            severity=_clean_optional_string(existing.get("severity")) or "watch",
            reason=normalized_reason,
            now=now,
            pool_context=existing,
        )
    )
    update_doc: dict[str, Any] = {
        "$set": updates,
        "$push": {
            "state_history": _state_history_entry(
                from_status=current_status,
                to_status=normalized_target,
                reason=normalized_reason,
                run_id=normalized_run_id,
                decision_id=normalized_decision_id,
            )
        },
    }
    add_to_set = _linked_add_to_set(
        run_id=normalized_run_id,
        decision_id=normalized_decision_id,
        conversation_id=None,
    )
    if add_to_set:
        update_doc["$addToSet"] = add_to_set
    actor_id = _actor_id(actor)
    if actor_id:
        update_doc.setdefault("$set", {})["updated_by"] = actor_id

    await _tasks(db).update_one({"_id": existing["_id"]}, update_doc)
    document = await _tasks(db).find_one({"_id": existing["_id"]})
    return serialize_doc(document) if document else None


async def mark_agent_task_review_due(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    reason: str = "Task review is due.",
    run_id: str | None = None,
    decision_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    return await transition_agent_task(
        db,
        task_id=task_id,
        target_status=TASK_STATUS_REVIEW_DUE,
        reason=reason,
        run_id=run_id,
        decision_id=decision_id,
        actor=actor,
    )


async def mark_due_agent_tasks_review_due(
    db: AsyncIOMotorDatabase,
    *,
    limit: int = 50,
    now: datetime | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Move due observing/waiting/alert tasks into review_due.

    This is a service helper for the future scheduler. It does not start a
    scheduler by itself.
    """

    current_time = now or now_utc()
    query = {
        "$or": [{"owner_scope": "agent"}, {"owner_scope": {"$exists": False}}],
        "status": {"$in": [TASK_STATUS_OBSERVING, TASK_STATUS_WAITING_HUMAN, TASK_STATUS_ALERT_DRAFTED]},
        "review_after": {"$lte": current_time},
    }
    normalized_limit = max(1, min(int(limit or 50), 200))
    candidates = [item async for item in _tasks(db).find(query).sort("review_after", 1).limit(normalized_limit)]
    updated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in candidates:
        task_id = str(item.get("task_id") or item.get("_id") or "")
        if not task_id:
            continue
        try:
            task = await mark_agent_task_review_due(
                db,
                task_id=task_id,
                reason="review_after reached; task moved to review_due.",
                decision_id=_clean_optional_string(item.get("current_decision_id")),
                actor=actor,
            )
            if task:
                updated.append({"task_id": task_id, "status": task.get("status")})
        except ValueError as exc:
            skipped.append({"task_id": task_id, "reason": str(exc)})
    return {"updated": updated, "skipped": skipped, "total": len(candidates)}


async def review_agent_task(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    run_id: str | None = None,
    actor: dict[str, Any] | None = None,
    review_window_hours: int = 24,
) -> dict[str, Any] | None:
    existing = await _tasks(db).find_one({"_id": task_id}) or await _tasks(db).find_one({"task_id": task_id})
    if not existing:
        return None

    decision_id = _task_review_decision_id(existing)
    if not decision_id:
        raise ValueError("task has no decision to review")

    current_status = _current_task_status(existing)
    if current_status != TASK_STATUS_REVIEW_DUE and _is_allowed_transition(current_status, TASK_STATUS_REVIEW_DUE):
        existing = await _tasks(db).find_one({"_id": existing["_id"]}) or existing
        await mark_agent_task_review_due(
            db,
            task_id=str(existing.get("task_id") or existing.get("_id")),
            reason="Manual task review requested.",
            run_id=run_id,
            decision_id=decision_id,
            actor=actor,
        )
        existing = await _tasks(db).find_one({"_id": existing["_id"]}) or existing

    from app.modules.agent.reviewer import review_agent_decision

    review = await review_agent_decision(
        db,
        decision_id=decision_id,
        task_id=str(existing.get("task_id") or existing.get("_id")),
        run_id=run_id,
        actor=actor,
        review_window_hours=review_window_hours,
    )
    next_status = _task_status_after_review(review=review)
    reason = _task_review_transition_reason(review=review, next_status=next_status)
    updated = await transition_agent_task(
        db,
        task_id=str(existing.get("task_id") or existing.get("_id")),
        target_status=next_status,
        reason=reason,
        run_id=run_id,
        decision_id=decision_id,
        actor=actor,
        extra_step_result={
            "review_after_hours": 24 if next_status == TASK_STATUS_OBSERVING else None,
            "human_confirm_questions": _review_human_questions(review) if next_status == TASK_STATUS_WAITING_HUMAN else None,
        },
    )
    if updated:
        reviewed_at = now_utc()
        update_doc: dict[str, Any] = {
            "$set": {
                "last_review": {
                    "decision_id": decision_id,
                    "review_result": review.get("review_result"),
                    "memory_id": review.get("memory_id"),
                    "next_status": next_status,
                    "summary": review.get("summary"),
                    "reviewed_at": reviewed_at,
                },
                "updated_at": reviewed_at,
            }
        }
        if _clean_optional_string(review.get("memory_id")):
            update_doc["$addToSet"] = {"linked_memory_ids": _clean_optional_string(review.get("memory_id"))}
        await _tasks(db).update_one({"_id": existing["_id"]}, update_doc)
        refreshed = await _tasks(db).find_one({"_id": existing["_id"]})
        updated = serialize_doc(refreshed) if refreshed else updated
    return {"task": updated, "review": review}


async def append_agent_task_feedback(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str,
    feedback: str,
    feedback_type: str | None = None,
    target_status: str | None = None,
    reason: str | None = None,
    run_id: str | None = None,
    conversation_id: str | None = None,
    actor: dict[str, Any] | None = None,
    write_memory: bool = True,
    memory_id: str | None = None,
) -> dict[str, Any] | None:
    existing = await _tasks(db).find_one({"_id": task_id})
    if not existing:
        return None

    now = now_utc()
    normalized_feedback = _clean_optional_string(feedback)
    if not normalized_feedback:
        raise ValueError("feedback is required")
    normalized_type = _normalize_feedback_type(feedback_type, normalized_feedback)
    current_status = _current_task_status(existing)
    normalized_run_id = _clean_optional_string(run_id)
    normalized_conversation_id = _clean_optional_string(conversation_id)
    normalized_reason = _clean_optional_string(reason) or _feedback_reason(normalized_type, normalized_feedback)

    memory = None
    linked_memory_id = _clean_optional_string(memory_id)
    if write_memory:
        memory = await save_agent_memory_summary(
            db,
            payload={
                "memory_id": linked_memory_id,
                "site_id": _clean_optional_string(existing.get("site_id")),
                "pool_id": _clean_optional_string(existing.get("pool_id")),
                "memory_type": MEMORY_TYPE_OPERATOR_FEEDBACK,
                "period_start": now,
                "period_end": now,
                "summary": _feedback_memory_summary(normalized_type, normalized_feedback),
                "facts": [normalized_feedback],
                "patterns": _feedback_patterns_for_type(normalized_type),
                "lessons": _feedback_lessons_for_type(normalized_type, normalized_feedback),
                "risk_baselines": {},
                "created_by": "operator",
                "source_run_ids": [normalized_run_id] if normalized_run_id else [],
                "source_decision_ids": [_clean_optional_string(existing.get("current_decision_id"))]
                if _clean_optional_string(existing.get("current_decision_id"))
                else [],
                "metadata": {
                    "generator": "agent_task_feedback.v1",
                    "task_id": existing.get("task_id"),
                    "feedback_type": normalized_type,
                    "conversation_id": normalized_conversation_id,
                },
            },
            actor=actor,
        )
        linked_memory_id = _clean_optional_string(memory.get("memory_id")) if isinstance(memory, dict) else linked_memory_id

    requested_status = _clean_optional_string(target_status)
    next_status = _feedback_target_status(
        current_status=current_status,
        feedback_type=normalized_type,
        requested_status=requested_status,
    )
    warnings: list[str] = []
    if requested_status and requested_status not in ALLOWED_TASK_STATUSES:
        warnings.append(f"Requested feedback target status {requested_status} is invalid; task status kept.")
        next_status = None
    if next_status and not _is_allowed_transition(current_status, next_status):
        warnings.append(f"Feedback task transition {current_status}->{next_status} is not allowed; task status kept.")
        next_status = None

    feedback_entry = {
        "feedback_id": _new_id(),
        "feedback_type": normalized_type,
        "content": normalized_feedback,
        "target_status": next_status,
        "requested_status": requested_status,
        "reason": normalized_reason,
        "run_id": normalized_run_id,
        "conversation_id": normalized_conversation_id,
        "memory_id": linked_memory_id,
        "actor_id": _actor_id(actor),
        "created_at": now,
    }
    updates: dict[str, Any] = {
        "schema_version": AGENT_TASK_SCHEMA_VERSION,
        "owner_scope": "agent",
        "last_human_feedback": feedback_entry,
        "updated_at": now,
    }
    if current_status == TASK_STATUS_WAITING_HUMAN:
        updates["human_confirm_status"] = "answered"
        updates["human_confirm_answered_at"] = now
    if warnings:
        updates["task_update_warnings"] = warnings

    push_updates: dict[str, Any] = {"human_feedback_history": feedback_entry}
    if next_status:
        updates["status"] = next_status
        updates.update(
            _status_fields(
                status=next_status,
                decision={},
                step_result={
                    "next_status": next_status,
                    "reason": normalized_reason,
                    "should_alert": next_status == TASK_STATUS_ALERT_DRAFTED,
                    "alert_reason": normalized_reason,
                    "alert_draft": _feedback_alert_draft(existing=existing, feedback=normalized_feedback, now=now)
                    if next_status == TASK_STATUS_ALERT_DRAFTED
                    else None,
                },
                severity=_clean_optional_string(existing.get("severity")) or "watch",
                reason=normalized_reason,
                now=now,
                pool_context=existing,
            )
        )
        push_updates["state_history"] = _state_history_entry(
            from_status=current_status,
            to_status=next_status,
            reason=normalized_reason,
            run_id=normalized_run_id,
            decision_id=_clean_optional_string(existing.get("current_decision_id")),
        )

    update_doc: dict[str, Any] = {"$set": updates, "$push": push_updates}
    add_to_set = _linked_add_to_set(
        run_id=normalized_run_id,
        decision_id=None,
        conversation_id=normalized_conversation_id,
    )
    if linked_memory_id:
        add_to_set["linked_memory_ids"] = linked_memory_id
    if add_to_set:
        update_doc["$addToSet"] = add_to_set

    await _tasks(db).update_one({"_id": existing["_id"]}, update_doc)
    document = await _tasks(db).find_one({"_id": existing["_id"]})
    serialized = serialize_doc(document) if document else None
    if serialized is not None:
        serialized["feedback_result"] = {
            "feedback_id": feedback_entry["feedback_id"],
            "feedback_type": normalized_type,
            "target_status": next_status,
            "memory_id": linked_memory_id,
            "warnings": warnings,
        }
    return serialized


async def list_agent_tasks(
    db: AsyncIOMotorDatabase,
    *,
    pool_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query: dict[str, Any] = {"$or": [{"owner_scope": "agent"}, {"owner_scope": {"$exists": False}}]}
    if _clean_optional_string(pool_id):
        query["pool_id"] = _clean_optional_string(pool_id)
    if _clean_optional_string(status):
        query["status"] = _clean_optional_string(status)
    normalized_limit = max(1, min(int(limit or 50), 200))
    items = [item async for item in _tasks(db).find(query).sort("updated_at", -1).limit(normalized_limit)]
    total = await _tasks(db).count_documents(query)
    return {"items": serialize_doc(items), "total": total}


async def get_agent_task(db: AsyncIOMotorDatabase, *, task_id: str) -> dict[str, Any] | None:
    normalized_task_id = _clean_optional_string(task_id)
    if not normalized_task_id:
        return None
    document = await _tasks(db).find_one(
        {
            "_id": normalized_task_id,
            "$or": [{"owner_scope": "agent"}, {"owner_scope": {"$exists": False}}],
        }
    )
    return serialize_doc(document) if document else None


async def append_agent_task_step_link(
    db: AsyncIOMotorDatabase,
    *,
    task_id: str | None,
    step_id: str | None,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    normalized_task_id = _clean_optional_string(task_id)
    normalized_step_id = _clean_optional_string(step_id)
    if not normalized_task_id or not normalized_step_id:
        return None
    await _tasks(db).update_one(
        {"_id": normalized_task_id},
        {
            "$set": {
                "schema_version": AGENT_TASK_SCHEMA_VERSION,
                "owner_scope": "agent",
                "updated_at": now_utc(),
            },
            "$addToSet": {
                "linked_step_ids": normalized_step_id,
                **({"linked_run_ids": _clean_optional_string(run_id)} if _clean_optional_string(run_id) else {}),
            },
        },
    )
    document = await _tasks(db).find_one({"_id": normalized_task_id})
    return serialize_doc(document) if document else None


def resolve_next_task_status(
    *,
    current_status: str | None = None,
    decision: dict[str, Any] | None = None,
    step_result: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Public task-state resolver used by controller/step-loop callers.

    It does not write data. It only applies the same transition rules used by
    create_or_update_agent_task.
    """

    normalized_current = current_status if current_status in ALLOWED_TASK_STATUSES else TASK_STATUS_OPEN
    next_status, warnings = _resolve_next_status(
        current_status=normalized_current,
        decision=decision if isinstance(decision, dict) else {},
        step_result=step_result if isinstance(step_result, dict) else {},
    )
    return {"current_status": normalized_current, "next_status": next_status, "task_update_warnings": warnings}


def build_task_schedule(
    *,
    status: str,
    decision: dict[str, Any] | None = None,
    step_result: dict[str, Any] | None = None,
    severity: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return schedule fields such as next_check_at and review_after."""

    current_time = now or now_utc()
    normalized_decision = decision if isinstance(decision, dict) else {}
    normalized_step = step_result if isinstance(step_result, dict) else {}
    fields = _status_fields(
        status=status if status in ALLOWED_TASK_STATUSES else TASK_STATUS_OBSERVING,
        decision=normalized_decision,
        step_result=normalized_step,
        severity=_clean_optional_string(severity or normalized_decision.get("severity") or normalized_step.get("severity")) or "watch",
        reason=_task_change_reason(decision=normalized_decision, step_result=normalized_step, fallback=None),
        now=current_time,
        pool_context=None,
    )
    return {
        "next_check_at": fields.get("next_check_at"),
        "review_after": fields.get("review_after"),
        "next_observation_focus": fields.get("next_observation_focus"),
        "requires_human_confirm": fields.get("requires_human_confirm"),
    }


def build_alert_draft_from_decision(
    *,
    decision: dict[str, Any] | None,
    step_result: dict[str, Any] | None = None,
    severity: str | None = None,
    reason: str | None = None,
    pool_context: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a draft-only alert payload without sending notifications."""

    normalized_decision = decision if isinstance(decision, dict) else {}
    normalized_step = step_result if isinstance(step_result, dict) else {}
    return _alert_draft(
        decision=normalized_decision,
        step_result=normalized_step,
        severity=_clean_optional_string(severity or normalized_decision.get("severity") or normalized_step.get("severity")) or "warning",
        reason=_clean_optional_string(reason)
        or _task_change_reason(decision=normalized_decision, step_result=normalized_step, fallback=None),
        now=now or now_utc(),
        pool_context=pool_context,
    )


def _resolve_next_status(
    *,
    current_status: str,
    decision: dict[str, Any],
    step_result: dict[str, Any],
) -> tuple[str, list[str]]:
    candidate_status, source, warnings = _candidate_next_status(decision=decision, step_result=step_result)
    if _is_allowed_transition(current_status, candidate_status):
        return candidate_status, _task_update_warnings(decision=decision, step_result=step_result, extra=warnings)

    if source == "requested":
        downgraded_status = TASK_STATUS_OBSERVING
        if _is_allowed_transition(current_status, downgraded_status):
            warnings.append(
                f"Requested task transition {current_status}->{candidate_status} is not allowed; downgraded to observing."
            )
            return downgraded_status, _task_update_warnings(decision=decision, step_result=step_result, extra=warnings)
        warnings.append(
            f"Requested task transition {current_status}->{candidate_status} is not allowed; kept current status."
        )
        return current_status, _task_update_warnings(decision=decision, step_result=step_result, extra=warnings)

    warnings.append(f"Derived task transition {current_status}->{candidate_status} is not allowed; kept current status.")
    return current_status, _task_update_warnings(decision=decision, step_result=step_result, extra=warnings)


def _candidate_next_status(*, decision: dict[str, Any], step_result: dict[str, Any]) -> tuple[str, str, list[str]]:
    warnings: list[str] = []
    requested = _requested_next_status(step_result)
    if requested:
        if requested in ALLOWED_TASK_STATUSES:
            return requested, "requested", warnings
        warnings.append(f"Requested task status {requested} is invalid; downgraded to observing.")
        return TASK_STATUS_OBSERVING, "requested", warnings

    if _requires_human_confirm(decision=decision, step_result=step_result):
        return TASK_STATUS_WAITING_HUMAN, "derived", warnings
    if decision.get("should_alert") or step_result.get("should_alert"):
        return TASK_STATUS_ALERT_DRAFTED, "derived", warnings

    severity = str(decision.get("severity") or step_result.get("severity") or "").lower()
    data_gap_count = len(_list_of_strings(decision.get("data_gaps") or step_result.get("data_gaps")))
    if data_gap_count and severity in {"danger", "critical"}:
        return TASK_STATUS_WAITING_HUMAN, "derived", warnings
    if data_gap_count:
        return TASK_STATUS_OBSERVING, "derived", warnings
    if _suggests_account_action(decision=decision):
        return TASK_STATUS_OBSERVING, "derived", warnings
    if severity in {"danger", "critical", "warning", "watch"}:
        return TASK_STATUS_OBSERVING, "derived", warnings
    if severity == "healthy":
        return TASK_STATUS_CLOSED, "derived", warnings
    return TASK_STATUS_OPEN, "derived", warnings


def _requested_next_status(step_result: dict[str, Any]) -> str | None:
    value = step_result.get("next_status") or _nested_get(step_result, "task_update", "next_status")
    return _clean_optional_string(value)


def _is_allowed_transition(from_status: str, to_status: str) -> bool:
    if to_status == TASK_STATUS_FAILED:
        return True
    if from_status == to_status:
        return from_status in ACTIVE_TASK_STATUSES
    return to_status in ALLOWED_TASK_TRANSITIONS.get(from_status, set())


def _current_task_status(task: dict[str, Any] | None) -> str:
    if isinstance(task, dict):
        status = _clean_optional_string(task.get("status"))
        if status in ALLOWED_TASK_STATUSES:
            return status
    return TASK_STATUS_OPEN


def _requires_human_confirm(*, decision: dict[str, Any], step_result: dict[str, Any]) -> bool:
    return bool(
        decision.get("requires_human_confirm")
        or decision.get("manual_review_required")
        or step_result.get("requires_human_confirm")
    )


def _task_update_warnings(
    *,
    decision: dict[str, Any],
    step_result: dict[str, Any],
    extra: list[str] | None = None,
) -> list[str]:
    warnings: list[str] = []
    warnings.extend(_list_of_strings(extra))
    validator = decision.get("validator") if isinstance(decision.get("validator"), dict) else {}
    warnings.extend(_list_of_strings(validator.get("warnings")))
    warnings.extend(_list_of_strings(step_result.get("task_update_warnings")))
    warnings.extend(_list_of_strings(_nested_get(step_result, "task_update", "warnings")))

    seen: set[str] = set()
    result: list[str] = []
    for warning in warnings:
        if warning not in seen:
            seen.add(warning)
            result.append(warning)
    return result[:20]


def _suggests_account_action(*, decision: dict[str, Any]) -> bool:
    if decision.get("should_add_accounts"):
        return True
    if _int_or_none(decision.get("suggested_add_count")):
        return True
    actions = decision.get("recommended_actions")
    if not isinstance(actions, list):
        return False
    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("action_type") in {"prepare_accounts", "manual_review", "notify_draft"}:
            return True
    return False


def _normalize_feedback_type(feedback_type: str | None, feedback: str) -> str:
    explicit = _clean_optional_string(feedback_type)
    if explicit:
        return explicit
    text = feedback.lower()
    if any(token in text for token in ("已补", "补了", "已经补", "已处理", "处理了", "done", "handled")):
        return "handled"
    if any(token in text for token in ("先观察", "继续观察", "观察一下", "watch", "observe")):
        return "observe"
    if any(token in text for token in ("不需要补", "不用补", "不处理", "不用处理", "no action")):
        return "no_action"
    if any(token in text for token in ("不用发告警", "不要发告警", "不发告警", "no alert")):
        return "alert_rejected"
    if any(token in text for token in ("可以发告警", "需要告警", "发告警", "alert")):
        return "alert_approved"
    if any(token in text for token in ("不是异常", "不是异常流量", "批量任务", "正常流量", "纠正", "修正")):
        return "operator_correction"
    return "operator_feedback"


def _feedback_target_status(*, current_status: str, feedback_type: str, requested_status: str | None) -> str | None:
    if requested_status:
        return requested_status
    if feedback_type == "handled":
        return TASK_STATUS_REVIEW_DUE if current_status != TASK_STATUS_REVIEW_DUE else TASK_STATUS_OBSERVING
    if feedback_type in {"observe", "operator_correction"}:
        return TASK_STATUS_OBSERVING
    if feedback_type == "no_action":
        return TASK_STATUS_CLOSED
    if feedback_type == "alert_approved":
        return TASK_STATUS_ALERT_DRAFTED
    if feedback_type == "alert_rejected":
        return TASK_STATUS_CLOSED if current_status == TASK_STATUS_ALERT_DRAFTED else TASK_STATUS_OBSERVING
    if current_status == TASK_STATUS_WAITING_HUMAN and feedback_type == "operator_feedback":
        return TASK_STATUS_OBSERVING
    return None


def _feedback_reason(feedback_type: str, feedback: str) -> str:
    if feedback_type == "handled":
        return "Operator confirmed the issue has been handled."
    if feedback_type == "observe":
        return "Operator asked Agent to keep observing."
    if feedback_type == "no_action":
        return "Operator confirmed no further action is needed."
    if feedback_type == "alert_approved":
        return "Operator confirmed an alert draft is needed."
    if feedback_type == "alert_rejected":
        return "Operator rejected the alert."
    if feedback_type == "operator_correction":
        return "Operator corrected or supplemented the operational facts."
    return _clean_optional_string(feedback) or "Operator feedback received."


def _feedback_memory_summary(feedback_type: str, feedback: str) -> str:
    return f"Operator feedback ({feedback_type}): {_short_text(feedback, limit=180)}"


def _feedback_patterns_for_type(feedback_type: str) -> list[str]:
    if feedback_type == "operator_correction":
        return ["Operator corrected the Agent's interpretation or supplied missing operational context."]
    if feedback_type == "handled":
        return ["Operator confirmed that a previously pending operational issue was handled."]
    if feedback_type == "alert_approved":
        return ["Operator approved preparing an alert draft."]
    if feedback_type == "alert_rejected":
        return ["Operator rejected alerting for this task."]
    return []


def _feedback_lessons_for_type(feedback_type: str, feedback: str) -> list[str]:
    if feedback_type == "operator_correction":
        return [f"Prefer this operator correction in future similar judgments: {_short_text(feedback, limit=160)}"]
    if feedback_type == "no_action":
        return ["When operators explicitly reject action, do not keep escalating the same task without new evidence."]
    if feedback_type == "observe":
        return ["When operators ask to observe, continue monitoring instead of closing the task immediately."]
    return [_short_text(feedback, limit=160)]


def _task_review_decision_id(task: dict[str, Any]) -> str | None:
    direct = _clean_optional_string(task.get("current_decision_id"))
    if direct:
        return direct
    history = task.get("decision_history") if isinstance(task.get("decision_history"), list) else []
    for item in reversed(history):
        if isinstance(item, dict):
            decision_id = _clean_optional_string(item.get("decision_id"))
            if decision_id:
                return decision_id
    linked = task.get("linked_decision_ids") if isinstance(task.get("linked_decision_ids"), list) else []
    for item in reversed(linked):
        decision_id = _clean_optional_string(item)
        if decision_id:
            return decision_id
    return None


def _task_status_after_review(*, review: dict[str, Any]) -> str:
    result = _clean_optional_string(review.get("review_result")) or "insufficient_data"
    if result == "wrong_interpretation":
        return TASK_STATUS_WAITING_HUMAN
    if result == "insufficient_data":
        return TASK_STATUS_OBSERVING
    if result == "too_conservative":
        return TASK_STATUS_OBSERVING
    if result == "too_aggressive":
        return TASK_STATUS_CLOSED if _review_risk_relieved(review) else TASK_STATUS_OBSERVING
    if result == "useful":
        return TASK_STATUS_CLOSED if _review_risk_relieved(review) else TASK_STATUS_OBSERVING
    return TASK_STATUS_OBSERVING


def _review_risk_relieved(review: dict[str, Any]) -> bool:
    summary = review.get("review_pack_summary") if isinstance(review.get("review_pack_summary"), dict) else {}
    capacity_delta = summary.get("capacity_delta") if isinstance(summary.get("capacity_delta"), dict) else {}
    improved = capacity_delta.get("capacity_improved_signals") if isinstance(capacity_delta.get("capacity_improved_signals"), list) else []
    worsened = capacity_delta.get("capacity_worsened_signals") if isinstance(capacity_delta.get("capacity_worsened_signals"), list) else []
    event_summary = summary.get("event_summary_24h") if isinstance(summary.get("event_summary_24h"), dict) else {}
    high_value_event_count = _int_or_none(event_summary.get("high_value_event_count")) or 0
    total_events = _int_or_none(event_summary.get("total_events")) or 0
    if worsened:
        return False
    if improved and high_value_event_count == 0:
        return True
    if total_events == 0 and review.get("review_result") in {"useful", "too_aggressive"}:
        return True
    return False


def _task_review_transition_reason(*, review: dict[str, Any], next_status: str) -> str:
    result = _clean_optional_string(review.get("review_result")) or "insufficient_data"
    summary = _clean_optional_string(review.get("summary")) or "Task review completed."
    return f"Review result={result}; next_status={next_status}. {summary}"


def _review_human_questions(review: dict[str, Any]) -> list[str]:
    lessons = _list_of_strings(review.get("lessons"))
    gaps = _list_of_strings(review.get("data_gaps"))
    questions = []
    if lessons:
        questions.append(f"请人工确认复盘纠正是否成立：{lessons[0]}")
    if gaps:
        questions.append(f"请补充复盘缺口：{gaps[0]}")
    if not questions:
        questions.append("请人工确认本次复盘指出的解释错误，并给出后续处理意见。")
    return questions[:3]


def _feedback_alert_draft(*, existing: dict[str, Any], feedback: str, now: datetime) -> dict[str, Any]:
    severity = _clean_optional_string(existing.get("severity")) or "warning"
    pool = {
        "pool_id": _clean_optional_string(existing.get("pool_id")),
        "site_id": _clean_optional_string(existing.get("site_id")),
        "name": _clean_optional_string(existing.get("title")),
        "account_type": _clean_optional_string(existing.get("account_type")),
    }
    return {
        "channel": "dingtalk",
        "status": "drafted",
        "send_behavior": "draft_only",
        "draft_only": True,
        "auto_send": False,
        "severity": severity,
        "title": _clean_optional_string(existing.get("title")) or "Agent alert draft",
        "content": _alert_content(
            title=_clean_optional_string(existing.get("title")) or "Agent alert draft",
            severity=severity,
            pool=pool,
            evidence=[feedback],
            actions=["Operator approved preparing an alert draft."],
            requires_human_confirm=True,
            summary=feedback,
        ),
        "pool": pool,
        "evidence": [feedback],
        "recommended_actions": ["Operator approved preparing an alert draft."],
        "requires_human_confirm": True,
        "notification_policy": {
            "auto_send": False,
            "manual_confirmation_required": True,
            "use_system_notification_config": "future_stage",
            "daytime_policy": "future_stage",
            "night_critical_policy": "future_stage",
        },
        "source_decision_id": _clean_optional_string(existing.get("current_decision_id")),
        "created_at": now,
    }


def _short_text(value: Any, *, limit: int = 120) -> str:
    text = _clean_optional_string(value) or ""
    return text if len(text) <= limit else f"{text[:limit]}..."


def _status_fields(
    *,
    status: str,
    decision: dict[str, Any],
    step_result: dict[str, Any],
    severity: str,
    reason: str,
    now: datetime,
    pool_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return required fields for the current task status.

    This keeps stage-six task status invariants in one place. The Agent still
    writes only Agent-owned state and never executes account or notification
    side effects from these fields.
    """

    should_alert = bool(decision.get("should_alert") or step_result.get("should_alert"))
    fields: dict[str, Any] = {
        "requires_human_confirm": False,
        "human_confirm_status": None,
        "human_confirm_questions": [],
        "next_observation_focus": _next_observation_focus(decision=decision, step_result=step_result),
    }

    if should_alert:
        fields.update(
            {
                "alert_status": "drafted",
                "alert_reason": _alert_reason(decision=decision, step_result=step_result, fallback=reason),
                "alert_draft": _alert_draft(
                    decision=decision,
                    step_result=step_result,
                    severity=severity,
                    reason=reason,
                    now=now,
                    pool_context=pool_context,
                ),
            }
        )
    elif status != TASK_STATUS_ALERT_DRAFTED:
        fields.update({"alert_status": None, "alert_reason": None, "alert_draft": None})

    if status == TASK_STATUS_OBSERVING:
        fields.update(
            {
                "next_check_at": _next_check_at(decision=decision, step_result=step_result, severity=severity, now=now),
                "review_after": _review_after(decision=decision, step_result=step_result, now=now),
                "observation_reason": reason,
                "closed_at": None,
                "close_reason": None,
            }
        )
        if not fields["next_observation_focus"]:
            fields["next_observation_focus"] = [
                "Watch capacity runway and burst trend.",
                "Watch whether recent account events repeat or stop.",
            ]
        return fields

    if status == TASK_STATUS_WAITING_HUMAN:
        fields.update(
            {
                "requires_human_confirm": True,
                "human_confirm_status": "pending",
                "human_confirm_questions": _human_confirm_questions(
                    decision=decision,
                    step_result=step_result,
                    fallback=reason,
                ),
                "next_check_at": None,
                "review_after": None,
                "closed_at": None,
                "close_reason": None,
            }
        )
        return fields

    if status == TASK_STATUS_ALERT_DRAFTED:
        fields.update(
            {
                "alert_status": "drafted",
                "alert_reason": _alert_reason(decision=decision, step_result=step_result, fallback=reason),
                "alert_draft": _alert_draft(
                    decision=decision,
                    step_result=step_result,
                    severity=severity,
                    reason=reason,
                    now=now,
                    pool_context=pool_context,
                ),
                "review_after": _review_after(decision=decision, step_result=step_result, now=now) or now + timedelta(hours=3),
                "next_check_at": None,
                "closed_at": None,
                "close_reason": None,
            }
        )
        return fields

    if status == TASK_STATUS_REVIEW_DUE:
        fields.update(
            {
                "review_after": _explicit_datetime(decision.get("review_after") or step_result.get("review_after")) or now,
                "next_check_at": None,
                "closed_at": None,
                "close_reason": None,
            }
        )
        return fields

    if status == TASK_STATUS_CLOSED:
        fields.update(
            {
                "closed_at": now,
                "close_reason": reason,
                "next_check_at": None,
                "review_after": None,
                "requires_human_confirm": False,
                "human_confirm_status": None,
            }
        )
        return fields

    if status == TASK_STATUS_FAILED:
        fields.update(
            {
                "failed_at": now,
                "error": _clean_optional_string(step_result.get("error") or decision.get("error") or reason)
                or "Agent task failed",
                "next_check_at": None,
                "review_after": None,
                "requires_human_confirm": False,
                "human_confirm_status": None,
            }
        )
        return fields

    if status == TASK_STATUS_OPEN:
        fields.update(
            {
                "next_check_at": _explicit_datetime(decision.get("next_check_at") or step_result.get("next_check_at")),
                "review_after": _explicit_datetime(decision.get("review_after") or step_result.get("review_after")),
                "closed_at": None,
                "close_reason": None,
            }
        )
        return fields

    return fields


def _next_check_at(
    *,
    decision: dict[str, Any],
    step_result: dict[str, Any],
    severity: str,
    now: datetime,
) -> datetime:
    explicit = _explicit_datetime(decision.get("next_check_at") or step_result.get("next_check_at"))
    if explicit:
        return explicit

    minutes = _int_or_none(
        decision.get("next_check_minutes")
        or step_result.get("next_check_minutes")
        or _nested_get(step_result, "task_update", "next_check_minutes")
    )
    if minutes is not None:
        return now + timedelta(minutes=max(5, min(minutes, 24 * 60)))

    defaults = {
        "critical": 15,
        "danger": 30,
        "warning": 60,
        "watch": 120,
    }
    return now + timedelta(minutes=defaults.get(str(severity or "").lower(), 120))


def _review_after(*, decision: dict[str, Any], step_result: dict[str, Any], now: datetime) -> datetime | None:
    explicit = _explicit_datetime(decision.get("review_after") or step_result.get("review_after"))
    if explicit:
        return explicit

    hours = _int_or_none(
        decision.get("review_after_hours")
        or step_result.get("review_after_hours")
        or _nested_get(step_result, "task_update", "review_after_hours")
    )
    if hours is not None:
        return now + timedelta(hours=max(1, min(hours, 168)))

    if decision.get("should_alert") or step_result.get("should_alert"):
        return now + timedelta(hours=3)
    if _event_assessment_has_ban_burst(decision.get("event_assessment")):
        return now + timedelta(hours=6)
    if _suggests_account_action(decision=decision):
        return now + timedelta(hours=12)
    return None


def _event_assessment_has_ban_burst(value: Any) -> bool:
    return isinstance(value, dict) and bool(value.get("has_recent_ban_burst"))


def _next_observation_focus(*, decision: dict[str, Any], step_result: dict[str, Any]) -> list[str]:
    return _list_of_strings(
        decision.get("next_observation_focus")
        or step_result.get("next_observation_focus")
        or _nested_get(step_result, "task_update", "next_observation_focus")
    )


def _human_confirm_questions(*, decision: dict[str, Any], step_result: dict[str, Any], fallback: str) -> list[str]:
    questions = _list_of_strings(
        step_result.get("human_confirm_questions")
        or _nested_get(step_result, "task_update", "human_confirm_questions")
        or decision.get("follow_up_questions")
        or decision.get("human_confirm_questions")
    )
    if questions:
        return questions
    return [fallback] if _clean_optional_string(fallback) else ["Human confirmation is required before action."]


def _alert_reason(*, decision: dict[str, Any], step_result: dict[str, Any], fallback: str) -> str:
    for value in (
        step_result.get("alert_reason"),
        _nested_get(step_result, "task_update", "alert_reason"),
        decision.get("alert_reason"),
        decision.get("summary"),
        fallback,
    ):
        text = _clean_optional_string(value)
        if text:
            return text
    return "Agent drafted an alert because the current risk requires operator attention."


def _alert_draft(
    *,
    decision: dict[str, Any],
    step_result: dict[str, Any],
    severity: str,
    reason: str,
    now: datetime,
    pool_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    existing = step_result.get("alert_draft") or decision.get("alert_draft")
    if isinstance(existing, dict):
        draft = dict(existing)
    else:
        draft = {}
    pool = _alert_pool_view(decision=decision, step_result=step_result, pool_context=pool_context)
    evidence = _alert_evidence(decision=decision, step_result=step_result, fallback=reason)
    actions = _alert_actions(decision=decision, step_result=step_result)
    requires_human_confirm = _requires_human_confirm(decision=decision, step_result=step_result)
    title = _clean_optional_string(decision.get("headline")) or _alert_title(severity=severity, pool=pool)
    content = _clean_optional_string(decision.get("operator_message") or decision.get("summary")) or reason
    refill_plan_summary = _clean_optional_string(decision.get("refill_plan_summary"))
    if refill_plan_summary and refill_plan_summary not in content:
        content = f"{content}\n补号方案：{refill_plan_summary}"
    draft.setdefault("channel", "dingtalk")
    draft.setdefault("status", "drafted")
    draft.setdefault("send_behavior", "draft_only")
    draft.setdefault("draft_only", True)
    draft.setdefault("auto_send", False)
    draft.setdefault("severity", severity)
    draft.setdefault("title", title)
    draft.setdefault(
        "content",
        _alert_content(
            title=title,
            severity=severity,
            pool=pool,
            evidence=evidence,
            actions=actions,
            requires_human_confirm=requires_human_confirm,
            summary=content,
        ),
    )
    draft.setdefault("pool", pool)
    draft.setdefault("evidence", evidence)
    draft.setdefault("recommended_actions", actions)
    draft.setdefault("requires_human_confirm", requires_human_confirm)
    draft.setdefault(
        "notification_policy",
        {
            "auto_send": False,
            "manual_confirmation_required": True,
            "use_system_notification_config": "future_stage",
            "daytime_policy": "future_stage",
            "night_critical_policy": "future_stage",
        },
    )
    draft.setdefault("source_decision_id", _clean_optional_string(decision.get("decision_id")))
    draft.setdefault("created_at", now)
    return draft


def _alert_title(*, severity: str, pool: dict[str, Any]) -> str:
    pool_name = _clean_optional_string(pool.get("name") or pool.get("pool_id")) or "account pool"
    return f"Agent alert draft: {pool_name} risk={severity}"


def _alert_pool_view(
    *,
    decision: dict[str, Any],
    step_result: dict[str, Any],
    pool_context: dict[str, Any] | None,
) -> dict[str, Any]:
    context = pool_context if isinstance(pool_context, dict) else {}
    decision_pool = decision.get("pool") if isinstance(decision.get("pool"), dict) else {}
    step_pool = step_result.get("pool") if isinstance(step_result.get("pool"), dict) else {}
    return {
        "pool_id": _clean_optional_string(
            context.get("pool_id") or decision_pool.get("pool_id") or decision_pool.get("id") or step_pool.get("pool_id") or step_pool.get("id")
        ),
        "site_id": _clean_optional_string(context.get("site_id") or decision_pool.get("site_id") or step_pool.get("site_id")),
        "name": _clean_optional_string(context.get("pool_name") or context.get("name") or decision_pool.get("name") or step_pool.get("name")),
        "account_type": _clean_optional_string(context.get("account_type") or decision_pool.get("account_type") or step_pool.get("account_type")),
    }


def _alert_evidence(*, decision: dict[str, Any], step_result: dict[str, Any], fallback: str) -> list[str]:
    evidence: list[str] = []
    evidence_summary = decision.get("evidence_summary") if isinstance(decision.get("evidence_summary"), dict) else {}
    for key in ("capacity", "events", "probe", "memory"):
        evidence.extend(_list_of_strings(evidence_summary.get(key))[:3])
    evidence.extend(_list_of_strings(decision.get("main_reasons"))[:5])
    evidence.extend(_list_of_strings(decision.get("risk_factors"))[:5])
    event_assessment = decision.get("event_assessment") if isinstance(decision.get("event_assessment"), dict) else {}
    if _clean_optional_string(event_assessment.get("interpretation")):
        evidence.append(_clean_optional_string(event_assessment.get("interpretation")) or "")
    evidence.extend(_list_of_strings(step_result.get("alert_evidence"))[:5])
    if not evidence and _clean_optional_string(fallback):
        evidence.append(_clean_optional_string(fallback) or "")
    return _dedupe_strings(evidence, limit=10)


def _alert_actions(*, decision: dict[str, Any], step_result: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    recommended = decision.get("recommended_actions")
    if isinstance(recommended, list):
        for item in recommended[:8]:
            if isinstance(item, dict):
                title = _clean_optional_string(item.get("title") or item.get("action_type"))
                reason = _clean_optional_string(item.get("reason"))
                if title and reason and title != reason:
                    actions.append(f"{title}: {reason}")
                elif title:
                    actions.append(title)
            else:
                text = _clean_optional_string(item)
                if text:
                    actions.append(text)
    actions.extend(_list_of_strings(decision.get("suggested_actions"))[:8])
    actions.extend(_list_of_strings(step_result.get("alert_actions"))[:5])
    return _dedupe_strings(actions, limit=8)


def _alert_content(
    *,
    title: str,
    severity: str,
    pool: dict[str, Any],
    evidence: list[str],
    actions: list[str],
    requires_human_confirm: bool,
    summary: str,
) -> str:
    lines = [
        title,
        f"Risk severity: {severity}",
        f"Pool: {_clean_optional_string(pool.get('name') or pool.get('pool_id')) or 'unknown'}",
        f"Summary: {summary}",
    ]
    if evidence:
        lines.append("Core evidence:")
        lines.extend(f"- {item}" for item in evidence[:6])
    if actions:
        lines.append("Suggested actions:")
        lines.extend(f"- {item}" for item in actions[:5])
    lines.append(f"Human confirmation required: {'yes' if requires_human_confirm else 'no'}")
    lines.append("Draft only: this message has not been sent automatically.")
    return "\n".join(lines)


def _dedupe_strings(values: list[str], *, limit: int) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = _clean_optional_string(value)
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
        if len(result) >= limit:
            break
    return result


def _task_summary(*, decision: dict[str, Any], step_result: dict[str, Any]) -> str | None:
    for value in (
        decision.get("summary"),
        decision.get("operator_message"),
        step_result.get("thought_summary"),
        step_result.get("summary"),
    ):
        text = _clean_optional_string(value)
        if text:
            return text
    return None


def _task_change_reason(*, decision: dict[str, Any], step_result: dict[str, Any], fallback: str | None) -> str:
    for value in (
        step_result.get("reason"),
        step_result.get("thought_summary"),
        step_result.get("summary"),
        decision.get("operator_message"),
        decision.get("summary"),
        fallback,
    ):
        text = _clean_optional_string(value)
        if text:
            return text
    return DEFAULT_TASK_CHANGE_REASON


def _state_history_entry(
    *,
    from_status: Any,
    to_status: str,
    reason: str,
    run_id: str | None,
    decision_id: str | None,
) -> dict[str, Any]:
    return {
        "from_status": _clean_optional_string(from_status),
        "to_status": to_status,
        "reason": _clean_optional_string(reason) or DEFAULT_TASK_CHANGE_REASON,
        "run_id": _clean_optional_string(run_id),
        "decision_id": _clean_optional_string(decision_id),
        "changed_at": now_utc(),
    }


def _run_history_entry(
    *,
    run_id: str | None,
    decision_id: str | None,
    severity: str | None,
    status: str,
    recorded_at: Any,
) -> dict[str, Any]:
    return {
        "run_id": _clean_optional_string(run_id),
        "decision_id": _clean_optional_string(decision_id),
        "severity": _clean_optional_string(severity),
        "status": status,
        "recorded_at": recorded_at,
    }


def _linked_add_to_set(
    *,
    run_id: str | None,
    decision_id: str | None,
    conversation_id: str | None,
) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if _clean_optional_string(run_id):
        updates["linked_run_ids"] = _clean_optional_string(run_id)
    if _clean_optional_string(decision_id):
        updates["linked_decision_ids"] = _clean_optional_string(decision_id)
    if _clean_optional_string(conversation_id):
        updates["linked_conversation_ids"] = _clean_optional_string(conversation_id)
    return updates


def _tasks(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_TASKS_COLLECTION]


async def _find_active_task_for_pool(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None = None,
    pool_id: str | None = None,
) -> dict[str, Any] | None:
    normalized_pool_id = _clean_optional_string(pool_id)
    normalized_site_id = _clean_optional_string(site_id)
    if not normalized_pool_id and not normalized_site_id:
        return None
    query: dict[str, Any] = {
        "status": {"$in": list(ACTIVE_TASK_STATUSES)},
        "$or": [{"owner_scope": "agent"}, {"owner_scope": {"$exists": False}}],
    }
    if normalized_pool_id:
        query["pool_id"] = normalized_pool_id
    elif normalized_site_id:
        query["site_id"] = normalized_site_id
    document = await _tasks(db).find_one(query, sort=[("updated_at", -1), ("created_at", -1)])
    return serialize_doc(document) if document else None


def _actor_id(actor: dict[str, Any] | None) -> Any:
    actor_id = actor.get("_id") if actor else None
    return str(actor_id) if actor_id is not None else None


def _new_id() -> str:
    return secrets.token_hex(12)


def _explicit_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    text = _clean_optional_string(value)
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _list_of_strings(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = _clean_optional_string(value)
        return [text] if text else []
    if isinstance(value, list):
        items: list[str] = []
        for item in value:
            text = _clean_optional_string(item)
            if text:
                items.append(text)
        return items
    text = _clean_optional_string(value)
    return [text] if text else []


def _nested_get(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
