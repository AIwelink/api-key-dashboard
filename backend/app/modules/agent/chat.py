from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.orchestrator import run_agent_analysis


async def analyze_pool(
    db: AsyncIOMotorDatabase,
    *,
    pool_id: str,
    user_message: str | None = None,
    trigger: str = "manual_analyze",
) -> dict[str, Any]:
    return await run_agent_analysis(
        db,
        pool_id=pool_id,
        user_message=user_message or "分析这个账号池当前是否需要补号、是否存在容量或探测风险。",
        trigger=trigger,
        allow_planning=True,
    )


async def chat(
    db: AsyncIOMotorDatabase,
    *,
    message: str,
    pool_id: str | None = None,
) -> dict[str, Any]:
    normalized = message.strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="message is required")
    return await run_agent_analysis(
        db,
        pool_id=pool_id,
        user_message=normalized,
        trigger="manual_chat",
        allow_planning=True,
    )
