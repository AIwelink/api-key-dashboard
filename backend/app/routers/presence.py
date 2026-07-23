from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.modules.system.presence import get_frontend_presence_history, list_active_frontend_presence, record_frontend_presence, remove_frontend_presence
from app.schemas import FrontendPresenceHeartbeat, FrontendPresenceLeave
from app.security import get_current_user
from app.modules.system.permissions import require_view_permission


router = APIRouter(prefix="/presence", tags=["presence"])


@router.post("/heartbeat")
async def post_presence_heartbeat(
    payload: FrontendPresenceHeartbeat,
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    try:
        await record_frontend_presence(db, actor=actor, payload=payload.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    return {"ok": True}


@router.post("/leave")
async def post_presence_leave(
    payload: FrontendPresenceLeave,
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    removed = await remove_frontend_presence(db, actor=actor, client_id=payload.client_id, session_id=payload.session_id)
    return {"ok": True, "removed": removed}


@router.get("")
async def get_active_presence(
    _: dict = Depends(require_view_permission("presence")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_active_frontend_presence(db)


@router.get("/history")
async def get_presence_history(
    _: dict = Depends(require_view_permission("presence")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_frontend_presence_history(db)
