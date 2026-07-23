from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.modules.sub2api.cache import get_site, upsert_cached_account_snapshot
from app.modules.sub2api.client import InvalidAdminApiKeyError, Sub2ApiClient, account_in_group
from app.modules.sub2api.postgres_repository import (
    fetch_admin_api_key as fetch_postgres_admin_api_key,
    fetch_groups as fetch_postgres_groups,
    fetch_pool_snapshot as fetch_postgres_pool_snapshot,
)
from app.modules.system.sql_dsn import redact_sql_error
from app.utils import now_utc, serialize_doc


SITE_ID = "US06-5002"
SOURCE_GROUP_ID = 4
PLUS_GROUP_ID = 6
BANNED_GROUP_ID = 7
PLUS_ERROR_GROUP_ID = 9
GROUP_SETTING_DEFAULTS = {
    "source_group_id": SOURCE_GROUP_ID,
    "plus_group_id": PLUS_GROUP_ID,
    "banned_group_id": BANNED_GROUP_ID,
    "plus_error_group_id": PLUS_ERROR_GROUP_ID,
}
PROBE_MODEL = "gpt-5.6-sol"
DEFAULT_INTERVAL_SECONDS = 15 * 60
SCHEDULER_POLL_SECONDS = 30
SETTINGS_ID = "plus-self-produced"
PROBE_LOCK_ID = "plus-self-produced-probe"
PROBE_LEASE_SECONDS = 5 * 60
PROBE_LEASE_RENEW_SECONDS = 60

MODEL_NOT_SUPPORTED_TEXT = "model is not supported when using codex with a chatgpt account"

_run_lock = asyncio.Lock()
logger = logging.getLogger("app.sub2api_plus_self_produced")


def classify_probe_result(
    verification: dict[str, Any] | None = None,
    *,
    error: str | None = None,
) -> str:
    verification = verification or {}
    error_text = " ".join(
        str(value).strip()
        for value in (verification.get("error"), error)
        if value is not None and str(value).strip()
    )
    normalized_error = error_text.lower()
    if MODEL_NOT_SUPPORTED_TEXT in normalized_error:
        return "model_not_supported"
    if _has_http_status(normalized_error, 401):
        return "unauthorized_banned"
    if verification.get("success") is True:
        return "passed"
    if _has_http_status(normalized_error, 429):
        return "rate_limited_but_eligible"
    return "failed"


def plus_account_name(name: Any) -> str:
    current_name = str(name or "").strip()
    if current_name.lower().startswith("plus"):
        return current_name
    return f"plus {current_name}".rstrip()


def free_account_name(name: Any) -> str:
    current_name = str(name or "").strip()
    return re.sub(r"^plus\s*", "", current_name, count=1, flags=re.IGNORECASE).strip()


async def get_settings(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    stored = await db.plus_self_produced_settings.find_one({"_id": SETTINGS_ID})
    settings = {
        "enabled": True,
        "interval_seconds": DEFAULT_INTERVAL_SECONDS,
        **GROUP_SETTING_DEFAULTS,
        **(stored or {}),
        "_id": SETTINGS_ID,
        "site_id": SITE_ID,
        "model": PROBE_MODEL,
    }
    for field, default in GROUP_SETTING_DEFAULTS.items():
        value = settings.get(field)
        settings[field] = value if isinstance(value, int) and value > 0 else default
    interval_seconds = max(60, int(settings.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS))
    settings["interval_seconds"] = interval_seconds
    settings["interval_minutes"] = interval_seconds // 60
    settings["running"] = _run_lock.locked()
    return serialize_doc(settings)


async def update_settings(
    db: AsyncIOMotorDatabase,
    payload: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    now = now_utc()
    current = await get_settings(db)
    effective_group_ids = {
        field: payload.get(field) if payload.get(field) is not None else current[field]
        for field in GROUP_SETTING_DEFAULTS
    }
    selected_ids = list(effective_group_ids.values())
    if len(set(selected_ids)) != len(selected_ids):
        raise HTTPException(status_code=422, detail="Plus routing group IDs must be distinct")
    available_groups = await list_groups(db)
    available_ids = {
        group.get("id")
        for group in available_groups
        if isinstance(group, dict) and isinstance(group.get("id"), int)
    }
    missing_ids = sorted(set(selected_ids) - available_ids)
    if missing_ids:
        missing_text = ", ".join(str(group_id) for group_id in missing_ids)
        raise HTTPException(status_code=422, detail=f"Sub2API groups not found: {missing_text}")
    updates: dict[str, Any] = {
        "site_id": SITE_ID,
        "updated_at": now,
        "updated_by": actor.get("_id"),
    }
    if "enabled" in payload and payload["enabled"] is not None:
        updates["enabled"] = bool(payload["enabled"])
    if "interval_minutes" in payload and payload["interval_minutes"] is not None:
        updates["interval_seconds"] = int(payload["interval_minutes"]) * 60
    for field in GROUP_SETTING_DEFAULTS:
        if field in payload and payload[field] is not None:
            updates[field] = int(payload[field])
    await db.plus_self_produced_settings.update_one(
        {"_id": SETTINGS_ID},
        {
            "$set": updates,
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return await get_settings(db)


def is_probe_due(settings: dict[str, Any], *, now: datetime | None = None) -> bool:
    if settings.get("enabled") is False:
        return False
    current_time = _as_utc(now or now_utc())
    last_finished_at = _parse_datetime(settings.get("last_finished_at"))
    if last_finished_at is None:
        return True
    interval_seconds = max(60, int(settings.get("interval_seconds") or DEFAULT_INTERVAL_SECONDS))
    return last_finished_at <= current_time - timedelta(seconds=interval_seconds)


async def get_status(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    settings = await get_settings(db)
    last_run = await db.plus_self_produced_runs.find_one(
        {"site_id": SITE_ID},
        sort=[("started_at", -1)],
    )
    return {
        "site_id": SITE_ID,
        "source_group_id": settings["source_group_id"],
        "plus_group_id": settings["plus_group_id"],
        "banned_group_id": settings["banned_group_id"],
        "plus_error_group_id": settings["plus_error_group_id"],
        "model": PROBE_MODEL,
        "running": _run_lock.locked(),
        "settings": settings,
        "last_run": serialize_doc(last_run) if last_run else None,
    }


async def list_groups(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    site = await get_site(db, SITE_ID, include_token=True)
    if not site:
        raise RuntimeError(f"Sub2API site {SITE_ID} not found")
    sql_dsn = str(site.get("sql_dsn") or "").strip()
    if not sql_dsn:
        raise RuntimeError(f"Sub2API site {SITE_ID} PostgreSQL SQL_DSN is not configured")
    try:
        groups = await fetch_postgres_groups(sql_dsn)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - API errors must not expose database credentials.
        raise RuntimeError(redact_sql_error(exc, sql_dsn, "postgresql")) from exc
    return [
        {
            "id": group["id"],
            "name": str(group.get("name") or f"Group {group['id']}"),
            "status": str(group.get("status") or ""),
        }
        for group in groups
        if isinstance(group, dict) and isinstance(group.get("id"), int)
    ]


async def list_results(
    db: AsyncIOMotorDatabase,
    *,
    page: int,
    page_size: int,
    classification: str | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {"site_id": SITE_ID}
    if classification:
        query["classification"] = classification
    total = await db.plus_self_produced_account_results.count_documents(query)
    cursor = (
        db.plus_self_produced_account_results.find(query)
        .sort("tested_at", -1)
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    items = [serialize_doc(item) async for item in cursor]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
    }


async def run_due_probe(
    db: AsyncIOMotorDatabase,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    settings = await get_settings(db)
    if not is_probe_due(settings, now=now):
        return {"ok": True, "skipped": True, "reason": "not due"}
    return await run_probe(db, trigger="scheduled")


async def scheduler_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        try:
            await run_due_probe(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the scheduler alive after infrastructure failures.
            logger.exception("plus_self_produced_scheduler_failed")
        await asyncio.sleep(SCHEDULER_POLL_SECONDS)


async def run_probe(
    db: AsyncIOMotorDatabase,
    *,
    trigger: str,
) -> dict[str, Any]:
    if _run_lock.locked():
        return {"ok": False, "conflict": True, "status": "running", "message": "plus self-produced probe is already running"}

    async with _run_lock:
        lease_owner = uuid4().hex
        lease = await acquire_probe_lease(db, owner=lease_owner)
        if not lease["acquired"]:
            return {"ok": False, "conflict": True, "status": "running", "message": "plus self-produced probe is already running"}
        lease_lost = asyncio.Event()
        heartbeat_task = asyncio.create_task(_probe_lease_heartbeat(db, owner=lease_owner, lease_lost=lease_lost))
        try:
            return await _run_probe_locked(db, trigger=trigger, lease_lost=lease_lost)
        finally:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
            try:
                await asyncio.shield(release_probe_lease(db, owner=lease_owner))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - lease expiry recovers cleanup failures.
                logger.exception("plus_self_produced_lease_release_failed")


async def acquire_probe_lease(
    db: AsyncIOMotorDatabase,
    *,
    owner: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    locked_at = _as_utc(now or now_utc())
    expires_at = locked_at + timedelta(seconds=PROBE_LEASE_SECONDS)
    try:
        document = await db.operation_locks.find_one_and_update(
            {
                "_id": PROBE_LOCK_ID,
                "$or": [
                    {"expires_at": {"$lte": locked_at}},
                    {"expires_at": {"$exists": False}},
                    {"owner": owner},
                ],
            },
            {
                "$set": {
                    "lock_type": "plus_self_produced_probe",
                    "owner": owner,
                    "locked_at": locked_at,
                    "expires_at": expires_at,
                    "updated_at": locked_at,
                },
                "$setOnInsert": {"created_at": locked_at},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        document = None
    acquired = bool(document and document.get("owner") == owner)
    return {"acquired": acquired, "owner": owner if acquired else None}


async def renew_probe_lease(db: AsyncIOMotorDatabase, *, owner: str) -> bool:
    now = now_utc()
    result = await db.operation_locks.update_one(
        {"_id": PROBE_LOCK_ID, "owner": owner},
        {"$set": {"expires_at": now + timedelta(seconds=PROBE_LEASE_SECONDS), "updated_at": now}},
    )
    return result.matched_count > 0


async def release_probe_lease(db: AsyncIOMotorDatabase, *, owner: str) -> bool:
    result = await db.operation_locks.delete_one({"_id": PROBE_LOCK_ID, "owner": owner})
    return result.deleted_count > 0


async def _probe_lease_heartbeat(
    db: AsyncIOMotorDatabase,
    *,
    owner: str,
    lease_lost: asyncio.Event,
) -> None:
    while True:
        await asyncio.sleep(PROBE_LEASE_RENEW_SECONDS)
        try:
            renewed = await renew_probe_lease(db, owner=owner)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - a lost lease must stop further remote mutations.
            logger.exception("plus_self_produced_lease_renew_failed")
            lease_lost.set()
            return
        if not renewed:
            lease_lost.set()
            return


def _ensure_probe_lease(lease_lost: asyncio.Event | None) -> None:
    if lease_lost is not None and lease_lost.is_set():
        raise RuntimeError("plus self-produced probe lease was lost")


async def _run_probe_locked(
    db: AsyncIOMotorDatabase,
    *,
    trigger: str,
    lease_lost: asyncio.Event | None = None,
) -> dict[str, Any]:
    started_at = now_utc()
    run_id = uuid4().hex
    settings = await get_settings(db)
    source_group_id_setting = settings["source_group_id"]
    plus_group_id_setting = settings["plus_group_id"]
    banned_group_id_setting = settings["banned_group_id"]
    plus_error_group_id_setting = settings["plus_error_group_id"]
    configured_group_ids = {
        source_group_id_setting,
        plus_group_id_setting,
        banned_group_id_setting,
        plus_error_group_id_setting,
    }
    counters = {
        "candidates": 0,
        "tested": 0,
        "eligible": 0,
        "promoted": 0,
        "banned": 0,
        "downgraded": 0,
        "plus_errors": 0,
        "failed": 0,
    }
    await db.plus_self_produced_runs.insert_one(
        {
            "_id": run_id,
            "site_id": SITE_ID,
            "trigger": trigger,
            "status": "running",
            "started_at": started_at,
            "created_at": started_at,
            **counters,
        }
    )
    await db.plus_self_produced_settings.update_one(
        {"_id": SETTINGS_ID},
        {
            "$set": {"last_started_at": started_at, "last_run_id": run_id, "updated_at": started_at},
            "$setOnInsert": {"enabled": True, "interval_seconds": DEFAULT_INTERVAL_SECONDS, "created_at": started_at},
        },
        upsert=True,
    )

    try:
        if len(configured_group_ids) != len(GROUP_SETTING_DEFAULTS):
            raise RuntimeError("Plus routing group IDs must be distinct")
        site = await get_site(db, SITE_ID, include_token=True)
        if not site:
            raise RuntimeError(f"Sub2API site {SITE_ID} not found")
        sql_dsn = str(site.get("sql_dsn") or "").strip()
        if not sql_dsn:
            raise RuntimeError(f"Sub2API site {SITE_ID} PostgreSQL SQL_DSN is not configured")
        try:
            pool_snapshot = await fetch_postgres_pool_snapshot(sql_dsn)
            admin_api_key = await fetch_postgres_admin_api_key(sql_dsn)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - persisted run errors must not expose database credentials.
            raise RuntimeError(redact_sql_error(exc, sql_dsn, "postgresql")) from exc
        client = Sub2ApiClient(base_url=site.get("base_url"), token=admin_api_key)
        _ensure_probe_lease(lease_lost)
        group_ids = {
            group.get("id")
            for group in pool_snapshot.get("groups", [])
            if isinstance(group, dict) and isinstance(group.get("id"), int)
        }
        missing_groups = configured_group_ids - group_ids
        if missing_groups:
            missing_text = ", ".join(str(group_id) for group_id in sorted(missing_groups))
            raise RuntimeError(f"Sub2API groups not found: {missing_text}")

        accounts = [
            item
            for item in pool_snapshot.get("accounts", [])
            if isinstance(item, dict)
            and (
                account_in_group(item, source_group_id_setting)
                or account_in_group(item, plus_group_id_setting)
            )
        ]
        counters["candidates"] = len(accounts)

        for account in accounts:
            _ensure_probe_lease(lease_lost)
            remote_account_id = account.get("id")
            if remote_account_id is None:
                counters["failed"] += 1
                continue

            tested_at = now_utc()
            source_group_id = (
                plus_group_id_setting
                if account_in_group(account, plus_group_id_setting)
                else source_group_id_setting
            )
            try:
                await client.update_account(
                    remote_account_id,
                    {"credentials": {"model_mapping": {}}},
                )
                _ensure_probe_lease(lease_lost)
            except asyncio.CancelledError:
                raise
            except InvalidAdminApiKeyError:
                raise
            except Exception as exc:  # noqa: BLE001 - one reset failure must not stop the queue.
                counters["failed"] += 1
                await _write_account_result(
                    db,
                    run_id=run_id,
                    account=account,
                    verification={"model": PROBE_MODEL, "latency_ms": None},
                    classification="failed",
                    action_status="model_reset_failed",
                    error=_exception_error(exc),
                    resulting_name=_account_name(account),
                    destination_group_id=None,
                    source_group_id=source_group_id,
                    tested_at=tested_at,
                )
                continue

            verification = await _test_account(client, remote_account_id)
            _ensure_probe_lease(lease_lost)
            counters["tested"] += 1
            classification = classify_probe_result(verification)
            test_error = _short_error(verification.get("error"))
            action_status = "not_moved"
            action_error: str | None = None
            resulting_name = _account_name(account)
            destination_group_id: int | None = None
            success_counter: str | None = None
            failure_action_status: str | None = None

            if source_group_id == source_group_id_setting:
                if classification in {"passed", "rate_limited_but_eligible"}:
                    counters["eligible"] += 1
                    destination_group_id = plus_group_id_setting
                    resulting_name = plus_account_name(_account_name(account))
                    payload = _move_payload(
                        account,
                        group_id=plus_group_id_setting,
                        name=resulting_name,
                        plan_type="plus",
                    )
                    action_status = "promoted"
                    success_counter = "promoted"
                    failure_action_status = "promotion_failed"
                elif classification == "unauthorized_banned":
                    destination_group_id = banned_group_id_setting
                    payload = _move_payload(account, group_id=banned_group_id_setting)
                    action_status = "banned"
                    success_counter = "banned"
                    failure_action_status = "ban_move_failed"
                else:
                    payload = None
                    counters["failed"] += 1
            else:
                if classification in {"passed", "rate_limited_but_eligible"}:
                    counters["eligible"] += 1
                    payload = None
                    action_status = "verified_plus"
                elif classification == "model_not_supported":
                    destination_group_id = source_group_id_setting
                    resulting_name = free_account_name(_account_name(account))
                    payload = _move_payload(
                        account,
                        group_id=source_group_id_setting,
                        name=resulting_name,
                        plan_type="free",
                    )
                    action_status = "reverted_to_free"
                    success_counter = "downgraded"
                    failure_action_status = "revert_failed"
                elif classification == "unauthorized_banned":
                    destination_group_id = plus_error_group_id_setting
                    payload = _move_payload(account, group_id=plus_error_group_id_setting)
                    action_status = "plus_error"
                    success_counter = "plus_errors"
                    failure_action_status = "plus_error_move_failed"
                else:
                    payload = None
                    counters["failed"] += 1

            if payload is not None:
                try:
                    updated = await client.update_account(remote_account_id, payload)
                    remote_snapshot = {**account, **(updated if isinstance(updated, dict) else {})}
                    remote_snapshot["id"] = remote_account_id
                    remote_snapshot["group_id"] = destination_group_id
                    remote_snapshot["group_ids"] = [destination_group_id]
                    remote_snapshot["name"] = resulting_name
                    if success_counter is not None:
                        counters[success_counter] += 1
                except asyncio.CancelledError:
                    raise
                except InvalidAdminApiKeyError:
                    raise
                except Exception as exc:  # noqa: BLE001 - one remote update must not stop the queue.
                    action_error = _exception_error(exc)
                    action_status = failure_action_status or "update_failed"
                    counters["failed"] += 1
                else:
                    try:
                        await upsert_cached_account_snapshot(db, SITE_ID, remote_snapshot)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - cache lag must not rewrite a successful remote result.
                        logger.warning(
                            "plus_self_produced_cache_update_failed site_id=%s account_id=%s error_type=%s",
                            SITE_ID,
                            remote_account_id,
                            exc.__class__.__name__,
                        )

            await _write_account_result(
                db,
                run_id=run_id,
                account=account,
                verification=verification,
                classification=classification,
                action_status=action_status,
                error=action_error or test_error,
                resulting_name=resulting_name,
                destination_group_id=destination_group_id,
                source_group_id=source_group_id,
                tested_at=tested_at,
            )

        finished_at = now_utc()
        run_status = "completed_with_errors" if counters["failed"] else "succeeded"
        result = {
            "ok": True,
            "run_id": run_id,
            "site_id": SITE_ID,
            "trigger": trigger,
            "status": run_status,
            "started_at": started_at,
            "finished_at": finished_at,
            **counters,
        }
        await _finish_run(db, result)
        return serialize_doc(result)
    except asyncio.CancelledError:
        finished_at = now_utc()
        result = {
            "ok": False,
            "run_id": run_id,
            "site_id": SITE_ID,
            "trigger": trigger,
            "status": "cancelled",
            "started_at": started_at,
            "finished_at": finished_at,
            "error": "probe cancelled during application shutdown",
            **counters,
        }
        await asyncio.shield(_finish_run(db, result))
        raise
    except Exception as exc:  # noqa: BLE001 - expose a recorded run failure to the scheduler and API.
        finished_at = now_utc()
        result = {
            "ok": False,
            "run_id": run_id,
            "site_id": SITE_ID,
            "trigger": trigger,
            "status": "failed",
            "started_at": started_at,
            "finished_at": finished_at,
            "error": _exception_error(exc),
            **counters,
        }
        await _finish_run(db, result)
        return serialize_doc(result)


async def _test_account(client: Sub2ApiClient, remote_account_id: int | str) -> dict[str, Any]:
    try:
        return await client.test_account(
            remote_account_id,
            model_id=PROBE_MODEL,
            prompt="",
            mode="default",
        )
    except asyncio.CancelledError:
        raise
    except InvalidAdminApiKeyError:
        raise
    except Exception as exc:  # noqa: BLE001 - transport failures are account-level probe results.
        error = _exception_error(exc)
        return {
            "success": False,
            "model": PROBE_MODEL,
            "latency_ms": None,
            "error": error,
        }


def _move_payload(
    account: dict[str, Any],
    *,
    group_id: int,
    name: str | None = None,
    plan_type: str | None = None,
) -> dict[str, Any]:
    del account
    payload: dict[str, Any] = {"group_id": group_id, "group_ids": [group_id]}
    if name is not None:
        payload = {"name": name, **payload}
    if plan_type is not None:
        payload["credentials"] = {"plan_type": plan_type}
    return payload


async def _write_account_result(
    db: AsyncIOMotorDatabase,
    *,
    run_id: str,
    account: dict[str, Any],
    verification: dict[str, Any],
    classification: str,
    action_status: str,
    error: str | None,
    resulting_name: str,
    destination_group_id: int | None,
    source_group_id: int,
    tested_at: datetime,
) -> None:
    remote_account_id = account.get("id")
    result_id = f"{SITE_ID}:{remote_account_id}"
    await db.plus_self_produced_account_results.update_one(
        {"_id": result_id},
        {
            "$set": {
                "site_id": SITE_ID,
                "remote_account_id": remote_account_id,
                "run_id": run_id,
                "account_name": _account_name(account),
                "email": _account_email(account),
                "classification": classification,
                "action_status": action_status,
                "error": _short_error(error),
                "model": verification.get("model") or PROBE_MODEL,
                "latency_ms": verification.get("latency_ms"),
                "source_group_id": source_group_id,
                "destination_group_id": destination_group_id,
                "resulting_name": resulting_name,
                "tested_at": tested_at,
                "updated_at": tested_at,
            },
            "$setOnInsert": {"created_at": tested_at},
        },
        upsert=True,
    )


async def _finish_run(db: AsyncIOMotorDatabase, result: dict[str, Any]) -> None:
    finished_at = result["finished_at"]
    run_updates = {key: value for key, value in result.items() if key not in {"run_id", "ok"}}
    run_updates["ok"] = result["ok"]
    await db.plus_self_produced_runs.update_one(
        {"_id": result["run_id"]},
        {"$set": run_updates},
    )
    await db.plus_self_produced_settings.update_one(
        {"_id": SETTINGS_ID},
        {
            "$set": {
                "last_finished_at": finished_at,
                "last_run_id": result["run_id"],
                "last_status": result["status"],
                "updated_at": finished_at,
            }
        },
        upsert=True,
    )


def _account_name(account: dict[str, Any]) -> str:
    value = account.get("name") or account.get("email") or account.get("id") or ""
    return str(value).strip()


def _account_email(account: dict[str, Any]) -> str | None:
    for value in (
        account.get("email"),
        (account.get("credentials") or {}).get("email") if isinstance(account.get("credentials"), dict) else None,
    ):
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _exception_error(exc: Exception) -> str:
    if isinstance(exc, HTTPException):
        return _redact_error_text(str(exc.detail))
    return _redact_error_text(str(exc) or exc.__class__.__name__)


def _short_error(value: Any) -> str | None:
    if value is None:
        return None
    text = _redact_error_text(str(value)).strip()
    return text[:500] if text else None


def _redact_error_text(value: str) -> str:
    text = str(value or "")
    text = re.sub(r"(?i)(\bBearer\s+)[^\s,;\"'}]+", r"\1***", text)
    text = re.sub(
        r"(?i)([\"'](?:access_token|refresh_token|id_token|(?:x[-_])?api[-_]?key|authorization|token)[\"']\s*:\s*[\"'])([^\"']*)([\"'])",
        r"\1***\3",
        text,
    )
    text = re.sub(
        r"(?i)(\b(?:access_token|refresh_token|id_token|(?:x[-_])?api[-_]?key|authorization|token)\b\s*[=:]\s*)([^\s,;&}\]]+)",
        r"\1***",
        text,
    )
    return re.sub(r"(?i)\bsk-[A-Za-z0-9_-]{8,}", "sk-***", text)


def _parse_datetime(value: Any) -> datetime | None:
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


def _has_http_status(error_text: str, status_code: int) -> bool:
    patterns = (
        rf"\bapi\s+returned\s+{status_code}\b",
        rf"\bstatus(?:_code)?\s*[:=]?\s*{status_code}\b",
        rf"\bhttp(?:/\d(?:\.\d)?)?\s+{status_code}\b",
    )
    return any(re.search(pattern, error_text, flags=re.IGNORECASE) for pattern in patterns)
