import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import AlertReadRequest, ApiPoolCreate, ApiPoolStatusPreferenceUpdate, ApiPoolUpdate, CapacityAccountLimitsUpdate, GroupObservabilitySettingUpdate
from app.modules.system.permissions import require_any_view_permission, require_view_permission
from app.modules.api_pools.pools import create_api_pool, list_api_pools, update_api_pool
from app.modules.api_pools.status_preferences import get_api_pool_status_preferences, update_api_pool_status_preferences
from app.modules.system.audit import write_audit_log
from app.modules.api_pools.capacity_limits import capacity_limits_setting_id, get_capacity_account_limits, update_capacity_account_limits
from app.modules.accounts.pool_lifecycle import capacity_check
from app.modules.sub2api.account_probe import list_duplicate_email_alerts, list_group_observability_settings, mark_duplicate_email_alert_read, probe_site_accounts, update_group_observability_setting
from app.modules.sub2api.account_health_analysis import get_account_health_analysis
from app.modules.sub2api.auto_refill import list_auto_refill_logs
from app.modules.sub2api.cache import get_site
from app.modules.sub2api.quota_detection import get_quota_detection_summary


router = APIRouter(prefix="/api-pools", tags=["api-pools"])
logger = logging.getLogger("app.api_pools")


@router.get("")
async def get_api_pools(
    _: dict = Depends(require_any_view_permission("api-pools", "todos")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_api_pools(db)


@router.post("")
async def post_api_pool(
    payload: ApiPoolCreate,
    actor: dict = Depends(require_view_permission("api-pools")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    created = await create_api_pool(db, payload.model_dump(), actor)
    await write_audit_log(db, actor=actor, action="api_pool.create", resource_type="api_pool", resource_id=created["id"])
    return created


@router.get("/auto-refill-logs")
async def get_auto_refill_logs(
    site_id: str | None = None,
    group_id: int | None = None,
    limit: int = Query(default=20, le=100),
    _: dict = Depends(require_view_permission("reserve-pool")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_auto_refill_logs(db, site_id=site_id, group_id=group_id, limit=limit)


@router.get("/capacity-limits")
async def get_capacity_limits(
    site_id: str | None = None,
    _: dict = Depends(require_view_permission("pool-lifecycle")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_capacity_account_limits(db, site_id)


@router.patch("/capacity-limits")
async def patch_capacity_limits(
    payload: CapacityAccountLimitsUpdate,
    site_id: str | None = None,
    actor: dict = Depends(require_view_permission("pool-lifecycle")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_capacity_account_limits(db, {key: value.model_dump() for key, value in payload.limits.items()}, actor, site_id)
    await write_audit_log(
        db,
        actor=actor,
        action="api_pool.capacity_limits.update",
        resource_type="setting",
        resource_id=capacity_limits_setting_id(site_id),
        after=updated,
    )
    return updated


@router.get("/quota-detection")
async def get_quota_detection(
    site_id: str,
    _: dict = Depends(require_view_permission("pool-lifecycle")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    if not await get_site(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    quota_result, health_result = await asyncio.gather(
        get_quota_detection_summary(db, site_id),
        get_account_health_analysis(db, site_id),
        return_exceptions=True,
    )
    if isinstance(quota_result, BaseException):
        raise quota_result
    quota_summary = quota_result
    if isinstance(health_result, BaseException):
        logger.warning(
            "sub2api_account_health_analysis_failed site_id=%s error_type=%s",
            site_id,
            type(health_result).__name__,
        )
        health_analysis = {"site_id": site_id, "periods": {}, "stale": True}
    else:
        health_analysis = health_result
    return {**quota_summary, "account_health_analysis": health_analysis}


@router.get("/status-preferences")
async def get_status_preferences(
    _: dict = Depends(require_view_permission("api-pools")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_api_pool_status_preferences(db)


@router.patch("/status-preferences")
async def patch_status_preferences(
    payload: ApiPoolStatusPreferenceUpdate,
    actor: dict = Depends(require_view_permission("api-pools")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_api_pool_status_preferences(db, payload.model_dump(exclude_unset=True), actor)
    await write_audit_log(
        db,
        actor=actor,
        action="api_pool.status_preferences.update",
        resource_type="setting",
        resource_id="api_pool_status_preferences",
        after=updated,
    )
    return updated


@router.get("/observability/groups")
async def get_group_observability_settings(
    site_id: str,
    _: dict = Depends(require_view_permission("pool-lifecycle")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_group_observability_settings(db, site_id)


@router.patch("/observability/groups/{group_id}")
async def patch_group_observability_setting(
    group_id: int,
    payload: GroupObservabilitySettingUpdate,
    site_id: str,
    actor: dict = Depends(require_view_permission("pool-lifecycle")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_group_observability_setting(db, site_id=site_id, group_id=group_id, payload=payload.model_dump(exclude_unset=True), actor=actor)
    await write_audit_log(
        db,
        actor=actor,
        action="api_pool.group_observability.update",
        resource_type="group_observability_setting",
        resource_id=f"{site_id}:{group_id}",
        after=updated,
    )
    return updated


@router.post("/observability/probe")
async def post_observability_probe(
    site_id: str,
    actor: dict = Depends(require_view_permission("pool-lifecycle")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await probe_site_accounts(db, site_id=site_id)
    await write_audit_log(
        db,
        actor=actor,
        action="api_pool.observability.probe",
        resource_type="sub2api_site",
        resource_id=site_id,
        after=result,
    )
    return result


@router.get("/observability/alerts")
async def get_observability_alerts(
    site_id: str | None = None,
    group_id: int | None = None,
    include_read: bool = False,
    limit: int = Query(default=100, ge=1, le=500),
    _: dict = Depends(require_view_permission("alert-center")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_duplicate_email_alerts(db, site_id=site_id, group_id=group_id, include_read=include_read, limit=limit)


@router.post("/observability/alerts/{alert_id}/read")
async def post_observability_alert_read(
    alert_id: str,
    payload: AlertReadRequest,
    actor: dict = Depends(require_view_permission("alert-center")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await mark_duplicate_email_alert_read(db, alert_id=alert_id, actor=actor, note=payload.note)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alert not found")
    await write_audit_log(
        db,
        actor=actor,
        action="api_pool.observability_alert.read",
        resource_type="remote_account_identity",
        resource_id=alert_id,
        after={"alert": updated, "note": payload.note},
    )
    return {"ok": True, "item": updated}


@router.patch("/{pool_id}")
async def patch_api_pool(
    pool_id: str,
    payload: ApiPoolUpdate,
    actor: dict = Depends(require_view_permission("api-pools")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_api_pool(db, pool_id, payload.model_dump(exclude_unset=True), actor)
    await write_audit_log(db, actor=actor, action="api_pool.update", resource_type="api_pool", resource_id=pool_id)
    return updated


@router.post("/{pool_id}/capacity-check")
async def post_capacity_check(
    pool_id: str,
    actor: dict = Depends(require_view_permission("pool-lifecycle")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await capacity_check(db, pool_id=pool_id, actor=actor)
