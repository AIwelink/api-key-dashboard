from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, quote_plus, unquote, urlsplit

from sqlalchemy.engine import URL


MYSQL_SQL_DSN_PATTERN = re.compile(
    r"^(?P<username>[^:]+):(?P<password>.*?)@tcp\((?P<address>[^)]+)\)/(?P<database>[^?\s]+)(?:\?(?P<query>.*))?$"
)
POSTGRES_SSL_MODES = {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}


@dataclass(frozen=True)
class ParsedSqlDsn:
    database_type: str
    username: str
    password: str
    host: str
    port: int
    database: str
    options: dict[str, str] = field(default_factory=dict)

    @property
    def endpoint(self) -> str:
        host = f"[{self.host}]" if ":" in self.host and not self.host.startswith("[") else self.host
        return f"{host}:{self.port}/{self.database}"

    def driver_url(self) -> str:
        driver = "mysql+aiomysql" if self.database_type == "mysql" else "postgresql+asyncpg"
        default_port = 3306 if self.database_type == "mysql" else 5432
        query = {}
        if self.database_type == "mysql" and self.options.get("charset"):
            query["charset"] = self.options["charset"]
        url = URL.create(
            driver,
            username=self.username,
            password=self.password,
            host=self.host,
            port=None if self.port == default_port else self.port,
            database=self.database,
            query=query,
        )
        return url.render_as_string(hide_password=False)

    def connect_args(self, timeout_seconds: int) -> dict[str, Any]:
        if self.database_type == "mysql":
            return {"connect_timeout": timeout_seconds}
        sslmode = self.options.get("sslmode", "prefer")
        return {
            "timeout": timeout_seconds,
            "ssl": False if sslmode == "disable" else sslmode,
        }


def parse_sql_dsn(value: Any, database_type: str) -> ParsedSqlDsn:
    sql_dsn = str(value or "").strip()
    if not sql_dsn:
        raise ValueError("SQL_DSN is not configured")
    normalized_type = str(database_type or "").strip().lower()
    if _looks_like_database_env(sql_dsn):
        return _parse_database_env(sql_dsn, normalized_type)
    if normalized_type == "mysql":
        return _parse_mysql_sql_dsn(sql_dsn)
    if normalized_type == "postgresql":
        return _parse_postgresql_sql_dsn(sql_dsn)
    raise ValueError(f"unsupported database type: {normalized_type}")


def validate_optional_sql_dsn(value: Any, database_type: str) -> str:
    sql_dsn = str(value or "").strip()
    if not sql_dsn:
        return ""
    parse_sql_dsn(sql_dsn, database_type)
    return sql_dsn


def sql_dsn_endpoint(value: Any, database_type: str) -> str:
    return parse_sql_dsn(value, database_type).endpoint


def redact_sql_error(exc: Exception, sql_dsn: str, database_type: str, *, max_length: int = 500) -> str:
    message = str(exc).strip() or exc.__class__.__name__
    secrets = {sql_dsn}
    try:
        parsed = parse_sql_dsn(sql_dsn, database_type)
        secrets.update(
            {
                parsed.username,
                parsed.password,
                quote(parsed.password, safe=""),
                quote_plus(parsed.password),
                parsed.driver_url(),
            }
        )
    except ValueError:
        pass
    for secret in sorted((item for item in secrets if item), key=len, reverse=True):
        message = message.replace(secret, "***")
    message = re.sub(r"[^\s]+@tcp\([^)]+\)/[^\s]+", "<sql-dsn>", message, flags=re.I)
    return message[:max_length]


def _parse_mysql_sql_dsn(sql_dsn: str) -> ParsedSqlDsn:
    match = MYSQL_SQL_DSN_PATTERN.fullmatch(sql_dsn)
    if match is None:
        raise ValueError("MySQL SQL_DSN must use user:password@tcp(host:3306)/database format")
    username = match.group("username").strip()
    password = match.group("password")
    database = unquote(match.group("database").strip("/"))
    address = urlsplit(f"//{match.group('address')}")
    try:
        port = address.port or 3306
    except ValueError as exc:
        raise ValueError("MySQL SQL_DSN contains an invalid port") from exc
    if not username or not password or not address.hostname or not database:
        raise ValueError("MySQL SQL_DSN must include user, password, host, and database")
    return ParsedSqlDsn(
        database_type="mysql",
        username=username,
        password=password,
        host=address.hostname,
        port=_valid_port(port, "MySQL"),
        database=database,
        options=dict(parse_qsl(match.group("query") or "", keep_blank_values=True)),
    )


def _parse_postgresql_sql_dsn(sql_dsn: str) -> ParsedSqlDsn:
    try:
        tokens = shlex.split(sql_dsn)
    except ValueError as exc:
        raise ValueError("PostgreSQL SQL_DSN contains invalid quoting") from exc
    options: dict[str, str] = {}
    for token in tokens:
        if "=" not in token:
            raise ValueError("PostgreSQL SQL_DSN must use key=value fields")
        key, value = token.split("=", 1)
        options[key.strip().lower()] = value.strip()
    database = options.get("dbname") or options.get("database") or ""
    required = {
        "host": options.get("host", ""),
        "user": options.get("user", ""),
        "password": options.get("password", ""),
        "dbname": database,
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ValueError(f"PostgreSQL SQL_DSN is missing: {', '.join(missing)}")
    try:
        port = int(options.get("port") or 5432)
    except ValueError as exc:
        raise ValueError("PostgreSQL SQL_DSN contains an invalid port") from exc
    sslmode = options.get("sslmode", "prefer").lower()
    if sslmode not in POSTGRES_SSL_MODES:
        raise ValueError(f"unsupported PostgreSQL sslmode: {sslmode}")
    options["sslmode"] = sslmode
    return ParsedSqlDsn(
        database_type="postgresql",
        username=required["user"],
        password=required["password"],
        host=required["host"],
        port=_valid_port(port, "PostgreSQL"),
        database=database,
        options=options,
    )


def _looks_like_database_env(value: str) -> bool:
    return any(
        line.strip().removeprefix("export ").startswith("DATABASE_")
        for line in value.splitlines()
    )


def _parse_database_env(value: str, database_type: str) -> ParsedSqlDsn:
    if database_type not in {"mysql", "postgresql"}:
        raise ValueError(f"unsupported database type: {database_type}")
    environment: dict[str, str] = {}
    for raw_line in value.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        line = line.removeprefix("export ").strip()
        if "=" not in line:
            raise ValueError(f"invalid DATABASE_* line: {line}")
        key, raw_value = line.split("=", 1)
        key = key.strip().upper()
        if not key.startswith("DATABASE_"):
            continue
        environment[key] = _unquote_env_value(raw_value.strip())
    field_map = {
        "host": "DATABASE_HOST",
        "database": "DATABASE_DBNAME",
        "username": "DATABASE_USER",
        "password": "DATABASE_PASSWORD",
    }
    missing = [env_key for env_key in field_map.values() if not environment.get(env_key)]
    if missing:
        raise ValueError(f"database environment block is missing: {', '.join(missing)}")
    default_port = 3306 if database_type == "mysql" else 5432
    try:
        port = int(environment.get("DATABASE_PORT") or default_port)
    except ValueError as exc:
        raise ValueError("DATABASE_PORT must be an integer") from exc
    options: dict[str, str] = {}
    if database_type == "mysql" and environment.get("DATABASE_CHARSET"):
        options["charset"] = environment["DATABASE_CHARSET"]
    if database_type == "postgresql":
        sslmode = (environment.get("DATABASE_SSLMODE") or "prefer").lower()
        if sslmode not in POSTGRES_SSL_MODES:
            raise ValueError(f"unsupported PostgreSQL sslmode: {sslmode}")
        options["sslmode"] = sslmode
    return ParsedSqlDsn(
        database_type=database_type,
        username=environment[field_map["username"]],
        password=environment[field_map["password"]],
        host=environment[field_map["host"]],
        port=_valid_port(port, "MySQL" if database_type == "mysql" else "PostgreSQL"),
        database=environment[field_map["database"]],
        options=options,
    )


def _unquote_env_value(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _valid_port(port: int, database_name: str) -> int:
    if not 1 <= port <= 65535:
        raise ValueError(f"{database_name} SQL_DSN contains an invalid port")
    return port
