from __future__ import annotations

import asyncio
import inspect
import logging
from contextlib import asynccontextmanager
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.operations import repository
from app.modules.operations.adapters.base import OperationsSourceAdapter, UsageFactInput
from app.modules.operations.adapters.newapi import NewApiOperationsAdapter
from app.modules.operations.adapters.sub2api import Sub2ApiOperationsAdapter
from app.modules.operations.cache import operations_response_cache
from app.modules.operations.domain import convert_balance_to_cny
from app.modules.growth.database import growth_connection
from app.modules.system.client_sites import get_client_site, list_client_sites
from app.modules.system.sql_dsn import parse_sql_dsn, redact_sql_error


OPERATIONS_SYNC_INTERVAL_SECONDS = 900
OPERATIONS_RECONCILIATION_WINDOW = timedelta(hours=48)
OPERATIONS_INITIAL_SYNC_WINDOW = timedelta(days=30)
SOURCE_DATABASE_TIMEOUT_SECONDS = 30
NEWAPI_DEFAULT_QUOTA_PER_UNIT = Decimal("500000")
logger = logging.getLogger("app.operations.sync")

_refresh_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
_refresh_lock = asyncio.Lock()


def reconciliation_start(
    *,
    now: datetime,
    last_success_at: datetime | None,
    initial_sync_from: datetime | None = None,
) -> datetime:
    if last_success_at is not None:
        return last_success_at - OPERATIONS_RECONCILIATION_WINDOW
    if initial_sync_from is not None:
        return initial_sync_from
    return now - OPERATIONS_INITIAL_SYNC_WINDOW


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def apply_usage_conversion_rates(
    facts: list[UsageFactInput],
    rates: list[dict[str, Any]],
) -> list[UsageFactInput]:
    normalized_rates = []
    for rate in rates:
        effective_from = _datetime(rate.get("effective_from"))
        if effective_from is None:
            continue
        normalized_rates.append(
            (
                effective_from,
                _datetime(rate.get("effective_until")),
                UUID(str(rate["conversion_rate_id"])),
                Decimal(str(rate["balance_units_per_cny"])),
            )
        )
    normalized_rates.sort(key=lambda item: item[0], reverse=True)

    converted = []
    for fact in facts:
        selected = next(
            (
                rate
                for rate in normalized_rates
                if rate[0] <= fact.occurred_at and (rate[1] is None or fact.occurred_at < rate[1])
            ),
            None,
        )
        if selected is None:
            raise ValueError(
                f"no balance conversion rate covers {fact.site_id} usage at {fact.occurred_at.isoformat()}"
            )
        converted.append(
            replace(
                fact,
                conversion_rate_id=selected[2],
                cost_cny=convert_balance_to_cny(fact.consumed_balance_units, selected[3]),
            )
        )
    return converted


async def sync_adapter_records(
    *,
    adapter: OperationsSourceAdapter,
    source_connection: Any,
    growth_connection: Any,
    since: datetime,
    now: datetime,
) -> dict[str, int]:
    await repository.acquire_operations_sync_lock(
        growth_connection,
        site_id=adapter.site_id,
    )
    users = await adapter.read_users(connection=source_connection, since=since)
    usage = await adapter.read_usage(connection=source_connection, since=since)
    credits = await adapter.read_credit_events(connection=source_connection, since=since)
    rates = await repository.list_conversion_rates(
        growth_connection,
        allowed_site_ids=(adapter.site_id,),
    )
    converted_usage = apply_usage_conversion_rates(usage, rates)

    user_count = await repository.upsert_user_snapshots(growth_connection, users)
    usage_count = await repository.upsert_usage_facts(growth_connection, converted_usage)
    credit_count = await repository.upsert_credit_events(growth_connection, credits)
    task_count = await repository.create_pending_classification_tasks(
        growth_connection,
        site_id=adapter.site_id,
    )
    await repository.replace_affected_aggregates(
        growth_connection,
        site_id=adapter.site_id,
        start_at=since,
        end_at=now,
    )
    operations_response_cache.invalidate(site_id=adapter.site_id)
    return {
        "users": user_count,
        "usage": usage_count,
        "credits": credit_count,
        "classification_tasks": task_count,
        "rows_scanned": len(users) + len(usage) + len(credits),
        "rows_upserted": user_count + usage_count + credit_count,
    }


async def request_operations_refresh(
    mongo_db: Any,
    *,
    site_id: str,
    sync_func: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    selected_sync = sync_func or sync_site_operations
    async with _refresh_lock:
        task = _refresh_tasks.get(site_id)
        if task is None or task.done():
            task = asyncio.create_task(
                selected_sync(mongo_db, site_id=site_id, trigger_type="manual")
            )
            _refresh_tasks[site_id] = task
    try:
        return await asyncio.shield(task)
    finally:
        if task.done():
            async with _refresh_lock:
                if _refresh_tasks.get(site_id) is task:
                    _refresh_tasks.pop(site_id, None)


async def clear_refresh_tasks() -> None:
    async with _refresh_lock:
        tasks = list(_refresh_tasks.values())
        _refresh_tasks.clear()
    for task in tasks:
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass


async def run_operations_sync_cycle(
    mongo_db: Any,
    *,
    sites: list[dict[str, Any]] | None = None,
    sync_func: Callable[..., Awaitable[dict[str, Any]]] | None = None,
) -> list[dict[str, Any]]:
    selected_sync = sync_func or sync_site_operations
    if sites is None:
        site_result = await list_client_sites(mongo_db)
        sites = [site for site in site_result["items"] if site.get("status") == "active"]
    results = []
    for site in sites:
        site_id = str(site.get("id") or site.get("_id") or "")
        if not site_id:
            continue
        try:
            result = await selected_sync(
                mongo_db,
                site_id=site_id,
                trigger_type="schedule",
            )
            results.append({"site_id": site_id, "status": "succeeded", "result": result})
        except Exception as exc:  # One source outage must not stop other sites.
            logger.exception("operations_sync_site_failed site_id=%s", site_id)
            results.append(
                {
                    "site_id": site_id,
                    "status": "failed",
                    "error": str(exc)[:500],
                }
            )
    return results


async def operations_sync_loop(
    mongo_db: Any,
    *,
    cycle_func: Callable[..., Awaitable[list[dict[str, Any]]]] = run_operations_sync_cycle,
    sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    while True:
        await cycle_func(mongo_db)
        await sleep_func(OPERATIONS_SYNC_INTERVAL_SECONDS)


async def sync_site_operations(
    mongo_db: Any,
    *,
    site_id: str,
    trigger_type: str,
    now: datetime | None = None,
    site_loader: Callable[..., Awaitable[dict[str, Any] | None]] = get_client_site,
    adapter_factory: Callable[[dict[str, Any]], Any] | None = None,
    source_connection_factory: Callable[[dict[str, Any]], Any] | None = None,
    growth_connection_factory: Callable[..., Any] = growth_connection,
    records_sync: Callable[..., Awaitable[dict[str, int]]] = sync_adapter_records,
    cursor_loader: Callable[..., Awaitable[dict[str, Any]]] = repository.get_operations_sync_cursor,
    run_starter: Callable[..., Awaitable[dict[str, Any]]] = repository.start_operations_sync_run,
    run_finisher: Callable[..., Awaitable[dict[str, Any]]] = repository.finish_operations_sync_run,
) -> dict[str, Any]:
    current_time = now or datetime.now(UTC)
    site = await site_loader(mongo_db, site_id, include_api_key=True)
    if site is None:
        raise LookupError("client site not found")
    if not str(site.get("sql_dsn") or "").strip():
        raise ValueError("client site SQL_DSN is not configured")
    client_type = str(site.get("client_type") or "").strip().lower()
    selected_adapter_factory = adapter_factory or create_source_adapter
    adapter_result = selected_adapter_factory(site)
    adapter = await adapter_result if inspect.isawaitable(adapter_result) else adapter_result
    selected_source_factory = source_connection_factory or source_database_connection

    async with growth_connection_factory(mongo_db, write=True) as connection:
        cursor = await cursor_loader(connection, site_id=site_id)
        since = reconciliation_start(
            now=current_time,
            last_success_at=_datetime(cursor.get("last_success_at")),
            initial_sync_from=_datetime(site.get("initial_sync_from")),
        )
        started = await run_starter(
            connection,
            site_id=site_id,
            adapter_name=client_type,
            trigger_type=trigger_type,
            started_at=current_time,
        )
    run_id = UUID(str(started["run_id"]))

    try:
        async with selected_source_factory(site) as source:
            async with growth_connection_factory(mongo_db, write=True) as connection:
                counts = await records_sync(
                    adapter=adapter,
                    source_connection=source,
                    growth_connection=connection,
                    since=since,
                    now=current_time,
                )
    except Exception as exc:
        database_type = "mysql" if client_type == "newapi" else "postgresql"
        error_message = redact_sql_error(
            exc,
            str(site.get("sql_dsn") or ""),
            database_type,
        )
        async with growth_connection_factory(mongo_db, write=True) as connection:
            await run_finisher(
                connection,
                run_id=run_id,
                site_id=site_id,
                adapter_name=client_type,
                status="failed",
                finished_at=datetime.now(UTC) if now is None else current_time,
                rows_scanned=0,
                rows_upserted=0,
                error_code=exc.__class__.__name__,
                error_message=error_message,
            )
        raise

    finished_at = datetime.now(UTC) if now is None else current_time
    async with growth_connection_factory(mongo_db, write=True) as connection:
        await run_finisher(
            connection,
            run_id=run_id,
            site_id=site_id,
            adapter_name=client_type,
            status="succeeded",
            finished_at=finished_at,
            rows_scanned=counts.get("rows_scanned", 0),
            rows_upserted=counts.get("rows_upserted", 0),
        )
    return {
        "site_id": site_id,
        "run_id": str(run_id),
        "status": "succeeded",
        "since": since.isoformat(),
        "finished_at": finished_at.isoformat(),
        **counts,
    }


def create_source_adapter(site: dict[str, Any]) -> OperationsSourceAdapter:
    site_id = str(site.get("id") or site.get("_id") or "").strip()
    client_type = str(site.get("client_type") or "").strip().lower()
    if client_type == "sub2api":
        return Sub2ApiOperationsAdapter(site_id=site_id)
    if client_type == "newapi":
        quota_per_unit = Decimal(
            str(site.get("quota_per_unit") or NEWAPI_DEFAULT_QUOTA_PER_UNIT)
        )
        return NewApiOperationsAdapter(site_id=site_id, quota_per_unit=quota_per_unit)
    raise ValueError(f"unsupported operations source type: {client_type}")


@asynccontextmanager
async def source_database_connection(
    site: dict[str, Any],
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
):
    client_type = str(site.get("client_type") or "").strip().lower()
    database_type = "mysql" if client_type == "newapi" else "postgresql"
    parsed = parse_sql_dsn(site.get("sql_dsn"), database_type)
    engine = engine_factory(
        parsed.driver_url(),
        poolclass=NullPool,
        connect_args=parsed.connect_args(SOURCE_DATABASE_TIMEOUT_SECONDS),
    )
    try:
        async with engine.connect() as connection:
            read_only_sql = (
                "SET SESSION TRANSACTION READ ONLY"
                if database_type == "mysql"
                else "SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY"
            )
            await connection.execute(text(read_only_sql))
            yield connection
    finally:
        await engine.dispose()
