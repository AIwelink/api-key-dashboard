from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Iterable, Mapping

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.system.sql_dsn import parse_sql_dsn, redact_sql_error
from app.utils import now_utc


CatalogRow = Mapping[str, Any]
SCHEMA_SCAN_TIMEOUT_SECONDS = 30


MYSQL_CATALOG_QUERIES = (
    """
    SELECT table_schema AS schema_name, table_name, table_type
    FROM information_schema.tables
    WHERE table_schema = :database_name
      AND table_type IN ('BASE TABLE', 'VIEW')
    ORDER BY table_name
    """,
    """
    SELECT table_schema AS schema_name, table_name, column_name, ordinal_position,
           data_type, column_type AS native_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema = :database_name
    ORDER BY table_name, ordinal_position
    """,
    """
    SELECT table_schema AS schema_name, table_name, index_name,
           (non_unique = 0) AS is_unique, index_type AS index_method,
           column_name, seq_in_index AS ordinal_position
    FROM information_schema.statistics
    WHERE table_schema = :database_name
    ORDER BY table_name, index_name, seq_in_index
    """,
    """
    SELECT tc.table_schema AS schema_name, tc.table_name, tc.constraint_name,
           tc.constraint_type, kcu.column_name, kcu.ordinal_position,
           kcu.referenced_table_schema AS referenced_schema,
           kcu.referenced_table_name AS referenced_table,
           kcu.referenced_column_name AS referenced_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON kcu.constraint_schema = tc.constraint_schema
     AND kcu.table_name = tc.table_name
     AND kcu.constraint_name = tc.constraint_name
    WHERE tc.table_schema = :database_name
      AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')
    ORDER BY tc.table_name, tc.constraint_name, kcu.ordinal_position
    """,
)


POSTGRESQL_CATALOG_QUERIES = (
    """
    SELECT table_schema AS schema_name, table_name, table_type
    FROM information_schema.tables
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
      AND table_schema NOT LIKE 'pg_toast%'
      AND table_type IN ('BASE TABLE', 'VIEW')
    ORDER BY table_schema, table_name
    """,
    """
    SELECT table_schema AS schema_name, table_name, column_name, ordinal_position,
           data_type, udt_name AS native_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
      AND table_schema NOT LIKE 'pg_toast%'
    ORDER BY table_schema, table_name, ordinal_position
    """,
    """
    SELECT ns.nspname AS schema_name, tbl.relname AS table_name,
           idx.relname AS index_name, ind.indisunique AS is_unique,
           am.amname AS index_method, attr.attname AS column_name,
           keys.ordinality AS ordinal_position
    FROM pg_catalog.pg_index ind
    JOIN pg_catalog.pg_class idx ON idx.oid = ind.indexrelid
    JOIN pg_catalog.pg_class tbl ON tbl.oid = ind.indrelid
    JOIN pg_catalog.pg_namespace ns ON ns.oid = tbl.relnamespace
    JOIN pg_catalog.pg_am am ON am.oid = idx.relam
    JOIN LATERAL unnest(ind.indkey) WITH ORDINALITY AS keys(attnum, ordinality) ON TRUE
    LEFT JOIN pg_catalog.pg_attribute attr
      ON attr.attrelid = tbl.oid AND attr.attnum = keys.attnum
    WHERE ns.nspname NOT IN ('pg_catalog', 'information_schema')
      AND ns.nspname NOT LIKE 'pg_toast%'
    ORDER BY ns.nspname, tbl.relname, idx.relname, keys.ordinality
    """,
    """
    SELECT tc.table_schema AS schema_name, tc.table_name, tc.constraint_name,
           tc.constraint_type, kcu.column_name, kcu.ordinal_position,
           rcu.table_schema AS referenced_schema,
           rcu.table_name AS referenced_table,
           rcu.column_name AS referenced_column
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON kcu.constraint_schema = tc.constraint_schema
     AND kcu.table_schema = tc.table_schema
     AND kcu.table_name = tc.table_name
     AND kcu.constraint_name = tc.constraint_name
    LEFT JOIN information_schema.referential_constraints rc
      ON rc.constraint_schema = tc.constraint_schema
     AND rc.constraint_name = tc.constraint_name
    LEFT JOIN information_schema.key_column_usage rcu
      ON rcu.constraint_schema = rc.unique_constraint_schema
     AND rcu.constraint_name = rc.unique_constraint_name
     AND rcu.ordinal_position = kcu.position_in_unique_constraint
    WHERE tc.table_schema NOT IN ('pg_catalog', 'information_schema')
      AND tc.table_schema NOT LIKE 'pg_toast%'
      AND tc.constraint_type IN ('PRIMARY KEY', 'FOREIGN KEY')
    ORDER BY tc.table_schema, tc.table_name, tc.constraint_name, kcu.ordinal_position
    """,
)


async def inspect_database_schema(
    site: dict[str, Any],
    *,
    site_scope: str,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    client_type = "sub2api" if site_scope == "account_pool" else str(site.get("client_type") or "").strip().lower()
    database_type = "mysql" if client_type == "newapi" else "postgresql"
    sql_dsn = str(site.get("sql_dsn") or "").strip()
    parsed = parse_sql_dsn(sql_dsn, database_type)
    queries = MYSQL_CATALOG_QUERIES if database_type == "mysql" else POSTGRESQL_CATALOG_QUERIES
    parameters = {"database_name": parsed.database} if database_type == "mysql" else {}
    engine = None
    try:
        engine = engine_factory(
            parsed.driver_url(),
            poolclass=NullPool,
            connect_args=parsed.connect_args(SCHEMA_SCAN_TIMEOUT_SECONDS),
        )
        async with asyncio.timeout(SCHEMA_SCAN_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                row_sets = []
                for query in queries:
                    result = await connection.execute(text(query), parameters)
                    row_sets.append([dict(row) for row in result.mappings().all()])
        return {
            "ok": True,
            "site_id": str(site.get("_id") or ""),
            "site_scope": site_scope,
            "client_type": client_type,
            "database_type": database_type,
            "database_endpoint": parsed.endpoint,
            "scanned_at": now_utc(),
            "schemas": normalize_schema_catalog(
                database_type=database_type,
                table_rows=row_sets[0],
                column_rows=row_sets[1],
                index_rows=row_sets[2],
                constraint_rows=row_sets[3],
            ),
        }
    finally:
        if engine is not None:
            await engine.dispose()


async def inspect_all_configured_database_schemas(
    db: AsyncIOMotorDatabase,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, Any]:
    query = {
        "status": "active",
        "sql_dsn": {"$exists": True, "$nin": ["", None]},
    }
    account_projection = {"_id": 1, "sql_dsn": 1}
    client_projection = {"_id": 1, "client_type": 1, "sql_dsn": 1}
    configured: list[tuple[str, dict[str, Any]]] = [
        ("account_pool", site)
        async for site in db.sub2api_sites.find(query, account_projection)
    ]
    configured.extend(
        [("client", site) async for site in db.client_sites.find(query, client_projection)]
    )

    sites: list[dict[str, Any]] = []
    for site_scope, site in configured:
        try:
            sites.append(
                await inspect_database_schema(
                    site,
                    site_scope=site_scope,
                    engine_factory=engine_factory,
                )
            )
        except Exception as exc:  # noqa: BLE001 - one remote database must not block the rest.
            client_type = "sub2api" if site_scope == "account_pool" else str(site.get("client_type") or "").strip().lower()
            database_type = "mysql" if client_type == "newapi" else "postgresql"
            sql_dsn = str(site.get("sql_dsn") or "")
            endpoint = ""
            try:
                endpoint = parse_sql_dsn(sql_dsn, database_type).endpoint
            except ValueError:
                pass
            sites.append(
                {
                    "ok": False,
                    "site_id": str(site.get("_id") or ""),
                    "site_scope": site_scope,
                    "client_type": client_type,
                    "database_type": database_type,
                    "database_endpoint": endpoint,
                    "scanned_at": now_utc(),
                    "error": redact_sql_error(exc, sql_dsn, database_type),
                    "schemas": [],
                }
            )
    return {
        "scanned_at": now_utc(),
        "summary": {
            "sites": len(sites),
            "succeeded": sum(1 for site in sites if site.get("ok") is True),
            "failed": sum(1 for site in sites if site.get("ok") is False),
        },
        "sites": sites,
    }


def normalize_schema_catalog(
    *,
    database_type: str,
    table_rows: Iterable[CatalogRow],
    column_rows: Iterable[CatalogRow],
    index_rows: Iterable[CatalogRow],
    constraint_rows: Iterable[CatalogRow],
) -> list[dict[str, Any]]:
    del database_type
    table_rows = [_casefold_row(row) for row in table_rows]
    column_rows = [_casefold_row(row) for row in column_rows]
    index_rows = [_casefold_row(row) for row in index_rows]
    constraint_rows = [_casefold_row(row) for row in constraint_rows]
    tables: dict[tuple[str, str], dict[str, Any]] = {}

    def table_for(schema_name: Any, table_name: Any, table_type: Any = "BASE TABLE") -> dict[str, Any]:
        key = (str(schema_name or ""), str(table_name or ""))
        if key not in tables:
            tables[key] = {
                "name": key[1],
                "type": str(table_type or "BASE TABLE"),
                "columns": [],
                "primary_key": [],
                "foreign_keys": [],
                "indexes": [],
            }
        return tables[key]

    for row in table_rows:
        table_for(row.get("schema_name"), row.get("table_name"), row.get("table_type"))

    for row in sorted(column_rows, key=_row_order):
        table = table_for(row.get("schema_name"), row.get("table_name"))
        table["columns"].append(
            {
                "name": str(row.get("column_name") or ""),
                "ordinal_position": _integer(row.get("ordinal_position")),
                "data_type": str(row.get("data_type") or ""),
                "native_type": str(row.get("native_type") or row.get("data_type") or ""),
                "nullable": str(row.get("is_nullable") or "").upper() == "YES",
            }
        )

    indexes: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in sorted(index_rows, key=_row_order):
        key = (
            str(row.get("schema_name") or ""),
            str(row.get("table_name") or ""),
            str(row.get("index_name") or ""),
        )
        index = indexes.setdefault(
            key,
            {
                "name": key[2],
                "unique": _boolean(row.get("is_unique")),
                "method": str(row.get("index_method") or ""),
                "columns": [],
            },
        )
        column_name = str(row.get("column_name") or "")
        if column_name:
            index["columns"].append(column_name)
    for (schema_name, table_name, _), index in indexes.items():
        table_for(schema_name, table_name)["indexes"].append(index)

    primary_keys: dict[tuple[str, str, str], list[tuple[int, str]]] = defaultdict(list)
    foreign_keys: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in constraint_rows:
        schema_name = str(row.get("schema_name") or "")
        table_name = str(row.get("table_name") or "")
        constraint_name = str(row.get("constraint_name") or "")
        constraint_type = str(row.get("constraint_type") or "").upper()
        column_name = str(row.get("column_name") or "")
        position = _integer(row.get("ordinal_position"))
        key = (schema_name, table_name, constraint_name)
        if constraint_type == "PRIMARY KEY":
            primary_keys[key].append((position, column_name))
        elif constraint_type == "FOREIGN KEY":
            foreign_key = foreign_keys.setdefault(
                key,
                {
                    "name": constraint_name,
                    "columns": [],
                    "referenced_schema": str(row.get("referenced_schema") or ""),
                    "referenced_table": str(row.get("referenced_table") or ""),
                    "referenced_columns": [],
                    "_positions": [],
                },
            )
            foreign_key["_positions"].append(
                (position, column_name, str(row.get("referenced_column") or ""))
            )

    for (schema_name, table_name, _), columns in primary_keys.items():
        table_for(schema_name, table_name)["primary_key"] = [column for _, column in sorted(columns)]
    for (schema_name, table_name, _), foreign_key in foreign_keys.items():
        positions = sorted(foreign_key.pop("_positions"))
        foreign_key["columns"] = [column for _, column, _ in positions]
        foreign_key["referenced_columns"] = [column for _, _, column in positions]
        table_for(schema_name, table_name)["foreign_keys"].append(foreign_key)

    schemas: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (schema_name, _), table in tables.items():
        table["indexes"].sort(key=lambda item: item["name"])
        table["foreign_keys"].sort(key=lambda item: item["name"])
        schemas[schema_name].append(table)
    return [
        {
            "name": schema_name,
            "tables": sorted(schema_tables, key=lambda item: item["name"]),
        }
        for schema_name, schema_tables in sorted(schemas.items())
    ]


def _row_order(row: CatalogRow) -> tuple[str, str, str, int]:
    return (
        str(row.get("schema_name") or ""),
        str(row.get("table_name") or ""),
        str(row.get("index_name") or row.get("column_name") or ""),
        _integer(row.get("ordinal_position")),
    )


def _integer(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _boolean(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in {"true", "t", "yes", "y", "1"}


def _casefold_row(row: CatalogRow) -> dict[str, Any]:
    return {str(key).lower(): value for key, value in row.items()}
