from __future__ import annotations

import logging
from math import ceil
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.utils import now_utc, object_id, serialize_doc


logger = logging.getLogger("app.pool_lifecycle")

POOL_STATUS_LIBRARY = "library"
POOL_STATUS_AVAILABLE = "available"
POOL_STATUS_RESERVE = "reserve"
POOL_STATUS_ACTIVE = "active"
POOL_STATUS_PROBLEM = "problem"
POOL_STATUS_DISCARDED = "discarded"
MANUAL_POOL_STATUSES = {
    POOL_STATUS_LIBRARY,
    POOL_STATUS_AVAILABLE,
    POOL_STATUS_RESERVE,
    POOL_STATUS_ACTIVE,
    POOL_STATUS_PROBLEM,
    POOL_STATUS_DISCARDED,
}

TODO_OPEN = "open"


def actor_name(actor: dict[str, Any] | None) -> str | None:
    if not actor:
        return None
    return actor.get("name") or actor.get("email") or actor.get("_id")


async def write_pool_action(
    db: AsyncIOMotorDatabase,
    *,
    action_type: str,
    actor: dict[str, Any] | None = None,
    account_id: str | None = None,
    pool_id: str | None = None,
    status_value: str = "succeeded",
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    remote_snapshot: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    now = now_utc()
    doc = {
        "action_type": action_type,
        "account_id": account_id,
        "pool_id": pool_id,
        "status": status_value,
        "reason": reason,
        "before": before or {},
        "after": after or {},
        "remote_snapshot": remote_snapshot or {},
        "error": error,
        "created_by": actor.get("_id") if actor else None,
        "created_by_name": actor_name(actor),
        "created_at": now,
        "finished_at": now if status_value in {"succeeded", "failed"} else None,
    }
    result = await db.pool_actions.insert_one(doc)
    created = await db.pool_actions.find_one({"_id": result.inserted_id})
    return serialize_doc(created)


async def upsert_todo(
    db: AsyncIOMotorDatabase,
    *,
    todo_type: str,
    pool_id: str | None,
    title: str,
    summary: dict[str, Any],
    suggested_action: str | None = None,
) -> dict[str, Any]:
    now = now_utc()
    dedupe_key = f"{todo_type}:{pool_id or 'global'}"
    result = await db.todo_items.find_one_and_update(
        {"dedupe_key": dedupe_key, "status": TODO_OPEN},
        {
            "$set": {
                "title": title,
                "todo_type": todo_type,
                "pool_id": pool_id,
                "summary": summary,
                "suggested_action": suggested_action,
                "updated_at": now,
            },
            "$setOnInsert": {
                "dedupe_key": dedupe_key,
                "status": TODO_OPEN,
                "created_at": now,
            },
            "$inc": {"occurrence_count": 1},
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return serialize_doc(result)


async def enter_reserve(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    pool_id: str,
    priority: int,
    reason: str | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    try:
        account_oid = object_id(account_id)
        pool_oid = object_id(pool_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account or pool not found") from exc

    pool = await db.api_pools.find_one({"_id": pool_oid, "status": {"$ne": "disabled"}})
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API pool not found")

    now = now_utc()
    result = await db.accounts.find_one_and_update(
        {
            "_id": account_oid,
            "metadata.deleted_at": {"$exists": False},
            "$or": [
                {"metadata.pool_status": {"$exists": False}},
                {"metadata.pool_status": {"$in": [POOL_STATUS_LIBRARY, POOL_STATUS_PROBLEM]}},
            ],
        },
        {
            "$set": {
                "metadata.pool_status": POOL_STATUS_RESERVE,
                "metadata.pool_id": pool_id,
                "metadata.priority": priority,
                "metadata.last_error": None,
                "metadata.updated_at": now,
                "metadata.updated_by_user_id": actor.get("_id"),
                "metadata.updated_by_name": actor_name(actor),
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if result is None:
        logger.warning("enter_reserve_rejected account_id=%s pool_id=%s actor=%s", account_id, pool_id, actor.get("_id"))
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Only library or problem accounts can enter reserve")

    await write_pool_action(
        db,
        action_type="enter_reserve",
        actor=actor,
        account_id=account_id,
        pool_id=pool_id,
        reason=reason,
        after={"pool_status": POOL_STATUS_RESERVE, "priority": priority},
    )
    logger.info("enter_reserve_succeeded account_id=%s pool_id=%s priority=%s actor=%s", account_id, pool_id, priority, actor.get("_id"))
    return serialize_doc(result)


async def manual_transfer_account(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    target_status: str,
    pool_id: str | None,
    priority: int | None,
    reason: str | None,
    last_error: str | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    if target_status not in MANUAL_POOL_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid target status")
    try:
        account_oid = object_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found") from exc

    account = await db.accounts.find_one({"_id": account_oid, "metadata.deleted_at": {"$exists": False}})
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    if target_status in {POOL_STATUS_RESERVE, POOL_STATUS_ACTIVE} and not pool_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="pool_id is required for reserve or active")
    pool_ref: dict[str, Any] | None = None
    if pool_id:
        pool_ref = await resolve_pool_reference(db, pool_id)

    metadata = dict(account.get("metadata", {}))
    before = {
        "pool_status": metadata.get("pool_status", POOL_STATUS_LIBRARY),
        "pool_id": metadata.get("pool_id"),
        "priority": metadata.get("priority"),
        "last_error": metadata.get("last_error"),
    }
    now = now_utc()
    updates: dict[str, Any] = {
        "metadata.pool_status": target_status,
        "metadata.updated_at": now,
        "metadata.updated_by_user_id": actor.get("_id"),
        "metadata.updated_by_name": actor_name(actor),
    }
    unsets: dict[str, str] = {}

    if target_status in {POOL_STATUS_RESERVE, POOL_STATUS_ACTIVE}:
        updates["metadata.pool_id"] = pool_id
        updates["metadata.priority"] = int(priority or 0)
        updates["metadata.last_error"] = None
        if pool_ref:
            updates.update(pool_ref)
    elif target_status == POOL_STATUS_PROBLEM:
        if pool_id:
            updates["metadata.pool_id"] = pool_id
        if pool_ref:
            updates.update(pool_ref)
        if priority is not None:
            updates["metadata.priority"] = int(priority)
        updates["metadata.last_error"] = last_error or reason or metadata.get("last_error") or "manual problem mark"
        updates["metadata.problem_marked_at"] = now
    elif target_status == POOL_STATUS_DISCARDED:
        updates["metadata.last_error"] = last_error or reason or metadata.get("last_error") or "manual discarded"
        updates["metadata.discarded_at"] = now
        unsets["metadata.pool_id"] = ""
        unsets.update(pool_reference_unsets())
        unsets["metadata.push_lock"] = ""
    else:
        updates["metadata.priority"] = int(priority or 0)
        updates["metadata.last_error"] = None
        unsets["metadata.pool_id"] = ""
        unsets.update(pool_reference_unsets())
        unsets["metadata.push_lock"] = ""

    update_doc: dict[str, Any] = {"$set": updates}
    if unsets:
        update_doc["$unset"] = unsets
    await db.accounts.update_one({"_id": account_oid}, update_doc)
    updated = await db.accounts.find_one({"_id": account_oid})
    after_metadata = dict(updated.get("metadata", {})) if updated else {}
    after = {
        "pool_status": after_metadata.get("pool_status"),
        "pool_id": after_metadata.get("pool_id"),
        "priority": after_metadata.get("priority"),
        "last_error": after_metadata.get("last_error"),
    }
    await write_pool_action(
        db,
        action_type="manual_transfer",
        actor=actor,
        account_id=account_id,
        pool_id=after.get("pool_id") or before.get("pool_id"),
        reason=reason,
        before=before,
        after=after,
    )
    logger.info(
        "manual_transfer_succeeded account_id=%s from=%s to=%s pool_id=%s actor=%s",
        account_id,
        before["pool_status"],
        target_status,
        after.get("pool_id"),
        actor.get("_id"),
    )
    return serialize_doc(updated)


async def resolve_pool_reference(db: AsyncIOMotorDatabase, pool_id: str) -> dict[str, Any]:
    try:
        pool_oid = object_id(pool_id)
    except ValueError:
        pool_oid = None
    if pool_oid is not None:
        pool = await db.api_pools.find_one({"_id": pool_oid, "status": {"$ne": "disabled"}})
        if pool is not None:
            return {
                "metadata.pool_ref_type": "api_pool",
                "metadata.api_pool_id": pool_id,
                "metadata.api_pool_name": pool.get("name"),
                "metadata.sub2api_site_id": pool.get("site_id"),
                "metadata.sub2api_group_id": pool.get("active_group_id"),
            }

    try:
        group_id = int(pool_id)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target group not found") from exc

    group_doc = await db.sub2api_groups_cache.find_one({"group_id": group_id})
    if group_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target group not found")
    group = group_doc.get("group", {})
    return {
        "metadata.pool_ref_type": "sub2api_group",
        "metadata.sub2api_site_id": group_doc.get("site_id", "default"),
        "metadata.sub2api_group_id": group_id,
        "metadata.sub2api_group_name": group.get("name") if isinstance(group, dict) else None,
    }


def pool_reference_unsets() -> dict[str, str]:
    return {
        "metadata.pool_ref_type": "",
        "metadata.api_pool_id": "",
        "metadata.api_pool_name": "",
        "metadata.sub2api_site_id": "",
        "metadata.sub2api_group_id": "",
        "metadata.sub2api_group_name": "",
    }


async def capacity_check(db: AsyncIOMotorDatabase, *, pool_id: str, actor: dict[str, Any]) -> dict[str, Any]:
    try:
        pool_oid = object_id(pool_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API pool not found") from exc

    pool = await db.api_pools.find_one({"_id": pool_oid})
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API pool not found")

    logger.info("capacity_check_start pool_id=%s actor=%s", pool_id, actor.get("_id"))
    active_group_id = pool["active_group_id"]
    account_type = pool.get("account_type")
    query = {"site_id": pool.get("site_id", "default"), "group_ids": active_group_id}
    remote_docs = [doc async for doc in db.sub2api_accounts_cache.find(query)]
    remote_accounts = [doc.get("account", {}) for doc in remote_docs]
    healthy_accounts = [account for account in remote_accounts if is_healthy_remote_account(account)]

    usage5 = observed_usage(healthy_accounts, "codex_5h_used_percent")
    usage7 = observed_usage(healthy_accounts, "codex_7d_used_percent")
    eligible_reserve_count = await count_eligible_reserve(db, pool_id=pool_id, account_type=account_type)
    suggestions = await suggest_reserve_accounts(
        db,
        pool_id=pool_id,
        account_type=account_type,
        need_count=max(0, int(pool.get("target_active", 30)) - len(healthy_accounts)),
    )

    metrics = {
        "healthy_active_count": len(healthy_accounts),
        "active_total_count": len(remote_accounts),
        "avg_5h_used_observed": usage5["avg"],
        "avg_7d_used_observed": usage7["avg"],
        "observed_5h_count": usage5["observed"],
        "observed_7d_count": usage7["observed"],
        "missing_5h_count": usage5["missing"],
        "missing_7d_count": usage7["missing"],
        "high_5h_count": usage5["high"],
        "high_7d_count": usage7["high"],
        "eligible_reserve_count": eligible_reserve_count,
    }
    triggered = evaluate_capacity(pool, metrics)
    for todo_type, summary, suggested_action in triggered:
        await upsert_todo(
            db,
            todo_type=todo_type,
            pool_id=pool_id,
            title=todo_title(todo_type, pool.get("name", "API pool")),
            summary=summary,
            suggested_action=suggested_action,
        )

    await write_pool_action(
        db,
        action_type="capacity_check",
        actor=actor,
        pool_id=pool_id,
        after={"metrics": metrics, "triggered": [item[0] for item in triggered]},
    )
    logger.info(
        "capacity_check_finished pool_id=%s healthy=%s active_total=%s reserve=%s triggered=%s",
        pool_id,
        metrics["healthy_active_count"],
        metrics["active_total_count"],
        metrics["eligible_reserve_count"],
        [item[0] for item in triggered],
    )
    return {
        "pool": serialize_doc(pool),
        "metrics": metrics,
        "triggered_todos": [item[0] for item in triggered],
        "suggested_account_ids": suggestions,
    }


def is_healthy_remote_account(account: dict[str, Any]) -> bool:
    return (
        account.get("status") == "active"
        and account.get("schedulable") is True
        and not account.get("error_message")
        and not account.get("rate_limited_at")
        and not account.get("temp_unschedulable_until")
    )


def observed_usage(accounts: list[dict[str, Any]], key: str) -> dict[str, int]:
    values: list[float] = []
    for account in accounts:
        extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
        value = extra.get(key)
        if isinstance(value, (int, float)):
            values.append(float(value))
    observed = len(values)
    missing = max(0, len(accounts) - observed)
    avg = round(sum(values) / observed) if observed else None
    high = sum(1 for value in values if value >= 90)
    return {"avg": avg, "observed": observed, "missing": missing, "high": high}


async def count_eligible_reserve(db: AsyncIOMotorDatabase, *, pool_id: str, account_type: str | None) -> int:
    query: dict[str, Any] = {
        "metadata.deleted_at": {"$exists": False},
        "metadata.pool_status": POOL_STATUS_RESERVE,
        "metadata.pool_id": pool_id,
        "$or": [{"metadata.last_error": {"$exists": False}}, {"metadata.last_error": None}, {"metadata.last_error": ""}],
    }
    if account_type:
        query["metadata.account_type"] = account_type
    return await db.accounts.count_documents(query)


async def suggest_reserve_accounts(
    db: AsyncIOMotorDatabase,
    *,
    pool_id: str,
    account_type: str | None,
    need_count: int,
) -> list[str]:
    if need_count <= 0:
        return []
    query: dict[str, Any] = {
        "metadata.deleted_at": {"$exists": False},
        "metadata.pool_status": POOL_STATUS_RESERVE,
        "metadata.pool_id": pool_id,
        "metadata.push_lock": {"$exists": False},
        "$or": [{"metadata.last_error": {"$exists": False}}, {"metadata.last_error": None}, {"metadata.last_error": ""}],
    }
    if account_type:
        query["metadata.account_type"] = account_type
    cursor = db.accounts.find(query).sort([("metadata.priority", -1), ("metadata.updated_at", 1), ("metadata.created_at", 1)]).limit(need_count)
    return [str(doc["_id"]) async for doc in cursor]


def evaluate_capacity(pool: dict[str, Any], metrics: dict[str, Any]) -> list[tuple[str, dict[str, Any], str]]:
    triggered: list[tuple[str, dict[str, Any], str]] = []
    healthy = metrics["healthy_active_count"]
    min_active = int(pool.get("min_active", 20))
    max_5h = int(pool.get("max_avg_5h_used", 70))
    max_7d = int(pool.get("max_avg_7d_used", 80))
    min_reserve = int(pool.get("min_reserve", 10))

    if healthy < min_active:
        triggered.append(("need_more_accounts", {"reason": "healthy active accounts below minimum", **metrics}, "add reserve accounts to active pool"))
    if metrics["avg_5h_used_observed"] is not None and metrics["avg_5h_used_observed"] >= max_5h:
        triggered.append(("need_more_accounts", {"reason": "5h average usage is high", **metrics}, "add more active capacity"))
    if metrics["avg_7d_used_observed"] is not None and metrics["avg_7d_used_observed"] >= max_7d:
        triggered.append(("need_more_accounts", {"reason": "7d average usage is high", **metrics}, "add more active capacity"))
    if metrics["eligible_reserve_count"] < min_reserve:
        triggered.append(("reserve_low", {"reason": "eligible reserve accounts below minimum", **metrics}, "prepare more reserve accounts"))

    missing_5h_ratio = missing_ratio(metrics["missing_5h_count"], healthy)
    missing_7d_ratio = missing_ratio(metrics["missing_7d_count"], healthy)
    if missing_5h_ratio > 0.3 or missing_7d_ratio > 0.3:
        triggered.append(("capacity_data_incomplete", {"reason": "usage observation data is incomplete", **metrics}, "refresh or inspect sub2api usage fields"))

    high_threshold = max(3, ceil(healthy * 0.3))
    if healthy and (metrics["high_5h_count"] >= high_threshold or metrics["high_7d_count"] >= high_threshold):
        triggered.append(("need_more_accounts", {"reason": "too many accounts are near usage limit", **metrics}, "add more active capacity"))
    return dedupe_triggered(triggered)


def missing_ratio(missing: int, total: int) -> float:
    return missing / total if total > 0 else 0


def dedupe_triggered(items: list[tuple[str, dict[str, Any], str]]) -> list[tuple[str, dict[str, Any], str]]:
    result: dict[str, tuple[str, dict[str, Any], str]] = {}
    for item in items:
        key = item[0]
        if key not in result:
            result[key] = item
    return list(result.values())


def todo_title(todo_type: str, pool_name: str) -> str:
    labels = {
        "need_more_accounts": "账号池需要补充账号",
        "reserve_low": "备用池账号不足",
        "capacity_data_incomplete": "账号池容量数据不完整",
    }
    return f"{pool_name}: {labels.get(todo_type, todo_type)}"
