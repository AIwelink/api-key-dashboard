from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import ApiPoolCreate, ApiPoolUpdate, CapacityAccountLimitsUpdate
from app.security import require_roles
from app.services.api_pools import create_api_pool, list_api_pools, update_api_pool
from app.services.audit import write_audit_log
from app.services.capacity_limits import get_capacity_account_limits, update_capacity_account_limits
from app.services.pool_lifecycle import capacity_check
from app.services.sub2api_auto_refill import list_auto_refill_logs


router = APIRouter(prefix="/api-pools", tags=["api-pools"])


@router.get("")
async def get_api_pools(
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_api_pools(db)


@router.post("")
async def post_api_pool(
    payload: ApiPoolCreate,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    created = await create_api_pool(db, payload.model_dump(), actor)
    await write_audit_log(db, actor=actor, action="api_pool.create", resource_type="api_pool", resource_id=created["id"])
    return created


@router.patch("/{pool_id}")
async def patch_api_pool(
    pool_id: str,
    payload: ApiPoolUpdate,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_api_pool(db, pool_id, payload.model_dump(exclude_unset=True), actor)
    await write_audit_log(db, actor=actor, action="api_pool.update", resource_type="api_pool", resource_id=pool_id)
    return updated


@router.get("/auto-refill-logs")
async def get_auto_refill_logs(
    site_id: str | None = None,
    group_id: int | None = None,
    limit: int = Query(default=20, le=100),
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_auto_refill_logs(db, site_id=site_id, group_id=group_id, limit=limit)


@router.get("/capacity-limits")
async def get_capacity_limits(
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_capacity_account_limits(db)


@router.patch("/capacity-limits")
async def patch_capacity_limits(
    payload: CapacityAccountLimitsUpdate,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_capacity_account_limits(db, {key: value.model_dump() for key, value in payload.limits.items()}, actor)
    await write_audit_log(db, actor=actor, action="api_pool.capacity_limits.update", resource_type="setting", resource_id="capacity_account_limits")
    return updated


@router.post("/{pool_id}/capacity-check")
async def post_capacity_check(
    pool_id: str,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await capacity_check(db, pool_id=pool_id, actor=actor)
