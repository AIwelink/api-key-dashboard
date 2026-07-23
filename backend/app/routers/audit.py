from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.security import get_current_user
from app.modules.system.permissions import require_view_permission
from app.utils import serialize_doc


router = APIRouter(prefix="/audit-logs", tags=["audit"], dependencies=[Depends(require_view_permission("logs"))])


@router.get("")
async def list_audit_logs(
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    items = [serialize_doc(item) async for item in db.audit_logs.find({}).sort("created_at", -1).limit(200)]
    return {"items": items, "total": len(items)}
