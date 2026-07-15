from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils import now_utc, serialize_doc


CAPACITY_LIMITS_SETTING_ID = "capacity_account_limits"

DEFAULT_CAPACITY_ACCOUNT_LIMITS: dict[str, dict[str, float]] = {
    "free": {"five_hour_usd": 2.0, "seven_day_usd": 10.0},
    "plus": {"five_hour_usd": 28.0, "seven_day_usd": 140.0},
    "team": {"five_hour_usd": 15.0, "seven_day_usd": 75.0},
    "bug_team": {"five_hour_usd": 230.0, "seven_day_usd": 230.0},
    "k12": {"five_hour_usd": 20.0, "seven_day_usd": 100.0},
    "pro": {"five_hour_usd": 360.0, "seven_day_usd": 2100.0},
}


def normalize_capacity_limits(value: Any) -> dict[str, dict[str, float]]:
    source = value if isinstance(value, dict) else {}
    normalized: dict[str, dict[str, float]] = {}
    for account_type, defaults in DEFAULT_CAPACITY_ACCOUNT_LIMITS.items():
        item = source.get(account_type) if isinstance(source.get(account_type), dict) else {}
        normalized[account_type] = {
            "five_hour_usd": _positive_float(item.get("five_hour_usd"), defaults["five_hour_usd"]),
            "seven_day_usd": _positive_float(item.get("seven_day_usd"), defaults["seven_day_usd"]),
        }
    return normalized


def capacity_limits_setting_id(site_id: str | None = None) -> str:
    normalized_site_id = str(site_id or "").strip()
    return f"{CAPACITY_LIMITS_SETTING_ID}:{normalized_site_id}" if normalized_site_id else CAPACITY_LIMITS_SETTING_ID


async def get_capacity_account_limits(db: AsyncIOMotorDatabase, site_id: str | None = None) -> dict[str, Any]:
    normalized_site_id = str(site_id or "").strip() or None
    doc = await db.app_settings.find_one({"_id": capacity_limits_setting_id(normalized_site_id)})
    inherited_from_global = False
    if doc is None and normalized_site_id is not None:
        doc = await db.app_settings.find_one({"_id": CAPACITY_LIMITS_SETTING_ID})
        inherited_from_global = doc is not None
    limits = normalize_capacity_limits((doc or {}).get("limits"))
    return {
        "site_id": normalized_site_id,
        "limits": limits,
        "default_limits": DEFAULT_CAPACITY_ACCOUNT_LIMITS,
        "inherited_from_global": inherited_from_global,
        "updated_at": (doc or {}).get("updated_at"),
        "updated_by_user_id": (doc or {}).get("updated_by_user_id"),
        "updated_by_name": (doc or {}).get("updated_by_name"),
    }


async def update_capacity_account_limits(
    db: AsyncIOMotorDatabase,
    limits: dict[str, Any],
    actor: dict[str, Any],
    site_id: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_capacity_limits(limits)
    normalized_site_id = str(site_id or "").strip() or None
    now = now_utc()
    await db.app_settings.update_one(
        {"_id": capacity_limits_setting_id(normalized_site_id)},
        {
            "$set": {
                "site_id": normalized_site_id,
                "limits": normalized,
                "updated_at": now,
                "updated_by_user_id": actor.get("_id"),
                "updated_by_name": actor.get("name") or actor.get("email") or actor.get("_id"),
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return serialize_doc(await get_capacity_account_limits(db, normalized_site_id))


def _positive_float(value: Any, fallback: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return float(fallback)
    if parsed < 0:
        return float(fallback)
    return parsed
