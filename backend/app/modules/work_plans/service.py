from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.modules.system.audit import write_audit_log
from app.modules.system.presence import list_member_presence_summaries
from app.modules.work_plans.domain import (
    SHANGHAI_TIMEZONE,
    WorkPlanConflictError,
    build_plan_drafts,
    collaboration_status,
    is_plan_manager,
    validate_update,
)
from app.modules.work_plans.schemas import WorkPlanCreate, WorkPlanUpdate
from app.utils import now_utc, serialize_doc


DEFAULT_HISTORY_LIMIT = 1_000
MAX_HISTORY_LIMIT = 4_000
MAX_READBACK_FALLBACKS = 5
MAX_SCHEDULE_PLANS = 4_000


logger = logging.getLogger(__name__)


class WorkPlanAccessError(ValueError):
    """Raised when an actor cannot use a personal work-plan operation."""


class WorkPlanNotFoundError(LookupError):
    """Raised when the requested work plan does not exist."""


class WorkPlanPermissionError(PermissionError):
    """Raised when an actor attempts to mutate another member's plan."""


def _log_create_failure(
    failure_code: str,
    error: Exception,
    *,
    plan_id: str | None = None,
) -> None:
    if plan_id is None:
        logger.error(
            "work_plan_create_failure code=%s exception_type=%s",
            failure_code,
            type(error).__name__,
        )
        return
    logger.error(
        "work_plan_create_failure code=%s plan_id=%s exception_type=%s",
        failure_code,
        plan_id,
        type(error).__name__,
    )


def require_browser_actor(actor: dict[str, Any]) -> None:
    """Personal work-plan history is available only to browser-authenticated users."""
    actor_type = actor.get("actor_type")
    actor_id = str(actor.get("_id") or "").strip()
    if ("actor_type" in actor and actor_type != "user") or not actor_id:
        raise WorkPlanAccessError("个人工作计划仅限已登录的浏览器用户访问")


async def create_work_plans(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    payload: WorkPlanCreate,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    require_browser_actor(actor)
    observed = observed_at or now_utc()
    # Draft construction performs all validation and must precede every write.
    drafts = build_plan_drafts(actor, payload, observed)
    ids = [draft["_id"] for draft in drafts]
    write_states: dict[str, str] = {}
    write_errors: dict[str, Exception] = {}

    for draft in drafts:
        plan_id = str(draft["_id"])
        try:
            update_result = await db.work_plans.update_one(
                {"_id": draft["_id"]},
                {"$setOnInsert": draft},
                upsert=True,
            )
        except Exception as exc:  # noqa: BLE001 - one date must not stop other dates.
            _log_create_failure("write", exc, plan_id=plan_id)
            write_states[plan_id] = "uncertain"
            write_errors[plan_id] = exc
            continue
        if getattr(update_result, "upserted_id", None) is not None:
            write_states[plan_id] = "created"
        else:
            write_states[plan_id] = "duplicate"

    documents_by_id: dict[str, dict[str, Any]] = {}
    bulk_read_failed = False
    try:
        cursor = db.work_plans.find({"_id": {"$in": ids}})
        async for document in cursor:
            documents_by_id[str(document.get("_id"))] = document
    except Exception as exc:  # noqa: BLE001 - bounded point reads reconcile partial results.
        bulk_read_failed = True
        _log_create_failure("bulk_readback", exc)

    if bulk_read_failed:
        unresolved_ids = [
            plan_id for plan_id in ids if str(plan_id) not in documents_by_id
        ][:MAX_READBACK_FALLBACKS]
        for plan_id in unresolved_ids:
            plan_id_text = str(plan_id)
            try:
                document = await db.work_plans.find_one({"_id": plan_id})
            except Exception as exc:  # noqa: BLE001 - one failed read must not hide other dates.
                _log_create_failure("point_readback", exc, plan_id=plan_id_text)
                continue
            if document is not None:
                documents_by_id[plan_id_text] = document

    results: list[dict[str, Any]] = []
    audit_documents: list[tuple[str, dict[str, Any] | None]] = []

    for draft in drafts:
        plan_id = str(draft["_id"])
        document = documents_by_id.get(plan_id)
        write_state = write_states[plan_id]
        if write_state == "created":
            durable_document = document or draft
            result = {
                "plan_date": draft["plan_date"],
                "outcome": "created",
                "plan": serialize_doc(durable_document),
            }
            audit_documents.append((plan_id, durable_document))
        elif write_state == "duplicate":
            result = {
                "plan_date": draft["plan_date"],
                "outcome": "duplicate",
            }
            if document is not None:
                result["plan"] = serialize_doc(document)
            audit_documents.append((plan_id, document))
        elif document is None:
            result: dict[str, Any] = {
                "plan_date": draft["plan_date"],
                "outcome": "failed",
                "error": _write_error_message(write_errors.get(plan_id)),
            }
        else:
            result = {
                "plan_date": draft["plan_date"],
                "outcome": "duplicate",
                "plan": serialize_doc(document),
            }
            audit_documents.append((plan_id, document))
        results.append(result)

    for plan_id, document in audit_documents:
        try:
            await write_audit_log(
                db,
                actor=actor,
                action="work_plan.create",
                resource_type="work_plan",
                resource_id=plan_id,
                after=document,
                dedupe_key=f"work_plan.create:{plan_id}",
            )
        except Exception as exc:  # noqa: BLE001 - an audit outage must not hide a durable create.
            _log_create_failure("audit", exc, plan_id=plan_id)

    return {
        "duplicate_submission": bool(results) and all(
            result["outcome"] == "duplicate" for result in results
        ),
        "results": results,
        "total": len(results),
    }


async def list_my_work_plans(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    limit: int = DEFAULT_HISTORY_LIMIT,
) -> dict[str, Any]:
    require_browser_actor(actor)
    member_id = str(actor["_id"]).strip()
    normalized_limit = max(1, min(int(limit), MAX_HISTORY_LIMIT))
    query = {"member_id": member_id}
    cursor = db.work_plans.find(query).sort(
        [("plan_date", -1), ("created_at", -1)]
    ).limit(normalized_limit)
    items = [serialize_doc(document) async for document in cursor]
    return {"items": items, "total": len(items)}


async def update_work_plan(
    db: AsyncIOMotorDatabase,
    *,
    plan_id: str,
    actor: dict[str, Any],
    payload: WorkPlanUpdate,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    require_browser_actor(actor)
    observed = observed_at or now_utc()
    existing = await _get_mutable_plan(db, plan_id=plan_id, actor=actor)
    updates = validate_update(existing, payload, observed)
    actor_id = str(actor["_id"]).strip()
    updates["updated_by"] = actor_id

    query: dict[str, Any] = {"_id": plan_id, "is_cancelled": False}
    if not is_plan_manager(actor):
        query["member_id"] = actor_id
    if payload.expected_updated_at is not None:
        query["updated_at"] = existing["updated_at"]

    updated = await db.work_plans.find_one_and_update(
        query,
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        await _raise_mutation_failure(
            db,
            plan_id=plan_id,
            actor=actor,
            expected_updated_at=payload.expected_updated_at,
        )

    await write_audit_log(
        db,
        actor=actor,
        action="work_plan.update",
        resource_type="work_plan",
        resource_id=plan_id,
        before=existing,
        after=updated,
    )
    return serialize_doc(updated)


async def cancel_work_plan(
    db: AsyncIOMotorDatabase,
    *,
    plan_id: str,
    actor: dict[str, Any],
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    require_browser_actor(actor)
    observed = observed_at or now_utc()
    existing = await _get_mutable_plan(db, plan_id=plan_id, actor=actor)
    if existing.get("is_cancelled") is True or existing.get("status") == "cancelled":
        raise WorkPlanConflictError("计划已经取消")

    actor_id = str(actor["_id"]).strip()
    query: dict[str, Any] = {"_id": plan_id, "is_cancelled": False}
    if not is_plan_manager(actor):
        query["member_id"] = actor_id
    updates = {
        "status": "cancelled",
        "is_cancelled": True,
        "cancelled_at": observed,
        "cancelled_by": actor_id,
        "updated_at": observed,
        "updated_by": actor_id,
    }
    cancelled = await db.work_plans.find_one_and_update(
        query,
        {"$set": updates},
        return_document=ReturnDocument.AFTER,
    )
    if cancelled is None:
        await _raise_mutation_failure(db, plan_id=plan_id, actor=actor)

    await write_audit_log(
        db,
        actor=actor,
        action="work_plan.cancel",
        resource_type="work_plan",
        resource_id=plan_id,
        before=existing,
        after=cancelled,
    )
    return serialize_doc(cancelled)


async def list_work_plan_schedule(
    db: AsyncIOMotorDatabase,
    *,
    range_name: str,
    member_ids: list[str] | None,
    include_cancelled: bool,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    if range_name not in {"7d", "30d", "all"}:
        raise ValueError("range_name must be one of 7d, 30d or all")

    observed = observed_at or now_utc()
    if observed.tzinfo is None or observed.utcoffset() is None:
        observed = observed.replace(tzinfo=UTC)
    else:
        observed = observed.astimezone(UTC)
    observed_local = observed.astimezone(SHANGHAI_TIMEZONE)
    local_today = observed_local.date()
    selected_member_ids = list(
        dict.fromkeys(
            member_id
            for value in member_ids or []
            if (member_id := str(value).strip())
        )
    )

    query: dict[str, Any] = {}
    if range_name != "all":
        day_count = 7 if range_name == "7d" else 30
        start_date = local_today
        end_date = start_date + timedelta(days=day_count - 1)
        query["plan_date"] = {
            "$gte": start_date.isoformat(),
            "$lte": end_date.isoformat(),
        }
    if selected_member_ids:
        query["member_id"] = {"$in": selected_member_ids}
    if not include_cancelled:
        query["is_cancelled"] = {"$ne": True}
        query["status"] = {"$ne": "cancelled"}

    metadata_cursor = db.work_plans.find(
        query,
        {
            "member_id": 1,
            "member_name": 1,
            "plan_date": 1,
            "plan_type": 1,
            "start_minute": 1,
            "end_minute": 1,
            "is_cancelled": 1,
            "status": 1,
        },
    ).sort([("plan_date", 1), ("start_minute", 1)])
    plan_cursor = db.work_plans.find(query).sort(
        [("plan_date", 1), ("start_minute", 1)]
    ).limit(MAX_SCHEDULE_PLANS)
    latest_plan_cursor = (
        db.work_plans.find(query).sort([("plan_date", -1)]).limit(1)
        if range_name == "all"
        else None
    )
    user_cursor = db.users.find({})
    plan_results = await asyncio.gather(
        _collect_documents(plan_cursor),
        _collect_documents(metadata_cursor),
        _collect_documents(user_cursor),
        list_member_presence_summaries(db, observed_at=observed),
        *(
            [_collect_documents(latest_plan_cursor)]
            if latest_plan_cursor is not None
            else []
        ),
    )
    plans, matching_plan_metadata, users, presence_by_user = plan_results[:4]

    if range_name == "all":
        latest_plans = plan_results[4]
        if plans and latest_plans:
            start_date_text = str(plans[0]["plan_date"])
            end_date_text = str(latest_plans[0]["plan_date"])
        else:
            start_date_text = end_date_text = local_today.isoformat()
    else:
        start_date_text = start_date.isoformat()
        end_date_text = end_date.isoformat()

    profiles: dict[str, dict[str, Any]] = {}
    for user in users:
        member_id = str(user.get("_id") or user.get("id") or "").strip()
        if not member_id or (
            selected_member_ids and member_id not in selected_member_ids
        ):
            continue
        profiles[member_id] = {
            "member_id": member_id,
            "member_name": user.get("name") or user.get("email") or member_id,
            "member_email": user.get("email"),
            "role": user.get("role"),
            "account_status": user.get("status"),
        }

    for plan in matching_plan_metadata:
        member_id = str(plan.get("member_id") or "").strip()
        if not member_id:
            continue
        profiles.setdefault(
            member_id,
            {
                "member_id": member_id,
                "member_name": plan.get("member_name") or member_id,
                "member_email": None,
                "role": None,
                "account_status": None,
            },
        )
    current_minute = observed_local.hour * 60 + observed_local.minute
    current_date_text = local_today.isoformat()
    active_plans: dict[str, dict[str, Any]] = {}
    for plan in matching_plan_metadata:
        if (
            plan.get("plan_date") != current_date_text
            or plan.get("is_cancelled") is True
            or plan.get("status") == "cancelled"
        ):
            continue
        start_minute = plan.get("start_minute")
        end_minute = plan.get("end_minute")
        if not isinstance(start_minute, int) or not isinstance(end_minute, int):
            continue
        if not (start_minute <= current_minute < end_minute):
            continue
        member_id = str(plan.get("member_id") or "").strip()
        current = active_plans.get(member_id)
        if current is None or (
            current.get("plan_type") != "temporary_unavailable"
            and plan.get("plan_type") == "temporary_unavailable"
        ):
            active_plans[member_id] = plan

    members = []
    for member_id, profile in profiles.items():
        summary = presence_by_user.get(member_id, {})
        is_online = bool(summary.get("is_online"))
        active_plan = active_plans.get(member_id)
        members.append(
            {
                **profile,
                "is_online": is_online,
                "active_clients": int(summary.get("active_clients") or 0),
                "last_seen_at": summary.get("last_seen_at"),
                "active_plan": active_plan,
                "collaboration_status": collaboration_status(
                    is_online=is_online,
                    active_plan=active_plan,
                ),
            }
        )
    members.sort(
        key=lambda member: (
            str(member.get("member_name") or "").casefold(),
            member["member_id"],
        )
    )

    return serialize_doc(
        {
            "members": members,
            "plans": plans,
            "start_date": start_date_text,
            "end_date": end_date_text,
            "observed_at": observed,
            "timezone": str(SHANGHAI_TIMEZONE),
        }
    )


async def _collect_documents(cursor: Any) -> list[dict[str, Any]]:
    return [document async for document in cursor]


async def _get_mutable_plan(
    db: AsyncIOMotorDatabase,
    *,
    plan_id: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    existing = await db.work_plans.find_one({"_id": plan_id})
    if existing is None:
        raise WorkPlanNotFoundError("工作计划不存在")
    if not is_plan_manager(actor) and str(existing.get("member_id") or "") != str(
        actor["_id"]
    ):
        raise WorkPlanPermissionError("不能修改其他成员的工作计划")
    return existing


async def _raise_mutation_failure(
    db: AsyncIOMotorDatabase,
    *,
    plan_id: str,
    actor: dict[str, Any],
    expected_updated_at: datetime | None = None,
) -> None:
    current = await _get_mutable_plan(db, plan_id=plan_id, actor=actor)
    if current.get("is_cancelled") is True or current.get("status") == "cancelled":
        raise WorkPlanConflictError("计划已经取消")
    if expected_updated_at is not None and current.get("updated_at") != expected_updated_at:
        raise WorkPlanConflictError("计划已被更新，请刷新后重试")
    raise WorkPlanConflictError("计划状态已变化，请刷新后重试")


def _write_error_message(error: Exception | None) -> str:
    del error
    return "保存工作计划失败，请稍后重试"


__all__ = [
    "WorkPlanAccessError",
    "WorkPlanNotFoundError",
    "WorkPlanPermissionError",
    "cancel_work_plan",
    "create_work_plans",
    "list_my_work_plans",
    "list_work_plan_schedule",
    "require_browser_actor",
    "update_work_plan",
]
