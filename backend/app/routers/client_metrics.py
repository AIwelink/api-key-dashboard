from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.modules.client_metrics.queries import get_client_metric_status, list_client_minute_metrics
from app.modules.client_metrics.sampler import sample_client_site
from app.modules.system.audit import write_audit_log
from app.security import get_current_user
from app.modules.system.permissions import require_view_permission


router = APIRouter(prefix="/client-sites", tags=["client-metrics"], dependencies=[Depends(require_view_permission("client-sites"))])


@router.get("/{site_id}/metrics/minutes")
async def get_site_minute_metrics(
    site_id: str,
    start_at: datetime,
    end_at: datetime,
    limit: int = Query(default=1_440, ge=1, le=10_080),
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    await _require_client_site(db, site_id)
    try:
        return await list_client_minute_metrics(
            db,
            site_id=site_id,
            start_at=start_at,
            end_at=end_at,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{site_id}/metrics/status")
async def get_site_metric_status(
    site_id: str,
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    await _require_client_site(db, site_id)
    return await get_client_metric_status(db, site_id=site_id)


@router.post("/{site_id}/metrics/sample")
async def post_site_metric_sample(
    site_id: str,
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    site = await _require_client_site(db, site_id)
    if site.get("status") != "active":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="client site is not active")
    result = await sample_client_site(db, site_id=site_id)
    await write_audit_log(
        db,
        actor=actor,
        action="client_site.metric_sample",
        resource_type="client_site",
        resource_id=site_id,
        after=result,
    )
    return result


async def _require_client_site(db: AsyncIOMotorDatabase, site_id: str) -> dict[str, Any]:
    site = await db.client_sites.find_one(
        {"_id": site_id, "status": {"$ne": "deleted"}},
        {"_id": 1, "status": 1, "client_type": 1},
    )
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="client site not found")
    return site
