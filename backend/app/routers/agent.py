from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.security import require_roles
from app.modules.agent.capacity import list_agent_pools
from app.modules.agent.chat import analyze_pool, chat
from app.modules.agent.memory import get_agent_latest_state, list_agent_messages, list_agent_runs
from app.modules.system.audit import write_audit_log
from app.modules.agent.tools import tool_manifest


router = APIRouter(prefix="/agent", tags=["agent"])
AGENT_ROLES = ("owner", "admin", "maintainer")


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    pool_id: str | None = None
    conversation_id: str | None = None


@router.get("/tools")
async def get_agent_tools(
    _: dict = Depends(require_roles(*AGENT_ROLES)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await tool_manifest(db)


@router.get("/pools")
async def get_agent_pools(
    _: dict = Depends(require_roles(*AGENT_ROLES)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_pools(db)


@router.get("/state")
async def get_agent_state(
    pool_id: str | None = None,
    _: dict = Depends(require_roles(*AGENT_ROLES)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_agent_latest_state(db, pool_id=pool_id)


@router.get("/runs")
async def get_agent_runs(
    pool_id: str | None = None,
    limit: int = 20,
    _: dict = Depends(require_roles(*AGENT_ROLES)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_runs(db, pool_id=pool_id, limit=max(1, min(limit, 100)))


@router.get("/conversations/{conversation_id}/messages")
async def get_agent_conversation_messages(
    conversation_id: str,
    limit: int = 50,
    _: dict = Depends(require_roles(*AGENT_ROLES)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_messages(db, conversation_id=conversation_id, limit=max(1, min(limit, 200)))


@router.post("/pools/{pool_id}/analyze")
async def analyze_agent_pool(
    pool_id: str,
    actor: dict = Depends(require_roles(*AGENT_ROLES)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await analyze_pool(db, pool_id=pool_id, actor=actor)
    await _write_agent_run_audit(db, actor=actor, action="agent.analyze", result=result)
    return result


@router.post("/chat")
async def post_agent_chat(
    payload: AgentChatRequest,
    actor: dict = Depends(require_roles(*AGENT_ROLES)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await chat(db, message=payload.message, pool_id=payload.pool_id, actor=actor, conversation_id=payload.conversation_id)
    await _write_agent_run_audit(db, actor=actor, action="agent.chat", result=result)
    return result


async def _write_agent_run_audit(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict,
    action: str,
    result: dict,
) -> None:
    run_id = str(result.get("run_id") or "")
    pool = result.get("pool") if isinstance(result.get("pool"), dict) else {}
    await write_audit_log(
        db,
        actor=actor,
        action=action,
        resource_type="agent_run",
        resource_id=run_id or None,
        after={
            "run_id": run_id or None,
            "conversation_id": result.get("conversation_id"),
            "decision_id": result.get("decision_id"),
            "pool_id": pool.get("id") or result.get("pool_id"),
            "severity": result.get("severity"),
            "trigger": result.get("trigger"),
        },
    )
