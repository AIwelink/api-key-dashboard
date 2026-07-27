from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.sub2api.smart_scheduling import (
    default_smart_scheduling_rules,
    normalize_smart_scheduling_rules,
)
from app.utils import now_utc, serialize_doc


SMART_SCHEDULING_SETTING_PREFIX = "smart_scheduling"


def smart_scheduling_setting_id(site_id: str) -> str:
    return f"{SMART_SCHEDULING_SETTING_PREFIX}:{str(site_id).strip()}"


async def get_smart_scheduling_settings(
    db: AsyncIOMotorDatabase,
    site_id: str,
) -> dict[str, Any]:
    normalized_site_id = str(site_id).strip()
    document = await db.app_settings.find_one(
        {"_id": smart_scheduling_setting_id(normalized_site_id)}
    )
    rules = normalize_smart_scheduling_rules((document or {}).get("rules"))
    last_run = await db.sub2api_smart_scheduling_runs.find_one(
        {"site_id": normalized_site_id},
        sort=[("started_at", -1)],
    )
    return serialize_doc(
        {
            "site_id": normalized_site_id,
            "rules": rules,
            "default_rules": default_smart_scheduling_rules(),
            "last_run": last_run,
            "updated_at": (document or {}).get("updated_at"),
            "updated_by_user_id": (document or {}).get("updated_by_user_id"),
            "updated_by_name": (document or {}).get("updated_by_name"),
        }
    )


async def update_smart_scheduling_settings(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    rules: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    normalized_site_id = str(site_id).strip()
    normalized_rules = normalize_smart_scheduling_rules(rules)
    updated_at = now_utc()
    await db.app_settings.update_one(
        {"_id": smart_scheduling_setting_id(normalized_site_id)},
        {
            "$set": {
                "site_id": normalized_site_id,
                "rules": normalized_rules,
                "updated_at": updated_at,
                "updated_by_user_id": actor.get("_id"),
                "updated_by_name": (
                    actor.get("name")
                    or actor.get("email")
                    or actor.get("_id")
                ),
            },
            "$setOnInsert": {"created_at": updated_at},
        },
        upsert=True,
    )
    return await get_smart_scheduling_settings(db, normalized_site_id)
