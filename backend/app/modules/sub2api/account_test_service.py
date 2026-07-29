from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.sub2api.account_test_outcomes import (
    classify_test_result,
    snapshot_has_http_status,
)
from app.modules.sub2api.client import InvalidAdminApiKeyError
from app.utils import now_utc


logger = logging.getLogger("app.sub2api_account_test")

TEST_MODEL = "gpt-5.5"
TEST_INTERVAL = timedelta(hours=24)
RAPID_403_TEST_INTERVAL = timedelta(minutes=3)
EVENT_RETENTION = timedelta(days=90)
MAX_ERROR_LENGTH = 2_000
MAX_RESPONSE_PREVIEW_LENGTH = 500

Dispatcher = Callable[[AsyncIOMotorDatabase, str], Awaitable[Any]]


async def execute_account_test(
    db: AsyncIOMotorDatabase,
    *,
    site: dict[str, Any],
    account: dict[str, Any],
    client: Any,
    dispatcher: Dispatcher | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    tested_at = _as_utc(now or now_utc())
    site_id = _site_id(site)
    remote_account_id = _remote_account_id(account)

    transport_error: str | None = None
    try:
        verification = await client.test_account(
            remote_account_id,
            model_id=TEST_MODEL,
            prompt="",
            mode="default",
        )
    except asyncio.CancelledError:
        raise
    except InvalidAdminApiKeyError:
        raise
    except Exception as exc:  # noqa: BLE001 - account failures must become durable results.
        transport_error = sanitize_account_test_text(
            str(getattr(exc, "detail", None) or exc),
            MAX_ERROR_LENGTH,
        )
        verification = {
            "success": False,
            "model": TEST_MODEL,
            "mode": "default",
            "latency_ms": None,
            "response_preview": "",
            "error": transport_error,
        }

    outcome = classify_test_result(verification, transport_error=transport_error)
    event = _event_document(
        site_id=site_id,
        account=account,
        remote_account_id=remote_account_id,
        verification=verification,
        outcome=outcome,
        tested_at=tested_at,
    )
    await db.sub2api_account_test_events.insert_one(event)
    await db.sub2api_account_test_states.update_one(
        {"_id": event["state_id"]},
        {
            "$set": _latest_state(event),
            "$setOnInsert": {"created_at": tested_at},
        },
        upsert=True,
    )
    try:
        await _sync_cache_test_fields(db, event)
    except asyncio.CancelledError:
        raise
    except Exception:  # noqa: BLE001 - latest state can repair this derived cache data.
        logger.exception(
            "sub2api_account_test_cache_sync_failed site_id=%s account_id=%s",
            site_id,
            remote_account_id,
        )

    if dispatcher is None:
        from app.modules.sub2api.account_test_dispatcher import dispatch_test_event

        dispatcher = dispatch_test_event
    await dispatcher(db, event["_id"])
    return event


async def repair_latest_states_from_events(
    db: AsyncIOMotorDatabase,
    *,
    limit: int = 100,
) -> int:
    cursor = db.sub2api_account_test_events.find({}).sort("tested_at", -1).limit(limit)
    repaired = 0
    seen_states: set[str] = set()
    async for event in cursor:
        state_id = str(event.get("state_id") or "").strip()
        if not state_id or state_id in seen_states:
            continue
        seen_states.add(state_id)
        state = await db.sub2api_account_test_states.find_one({"_id": state_id})
        current_tested_at = _optional_datetime((state or {}).get("last_tested_at"))
        event_tested_at = _optional_datetime(event.get("tested_at"))
        if state and current_tested_at and event_tested_at and current_tested_at >= event_tested_at:
            continue
        await db.sub2api_account_test_states.update_one(
            {"_id": state_id},
            {
                "$set": _latest_state(event),
                "$setOnInsert": {"created_at": event_tested_at or now_utc()},
            },
            upsert=True,
        )
        repaired += 1
    return repaired


def _event_document(
    *,
    site_id: str,
    account: dict[str, Any],
    remote_account_id: int | str,
    verification: dict[str, Any],
    outcome: str,
    tested_at: datetime,
) -> dict[str, Any]:
    error = sanitize_account_test_text(
        str(verification.get("error") or "").strip(),
        MAX_ERROR_LENGTH,
    ) or None
    http_status = _http_status(error)
    snapshot_http_403 = snapshot_has_http_status(account, 403)
    rapid_http_403 = snapshot_http_403 or http_status == 403
    recovery_required = snapshot_http_403 and outcome == "passed"
    next_test_at = tested_at + (
        RAPID_403_TEST_INTERVAL if rapid_http_403 else TEST_INTERVAL
    )
    return {
        "_id": uuid4().hex,
        "state_id": f"{site_id}:{remote_account_id}",
        "site_id": site_id,
        "remote_account_id": remote_account_id,
        "normalized_email": _normalized_email(account),
        "model": TEST_MODEL,
        "mode": "default",
        "outcome": outcome,
        "success": verification.get("success") is True,
        "http_status": http_status,
        "error_code": _error_code(error),
        "error": error,
        "response_preview": sanitize_account_test_text(
            str(verification.get("response_preview") or ""),
            MAX_RESPONSE_PREVIEW_LENGTH,
        ),
        "latency_ms": _number(verification.get("latency_ms")),
        "tested_at": tested_at,
        "next_test_at": next_test_at,
        "recovery": {
            "required": recovery_required,
            "snapshot_http_403": snapshot_http_403,
            "snapshot_fetched_at": _optional_datetime(account.get("fetched_at")),
        },
        "dispatch": {
            "scheduling": {
                "status": "pending",
                "attempts": 0,
                "recover_state_status": (
                    "pending" if recovery_required else "not_required"
                ),
                "recover_state_attempts": 0,
                "enable_schedulable_status": (
                    "pending" if recovery_required else "not_required"
                ),
                "enable_schedulable_attempts": 0,
            },
            "plan_correction": {"status": "pending", "attempts": 0},
        },
        "expires_at": tested_at + EVENT_RETENTION,
    }


def _latest_state(event: dict[str, Any]) -> dict[str, Any]:
    tested_at = _as_utc(event["tested_at"])
    recovery = event.get("recovery") if isinstance(event.get("recovery"), dict) else {}
    next_test_at = event.get("next_test_at") or tested_at + TEST_INTERVAL
    rapid_http_403 = next_test_at - tested_at == RAPID_403_TEST_INTERVAL
    return {
        "site_id": event["site_id"],
        "remote_account_id": event["remote_account_id"],
        "normalized_email": event.get("normalized_email"),
        "last_event_id": event["_id"],
        "last_outcome": event["outcome"],
        "last_success": event.get("success") is True,
        "last_error": event.get("error"),
        "last_response_preview": event.get("response_preview") or "",
        "last_latency_ms": event.get("latency_ms"),
        "last_http_status": event.get("http_status"),
        "last_snapshot_http_403": recovery.get("snapshot_http_403") is True,
        "last_snapshot_fetched_at": recovery.get("snapshot_fetched_at"),
        "last_tested_at": tested_at,
        "next_test_at": next_test_at,
        "interval_mode": "rapid_403" if rapid_http_403 else "normal",
        "model": event.get("model") or TEST_MODEL,
        "updated_at": tested_at,
    }


async def _sync_cache_test_fields(db: AsyncIOMotorDatabase, event: dict[str, Any]) -> None:
    await db.sub2api_accounts_cache.update_one(
        {
            "site_id": event["site_id"],
            "sub2api_account_id": event["remote_account_id"],
        },
        {
            "$set": {
                "remote_test_outcome": event["outcome"],
                "remote_test_success": event.get("success") is True,
                "remote_tested_at": event["tested_at"],
                "remote_test_model": event.get("model") or TEST_MODEL,
                "remote_test_error": event.get("error"),
                "remote_test_response_preview": event.get("response_preview") or "",
                "remote_test_latency_ms": event.get("latency_ms"),
            }
        },
    )


def _site_id(site: dict[str, Any]) -> str:
    value = str(site.get("_id") or site.get("id") or "").strip()
    if not value:
        raise ValueError("sub2api site id is required")
    return value


def _remote_account_id(account: dict[str, Any]) -> int | str:
    value = account.get("remote_account_id")
    if value is None:
        value = account.get("sub2api_account_id")
    nested = account.get("account") if isinstance(account.get("account"), dict) else {}
    if value is None:
        value = nested.get("id")
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip():
        raise ValueError("sub2api remote account id is required")
    return value


def _normalized_email(account: dict[str, Any]) -> str | None:
    nested = account.get("account") if isinstance(account.get("account"), dict) else {}
    credentials = account.get("credentials")
    if not isinstance(credentials, dict):
        credentials = nested.get("credentials") if isinstance(nested.get("credentials"), dict) else {}
    email = str(credentials.get("email") or account.get("email") or nested.get("email") or "").strip().lower()
    return email or None


def _bounded(value: str, length: int) -> str:
    return value[:length]


def sanitize_account_test_text(value: str, length: int) -> str:
    text = str(value or "")
    text = re.sub(
        r"(?i)([\"'](?:access_token|refresh_token|id_token|(?:x[-_])?api[-_]?key|authorization)[\"']\s*:\s*[\"'])([^\"']*)([\"'])",
        r"\1***\3",
        text,
    )
    text = re.sub(
        r"(?i)(\b(?:access_token|refresh_token|id_token|(?:x[-_])?api[-_]?key|authorization)\b\s*[=:]\s*)([^\s,;&}\]]+)",
        r"\1***",
        text,
    )
    text = re.sub(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+", "Bearer ***", text)
    return _bounded(text, length)


def _http_status(error: str | None) -> int | None:
    match = re.search(
        r"\b(?:returned|status|http(?:/\d(?:\.\d)?)?)[^0-9]{0,12}([1-5][0-9]{2})\b",
        str(error or "").lower(),
    )
    return int(match.group(1)) if match else None


def _error_code(error: str | None) -> str | None:
    text = str(error or "")
    match = re.search(r'(?i)["\']code["\']\s*:\s*["\']([^"\']+)', text)
    return match.group(1)[:120] if match else None


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value


def _optional_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
