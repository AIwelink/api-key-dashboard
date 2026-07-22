from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any, Callable

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.system.sql_dsn import parse_sql_dsn


GROWTH_DATABASE_TYPE = "postgresql"
GROWTH_CONNECT_TIMEOUT_SECONDS = 10


def create_growth_engine(
    sql_dsn: str,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> Any:
    parsed = parse_sql_dsn(sql_dsn, GROWTH_DATABASE_TYPE)
    return engine_factory(
        parsed.driver_url(),
        poolclass=NullPool,
        connect_args=parsed.connect_args(GROWTH_CONNECT_TIMEOUT_SECONDS),
    )


@asynccontextmanager
async def growth_connection(
    mongo_db: Any,
    *,
    write: bool = False,
    engine_factory: Callable[..., Any] = create_async_engine,
):
    settings = await mongo_db.app_settings.find_one({"_id": "growth_database"})
    sql_dsn = str((settings or {}).get("sql_dsn") or "").strip()
    if not sql_dsn:
        raise ValueError("PostgreSQL SQL_DSN is not configured")
    engine = create_growth_engine(sql_dsn, engine_factory=engine_factory)
    try:
        context = engine.begin() if write else engine.connect()
        async with context as connection:
            yield connection
    finally:
        await engine.dispose()
