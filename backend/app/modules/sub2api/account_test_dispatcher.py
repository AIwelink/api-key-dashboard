from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.sub2api.account_test_outcomes import disable_reason
from app.modules.sub2api.account_test_service import (
    TEST_INTERVAL,
    TEST_MODEL,
    sanitize_account_test_text,
)
from app.modules.sub2api.cache import (
    _plan_type_from_plus_bundle_signature,
    get_site,
)
from app.modules.sub2api.client import Sub2ApiClient
from app.modules.sub2api.postgres_repository import fetch_admin_api_key as fetch_postgres_admin_api_key
from app.utils import now_utc


logger = logging.getLogger("app.sub2api_account_test_dispatcher")

HANDLER_RETRY_DELAY = timedelta(minutes=5)
RECOVERY_HANDLER_RETRY_DELAY = timedelta(minutes=3)
HANDLER_PROCESSING_TIMEOUT = timedelta(minutes=10)
MAX_HANDLER_ERROR_LENGTH = 1_000
AUTO_DISABLE_CONFIRMED_FAILURES = False

Handler = Callable[[AsyncIOMotorDatabase, dict[str, Any]], Awaitable[None]]


async def handle_scheduling(
    db: AsyncIOMotorDatabase,
    event: dict[str, Any],
    *,
    site: dict[str, Any] | None = None,
    client: Sub2ApiClient | Any | None = None,
) -> None:
    if not await _event_is_latest(db, event):
        return
    outcome = str(event.get("outcome") or "")
    recovery_required = _recovery_required(event)
    desired: bool | None = None
    if outcome == "passed":
        desired = True
    elif AUTO_DISABLE_CONFIRMED_FAILURES and disable_reason(outcome) is not None:
        desired = False
    if desired is None:
        return

    account = await db.sub2api_accounts_cache.find_one(
        {
            "site_id": event["site_id"],
            "sub2api_account_id": event["remote_account_id"],
        }
    )
    if account is None:
        return

    if recovery_required:
        if client is None:
            site, client = await _site_client(db, str(event["site_id"]), site=site)
        await _recover_403_account(
            db,
            event=event,
            account=account,
            client=client,
        )
        return

    current = _account_schedulable(account)
    if current is desired:
        return
    if desired is True and current is not False:
        return

    if client is None:
        site, client = await _site_client(db, str(event["site_id"]), site=site)
    await client.set_account_schedulable(event["remote_account_id"], desired)
    await db.sub2api_accounts_cache.update_one(
        {
            "site_id": event["site_id"],
            "sub2api_account_id": event["remote_account_id"],
        },
        {"$set": {"schedulable": desired, "account.schedulable": desired}},
    )


async def handle_plan_correction(
    db: AsyncIOMotorDatabase,
    event: dict[str, Any],
) -> None:
    if not await _event_is_latest(db, event):
        return
    outcome = str(event.get("outcome") or "")
    if outcome == "model_not_supported":
        await db.sub2api_account_test_states.update_one(
            {"_id": event["state_id"]},
            {
                "$unset": {
                    "verified_plan_type": "",
                    "verified_plan_type_source": "",
                    "verified_plan_type_at": "",
                },
                "$set": {"updated_at": now_utc()},
            },
        )
        return
    if outcome not in {"passed", "rate_limited"}:
        return

    account = await db.sub2api_accounts_cache.find_one(
        {
            "site_id": event["site_id"],
            "sub2api_account_id": event["remote_account_id"],
        }
    )
    if not account:
        return
    raw_account = account.get("account") if isinstance(account.get("account"), dict) else account
    remote_plan_type = _remote_plan_type(raw_account)
    if _plan_type_from_plus_bundle_signature(raw_account, remote_plan_type) != "plus":
        return

    await db.sub2api_account_test_states.update_one(
        {"_id": event["state_id"]},
        {
            "$set": {
                "verified_plan_type": "plus",
                "verified_plan_type_source": event.get("model") or TEST_MODEL,
                "verified_plan_type_at": event.get("tested_at") or now_utc(),
                "updated_at": now_utc(),
            }
        },
    )


HANDLERS: dict[str, Handler] = {
    "scheduling": handle_scheduling,
    "plan_correction": handle_plan_correction,
}


async def dispatch_test_event(
    db: AsyncIOMotorDatabase,
    event_id: str,
) -> dict[str, Any]:
    event = await db.sub2api_account_test_events.find_one({"_id": event_id})
    if not event:
        return {"event_id": event_id, "found": False, "completed": 0, "failed": 0}

    completed = 0
    failed = 0
    for name, handler in HANDLERS.items():
        if not await _claim_handler(db, event_id, name):
            continue
        try:
            await handler(db, event)
        except asyncio.CancelledError:
            await asyncio.shield(
                _finish_handler(
                    db,
                    event_id,
                    name,
                    status="failed",
                    error="dispatcher cancelled during application shutdown",
                    retry_delay=_handler_retry_delay(event, name),
                )
            )
            raise
        except Exception as exc:  # noqa: BLE001 - persist and replay judgment failures.
            failed += 1
            await _finish_handler(
                db,
                event_id,
                name,
                status="failed",
                error=str(exc),
                retry_delay=_handler_retry_delay(event, name),
            )
            logger.warning(
                "sub2api_account_test_handler_failed event_id=%s handler=%s error_type=%s",
                event_id,
                name,
                exc.__class__.__name__,
            )
        else:
            completed += 1
            await _finish_handler(db, event_id, name, status="completed")
    return {
        "event_id": event_id,
        "found": True,
        "completed": completed,
        "failed": failed,
    }


async def replay_pending_dispatches(
    db: AsyncIOMotorDatabase,
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> int:
    now = _as_utc(now or now_utc())
    due_paths: list[dict[str, Any]] = []
    for name in HANDLERS:
        due_paths.extend(
            [
                {f"dispatch.{name}.status": "pending"},
                {
                    f"dispatch.{name}.status": "failed",
                    "$or": [
                        {f"dispatch.{name}.next_retry_at": {"$lte": now}},
                        {f"dispatch.{name}.next_retry_at": {"$exists": False}},
                    ],
                },
            ]
        )
    cursor = db.sub2api_account_test_events.find({"$or": due_paths}).sort("tested_at", 1).limit(limit)
    replayed = 0
    async for event in cursor:
        await dispatch_test_event(db, str(event["_id"]))
        replayed += 1
    return replayed


async def _claim_handler(
    db: AsyncIOMotorDatabase,
    event_id: str,
    name: str,
    *,
    now: datetime | None = None,
) -> bool:
    now = _as_utc(now or now_utc())
    status_path = f"dispatch.{name}.status"
    retry_path = f"dispatch.{name}.next_retry_at"
    started_path = f"dispatch.{name}.started_at"
    result = await db.sub2api_account_test_events.update_one(
        {
            "_id": event_id,
            "$or": [
                {status_path: "pending"},
                {
                    status_path: "failed",
                    "$or": [
                        {retry_path: {"$lte": now}},
                        {retry_path: {"$exists": False}},
                    ],
                },
                {
                    status_path: "processing",
                    started_path: {"$lte": now - HANDLER_PROCESSING_TIMEOUT},
                },
            ],
        },
        {
            "$set": {
                status_path: "processing",
                started_path: now,
                f"dispatch.{name}.updated_at": now,
            },
            "$inc": {f"dispatch.{name}.attempts": 1},
            "$unset": {
                f"dispatch.{name}.last_error": "",
                retry_path: "",
            },
        },
    )
    return bool(result.modified_count)


async def _finish_handler(
    db: AsyncIOMotorDatabase,
    event_id: str,
    name: str,
    *,
    status: str,
    error: str | None = None,
    retry_delay: timedelta = HANDLER_RETRY_DELAY,
) -> None:
    finished_at = now_utc()
    updates: dict[str, Any] = {
        f"dispatch.{name}.status": status,
        f"dispatch.{name}.finished_at": finished_at,
        f"dispatch.{name}.updated_at": finished_at,
    }
    if error is not None:
        updates[f"dispatch.{name}.last_error"] = sanitize_account_test_text(
            error,
            MAX_HANDLER_ERROR_LENGTH,
        )
        updates[f"dispatch.{name}.next_retry_at"] = finished_at + retry_delay
    update: dict[str, Any] = {"$set": updates}
    if error is None:
        update["$unset"] = {
            f"dispatch.{name}.last_error": "",
            f"dispatch.{name}.next_retry_at": "",
        }
    await db.sub2api_account_test_events.update_one(
        {"_id": event_id, f"dispatch.{name}.status": "processing"},
        update,
    )


async def _recover_403_account(
    db: AsyncIOMotorDatabase,
    *,
    event: dict[str, Any],
    account: dict[str, Any],
    client: Sub2ApiClient | Any,
) -> None:
    scheduling = (
        event.get("dispatch", {}).get("scheduling", {})
        if isinstance(event.get("dispatch"), dict)
        else {}
    )
    cache_updates: dict[str, Any] = {}
    effective_schedulable = _account_schedulable(account)

    if scheduling.get("recover_state_status") != "completed":
        await _start_recovery_phase(db, event, "recover_state")
        try:
            response = await client.recover_account_state(event["remote_account_id"])
            recovery_cache_updates = _cache_updates_from_response(response)
            if recovery_cache_updates:
                await db.sub2api_accounts_cache.update_one(
                    {
                        "site_id": event["site_id"],
                        "sub2api_account_id": event["remote_account_id"],
                    },
                    {"$set": recovery_cache_updates},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _fail_recovery_phase(db, event, "recover_state", exc)
            raise
        await _complete_recovery_phase(db, event, "recover_state")
        cache_updates.update(recovery_cache_updates)
        response_schedulable = _response_schedulable(response)
        if response_schedulable is not None:
            effective_schedulable = response_schedulable

    if not await _event_is_latest(db, event):
        return

    if scheduling.get("enable_schedulable_status") != "completed":
        await _start_recovery_phase(db, event, "enable_schedulable")
        try:
            response = None
            if effective_schedulable is not True:
                response = await client.set_account_schedulable(
                    event["remote_account_id"],
                    True,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await _fail_recovery_phase(db, event, "enable_schedulable", exc)
            raise
        await _complete_recovery_phase(db, event, "enable_schedulable")
        cache_updates.update(_cache_updates_from_response(response))

    if not await _event_is_latest(db, event):
        return

    completed_at = now_utc()
    await db.sub2api_account_test_events.update_one(
        {"_id": event["_id"]},
        {
            "$set": {
                "dispatch.scheduling.recovery_completed_at": completed_at,
                "dispatch.scheduling.updated_at": completed_at,
            }
        },
    )
    await db.sub2api_account_test_states.update_one(
        {"_id": event["state_id"], "last_event_id": event["_id"]},
        {
            "$set": {
                "recovery_event_id": event["_id"],
                "recovery_completed_at": completed_at,
                "next_test_at": _as_utc(event["tested_at"]) + TEST_INTERVAL,
                "interval_mode": "normal",
                "updated_at": completed_at,
            }
        },
    )
    cache_updates.update(
        {
            "schedulable": True,
            "account.schedulable": True,
        }
    )
    await db.sub2api_accounts_cache.update_one(
        {
            "site_id": event["site_id"],
            "sub2api_account_id": event["remote_account_id"],
        },
        {"$set": cache_updates},
    )


async def _start_recovery_phase(
    db: AsyncIOMotorDatabase,
    event: dict[str, Any],
    phase: str,
) -> None:
    updated_at = now_utc()
    await db.sub2api_account_test_events.update_one(
        {"_id": event["_id"]},
        {
            "$set": {
                f"dispatch.scheduling.{phase}_status": "processing",
                f"dispatch.scheduling.{phase}_started_at": updated_at,
                "dispatch.scheduling.updated_at": updated_at,
            },
            "$inc": {f"dispatch.scheduling.{phase}_attempts": 1},
            "$unset": {f"dispatch.scheduling.{phase}_last_error": ""},
        },
    )


async def _complete_recovery_phase(
    db: AsyncIOMotorDatabase,
    event: dict[str, Any],
    phase: str,
) -> None:
    completed_at = now_utc()
    await db.sub2api_account_test_events.update_one(
        {"_id": event["_id"]},
        {
            "$set": {
                f"dispatch.scheduling.{phase}_status": "completed",
                f"dispatch.scheduling.{phase}_completed_at": completed_at,
                "dispatch.scheduling.updated_at": completed_at,
            },
            "$unset": {f"dispatch.scheduling.{phase}_last_error": ""},
        },
    )


async def _fail_recovery_phase(
    db: AsyncIOMotorDatabase,
    event: dict[str, Any],
    phase: str,
    error: Exception,
) -> None:
    failed_at = now_utc()
    await db.sub2api_account_test_events.update_one(
        {"_id": event["_id"]},
        {
            "$set": {
                f"dispatch.scheduling.{phase}_status": "failed",
                f"dispatch.scheduling.{phase}_failed_at": failed_at,
                f"dispatch.scheduling.{phase}_last_error": (
                    sanitize_account_test_text(str(error), MAX_HANDLER_ERROR_LENGTH)
                ),
                "dispatch.scheduling.updated_at": failed_at,
            }
        },
    )


def _recovery_required(event: dict[str, Any]) -> bool:
    recovery = event.get("recovery") if isinstance(event.get("recovery"), dict) else {}
    return event.get("outcome") == "passed" and recovery.get("required") is True


def _handler_retry_delay(event: dict[str, Any], name: str) -> timedelta:
    if name == "scheduling" and _recovery_required(event):
        return RECOVERY_HANDLER_RETRY_DELAY
    return HANDLER_RETRY_DELAY


def _cache_updates_from_response(response: Any) -> dict[str, Any]:
    if not isinstance(response, dict):
        return {}
    payload = response.get("data") if isinstance(response.get("data"), dict) else response
    updates: dict[str, Any] = {}
    for field in ("status", "error_message", "schedulable"):
        if field not in payload:
            continue
        updates[field] = payload[field]
        updates[f"account.{field}"] = payload[field]
    return updates


def _response_schedulable(response: Any) -> bool | None:
    if not isinstance(response, dict):
        return None
    payload = response.get("data") if isinstance(response.get("data"), dict) else response
    value = payload.get("schedulable")
    return value if isinstance(value, bool) else None


async def _site_client(
    db: AsyncIOMotorDatabase,
    site_id: str,
    *,
    site: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], Sub2ApiClient]:
    site = site or await get_site(db, site_id, include_token=True)
    if not site:
        raise LookupError(f"Sub2API site {site_id} not found")
    sql_dsn = str(site.get("sql_dsn") or "").strip()
    token = await fetch_postgres_admin_api_key(sql_dsn) if sql_dsn else site.get("token")
    return site, Sub2ApiClient(base_url=site.get("base_url"), token=token)


async def _event_is_latest(
    db: AsyncIOMotorDatabase,
    event: dict[str, Any],
) -> bool:
    state = await db.sub2api_account_test_states.find_one(
        {"_id": event.get("state_id")},
        {"last_event_id": 1},
    )
    return bool(state and state.get("last_event_id") == event.get("_id"))


def _account_schedulable(account: dict[str, Any] | None) -> bool | None:
    if not account:
        return None
    value = account.get("schedulable")
    nested = account.get("account") if isinstance(account.get("account"), dict) else {}
    if value is None:
        value = nested.get("schedulable")
    return value if isinstance(value, bool) else None


def _remote_plan_type(account: dict[str, Any]) -> Any:
    nested = account.get("account") if isinstance(account.get("account"), dict) else {}
    credentials = account.get("credentials")
    if not isinstance(credentials, dict):
        credentials = nested.get("credentials") if isinstance(nested.get("credentials"), dict) else {}
    return credentials.get("plan_type") or account.get("plan_type") or nested.get("plan_type")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
