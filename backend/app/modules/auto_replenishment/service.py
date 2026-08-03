from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.auto_replenishment.settings import (
    SETTINGS_ID,
    decrypt_configured_password,
    get_stored_auto_replenishment_settings,
)
from app.modules.auto_replenishment.sogouedu import SogouEduClient, SogouEduError
from app.utils import now_utc, serialize_doc


async def test_supplier_connection(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    client: SogouEduClient | None = None,
    secret_key: str | None = None,
) -> dict[str, Any]:
    document = await get_stored_auto_replenishment_settings(db)
    if document is None:
        raise LookupError("auto replenishment settings are not configured")
    username = str(document.get("username") or "").strip()
    if not username:
        raise ValueError("supplier username is not configured")
    password = decrypt_configured_password(document, secret_key=secret_key)
    provider_client = client or SogouEduClient(base_url=str(document.get("base_url") or "https://sogouedu.cc"))

    try:
        result = await provider_client.test_connection(
            username=username,
            password=password,
            product=str(document.get("product") or "oauth_7d"),
        )
    except SogouEduError as exc:
        result = {
            "ok": False,
            "tested_at": now_utc(),
            "error": str(exc),
        }

    tested_at = result.get("tested_at") or now_utc()
    updates = {
        "last_test_at": tested_at,
        "last_test_ok": result.get("ok") is True,
        "last_test_error": str(result.get("error") or ""),
        "last_test_balance": result.get("balance") if isinstance(result.get("balance"), dict) else None,
        "last_test_inventory": result.get("inventory") if isinstance(result.get("inventory"), dict) else None,
        "last_test_by": actor.get("_id"),
        "last_test_by_name": actor.get("name") or actor.get("email") or actor.get("_id"),
    }
    await db.auto_replenishment_settings.update_one({"_id": SETTINGS_ID}, {"$set": updates})
    return serialize_doc(result)
