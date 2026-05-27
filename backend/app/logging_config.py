from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import logging.config
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import PROJECT_ROOT, Settings


request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")

SENSITIVE_KEYS = {
    "access_token",
    "refresh_token",
    "id_token",
    "authorization",
    "password",
    "password_hash",
    "app_secret_key",
    "sub2api_token",
    "token",
    "credentials",
    "account_json",
}


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


def resolve_log_dir(settings: Settings) -> Path:
    path = Path(settings.log_dir)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    path.mkdir(parents=True, exist_ok=True)
    return path


def setup_logging(settings: Settings) -> None:
    log_dir = resolve_log_dir(settings)
    level = _resolve_level(settings.log_level)
    profile = settings.log_profile.lower()
    console_level = "DEBUG" if profile == "development" else "INFO"

    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_id": {"()": "app.logging_config.RequestIdFilter"},
            },
            "formatters": {
                "standard": {
                    "format": "%(asctime)s %(levelname)s [%(request_id)s] %(name)s: %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
                "access": {
                    "format": "%(asctime)s %(levelname)s [%(request_id)s] %(message)s",
                    "datefmt": "%Y-%m-%d %H:%M:%S",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "level": console_level,
                    "formatter": "standard",
                    "filters": ["request_id"],
                },
                "app_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": level,
                    "formatter": "standard",
                    "filters": ["request_id"],
                    "filename": str(log_dir / "app.log"),
                    "maxBytes": settings.log_max_bytes,
                    "backupCount": settings.log_backup_count,
                    "encoding": "utf-8",
                },
                "access_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": "INFO",
                    "formatter": "access",
                    "filters": ["request_id"],
                    "filename": str(log_dir / "access.log"),
                    "maxBytes": settings.log_max_bytes,
                    "backupCount": settings.log_backup_count,
                    "encoding": "utf-8",
                },
                "error_file": {
                    "class": "logging.handlers.RotatingFileHandler",
                    "level": "ERROR",
                    "formatter": "standard",
                    "filters": ["request_id"],
                    "filename": str(log_dir / "error.log"),
                    "maxBytes": settings.log_max_bytes,
                    "backupCount": settings.log_backup_count,
                    "encoding": "utf-8",
                },
            },
            "loggers": {
                "app": {
                    "level": level,
                    "handlers": ["console", "app_file", "error_file"],
                    "propagate": False,
                },
                "app.access": {
                    "level": "INFO",
                    "handlers": ["access_file"],
                    "propagate": profile == "development",
                },
                "uvicorn": {
                    "level": "INFO",
                    "handlers": ["console", "app_file", "error_file"],
                    "propagate": False,
                },
                "uvicorn.access": {
                    "level": "WARNING" if profile == "development" else "ERROR",
                    "handlers": ["access_file"],
                    "propagate": False,
                },
                "motor": {"level": "WARNING"},
                "pymongo": {"level": "WARNING"},
                "httpx": {"level": "INFO"},
                "httpcore": {"level": "WARNING"},
            },
            "root": {
                "level": level,
                "handlers": ["console", "app_file", "error_file"],
            },
        }
    )


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: Any, settings: Settings) -> None:
        super().__init__(app)
        self.settings = settings
        self.logger = logging.getLogger("app.access")
        self.error_logger = logging.getLogger("app")

    async def dispatch(self, request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status_code = 500

        try:
            if self.settings.log_profile.lower() == "development":
                self.logger.info("request_start %s", json.dumps(self._request_summary(request), ensure_ascii=False))

            response = await call_next(request)
            status_code = response.status_code
            response.headers["x-request-id"] = request_id
            return response
        except Exception:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            self.error_logger.exception(
                "request_exception method=%s path=%s elapsed_ms=%s",
                request.method,
                request.url.path,
                elapsed_ms,
            )
            raise
        finally:
            elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
            level = logging.WARNING if elapsed_ms >= self.settings.log_slow_request_ms or status_code >= 500 else logging.INFO
            self.logger.log(
                level,
                "request_end %s",
                json.dumps(
                    {
                        **self._request_summary(request),
                        "status_code": status_code,
                        "elapsed_ms": elapsed_ms,
                        "slow": elapsed_ms >= self.settings.log_slow_request_ms,
                    },
                    ensure_ascii=False,
                ),
            )
            request_id_var.reset(token)

    def _request_summary(self, request: Request) -> dict[str, Any]:
        client_host = request.client.host if request.client else None
        summary = {
            "method": request.method,
            "path": request.url.path,
            "query": _redact_mapping(dict(request.query_params)),
            "client": client_host,
            "content_length": request.headers.get("content-length"),
            "user_agent": request.headers.get("user-agent"),
        }
        if self.settings.log_request_body:
            summary["body_logging"] = "enabled but body capture is disabled for token safety"
        return summary


def cleanup_old_logs(settings: Settings) -> int:
    log_dir = resolve_log_dir(settings)
    retention_seconds = max(1, settings.log_retention_days) * 86400
    threshold = time.time() - retention_seconds
    removed = 0
    for path in log_dir.glob("*.log*"):
        if not path.is_file():
            continue
        try:
            if path.stat().st_mtime < threshold:
                path.unlink()
                removed += 1
        except OSError:
            logging.getLogger("app").warning("log_cleanup_failed path=%s", path, exc_info=True)
    return removed


async def log_cleanup_loop(settings: Settings) -> None:
    logger = logging.getLogger("app")
    while True:
        try:
            removed = cleanup_old_logs(settings)
            if removed:
                logger.info("log_cleanup removed=%s", removed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("log_cleanup_loop_failed")
        await asyncio.sleep(24 * 60 * 60)


def _resolve_level(value: str) -> str:
    normalized = value.upper()
    if normalized in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
        return normalized
    return "DEBUG"


def _redact_mapping(value: dict[str, Any]) -> dict[str, Any]:
    return {key: _redact_value(key, item) for key, item in value.items()}


def _redact_value(key: str, value: Any) -> Any:
    if key.lower() in SENSITIVE_KEYS:
        return "***"
    if isinstance(value, dict):
        return _redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(key, item) for item in value]
    return value
