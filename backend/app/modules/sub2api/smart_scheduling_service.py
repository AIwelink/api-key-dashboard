from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from time import monotonic
from typing import Any
from uuid import uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.modules.sub2api.smart_scheduling import (
    build_type_priority_queue,
    default_smart_scheduling_rules,
    evaluate_account,
    normalize_smart_scheduling_rules,
)
from app.modules.sub2api.client import InvalidAdminApiKeyError, Sub2ApiClient
from app.modules.sub2api.postgres_repository import fetch_admin_api_key
from app.utils import now_utc, serialize_doc


SMART_SCHEDULING_SETTING_PREFIX = "smart_scheduling"
SMART_SCHEDULING_LEASE_SECONDS = 300
SMART_SCHEDULING_LEASE_RENEWAL_SECONDS = SMART_SCHEDULING_LEASE_SECONDS // 2
RUN_RETENTION = timedelta(days=90)
OUTCOME_RETENTION = timedelta(days=30)
logger = logging.getLogger("app.sub2api_smart_scheduling")


class _SmartSchedulingLeaseLostError(RuntimeError):
    pass


def smart_scheduling_setting_id(site_id: str) -> str:
    return f"{SMART_SCHEDULING_SETTING_PREFIX}:{str(site_id).strip()}"


async def get_smart_scheduling_settings(
    db: AsyncIOMotorDatabase,
    site_id: str,
) -> dict[str, Any]:
    normalized_site_id = str(site_id).strip()
    document = await db.app_settings.find_one(
        {"_id": smart_scheduling_setting_id(normalized_site_id)}
    )
    rules = normalize_smart_scheduling_rules((document or {}).get("rules"))
    last_run = await db.sub2api_smart_scheduling_runs.find_one(
        {"site_id": normalized_site_id},
        sort=[("started_at", -1)],
    )
    return serialize_doc(
        {
            "site_id": normalized_site_id,
            "rules": rules,
            "default_rules": default_smart_scheduling_rules(),
            "last_run": last_run,
            "updated_at": (document or {}).get("updated_at"),
            "updated_by_user_id": (document or {}).get("updated_by_user_id"),
            "updated_by_name": (document or {}).get("updated_by_name"),
        }
    )


async def update_smart_scheduling_settings(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    rules: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    normalized_site_id = str(site_id).strip()
    normalized_rules = normalize_smart_scheduling_rules(rules)
    updated_at = now_utc()
    await db.app_settings.update_one(
        {"_id": smart_scheduling_setting_id(normalized_site_id)},
        {
            "$set": {
                "site_id": normalized_site_id,
                "rules": normalized_rules,
                "updated_at": updated_at,
                "updated_by_user_id": actor.get("_id"),
                "updated_by_name": (
                    actor.get("name")
                    or actor.get("email")
                    or actor.get("_id")
                ),
            },
            "$setOnInsert": {"created_at": updated_at},
        },
        upsert=True,
    )
    return await get_smart_scheduling_settings(db, normalized_site_id)


async def acquire_smart_scheduling_lease(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    owner: str,
    now: datetime | None = None,
) -> bool:
    acquired_at = _as_utc(now or now_utc())
    lock_id = f"smart-scheduling:{site_id}"
    try:
        document = await db.operation_locks.find_one_and_update(
            {
                "_id": lock_id,
                "$or": [
                    {"expires_at": {"$lte": acquired_at}},
                    {"expires_at": {"$exists": False}},
                    {"owner": owner},
                ],
            },
            {
                "$set": {
                    "lock_type": "smart_scheduling",
                    "site_id": site_id,
                    "owner": owner,
                    "locked_at": acquired_at,
                    "expires_at": acquired_at
                    + timedelta(seconds=SMART_SCHEDULING_LEASE_SECONDS),
                    "updated_at": acquired_at,
                },
                "$setOnInsert": {"created_at": acquired_at},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        return False
    return bool(document and document.get("owner") == owner)


async def release_smart_scheduling_lease(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    owner: str,
) -> None:
    await db.operation_locks.delete_one(
        {
            "_id": f"smart-scheduling:{site_id}",
            "owner": owner,
        }
    )


async def renew_smart_scheduling_lease(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    owner: str,
    now: datetime | None = None,
) -> bool:
    renewed_at = _as_utc(now or now_utc())
    result = await db.operation_locks.update_one(
        {
            "_id": f"smart-scheduling:{site_id}",
            "owner": owner,
            "expires_at": {"$gt": renewed_at},
        },
        {
            "$set": {
                "expires_at": renewed_at
                + timedelta(seconds=SMART_SCHEDULING_LEASE_SECONDS),
                "updated_at": renewed_at,
            }
        },
    )
    return bool(result and result.matched_count == 1)


async def run_smart_scheduling(
    db: AsyncIOMotorDatabase,
    *,
    site: dict[str, Any],
    accounts: list[dict[str, Any]],
    group_settings: dict[int, dict[str, Any]],
    probe_run_id: str,
    rules: dict[str, Any] | None = None,
    client: Sub2ApiClient | Any | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    site_id = str(site.get("id") or site.get("_id") or "").strip()
    evaluated_at = _as_utc(now or now_utc())
    eligible = _eligible_accounts(accounts, group_settings)
    if not eligible:
        return _empty_summary(site_id, status="disabled")

    owner = uuid4().hex
    if not await acquire_smart_scheduling_lease(
        db,
        site_id=site_id,
        owner=owner,
        now=evaluated_at,
    ):
        return _empty_summary(site_id, status="locked")
    lease_renewed_monotonic = monotonic()
    try:
        return await _run_smart_scheduling_locked(
            db,
            site=site,
            site_id=site_id,
            eligible=eligible,
            probe_run_id=probe_run_id,
            rules=rules,
            client=client,
            now=evaluated_at,
            lease_owner=owner,
            lease_renewed_monotonic=lease_renewed_monotonic,
        )
    finally:
        try:
            await release_smart_scheduling_lease(
                db,
                site_id=site_id,
                owner=owner,
            )
        except Exception as exc:  # noqa: BLE001 - the lease expires without masking the run result.
            logger.error(
                "smart_scheduling_lease_release_failed site_id=%s error_type=%s",
                site_id,
                type(exc).__name__,
            )


async def _run_smart_scheduling_locked(
    db: AsyncIOMotorDatabase,
    *,
    site: dict[str, Any],
    site_id: str,
    eligible: dict[str, dict[str, Any]],
    probe_run_id: str,
    rules: dict[str, Any] | None,
    client: Sub2ApiClient | Any | None,
    now: datetime,
    lease_owner: str,
    lease_renewed_monotonic: float,
) -> dict[str, Any]:
    effective_rules = (
        normalize_smart_scheduling_rules(rules)
        if rules is not None
        else (await get_smart_scheduling_settings(db, site_id))["rules"]
    )
    run_id = uuid4().hex
    expires_at = now + RUN_RETENTION
    summary = {
        "ok": True,
        "status": "completed",
        "site_id": site_id,
        "run_id": run_id,
        "probe_run_id": probe_run_id,
        "scanned": 0,
        "changed": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
    }
    await db.sub2api_smart_scheduling_runs.insert_one(
        {
            "_id": run_id,
            **summary,
            "status": "running",
            "started_at": now,
            "created_at": now,
            "expires_at": expires_at,
        }
    )
    states = await _states_for_accounts(
        db,
        site_id=site_id,
        remote_account_ids=[
            item["remote_account_id"] for item in eligible.values()
        ],
    )
    queue_plan = build_type_priority_queue(
        [
            {
                **item,
                "state": states.get(str(item["remote_account_id"])),
            }
            for item in eligible.values()
        ],
        rules=effective_rules,
        now=now,
    )
    effective_client = client
    pending_updates: list[dict[str, Any]] = []

    async def ensure_live_lease() -> bool:
        nonlocal lease_renewed_monotonic
        checked_monotonic = monotonic()
        if (
            checked_monotonic - lease_renewed_monotonic
            < SMART_SCHEDULING_LEASE_RENEWAL_SECONDS
        ):
            return True
        try:
            renewed = await renew_smart_scheduling_lease(
                db,
                site_id=site_id,
                owner=lease_owner,
            )
        except Exception:  # noqa: BLE001 - any renewal failure must stop writes.
            return False
        if renewed:
            lease_renewed_monotonic = checked_monotonic
        return renewed

    for item in eligible.values():
        summary["scanned"] += 1
        account = item["account"]
        remote_account_id = item["remote_account_id"]
        state = states.get(str(remote_account_id))
        queue_entry = queue_plan.get(str(remote_account_id))
        decision = _with_queue_metadata(
            evaluate_account(
                account=account,
                rules=effective_rules,
                type_priority_enabled=item["type_priority_enabled"],
                quota_acceleration_enabled=item["quota_acceleration_enabled"],
                state=state,
                now=now,
                normal_priority=(queue_entry or {}).get("priority"),
            ),
            queue_entry,
        )
        before = _runtime_values(account)
        outcome_status = decision["status"]
        error_code = None
        error_type = None
        stop_remote_updates = False
        client_configuration_failed = False
        latest: dict[str, Any] | None = None

        try:
            if decision["status"] == "change":
                if not await ensure_live_lease():
                    raise _SmartSchedulingLeaseLostError
                if effective_client is None:
                    try:
                        effective_client = await _build_site_client(site)
                    except Exception:
                        client_configuration_failed = True
                        raise
                latest = await effective_client.get_account(remote_account_id)
                latest_account = {
                    **account,
                    **{
                        field: latest[field]
                        for field in ("priority", "concurrency", "load_factor")
                        if field in latest
                    },
                }
                before = _runtime_values(latest_account)
                decision = _with_queue_metadata(
                    evaluate_account(
                        account=latest_account,
                        rules=effective_rules,
                        type_priority_enabled=item["type_priority_enabled"],
                        quota_acceleration_enabled=item["quota_acceleration_enabled"],
                        state=state,
                        now=now,
                        normal_priority=(queue_entry or {}).get("priority"),
                    ),
                    queue_entry,
                )
            if _needs_original_load_factor_capture(decision, state):
                original_load_factor = before.get("load_factor")
                if original_load_factor is None:
                    raise ValueError(
                        "Cannot enter extreme scheduling without a valid load_factor"
                    )
                await _capture_original_load_factor(
                    db,
                    site_id=site_id,
                    remote_account_id=remote_account_id,
                    original_load_factor=original_load_factor,
                    captured_at=now,
                )
                state = {
                    **(state or {}),
                    "original_load_factor": original_load_factor,
                    "original_load_factor_captured_at": now,
                }
                states[str(remote_account_id)] = state
            if decision["status"] == "change":
                pending_updates.append(
                    {
                        "item": item,
                        "decision": decision,
                        "before": before,
                        "group_ids": _account_group_ids(
                            latest,
                            fallback=item["group_ids"],
                        ),
                    }
                )
                continue
            elif decision["status"] == "unchanged":
                summary["unchanged"] += 1
                outcome_status = "unchanged"
                await _persist_scheduler_state(
                    db,
                    site_id=site_id,
                    remote_account_id=remote_account_id,
                    decision=decision,
                    probe_run_id=probe_run_id,
                    run_id=run_id,
                    evaluated_at=now,
                    changed=False,
                    clear_original_load_factor=(
                        _should_clear_original_load_factor(decision, state)
                    ),
                )
            else:
                summary["skipped"] += 1
                outcome_status = decision["status"]
        except Exception as exc:  # noqa: BLE001 - isolate remote failures per account.
            summary["failed"] += 1
            outcome_status = "failed"
            admin_auth_failed = isinstance(exc, InvalidAdminApiKeyError)
            lease_lost = isinstance(exc, _SmartSchedulingLeaseLostError)
            stop_remote_updates = (
                admin_auth_failed or client_configuration_failed or lease_lost
            )
            if lease_lost:
                error_code = "scheduling_lease_lost"
            elif admin_auth_failed:
                error_code = "admin_auth_error"
            elif client_configuration_failed:
                error_code = "admin_api_configuration_error"
            else:
                error_code = "remote_update_failed"
            error_type = type(exc).__name__
            logger.warning(
                "smart_scheduling_account_failed site_id=%s remote_account_id=%s error_type=%s",
                site_id,
                remote_account_id,
                error_type,
            )

        await _persist_outcome(
            db,
            site_id=site_id,
            run_id=run_id,
            probe_run_id=probe_run_id,
            item=item,
            decision=decision,
            before=before,
            status=outcome_status,
            error_code=error_code,
            error_type=error_type,
            evaluated_at=now,
        )
        if stop_remote_updates:
            break

    batches: dict[
        tuple[int, int, int | None, tuple[int, ...]],
        list[dict[str, Any]],
    ] = {}
    for pending in pending_updates:
        target = pending["decision"]["target"]
        load_factor = (
            int(target["load_factor"])
            if target.get("load_factor") is not None
            else None
        )
        key = (
            int(target["priority"]),
            int(target["concurrency"]),
            load_factor,
            tuple(pending["group_ids"]),
        )
        batches.setdefault(key, []).append(pending)

    stopped_error: tuple[str, str] | None = None
    for (priority, concurrency, load_factor, group_ids), batch in batches.items():
        account_ids = [pending["item"]["remote_account_id"] for pending in batch]
        successful_ids: set[str] = set()
        batch_error_code: str | None = None
        batch_error_type: str | None = None
        try:
            if stopped_error is not None:
                batch_error_code, batch_error_type = stopped_error
            else:
                if not await ensure_live_lease():
                    raise _SmartSchedulingLeaseLostError
                payload: dict[str, Any] = {
                    "priority": priority,
                    "concurrency": concurrency,
                    "group_ids": list(group_ids),
                }
                if load_factor is not None:
                    payload["load_factor"] = load_factor
                response = await effective_client.bulk_update_accounts_runtime(
                    account_ids,
                    payload,
                )
                successful_ids = _successful_bulk_account_ids(
                    response,
                    requested_ids=account_ids,
                )
        except Exception as exc:  # noqa: BLE001 - isolate remote failures per batch.
            admin_auth_failed = isinstance(exc, InvalidAdminApiKeyError)
            lease_lost = isinstance(exc, _SmartSchedulingLeaseLostError)
            batch_error_code = (
                "scheduling_lease_lost"
                if lease_lost
                else "admin_auth_error"
                if admin_auth_failed
                else "remote_update_failed"
            )
            batch_error_type = type(exc).__name__
            if admin_auth_failed or lease_lost:
                stopped_error = (batch_error_code, batch_error_type)
            logger.warning(
                "smart_scheduling_batch_failed site_id=%s account_count=%s error_type=%s",
                site_id,
                len(account_ids),
                batch_error_type,
            )

        for pending in batch:
            item = pending["item"]
            decision = pending["decision"]
            remote_account_id = item["remote_account_id"]
            changed = str(remote_account_id) in successful_ids
            if changed:
                summary["changed"] += 1
                outcome_status = "changed"
                error_code = None
                error_type = None
                await _persist_scheduler_state(
                    db,
                    site_id=site_id,
                    remote_account_id=remote_account_id,
                    decision=decision,
                    probe_run_id=probe_run_id,
                    run_id=run_id,
                    evaluated_at=now,
                    changed=True,
                    clear_original_load_factor=(
                        _should_clear_original_load_factor(
                            decision,
                            states.get(str(remote_account_id)),
                        )
                    ),
                )
            else:
                summary["failed"] += 1
                outcome_status = "failed"
                error_code = batch_error_code or "remote_update_failed"
                error_type = batch_error_type or "BulkUpdateAccountFailed"
            await _persist_outcome(
                db,
                site_id=site_id,
                run_id=run_id,
                probe_run_id=probe_run_id,
                item=item,
                decision=decision,
                before=pending["before"],
                status=outcome_status,
                error_code=error_code,
                error_type=error_type,
                evaluated_at=now,
            )

    finished_at = _as_utc(now_utc()) if now_utc is not None else now
    summary["status"] = "partial" if summary["failed"] else "completed"
    await db.sub2api_smart_scheduling_runs.update_one(
        {"_id": run_id},
        {
            "$set": {
                **summary,
                "finished_at": finished_at,
                "updated_at": finished_at,
            }
        },
    )
    return summary


def _eligible_accounts(
    accounts: list[dict[str, Any]],
    group_settings: dict[int, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    eligible: dict[str, dict[str, Any]] = {}
    for account in accounts:
        remote_account_id = account.get("remote_account_id")
        if remote_account_id is None:
            remote_account_id = account.get("id")
        if remote_account_id is None:
            continue
        group_ids = {
            group_id
            for value in account.get("group_ids") or []
            if (group_id := _optional_int(value)) is not None
        }
        type_enabled = any(
            group_settings.get(group_id, {}).get("type_priority_enabled") is True
            for group_id in group_ids
        )
        quota_enabled = any(
            group_settings.get(group_id, {}).get("quota_acceleration_enabled") is True
            for group_id in group_ids
        )
        if not type_enabled and not quota_enabled:
            continue
        key = str(remote_account_id)
        existing = eligible.get(key)
        if existing is None:
            eligible[key] = {
                "account": account,
                "remote_account_id": remote_account_id,
                "group_ids": sorted(group_ids),
                "type_priority_enabled": type_enabled,
                "quota_acceleration_enabled": quota_enabled,
            }
        else:
            existing["group_ids"] = sorted(set(existing["group_ids"]) | group_ids)
            existing["type_priority_enabled"] = (
                existing["type_priority_enabled"] or type_enabled
            )
            existing["quota_acceleration_enabled"] = (
                existing["quota_acceleration_enabled"] or quota_enabled
            )
    return eligible


async def _states_for_accounts(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account_ids: list[Any],
) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    cursor = db.sub2api_smart_scheduling_states.find(
        {
            "site_id": site_id,
            "remote_account_id": {"$in": remote_account_ids},
        },
        {
            "remote_account_id": 1,
            "mode": 1,
            "seven_day_reset_at": 1,
            "rate_limit_detected_at": 1,
            "original_load_factor": 1,
            "original_load_factor_captured_at": 1,
        },
    )
    async for document in cursor:
        states[str(document.get("remote_account_id"))] = document
    return states


async def _persist_scheduler_state(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account_id: Any,
    decision: dict[str, Any],
    probe_run_id: str,
    run_id: str,
    evaluated_at: datetime,
    changed: bool,
    clear_original_load_factor: bool = False,
) -> None:
    updates = {
        "site_id": site_id,
        "remote_account_id": remote_account_id,
        "adapted_type": decision.get("adapted_type"),
        "mode": decision.get("mode"),
        "last_strategy": decision.get("strategy"),
        "last_reason": decision.get("reason"),
        "last_target": decision.get("target"),
        "seven_day_used_percent": decision.get("seven_day_used_percent"),
        "seven_day_reset_at": decision.get("seven_day_reset_at"),
        "rate_limit_detected_at": decision.get("rate_limit_detected_at"),
        "queue_partition": decision.get("queue_partition"),
        "queue_index": decision.get("queue_index"),
        "queue_priority": decision.get("queue_priority"),
        "queue_created_at": decision.get("queue_created_at"),
        "last_probe_run_id": probe_run_id,
        "last_run_id": run_id,
        "last_evaluated_at": evaluated_at,
        "updated_at": evaluated_at,
    }
    if changed:
        updates["last_successful_update_at"] = evaluated_at
    update: dict[str, Any] = {
        "$set": updates,
        "$setOnInsert": {"created_at": evaluated_at},
    }
    if clear_original_load_factor:
        update["$unset"] = {
            "original_load_factor": "",
            "original_load_factor_captured_at": "",
        }
    await db.sub2api_smart_scheduling_states.update_one(
        {"_id": f"{site_id}:{remote_account_id}"},
        update,
        upsert=True,
    )


async def _capture_original_load_factor(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account_id: Any,
    original_load_factor: int,
    captured_at: datetime,
) -> None:
    state_id = f"{site_id}:{remote_account_id}"
    capture = {
        "original_load_factor": original_load_factor,
        "original_load_factor_captured_at": captured_at,
    }
    await db.sub2api_smart_scheduling_states.update_one(
        {"_id": state_id},
        {
            "$set": {"updated_at": captured_at},
            "$setOnInsert": {
                "site_id": site_id,
                "remote_account_id": remote_account_id,
                **capture,
                "created_at": captured_at,
            },
        },
        upsert=True,
    )
    await db.sub2api_smart_scheduling_states.update_one(
        {
            "_id": state_id,
            "$or": [
                {"original_load_factor": {"$exists": False}},
                {"original_load_factor": None},
            ],
        },
        {"$set": {**capture, "updated_at": captured_at}},
        upsert=False,
    )


async def _persist_outcome(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    run_id: str,
    probe_run_id: str,
    item: dict[str, Any],
    decision: dict[str, Any],
    before: dict[str, int | None],
    status: str,
    error_code: str | None,
    error_type: str | None,
    evaluated_at: datetime,
) -> None:
    remote_account_id = item["remote_account_id"]
    await db.sub2api_smart_scheduling_outcomes.update_one(
        {"_id": f"{run_id}:{remote_account_id}"},
        {
            "$set": {
                "site_id": site_id,
                "run_id": run_id,
                "probe_run_id": probe_run_id,
                "remote_account_id": remote_account_id,
                "group_ids": item["group_ids"],
                "adapted_type": decision.get("adapted_type"),
                "strategy": decision.get("strategy"),
                "mode": decision.get("mode"),
                "reason": decision.get("reason"),
                "seven_day_used_percent": decision.get(
                    "seven_day_used_percent"
                ),
                "seven_day_reset_at": decision.get("seven_day_reset_at"),
                "quota_fresh": decision.get("quota_fresh"),
                "queue_partition": decision.get("queue_partition"),
                "queue_index": decision.get("queue_index"),
                "queue_priority": decision.get("queue_priority"),
                "queue_created_at": decision.get("queue_created_at"),
                "before": before,
                "target": decision.get("target"),
                "status": status,
                "error_code": error_code,
                "error_type": error_type,
                "evaluated_at": evaluated_at,
                "expires_at": evaluated_at + OUTCOME_RETENTION,
                "updated_at": evaluated_at,
            },
            "$setOnInsert": {"created_at": evaluated_at},
        },
        upsert=True,
    )


def _with_queue_metadata(
    decision: dict[str, Any],
    queue_entry: dict[str, Any] | None,
) -> dict[str, Any]:
    if not queue_entry:
        return decision
    return decision | {
        "queue_partition": queue_entry["queue_partition"],
        "queue_index": queue_entry["queue_index"],
        "queue_priority": queue_entry["priority"],
        "queue_created_at": queue_entry["queue_created_at"],
    }


async def _build_site_client(site: dict[str, Any]) -> Sub2ApiClient:
    sql_dsn = str(site.get("sql_dsn") or "").strip()
    base_url = str(site.get("base_url") or "").strip()
    if not sql_dsn:
        raise ValueError("Sub2API site SQL_DSN is not configured")
    if not base_url:
        raise ValueError("Sub2API site base_url is not configured")
    token = await fetch_admin_api_key(sql_dsn)
    return Sub2ApiClient(
        base_url=base_url,
        token=token,
    )


def _runtime_values(account: dict[str, Any]) -> dict[str, int | None]:
    return {
        "priority": _optional_int(account.get("priority")),
        "concurrency": _optional_int(account.get("concurrency")),
        "load_factor": _optional_int(account.get("load_factor")),
    }


def _needs_original_load_factor_capture(
    decision: dict[str, Any],
    state: dict[str, Any] | None,
) -> bool:
    target = decision.get("target")
    return bool(
        isinstance(target, dict)
        and target.get("load_factor") is not None
        and decision.get("mode") in {"extreme", "rate_limit_pending"}
        and _optional_int((state or {}).get("original_load_factor")) is None
    )


def _should_clear_original_load_factor(
    decision: dict[str, Any],
    state: dict[str, Any] | None,
) -> bool:
    target = decision.get("target")
    return bool(
        isinstance(target, dict)
        and target.get("load_factor") is not None
        and decision.get("strategy")
        in {"quota_recovery", "rate_limit_recovery"}
        and decision.get("mode") in {"normal", "rate_limited_cooldown"}
        and _optional_int((state or {}).get("original_load_factor")) is not None
    )


def _account_group_ids(
    account: dict[str, Any] | None,
    *,
    fallback: list[int],
) -> list[int]:
    raw_values = account.get("group_ids") if isinstance(account, dict) else None
    values = raw_values if isinstance(raw_values, list) else fallback
    return sorted(
        {
            group_id
            for value in values
            if (group_id := _optional_int(value)) is not None
        }
    )


def _successful_bulk_account_ids(
    response: dict[str, Any],
    *,
    requested_ids: list[Any],
) -> set[str]:
    requested = {str(account_id) for account_id in requested_ids}
    successful = {
        str(account_id)
        for account_id in response.get("success_ids") or []
        if str(account_id) in requested
    }
    failed = {
        str(account_id)
        for account_id in response.get("failed_ids") or []
        if str(account_id) in requested
    }
    for result in response.get("results") or []:
        if not isinstance(result, dict) or result.get("account_id") is None:
            continue
        account_id = str(result["account_id"])
        if account_id not in requested:
            continue
        if result.get("success") is True:
            successful.add(account_id)
        elif result.get("success") is False:
            failed.add(account_id)
    successful -= failed
    if not successful and not failed:
        success_count = _optional_int(response.get("success"))
        failed_count = _optional_int(response.get("failed"))
        if success_count == len(requested) and failed_count == 0:
            return requested
    return successful


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed_float = float(value)
        parsed = int(parsed_float)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed_float else None


def _empty_summary(site_id: str, *, status: str) -> dict[str, Any]:
    return {
        "ok": status != "locked",
        "status": status,
        "site_id": site_id,
        "run_id": None,
        "scanned": 0,
        "changed": 0,
        "unchanged": 0,
        "skipped": 0,
        "failed": 0,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
