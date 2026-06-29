from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.accounts.pool_lifecycle import actor_name
from app.utils import now_utc, serialize_doc


async def write_account_operation(
    db: AsyncIOMotorDatabase,
    *,
    operation_class: str,
    operation_name: str,
    remark_zh: str,
    actor: dict[str, Any] | None = None,
    account_id: str | None = None,
    status_value: str = "succeeded",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now_utc()
    doc = {
        "operation_class": operation_class,
        "operation_name": operation_name,
        "remark_zh": remark_zh,
        "account_id": account_id,
        "status": status_value,
        "details": details or {},
        "created_by": actor.get("_id") if actor else None,
        "created_by_name": actor_name(actor),
        "occurred_at": now,
        "created_at": now,
    }
    result = await db.account_operations.insert_one(doc)
    created = await db.account_operations.find_one({"_id": result.inserted_id})
    return serialize_doc(created)


async def write_account_problem(
    db: AsyncIOMotorDatabase,
    *,
    problem_class: str,
    problem_name: str,
    remark_zh: str,
    account_id: str | None = None,
    severity: str = "warning",
    status_value: str = "open",
    site_id: str | None = None,
    remote_account_id: Any = None,
    details: dict[str, Any] | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now_utc()
    doc = {
        "problem_class": problem_class,
        "problem_name": problem_name,
        "remark_zh": remark_zh,
        "account_id": account_id,
        "severity": severity,
        "status": status_value,
        "site_id": site_id,
        "remote_account_id": remote_account_id,
        "details": details or {},
        "created_by": actor.get("_id") if actor else None,
        "created_by_name": actor_name(actor),
        "occurred_at": now,
        "created_at": now,
    }
    result = await db.account_problems.insert_one(doc)
    created = await db.account_problems.find_one({"_id": result.inserted_id})
    return serialize_doc(created)
