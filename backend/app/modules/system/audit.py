from decimal import Decimal
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils import now_utc


def _bson_safe_audit_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _bson_safe_audit_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_bson_safe_audit_value(item) for item in value]
    return value


async def write_audit_log(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any] | None,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
) -> None:
    document = {
        "actor_type": actor.get("actor_type") if actor and actor.get("actor_type") else ("user" if actor else "system"),
        "actor_id": actor.get("_id") if actor else None,
        "actor_name": actor.get("name") if actor else None,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "before": _bson_safe_audit_value(before),
        "after": _bson_safe_audit_value(after),
        "created_at": now_utc(),
    }
    if dedupe_key is None:
        await db.audit_logs.insert_one(document)
        return

    document["dedupe_key"] = dedupe_key
    await db.audit_logs.update_one(
        {"dedupe_key": dedupe_key},
        {"$setOnInsert": document},
        upsert=True,
    )
    if after is not None:
        await db.audit_logs.update_one(
            {"dedupe_key": dedupe_key, "after": None},
            {"$set": {"after": document["after"]}},
        )
