"""Feishu OAuth authentication and local identity binding."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlencode

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.config import Settings, get_settings
from app.utils import now_utc


AuthPurpose = Literal["login", "bind"]
SESSION_TTL = timedelta(minutes=5)
TICKET_TTL = timedelta(seconds=60)


class FeishuAuthError(RuntimeError):
    def __init__(self, message: str, *, code: str = "feishu_auth_failed") -> None:
        super().__init__(message)
        self.code = code


class FeishuConfigurationError(FeishuAuthError):
    pass


class FeishuBindingConflictError(FeishuAuthError):
    pass


@dataclass(frozen=True)
class FeishuIdentity:
    tenant_key: str
    open_id: str
    union_id: str | None
    user_id: str | None
    name: str | None
    email: str | None
    avatar_url: str | None

    @property
    def identity_key(self) -> str:
        if self.union_id:
            return f"{self.tenant_key}:union:{self.union_id}"
        return f"{self.tenant_key}:open:{self.open_id}"


@dataclass(frozen=True)
class FeishuAuthorizationSession:
    session_id: str
    authorization_url: str
    ticket: str
    expires_at: datetime


async def create_authorization_session(
    db: AsyncIOMotorDatabase,
    *,
    purpose: AuthPurpose,
    target_user_id: str | None = None,
    settings: Settings | None = None,
) -> FeishuAuthorizationSession:
    settings = settings or get_settings()
    _require_configured(settings)
    if purpose == "bind" and not target_user_id:
        raise ValueError("bind authorization requires a target user")

    created_at = now_utc()
    expires_at = created_at + SESSION_TTL
    session_id = str(uuid.uuid4())
    state = secrets.token_urlsafe(32)
    ticket = secrets.token_urlsafe(32)
    document = {
        "_id": session_id,
        "purpose": purpose,
        "target_user_id": target_user_id,
        "state_hash": _secret_hash(state),
        "ticket_hash": _secret_hash(ticket),
        "status": "pending",
        "created_at": created_at,
        "expires_at": expires_at,
    }
    await db.feishu_auth_sessions.insert_one(document)
    query = urlencode(
        {
            "app_id": settings.feishu_app_id,
            "redirect_uri": settings.feishu_redirect_uri,
            "state": state,
        }
    )
    authorization_url = (
        f"{settings.feishu_authorize_base_url.rstrip('/')}"
        f"/open-apis/authen/v1/authorize?{query}"
    )
    return FeishuAuthorizationSession(
        session_id=session_id,
        authorization_url=authorization_url,
        ticket=ticket,
        expires_at=expires_at,
    )


async def resolve_feishu_user(
    db: AsyncIOMotorDatabase,
    *,
    identity: FeishuIdentity,
    purpose: AuthPurpose,
    target_user_id: str | None,
) -> dict[str, Any]:
    current = await db.users.find_one({"feishu_identity.identity_key": identity.identity_key})
    if current is not None:
        if purpose == "bind" and target_user_id and current.get("_id") != target_user_id:
            raise FeishuBindingConflictError(
                "该飞书账号已绑定其他用户，请联系管理员",
                code="identity_already_bound",
            )
        _require_enabled_user(current)
        await db.users.update_one(
            {"_id": current["_id"], "status": {"$ne": "disabled"}},
            {"$set": _identity_login_updates(identity)},
        )
        return current

    candidate: dict[str, Any] | None = None
    bound_via: Literal["feishu_email", "password_binding"]
    if purpose == "bind":
        if not target_user_id:
            raise ValueError("bind authorization requires a target user")
        candidate = await db.users.find_one({"_id": target_user_id})
        bound_via = "password_binding"
    else:
        email = _normalize_email(identity.email)
        if email:
            candidate = await db.users.find_one({"email": email})
        bound_via = "feishu_email"

    if candidate is not None:
        _require_enabled_user(candidate)
        current_identity_key = ((candidate.get("feishu_identity") or {}).get("identity_key"))
        if current_identity_key and current_identity_key != identity.identity_key:
            raise FeishuBindingConflictError(
                "当前用户已绑定其他飞书账号，请联系管理员",
                code="user_already_bound",
            )
        if current_identity_key == identity.identity_key:
            return candidate

        updates = _identity_login_updates(identity)
        updates["feishu_identity.bound_via"] = bound_via
        updates["feishu_identity.bound_at"] = now_utc()
        bound = await db.users.find_one_and_update(
            {
                "_id": candidate["_id"],
                "status": {"$ne": "disabled"},
                "feishu_identity.identity_key": {"$exists": False},
            },
            {"$set": updates},
            return_document=ReturnDocument.AFTER,
        )
        if bound is not None:
            return bound
        concurrent = await db.users.find_one({"feishu_identity.identity_key": identity.identity_key})
        if concurrent is not None and concurrent.get("_id") == candidate.get("_id"):
            return concurrent
        raise FeishuBindingConflictError(
            "飞书账号绑定发生冲突，请联系管理员",
            code="binding_conflict",
        )

    if purpose == "bind":
        raise FeishuAuthError("待绑定的本地用户不存在", code="target_user_not_found")
    return await _create_pending_user(db, identity)


async def consume_login_ticket(
    db: AsyncIOMotorDatabase,
    *,
    ticket: str,
) -> dict[str, Any]:
    consumed_at = now_utc()
    session = await db.feishu_auth_sessions.find_one_and_update(
        {
            "ticket_hash": _secret_hash(ticket),
            "status": "completed",
            "consumed_at": {"$exists": False},
            "ticket_expires_at": {"$gt": consumed_at},
        },
        {"$set": {"consumed_at": consumed_at}},
        return_document=ReturnDocument.AFTER,
    )
    if session is None:
        raise FeishuAuthError("登录票据无效或已使用", code="ticket_invalid")
    user = await db.users.find_one({"_id": session.get("result_user_id")})
    if user is None:
        raise FeishuAuthError("登录用户不存在", code="user_not_found")
    _require_enabled_user(user)
    return user


async def fetch_feishu_identity(
    *,
    code: str,
    settings: Settings | None = None,
    http_client: Any | None = None,
) -> FeishuIdentity:
    settings = settings or get_settings()
    _require_configured(settings)
    if http_client is None:
        async with httpx.AsyncClient(timeout=settings.feishu_request_timeout_seconds) as client:
            return await _fetch_feishu_identity(client, code=code, settings=settings)
    return await _fetch_feishu_identity(http_client, code=code, settings=settings)


async def complete_authorization_session(
    db: AsyncIOMotorDatabase,
    *,
    state: str,
    code: str,
    settings: Settings | None = None,
    http_client: Any | None = None,
) -> str:
    settings = settings or get_settings()
    _require_configured(settings)
    claimed_at = now_utc()
    session = await db.feishu_auth_sessions.find_one_and_update(
        {
            "state_hash": _secret_hash(state),
            "status": "pending",
            "expires_at": {"$gt": claimed_at},
        },
        {"$set": {"status": "processing", "claimed_at": claimed_at}},
        return_document=ReturnDocument.AFTER,
    )
    if session is None:
        raise FeishuAuthError("授权状态已过期或已使用", code="state_invalid")

    try:
        identity = await fetch_feishu_identity(
            code=code,
            settings=settings,
            http_client=http_client,
        )
        user = await resolve_feishu_user(
            db,
            identity=identity,
            purpose=session["purpose"],
            target_user_id=session.get("target_user_id"),
        )
    except Exception as exc:
        error_code = exc.code if isinstance(exc, FeishuAuthError) else "feishu_exchange_failed"
        await db.feishu_auth_sessions.update_one(
            {"_id": session["_id"], "status": "processing"},
            {
                "$set": {
                    "status": "failed",
                    "error_code": error_code,
                    "completed_at": now_utc(),
                }
            },
        )
        if isinstance(exc, FeishuAuthError):
            raise
        raise FeishuAuthError("飞书授权失败，请重试", code=error_code) from exc

    completed_at = now_utc()
    await db.feishu_auth_sessions.update_one(
        {"_id": session["_id"], "status": "processing"},
        {
            "$set": {
                "status": "completed",
                "result_user_id": user["_id"],
                "ticket_expires_at": completed_at + TICKET_TTL,
                "completed_at": completed_at,
            }
        },
    )
    return str(session["_id"])


async def get_authorization_session_status(
    db: AsyncIOMotorDatabase,
    *,
    session_id: str,
    ticket: str,
) -> dict[str, Any]:
    session = await db.feishu_auth_sessions.find_one(
        {"_id": session_id, "ticket_hash": _secret_hash(ticket)}
    )
    if session is None:
        raise FeishuAuthError("授权会话不存在", code="session_not_found")

    current_time = now_utc()
    expires_at = _stored_datetime_as_utc(session.get("expires_at"))
    if expires_at is None or expires_at <= current_time:
        raise FeishuAuthError("授权会话已过期", code="session_expired")
    if session.get("status") == "completed":
        ticket_expires_at = _stored_datetime_as_utc(session.get("ticket_expires_at"))
        if ticket_expires_at is None or ticket_expires_at <= current_time:
            raise FeishuAuthError("登录票据已过期", code="ticket_expired")
    return {
        "session_id": session_id,
        "status": session.get("status") or "pending",
        "error_code": session.get("error_code"),
        "expires_at": expires_at,
    }


async def fail_authorization_session(
    db: AsyncIOMotorDatabase,
    *,
    state: str,
    error_code: str,
) -> str:
    failed_at = now_utc()
    session = await db.feishu_auth_sessions.find_one_and_update(
        {
            "state_hash": _secret_hash(state),
            "status": "pending",
            "expires_at": {"$gt": failed_at},
        },
        {
            "$set": {
                "status": "failed",
                "error_code": error_code,
                "completed_at": failed_at,
            }
        },
        return_document=ReturnDocument.AFTER,
    )
    if session is None:
        raise FeishuAuthError("授权状态已过期或已使用", code="state_invalid")
    return str(session["_id"])


async def _fetch_feishu_identity(
    client: Any,
    *,
    code: str,
    settings: Settings,
) -> FeishuIdentity:
    token_url = f"{settings.feishu_open_api_base_url.rstrip('/')}/open-apis/authen/v2/oauth/token"
    token_response = await client.post(
        token_url,
        json={
            "grant_type": "authorization_code",
            "client_id": settings.feishu_app_id,
            "client_secret": settings.feishu_app_secret,
            "code": code,
            "redirect_uri": settings.feishu_redirect_uri,
        },
    )
    token_response.raise_for_status()
    token_payload = _successful_payload(token_response.json(), operation="交换授权码")
    access_token = token_payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise FeishuAuthError("飞书未返回用户访问凭证", code="access_token_missing")

    user_info_url = f"{settings.feishu_open_api_base_url.rstrip('/')}/open-apis/authen/v1/user_info"
    user_response = await client.get(
        user_info_url,
        headers={"Authorization": f"Bearer {access_token}"},
    )
    user_response.raise_for_status()
    user_payload = _successful_payload(user_response.json(), operation="读取用户信息")
    tenant_key = _text(user_payload.get("tenant_key"))
    open_id = _text(user_payload.get("open_id"))
    if not tenant_key or not open_id:
        raise FeishuAuthError("飞书用户身份信息不完整", code="identity_incomplete")
    if tenant_key not in settings.allowed_feishu_tenant_keys():
        raise FeishuAuthError("当前飞书租户未获准访问系统", code="tenant_forbidden")

    return FeishuIdentity(
        tenant_key=tenant_key,
        open_id=open_id,
        union_id=_text(user_payload.get("union_id")),
        user_id=_text(user_payload.get("user_id")),
        name=_text(user_payload.get("name")),
        email=_normalize_email(
            _text(user_payload.get("enterprise_email")) or _text(user_payload.get("email"))
        ),
        avatar_url=_text(user_payload.get("avatar_url")),
    )


def _identity_login_updates(identity: FeishuIdentity) -> dict[str, Any]:
    timestamp = now_utc()
    return {
        "feishu_identity.identity_key": identity.identity_key,
        "feishu_identity.tenant_key": identity.tenant_key,
        "feishu_identity.union_id": identity.union_id,
        "feishu_identity.open_id": identity.open_id,
        "feishu_identity.user_id": identity.user_id,
        "feishu_identity.name": identity.name,
        "feishu_identity.email": _normalize_email(identity.email),
        "feishu_identity.avatar_url": identity.avatar_url,
        "last_feishu_login_at": timestamp,
        "updated_at": timestamp,
    }


async def _create_pending_user(
    db: AsyncIOMotorDatabase,
    identity: FeishuIdentity,
) -> dict[str, Any]:
    timestamp = now_utc()
    generated_id = f"feishu-{uuid.uuid4().hex}"
    normalized_email = _normalize_email(identity.email)
    document = {
        "_id": generated_id,
        "email": normalized_email or f"{generated_id}@identity.invalid",
        "name": identity.name or "飞书用户",
        "role": "viewer",
        "status": "active",
        "authorization_status": "pending",
        "must_change_password": False,
        "feishu_identity": {
            "identity_key": identity.identity_key,
            "tenant_key": identity.tenant_key,
            "union_id": identity.union_id,
            "open_id": identity.open_id,
            "user_id": identity.user_id,
            "name": identity.name,
            "email": normalized_email,
            "avatar_url": identity.avatar_url,
            "bound_at": timestamp,
            "bound_via": "auto_provision",
        },
        "last_feishu_login_at": timestamp,
        "created_by": "feishu",
        "updated_by": "feishu",
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    try:
        await db.users.insert_one(document)
        return document
    except DuplicateKeyError:
        existing = await db.users.find_one({"feishu_identity.identity_key": identity.identity_key})
        if existing is not None:
            _require_enabled_user(existing)
            return existing
        raise FeishuBindingConflictError(
            "飞书用户创建发生冲突，请联系管理员",
            code="provision_conflict",
        )


def _normalize_email(value: str | None) -> str | None:
    normalized = (value or "").strip().lower()
    return normalized or None


def _successful_payload(value: Any, *, operation: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise FeishuAuthError(f"飞书{operation}响应格式错误", code="response_invalid")
    result_code = value.get("code")
    if result_code not in (None, 0):
        raise FeishuAuthError(f"飞书{operation}失败", code="feishu_api_error")
    nested = value.get("data")
    if isinstance(nested, dict):
        return nested
    return value


def _text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _stored_datetime_as_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _require_enabled_user(user: dict[str, Any]) -> None:
    if user.get("status") == "disabled":
        raise FeishuAuthError("用户已被禁用", code="user_disabled")


def _require_configured(settings: Settings) -> None:
    if not settings.feishu_auth_enabled:
        raise FeishuConfigurationError("飞书登录未启用", code="feishu_disabled")
    if not settings.feishu_app_id or not settings.feishu_app_secret or not settings.feishu_redirect_uri:
        raise FeishuConfigurationError("飞书登录配置不完整", code="feishu_misconfigured")
    if not settings.allowed_feishu_tenant_keys():
        raise FeishuConfigurationError("飞书允许租户未配置", code="feishu_tenant_missing")
