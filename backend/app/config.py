from functools import lru_cache
from pathlib import Path
from urllib.parse import parse_qsl, quote_plus, urlencode, urlsplit, urlunsplit

from pydantic_settings import BaseSettings, SettingsConfigDict


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    app_name: str = "API Key Admin Backend"
    app_env: str = "development"
    app_secret_key: str = "change-me-in-production"
    backend_host: str = "127.0.0.1"
    backend_port: int = 8000
    frontend_origin: str = "http://127.0.0.1:5173"

    mongodb_uri: str | None = None
    mongodb_host: str = "localhost"
    mongodb_port: int = 27017
    mongodb_user: str | None = None
    mongodb_password: str | None = None
    mongodb_db: str = "api_key_admin"

    access_token_expire_minutes: int = 10080

    initial_owner_email: str | None = None
    initial_owner_name: str = "Admin"
    initial_owner_password: str | None = None

    log_profile: str = "development"
    log_level: str = "DEBUG"
    log_dir: str = "logs"
    log_retention_days: int = 14
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 10
    log_request_body: bool = False
    log_slow_request_ms: int = 1000

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def get_mongodb_uri() -> str:
    settings = get_settings()
    if settings.mongodb_uri:
        return _with_retry_writes_disabled(settings.mongodb_uri)
    if settings.mongodb_user and settings.mongodb_password:
        user = quote_plus(settings.mongodb_user)
        password = quote_plus(settings.mongodb_password)
        db_name = quote_plus(settings.mongodb_db)
        return f"mongodb://{user}:{password}@{settings.mongodb_host}:{settings.mongodb_port}/{db_name}?retryWrites=false"
    return f"mongodb://{settings.mongodb_host}:{settings.mongodb_port}/{quote_plus(settings.mongodb_db)}?retryWrites=false"


def _with_retry_writes_disabled(uri: str) -> str:
    parts = urlsplit(uri)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["retryWrites"] = "false"
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
