from fastapi import APIRouter, Depends, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import AccountCreate, AccountUpdate, EnterReserveRequest, ManualTransferRequest, PushToSub2ApiRequest, ReservePinRequest, VerifyViaSub2ApiRequest
from app.security import get_current_user, require_roles
from app.services.accounts import (
    create_account,
    get_account_or_404,
    list_accounts,
    soft_delete_account,
    update_account,
)
from app.services.audit import write_audit_log
from app.services.json_parser import extract_account_objects
from app.services.pool_lifecycle import enter_reserve, manual_transfer_account, set_reserve_pin
from app.services.sub2api_binding import manually_unbind_sub2api_account
from app.services.sub2api_push import push_account_to_sub2api
from app.services.sub2api_verify import verify_account_via_sub2api_group
from app.utils import serialize_doc


router = APIRouter(prefix="/accounts", tags=["accounts"])


@router.get("")
async def get_accounts(
    q: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    payment_type: str | None = None,
    account_type: str | None = None,
    phone_bound: bool | None = None,
    uploader_name: str | None = None,
    manual_status_label: str | None = None,
    account_scope: str | None = None,
    pool_status: str | None = None,
    pool_id: str | None = None,
    site_id: str | None = None,
    sort_by: str = Query(default="updated_at"),
    sort_dir: str = Query(default="desc", pattern="^(asc|desc)$"),
    skip: int = 0,
    limit: int = Query(default=50, le=500),
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_accounts(
        db,
        q=q,
        status_filter=status_filter,
        payment_type=payment_type,
        account_type=account_type,
        phone_bound=phone_bound,
        uploader_name=uploader_name,
        manual_status_label=manual_status_label,
        account_scope=account_scope,
        pool_status=pool_status,
        pool_id=pool_id,
        site_id=site_id,
        sort_by=sort_by,
        sort_dir=sort_dir,
        skip=skip,
        limit=limit,
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_account(
    payload: AccountCreate,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    account_json_list = extract_account_objects(payload.account_json)
    created = []
    for account_json in account_json_list:
        account = await create_account(
            db,
            account_json=account_json,
            metadata=payload.metadata,
            actor=actor,
        )
        await write_audit_log(db, actor=actor, action="account.create", resource_type="account", resource_id=account["id"])
        created.append(account)
    if len(created) == 1:
        return created[0]
    return {"items": created, "count": len(created)}


@router.get("/{account_id}")
async def get_account(
    account_id: str,
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return serialize_doc(await get_account_or_404(db, account_id))


@router.patch("/{account_id}")
async def patch_account(
    account_id: str,
    payload: AccountUpdate,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_account(
        db,
        account_id=account_id,
        account_json=payload.account_json,
        metadata=payload.metadata,
        actor=actor,
    )
    await write_audit_log(db, actor=actor, action="account.update", resource_type="account", resource_id=account_id)
    return updated


@router.post("/{account_id}/enter-reserve")
async def post_enter_reserve(
    account_id: str,
    payload: EnterReserveRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await enter_reserve(
        db,
        account_id=account_id,
        pool_id=payload.pool_id,
        priority=payload.priority,
        reason=payload.reason,
        actor=actor,
    )
    await write_audit_log(db, actor=actor, action="account.enter_reserve", resource_type="account", resource_id=account_id)
    return updated


@router.post("/{account_id}/manual-transfer")
async def post_manual_transfer(
    account_id: str,
    payload: ManualTransferRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await manual_transfer_account(
        db,
        account_id=account_id,
        target_status=payload.target_status,
        pool_id=payload.pool_id,
        site_id=payload.site_id,
        priority=payload.priority,
        reason=payload.reason,
        last_error=payload.last_error,
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="account.manual_transfer",
        resource_type="account",
        resource_id=account_id,
        after={
            "target_status": payload.target_status,
            "pool_id": payload.pool_id,
            "site_id": payload.site_id,
            "priority": payload.priority,
            "reason": payload.reason,
            "last_error": payload.last_error,
        },
    )
    return updated


@router.post("/{account_id}/reserve-pin")
async def post_reserve_pin(
    account_id: str,
    payload: ReservePinRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await set_reserve_pin(db, account_id=account_id, pinned=payload.pinned, actor=actor)
    await write_audit_log(
        db,
        actor=actor,
        action="account.reserve_pin" if payload.pinned else "account.reserve_unpin",
        resource_type="account",
        resource_id=account_id,
        after={"pinned": payload.pinned},
    )
    return updated


@router.post("/{account_id}/push-to-sub2api")
async def post_push_to_sub2api(
    account_id: str,
    payload: PushToSub2ApiRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await push_account_to_sub2api(
        db,
        account_id=account_id,
        site_id=payload.site_id,
        group_id=payload.group_id,
        run_verification=payload.run_verification,
        model_id=payload.model_id,
        prompt=payload.prompt,
        concurrency=payload.concurrency,
        load_factor=payload.load_factor,
        priority=payload.priority,
        reason=payload.reason,
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="account.push_to_sub2api",
        resource_type="account",
        resource_id=account_id,
        after={
            "site_id": payload.site_id,
            "group_id": payload.group_id,
            "run_verification": payload.run_verification,
            "model_id": payload.model_id,
            "concurrency": payload.concurrency,
            "load_factor": payload.load_factor,
            "priority": payload.priority,
            "verification": result.get("verification", {}),
        },
    )
    return result


@router.post("/{account_id}/unbind-sub2api")
async def post_unbind_sub2api(
    account_id: str,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await manually_unbind_sub2api_account(db, account_id=account_id, actor=actor)
    await write_audit_log(
        db,
        actor=actor,
        action="account.unbind_sub2api",
        resource_type="account",
        resource_id=account_id,
        after={"sub2api_account_id": None},
    )
    return updated


@router.post("/{account_id}/verify-via-sub2api")
async def post_verify_via_sub2api(
    account_id: str,
    payload: VerifyViaSub2ApiRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await verify_account_via_sub2api_group(
        db,
        account_id=account_id,
        site_id=payload.site_id,
        verification_group_id=payload.verification_group_id,
        model_id=payload.model_id,
        prompt=payload.prompt,
        cleanup_remote=payload.cleanup_remote,
        concurrency=payload.concurrency,
        load_factor=payload.load_factor,
        priority=payload.priority,
        reason=payload.reason,
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="account.verify_via_sub2api",
        resource_type="account",
        resource_id=account_id,
        after={
            "site_id": payload.site_id,
            "verification_group_id": payload.verification_group_id,
            "model_id": payload.model_id,
            "cleanup_remote": payload.cleanup_remote,
            "verification": result.get("verification", {}),
            "cleanup": result.get("cleanup", {}),
        },
    )
    return result


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: str,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> None:
    await soft_delete_account(db, account_id=account_id, actor=actor)
    await write_audit_log(db, actor=actor, action="account.delete", resource_type="account", resource_id=account_id)
