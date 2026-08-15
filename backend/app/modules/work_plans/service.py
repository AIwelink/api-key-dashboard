from __future__ import annotations

import asyncio
import base64
import binascii
import json
import logging
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.modules.system.audit import write_audit_log
from app.modules.system.presence import list_member_presence_summaries
from app.modules.work_plans.domain import (
    SHANGHAI_TIMEZONE,
    WorkPlanConflictError,
    WorkPlanRuleError,
    build_operation_drafts,
    build_plan_drafts,
    collaboration_status,
    is_plan_manager,
    validate_update,
)
from app.modules.work_plans.projection import (
    NormalizedOperation,
    clip_cancellation,
    normalize_legacy_records,
    normalize_v2_operation,
    project_operations,
    sort_members,
)
from app.modules.work_plans.schemas import (
    WorkPlanCreate,
    WorkPlanOperationCreate,
    WorkPlanUpdate,
)
from app.utils import now_utc, serialize_doc


DEFAULT_HISTORY_LIMIT = 100
MAX_HISTORY_LIMIT = 200
MAX_READBACK_FALLBACKS = 5
MAX_SCHEDULE_PLANS = 4_000
MAX_AUDIT_REPAIR_INTENTS = 100
DEFAULT_AUDIT_RECONCILIATION_INTERVAL_SECONDS = 60
MEMBER_OPERATION_LEASE_SECONDS = 10
AUDIT_INTENTS_FIELD = "_audit_intents"


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


def _log_audit_reconciliation_failure(
    failure_code: str,
    error: Exception,
    *,
    action: str,
    plan_id: str,
) -> None:
    if action == "work_plan.create":
        _log_create_failure(f"audit_{failure_code}", error, plan_id=plan_id)
        return
    logger.error(
        "work_plan_audit_reconciliation_failure code=%s action=%s "
        "plan_id=%s exception_type=%s",
        failure_code,
        action,
        plan_id,
        type(error).__name__,
    )


def require_browser_actor(actor: dict[str, Any]) -> None:
    """Personal work-plan history is available only to browser-authenticated users."""
    actor_type = actor.get("actor_type")
    actor_id = str(actor.get("_id") or "").strip()
    if ("actor_type" in actor and actor_type != "user") or not actor_id:
        raise WorkPlanAccessError("个人工作计划仅限已登录的浏览器用户访问")


def _plan_snapshot(document: dict[str, Any] | None) -> dict[str, Any] | None:
    if document is None:
        return None
    return {
        key: value
        for key, value in document.items()
        if key != AUDIT_INTENTS_FIELD
    }


def _serialize_plan(document: dict[str, Any]) -> dict[str, Any]:
    snapshot = _plan_snapshot(document)
    assert snapshot is not None
    return serialize_doc(snapshot)


def _serialize_history_plan(
    document: dict[str, Any],
    *,
    replaced_operation_ids: set[str],
) -> dict[str, Any]:
    serialized = _serialize_plan(document)
    if document.get("schema_version") != 2:
        serialized["legacy_derived"] = True
        return serialized
    serialized.setdefault("plan_date", document.get("anchor_date"))
    requested_start = document.get("requested_start_at")
    requested_end = document.get("requested_end_at")
    effective_start = document.get("effective_start_at")
    effective_end = document.get("effective_end_at")
    serialized["is_clipped"] = (
        requested_start != effective_start or requested_end != effective_end
    )
    operation_id = str(document.get("_id") or "")
    if operation_id in replaced_operation_ids:
        serialized["history_state"] = "replaced"
    elif document.get("operation_type") == "cancel":
        serialized["history_state"] = "cancelled"
    else:
        serialized["history_state"] = "active"
    serialized["legacy_derived"] = False
    return serialized


def _build_audit_intent(
    *,
    actor: dict[str, Any],
    action: str,
    plan_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    dedupe_key: str,
) -> dict[str, Any]:
    return {
        "dedupe_key": dedupe_key,
        "actor": {
            "_id": actor.get("_id"),
            "name": actor.get("name"),
            "actor_type": actor.get("actor_type") or "user",
        },
        "action": action,
        "resource_type": "work_plan",
        "resource_id": plan_id,
        "before": _plan_snapshot(before),
        "after": _plan_snapshot(after),
    }


def _mutation_audit_dedupe_key(
    *,
    action: str,
    plan_id: str,
    updated_at: datetime,
) -> str:
    return f"{action}:{plan_id}:{updated_at.isoformat()}"


async def _reconcile_document_audit_intents(
    db: AsyncIOMotorDatabase,
    *,
    plan_id: str,
    document: dict[str, Any] | None,
    fallback_intent: dict[str, Any] | None = None,
) -> int:
    intents = list((document or {}).get(AUDIT_INTENTS_FIELD) or [])
    if fallback_intent is not None and not any(
        intent.get("dedupe_key") == fallback_intent["dedupe_key"]
        for intent in intents
        if isinstance(intent, dict)
    ):
        intents.append(fallback_intent)

    repaired = 0
    for intent in intents:
        if not isinstance(intent, dict):
            continue
        dedupe_key = str(intent.get("dedupe_key") or "").strip()
        action = str(intent.get("action") or "").strip()
        if not dedupe_key or not action:
            continue
        try:
            await write_audit_log(
                db,
                actor=intent.get("actor") if isinstance(intent.get("actor"), dict) else None,
                action=action,
                resource_type=str(intent.get("resource_type") or "work_plan"),
                resource_id=str(intent.get("resource_id") or plan_id),
                before=intent.get("before") if isinstance(intent.get("before"), dict) else None,
                after=intent.get("after") if isinstance(intent.get("after"), dict) else None,
                dedupe_key=dedupe_key,
            )
        except Exception as exc:  # noqa: BLE001 - the durable intent remains queued.
            _log_audit_reconciliation_failure(
                "write",
                exc,
                action=action,
                plan_id=plan_id,
            )
            continue
        try:
            await db.work_plans.update_one(
                {"_id": plan_id},
                {"$pull": {AUDIT_INTENTS_FIELD: {"dedupe_key": dedupe_key}}},
            )
        except Exception as exc:  # noqa: BLE001 - dedupe makes cleanup safely replayable.
            _log_audit_reconciliation_failure(
                "cleanup",
                exc,
                action=action,
                plan_id=plan_id,
            )
            continue
        repaired += 1
    return repaired


async def reconcile_work_plan_audit_intents(
    db: AsyncIOMotorDatabase,
    *,
    limit: int = MAX_AUDIT_REPAIR_INTENTS,
) -> int:
    normalized_limit = max(1, min(int(limit), MAX_AUDIT_REPAIR_INTENTS))
    cursor = db.work_plans.find(
        {f"{AUDIT_INTENTS_FIELD}.0": {"$exists": True}}
    ).limit(normalized_limit)
    repaired = 0
    async for document in cursor:
        plan_id = str(document.get("_id") or "").strip()
        if not plan_id:
            continue
        repaired += await _reconcile_document_audit_intents(
            db,
            plan_id=plan_id,
            document=document,
        )
    return repaired


async def work_plan_audit_reconciliation_loop(
    db: AsyncIOMotorDatabase,
    *,
    interval_seconds: float = DEFAULT_AUDIT_RECONCILIATION_INTERVAL_SECONDS,
) -> None:
    interval = max(0.0, float(interval_seconds))
    while True:
        repaired = 0
        try:
            repaired = await reconcile_work_plan_audit_intents(db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconciliation must survive transient outages.
            logger.error(
                "work_plan_audit_reconciliation_loop_failure exception_type=%s",
                type(exc).__name__,
            )
        await asyncio.sleep(0 if repaired >= MAX_AUDIT_REPAIR_INTENTS else interval)


async def create_work_plans(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    payload: WorkPlanCreate | WorkPlanOperationCreate,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    require_browser_actor(actor)
    observed = observed_at or now_utc()
    if isinstance(payload, WorkPlanOperationCreate):
        return await _create_work_plan_operations(
            db,
            actor=actor,
            payload=payload,
            observed_at=observed,
        )
    # Draft construction performs all validation and must precede every write.
    drafts = build_plan_drafts(actor, payload, observed)
    for draft in drafts:
        plan_id = str(draft["_id"])
        draft[AUDIT_INTENTS_FIELD] = [
            _build_audit_intent(
                actor=actor,
                action="work_plan.create",
                plan_id=plan_id,
                before=None,
                after=draft,
                dedupe_key=f"work_plan.create:{plan_id}",
            )
        ]
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
    unavailable_readbacks: set[str] = set()
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
                unavailable_readbacks.add(plan_id_text)
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
                "plan": _serialize_plan(durable_document),
            }
            audit_documents.append((plan_id, durable_document))
        elif write_state == "duplicate":
            result = {
                "plan_date": draft["plan_date"],
                "outcome": "duplicate",
            }
            if document is not None:
                result["plan"] = _serialize_plan(document)
            audit_documents.append((plan_id, document))
        elif document is None and plan_id in unavailable_readbacks:
            result = {
                "plan_date": draft["plan_date"],
                "outcome": "uncertain",
                "error": _uncertain_write_message(),
            }
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
                "plan": _serialize_plan(document),
            }
            audit_documents.append((plan_id, document))
        results.append(result)

    for plan_id, document in audit_documents:
        fallback_intent = _build_audit_intent(
            actor=actor,
            action="work_plan.create",
            plan_id=plan_id,
            before=None,
            after=document,
            dedupe_key=f"work_plan.create:{plan_id}",
        )
        await _reconcile_document_audit_intents(
            db,
            plan_id=plan_id,
            document=document,
            fallback_intent=fallback_intent,
        )

    return {
        "duplicate_submission": bool(results) and all(
            result["outcome"] == "duplicate" for result in results
        ),
        "results": results,
        "total": len(results),
    }


@asynccontextmanager
async def _member_operation_lease(
    db: AsyncIOMotorDatabase,
    *,
    member_id: str,
    observed_at: datetime,
):
    owner = str(uuid4())
    lease_until = observed_at + timedelta(seconds=MEMBER_OPERATION_LEASE_SECONDS)
    try:
        head = await db.work_plan_member_heads.find_one_and_update(
            {
                "_id": member_id,
                "$or": [
                    {"lease_until": {"$lte": observed_at}},
                    {"lease_until": {"$exists": False}},
                ],
            },
            {
                "$set": {
                    "lease_owner": owner,
                    "lease_until": lease_until,
                    "updated_at": observed_at,
                },
                "$setOnInsert": {"last_sequence": 0},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError as exc:
        raise WorkPlanConflictError("计划正在更新，请稍后重试") from exc
    if head is None or head.get("lease_owner") != owner:
        raise WorkPlanConflictError("计划正在更新，请稍后重试")
    try:
        yield head
    finally:
        await db.work_plan_member_heads.update_one(
            {"_id": member_id, "lease_owner": owner},
            {"$unset": {"lease_owner": "", "lease_until": ""}},
        )


async def _repair_operation_sequence(
    db: AsyncIOMotorDatabase,
    *,
    member_id: str,
    head: dict[str, Any],
) -> int:
    cursor = db.work_plans.find(
        {"member_id": member_id, "schema_version": 2}
    ).sort([("member_sequence", -1)]).limit(1)
    latest_documents = await _collect_documents(cursor)
    highest_committed = int(
        (latest_documents[0] if latest_documents else {}).get("member_sequence") or 0
    )
    highest = max(int(head.get("last_sequence") or 0), highest_committed)
    await db.work_plan_member_heads.update_one(
        {"_id": member_id},
        {"$max": {"last_sequence": highest}},
    )
    return highest


async def _create_work_plan_operations(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    payload: WorkPlanOperationCreate,
    observed_at: datetime,
) -> dict[str, Any]:
    drafts = build_operation_drafts(actor, payload, observed_at)
    member_id = str(actor["_id"]).strip()
    async with _member_operation_lease(
        db,
        member_id=member_id,
        observed_at=observed_at,
    ) as head:
        replay = await _find_operation_replay(
            db,
            member_id=member_id,
            idempotency_key=str(payload.idempotency_key),
        )
        if replay:
            return _operation_command_response(replay, outcome="duplicate")

        sequence = await _repair_operation_sequence(
            db,
            member_id=member_id,
            head=head,
        )
        operations = await _expand_operation_drafts(db, drafts)
        persisted: list[dict[str, Any]] = []
        outcomes: list[str] = []
        for draft in operations:
            sequence += 1
            draft["member_sequence"] = sequence
            plan_id = str(draft["_id"])
            draft[AUDIT_INTENTS_FIELD] = [
                _build_audit_intent(
                    actor=actor,
                    action="work_plan.create",
                    plan_id=plan_id,
                    before=None,
                    after=draft,
                    dedupe_key=f"work_plan.create:{plan_id}",
                )
            ]
            outcome = "created"
            try:
                result = await db.work_plans.update_one(
                    {"_id": draft["_id"]},
                    {"$setOnInsert": draft},
                    upsert=True,
                )
                if getattr(result, "upserted_id", None) is None:
                    outcome = "duplicate"
            except Exception as exc:  # noqa: BLE001 - readback resolves lost acknowledgements.
                _log_create_failure("operation_write", exc, plan_id=plan_id)
                stored_after_error = await db.work_plans.find_one({"_id": draft["_id"]})
                if stored_after_error is None:
                    raise WorkPlanConflictError("工作计划提交失败，请重试") from exc
                outcome = "created"

            stored = await db.work_plans.find_one({"_id": draft["_id"]})
            if stored is None:
                raise WorkPlanConflictError("工作计划提交结果不确定，请稍后刷新")
            persisted.append(stored)
            outcomes.append(outcome)
            await db.work_plan_member_heads.update_one(
                {"_id": member_id},
                {"$max": {"last_sequence": int(stored["member_sequence"])}},
            )
            await _reconcile_document_audit_intents(
                db,
                plan_id=plan_id,
                document=stored,
            )

        response_outcome = "duplicate" if outcomes and all(
            outcome == "duplicate" for outcome in outcomes
        ) else "created"
        return _operation_command_response(persisted, outcome=response_outcome)


async def _find_operation_replay(
    db: AsyncIOMotorDatabase,
    *,
    member_id: str,
    idempotency_key: str,
) -> list[dict[str, Any]]:
    cursor = db.work_plans.find(
        {
            "member_id": member_id,
            "schema_version": 2,
            "idempotency_key": idempotency_key,
        }
    ).sort([("member_sequence", 1)])
    return await _collect_documents(cursor)


async def _expand_operation_drafts(
    db: AsyncIOMotorDatabase,
    drafts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    if not drafts or drafts[0]["operation_type"] != "cancel":
        return drafts

    draft = drafts[0]
    cursor = db.work_plans.find(
        {"member_id": draft["member_id"], "schema_version": 2}
    )
    committed = await _collect_documents(cursor)
    normalized = [
        NormalizedOperation(
            operation_id=str(document["_id"]),
            member_id=str(document["member_id"]),
            operation_type=str(document["operation_type"]),
            start_at=document["effective_start_at"],
            end_at=document["effective_end_at"],
            order_key=(2, int(document.get("member_sequence") or 0), str(document["_id"])),
        )
        for document in committed
    ]
    projected = project_operations(
        normalized,
        window_start=draft["requested_start_at"],
        window_end=draft["requested_end_at"],
    )
    fragments = clip_cancellation(
        projected,
        requested_start=draft["requested_start_at"],
        requested_end=draft["requested_end_at"],
    )
    if not fragments:
        raise WorkPlanRuleError("所选时间段没有可取消的工作计划")

    expanded: list[dict[str, Any]] = []
    for index, (effective_start_at, effective_end_at) in enumerate(fragments):
        operation = dict(draft)
        if index:
            operation["_id"] = f"{draft['_id']}:{index + 1}"
        start_delta = int(
            (effective_start_at - draft["requested_start_at"]).total_seconds() // 60
        )
        end_delta = int(
            (effective_end_at - draft["requested_start_at"]).total_seconds() // 60
        )
        operation["effective_start_at"] = effective_start_at
        operation["effective_end_at"] = effective_end_at
        operation["effective_start_offset_minute"] = (
            draft["start_offset_minute"] + start_delta
        )
        operation["effective_end_offset_minute"] = (
            draft["start_offset_minute"] + end_delta
        )
        expanded.append(operation)
    return expanded


def _operation_command_response(
    operations: list[dict[str, Any]],
    *,
    outcome: str,
) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        grouped.setdefault(str(operation["anchor_date"]), []).append(operation)
    results = []
    for anchor_date in sorted(grouped):
        serialized = [_serialize_plan(operation) for operation in grouped[anchor_date]]
        results.append(
            {
                "anchor_date": anchor_date,
                "plan_date": anchor_date,
                "outcome": outcome,
                "operation": serialized[0],
                "operations": serialized,
            }
        )
    return {
        "duplicate_submission": outcome == "duplicate" and bool(results),
        "results": results,
        "total": len(results),
    }


async def list_my_work_plans(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    limit: int = DEFAULT_HISTORY_LIMIT,
    cursor: str | None = None,
) -> dict[str, Any]:
    require_browser_actor(actor)
    member_id = str(actor["_id"]).strip()
    normalized_limit = max(1, min(int(limit), MAX_HISTORY_LIMIT))
    base_query = {"member_id": member_id}
    query: dict[str, Any] = dict(base_query)
    if cursor:
        cursor_date, cursor_created_at, cursor_id = _decode_history_cursor(cursor)
        query["$or"] = [
            {"plan_date": {"$lt": cursor_date}},
            {"plan_date": cursor_date, "created_at": {"$lt": cursor_created_at}},
            {
                "plan_date": cursor_date,
                "created_at": cursor_created_at,
                "_id": {"$lt": cursor_id},
            },
        ]
    cursor = db.work_plans.find(query).sort(
        [("plan_date", -1), ("created_at", -1), ("_id", -1)]
    ).limit(normalized_limit + 1)
    documents, total = await asyncio.gather(
        _collect_documents(cursor),
        db.work_plans.count_documents(base_query),
    )
    has_more = len(documents) > normalized_limit
    page = documents[:normalized_limit]
    replaced_operation_ids = {
        str(document.get("compensates_operation_id"))
        for document in documents
        if document.get("compensates_operation_id")
    }
    return {
        "items": [
            _serialize_history_plan(
                document,
                replaced_operation_ids=replaced_operation_ids,
            )
            for document in page
        ],
        "total": int(total),
        "has_more": has_more,
        "next_cursor": _encode_history_cursor(page[-1]) if has_more and page else None,
    }


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
    after_snapshot = {**_plan_snapshot(existing), **updates}
    audit_intent = _build_audit_intent(
        actor=actor,
        action="work_plan.update",
        plan_id=plan_id,
        before=existing,
        after=after_snapshot,
        dedupe_key=_mutation_audit_dedupe_key(
            action="work_plan.update",
            plan_id=plan_id,
            updated_at=updates["updated_at"],
        ),
    )

    query: dict[str, Any] = {"_id": plan_id, "is_cancelled": False}
    if not is_plan_manager(actor):
        query["member_id"] = actor_id
    if payload.expected_updated_at is not None:
        query["updated_at"] = existing["updated_at"]

    updated = await db.work_plans.find_one_and_update(
        query,
        {"$set": updates, "$push": {AUDIT_INTENTS_FIELD: audit_intent}},
        return_document=ReturnDocument.AFTER,
    )
    if updated is None:
        await _raise_mutation_failure(
            db,
            plan_id=plan_id,
            actor=actor,
            expected_updated_at=payload.expected_updated_at,
        )

    audit_document = dict(updated)
    audit_document.setdefault(
        AUDIT_INTENTS_FIELD,
        [*existing.get(AUDIT_INTENTS_FIELD, []), audit_intent],
    )
    await _reconcile_document_audit_intents(
        db,
        plan_id=plan_id,
        document=audit_document,
    )
    return _serialize_plan(updated)


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
        await _reconcile_document_audit_intents(
            db,
            plan_id=plan_id,
            document=existing,
        )
        return _serialize_plan(existing)

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
    audit_intent = _build_audit_intent(
        actor=actor,
        action="work_plan.cancel",
        plan_id=plan_id,
        before=existing,
        after={**_plan_snapshot(existing), **updates},
        dedupe_key=_mutation_audit_dedupe_key(
            action="work_plan.cancel",
            plan_id=plan_id,
            updated_at=updates["updated_at"],
        ),
    )
    cancelled = await db.work_plans.find_one_and_update(
        query,
        {"$set": updates, "$push": {AUDIT_INTENTS_FIELD: audit_intent}},
        return_document=ReturnDocument.AFTER,
    )
    if cancelled is None:
        current = await _get_mutable_plan(db, plan_id=plan_id, actor=actor)
        if current.get("is_cancelled") is True or current.get("status") == "cancelled":
            await _reconcile_document_audit_intents(
                db,
                plan_id=plan_id,
                document=current,
            )
            return _serialize_plan(current)
        raise WorkPlanConflictError("计划状态已变化，请刷新后重试")

    audit_document = dict(cancelled)
    audit_document.setdefault(
        AUDIT_INTENTS_FIELD,
        [*existing.get(AUDIT_INTENTS_FIELD, []), audit_intent],
    )
    await _reconcile_document_audit_intents(
        db,
        plan_id=plan_id,
        document=audit_document,
    )
    return _serialize_plan(cancelled)


async def list_work_plan_schedule(
    db: AsyncIOMotorDatabase,
    *,
    range_name: str,
    member_ids: list[str] | None,
    include_cancelled: bool,
    cursor: str | None = None,
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

    start_date = local_today
    end_date = local_today
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
    base_query = dict(query)
    if cursor:
        cursor_date, cursor_start, cursor_id = _decode_schedule_cursor(cursor)
        query["$or"] = [
            {"plan_date": {"$lt": cursor_date}},
            {"plan_date": cursor_date, "start_minute": {"$lt": cursor_start}},
            {
                "plan_date": cursor_date,
                "start_minute": cursor_start,
                "_id": {"$lt": cursor_id},
            },
        ]

    plan_cursor = db.work_plans.find(query).sort(
        [("plan_date", -1), ("start_minute", -1), ("_id", -1)]
    ).limit(MAX_SCHEDULE_PLANS + 1)
    active_query: dict[str, Any] = {
        "plan_date": local_today.isoformat(),
        "start_minute": {"$lte": observed_local.hour * 60 + observed_local.minute},
        "end_minute": {"$gt": observed_local.hour * 60 + observed_local.minute},
        "is_cancelled": {"$ne": True},
        "status": {"$ne": "cancelled"},
    }
    if selected_member_ids:
        active_query["member_id"] = {"$in": selected_member_ids}
    active_cursor = db.work_plans.find(
        active_query,
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
    ).limit(MAX_SCHEDULE_PLANS)
    projection_query: dict[str, Any] = {}
    if range_name != "all":
        projection_start_at = datetime.combine(
            start_date,
            time.min,
            tzinfo=SHANGHAI_TIMEZONE,
        ).astimezone(UTC)
        projection_end_at = datetime.combine(
            end_date + timedelta(days=1),
            time.min,
            tzinfo=SHANGHAI_TIMEZONE,
        ).astimezone(UTC)
        projection_query["$or"] = [
            {
                "schema_version": 2,
                "effective_start_at": {"$lt": projection_end_at},
                "effective_end_at": {"$gt": projection_start_at},
            },
            {
                "schema_version": {"$ne": 2},
                "plan_date": {
                    "$gte": start_date.isoformat(),
                    "$lte": end_date.isoformat(),
                },
            },
        ]
    if selected_member_ids:
        projection_query["member_id"] = {"$in": selected_member_ids}
    projection_cursor = db.work_plans.find(projection_query).limit(MAX_SCHEDULE_PLANS)
    user_cursor = db.users.find({})
    plan_results = await asyncio.gather(
        _collect_documents(plan_cursor),
        _collect_documents(active_cursor),
        _collect_documents(projection_cursor),
        _collect_documents(user_cursor),
        list_member_presence_summaries(db, observed_at=observed),
        db.work_plans.count_documents(base_query),
    )
    (
        plan_documents,
        active_plan_documents,
        projection_documents,
        users,
        presence_by_user,
        total,
    ) = plan_results
    has_more = len(plan_documents) > MAX_SCHEDULE_PLANS
    page_documents = plan_documents[:MAX_SCHEDULE_PLANS]
    next_cursor = (
        _encode_schedule_cursor(page_documents[-1])
        if has_more and page_documents
        else None
    )
    page_documents.reverse()
    plans = [_plan_snapshot(plan) for plan in page_documents]

    if range_name == "all":
        if plans:
            start_date_text = str(plans[0]["plan_date"])
            end_date_text = str(plans[-1]["plan_date"])
        elif projection_documents:
            projection_dates = [
                str(document.get("anchor_date") or document.get("plan_date") or "")
                for document in projection_documents
            ]
            projection_dates = [value for value in projection_dates if _is_iso_date(value)]
            start_date_text = min(projection_dates) if projection_dates else local_today.isoformat()
            end_date_text = max(projection_dates) if projection_dates else local_today.isoformat()
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
            "work_plan_priority": user.get("work_plan_priority"),
        }

    for plan in [*page_documents, *active_plan_documents, *projection_documents]:
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
                "work_plan_priority": None,
            },
        )

    visible_start_at = datetime.combine(
        date.fromisoformat(start_date_text),
        time.min,
        tzinfo=SHANGHAI_TIMEZONE,
    ).astimezone(UTC)
    visible_end_at = datetime.combine(
        date.fromisoformat(end_date_text) + timedelta(days=1),
        time.min,
        tzinfo=SHANGHAI_TIMEZONE,
    ).astimezone(UTC)
    projection_by_member: dict[str, list[dict[str, Any]]] = {}
    for document in projection_documents:
        member_id = str(document.get("member_id") or "").strip()
        if member_id:
            projection_by_member.setdefault(member_id, []).append(document)

    segments_by_member: dict[str, list[dict[str, Any]]] = {}
    for member_id, documents in projection_by_member.items():
        normalized = normalize_legacy_records(
            documents,
            local_timezone=SHANGHAI_TIMEZONE,
        )
        for document in documents:
            if document.get("schema_version") != 2:
                continue
            try:
                normalized.append(normalize_v2_operation(document))
            except (KeyError, TypeError, ValueError):
                logger.warning(
                    "work_plan_projection_skipped member_id=%s operation_id=%s",
                    member_id,
                    document.get("_id"),
                )
        projected = project_operations(
            normalized,
            window_start=visible_start_at,
            window_end=visible_end_at,
        )
        segments_by_member[member_id] = [
            {
                "member_id": member_id,
                "member_name": profiles.get(member_id, {}).get("member_name") or member_id,
                "state": segment.state,
                "start_at": segment.start_at,
                "end_at": segment.end_at,
                "winning_operation_id": segment.winning_operation_id,
                "operation_ids": list(segment.operation_ids),
            }
            for segment in projected
        ]

    active_plans: dict[str, dict[str, Any]] = {}
    for plan in active_plan_documents:
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
        member_segments = segments_by_member.get(member_id, [])
        active_segments = [
            segment for segment in member_segments if segment["state"] == "active"
        ]
        current_segment = next(
            (
                segment
                for segment in active_segments
                if segment["start_at"] <= observed < segment["end_at"]
            ),
            None,
        )
        next_green_start = min(
            (
                segment["start_at"]
                for segment in active_segments
                if segment["start_at"] > observed
            ),
            default=None,
        )
        latest_green_end = max(
            (
                segment["end_at"]
                for segment in active_segments
                if segment["end_at"] <= observed
            ),
            default=None,
        )
        if current_segment is not None and active_plan is None:
            active_plan = {
                "plan_type": "work",
                "state": "active",
                "start_at": current_segment["start_at"],
                "end_at": current_segment["end_at"],
            }
        members.append(
            {
                **profile,
                "is_online": is_online,
                "active_clients": int(summary.get("active_clients") or 0),
                "last_seen_at": summary.get("last_seen_at"),
                "active_plan": active_plan,
                "current_green": current_segment is not None,
                "next_green_start": next_green_start,
                "latest_green_end": latest_green_end,
                "collaboration_status": collaboration_status(
                    is_online=is_online,
                    active_plan=active_plan,
                ),
            }
        )
    members = sort_members(members)
    member_order = {
        member["member_id"]: index for index, member in enumerate(members)
    }
    segments = [
        segment
        for member_id in sorted(
            segments_by_member,
            key=lambda value: member_order.get(value, len(member_order)),
        )
        for segment in segments_by_member[member_id]
    ]

    return serialize_doc(
        {
            "members": members,
            "plans": plans,
            "segments": segments,
            "start_date": start_date_text,
            "end_date": end_date_text,
            "start_at": visible_start_at,
            "end_at": visible_end_at,
            "observed_at": observed,
            "timezone": str(SHANGHAI_TIMEZONE),
            "total": int(total),
            "total_operations": len(projection_documents),
            "has_more": has_more,
            "next_cursor": next_cursor,
        }
    )


def _encode_history_cursor(document: dict[str, Any]) -> str:
    created_at = document.get("created_at")
    if not isinstance(created_at, datetime):
        raise ValueError("工作计划历史缺少创建时间")
    return _encode_cursor(
        [str(document.get("plan_date") or ""), created_at.isoformat(), str(document.get("_id") or "")]
    )


def _decode_history_cursor(value: str) -> tuple[str, datetime, str]:
    payload = _decode_cursor(value, expected_size=3)
    plan_date = str(payload[0])
    if not _is_iso_date(plan_date):
        raise ValueError("分页位置已失效，请刷新后重试")
    try:
        created_at = datetime.fromisoformat(str(payload[1]))
    except (TypeError, ValueError) as exc:
        raise ValueError("分页位置已失效，请刷新后重试") from exc
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("分页位置已失效，请刷新后重试")
    created_at = created_at.astimezone(UTC)
    cursor_id = str(payload[2]).strip()
    if not cursor_id:
        raise ValueError("分页位置已失效，请刷新后重试")
    return plan_date, created_at, cursor_id


def _encode_schedule_cursor(document: dict[str, Any]) -> str:
    return _encode_cursor(
        [
            str(document.get("plan_date") or ""),
            int(document.get("start_minute") or 0),
            str(document.get("_id") or ""),
        ]
    )


def _decode_schedule_cursor(value: str) -> tuple[str, int, str]:
    payload = _decode_cursor(value, expected_size=3)
    plan_date = str(payload[0])
    cursor_id = str(payload[2]).strip()
    try:
        start_minute = int(payload[1])
    except (TypeError, ValueError) as exc:
        raise ValueError("分页位置已失效，请刷新后重试") from exc
    if not _is_iso_date(plan_date) or not 0 <= start_minute <= 1_440 or not cursor_id:
        raise ValueError("分页位置已失效，请刷新后重试")
    return plan_date, start_minute, cursor_id


def _encode_cursor(values: list[Any]) -> str:
    raw = json.dumps(values, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(value: str, *, expected_size: int) -> list[Any]:
    try:
        padding = "=" * (-len(value) % 4)
        payload = json.loads(base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8"))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ValueError("分页位置已失效，请刷新后重试") from exc
    if not isinstance(payload, list) or len(payload) != expected_size:
        raise ValueError("分页位置已失效，请刷新后重试")
    return payload


def _is_iso_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


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


def _uncertain_write_message() -> str:
    return "保存结果暂时无法确认，请保留当前表单并使用相同提交标识重试"


__all__ = [
    "WorkPlanAccessError",
    "WorkPlanNotFoundError",
    "WorkPlanPermissionError",
    "cancel_work_plan",
    "create_work_plans",
    "list_my_work_plans",
    "list_work_plan_schedule",
    "reconcile_work_plan_audit_intents",
    "require_browser_actor",
    "update_work_plan",
    "work_plan_audit_reconciliation_loop",
]
