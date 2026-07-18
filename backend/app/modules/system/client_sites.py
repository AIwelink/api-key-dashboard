from __future__ import annotations

from typing import Any
from urllib.parse import unquote, urlparse

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils import now_utc, serialize_doc


CLIENT_SITE_TYPES = {"newapi", "sub2api"}
DATABASE_SCHEME_BY_CLIENT_TYPE = {
    "newapi": "mysql",
    "sub2api": "postgresql",
}
DEFAULT_DATA_RETENTION_DAYS = 90
MAX_DATA_RETENTION_DAYS = 3650


def public_client_site(site: dict[str, Any]) -> dict[str, Any]:
    result = dict(site)
    result["id"] = str(result.get("_id") or result.get("id") or "")
    result["client_type"] = _client_type(result.get("client_type"))
    result.setdefault("status", "active")
    result.setdefault("data_retention_days", DEFAULT_DATA_RETENTION_DAYS)
    result["api_key_configured"] = bool(result.get("api_key"))
    database_dsn = str(result.get("database_dsn") or "")
    result["database_dsn_configured"] = bool(database_dsn)
    result["database_type"] = DATABASE_SCHEME_BY_CLIENT_TYPE[result["client_type"]]
    if database_dsn:
        result["database_endpoint"] = _database_endpoint(database_dsn)
    result.pop("api_key", None)
    result.pop("database_dsn", None)
    return serialize_doc(result)


async def list_client_sites(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    cursor = db.client_sites.find({"status": {"$ne": "deleted"}}).sort([("created_at", 1), ("_id", 1)])
    items = [public_client_site(doc) async for doc in cursor]
    return {"items": items, "total": len(items)}


async def get_client_site(db: AsyncIOMotorDatabase, site_id: str, *, include_api_key: bool = False) -> dict[str, Any] | None:
    doc = await db.client_sites.find_one({"_id": site_id, "status": {"$ne": "deleted"}})
    if doc is None:
        return None
    return serialize_doc(doc | {"id": site_id}) if include_api_key else public_client_site(doc)


async def create_client_site(
    db: AsyncIOMotorDatabase,
    *,
    payload: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    site_id = str(payload.get("id") or "").strip()
    base_url = _http_url(payload.get("base_url"), "base_url")
    if not site_id or not base_url:
        raise ValueError("client site id and base_url are required")
    client_type = _client_type(payload.get("client_type"))
    admin_user_id = str(payload.get("admin_user_id") or "").strip()
    if client_type == "newapi" and not admin_user_id:
        raise ValueError("admin_user_id is required for newapi client sites")
    database_dsn = _database_dsn(payload.get("database_dsn"), client_type)
    now = now_utc()
    doc = {
        "_id": site_id,
        "name": str(payload.get("name") or site_id).strip() or site_id,
        "client_type": client_type,
        "base_url": base_url,
        "api_key": str(payload.get("api_key") or "").strip(),
        "admin_user_id": admin_user_id if client_type == "newapi" else "",
        "status": _status(payload.get("status")),
        "note": str(payload.get("note") or "").strip(),
        "data_retention_days": _retention_days(payload.get("data_retention_days")),
        "created_by": actor.get("_id"),
        "created_by_name": actor.get("name") or actor.get("email") or actor.get("_id"),
        "updated_by": actor.get("_id"),
        "updated_by_name": actor.get("name") or actor.get("email") or actor.get("_id"),
        "created_at": now,
        "updated_at": now,
    }
    if database_dsn:
        doc["database_dsn"] = database_dsn
    await db.client_sites.replace_one({"_id": site_id}, doc, upsert=True)
    return await get_client_site(db, site_id) or public_client_site(doc)


async def update_client_site(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    payload: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    current = await db.client_sites.find_one({"_id": site_id, "status": {"$ne": "deleted"}})
    if current is None:
        return {}
    client_type = _client_type(payload.get("client_type", current.get("client_type")))
    admin_user_id = str(payload.get("admin_user_id", current.get("admin_user_id")) or "").strip()
    if client_type == "newapi" and not admin_user_id:
        raise ValueError("admin_user_id is required for newapi client sites")
    incoming_database_dsn = str(payload.get("database_dsn") or "").strip()
    current_database_dsn = str(current.get("database_dsn") or "").strip()
    selected_database_dsn = _database_dsn(incoming_database_dsn or current_database_dsn, client_type)
    updates: dict[str, Any] = {
        "client_type": client_type,
        "admin_user_id": admin_user_id if client_type == "newapi" else "",
        "updated_by": actor.get("_id"),
        "updated_by_name": actor.get("name") or actor.get("email") or actor.get("_id"),
        "updated_at": now_utc(),
    }
    if "name" in payload:
        updates["name"] = str(payload.get("name") or site_id).strip() or site_id
    if "base_url" in payload:
        updates["base_url"] = _http_url(payload.get("base_url"), "base_url")
    if str(payload.get("api_key") or "").strip():
        updates["api_key"] = str(payload["api_key"]).strip()
    if incoming_database_dsn:
        updates["database_dsn"] = selected_database_dsn
    if "data_retention_days" in payload:
        updates["data_retention_days"] = _retention_days(payload.get("data_retention_days"))
    if "status" in payload:
        updates["status"] = _status(payload.get("status"))
    if "note" in payload:
        updates["note"] = str(payload.get("note") or "").strip()
    await db.client_sites.update_one({"_id": site_id}, {"$set": updates})
    return await get_client_site(db, site_id) or {}


async def delete_client_site(db: AsyncIOMotorDatabase, *, site_id: str, actor: dict[str, Any]) -> bool:
    now = now_utc()
    result = await db.client_sites.update_one(
        {"_id": site_id, "status": {"$ne": "deleted"}},
        {
            "$set": {
                "status": "deleted",
                "deleted_at": now,
                "updated_at": now,
                "updated_by": actor.get("_id"),
                "updated_by_name": actor.get("name") or actor.get("email") or actor.get("_id"),
            }
        },
    )
    return result.modified_count > 0


async def migrate_legacy_client_sites(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    migrated = 0
    async for legacy in db.sub2api_sites.find({"site_type": "newapi", "status": {"$ne": "deleted"}}):
        site_id = str(legacy.get("_id") or "").strip()
        if not site_id:
            continue
        now = now_utc()
        client_doc = {
            "_id": site_id,
            "name": str(legacy.get("name") or site_id),
            "client_type": "newapi",
            "base_url": str(legacy.get("base_url") or "").rstrip("/"),
            "api_key": str(legacy.get("token") or ""),
            "admin_user_id": str(legacy.get("admin_user_id") or ""),
            "data_retention_days": DEFAULT_DATA_RETENTION_DAYS,
            "status": str(legacy.get("status") or "active"),
            "note": "Migrated from legacy mixed site configuration",
            "created_by": legacy.get("created_by") or "system",
            "created_by_name": legacy.get("created_by_name") or "system",
            "updated_by": "system",
            "updated_by_name": "system",
            "created_at": legacy.get("created_at") or now,
            "updated_at": now,
            "migrated_from": "sub2api_sites",
        }
        await db.client_sites.update_one({"_id": site_id}, {"$setOnInsert": client_doc}, upsert=True)
        await db.sub2api_sites.update_one(
            {"_id": site_id},
            {
                "$set": {
                    "status": "deleted",
                    "migrated_to_client_site_id": site_id,
                    "migrated_at": now,
                    "updated_at": now,
                }
            },
        )
        migrated += 1
    return {"ok": True, "migrated": migrated}


def _client_type(value: Any) -> str:
    normalized = str(value or "sub2api").strip().lower()
    if normalized not in CLIENT_SITE_TYPES:
        raise ValueError(f"unsupported client_type: {normalized}")
    return normalized


def _http_url(value: Any, field_name: str) -> str:
    normalized = str(value or "").strip().rstrip("/")
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an http or https URL")
    return normalized


def _database_dsn(value: Any, client_type: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    parsed = urlparse(normalized)
    required_scheme = DATABASE_SCHEME_BY_CLIENT_TYPE[client_type]
    if parsed.scheme.lower() != required_scheme:
        raise ValueError(f"{client_type} client sites require a {required_scheme} database DSN")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("database DSN contains an invalid port") from exc
    if not parsed.hostname or parsed.username is None or parsed.password is None or not parsed.path.strip("/"):
        raise ValueError("database DSN must include username, password, host, and database name")
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("database DSN contains an invalid port")
    return normalized


def _database_endpoint(database_dsn: str) -> str:
    parsed = urlparse(database_dsn)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = parsed.port or (3306 if parsed.scheme == "mysql" else 5432)
    database_name = unquote(parsed.path.strip("/"))
    return f"{host}:{port}/{database_name}"


def _retention_days(value: Any) -> int:
    if value in (None, ""):
        return DEFAULT_DATA_RETENTION_DAYS
    if isinstance(value, bool):
        raise ValueError("data_retention_days must be an integer")
    try:
        days = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("data_retention_days must be an integer") from exc
    if not 1 <= days <= MAX_DATA_RETENTION_DAYS:
        raise ValueError(f"data_retention_days must be between 1 and {MAX_DATA_RETENTION_DAYS}")
    return days


def _status(value: Any) -> str:
    normalized = str(value or "active").strip().lower()
    if normalized not in {"active", "disabled"}:
        raise ValueError(f"unsupported client site status: {normalized}")
    return normalized
