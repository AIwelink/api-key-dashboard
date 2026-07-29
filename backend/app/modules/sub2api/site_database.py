from __future__ import annotations

from typing import Any, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import create_async_engine

from app.modules.sub2api.cache import get_site
from app.modules.system.client_site_database import probe_database_connection


async def run_sub2api_site_database_test(
    db: AsyncIOMotorDatabase,
    site_id: str,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    site = await get_site(db, site_id, include_token=True)
    if site is None:
        raise LookupError("sub2api site not found")
    if not str(site.get("sql_dsn") or "").strip():
        raise ValueError("SQL_DSN is not configured")
    result = await probe_database_connection(
        site | {"client_type": "sub2api"},
        engine_factory=engine_factory,
    )
    updates = {
        "last_database_test_at": result["tested_at"],
        "last_database_test_ok": result["ok"],
        "last_database_test_error": str(result.get("error") or ""),
        "last_database_latency_ms": result["latency_ms"],
        "last_database_version": str(result.get("server_version") or ""),
    }
    await db.sub2api_sites.update_one({"_id": site_id}, {"$set": updates})
    return result
