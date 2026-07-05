from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.controller import run_agent_controller


async def analyze_pool(
    db: AsyncIOMotorDatabase,
    *,
    pool_id: str,
    user_message: str | None = None,
    trigger: str = "manual_analyze",
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await run_agent_controller(
        db,
        trigger=trigger,
        user_message=user_message or "分析这个账号池当前是否需要补号，是否存在容量、事件或探测风险。",
        pool_id=pool_id,
        actor=actor,
    )


async def chat(
    db: AsyncIOMotorDatabase,
    *,
    message: str,
    pool_id: str | None = None,
    actor: dict[str, Any] | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    normalized = message.strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message is required")
    return await run_agent_controller(
        db,
        trigger="manual_chat",
        user_message=normalized,
        pool_id=pool_id,
        actor=actor,
        conversation_id=conversation_id,
    )
