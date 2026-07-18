from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.modules.system.audit import write_audit_log
from app.modules.system.client_site_database import run_client_site_database_test
from app.modules.system.client_sites import (
    create_client_site,
    delete_client_site,
    list_client_sites,
    update_client_site,
)
from app.security import require_roles


router = APIRouter(prefix="/client-sites", tags=["client-sites"])


@router.get("")
async def get_client_sites(
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    return await list_client_sites(db)


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_client_site(
    payload: dict[str, Any],
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        created = await create_client_site(db, payload=payload, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="client_site.create",
        resource_type="client_site",
        resource_id=created["id"],
        after=created,
    )
    return created


@router.patch("/{site_id}")
async def patch_client_site(
    site_id: str,
    payload: dict[str, Any],
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        updated = await update_client_site(db, site_id=site_id, payload=payload, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="client site not found")
    await write_audit_log(
        db,
        actor=actor,
        action="client_site.update",
        resource_type="client_site",
        resource_id=site_id,
        after=updated,
    )
    return updated


@router.post("/{site_id}/database/test")
async def test_site_database(
    site_id: str,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    try:
        result = await run_client_site_database_test(db, site_id)
    except LookupError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="client_site.database_test",
        resource_type="client_site",
        resource_id=site_id,
        after=result,
    )
    return result


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_client_site(
    site_id: str,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> None:
    if not await delete_client_site(db, site_id=site_id, actor=actor):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="client site not found")
    await write_audit_log(
        db,
        actor=actor,
        action="client_site.delete",
        resource_type="client_site",
        resource_id=site_id,
    )
