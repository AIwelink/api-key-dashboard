from datetime import timedelta
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.utils import now_utc, object_id, serialize_doc


TODO_ACCOUNT_PROJECTION = {
    "metadata": 1,
    "account_json.name": 1,
    "account_json.platform": 1,
    "account_json.type": 1,
    "account_json.extra.email": 1,
    "account_json.extra.email_session": 1,
    "account_json.extra.mailbox_connection": 1,
    "account_json.extra.2FA": 1,
    "account_json.extra.password": 1,
    "account_json.credentials.email": 1,
    "account_json.credentials.plan_type": 1,
}

TASK_TYPE = "free_to_plus"
STATUS_PENDING = "pending"
STATUS_PROCESSING = "processing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
ELIGIBLE_POOL_STATUSES = ["library", "available"]
LOCK_TTL = timedelta(hours=2)


def actor_name(actor: dict[str, Any]) -> str:
    return str(actor.get("name") or actor.get("email") or actor.get("_id") or "")


def eligible_base_query() -> dict[str, Any]:
    return {
        "metadata.deleted_at": {"$exists": False},
        "metadata.account_type": "free",
        "$and": [
            {
                "$or": [
                    {"metadata.pool_status": {"$exists": False}},
                    {"metadata.pool_status": {"$in": ELIGIBLE_POOL_STATUSES}},
                ]
            },
            {
                "$or": [
                    {"metadata.email_session": {"$exists": True, "$nin": [None, ""]}},
                    {"account_json.extra.email_session": {"$exists": True, "$nin": [None, ""]}},
                ]
            },
        ],
    }


def eligible_pool_query() -> dict[str, Any]:
    return {
        "$or": [
            {"metadata.pool_status": {"$exists": False}},
            {"metadata.pool_status": {"$in": ELIGIBLE_POOL_STATUSES}},
        ]
    }


def status_query(status_filter: str) -> dict[str, Any]:
    base = eligible_base_query()
    existing_task = {"metadata.upgrade_task_type": TASK_TYPE, **eligible_pool_query()}
    if status_filter == "processing":
        return {**existing_task, "metadata.upgrade_status": STATUS_PROCESSING}
    if status_filter == "completed":
        return {**existing_task, "metadata.upgrade_status": STATUS_COMPLETED}
    if status_filter == "failed":
        return {**existing_task, "metadata.upgrade_status": STATUS_FAILED}
    if status_filter == "all":
        return {"$or": [base, existing_task], "metadata.deleted_at": {"$exists": False}}
    if status_filter == "pending":
        return {
            **base,
            "$or": [
                {"metadata.upgrade_status": {"$exists": False}},
                {"metadata.upgrade_status": STATUS_PENDING},
            ],
        }
    return {
        "$or": [
            {
                **base,
                "$or": [
                    {"metadata.upgrade_status": {"$exists": False}},
                    {"metadata.upgrade_status": STATUS_PENDING},
                ],
            },
            {**existing_task, "metadata.upgrade_status": STATUS_PROCESSING},
        ],
        "metadata.deleted_at": {"$exists": False},
    }


def with_search(query: dict[str, Any], q: str | None) -> dict[str, Any]:
    if not q:
        return query
    search = {
        "$or": [
            {"metadata.email": {"$regex": q, "$options": "i"}},
            {"account_json.name": {"$regex": q, "$options": "i"}},
            {"metadata.purchase_source": {"$regex": q, "$options": "i"}},
            {"metadata.remark": {"$regex": q, "$options": "i"}},
            {"metadata.upgrade_assignee_name": {"$regex": q, "$options": "i"}},
        ]
    }
    return {"$and": [query, search]}


async def list_free_to_plus_accounts(
    db: AsyncIOMotorDatabase,
    *,
    status_filter: str,
    q: str | None,
    skip: int,
    limit: int,
) -> dict[str, Any]:
    query = with_search(status_query(status_filter), q)
    cursor = db.accounts.find(query, TODO_ACCOUNT_PROJECTION).sort("metadata.updated_at", -1).skip(skip).limit(limit)
    items = [serialize_doc(account) async for account in cursor]
    total = await db.accounts.count_documents(query)
    stats = {
        "pending": await db.accounts.count_documents(status_query("pending")),
        "processing": await db.accounts.count_documents(status_query("processing")),
        "completed": await db.accounts.count_documents(status_query("completed")),
        "failed": await db.accounts.count_documents(status_query("failed")),
    }
    return {"items": items, "total": total, "skip": skip, "limit": limit, "stats": stats}


async def start_free_to_plus(db: AsyncIOMotorDatabase, *, account_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    try:
        account_oid = object_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found") from exc

    now = now_utc()
    lock_expires_at = now + LOCK_TTL
    query = {
        "_id": account_oid,
        **eligible_base_query(),
        "$and": [
            *eligible_base_query()["$and"],
            {
                "$or": [
                    {"metadata.upgrade_status": {"$exists": False}},
                    {"metadata.upgrade_status": {"$in": [STATUS_PENDING, STATUS_FAILED]}},
                    {
                        "metadata.upgrade_status": STATUS_PROCESSING,
                        "metadata.upgrade_lock.locked_by_user_id": actor.get("_id"),
                    },
                    {
                        "metadata.upgrade_status": STATUS_PROCESSING,
                        "metadata.upgrade_lock.expires_at": {"$lte": now},
                    },
                ]
            },
            {
                "$or": [
                    {"metadata.upgrade_lock": {"$exists": False}},
                    {"metadata.upgrade_lock.locked_by_user_id": actor.get("_id")},
                    {"metadata.upgrade_lock.expires_at": {"$lte": now}},
                ]
            },
        ],
    }
    result = await db.accounts.find_one_and_update(
        query,
        {
            "$set": {
                "metadata.upgrade_task_type": TASK_TYPE,
                "metadata.upgrade_status": STATUS_PROCESSING,
                "metadata.upgrade_from": "free",
                "metadata.upgrade_to": "plus",
                "metadata.upgrade_assignee_user_id": actor.get("_id"),
                "metadata.upgrade_assignee_name": actor_name(actor),
                "metadata.upgrade_started_at": now,
                "metadata.upgrade_lock": {
                    "task_type": TASK_TYPE,
                    "locked_by_user_id": actor.get("_id"),
                    "locked_by_name": actor_name(actor),
                    "locked_at": now,
                    "expires_at": lock_expires_at,
                },
                "metadata.updated_at": now,
                "metadata.updated_by_user_id": actor.get("_id"),
                "metadata.updated_by_name": actor_name(actor),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account is not eligible or is locked by another user")
    return serialize_doc(result)


async def release_free_to_plus(db: AsyncIOMotorDatabase, *, account_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    return await update_locked_task(
        db,
        account_id=account_id,
        actor=actor,
        updates={
            "metadata.upgrade_status": STATUS_PENDING,
            "metadata.updated_at": now_utc(),
            "metadata.updated_by_user_id": actor.get("_id"),
            "metadata.updated_by_name": actor_name(actor),
        },
        unset={"metadata.upgrade_lock": "", "metadata.upgrade_returned_from_completed": ""},
        not_found_detail="Only the current handler can cancel this task",
    )


async def complete_free_to_plus(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    payment_type: str,
    note: str | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    now = now_utc()
    updates = {
        "metadata.account_type": "plus",
        "metadata.payment_type": payment_type,
        "metadata.pool_status": "available",
        "metadata.upgrade_status": STATUS_COMPLETED,
        "metadata.upgrade_completed_at": now,
        "metadata.upgrade_note": note or "",
        "metadata.updated_at": now,
        "metadata.updated_by_user_id": actor.get("_id"),
        "metadata.updated_by_name": actor_name(actor),
        "account_json.extra.account_type": "plus",
        "account_json.extra.payment_type": payment_type,
        "account_json.credentials.plan_type": "plus",
    }
    return await update_locked_task(
        db,
        account_id=account_id,
        actor=actor,
        updates=updates,
        unset={"metadata.upgrade_lock": "", "metadata.upgrade_returned_from_completed": ""},
        not_found_detail="Only the current handler can complete this task",
    )


async def return_completed_free_to_plus(db: AsyncIOMotorDatabase, *, account_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    try:
        account_oid = object_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found") from exc

    now = now_utc()
    lock_expires_at = now + LOCK_TTL
    result = await db.accounts.find_one_and_update(
        {
            "_id": account_oid,
            "metadata.deleted_at": {"$exists": False},
            "metadata.upgrade_task_type": TASK_TYPE,
            "metadata.upgrade_status": STATUS_COMPLETED,
            **eligible_pool_query(),
            "$or": [
                {"metadata.upgrade_lock": {"$exists": False}},
                {"metadata.upgrade_lock.locked_by_user_id": actor.get("_id")},
                {"metadata.upgrade_lock.expires_at": {"$lte": now}},
            ],
        },
        {
            "$set": {
                "metadata.upgrade_status": STATUS_PROCESSING,
                "metadata.upgrade_returned_from_completed": True,
                "metadata.upgrade_returned_at": now,
                "metadata.upgrade_assignee_user_id": actor.get("_id"),
                "metadata.upgrade_assignee_name": actor_name(actor),
                "metadata.upgrade_lock": {
                    "task_type": TASK_TYPE,
                    "locked_by_user_id": actor.get("_id"),
                    "locked_by_name": actor_name(actor),
                    "locked_at": now,
                    "expires_at": lock_expires_at,
                },
                "metadata.updated_at": now,
                "metadata.updated_by_user_id": actor.get("_id"),
                "metadata.updated_by_name": actor_name(actor),
            },
            "$unset": {
                "metadata.upgrade_error": "",
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only completed tasks in library or available can be returned to processing")
    return serialize_doc(result)


async def fail_free_to_plus(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    error: str,
    note: str | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    now = now_utc()
    return await update_locked_task(
        db,
        account_id=account_id,
        actor=actor,
        updates={
            "metadata.upgrade_status": STATUS_FAILED,
            "metadata.upgrade_failed_at": now,
            "metadata.upgrade_error": error,
            "metadata.upgrade_note": note or "",
            "metadata.updated_at": now,
            "metadata.updated_by_user_id": actor.get("_id"),
            "metadata.updated_by_name": actor_name(actor),
        },
        unset={"metadata.upgrade_lock": "", "metadata.upgrade_returned_from_completed": ""},
        not_found_detail="Only the current handler can fail this task",
    )


async def update_locked_task(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    actor: dict[str, Any],
    updates: dict[str, Any],
    unset: dict[str, Any],
    not_found_detail: str,
) -> dict[str, Any]:
    try:
        account_oid = object_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found") from exc

    query = {
        "_id": account_oid,
        "metadata.deleted_at": {"$exists": False},
        "metadata.upgrade_task_type": TASK_TYPE,
        "metadata.upgrade_status": STATUS_PROCESSING,
        "metadata.upgrade_lock.locked_by_user_id": actor.get("_id"),
        **eligible_pool_query(),
    }
    update: dict[str, Any] = {"$set": updates}
    if unset:
        update["$unset"] = unset
    result = await db.accounts.find_one_and_update(query, update, return_document=ReturnDocument.AFTER)
    if result is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=not_found_detail)
    return serialize_doc(result)
