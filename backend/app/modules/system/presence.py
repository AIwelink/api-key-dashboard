from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils import now_utc, serialize_doc


ACTIVE_PRESENCE_SECONDS = 60
PRESENCE_RETENTION_HOURS = 24


def presence_document_id(user_id: Any, client_id: str) -> str:
    identity = f"{user_id}:{client_id}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


async def record_frontend_presence(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    payload: dict[str, Any],
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    if actor.get("actor_type") == "api_token":
        raise ValueError("browser user is required")

    observed_at = observed_at or now_utc()
    client_id = str(payload.get("client_id") or "").strip()
    user_id = str(actor.get("_id") or "").strip()
    if not user_id or not client_id:
        raise ValueError("user_id and client_id are required")

    foreground_since_at = _bounded_foreground_since(payload.get("foreground_since_at"), observed_at)
    document_id = presence_document_id(user_id, client_id)
    updates = {
        "user_id": user_id,
        "user_name": actor.get("name") or actor.get("email") or user_id,
        "user_email": actor.get("email"),
        "role": actor.get("role"),
        "client_id": client_id,
        "session_id": str(payload.get("session_id") or "").strip(),
        "client_label": str(payload.get("client_label") or "Unknown client").strip(),
        "device_type": str(payload.get("device_type") or "unknown").strip(),
        "view": str(payload.get("view") or "").strip(),
        "path": str(payload.get("path") or "").strip(),
        "foreground_since_at": foreground_since_at,
        "last_seen_at": observed_at,
        "expires_at": observed_at + timedelta(hours=PRESENCE_RETENTION_HOURS),
    }
    await db.frontend_presence.update_one(
        {"_id": document_id},
        {
            "$set": updates,
            "$setOnInsert": {"created_at": observed_at},
        },
        upsert=True,
    )
    return serialize_doc({"_id": document_id, **updates})


async def remove_frontend_presence(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    client_id: str,
) -> bool:
    if actor.get("actor_type") == "api_token":
        return False
    document_id = presence_document_id(actor.get("_id"), client_id)
    result = await db.frontend_presence.delete_one({"_id": document_id})
    return bool(result.deleted_count)


async def list_active_frontend_presence(
    db: AsyncIOMotorDatabase,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    observed_at = observed_at or now_utc()
    active_after = observed_at - timedelta(seconds=ACTIVE_PRESENCE_SECONDS)
    cursor = db.frontend_presence.find({"last_seen_at": {"$gte": active_after}}).sort("last_seen_at", -1).limit(500)
    items = [serialize_doc(item) async for item in cursor]
    return {
        "items": items,
        "total": len(items),
        "active_window_seconds": ACTIVE_PRESENCE_SECONDS,
        "observed_at": observed_at,
    }


def _bounded_foreground_since(value: Any, observed_at: datetime) -> datetime:
    parsed = _datetime_or_none(value)
    if parsed is None:
        return observed_at
    earliest = observed_at - timedelta(hours=PRESENCE_RETENTION_HOURS)
    return min(observed_at, max(earliest, parsed))


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
