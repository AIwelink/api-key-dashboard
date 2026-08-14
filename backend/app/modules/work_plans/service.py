from __future__ import annotations

from datetime import datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.system.audit import write_audit_log
from app.modules.work_plans.domain import build_plan_drafts
from app.modules.work_plans.schemas import WorkPlanCreate
from app.utils import now_utc, serialize_doc


DEFAULT_HISTORY_LIMIT = 1_000
MAX_HISTORY_LIMIT = 4_000


class WorkPlanAccessError(ValueError):
    """Raised when an actor cannot use a personal work-plan operation."""


def require_browser_actor(actor: dict[str, Any]) -> None:
    """Personal work-plan history is available only to browser-authenticated users."""
    if actor.get("actor_type") == "api_token":
        raise WorkPlanAccessError("API 令牌不能访问个人工作计划，请使用浏览器登录")


async def create_work_plans(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    payload: WorkPlanCreate,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    observed = observed_at or now_utc()
    # Draft construction performs all validation and must precede every write.
    drafts = build_plan_drafts(actor, payload, observed)
    ids = [draft["_id"] for draft in drafts]
    created_ids: set[str] = set()
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
            write_errors[plan_id] = exc
            continue
        if getattr(update_result, "upserted_id", None) is not None:
            created_ids.add(plan_id)

    readback = [
        document
        async for document in db.work_plans.find({"_id": {"$in": ids}})
    ]
    documents_by_id = {str(document.get("_id")): document for document in readback}
    results: list[dict[str, Any]] = []
    audit_documents: list[dict[str, Any]] = []

    for draft in drafts:
        plan_id = str(draft["_id"])
        document = documents_by_id.get(plan_id)
        if document is None:
            result: dict[str, Any] = {
                "plan_date": draft["plan_date"],
                "outcome": "failed",
                "error": _write_error_message(write_errors.get(plan_id)),
            }
        else:
            outcome = "created" if plan_id in created_ids else "duplicate"
            result = {
                "plan_date": draft["plan_date"],
                "outcome": outcome,
                "plan": serialize_doc(document),
            }
            if outcome == "created":
                audit_documents.append(document)
        results.append(result)

    for document in audit_documents:
        try:
            await write_audit_log(
                db,
                actor=actor,
                action="work_plan.create",
                resource_type="work_plan",
                resource_id=str(document.get("_id")),
                after=document,
            )
        except Exception:  # noqa: BLE001 - an audit outage must not hide a durable create.
            continue

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
    member_id = str(actor.get("_id") or actor.get("id") or actor.get("email") or "").strip()
    if not member_id:
        raise WorkPlanAccessError("无法识别当前计划成员")
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
