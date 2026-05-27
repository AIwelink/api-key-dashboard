from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import ApiPoolCreate, ApiPoolUpdate
from app.security import require_roles
from app.services.api_pools import create_api_pool, list_api_pools, update_api_pool
from app.services.audit import write_audit_log
from app.services.pool_lifecycle import capacity_check


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


@router.post("/{pool_id}/capacity-check")
async def post_capacity_check(
    pool_id: str,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await capacity_check(db, pool_id=pool_id, actor=actor)
