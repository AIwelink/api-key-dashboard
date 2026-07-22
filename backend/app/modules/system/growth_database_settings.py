from __future__ import annotations

from typing import Any, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import create_async_engine

from app.modules.growth.database import create_growth_engine
from app.modules.growth.migrations import inspect_growth_database, run_growth_migrations
from app.modules.system.client_site_database import probe_sql_database_connection
from app.modules.system.sql_dsn import parse_sql_dsn, redact_sql_error
from app.utils import now_utc


SETTINGS_ID = "growth_database"
DATABASE_TYPE = "postgresql"


class GrowthDatabaseOperationError(RuntimeError):
    pass


def _default_private_settings() -> dict[str, Any]:
    return {
        "_id": SETTINGS_ID,
        "database_type": DATABASE_TYPE,
        "sql_dsn": "",
        "database_endpoint": "",
        "last_database_test_at": None,
        "last_database_test_ok": None,
        "last_database_test_error": "",
        "last_database_latency_ms": None,
        "last_database_version": "",
    }


def _public_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    private = {**_default_private_settings(), **(settings or {})}
    return {
        "database_type": DATABASE_TYPE,
        "sql_dsn_configured": bool(str(private.get("sql_dsn") or "").strip()),
        "database_endpoint": str(private.get("database_endpoint") or ""),
        "last_database_test_at": private.get("last_database_test_at"),
        "last_database_test_ok": private.get("last_database_test_ok"),
        "last_database_test_error": str(private.get("last_database_test_error") or ""),
        "last_database_latency_ms": private.get("last_database_latency_ms"),
        "last_database_version": str(private.get("last_database_version") or ""),
    }


async def get_growth_database_settings(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    return _public_settings(await db.app_settings.find_one({"_id": SETTINGS_ID}))


async def get_growth_database_settings_private(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    stored = await db.app_settings.find_one({"_id": SETTINGS_ID})
    return {**_default_private_settings(), **(stored or {})}


async def update_growth_database_settings(
    db: AsyncIOMotorDatabase,
    *,
    sql_dsn: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    current = await get_growth_database_settings_private(db)
    incoming_dsn = str(sql_dsn or "").strip()
    selected_dsn = incoming_dsn or str(current.get("sql_dsn") or "").strip()
    if not selected_dsn:
        raise ValueError("PostgreSQL SQL_DSN is required")
    parsed = parse_sql_dsn(selected_dsn, DATABASE_TYPE)
    updates: dict[str, Any] = {
        "database_type": DATABASE_TYPE,
        "database_endpoint": parsed.endpoint,
        "updated_at": now_utc(),
        "updated_by": _actor_id(actor),
    }
    if incoming_dsn:
        updates["sql_dsn"] = incoming_dsn
    await db.app_settings.update_one(
        {"_id": SETTINGS_ID},
        {"$set": updates},
        upsert=True,
    )
    return _public_settings({**current, **updates})


async def run_growth_database_test(
    db: AsyncIOMotorDatabase,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    current = await get_growth_database_settings_private(db)
    sql_dsn = str(current.get("sql_dsn") or "").strip()
    if not sql_dsn:
        raise ValueError("PostgreSQL SQL_DSN is not configured")
    result = await probe_sql_database_connection(
        sql_dsn,
        DATABASE_TYPE,
        engine_factory=engine_factory,
    )
    updates = {
        "database_type": DATABASE_TYPE,
        "database_endpoint": result["database_endpoint"],
        "last_database_test_at": result["tested_at"],
        "last_database_test_ok": result["ok"],
        "last_database_test_error": str(result.get("error") or ""),
        "last_database_latency_ms": result["latency_ms"],
        "last_database_version": str(result.get("server_version") or ""),
    }
    await db.app_settings.update_one(
        {"_id": SETTINGS_ID},
        {"$set": updates},
        upsert=True,
    )
    return {**result, "settings": _public_settings({**current, **updates})}


async def get_growth_schema_status(
    db: AsyncIOMotorDatabase,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    current = await get_growth_database_settings_private(db)
    sql_dsn = str(current.get("sql_dsn") or "").strip()
    if not sql_dsn:
        raise ValueError("PostgreSQL SQL_DSN is not configured")
    engine = create_growth_engine(sql_dsn, engine_factory=engine_factory)
    try:
        return await inspect_growth_database(engine)
    except Exception as exc:  # noqa: BLE001 - normalize and redact infrastructure failures.
        raise GrowthDatabaseOperationError(redact_sql_error(exc, sql_dsn, DATABASE_TYPE)) from exc
    finally:
        await engine.dispose()


async def initialize_growth_database(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    current = await get_growth_database_settings_private(db)
    sql_dsn = str(current.get("sql_dsn") or "").strip()
    if not sql_dsn:
        raise ValueError("PostgreSQL SQL_DSN is not configured")
    engine = create_growth_engine(sql_dsn, engine_factory=engine_factory)
    try:
        result = await run_growth_migrations(engine)
    except Exception as exc:  # noqa: BLE001 - normalize and redact infrastructure failures.
        raise GrowthDatabaseOperationError(redact_sql_error(exc, sql_dsn, DATABASE_TYPE)) from exc
    finally:
        await engine.dispose()

    await db.app_settings.update_one(
        {"_id": SETTINGS_ID},
        {
            "$set": {
                "last_schema_version": result.get("current_version"),
                "last_schema_initialized_at": now_utc(),
                "last_schema_initialized_by": _actor_id(actor),
            }
        },
        upsert=True,
    )
    return result


def _actor_id(actor: dict[str, Any]) -> str:
    return str(actor.get("_id") or actor.get("email") or actor.get("id") or "")
