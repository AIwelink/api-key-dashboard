from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils import now_utc, serialize_doc


API_POOL_STATUS_PREFERENCES_ID = "api_pool_status_preferences"


async def get_api_pool_status_preferences(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    doc = await db.app_settings.find_one({"_id": API_POOL_STATUS_PREFERENCES_ID}) or {}
    return {
        "pinned_site_id": doc.get("pinned_site_id"),
        "pinned_group_id": doc.get("pinned_group_id"),
        "updated_at": doc.get("updated_at"),
        "updated_by_user_id": doc.get("updated_by_user_id"),
        "updated_by_name": doc.get("updated_by_name"),
    }


async def update_api_pool_status_preferences(
    db: AsyncIOMotorDatabase,
    payload: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if "pinned_site_id" in payload:
        value = payload.get("pinned_site_id")
        updates["pinned_site_id"] = str(value).strip() if value else None
    if "pinned_group_id" in payload:
        value = payload.get("pinned_group_id")
        updates["pinned_group_id"] = int(value) if value is not None else None

    now = now_utc()
    updates.update(
        {
            "updated_at": now,
            "updated_by_user_id": actor.get("_id"),
            "updated_by_name": actor.get("name") or actor.get("email") or actor.get("_id"),
        }
    )
    await db.app_settings.update_one(
        {"_id": API_POOL_STATUS_PREFERENCES_ID},
        {"$set": updates, "$setOnInsert": {"created_at": now}},
        upsert=True,
    )
    return serialize_doc(await get_api_pool_status_preferences(db))
