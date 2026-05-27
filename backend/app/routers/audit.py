from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.security import require_roles
from app.utils import serialize_doc


router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("")
async def list_audit_logs(
    _: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    items = [serialize_doc(item) async for item in db.audit_logs.find({}).sort("created_at", -1).limit(200)]
    return {"items": items, "total": len(items)}

