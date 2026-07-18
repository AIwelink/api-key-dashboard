from __future__ import annotations

import asyncio
import re
from time import perf_counter
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.system.client_sites import _client_type, _database_dsn, _database_endpoint
from app.utils import now_utc


DATABASE_DRIVER_BY_CLIENT_TYPE = {
    "newapi": "mysql+aiomysql",
    "sub2api": "postgresql+asyncpg",
}
DATABASE_CONNECTION_TIMEOUT_SECONDS = 10
MAX_DATABASE_ERROR_LENGTH = 500


def driver_database_url(database_dsn: str, client_type: str) -> str:
    normalized_type = _client_type(client_type)
    normalized_dsn = _database_dsn(database_dsn, normalized_type)
    if not normalized_dsn:
        raise ValueError("database DSN is not configured")
    parsed = urlparse(normalized_dsn)
    driver_scheme = DATABASE_DRIVER_BY_CLIENT_TYPE[normalized_type]
    return normalized_dsn.replace(f"{parsed.scheme}://", f"{driver_scheme}://", 1)


async def probe_database_connection(
    site: dict[str, Any],
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    client_type = _client_type(site.get("client_type"))
    database_dsn = _database_dsn(site.get("database_dsn"), client_type)
    if not database_dsn:
        raise ValueError("database DSN is not configured")
    database_type = "mysql" if client_type == "newapi" else "postgresql"
    engine = None
    started = perf_counter()
    tested_at = now_utc()
    try:
        engine = engine_factory(
            driver_database_url(database_dsn, client_type),
            poolclass=NullPool,
            connect_args=_connect_args(client_type),
        )
        async with asyncio.timeout(DATABASE_CONNECTION_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
                version_result = await connection.execute(text("SELECT VERSION()"))
                server_version = str(version_result.scalar_one_or_none() or "")
        return {
            "ok": True,
            "database_type": database_type,
            "database_endpoint": _database_endpoint(database_dsn),
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "server_version": server_version,
            "tested_at": tested_at,
        }
    except Exception as exc:  # noqa: BLE001 - the caller needs a persisted, redacted connection result.
        return {
            "ok": False,
            "database_type": database_type,
            "database_endpoint": _database_endpoint(database_dsn),
            "latency_ms": round((perf_counter() - started) * 1000, 2),
            "error": _redact_database_error(exc, database_dsn),
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
    if not str(site.get("database_dsn") or "").strip():
        raise ValueError("database DSN is not configured")
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


def _connect_args(client_type: str) -> dict[str, Any]:
    if client_type == "newapi":
        return {"connect_timeout": DATABASE_CONNECTION_TIMEOUT_SECONDS}
    return {"timeout": DATABASE_CONNECTION_TIMEOUT_SECONDS}


def _redact_database_error(exc: Exception, database_dsn: str) -> str:
    parsed = urlparse(database_dsn)
    message = str(exc).strip() or exc.__class__.__name__
    secrets = {
        database_dsn,
        parsed.username or "",
        parsed.password or "",
        unquote(parsed.username or ""),
        unquote(parsed.password or ""),
    }
    for secret in sorted((value for value in secrets if value), key=len, reverse=True):
        message = message.replace(secret, "***")
    message = re.sub(r"(?:mysql|postgres(?:ql)?)(?:\+[a-z0-9_]+)?://[^\s]+", "<database-dsn>", message, flags=re.I)
    return message[:MAX_DATABASE_ERROR_LENGTH]
