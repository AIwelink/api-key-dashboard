from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import FreeToPlusCompleteRequest, FreeToPlusFailRequest
from app.security import get_current_user
from app.security import require_roles
from app.services.audit import write_audit_log
from app.services.todo_free_to_plus import (
    complete_free_to_plus,
    fail_free_to_plus,
    list_free_to_plus_accounts,
    release_free_to_plus,
    return_completed_free_to_plus,
    start_free_to_plus,
)
from app.utils import serialize_doc


router = APIRouter(prefix="/todo-items", tags=["todo-items"])


@router.get("")
async def list_todo_items(
    status_filter: str | None = Query(default=None, alias="status"),
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    query = {}
    if status_filter:
        query["status"] = status_filter
    items = [serialize_doc(item) async for item in db.todo_items.find(query).sort("updated_at", -1).limit(200)]
    return {"items": items, "total": len(items)}


@router.get("/free-to-plus/accounts")
async def get_free_to_plus_accounts(
    status_filter: str = Query(default="open", alias="status"),
    q: str | None = None,
    skip: int = 0,
    limit: int = Query(default=50, le=500),
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_free_to_plus_accounts(db, status_filter=status_filter, q=q, skip=skip, limit=limit)


@router.post("/free-to-plus/accounts/{account_id}/start")
async def post_start_free_to_plus(
    account_id: str,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await start_free_to_plus(db, account_id=account_id, actor=actor)
    await write_audit_log(db, actor=actor, action="todo.free_to_plus.start", resource_type="account", resource_id=account_id)
    return updated


@router.post("/free-to-plus/accounts/{account_id}/release")
async def post_release_free_to_plus(
    account_id: str,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await release_free_to_plus(db, account_id=account_id, actor=actor)
    await write_audit_log(db, actor=actor, action="todo.free_to_plus.release", resource_type="account", resource_id=account_id)
    return updated


@router.post("/free-to-plus/accounts/{account_id}/return-processing")
async def post_return_completed_free_to_plus(
    account_id: str,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await return_completed_free_to_plus(db, account_id=account_id, actor=actor)
    await write_audit_log(db, actor=actor, action="todo.free_to_plus.return_processing", resource_type="account", resource_id=account_id)
    return updated


@router.post("/free-to-plus/accounts/{account_id}/complete")
async def post_complete_free_to_plus(
    account_id: str,
    payload: FreeToPlusCompleteRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await complete_free_to_plus(db, account_id=account_id, payment_type=payload.payment_type, note=payload.note, actor=actor)
    await write_audit_log(
        db,
        actor=actor,
        action="todo.free_to_plus.complete",
        resource_type="account",
        resource_id=account_id,
        after={"payment_type": payload.payment_type, "note": payload.note},
    )
    return updated


@router.post("/free-to-plus/accounts/{account_id}/fail")
async def post_fail_free_to_plus(
    account_id: str,
    payload: FreeToPlusFailRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await fail_free_to_plus(db, account_id=account_id, error=payload.error, note=payload.note, actor=actor)
    await write_audit_log(
        db,
        actor=actor,
        action="todo.free_to_plus.fail",
        resource_type="account",
        resource_id=account_id,
        after={"error": payload.error, "note": payload.note},
    )
    return updated
