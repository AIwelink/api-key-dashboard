from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from app.schemas import OperationsSitePermissionsUpdate


OPERATIONS_SITE_IDS = ("aiwelink", "aigclink")
AVAILABLE_OPERATIONS_SITES = (
    {"id": "aiwelink", "label": "AIWeLink"},
    {"id": "aigclink", "label": "AIGCLink"},
)


class OperationsSitePermissionsValidationError(ValueError):
    pass


class OperationsSitePermissionsConflictError(ValueError):
    pass


def normalize_operations_site_ids(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    requested_ids = {site_id for site_id in value if isinstance(site_id, str) and site_id in OPERATIONS_SITE_IDS}
    return [site_id for site_id in OPERATIONS_SITE_IDS if site_id in requested_ids]


async def get_operations_site_permissions(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    users = await list_operations_site_permission_users(db)
    return {
        "available_sites": [dict(site) for site in AVAILABLE_OPERATIONS_SITES],
        "users": users,
    }


async def list_operations_site_permission_users(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    cursor = db.users.find({}).sort("created_at", -1)
    return [_public_user_permissions(user) async for user in cursor]


async def update_operations_site_permissions(
    db: AsyncIOMotorDatabase,
    *,
    payload: OperationsSitePermissionsUpdate,
) -> dict[str, Any]:
    current_users = await list_operations_site_permission_users(db)
    current_user_ids = {user["user_id"] for user in current_users}
    submitted_by_user_id = {entry.user_id: normalize_operations_site_ids(entry.operations_site_ids) for entry in payload.users}
    submitted_user_ids = set(submitted_by_user_id)
    unknown_user_ids = submitted_user_ids - current_user_ids
    missing_user_ids = current_user_ids - submitted_user_ids
    if unknown_user_ids or missing_user_ids:
        details = []
        if unknown_user_ids:
            details.append(f"Unknown users: {', '.join(sorted(unknown_user_ids))}")
        if missing_user_ids:
            details.append(f"Missing users: {', '.join(sorted(missing_user_ids))}")
        raise OperationsSitePermissionsValidationError("; ".join(details))

    operations = [
        UpdateOne({"_id": user_id}, {"$set": {"operations_site_ids": submitted_by_user_id[user_id]}})
        for user_id in sorted(current_user_ids)
    ]
    if operations:
        async with await db.client.start_session() as session:
            async with session.start_transaction():
                result = await db.users.bulk_write(operations, ordered=True, session=session)
                if result.matched_count != len(operations):
                    raise OperationsSitePermissionsConflictError("One or more users no longer exist")

    return await get_operations_site_permissions(db)


def _public_user_permissions(user: dict[str, Any]) -> dict[str, Any]:
    return {
        "user_id": str(user["_id"]),
        "email": user.get("email"),
        "name": user.get("name"),
        "role": user.get("role"),
        "status": user.get("status"),
        "operations_site_ids": normalize_operations_site_ids(user.get("operations_site_ids")),
    }
