from typing import Any
from urllib.parse import parse_qs, urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import Sub2ApiAccountTestRequest, Sub2ApiManualDeleteRequest, Sub2ApiOAuthApplyRequest, Sub2ApiOAuthExchangeRequest, Sub2ApiResurrectionFailRequest
from app.security import require_roles
from app.services.audit import write_audit_log
from app.services.sub2api import Sub2ApiClient
from app.services.sub2api_cache import (
    create_site_config,
    delete_site_config,
    get_site,
    list_cached_group_accounts,
    list_cached_groups,
    list_sites as list_cached_sites,
    request_debounced_refresh,
    update_site_config,
    upsert_cached_account_snapshot,
)
from app.services.sub2api_dashboard import get_stored_dashboard_snapshots, refresh_dashboard_snapshots
from app.services.sub2api_push import _move_remote_to_problem_group, _resolve_push_problem_group
from app.services.sub2api_return import manual_delete_sub2api_account
from app.services.sub2api_verify import test_remote_sub2api_account


router = APIRouter(prefix="/sub2api-sites", tags=["sub2api-sites"])


async def _client_for_site(db: AsyncIOMotorDatabase, site_id: str) -> Sub2ApiClient:
    site = await get_site(db, site_id, include_token=True)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    return Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))


@router.get("")
async def list_sites(
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_cached_sites(db)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_site(
    payload: dict[str, Any],
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    created = await create_site_config(db, payload)
    if not created:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="site id and base_url are required")
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.site.create",
        resource_type="sub2api_site",
        resource_id=created["id"],
        after={key: value for key, value in created.items() if key != "token"},
    )
    return created


@router.patch("/{site_id}")
async def update_site(
    site_id: str,
    payload: dict[str, Any],
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_site_config(db, site_id, payload)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.site.update",
        resource_type="sub2api_site",
        resource_id=site_id,
        after={key: value for key, value in updated.items() if key != "token"},
    )
    if payload.get("auto_remove_abnormal_accounts") is True:
        try:
            updated["auto_remove_refresh"] = await request_debounced_refresh(db, site_id)
        except Exception as exc:  # noqa: BLE001 - keep the saved switch visible, but report the scan failure.
            updated["auto_remove_refresh"] = {
                "ok": False,
                "status": "failed",
                "message": str(exc),
            }
    return updated


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(
    site_id: str,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> None:
    if not await delete_site_config(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.site.delete",
        resource_type="sub2api_site",
        resource_id=site_id,
    )


@router.post("/{site_id}/test")
async def test_site(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await (await _client_for_site(db, site_id)).test_connection()


@router.post("/{site_id}/refresh")
async def refresh_site(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    if not await get_site(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    return await request_debounced_refresh(db, site_id)


@router.post("/{site_id}/dashboard/refresh")
async def refresh_site_dashboard(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    if not await get_site(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    client = await _client_for_site(db, site_id)
    return await refresh_dashboard_snapshots(db, site_id=site_id, client=client, force=True)


@router.get("/{site_id}/dashboard")
async def get_site_dashboard(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    if not await get_site(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    return await get_stored_dashboard_snapshots(db, site_id=site_id)


@router.get("/{site_id}/groups")
async def list_site_groups(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> dict:
    site = await get_site(db, site_id)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    data = await list_cached_groups(db, site_id, page=page, page_size=page_size)
    return {"site": site, **data}


@router.get("/{site_id}/groups/{group_id}/accounts")
async def list_site_group_accounts(
    site_id: str,
    group_id: int,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> dict:
    if not await get_site(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    return await list_cached_group_accounts(db, site_id, group_id, status_filter=status_filter, page=page, page_size=page_size)


@router.post("/{site_id}/accounts/{account_id}/manual-delete")
async def post_manual_delete_remote_account(
    site_id: str,
    account_id: int,
    payload: Sub2ApiManualDeleteRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await manual_delete_sub2api_account(
        db,
        site_id=site_id,
        remote_account_id=account_id,
        target_status=payload.target_status,
        reason=payload.reason,
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.account.manual_delete",
        resource_type="sub2api_account",
        resource_id=str(account_id),
        after={
            "site_id": site_id,
            "target_status": payload.target_status,
            "reason": payload.reason,
            "local_account_id": result.get("account", {}).get("id"),
            "delete_result": result.get("delete_result", {}),
        },
    )
    return result


@router.post("/{site_id}/openai/generate-auth-url")
async def post_generate_openai_auth_url(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    client = await _client_for_site(db, site_id)
    return await client.request_admin("POST", "/openai/generate-auth-url", json={})


@router.post("/{site_id}/openai/exchange-code")
async def post_exchange_openai_code(
    site_id: str,
    payload: Sub2ApiOAuthExchangeRequest,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    code = payload.code
    state = payload.state
    if payload.callback_url:
        parsed = parse_qs(urlparse(payload.callback_url).query)
        code = code or (parsed.get("code") or [None])[0]
        state = state or (parsed.get("state") or [None])[0]
    if not payload.session_id or not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id, code and state are required")
    client = await _client_for_site(db, site_id)
    return await client.request_admin(
        "POST",
        "/openai/exchange-code",
        json={"session_id": payload.session_id, "code": code, "state": state},
    )


@router.post("/{site_id}/accounts/{account_id}/apply-oauth-credentials")
async def post_apply_oauth_credentials(
    site_id: str,
    account_id: int,
    payload: Sub2ApiOAuthApplyRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    client = await _client_for_site(db, site_id)
    apply_payload = {"type": payload.account_type, "credentials": payload.credentials}
    result = await client.request_admin("POST", f"/accounts/{account_id}/apply-oauth-credentials", json=apply_payload)
    refreshed = await client.update_account(account_id, {"status": "active", "schedulable": True})
    if isinstance(refreshed, dict) and refreshed.get("id") is not None:
        await upsert_cached_account_snapshot(db, site_id, refreshed)
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.account.resurrection_apply_oauth",
        resource_type="sub2api_account",
        resource_id=str(account_id),
        after={"site_id": site_id, "status": "active", "schedulable": True},
    )
    return {"apply": result, "account": refreshed}


@router.post("/{site_id}/accounts/{account_id}/resurrection-fail")
async def post_resurrection_fail(
    site_id: str,
    account_id: int,
    payload: Sub2ApiResurrectionFailRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    if not payload.reason.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reason is required")
    client = await _client_for_site(db, site_id)
    remote_account = await client.get_account(account_id)
    problem_group_id, problem_group_name = await _resolve_push_problem_group(db, site_id)
    moved = await _move_remote_to_problem_group(
        db,
        client=client,
        site_id=site_id,
        remote_account={**remote_account, "error_message": payload.reason},
        problem_group_id=problem_group_id,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.account.resurrection_failed",
        resource_type="sub2api_account",
        resource_id=str(account_id),
        after={"site_id": site_id, "reason": payload.reason, "problem_group_id": problem_group_id},
    )
    return {"account": moved, "problem_group_id": problem_group_id, "problem_group_name": problem_group_name}


@router.post("/{site_id}/accounts/{account_id}/test")
async def post_test_remote_account(
    site_id: str,
    account_id: int,
    payload: Sub2ApiAccountTestRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await test_remote_sub2api_account(
        db,
        site_id=site_id,
        remote_account_id=account_id,
        model_id=payload.model_id,
        prompt=payload.prompt,
        reason=payload.reason,
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.account.test",
        resource_type="sub2api_account",
        resource_id=str(account_id),
        after={
            "site_id": site_id,
            "model_id": payload.model_id,
            "verification": result.get("verification", {}),
        },
    )
    return result
