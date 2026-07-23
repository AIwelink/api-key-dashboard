from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.security import get_current_user
from app.modules.system.permissions import require_view_permission
from app.modules.events.records import event_records_summary, get_event_account_detail, list_event_accounts, list_event_records


router = APIRouter(prefix="/event-records", tags=["event-records"], dependencies=[Depends(require_view_permission("event-records"))])


@router.get("/events")
async def get_events(
    site_id: str | None = None,
    group_id: int | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    account_type: str | None = None,
    q: str | None = None,
    range: str = Query(default="24h"),  # noqa: A002 - API query name is intentionally concise.
    only_401: bool = False,
    only_abnormal: bool = False,
    only_pro: bool = False,
    only_cumulative: bool = False,
    only_delete_archive: bool = False,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_event_records(
        db,
        site_id=site_id,
        group_id=group_id,
        event_type=event_type,
        severity=severity,
        account_type=account_type,
        q=q,
        range_value=range,
        only_401=only_401,
        only_abnormal=only_abnormal,
        only_pro=only_pro,
        only_cumulative=only_cumulative,
        only_delete_archive=only_delete_archive,
        skip=skip,
        limit=limit,
    )


@router.get("/accounts")
async def get_accounts(
    site_id: str | None = None,
    group_id: int | None = None,
    account_type: str | None = None,
    q: str | None = None,
    presence: str | None = None,
    only_401: bool = False,
    only_abnormal: bool = False,
    only_pro: bool = False,
    only_cumulative: bool = False,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_event_accounts(
        db,
        site_id=site_id,
        group_id=group_id,
        account_type=account_type,
        q=q,
        presence=presence,
        only_401=only_401,
        only_abnormal=only_abnormal,
        only_pro=only_pro,
        only_cumulative=only_cumulative,
        skip=skip,
        limit=limit,
    )


@router.get("/accounts/{identity_id}")
async def get_account_detail(
    identity_id: str,
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_event_account_detail(db, identity_id)


@router.get("/summary")
async def get_summary(
    site_id: str | None = None,
    group_id: int | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    account_type: str | None = None,
    q: str | None = None,
    range: str = Query(default="24h"),  # noqa: A002
    only_401: bool = False,
    only_abnormal: bool = False,
    only_pro: bool = False,
    only_cumulative: bool = False,
    only_delete_archive: bool = False,
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await event_records_summary(
        db,
        site_id=site_id,
        group_id=group_id,
        event_type=event_type,
        severity=severity,
        account_type=account_type,
        q=q,
        range_value=range,
        only_401=only_401,
        only_abnormal=only_abnormal,
        only_pro=only_pro,
        only_cumulative=only_cumulative,
        only_delete_archive=only_delete_archive,
    )
