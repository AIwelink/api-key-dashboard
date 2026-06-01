import base64
import hashlib
import hmac
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.database import db_dependency
from app.utils import now_utc


bearer_scheme = HTTPBearer(auto_error=False)


def hash_password(password: str, *, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000)
    return f"pbkdf2_sha256$200000${salt}${base64.b64encode(digest).decode('ascii')}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt, digest = stored_hash.split("$", 3)
    except ValueError:
        return False
    if algorithm != "pbkdf2_sha256":
        return False
    recalculated = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        int(iterations),
    )
    expected = base64.b64decode(digest.encode("ascii"))
    return hmac.compare_digest(recalculated, expected)


def hash_api_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode((data + padding).encode("ascii"))


def create_access_token(subject: str, role: str) -> str:
    settings = get_settings()
    expires_at = now_utc() + timedelta(minutes=settings.access_token_expire_minutes)
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "sub": subject,
        "role": role,
        "exp": int(expires_at.timestamp()),
        "iat": int(now_utc().timestamp()),
    }
    signing_input = ".".join(
        [
            _b64url_encode(json.dumps(header, separators=(",", ":")).encode("utf-8")),
            _b64url_encode(json.dumps(payload, separators=(",", ":")).encode("utf-8")),
        ]
    )
    signature = hmac.new(
        settings.app_secret_key.encode("utf-8"),
        signing_input.encode("ascii"),
        hashlib.sha256,
    ).digest()
    return f"{signing_input}.{_b64url_encode(signature)}"


def decode_access_token(token: str) -> dict[str, Any]:
    settings = get_settings()
    try:
        signing_input, signature = token.rsplit(".", 1)
        expected_signature = hmac.new(
            settings.app_secret_key.encode("utf-8"),
            signing_input.encode("ascii"),
            hashlib.sha256,
        ).digest()
        if not hmac.compare_digest(_b64url_decode(signature), expected_signature):
            raise ValueError("Invalid signature")
        _header, payload = signing_input.split(".", 1)
        data = json.loads(_b64url_decode(payload))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

    exp = data.get("exp")
    if not isinstance(exp, int) or datetime.fromtimestamp(exp, UTC) < now_utc():
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="登录过期")
    return data


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, Any]:
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    if credentials.credentials.startswith("akd_"):
        return await get_api_token_actor(credentials.credentials, db)
    payload = decode_access_token(credentials.credentials)
    user = await db.users.find_one({"_id": payload.get("sub")})
    if user is None or user.get("status") == "disabled":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
    return user


async def get_api_token_actor(token: str, db: AsyncIOMotorDatabase) -> dict[str, Any]:
    token_hash = hash_api_token(token)
    document = await db.api_tokens.find_one({"token_hash": token_hash})
    now = now_utc()
    if document is None or document.get("status") != "active":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API token")
    expires_at = document.get("expires_at")
    if isinstance(expires_at, datetime):
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)
        if expires_at < now:
            await db.api_tokens.update_one({"_id": document["_id"]}, {"$set": {"status": "expired", "updated_at": now}})
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="API token expired")

    await db.api_tokens.update_one(
        {"_id": document["_id"]},
        {"$set": {"last_used_at": now}, "$inc": {"usage_count": 1}},
    )
    token_id = str(document["_id"])
    return {
        "_id": f"api_token:{token_id}",
        "email": f"{document.get('name') or token_id}@api-token.local",
        "name": document.get("name") or "API Token",
        "role": document.get("role") or "viewer",
        "status": "active",
        "actor_type": "api_token",
        "api_token_id": token_id,
        "api_token_prefix": document.get("token_prefix"),
    }


def require_roles(*roles: str):
    async def dependency(user: dict[str, Any] = Depends(get_current_user)) -> dict[str, Any]:
        if user.get("role") not in roles:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
        return user

    return dependency
