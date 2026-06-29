from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.security import require_roles
from app.modules.system.audit import write_audit_log


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/sync-policy")
async def get_sync_policy(
    _: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    policy = await db.app_settings.find_one({"_id": "sync_policy"})
    if not policy:
        return {"auto_sync": False, "interval_minutes": 30, "auto_pause_on_expired": True}
    policy.pop("_id", None)
    return policy


@router.patch("/sync-policy")
async def update_sync_policy(
    payload: dict,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    await db.app_settings.update_one({"_id": "sync_policy"}, {"$set": payload}, upsert=True)
    await write_audit_log(db, actor=actor, action="settings.sync_policy.update", resource_type="setting", resource_id="sync_policy")
    return await get_sync_policy(actor, db)
