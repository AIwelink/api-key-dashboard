import json
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, status
from fastapi.responses import HTMLResponse
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.config import get_settings
from app.schemas import (
    ChangePasswordRequest,
    FeishuAuthorizationSessionResponse,
    FeishuAuthorizationSessionStatusResponse,
    FeishuTicketExchangeRequest,
    LoginBindingRequiredResponse,
    LoginRequest,
    LoginResponse,
)
from app.modules.auth.feishu import (
    FeishuAuthError,
    FeishuConfigurationError,
    complete_authorization_session,
    consume_login_ticket,
    create_authorization_session,
    fail_authorization_session,
    get_authorization_session_status,
    has_feishu_binding,
)
from app.modules.system.permissions import permissions_for_user
from app.modules.system.user_projection import public_user
from app.modules.operations.site_permissions import normalize_operations_site_ids
from app.security import create_access_token, get_authenticated_user, get_current_user, hash_password, verify_password
from app.modules.system.audit import write_audit_log
from app.utils import now_utc


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse | LoginBindingRequiredResponse)
async def login(
    payload: LoginRequest,
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> LoginResponse | LoginBindingRequiredResponse:
    user = await db.users.find_one({"email": payload.email.lower()})
    if user is None or user.get("status") == "disabled":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if user.get("email_is_placeholder"):
        await write_audit_log(
            db,
            actor=None,
            action="auth.login_failed",
            resource_type="user",
            resource_id=user.get("_id"),
            after={"result_code": "password_login_unavailable"},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not verify_password(payload.password, user.get("password_hash", "")):
        await write_audit_log(
            db,
            actor=None,
            action="auth.login_failed",
            resource_type="user",
            resource_id=user.get("_id"),
            after={"email": payload.email.lower()},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    settings = get_settings()
    if settings.feishu_auth_enabled and not has_feishu_binding(user):
        try:
            auth_session = await create_authorization_session(
                db,
                purpose="bind",
                target_user_id=user["_id"],
                settings=settings,
            )
        except FeishuAuthError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        await write_audit_log(
            db,
            actor=user,
            action="auth.password.binding_required",
            resource_type="user",
            resource_id=user["_id"],
        )
        return LoginBindingRequiredResponse(
            authorization_url=auth_session.authorization_url,
            session_id=auth_session.session_id,
            ticket=auth_session.ticket,
            expires_at=auth_session.expires_at,
        )
    return await _finish_login(db, user=user, audit_action="auth.login_succeeded")


@router.post("/feishu/sessions", response_model=FeishuAuthorizationSessionResponse)
async def start_feishu_session(
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> FeishuAuthorizationSessionResponse:
    settings = get_settings()
    try:
        auth_session = await create_authorization_session(
            db,
            purpose="login",
            settings=settings,
        )
    except FeishuConfigurationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    return FeishuAuthorizationSessionResponse(
        session_id=auth_session.session_id,
        authorization_url=auth_session.authorization_url,
        ticket=auth_session.ticket,
        expires_at=auth_session.expires_at,
    )


@router.post("/feishu/bind-session", response_model=FeishuAuthorizationSessionResponse)
async def start_feishu_binding_session(
    user: dict = Depends(get_authenticated_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> FeishuAuthorizationSessionResponse:
    if user.get("actor_type") == "api_token":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="API Token 不能绑定飞书")
    if user.get("authorization_status", "active") != "active":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="尚未分配系统权限，请联系管理员")
    if has_feishu_binding(user):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="当前账号已绑定飞书")

    settings = get_settings()
    try:
        auth_session = await create_authorization_session(
            db,
            purpose="bind",
            target_user_id=user["_id"],
            settings=settings,
        )
    except FeishuAuthError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=user,
        action="auth.feishu.binding_started",
        resource_type="user",
        resource_id=user["_id"],
        after={"session_id": auth_session.session_id},
    )
    return FeishuAuthorizationSessionResponse(
        session_id=auth_session.session_id,
        authorization_url=auth_session.authorization_url,
        ticket=auth_session.ticket,
        expires_at=auth_session.expires_at,
    )


@router.get("/feishu/sessions/{session_id}", response_model=FeishuAuthorizationSessionStatusResponse)
async def feishu_session_status(
    session_id: str,
    ticket: Annotated[str, Header(alias="X-Feishu-Session-Ticket")],
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> FeishuAuthorizationSessionStatusResponse:
    try:
        payload = await get_authorization_session_status(
            db,
            session_id=session_id,
            ticket=ticket,
        )
    except FeishuAuthError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return FeishuAuthorizationSessionStatusResponse(**payload)


@router.get("/feishu/callback", response_class=HTMLResponse)
async def feishu_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> HTMLResponse:
    settings = get_settings()
    session_id: str | None = None
    message = "授权成功，正在返回系统"
    callback_status = "completed"
    try:
        if not state:
            raise FeishuAuthError("飞书授权状态缺失", code="state_missing")
        if error:
            session_id = await fail_authorization_session(
                db,
                state=state,
                error_code=error,
            )
            callback_status = "failed"
            message = "授权已取消，请返回系统重新扫码"
        elif not code:
            session_id = await fail_authorization_session(
                db,
                state=state,
                error_code="code_missing",
            )
            callback_status = "failed"
            message = "飞书未返回授权码，请重新扫码"
        else:
            session_id = await complete_authorization_session(
                db,
                state=state,
                code=code,
                settings=settings,
            )
    except FeishuAuthError as exc:
        callback_status = "failed"
        message = str(exc)
    return HTMLResponse(
        _callback_page(
            session_id=session_id,
            callback_status=callback_status,
            message=message,
            frontend_origin=settings.frontend_origin,
        )
    )


@router.post("/feishu/exchange", response_model=LoginResponse)
async def exchange_feishu_ticket(
    payload: FeishuTicketExchangeRequest,
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> LoginResponse:
    try:
        user = await consume_login_ticket(db, ticket=payload.ticket)
    except FeishuAuthError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
    return await _finish_login(db, user=user, audit_action="auth.feishu.login_succeeded")


@router.post("/logout")
async def logout() -> dict[str, bool]:
    return {"ok": True}


@router.get("/me")
async def me(
    user: dict = Depends(get_authenticated_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await user_with_permissions(db, user)


async def _finish_login(
    db: AsyncIOMotorDatabase,
    *,
    user: dict,
    audit_action: str,
) -> LoginResponse:
    timestamp = now_utc()
    update_result = await db.users.update_one(
        {"_id": user["_id"], "status": {"$ne": "disabled"}},
        {"$set": {"last_login_at": timestamp, "updated_at": timestamp}},
    )
    if getattr(update_result, "matched_count", 1) == 0:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="当前账号已停用")
    current = await db.users.find_one({"_id": user["_id"]})
    if current is None or current.get("status") == "disabled":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="当前账号已停用")
    token = create_access_token(subject=current["_id"], role=current.get("role") or "viewer")
    safe_user = await user_with_permissions(db, current)
    await write_audit_log(
        db,
        actor=current,
        action=audit_action,
        resource_type="user",
        resource_id=current["_id"],
    )
    return LoginResponse(access_token=token, user=safe_user)


def _callback_page(
    *,
    session_id: str | None,
    callback_status: str,
    message: str,
    frontend_origin: str,
) -> str:
    origin = frontend_origin.rstrip("/")
    event_payload = json.dumps(
        {
            "type": "feishu-auth-complete",
            "sessionId": session_id,
            "status": callback_status,
        },
        ensure_ascii=True,
    )
    target_origin = json.dumps(origin, ensure_ascii=True)
    fallback_url = f"{origin}/?feishu_session={quote(session_id or '')}"
    safe_message = json.dumps(message, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>飞书授权</title></head>
<body><main><p id="message"></p></main><script>
document.getElementById("message").textContent = {safe_message};
const payload = {event_payload};
const targetOrigin = {target_origin};
if (window.opener) {{ window.opener.postMessage(payload, targetOrigin); window.close(); }}
else {{ window.setTimeout(() => window.location.replace({json.dumps(fallback_url)}), 400); }}
</script></body></html>"""


async def user_with_permissions(db: AsyncIOMotorDatabase, user: dict) -> dict:
    safe_user = public_user(user)
    safe_user["feishu_binding_required"] = get_settings().feishu_auth_enabled and not has_feishu_binding(user)
    safe_user["operations_site_ids"] = normalize_operations_site_ids(user.get("operations_site_ids"))
    safe_user["permissions"] = await permissions_for_user(db, user)
    return safe_user


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    if not verify_password(payload.current_password, user.get("password_hash", "")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hash_password(payload.new_password),
                "must_change_password": False,
                "updated_at": now_utc(),
            }
        },
    )
    await write_audit_log(
        db,
        actor=user,
        action="auth.change_password",
        resource_type="user",
        resource_id=user["_id"],
    )
    return {"ok": True}
