from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.system.audit import write_audit_log
from app.modules.work_plans.domain import build_plan_drafts
from app.modules.work_plans.schemas import WorkPlanCreate
from app.utils import now_utc, serialize_doc


DEFAULT_HISTORY_LIMIT = 1_000
MAX_HISTORY_LIMIT = 4_000
MAX_READBACK_FALLBACKS = 5


logger = logging.getLogger(__name__)


class WorkPlanAccessError(ValueError):
    """Raised when an actor cannot use a personal work-plan operation."""


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


def _write_error_message(error: Exception | None) -> str:
    del error
    return "保存工作计划失败，请稍后重试"


__all__ = [
    "WorkPlanAccessError",
    "create_work_plans",
    "list_my_work_plans",
    "require_browser_actor",
]
