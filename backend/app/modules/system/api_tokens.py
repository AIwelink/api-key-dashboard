import secrets
from datetime import timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.schemas import ApiTokenCreate
from app.security import hash_api_token
from app.utils import now_utc, serialize_doc


def public_api_token(document: dict[str, Any]) -> dict[str, Any]:
    data = serialize_doc(document)
    data.pop("token_hash", None)
    return data


def generate_api_token() -> str:
    return f"akd_{secrets.token_urlsafe(32)}"


async def list_api_tokens(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    items = [public_api_token(item) async for item in db.api_tokens.find({}).sort("created_at", -1)]
    return {"items": items, "total": len(items)}


async def create_api_token(db: AsyncIOMotorDatabase, *, payload: ApiTokenCreate, actor: dict[str, Any]) -> dict[str, Any]:
    now = now_utc()
    token = generate_api_token()
    expires_at = now + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None
    token_id = secrets.token_hex(12)
    document = {
        "_id": token_id,
        "name": payload.name.strip(),
        "role": payload.role,
        "status": "active",
        "token_prefix": token[:12],
        "token_hash": hash_api_token(token),
        "note": payload.note.strip() if payload.note else None,
        "expires_at": expires_at,
        "last_used_at": None,
        "usage_count": 0,
        "created_by": actor.get("_id"),
        "updated_by": actor.get("_id"),
        "created_at": now,
        "updated_at": now,
    }
    await db.api_tokens.insert_one(document)
    result = public_api_token(document)
    result["token"] = token
    return result


async def revoke_api_token(db: AsyncIOMotorDatabase, *, token_id: str, actor: dict[str, Any]) -> bool:
    now = now_utc()
    result = await db.api_tokens.update_one(
        {"_id": token_id, "status": {"$ne": "revoked"}},
        {
            "$set": {
                "status": "revoked",
                "revoked_at": now,
                "revoked_by": actor.get("_id"),
                "updated_by": actor.get("_id"),
                "updated_at": now,
            }
        },
    )
    return result.matched_count > 0
