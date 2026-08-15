from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.modules.system.permissions import require_view_permission
from app.modules.work_plans.domain import (
    WorkPlanConflictError,
    WorkPlanRuleError,
    is_plan_manager,
)
from app.modules.work_plans.schemas import WorkPlanCreate, WorkPlanUpdate
from app.modules.work_plans.service import (
    WorkPlanAccessError,
    WorkPlanNotFoundError,
    WorkPlanPermissionError,
    cancel_work_plan,
    create_work_plans,
    list_my_work_plans,
    list_work_plan_schedule,
    require_browser_actor,
    update_work_plan,
)


WORK_PLAN_PERMISSION = require_view_permission("work-plans")
router = APIRouter(prefix="/work-plans", tags=["work-plans"])


@router.get("/schedule")
async def get_work_plan_schedule(
    range_name: Literal["7d", "30d", "all"] = Query(default="7d", alias="range"),
    member_ids: list[str] | None = Query(default=None, alias="member_id"),
    include_cancelled: bool = Query(default=False),
    actor: dict = Depends(WORK_PLAN_PERMISSION),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    _require_browser_actor(actor)
    if include_cancelled and not is_plan_manager(actor):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="只有管理员可以查看已取消的团队计划",
        )
    try:
        return await list_work_plan_schedule(
            db,
            range_name=range_name,
            member_ids=member_ids,
            include_cancelled=include_cancelled,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/mine")
async def get_my_work_plans(
    limit: int = Query(default=1_000, ge=1, le=4_000),
    actor: dict = Depends(WORK_PLAN_PERMISSION),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    _require_browser_actor(actor)
    return await list_my_work_plans(db, actor=actor, limit=limit)


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_work_plans(
    payload: WorkPlanCreate,
    actor: dict = Depends(WORK_PLAN_PERMISSION),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    _require_browser_actor(actor)
    try:
        return await create_work_plans(db, actor=actor, payload=payload)
    except WorkPlanRuleError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.patch("/{plan_id}")
async def patch_work_plan(
    plan_id: str,
    payload: WorkPlanUpdate,
    actor: dict = Depends(WORK_PLAN_PERMISSION),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    _require_browser_actor(actor)
    try:
        return await update_work_plan(
            db,
            plan_id=plan_id,
            actor=actor,
            payload=payload,
        )
    except (
        WorkPlanNotFoundError,
        WorkPlanPermissionError,
        WorkPlanAccessError,
        WorkPlanConflictError,
        WorkPlanRuleError,
    ) as exc:
        _raise_http_error(exc)


@router.post("/{plan_id}/cancel")
async def post_cancel_work_plan(
    plan_id: str,
    actor: dict = Depends(WORK_PLAN_PERMISSION),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    _require_browser_actor(actor)
    try:
        return await cancel_work_plan(db, plan_id=plan_id, actor=actor)
    except (
        WorkPlanNotFoundError,
        WorkPlanPermissionError,
        WorkPlanAccessError,
        WorkPlanConflictError,
        WorkPlanRuleError,
    ) as exc:
        _raise_http_error(exc)


def _require_browser_actor(actor: dict) -> None:
    try:
        require_browser_actor(actor)
    except WorkPlanAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        ) from exc


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, WorkPlanNotFoundError):
        code = status.HTTP_404_NOT_FOUND
    elif isinstance(exc, WorkPlanPermissionError | WorkPlanAccessError):
        code = status.HTTP_403_FORBIDDEN
    elif isinstance(exc, WorkPlanConflictError):
        code = status.HTTP_409_CONFLICT
    elif isinstance(exc, WorkPlanRuleError):
        code = status.HTTP_400_BAD_REQUEST
    else:
        raise exc
    raise HTTPException(status_code=code, detail=str(exc)) from exc
