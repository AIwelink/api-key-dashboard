from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.modules.sub2api.account_test_dispatcher import replay_pending_dispatches
from app.modules.sub2api.account_test_outcomes import snapshot_has_http_status
from app.modules.sub2api.account_test_service import (
    RAPID_403_TEST_INTERVAL,
    execute_account_test,
    repair_latest_states_from_events,
)
from app.modules.sub2api.cache import sub2api_site_query
from app.modules.sub2api.client import InvalidAdminApiKeyError, Sub2ApiClient
from app.modules.sub2api.postgres_repository import fetch_admin_api_key as fetch_postgres_admin_api_key
from app.utils import now_utc


logger = logging.getLogger("app.sub2api_account_test_scheduler")

SCHEDULER_LOCK_ID = "unified-account-test-scheduler"
SCHEDULER_LEASE = timedelta(minutes=5)
ADMIN_AUTH_BACKOFF = timedelta(minutes=30)
SITE_ERROR_BACKOFF = timedelta(minutes=5)
IDLE_POLL_SECONDS = 30
ACTIVE_POLL_SECONDS = 1


def select_due_account(
    sites: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
    *,
    now: datetime | None = None,
    site_backoffs: dict[str, datetime] | None = None,
) -> dict[str, Any] | None:
    now = _as_utc(now or now_utc())
    site_backoffs = site_backoffs or {}
    sites_by_id = {
        str(site.get("_id") or site.get("id")): site
        for site in sites
        if str(site.get("_id") or site.get("id") or "").strip()
    }
    candidates: list[tuple[tuple[Any, ...], dict[str, Any], dict[str, Any] | None]] = []
    for account in accounts:
        site_id = str(account.get("site_id") or "").strip()
        site = sites_by_id.get(site_id)
        remote_account_id = _remote_account_id(account)
        if site is None or remote_account_id is None:
            continue
        backoff_until = _optional_datetime(site_backoffs.get(site_id))
        if backoff_until is not None and backoff_until > now:
            continue

        state_id = f"{site_id}:{remote_account_id}"
        state = states.get(state_id)
        next_test_at = _optional_datetime((state or {}).get("next_test_at"))
        last_tested_at = _optional_datetime((state or {}).get("last_tested_at"))
        snapshot_fetched_at = _optional_datetime(account.get("fetched_at"))
        recovery_completed_at = _optional_datetime(
            (state or {}).get("recovery_completed_at")
        )
        snapshot_http_403 = snapshot_has_http_status(account, 403)
        stale_snapshot_403 = bool(
            snapshot_http_403
            and recovery_completed_at is not None
            and snapshot_fetched_at is not None
            and snapshot_fetched_at <= recovery_completed_at
        )
        current_snapshot_403 = snapshot_http_403 and not stale_snapshot_403
        latest_model_403 = (state or {}).get("last_http_status") == 403
        rapid_http_403 = current_snapshot_403 or latest_model_403

        if rapid_http_403:
            snapshot_newly_403 = bool(
                current_snapshot_403
                and (state or {}).get("last_snapshot_http_403") is not True
            )
            if state is None or next_test_at is None or snapshot_newly_403:
                effective_due_at = datetime.min.replace(tzinfo=UTC)
            elif last_tested_at is not None:
                effective_due_at = min(
                    next_test_at,
                    last_tested_at + RAPID_403_TEST_INTERVAL,
                )
            else:
                effective_due_at = next_test_at
            if effective_due_at > now:
                continue
            priority = (0, effective_due_at, site_id, str(remote_account_id))
        elif state is None or next_test_at is None:
            priority = (
                1,
                datetime.min.replace(tzinfo=UTC),
                site_id,
                str(remote_account_id),
            )
        elif next_test_at <= now:
            priority = (2, next_test_at, site_id, str(remote_account_id))
        else:
            continue
        candidates.append((priority, {"site": site, "account": account}, state))

    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    selected = candidates[0][1]
    selected["state"] = candidates[0][2]
    return selected


async def load_due_account(
    db: AsyncIOMotorDatabase,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    now = _as_utc(now or now_utc())
    sites = [
        site
        async for site in db.sub2api_sites.find(sub2api_site_query(status="active")).sort("_id", 1)
    ]
    if not sites:
        return None
    site_ids = [str(site.get("_id") or site.get("id")) for site in sites]
    accounts = [
        account
        async for account in db.sub2api_accounts_cache.find(
            {"site_id": {"$in": site_ids}},
            {
                "site_id": 1,
                "sub2api_account_id": 1,
                "status": 1,
                "schedulable": 1,
                "error_message": 1,
                "fetched_at": 1,
                "account.status": 1,
                "account.error_message": 1,
                "account.schedulable": 1,
                "credentials.email": 1,
                "account.credentials.email": 1,
            },
        )
    ]
    state_ids = [
        f"{account.get('site_id')}:{remote_id}"
        for account in accounts
        if (remote_id := _remote_account_id(account)) is not None
    ]
    states = {
        str(state["_id"]): state
        async for state in db.sub2api_account_test_states.find({"_id": {"$in": state_ids}})
    }
    site_meta = {
        str(meta.get("site_id") or meta.get("_id")): meta
        async for meta in db.sub2api_account_test_site_meta.find(
            {"site_id": {"$in": site_ids}},
            {"site_id": 1, "backoff_until": 1},
        )
    }
    backoffs = {
        site_id: meta.get("backoff_until")
        for site_id, meta in site_meta.items()
        if meta.get("backoff_until") is not None
    }
    return select_due_account(sites, accounts, states, now=now, site_backoffs=backoffs)


async def build_site_client(site: dict[str, Any]) -> Sub2ApiClient:
    sql_dsn = str(site.get("sql_dsn") or "").strip()
    token = await fetch_postgres_admin_api_key(sql_dsn) if sql_dsn else site.get("token")
    return Sub2ApiClient(base_url=site.get("base_url"), token=token)


async def run_account_test_cycle(
    db: AsyncIOMotorDatabase,
    *,
    now: datetime | None = None,
    owner: str | None = None,
) -> dict[str, Any]:
    cycle_at = _as_utc(now or now_utc())
    owner = owner or uuid4().hex
    if not await acquire_scheduler_lease(db, owner=owner, now=cycle_at):
        return {"ok": True, "tested": False, "skipped": True, "reason": "lease_unavailable"}

    try:
        repaired = await repair_latest_states_from_events(db)
        replayed = await replay_pending_dispatches(db, now=cycle_at)
        due = await load_due_account(db, now=cycle_at)
        if due is None:
            return {
                "ok": True,
                "tested": False,
                "reason": "no_due_accounts",
                "states_repaired": repaired,
                "dispatches_replayed": replayed,
            }

        site = due["site"]
        account = due["account"]
        site_id = str(site.get("_id") or site.get("id"))
        remote_account_id = _remote_account_id(account)
        try:
            client = await build_site_client(site)
            event = await execute_account_test(
                db,
                site=site,
                account=account,
                client=client,
                now=cycle_at,
            )
        except asyncio.CancelledError:
            raise
        except InvalidAdminApiKeyError as exc:
            await _record_site_failure(
                db,
                site_id=site_id,
                status="admin_auth_error",
                error=str(exc),
                failed_at=cycle_at,
                backoff=ADMIN_AUTH_BACKOFF,
            )
            return {
                "ok": False,
                "tested": False,
                "reason": "admin_auth_error",
                "site_id": site_id,
                "remote_account_id": remote_account_id,
            }
        except Exception as exc:  # noqa: BLE001 - a site failure must not stop the global queue.
            await _record_site_failure(
                db,
                site_id=site_id,
                status="site_error",
                error=str(exc),
                failed_at=cycle_at,
                backoff=SITE_ERROR_BACKOFF,
            )
            logger.warning(
                "sub2api_account_test_site_failed site_id=%s error_type=%s",
                site_id,
                exc.__class__.__name__,
            )
            return {
                "ok": False,
                "tested": False,
                "reason": "site_error",
                "site_id": site_id,
                "remote_account_id": remote_account_id,
            }

        await db.sub2api_account_test_site_meta.update_one(
            {"site_id": site_id},
            {
                "$set": {
                    "site_id": site_id,
                    "status": "active",
                    "last_tested_at": cycle_at,
                    "last_event_id": event.get("_id"),
                    "updated_at": cycle_at,
                },
                "$unset": {"last_error": "", "backoff_until": ""},
                "$setOnInsert": {"created_at": cycle_at},
            },
            upsert=True,
        )
        return {
            "ok": True,
            "tested": True,
            "site_id": site_id,
            "remote_account_id": remote_account_id,
            "outcome": event.get("outcome"),
            "states_repaired": repaired,
            "dispatches_replayed": replayed,
        }
    finally:
        try:
            await asyncio.shield(release_scheduler_lease(db, owner=owner))
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - lease expiry is the recovery path.
            logger.exception("sub2api_account_test_lease_release_failed")


async def account_test_scheduler_loop(db: AsyncIOMotorDatabase) -> None:
    owner = uuid4().hex
    while True:
        try:
            result = await run_account_test_cycle(db, owner=owner)
            await asyncio.sleep(ACTIVE_POLL_SECONDS if result.get("tested") else IDLE_POLL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - infrastructure recovery must keep the scheduler alive.
            logger.exception("sub2api_account_test_scheduler_failed")
            await asyncio.sleep(IDLE_POLL_SECONDS)


async def acquire_scheduler_lease(
    db: AsyncIOMotorDatabase,
    *,
    owner: str,
    now: datetime | None = None,
) -> bool:
    locked_at = _as_utc(now or now_utc())
    try:
        document = await db.operation_locks.find_one_and_update(
            {
                "_id": SCHEDULER_LOCK_ID,
                "$or": [
                    {"expires_at": {"$lte": locked_at}},
                    {"expires_at": {"$exists": False}},
                    {"owner": owner},
                ],
            },
            {
                "$set": {
                    "lock_type": "unified_account_test_scheduler",
                    "owner": owner,
                    "locked_at": locked_at,
                    "expires_at": locked_at + SCHEDULER_LEASE,
                    "updated_at": locked_at,
                },
                "$setOnInsert": {"created_at": locked_at},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return False
    return bool(document and document.get("owner") == owner)


async def release_scheduler_lease(db: AsyncIOMotorDatabase, *, owner: str) -> bool:
    result = await db.operation_locks.delete_one({"_id": SCHEDULER_LOCK_ID, "owner": owner})
    return bool(result.deleted_count)


async def _record_site_failure(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    status: str,
    error: str,
    failed_at: datetime,
    backoff: timedelta,
) -> None:
    await db.sub2api_account_test_site_meta.update_one(
        {"site_id": site_id},
        {
            "$set": {
                "site_id": site_id,
                "status": status,
                "last_error": str(error)[:1_000],
                "last_failed_at": failed_at,
                "backoff_until": failed_at + backoff,
                "updated_at": failed_at,
            },
            "$setOnInsert": {"created_at": failed_at},
        },
        upsert=True,
    )


def _remote_account_id(account: dict[str, Any]) -> int | str | None:
    value = account.get("remote_account_id")
    if value is None:
        value = account.get("sub2api_account_id")
    nested = account.get("account") if isinstance(account.get("account"), dict) else {}
    if value is None:
        value = nested.get("id")
    if isinstance(value, bool) or not isinstance(value, (int, str)) or not str(value).strip():
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
