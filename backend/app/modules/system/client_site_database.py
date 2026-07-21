from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.system.client_sites import DATABASE_TYPE_BY_CLIENT_TYPE, _client_type
from app.modules.system.sql_dsn import parse_sql_dsn, redact_sql_error
from app.utils import now_utc


DATABASE_CONNECTION_TIMEOUT_SECONDS = 10


def driver_database_url(sql_dsn: str, client_type: str) -> str:
    normalized_type = _client_type(client_type)
    return parse_sql_dsn(sql_dsn, DATABASE_TYPE_BY_CLIENT_TYPE[normalized_type]).driver_url()


async def probe_database_connection(
    site: dict[str, Any],
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    client_type = _client_type(site.get("client_type"))
    database_type = DATABASE_TYPE_BY_CLIENT_TYPE[client_type]
    sql_dsn = str(site.get("sql_dsn") or "").strip()
    return await probe_sql_database_connection(
        sql_dsn,
        database_type,
        engine_factory=engine_factory,
    )


async def probe_sql_database_connection(
    sql_dsn: str,
    database_type: str,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    parsed_dsn = parse_sql_dsn(sql_dsn, database_type)
    engine = None
    started = perf_counter()
    tested_at = now_utc()
    try:
        engine = engine_factory(
            parsed_dsn.driver_url(),
            poolclass=NullPool,
            connect_args=parsed_dsn.connect_args(DATABASE_CONNECTION_TIMEOUT_SECONDS),
        )
        async with asyncio.timeout(DATABASE_CONNECTION_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                version_result = await connection.execute(text("SELECT VERSION()"))
                server_version = str(version_result.scalar_one_or_none() or "")
        return {
            "ok": True,
            "database_type": database_type,
            "database_endpoint": parsed_dsn.endpoint,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "server_version": server_version,
            "tested_at": tested_at,
        }
    except Exception as exc:  # noqa: BLE001 - the caller needs a persisted, redacted connection result.
        return {
            "ok": False,
            "database_type": database_type,
            "database_endpoint": parsed_dsn.endpoint,
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "error": redact_sql_error(exc, sql_dsn, database_type),
            "tested_at": tested_at,
        }
    finally:
        if engine is not None:
            await engine.dispose()


async def run_client_site_database_test(
    db: AsyncIOMotorDatabase,
    site_id: str,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    site = await db.client_sites.find_one({"_id": site_id, "status": {"$ne": "deleted"}})
    if site is None:
        raise LookupError("client site not found")
    if not str(site.get("sql_dsn") or "").strip():
        raise ValueError("SQL_DSN is not configured")
    result = await probe_database_connection(site, engine_factory=engine_factory)
    updates = {
        "last_database_test_at": result["tested_at"],
        "last_database_test_ok": result["ok"],
        "last_database_test_error": str(result.get("error") or ""),
        "last_database_latency_ms": result["latency_ms"],
        "last_database_version": str(result.get("server_version") or ""),
    }
    await db.client_sites.update_one({"_id": site_id}, {"$set": updates})
    return result
