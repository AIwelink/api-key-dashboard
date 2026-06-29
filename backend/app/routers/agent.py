from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.security import require_roles
from app.services.agent_capacity import list_agent_pools
from app.services.agent_chat import analyze_pool, chat
from app.services.agent_tools import tool_manifest


router = APIRouter(prefix="/agent", tags=["agent"])


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    pool_id: str | None = None


@router.get("/tools")
async def get_agent_tools(
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
) -> dict:
    return tool_manifest()


@router.get("/pools")
async def get_agent_pools(
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_pools(db)


@router.post("/pools/{pool_id}/analyze")
async def analyze_agent_pool(
    pool_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await analyze_pool(db, pool_id=pool_id)


@router.post("/chat")
async def post_agent_chat(
    payload: AgentChatRequest,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await chat(db, message=payload.message, pool_id=payload.pool_id)
