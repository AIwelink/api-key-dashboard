from __future__ import annotations

from datetime import timedelta
from typing import Any, Literal

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.services.account_records import write_account_operation, write_account_problem
from app.services.pool_lifecycle import actor_name, operation_actor_updates, write_pool_action
from app.services.sub2api import Sub2ApiClient
from app.services.sub2api_cache import get_site
from app.services.sub2api_push import PROBLEM_CLASS_PUSH_TOKEN_EXPIRED, PUSH_ERROR_TASK_TYPE
from app.services.sub2api_return import remote_cumulative_usage_snapshot, remote_usage_snapshot
from app.utils import now_utc, object_id, serialize_doc


TODO_ACCOUNT_PROJECTION = {
    "metadata": 1,
    "account_json.name": 1,
    "account_json.platform": 1,
    "account_json.type": 1,
    "account_json.extra.email": 1,
    "account_json.extra.mailbox_connection": 1,
    "account_json.extra.email_session": 1,
    "account_json.credentials.email": 1,
    "account_json.credentials.plan_type": 1,
}

STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_ARCHIVED = "archived"
STATUS_RESOLVED = "resolved"
LOCK_TTL = timedelta(hours=2)

PushErrorDecision = Literal["plus_reprocess", "problem_library"]


def base_query() -> dict[str, Any]:
    return {
        "metadata.deleted_at": {"$exists": False},
        "metadata.problem_task_type": PUSH_ERROR_TASK_TYPE,
        "metadata.problem_class": PROBLEM_CLASS_PUSH_TOKEN_EXPIRED,
    }


def status_query(status_filter: str, account_type: str | None) -> dict[str, Any]:
    query = base_query()
    if status_filter == "open":
        query["metadata.problem_task_status"] = {"$in": [STATUS_PENDING, STATUS_PROCESSING]}
    elif status_filter != "all":
        query["metadata.problem_task_status"] = status_filter
    if account_type and account_type != "all":
        query["metadata.account_type"] = account_type
    return query


def with_search(query: dict[str, Any], q: str | None) -> dict[str, Any]:
    if not q:
        return query
    search = {
        "$or": [
            {"metadata.email": {"$regex": q, "$options": "i"}},
            {"account_json.name": {"$regex": q, "$options": "i"}},
            {"metadata.problem_error": {"$regex": q, "$options": "i"}},
            {"metadata.problem_remark_zh": {"$regex": q, "$options": "i"}},
            {"metadata.problem_assignee_name": {"$regex": q, "$options": "i"}},
        ]
    }
    return {"$and": [query, search]}


async def list_push_error_accounts(
    db: AsyncIOMotorDatabase,
    *,
    status_filter: str,
    account_type: str | None,
    q: str | None,
    skip: int,
    limit: int,
) -> dict[str, Any]:
    query = with_search(status_query(status_filter, account_type), q)
    cursor = db.accounts.find(query, TODO_ACCOUNT_PROJECTION).sort("metadata.problem_detected_at", -1).skip(skip).limit(limit)
    items = [serialize_doc(account) async for account in cursor]
    total = await db.accounts.count_documents(query)
    stats = {
        "pending": await db.accounts.count_documents(status_query(STATUS_PENDING, account_type)),
        "processing": await db.accounts.count_documents(status_query(STATUS_PROCESSING, account_type)),
        "archived": await db.accounts.count_documents(status_query(STATUS_ARCHIVED, account_type)),
        "resolved": await db.accounts.count_documents(status_query(STATUS_RESOLVED, account_type)),
        "free": await db.accounts.count_documents(status_query("all", "free")),
        "team": await db.accounts.count_documents(status_query("all", "team")),
        "k12": await db.accounts.count_documents(status_query("all", "k12")),
        "plus": await db.accounts.count_documents(status_query("all", "plus")),
    }
    return {"items": items, "total": total, "skip": skip, "limit": limit, "stats": stats}


async def start_push_error_task(db: AsyncIOMotorDatabase, *, account_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    account_oid = _account_oid(account_id)
    now = now_utc()
    result = await db.accounts.find_one_and_update(
        {
            "_id": account_oid,
            **base_query(),
            "metadata.problem_task_status": {"$in": [STATUS_PENDING]},
            "$or": [
                {"metadata.problem_lock": {"$exists": False}},
                {"metadata.problem_lock.locked_by_user_id": actor.get("_id")},
                {"metadata.problem_lock.expires_at": {"$lte": now}},
            ],
        },
        {
            "$set": {
                "metadata.problem_task_status": STATUS_PROCESSING,
                "metadata.problem_assignee_user_id": actor.get("_id"),
                "metadata.problem_assignee_name": actor_name(actor),
                "metadata.problem_started_at": now,
                "metadata.problem_lock": {
                    "task_type": PUSH_ERROR_TASK_TYPE,
                    "locked_by_user_id": actor.get("_id"),
                    "locked_by_name": actor_name(actor),
                    "locked_at": now,
                    "expires_at": now + LOCK_TTL,
                },
                **operation_actor_updates(actor, "开始处理推送错误账号", at=now),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="任务不可处理，或已被其他用户领取")
    await write_account_operation(
        db,
        operation_class="push_error_start_processing",
        operation_name="开始处理推送错误账号",
        remark_zh="人工开始处理推送使用池错误账号。",
        actor=actor,
        account_id=account_id,
    )
    return serialize_doc(result)


async def release_push_error_task(db: AsyncIOMotorDatabase, *, account_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    result = await update_locked_push_error_task(
        db,
        account_id=account_id,
        actor=actor,
        updates={
            "metadata.problem_task_status": STATUS_PENDING,
            **operation_actor_updates(actor, "取消处理推送错误账号"),
        },
        unset={"metadata.problem_lock": ""},
        not_found_detail="只有当前处理人可以取消处理",
    )
    await write_account_operation(
        db,
        operation_class="push_error_release_processing",
        operation_name="取消处理推送错误账号",
        remark_zh="人工取消处理，账号回到推送错误待办。",
        actor=actor,
        account_id=account_id,
    )
    return result


async def test_push_error_account(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    model_id: str,
    prompt: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    account = await _get_locked_account(db, account_id=account_id, actor=actor)
    metadata = dict(account.get("metadata", {}))
    site_id = str(metadata.get("problem_site_id") or metadata.get("sub2api_site_id") or "default")
    remote_id = metadata.get("problem_remote_account_id") or metadata.get("sub2api_account_id")
    if remote_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="账号缺少远端 sub2api id，无法继续测试")
    site = await get_site(db, site_id, include_token=True)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
    try:
        verification = await client.test_account(remote_id, model_id=model_id, prompt=prompt)
    except HTTPException as exc:
        verification = {
            "success": False,
            "model": model_id,
            "prompt": prompt,
            "latency_ms": None,
            "response_preview": "",
            "error": str(exc.detail),
        }
    now = now_utc()
    updates = {
        "metadata.problem_last_test_status": "passed" if verification.get("success") is True else "failed",
        "metadata.problem_last_test_at": now,
        "metadata.problem_last_test_error": None if verification.get("success") is True else str(verification.get("error") or "测试失败"),
        "metadata.problem_last_test_result": verification,
        **operation_actor_updates(actor, "推送错误账号人工测试", at=now),
    }
    await db.accounts.update_one({"_id": account["_id"]}, {"$set": updates})
    await write_account_operation(
        db,
        operation_class="push_error_manual_test",
        operation_name="推送错误账号人工测试",
        remark_zh="人工点击继续测试推送错误账号。",
        actor=actor,
        account_id=account_id,
        status_value="succeeded" if verification.get("success") is True else "failed",
        details={"verification": verification, "site_id": site_id, "remote_account_id": remote_id},
    )
    if verification.get("success") is not True:
        await write_account_problem(
            db,
            problem_class=str(metadata.get("problem_class") or PROBLEM_CLASS_PUSH_TOKEN_EXPIRED),
            problem_name=str(metadata.get("problem_name") or "推送测试凭证过期"),
            remark_zh="人工继续测试后仍失败。",
            account_id=account_id,
            severity="error",
            status_value="open",
            site_id=site_id,
            remote_account_id=remote_id,
            details={"verification": verification},
            actor=actor,
        )
    updated = await db.accounts.find_one({"_id": account["_id"]}, TODO_ACCOUNT_PROJECTION)
    return {"account": serialize_doc(updated), "verification": serialize_doc(verification)}


async def decide_push_error_account(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    decision: PushErrorDecision,
    note: str | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    account = await _get_locked_account(db, account_id=account_id, actor=actor)
    metadata = dict(account.get("metadata", {}))
    account_type = str(metadata.get("account_type") or "").lower()
    if account_type == "free":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="free 错误账号应自动归档，不需要人工决定")
    now = now_utc()
    remote_delete_result = await _delete_remote_problem_account(db, account=account, actor=actor)
    if decision == "plus_reprocess":
        updates = {
            "metadata.problem_task_status": STATUS_RESOLVED,
            "metadata.problem_status": "closed",
            "metadata.problem_resolution": "plus_reprocess",
            "metadata.problem_resolved_at": now,
            "metadata.problem_resolved_by_user_id": actor.get("_id"),
            "metadata.problem_resolved_by_name": actor_name(actor),
            "metadata.problem_resolution_note": note or "",
            "metadata.plus_reprocess_task_type": "plus_reprocess",
            "metadata.plus_reprocess_status": "pending",
            "metadata.plus_reprocess_reason": metadata.get("problem_error") or "push token expired",
            "metadata.plus_reprocess_created_at": now,
            "metadata.pool_status": "problem",
            **operation_actor_updates(actor, "推送错误账号加入 plus 重处理", at=now),
        }
        remark = "已决定加入 plus 账号重新处理待办。"
        operation_class = "push_error_decide_plus_reprocess"
    else:
        updates = {
            "metadata.problem_task_status": STATUS_ARCHIVED,
            "metadata.problem_status": "closed",
            "metadata.problem_resolution": "problem_library",
            "metadata.problem_resolved_at": now,
            "metadata.problem_resolved_by_user_id": actor.get("_id"),
            "metadata.problem_resolved_by_name": actor_name(actor),
            "metadata.problem_resolution_note": note or "",
            "metadata.pool_status": "problem",
            **operation_actor_updates(actor, "推送错误账号归档问题库", at=now),
        }
        remark = "已决定归档进入问题库。"
        operation_class = "push_error_decide_problem_library"

    result = await db.accounts.find_one_and_update(
        {"_id": account["_id"]},
        {
            "$set": updates,
            "$unset": {
                "metadata.problem_lock": "",
                "metadata.sub2api_account_id": "",
                "metadata.sub2api_group_id": "",
                "metadata.sub2api_group_ids": "",
                "metadata.sub2api_group_name": "",
                "metadata.sub2api_last_sync_at": "",
                "metadata.sub2api_pushed_at": "",
                "metadata.push_lock": "",
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    await write_account_operation(
        db,
        operation_class=operation_class,
        operation_name="推送错误账号处理决定",
        remark_zh=remark,
        actor=actor,
        account_id=account_id,
        details={"decision": decision, "note": note, "remote_delete_result": remote_delete_result},
    )
    await write_pool_action(
        db,
        action_type=operation_class,
        actor=actor,
        account_id=account_id,
        status_value="succeeded",
        reason=note,
        after={"decision": decision, "problem_task_status": updates["metadata.problem_task_status"]},
    )
    return serialize_doc(result)


async def update_locked_push_error_task(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    actor: dict[str, Any],
    updates: dict[str, Any],
    unset: dict[str, Any],
    not_found_detail: str,
) -> dict[str, Any]:
    account_oid = _account_oid(account_id)
    update_doc: dict[str, Any] = {"$set": updates}
    if unset:
        update_doc["$unset"] = unset
    result = await db.accounts.find_one_and_update(
        {
            "_id": account_oid,
            **base_query(),
            "metadata.problem_task_status": STATUS_PROCESSING,
            "metadata.problem_lock.locked_by_user_id": actor.get("_id"),
        },
        update_doc,
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=not_found_detail)
    return serialize_doc(result)


async def _get_locked_account(db: AsyncIOMotorDatabase, *, account_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    account_oid = _account_oid(account_id)
    account = await db.accounts.find_one(
        {
            "_id": account_oid,
            **base_query(),
            "metadata.problem_task_status": STATUS_PROCESSING,
            "metadata.problem_lock.locked_by_user_id": actor.get("_id"),
        }
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="只有当前处理人可以操作该错误账号")
    return account


async def _delete_remote_problem_account(db: AsyncIOMotorDatabase, *, account: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(account.get("metadata", {}))
    site_id = str(metadata.get("problem_site_id") or metadata.get("sub2api_site_id") or "default")
    remote_id = metadata.get("problem_remote_account_id") or metadata.get("sub2api_account_id")
    if remote_id is None:
        return {"ok": False, "reason": "missing remote id"}
    site = await get_site(db, site_id, include_token=True)
    if not site:
        return {"ok": False, "reason": "site not found", "site_id": site_id}
    client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
    cached = await db.sub2api_accounts_cache.find_one({"site_id": site_id, "sub2api_account_id": remote_id})
    remote_account = cached.get("account", {}) if cached and isinstance(cached.get("account"), dict) else {}
    if not remote_account:
        try:
            remote_account = await client.get_account(remote_id)
        except HTTPException:
            remote_account = {}
    cumulative_usage = await remote_cumulative_usage_snapshot(db, site_id=site_id, remote_account=remote_account) if remote_account else {}
    usage_snapshot = remote_usage_snapshot(remote_account, cumulative_usage=cumulative_usage) if remote_account else {}
    snapshot_updates: dict[str, Any] = {
        "metadata.sub2api_delete_remote_snapshot": remote_account,
        "metadata.sub2api_delete_usage_snapshot": usage_snapshot,
        "metadata.sub2api_delete_remote_last_used_at": remote_account.get("last_used_at") if remote_account else None,
        "metadata.sub2api_delete_remote_status": remote_account.get("status") if remote_account else None,
        "metadata.sub2api_delete_remote_error_message": remote_account.get("error_message") if remote_account else None,
    }
    result = await client.delete_account(remote_id)
    await db.sub2api_accounts_cache.delete_one({"site_id": site_id, "sub2api_account_id": remote_id})
    await db.accounts.update_one({"_id": account["_id"]}, {"$set": snapshot_updates})
    await write_account_operation(
        db,
        operation_class="push_error_remote_deleted",
        operation_name="删除远端推送错误账号",
        remark_zh="处理决定后，从远端推送错误池删除账号。",
        actor=actor,
        account_id=str(account["_id"]),
        details={"site_id": site_id, "remote_account_id": remote_id, "delete_result": result, "remote_snapshot": remote_account},
    )
    return {"ok": True, "site_id": site_id, "remote_account_id": remote_id, "delete_result": result}


def _account_oid(account_id: str) -> Any:
    try:
        return object_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found") from exc
