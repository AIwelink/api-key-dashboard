from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.database import db_dependency
from app.modules.growth.analytics_schemas import (
    Milestone,
    SourceKind,
    TrafficAnalyticsFilters,
    TrafficRange,
    TrafficUsersQuery,
    UserSegment,
)
from app.modules.growth.analytics_service import (
    get_traffic_analytics_overview,
    get_traffic_analytics_users,
)
from app.modules.growth.repository import (
    GrowthConflictError,
    GrowthNotFoundError,
    create_campaign_config,
    create_channel_config,
    create_tracking_link_config,
    list_campaign_configs,
    list_channel_configs,
    list_growth_site_configs,
    list_tracking_link_configs,
    update_campaign_config,
    update_channel_config,
    update_growth_site_config,
    update_tracking_link_config,
)
from app.modules.growth.schemas import (
    CampaignCreate,
    CampaignUpdate,
    ChannelCreate,
    ChannelUpdate,
    GrowthSiteUpdate,
    TrackingLinkCreate,
    TrackingLinkUpdate,
)
from app.modules.system.audit import write_audit_log
from app.modules.system.permissions import require_view_permission


router = APIRouter(prefix="/growth", tags=["growth"])
GROWTH_PERMISSION = "traffic-analysis"


def _actor_id(actor: dict[str, Any]) -> str:
    return str(actor.get("_id") or actor.get("email") or actor.get("id") or "")


def _raise_http_error(exc: Exception) -> None:
    if isinstance(exc, GrowthConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, (GrowthNotFoundError, LookupError)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    if isinstance(exc, IntegrityError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Growth configuration conflicts with an existing record",
        ) from exc
    if isinstance(exc, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if isinstance(exc, SQLAlchemyError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Growth database is unavailable or not initialized",
        ) from exc
    raise exc


@router.get("/analytics/overview")
async def get_growth_analytics_overview_route(
    range_key: TrafficRange = Query(default="7d", alias="range"),
    segment: UserSegment = Query(default="ordinary"),
    site_id: str | None = Query(default=None),
    source_kind: SourceKind | None = Query(default=None),
    channel_id: UUID | None = Query(default=None),
    campaign_id: UUID | None = Query(default=None),
    tracking_link_id: UUID | None = Query(default=None),
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    del actor
    filters = TrafficAnalyticsFilters(
        range_key=range_key,
        segment=segment,
        site_id=site_id,
        source_kind=source_kind,
        channel_id=channel_id,
        campaign_id=campaign_id,
        tracking_link_id=tracking_link_id,
    )
    try:
        return await get_traffic_analytics_overview(db, filters)
    except Exception as exc:  # noqa: BLE001 - normalized into the public API contract.
        _raise_http_error(exc)


@router.get("/analytics/users")
async def get_growth_analytics_users_route(
    range_key: TrafficRange = Query(default="7d", alias="range"),
    segment: UserSegment = Query(default="ordinary"),
    milestone: Milestone = Query(default="registered"),
    site_id: str | None = Query(default=None),
    source_kind: SourceKind | None = Query(default=None),
    channel_id: UUID | None = Query(default=None),
    campaign_id: UUID | None = Query(default=None),
    tracking_link_id: UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    del actor
    query = TrafficUsersQuery(
        range_key=range_key,
        segment=segment,
        milestone=milestone,
        site_id=site_id,
        source_kind=source_kind,
        channel_id=channel_id,
        campaign_id=campaign_id,
        tracking_link_id=tracking_link_id,
        limit=limit,
        offset=offset,
    )
    try:
        return await get_traffic_analytics_users(db, query)
    except Exception as exc:  # noqa: BLE001 - normalized into the public API contract.
        _raise_http_error(exc)


@router.get("/sites")
async def get_growth_sites(
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    del actor
    try:
        return await list_growth_site_configs(db)
    except Exception as exc:  # noqa: BLE001 - normalized into the public API contract.
        _raise_http_error(exc)


@router.put("/sites/{site_id}")
async def put_growth_site(
    site_id: str,
    payload: GrowthSiteUpdate,
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        result = await update_growth_site_config(db, site_id, payload)
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="growth.site.update",
        resource_type="growth_site",
        resource_id=site_id,
        after=result,
    )
    return result


@router.get("/channels")
async def get_growth_channels(
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    del actor
    try:
        return await list_channel_configs(db)
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.post("/channels", status_code=status.HTTP_201_CREATED)
async def post_growth_channel(
    payload: ChannelCreate,
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        result = await create_channel_config(db, payload, actor_id=_actor_id(actor))
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="growth.channel.create",
        resource_type="growth_channel",
        resource_id=result["channel_id"],
        after=result,
    )
    return result


@router.patch("/channels/{channel_id}")
async def patch_growth_channel(
    channel_id: UUID,
    payload: ChannelUpdate,
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        result = await update_channel_config(db, channel_id, payload, actor_id=_actor_id(actor))
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="growth.channel.update",
        resource_type="growth_channel",
        resource_id=str(channel_id),
        after=result,
    )
    return result


@router.get("/campaigns")
async def get_growth_campaigns(
    site_id: str | None = Query(default=None),
    channel_id: UUID | None = Query(default=None),
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    del actor
    try:
        return await list_campaign_configs(db, site_id=site_id, channel_id=channel_id)
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def post_growth_campaign(
    payload: CampaignCreate,
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        result = await create_campaign_config(db, payload, actor_id=_actor_id(actor))
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="growth.campaign.create",
        resource_type="growth_campaign",
        resource_id=result["campaign_id"],
        after=result,
    )
    return result


@router.patch("/campaigns/{campaign_id}")
async def patch_growth_campaign(
    campaign_id: UUID,
    payload: CampaignUpdate,
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        result = await update_campaign_config(db, campaign_id, payload, actor_id=_actor_id(actor))
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="growth.campaign.update",
        resource_type="growth_campaign",
        resource_id=str(campaign_id),
        after=result,
    )
    return result


@router.get("/tracking-links")
async def get_growth_tracking_links(
    site_id: str | None = Query(default=None),
    campaign_id: UUID | None = Query(default=None),
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    del actor
    try:
        return await list_tracking_link_configs(db, site_id=site_id, campaign_id=campaign_id)
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)


@router.post("/tracking-links", status_code=status.HTTP_201_CREATED)
async def post_growth_tracking_link(
    payload: TrackingLinkCreate,
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        result = await create_tracking_link_config(db, payload, actor_id=_actor_id(actor))
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="growth.tracking_link.create",
        resource_type="growth_tracking_link",
        resource_id=result["tracking_link_id"],
        after=result,
    )
    return result


@router.patch("/tracking-links/{tracking_link_id}")
async def patch_growth_tracking_link(
    tracking_link_id: UUID,
    payload: TrackingLinkUpdate,
    actor: dict = Depends(require_view_permission(GROWTH_PERMISSION)),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        result = await update_tracking_link_config(
            db,
            tracking_link_id,
            payload,
            actor_id=_actor_id(actor),
        )
    except Exception as exc:  # noqa: BLE001
        _raise_http_error(exc)
    await write_audit_log(
        db,
        actor=actor,
        action="growth.tracking_link.update",
        resource_type="growth_tracking_link",
        resource_id=str(tracking_link_id),
        after=result,
    )
    return result
