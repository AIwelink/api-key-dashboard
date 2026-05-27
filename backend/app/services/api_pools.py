import logging
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils import now_utc, object_id, serialize_doc


logger = logging.getLogger("app.api_pools")

DEFAULT_POOL_VALUES = {
    "min_active": 20,
    "target_active": 30,
    "max_avg_5h_used": 70,
    "max_avg_7d_used": 80,
    "min_reserve": 10,
    "status": "active",
}


async def list_api_pools(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    items = [serialize_doc(item) async for item in db.api_pools.find({}).sort("created_at", -1)]
    return {"items": items, "total": len(items)}


async def create_api_pool(db: AsyncIOMotorDatabase, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    now = now_utc()
    doc = {
        **DEFAULT_POOL_VALUES,
        **payload,
        "created_by": actor.get("_id"),
        "created_by_name": actor.get("name") or actor.get("email"),
        "created_at": now,
        "updated_at": now,
    }
    result = await db.api_pools.insert_one(doc)
    created = await db.api_pools.find_one({"_id": result.inserted_id})
    logger.info(
        "api_pool_created pool_id=%s name=%s account_type=%s active_group_id=%s actor=%s",
        str(result.inserted_id),
        doc.get("name"),
        doc.get("account_type"),
        doc.get("active_group_id"),
        actor.get("_id"),
    )
    return serialize_doc(created)


async def get_api_pool_or_404(db: AsyncIOMotorDatabase, pool_id: str) -> dict[str, Any]:
    try:
        oid = object_id(pool_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API pool not found") from exc
    pool = await db.api_pools.find_one({"_id": oid})
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API pool not found")
    return pool


async def update_api_pool(db: AsyncIOMotorDatabase, pool_id: str, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    pool = await get_api_pool_or_404(db, pool_id)
    updates = {key: value for key, value in payload.items() if value is not None}
    updates["updated_at"] = now_utc()
    updates["updated_by"] = actor.get("_id")
    updates["updated_by_name"] = actor.get("name") or actor.get("email")
    await db.api_pools.update_one({"_id": pool["_id"]}, {"$set": updates})
    updated = await db.api_pools.find_one({"_id": pool["_id"]})
    logger.info("api_pool_updated pool_id=%s fields=%s actor=%s", pool_id, sorted(updates.keys()), actor.get("_id"))
    return serialize_doc(updated)
