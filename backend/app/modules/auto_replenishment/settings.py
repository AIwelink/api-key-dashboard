from __future__ import annotations

import re
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.modules.auto_replenishment.secrets import decrypt_secret, encrypt_secret
from app.utils import now_utc, serialize_doc


SETTINGS_ID = "sogouedu:us06-5001:plus-account-pool-01"
DEFAULT_SETTINGS: dict[str, Any] = {
    "provider": "sogouedu",
    "base_url": "https://sogouedu.cc",
    "enabled": False,
    "username": "",
    "minimum_account_count": 2,
    "minimum_runway_minutes": 5,
    "product": "oauth_7d",
    "local_account_type": "team",
    "target_site_id": "us06-5001",
    "target_group_id": None,
    "target_group_name": "plus账号池01",
}


async def get_auto_replenishment_settings(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    document = await db.auto_replenishment_settings.find_one({"_id": SETTINGS_ID})
    return public_auto_replenishment_settings(document)


async def get_stored_auto_replenishment_settings(db: AsyncIOMotorDatabase) -> dict[str, Any] | None:
    return await db.auto_replenishment_settings.find_one({"_id": SETTINGS_ID})


async def save_auto_replenishment_settings(
    db: AsyncIOMotorDatabase,
    *,
    payload: dict[str, Any],
    actor: dict[str, Any],
    secret_key: str | None = None,
) -> dict[str, Any]:
    current = await get_stored_auto_replenishment_settings(db)
    username = str(payload.get("username") or "").strip()
    if not username:
        raise ValueError("username is required")

    incoming_password = str(payload.get("password") or "")
    current_ciphertext = str((current or {}).get("password_ciphertext") or "")
    if not incoming_password and not current_ciphertext:
        raise ValueError("password is required for initial configuration")

    minimum_account_count = _bounded_int(
        payload.get("minimum_account_count", DEFAULT_SETTINGS["minimum_account_count"]),
        field="minimum_account_count",
        minimum=1,
        maximum=10_000,
    )
    minimum_runway_minutes = _bounded_int(
        payload.get("minimum_runway_minutes", DEFAULT_SETTINGS["minimum_runway_minutes"]),
        field="minimum_runway_minutes",
        minimum=1,
        maximum=1_440,
    )
    target_site = await _require_target_site(db)
    target_group = await _require_target_group(db)
    group_id = _group_id(target_group)
    if group_id is None:
        raise LookupError("target sub2api group is missing its group id")

    now = now_utc()
    key = secret_key if secret_key is not None else get_settings().app_secret_key
    ciphertext = encrypt_secret(incoming_password, key) if incoming_password else current_ciphertext
    document: dict[str, Any] = {
        **DEFAULT_SETTINGS,
        "_id": SETTINGS_ID,
        "enabled": _boolean(payload.get("enabled", DEFAULT_SETTINGS["enabled"]), field="enabled"),
        "username": username,
        "password_ciphertext": ciphertext,
        "password_encryption_version": 1,
        "minimum_account_count": minimum_account_count,
        "minimum_runway_minutes": minimum_runway_minutes,
        "target_site_id": str(target_site.get("_id") or DEFAULT_SETTINGS["target_site_id"]),
        "target_group_id": group_id,
        "target_group_name": _group_name(target_group) or str(DEFAULT_SETTINGS["target_group_name"]),
        "updated_by": actor.get("_id"),
        "updated_by_name": actor.get("name") or actor.get("email") or actor.get("_id"),
        "updated_at": now,
        "created_by": (current or {}).get("created_by") or actor.get("_id"),
        "created_by_name": (current or {}).get("created_by_name") or actor.get("name") or actor.get("email") or actor.get("_id"),
        "created_at": (current or {}).get("created_at") or now,
    }
    for key_name in (
        "last_test_at",
        "last_test_ok",
        "last_test_error",
        "last_test_balance",
        "last_test_inventory",
        "last_test_by",
        "last_test_by_name",
    ):
        if key_name in (current or {}):
            document[key_name] = current[key_name]

    await db.auto_replenishment_settings.replace_one({"_id": SETTINGS_ID}, document, upsert=True)
    return public_auto_replenishment_settings(document)


def public_auto_replenishment_settings(document: dict[str, Any] | None) -> dict[str, Any]:
    source = {**DEFAULT_SETTINGS, **(document or {})}
    result = {
        key: value
        for key, value in source.items()
        if key not in {"_id", "password_ciphertext", "password_encryption_version"}
    }
    result["password_configured"] = bool((document or {}).get("password_ciphertext"))
    return serialize_doc(result)


def decrypt_configured_password(document: dict[str, Any], *, secret_key: str | None = None) -> str:
    ciphertext = str(document.get("password_ciphertext") or "")
    if not ciphertext:
        raise ValueError("supplier password is not configured")
    key = secret_key if secret_key is not None else get_settings().app_secret_key
    return decrypt_secret(ciphertext, key)


async def _require_target_site(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    site_id = str(DEFAULT_SETTINGS["target_site_id"])
    site = await db.sub2api_sites.find_one({"_id": site_id, "status": "active"})
    if site is None or str(site.get("site_type") or "sub2api").strip().lower() != "sub2api":
        raise LookupError("target sub2api site not found or inactive")
    return site


async def _require_target_group(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    site_id = str(DEFAULT_SETTINGS["target_site_id"])
    group_name_pattern = re.compile(r"^plus\s*账号池\s*0*1$", re.IGNORECASE)
    group = await db.sub2api_groups_cache.find_one(
        {
            "site_id": site_id,
            "$or": [
                {"group.name": group_name_pattern},
                {"name": group_name_pattern},
            ],
        }
    )
    if group is None:
        raise LookupError("target sub2api group not found")
    return group


def _group_id(document: dict[str, Any]) -> int | None:
    value = document.get("group_id")
    if value is None and isinstance(document.get("group"), dict):
        value = document["group"].get("id")
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _group_name(document: dict[str, Any]) -> str:
    value = document.get("name")
    if not value and isinstance(document.get("group"), dict):
        value = document["group"].get("name")
    return str(value or "").strip()


def _bounded_int(value: Any, *, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be an integer") from exc
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{field} must be between {minimum} and {maximum}")
    return parsed


def _boolean(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError(f"{field} must be a boolean")
