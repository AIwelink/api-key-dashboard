from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from app.modules.notifications.service import send_notification_event
from app.modules.sub2api.account_history import (
    build_history_change,
    dynamic_snapshot,
    ensure_daily_checkpoint,
    persist_history_changes,
    snapshot_hash,
)
from app.modules.sub2api.client import Sub2ApiClient
from app.modules.sub2api.cache import _get_or_update_group_capacity_summary, get_site, is_bug_team_account, is_sub2api_site, list_sites
from app.utils import now_utc, serialize_doc


logger = logging.getLogger("app.sub2api_account_probe")

DEFAULT_PROBE_INTERVAL_SECONDS = 180
DEFAULT_SAMPLE_RETENTION_DAYS = 14
DEFAULT_MISSING_CONFIRM_COUNT = 3
CONFIRMED_401_RECOVERY_COUNT = 3
PROBE_LOOP_SLEEP_SECONDS = 30
ACCOUNT_LIST_PAGE_SIZE = 200
MAX_ACCOUNT_LIST_PAGES = 100

STATUS_NORMAL = {"active", "ok", "healthy", "normal", "available"}
STATUS_ABNORMAL = {"abnormal", "error", "failed", "disabled", "inactive", "invalid", "revoked"}
ERROR_401_PATTERN = re.compile(
    r"401|token[_ -]?invalidated|token[_ -]?revoked|token refresh failed|refresh token|OPENAI_OAUTH_TOKEN_REFRESH_FAILED|authentication failed|invalid_request_error",
    re.I,
)
PRO_MARKER_PATTERN = re.compile(r"(^|[^a-z0-9])(?:pro|20x)(?:[^a-z0-9]|$)", re.I)
SPARK_SHADOW_NAME_PATTERN = re.compile(r"\(spark\)\s*$", re.I)
NOTIFICATION_401_THROTTLE_SECONDS = 180
OFFICIAL_REFRESH_NOTIFICATION_DEDUPE_HOURS = 24
OFFICIAL_REFRESH_MIN_ACCOUNT_COUNT = 2
OFFICIAL_REFRESH_MIN_ACCOUNT_RATIO = 0.8
SHANGHAI_TZ = timezone(timedelta(hours=8))
USAGE_WEEKLY_ROLLOVER_FIELDS = (
    "codex_7d_actual_cost",
    "codex_7d_total_cost",
    "codex_7d_request_count",
    "codex_7d_token_count",
)
USAGE_TOTAL_ROLLOVER_FIELDS = (
    "codex_total_actual_cost",
    "codex_total_cost",
    "codex_total_request_count",
    "codex_total_token_count",
)

_probe_tasks: dict[str, asyncio.Task[dict[str, Any]]] = {}
_probe_tasks_lock = asyncio.Lock()


def default_group_observability_setting(site_id: str, group_id: int, group_name: str | None = None) -> dict[str, Any]:
    now = now_utc()
    lower_name = (group_name or "").lower()
    likely_free = "free" in lower_name
    return {
        "_id": f"{site_id}:{group_id}",
        "site_id": site_id,
        "group_id": group_id,
        "group_name": group_name or f"#{group_id}",
        "enabled": True,
        "detailed_enabled": not likely_free,
        "probe_interval_seconds": DEFAULT_PROBE_INTERVAL_SECONDS,
        "sample_retention_days": 7 if likely_free else DEFAULT_SAMPLE_RETENTION_DAYS,
        "record_usage_samples": not likely_free,
        "record_status_events": True,
        "record_duplicate_email_warning": True,
        "capacity_notification_enabled": False,
        "capacity_notification_threshold": "tight",
        "capacity_notification_cooldown_minutes": 60,
        "uptime_kuma_monitor_url": "",
        "missing_confirm_count": DEFAULT_MISSING_CONFIRM_COUNT,
        "status": "active",
        "created_at": now,
        "updated_at": now,
    }


async def list_group_observability_settings(db: AsyncIOMotorDatabase, site_id: str) -> dict[str, Any]:
    settings = {
        int(doc["group_id"]): doc
        async for doc in db.group_observability_settings.find({"site_id": site_id}).sort("group_id", 1)
        if isinstance(doc.get("group_id"), int)
    }
    group_docs = db.sub2api_groups_cache.find({"site_id": site_id}).sort("group_id", 1)
    notification_meta = {
        int(doc["group_id"]): doc
        async for doc in db.sub2api_capacity_notification_meta.find({"site_id": site_id})
        if isinstance(doc.get("group_id"), int)
    }
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    async for group_doc in group_docs:
        group_id = group_doc.get("group_id")
        if not isinstance(group_id, int):
            continue
        seen.add(group_id)
        group = group_doc.get("group") if isinstance(group_doc.get("group"), dict) else {}
        group_name = str(group.get("name") or f"#{group_id}")
        setting = settings.get(group_id) or default_group_observability_setting(site_id, group_id, group_name)
        setting["group_name"] = setting.get("group_name") or group_name
        setting["group_account_count"] = group.get("account_count")
        setting["group_active_account_count"] = group.get("active_account_count")
        meta = notification_meta.get(group_id, {})
        setting["capacity_notification_last_at"] = meta.get("last_attempt_at")
        setting["capacity_notification_last_status"] = meta.get("last_delivery_status")
        setting["capacity_notification_last_health_status"] = meta.get("last_notified_status")
        items.append(serialize_doc(setting))
    for group_id, setting in settings.items():
        if group_id not in seen:
            items.append(serialize_doc(setting))
    return {"items": items, "total": len(items)}


async def update_group_observability_setting(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    payload: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    group_doc = await db.sub2api_groups_cache.find_one({"site_id": site_id, "group_id": group_id})
    group = group_doc.get("group") if group_doc and isinstance(group_doc.get("group"), dict) else {}
    group_name = str(group.get("name") or f"#{group_id}")
    base = default_group_observability_setting(site_id, group_id, group_name)
    now = now_utc()
    allowed = {
        "enabled",
        "detailed_enabled",
        "probe_interval_seconds",
        "sample_retention_days",
        "record_usage_samples",
        "record_status_events",
        "record_duplicate_email_warning",
        "capacity_notification_enabled",
        "capacity_notification_threshold",
        "capacity_notification_cooldown_minutes",
        "uptime_kuma_monitor_url",
    }
    updates = {key: payload[key] for key in allowed if key in payload and payload[key] is not None}
    updates["group_name"] = group_name
    updates["updated_at"] = now
    updates["updated_by"] = actor.get("_id")
    insert_defaults = {key: value for key, value in base.items() if key not in updates}
    await db.group_observability_settings.update_one(
        {"_id": base["_id"]},
        {"$setOnInsert": insert_defaults, "$set": updates},
        upsert=True,
    )
    doc = await db.group_observability_settings.find_one({"_id": base["_id"]})
    return serialize_doc(doc or {})


async def list_duplicate_email_alerts(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None = None,
    group_id: int | None = None,
    include_read: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    query: dict[str, Any] = {
        "duplicate_remote_count": {"$gt": 1},
        "current_presence": "present",
    }
    if site_id:
        query["site_id"] = site_id
    if group_id is not None:
        query["current_group_ids"] = group_id
    sites = await _alert_site_map(db)
    group_names = await _alert_group_name_map(db)
    cursor = db.remote_account_identities.find(query).sort([("updated_at", -1), ("last_seen_at", -1)])
    items: list[dict[str, Any]] = []
    async for doc in cursor:
        item = _duplicate_email_alert_item(doc, sites=sites, group_names=group_names)
        if not include_read and item.get("is_read"):
            continue
        items.append(item)
    items.sort(key=_alert_sort_key)
    total = len(items)
    items = items[:limit]
    return {"items": items, "total": total, "site_id": site_id, "group_id": group_id, "include_read": include_read}


async def mark_duplicate_email_alert_read(
    db: AsyncIOMotorDatabase,
    *,
    alert_id: str,
    actor: dict[str, Any],
    note: str | None = None,
) -> dict[str, Any] | None:
    doc = await db.remote_account_identities.find_one(
        {
            "_id": alert_id,
            "duplicate_remote_count": {"$gt": 1},
            "current_presence": "present",
        }
    )
    if not doc:
        return None
    now = now_utc()
    remote_ids = _alert_remote_ids(doc)
    signature = _duplicate_email_alert_signature(remote_ids)
    updates = {
        "duplicate_email_alert_read_at": now,
        "duplicate_email_alert_read_by": actor.get("_id"),
        "duplicate_email_alert_read_by_name": actor.get("name") or actor.get("email") or actor.get("_id"),
        "duplicate_email_alert_read_signature": signature,
        "duplicate_email_alert_read_note": note,
        "updated_at": now,
    }
    await db.remote_account_identities.update_one({"_id": alert_id}, {"$set": updates})
    updated = await db.remote_account_identities.find_one({"_id": alert_id})
    sites = await _alert_site_map(db)
    group_names = await _alert_group_name_map(db)
    return _duplicate_email_alert_item(updated or (doc | updates), sites=sites, group_names=group_names)


async def _alert_site_map(db: AsyncIOMotorDatabase) -> dict[str, dict[str, Any]]:
    sites: dict[str, dict[str, Any]] = {}
    async for doc in db.sub2api_sites.find({"status": {"$ne": "deleted"}}):
        site_id = str(doc.get("_id") or "")
        if not site_id:
            continue
        site = serialize_doc(doc | {"id": site_id})
        site.pop("token", None)
        site["token_configured"] = bool(doc.get("token"))
        sites[site_id] = site
    return sites


async def _alert_group_name_map(db: AsyncIOMotorDatabase) -> dict[tuple[str, int], str]:
    group_names: dict[tuple[str, int], str] = {}
    async for doc in db.sub2api_groups_cache.find({}, {"site_id": 1, "group_id": 1, "group.name": 1}):
        site_id = doc.get("site_id")
        group_id = doc.get("group_id")
        group = doc.get("group") if isinstance(doc.get("group"), dict) else {}
        if isinstance(site_id, str) and isinstance(group_id, int):
            group_names[(site_id, group_id)] = str(group.get("name") or f"#{group_id}")
    return group_names


def _duplicate_email_alert_item(doc: dict[str, Any], *, sites: dict[str, dict[str, Any]], group_names: dict[tuple[str, int], str]) -> dict[str, Any]:
    item = serialize_doc(doc)
    site_id = str(doc.get("site_id") or "")
    site = sites.get(site_id, {})
    group_ids = [group_id for group_id in doc.get("current_group_ids") or [] if isinstance(group_id, int)]
    remote_ids = _alert_remote_ids(doc)
    signature = _duplicate_email_alert_signature(remote_ids)
    read_signature = str(doc.get("duplicate_email_alert_read_signature") or "")
    read_at = doc.get("duplicate_email_alert_read_at")
    item["alert_type"] = "duplicate_email"
    item["alert_label"] = "同邮箱多个 sub2 账号"
    item["alert_category"] = "账号"
    item["alert_severity"] = "warning"
    item["alert_at"] = serialize_doc(doc.get("last_seen_at") or doc.get("updated_at"))
    item["message"] = "同一个邮箱在 sub2 中存在多个 remote id，容量预估按一个账号计算，用量按多个 id 加和。"
    item["site_name"] = site.get("name") or site_id or "-"
    item["site_base_url"] = site.get("base_url")
    item["group_names"] = [group_names.get((site_id, group_id), f"#{group_id}") for group_id in group_ids]
    item["is_read"] = bool(read_at and read_signature == signature)
    item["read_at"] = serialize_doc(read_at)
    item["read_by_name"] = doc.get("duplicate_email_alert_read_by_name")
    item["read_note"] = doc.get("duplicate_email_alert_read_note")
    item["read_signature"] = read_signature or None
    item["alert_signature"] = signature
    return item


def _alert_sort_key(item: dict[str, Any]) -> tuple[int, float]:
    alert_at = _parse_datetime(item.get("alert_at") or item.get("last_seen_at") or item.get("updated_at"))
    timestamp = alert_at.timestamp() if alert_at else 0.0
    return (1 if item.get("is_read") else 0, -timestamp)


def _alert_remote_ids(doc: dict[str, Any]) -> list[Any]:
    values = doc.get("current_remote_account_ids")
    if isinstance(values, list) and values:
        return [item for item in values if item is not None and item != ""]
    fallback = doc.get("current_remote_account_id")
    return [fallback] if fallback is not None and fallback != "" else []


def _duplicate_email_alert_signature(remote_ids: list[Any]) -> str:
    return ",".join(sorted(str(item) for item in remote_ids))


def _normalized_remote_id_signature(remote_ids: Any) -> list[str]:
    return [str(item) for item in _stable_remote_ids(remote_ids)]


async def probe_scheduler_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        try:
            await probe_due_sites(db)
            await flush_due_401_notification_batches(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sub2api_account_probe_scheduler_failed")
        await asyncio.sleep(PROBE_LOOP_SLEEP_SECONDS)


async def probe_due_sites(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    sites = (await list_sites(db, site_type="sub2api")).get("items", [])
    results: list[dict[str, Any]] = []
    for site in sites:
        if not site or site.get("status") != "active":
            continue
        site_id = str(site.get("id"))
        try:
            due_group_ids = await _due_group_ids(db, site_id)
            if due_group_ids:
                results.append(await probe_site_accounts(db, site_id=site_id, group_ids=due_group_ids))
        except Exception as exc:  # noqa: BLE001 - each site is independent.
            logger.warning("sub2api_account_probe_site_failed site_id=%s error=%s", site_id, exc)
            results.append({"ok": False, "site_id": site_id, "message": str(exc)})
    return {"ok": True, "results": results, "probed": sum(1 for item in results if item.get("ok") is True)}


async def probe_site_accounts(db: AsyncIOMotorDatabase, *, site_id: str, group_ids: list[int] | None = None) -> dict[str, Any]:
    async with _probe_tasks_lock:
        current = _probe_tasks.get(site_id)
        if current and not current.done():
            task = current
        else:
            task = asyncio.create_task(_run_site_account_probe(db, site_id=site_id, group_ids=group_ids))
            _probe_tasks[site_id] = task
    try:
        return await task
    finally:
        async with _probe_tasks_lock:
            if _probe_tasks.get(site_id) is task and task.done():
                _probe_tasks.pop(site_id, None)


async def _run_site_account_probe(db: AsyncIOMotorDatabase, *, site_id: str, group_ids: list[int] | None = None) -> dict[str, Any]:
    site = await get_site(db, site_id, include_token=True)
    if not site:
        return {"ok": False, "site_id": site_id, "message": "sub2api site not found"}
    if not is_sub2api_site(site):
        return {"ok": False, "site_id": site_id, "message": "site is not a sub2api client"}
    run_id = secrets.token_hex(12)
    started_at = now_utc()
    await db.remote_account_probe_runs.insert_one(
        {
            "_id": run_id,
            "site_id": site_id,
            "started_at": started_at,
            "status": "running",
            "created_at": started_at,
        }
    )
    counters = {
        "accounts_seen": 0,
        "accounts_new": 0,
        "accounts_changed": 0,
        "accounts_401": 0,
        "accounts_missing_suspected": 0,
        "accounts_removed_confirmed": 0,
        "duplicate_email_count": 0,
    }
    try:
        client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
        settings = await _settings_for_site(db, site_id)
        enabled_group_ids = {group_id for group_id, setting in settings.items() if setting.get("enabled") is not False}
        if group_ids is not None:
            enabled_group_ids &= set(group_ids)
        if settings and not enabled_group_ids:
            finished_at = now_utc()
            await db.remote_account_probe_runs.update_one(
                {"_id": run_id},
                {
                    "$set": {
                        "status": "succeeded",
                        "finished_at": finished_at,
                        "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
                        "group_ids_checked": [],
                        "message": "no enabled groups due",
                        **counters,
                    }
                },
            )
            await db.remote_account_probe_meta.update_one(
                {"_id": site_id},
                {"$set": {"site_id": site_id, "last_probe_at": finished_at, "last_run_id": run_id, "status": "succeeded", "updated_at": finished_at}},
                upsert=True,
            )
            return {"ok": True, "site_id": site_id, "run_id": run_id, "group_ids_checked": [], "message": "no enabled groups due", **counters}
        accounts = [_normalize_probe_account(item) for item in await _fetch_all_accounts(client)]
        fetched_at = now_utc()
        identity_accounts = [account for account in accounts if not _is_spark_shadow_account(account)]
        all_seen_identity_ids = {
            _identity_id(site_id, str(account.get("normalized_email")))
            for account in identity_accounts
            if account.get("normalized_email")
        }
        filtered_accounts = [account for account in identity_accounts if _account_in_enabled_groups(account, enabled_group_ids)]
        counters["accounts_seen"] = len(filtered_accounts)

        by_email: dict[str, list[dict[str, Any]]] = {}
        for account in filtered_accounts:
            email = account.get("normalized_email")
            if email:
                by_email.setdefault(email, []).append(account)
        for email, same_email_accounts in by_email.items():
            if len({str(item.get("remote_account_id")) for item in same_email_accounts}) > 1:
                counters["duplicate_email_count"] += 1

        seen_identity_ids: set[str] = set()
        seen_remote_ids: set[Any] = set()
        official_refresh_accounts: list[dict[str, Any]] = []
        official_refresh_eligible_accounts: dict[str, int] = {}
        history_changes: list[dict[str, Any]] = []
        accounts_for_identity = _collapse_probe_accounts_by_email(filtered_accounts)
        for account in accounts_for_identity:
            remote_id = account.get("remote_account_id")
            normalized_email = account.get("normalized_email")
            if not remote_id or not normalized_email:
                continue
            setting = _setting_for_account(settings, account)
            identity_id = _identity_id(site_id, normalized_email)
            seen_identity_ids.add(identity_id)
            seen_remote_ids.add(remote_id)
            previous_identity = await db.remote_account_identities.find_one({"_id": identity_id})
            account["plan_type"], account["plan_type_source"] = _resolved_probe_plan_type(
                account.get("plan_type"),
                (previous_identity or {}).get("plan_type"),
            )
            history_change, history_baseline_override = _prepare_history_change(
                site_id=site_id,
                account=account,
                identity=previous_identity,
                setting=setting,
            )
            if history_change is not None:
                history_changes.append(history_change)
            official_refresh = _official_usage_refresh_state(
                previous_snapshot=(previous_identity or {}).get("last_usage_snapshot"),
                current_snapshot=account.get("usage_snapshot") or {},
                detected_at=fetched_at,
            )
            if official_refresh["comparable"]:
                account_type = _official_refresh_account_type(account)
                official_refresh_eligible_accounts[account_type] = official_refresh_eligible_accounts.get(account_type, 0) + 1
            session = await _ensure_session(db, site_id=site_id, account=account, identity=previous_identity, detected_at=fetched_at)
            is_new = previous_identity is None
            changed = await _update_identity_and_events(
                db,
                site_id=site_id,
                account=account,
                identity=previous_identity,
                session=session,
                setting=setting,
                detected_at=fetched_at,
                history_baseline_override=history_baseline_override,
            )
            if is_new:
                counters["accounts_new"] += 1
            if changed:
                counters["accounts_changed"] += 1
            if _is_401(account):
                counters["accounts_401"] += 1
            if official_refresh["detected"]:
                official_refresh_accounts.append(account | {"official_refresh": official_refresh})

        history_summary = await persist_history_changes(
            db,
            site_id=site_id,
            run_id=run_id,
            observed_at=fetched_at,
            changes=history_changes,
        )
        counters["history_changed_accounts"] = history_summary["changed_accounts"]
        counters["history_change_fields"] = history_summary["changed_fields"]
        counters["history_batches"] = history_summary["batches"]

        refresh_consensus = _official_refresh_consensus(
            official_refresh_accounts,
            eligible_account_counts=official_refresh_eligible_accounts,
        )
        if refresh_consensus["confirmed"]:
            confirmed_accounts = refresh_consensus["confirmed_accounts"]
            try:
                await _write_official_refresh_events(
                    db,
                    site_id=site_id,
                    detected_at=fetched_at,
                    accounts=confirmed_accounts,
                    consensus=refresh_consensus,
                )
                await _notify_official_usage_refresh(
                    db,
                    site_id=site_id,
                    detected_at=fetched_at,
                    accounts=confirmed_accounts,
                    consensus=refresh_consensus,
                )
            except Exception as exc:  # noqa: BLE001 - notification failures must not fail a probe run.
                logger.warning("sub2api_official_usage_refresh_notification_failed site_id=%s error=%s", site_id, exc)

        missing_counts = await _mark_missing_identities(
            db,
            site_id=site_id,
            seen_identity_ids=all_seen_identity_ids,
            group_ids=enabled_group_ids,
            detected_at=fetched_at,
        )
        counters.update(missing_counts)
        try:
            checkpoint_result = await ensure_daily_checkpoint(
                db,
                site_id=site_id,
                checkpoint_at=fetched_at,
            )
            counters["daily_checkpoint_created"] = int(checkpoint_result.get("status") == "created")
            counters["daily_checkpoint_accounts"] = int(checkpoint_result.get("accounts") or 0)
        except Exception as exc:  # noqa: BLE001 - checkpoint history must not fail account probing.
            counters["daily_checkpoint_created"] = 0
            counters["daily_checkpoint_accounts"] = 0
            counters["daily_checkpoint_error"] = str(exc) or exc.__class__.__name__
            logger.warning("sub2api_account_checkpoint_failed site_id=%s error=%s", site_id, exc)
        finished_at = now_utc()
        group_ids_checked = sorted(enabled_group_ids)
        await db.remote_account_probe_runs.update_one(
            {"_id": run_id},
            {
                "$set": {
                    "status": "succeeded",
                    "finished_at": finished_at,
                    "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
                    "group_ids_checked": group_ids_checked,
                    **counters,
                }
            },
        )
        await db.group_observability_settings.update_many(
            {"site_id": site_id, "group_id": {"$in": group_ids_checked}},
            {"$set": {"last_probe_at": finished_at, "last_run_id": run_id, "updated_at": finished_at}},
        )
        await db.remote_account_probe_meta.update_one(
            {"_id": site_id},
            {"$set": {"site_id": site_id, "last_probe_at": finished_at, "last_run_id": run_id, "status": "succeeded", "updated_at": finished_at}},
            upsert=True,
        )
        logger.info("sub2api_account_probe_finished site_id=%s accounts=%s changed=%s 401=%s", site_id, counters["accounts_seen"], counters["accounts_changed"], counters["accounts_401"])
        return {"ok": True, "site_id": site_id, "run_id": run_id, "group_ids_checked": group_ids_checked, **counters}
    except Exception as exc:
        finished_at = now_utc()
        message = str(exc) or exc.__class__.__name__
        await db.remote_account_probe_runs.update_one(
            {"_id": run_id},
            {"$set": {"status": "failed", "finished_at": finished_at, "error_message": message}},
        )
        await db.remote_account_probe_meta.update_one(
            {"_id": site_id},
            {"$set": {"site_id": site_id, "status": "failed", "message": message, "updated_at": finished_at}},
            upsert=True,
        )
        raise


async def _due_group_ids(db: AsyncIOMotorDatabase, site_id: str) -> list[int]:
    settings = await _settings_for_site(db, site_id)
    now = now_utc()
    due: list[int] = []
    for group_id, setting in settings.items():
        if setting.get("enabled") is False:
            continue
        last_probe_at = _parse_datetime(setting.get("last_probe_at"))
        interval_seconds = max(60, int(setting.get("probe_interval_seconds") or DEFAULT_PROBE_INTERVAL_SECONDS))
        if not last_probe_at or now - last_probe_at >= timedelta(seconds=interval_seconds):
            due.append(group_id)
    return due


async def _settings_for_site(db: AsyncIOMotorDatabase, site_id: str) -> dict[int, dict[str, Any]]:
    settings = {
        int(doc["group_id"]): doc
        async for doc in db.group_observability_settings.find({"site_id": site_id})
        if isinstance(doc.get("group_id"), int)
    }
    group_docs = db.sub2api_groups_cache.find({"site_id": site_id})
    ops = []
    async for group_doc in group_docs:
        group_id = group_doc.get("group_id")
        if not isinstance(group_id, int) or group_id in settings:
            continue
        group = group_doc.get("group") if isinstance(group_doc.get("group"), dict) else {}
        default_setting = default_group_observability_setting(site_id, group_id, str(group.get("name") or f"#{group_id}"))
        settings[group_id] = default_setting
        ops.append(UpdateOne({"_id": default_setting["_id"]}, {"$setOnInsert": default_setting}, upsert=True))
    if ops:
        await db.group_observability_settings.bulk_write(ops, ordered=False)
    return settings


async def _fetch_all_accounts(client: Sub2ApiClient) -> list[dict[str, Any]]:
    accounts: list[dict[str, Any]] = []
    total: int | None = None
    page = 1
    while page <= MAX_ACCOUNT_LIST_PAGES:
        data = await client.list_accounts(page=page, page_size=ACCOUNT_LIST_PAGE_SIZE, sort_by="last_used_at", sort_order="asc", timezone="Asia/Shanghai")
        items = data.get("items", [])
        if total is None and isinstance(data.get("total"), int):
            total = int(data["total"])
        accounts.extend([item for item in items if isinstance(item, dict)])
        if not items:
            break
        if total is not None and page * ACCOUNT_LIST_PAGE_SIZE >= total:
            break
        page += 1
    return accounts


def _normalize_probe_account(account: dict[str, Any]) -> dict[str, Any]:
    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    email = _first_present(account, credentials, extra, "email")
    group_ids = _extract_group_ids(account)
    normalized = {
        "remote_account_id": account.get("id"),
        "email": str(email).strip() if email else "",
        "normalized_email": str(email).strip().lower() if email else "",
        "name": account.get("name"),
        "status": _first_present(account, extra, "status", "account_status", "state", "sub2api_status"),
        "schedulable": _bool_or_none(_first_present(account, extra, "schedulable", "is_schedulable", "sub2api_schedulable")),
        "error_message": _first_present(account, extra, "error_message", "last_error", "error", "message"),
        "group_ids": group_ids,
        "plan_type": "bug_team" if is_bug_team_account(account) else _first_present(account, credentials, extra, "plan_type"),
        "last_used_at": _first_present(account, extra, "last_used_at"),
        "updated_at": _first_present(account, extra, "updated_at"),
        "usage_snapshot": _usage_snapshot(account, extra),
        "subscription_snapshot": _subscription_snapshot(account, credentials, extra),
        "raw_hash": _stable_hash(_compact_raw(account)),
    }
    return normalized


def _subscription_snapshot(
    account: dict[str, Any],
    credentials: dict[str, Any],
    extra: dict[str, Any],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    date_fields = (
        "subscription_expires_at",
        "chatgpt_subscription_active_start",
        "chatgpt_subscription_active_until",
        "chatgpt_subscription_last_checked",
    )
    for field in date_fields:
        parsed = _parse_datetime(_first_present(account, credentials, extra, field))
        if parsed is not None:
            result[field] = parsed.astimezone(UTC)
    credential_expires_at = _parse_datetime(
        _first_present(account, extra, "credential_expires_at")
        or credentials.get("credential_expires_at")
        or credentials.get("expires_at")
    )
    if credential_expires_at is not None:
        result["credential_expires_at"] = credential_expires_at.astimezone(UTC)
    for field in ("subscription_status", "chatgpt_subscription_status"):
        value = _first_present(account, credentials, extra, field)
        if value is not None and str(value).strip():
            result[field] = str(value).strip()
    return result


def _collapse_probe_accounts_by_email(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_email: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for account in accounts:
        if _is_spark_shadow_account(account):
            continue
        email = account.get("normalized_email")
        if not email:
            passthrough.append(account)
            continue
        current = by_email.get(email)
        if current is None:
            collapsed = dict(account)
            collapsed["remote_account_ids"] = _stable_remote_ids([account.get("remote_account_id")])
            collapsed["duplicate_remote_count"] = 1
            by_email[email] = collapsed
            continue
        by_email[email] = _merge_probe_duplicate_account(current, account)
    return [*by_email.values(), *passthrough]


def _is_spark_shadow_account(account: dict[str, Any]) -> bool:
    return bool(SPARK_SHADOW_NAME_PATTERN.search(str(account.get("name") or "").strip()))


def _merge_probe_duplicate_account(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    merged["remote_account_ids"] = _stable_remote_ids([*_remote_ids(left), right.get("remote_account_id")])
    merged["duplicate_remote_count"] = len(merged["remote_account_ids"])
    merged["remote_account_id"] = merged["remote_account_ids"][0] if merged["remote_account_ids"] else left.get("remote_account_id")
    merged["group_ids"] = sorted(set([*(left.get("group_ids") or []), *(right.get("group_ids") or [])]))
    if _is_401(right):
        merged["status"] = right.get("status")
        merged["error_message"] = right.get("error_message")
    elif not _is_401(left) and _is_abnormal(right):
        merged["status"] = right.get("status")
        merged["error_message"] = right.get("error_message")
    merged["schedulable"] = False if left.get("schedulable") is False or right.get("schedulable") is False else (left.get("schedulable") if left.get("schedulable") is not None else right.get("schedulable"))
    merged["usage_snapshot"] = _merge_usage_snapshots(left.get("usage_snapshot") or {}, right.get("usage_snapshot") or {})
    merged["raw_hash"] = _stable_hash({"remote_ids": merged["remote_account_ids"], "usage": merged["usage_snapshot"], "status": merged.get("status"), "error": merged.get("error_message")})
    return merged


def _remote_ids(account: dict[str, Any]) -> list[Any]:
    values = account.get("remote_account_ids")
    if isinstance(values, list) and values:
        return values
    return [account.get("remote_account_id")]


def _stable_remote_ids(remote_ids: Any) -> list[Any]:
    values = remote_ids if isinstance(remote_ids, list) else [remote_ids]
    by_key = {str(item): item for item in values if item is not None and str(item) != ""}
    return [by_key[key] for key in sorted(by_key, key=_remote_id_sort_key)]


def _remote_id_sort_key(value: str) -> tuple[int, int | str]:
    return (0, int(value)) if value.isdigit() else (1, value)


def _merge_usage_snapshots(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    percent_fields = {"codex_5h_used_percent", "codex_7d_used_percent"}
    min_fields = {"codex_5h_reset_after_seconds", "codex_7d_reset_after_seconds"}
    all_keys = set(left) | set(right)
    for key in all_keys:
        if key in percent_fields:
            merged[key] = min(100.0, _number_float(left.get(key)) + _number_float(right.get(key)))
        elif key in min_fields:
            values = [_number_float(left.get(key), none_if_missing=True), _number_float(right.get(key), none_if_missing=True)]
            numeric = [value for value in values if value is not None]
            if numeric:
                merged[key] = min(numeric)
        elif key.startswith("codex_") and any(part in key for part in ("cost", "count", "token")):
            merged[key] = _number_float(left.get(key)) + _number_float(right.get(key))
        else:
            merged[key] = right.get(key) if right.get(key) not in (None, "") else left.get(key)
    return merged


def _usage_rollover_state(
    *,
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
    previous_totals: dict[str, Any] | None,
) -> dict[str, Any]:
    previous_snapshot = previous_snapshot if isinstance(previous_snapshot, dict) else {}
    current_snapshot = current_snapshot if isinstance(current_snapshot, dict) else {}
    previous_totals = previous_totals if isinstance(previous_totals, dict) else {}
    totals = dict(previous_totals)
    cumulative_snapshot = dict(current_snapshot)
    rollover_fields: list[str] = []

    for field in (*USAGE_WEEKLY_ROLLOVER_FIELDS, *USAGE_TOTAL_ROLLOVER_FIELDS):
        current_value = _number_float(current_snapshot.get(field), none_if_missing=True)
        previous_value = _number_float(previous_snapshot.get(field), none_if_missing=True)
        base_key = f"{field}_rollover_base"
        base_value = _number_float(previous_totals.get(base_key), none_if_missing=True) or 0.0
        if current_value is None:
            if previous_totals.get(field) is not None:
                cumulative_snapshot[f"{field}_cumulative"] = previous_totals.get(field)
            continue
        if previous_value is not None and current_value < previous_value:
            base_value += previous_value
            rollover_fields.append(field)
        cumulative_value = base_value + current_value
        totals[base_key] = round(base_value, 6)
        totals[field] = round(cumulative_value, 6)
        cumulative_snapshot[f"{field}_cumulative"] = round(cumulative_value, 6)

    return {
        "totals": totals,
        "snapshot": cumulative_snapshot,
        "rollover_detected": bool(rollover_fields),
        "rollover_details": {
            "rollover_fields": rollover_fields,
            "previous_usage_snapshot": previous_snapshot,
            "current_usage_snapshot": current_snapshot,
            "cumulative_usage_totals": totals,
        },
    }


def _official_usage_refresh_state(
    *,
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
    detected_at: datetime,
) -> dict[str, Any]:
    previous_snapshot = previous_snapshot if isinstance(previous_snapshot, dict) else {}
    current_snapshot = current_snapshot if isinstance(current_snapshot, dict) else {}
    previous_used = _number_float(previous_snapshot.get("codex_7d_used_percent"), none_if_missing=True)
    current_used = _number_float(current_snapshot.get("codex_7d_used_percent"), none_if_missing=True)
    expected_reset_at = _parse_datetime(previous_snapshot.get("codex_7d_reset_at"))
    comparable = bool(
        previous_used is not None
        and previous_used > 0
        and current_used is not None
        and expected_reset_at is not None
        and expected_reset_at > detected_at
    )
    eligible = bool(
        comparable
        and current_used == 0
    )
    return {
        "comparable": comparable,
        "eligible": eligible,
        "detected": eligible,
        "previous_used_percent": previous_used,
        "current_used_percent": current_used,
        "previous_reset_at": expected_reset_at,
        "current_reset_at": _parse_datetime(current_snapshot.get("codex_7d_reset_at")),
    }


def _official_refresh_consensus(accounts: list[dict[str, Any]], *, eligible_account_counts: dict[str, int]) -> dict[str, Any]:
    candidates_by_type: dict[str, list[dict[str, Any]]] = {}
    for account in accounts:
        refresh = account.get("official_refresh")
        if not isinstance(refresh, dict) or refresh.get("detected") is not True:
            continue
        candidates_by_type.setdefault(_official_refresh_account_type(account), []).append(account)

    type_consensus: dict[str, dict[str, Any]] = {}
    confirmed_account_types: list[str] = []
    confirmed_accounts: list[dict[str, Any]] = []
    for account_type, candidates in sorted(candidates_by_type.items()):
        eligible_count = int(eligible_account_counts.get(account_type) or 0)
        candidate_count = len(candidates)
        candidate_ratio = candidate_count / eligible_count if eligible_count > 0 else 0.0
        confirmed = candidate_count >= OFFICIAL_REFRESH_MIN_ACCOUNT_COUNT and candidate_ratio >= OFFICIAL_REFRESH_MIN_ACCOUNT_RATIO
        type_consensus[account_type] = {
            "confirmed": confirmed,
            "candidate_count": candidate_count,
            "eligible_account_count": eligible_count,
            "candidate_ratio": candidate_ratio,
        }
        if confirmed:
            confirmed_account_types.append(account_type)
            confirmed_accounts.extend(candidates)

    return {
        "confirmed": bool(confirmed_account_types),
        "candidate_count": sum(len(items) for items in candidates_by_type.values()),
        "confirmed_candidate_count": len(confirmed_accounts),
        "eligible_account_count": sum(int(value or 0) for value in eligible_account_counts.values()),
        "confirmed_account_types": confirmed_account_types,
        "confirmed_accounts": confirmed_accounts,
        "type_consensus": type_consensus,
    }


def _official_refresh_account_type(account: dict[str, Any]) -> str:
    return str(account.get("plan_type") or "unknown").strip().lower() or "unknown"


def _resolved_probe_plan_type(current: Any, previous: Any) -> tuple[str, str]:
    current_value = str(current or "").strip()
    if current_value:
        return current_value, "remote"
    previous_value = str(previous or "").strip()
    if previous_value:
        return previous_value, "cached"
    return "k12", "fallback_k12"


def _number_float(value: Any, *, none_if_missing: bool = False) -> float | None:
    if value is None or value == "":
        return None if none_if_missing else 0.0
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None if none_if_missing else 0.0


def _account_in_enabled_groups(account: dict[str, Any], enabled_group_ids: set[int]) -> bool:
    if not enabled_group_ids:
        return True
    group_ids = account.get("group_ids") if isinstance(account.get("group_ids"), list) else []
    return any(group_id in enabled_group_ids for group_id in group_ids)


def _setting_for_account(settings: dict[int, dict[str, Any]], account: dict[str, Any]) -> dict[str, Any]:
    for group_id in account.get("group_ids") or []:
        if group_id in settings:
            return settings[group_id]
    return {
        "enabled": True,
        "detailed_enabled": True,
        "record_usage_samples": True,
        "record_status_events": True,
        "record_duplicate_email_warning": True,
        "sample_retention_days": DEFAULT_SAMPLE_RETENTION_DAYS,
        "missing_confirm_count": DEFAULT_MISSING_CONFIRM_COUNT,
    }


def _prepare_history_change(
    *,
    site_id: str,
    account: dict[str, Any],
    identity: dict[str, Any] | None,
    setting: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    current = dynamic_snapshot(account)
    baseline = (identity or {}).get("history_baseline_snapshot")
    history_enabled = (
        setting.get("detailed_enabled") is not False
        and setting.get("record_usage_samples") is not False
    )
    if not isinstance(baseline, dict) or not history_enabled:
        return None, current
    return (
        build_history_change(
            identity_id=_identity_id(site_id, account["normalized_email"]),
            remote_account_id=account.get("remote_account_id"),
            previous=baseline,
            current=current,
        ),
        None,
    )


async def _ensure_session(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    account: dict[str, Any],
    identity: dict[str, Any] | None,
    detected_at: datetime,
) -> dict[str, Any]:
    normalized_email = account["normalized_email"]
    remote_id = account["remote_account_id"]
    remote_ids = set(_normalized_remote_id_signature(account.get("remote_account_ids") or [remote_id]))
    current_session_id = identity.get("current_session_id") if identity else None
    if current_session_id:
        session = await db.remote_account_sessions.find_one({"_id": current_session_id, "status": "open"})
        if session and str(session.get("remote_account_id")) in remote_ids:
            return session
    session_index = int((identity or {}).get("total_sessions") or 0) + 1
    session_id = f"{site_id}:{normalized_email}:{session_index}"
    initial_usage = _usage_rollover_state(previous_snapshot=None, current_snapshot=account.get("usage_snapshot") or {}, previous_totals=None)
    doc = {
        "_id": session_id,
        "site_id": site_id,
        "identity_id": _identity_id(site_id, normalized_email),
        "normalized_email": normalized_email,
        "email": account.get("email"),
        "remote_account_id": remote_id,
        "session_index": session_index,
        "started_at": detected_at,
        "status": "open",
        "first_active_at": detected_at if _is_normal(account) else None,
        "last_active_at": detected_at if _is_normal(account) else None,
        "first_abnormal_at": detected_at if _is_abnormal(account) else None,
        "last_abnormal_at": detected_at if _is_abnormal(account) else None,
        "first_401_at": detected_at if _is_401(account) else None,
        "last_401_at": detected_at if _is_401(account) else None,
        "group_ids_first": account.get("group_ids") or [],
        "group_ids_last": account.get("group_ids") or [],
        "plan_type_first": account.get("plan_type"),
        "plan_type_last": account.get("plan_type"),
        "error_message_first": account.get("error_message"),
        "error_message_last": account.get("error_message"),
        "last_usage_snapshot": account.get("usage_snapshot") or {},
        "cumulative_usage_totals": initial_usage["totals"],
        "cumulative_usage_snapshot": initial_usage["snapshot"],
        "last_usage_rollover_at": None,
        "created_at": detected_at,
        "updated_at": detected_at,
    }
    if current_session_id:
        await db.remote_account_sessions.update_one({"_id": current_session_id, "status": "open"}, {"$set": {"status": "closed", "ended_at": detected_at, "end_reason": "remote_replaced", "updated_at": detected_at}})
    await db.remote_account_sessions.update_one({"_id": session_id}, {"$setOnInsert": doc}, upsert=True)
    created = await db.remote_account_sessions.find_one({"_id": session_id})
    return created or doc


async def _update_identity_and_events(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    account: dict[str, Any],
    identity: dict[str, Any] | None,
    session: dict[str, Any],
    setting: dict[str, Any],
    detected_at: datetime,
    history_baseline_override: dict[str, Any] | None,
) -> bool:
    identity_id = _identity_id(site_id, account["normalized_email"])
    event_enabled = setting.get("record_status_events") is not False
    identity_usage = _usage_rollover_state(
        previous_snapshot=(identity or {}).get("last_usage_snapshot") if identity else None,
        current_snapshot=account.get("usage_snapshot") or {},
        previous_totals=(identity or {}).get("cumulative_usage_totals") if identity else None,
    )
    confirmed_401 = _confirmed_401_state(
        account=account,
        previous_is_401=bool((identity or {}).get("current_is_401")),
        previous_recovery_streak=int((identity or {}).get("401_recovery_streak") or 0),
    )
    changed = False
    if identity is None:
        changed = True
        if event_enabled:
            await _write_event(db, site_id=site_id, event_type="remote_account_seen_first", severity="info", detected_at=detected_at, account=account, session=session)
            if _is_401(account):
                await _write_event(db, site_id=site_id, event_type="401_detected", severity="critical", detected_at=detected_at, account=account, session=session)
    else:
        current_remote_ids = _normalized_remote_id_signature(account.get("remote_account_ids") or [account.get("remote_account_id")])
        previous_remote_ids = _normalized_remote_id_signature(identity.get("current_remote_account_ids") or [identity.get("current_remote_account_id")])
        remote_sets_overlap = bool(set(current_remote_ids) & set(previous_remote_ids))
        remote_account_replaced = str(identity.get("current_remote_account_id")) != str(account.get("remote_account_id")) and not remote_sets_overlap
        if identity.get("current_presence") in {"removed", "missing_suspected"} or remote_account_replaced:
            changed = True
            if event_enabled:
                await _write_event(db, site_id=site_id, event_type="remote_account_reappeared", severity="info", detected_at=detected_at, account=account, session=session, previous=identity)
        for field, event_type in (("current_status", "status_changed"), ("current_error_message", "error_changed"), ("current_schedulable", "schedulable_changed")):
            current_value = _identity_compare_value(field, account)
            if identity.get(field) != current_value:
                changed = True
                if event_enabled:
                    await _write_event(db, site_id=site_id, event_type=event_type, severity=_event_severity(account), detected_at=detected_at, account=account, session=session, previous=identity)
        if (identity.get("current_group_ids") or []) != (account.get("group_ids") or []):
            changed = True
            if event_enabled:
                await _write_event(db, site_id=site_id, event_type="group_changed", severity="info", detected_at=detected_at, account=account, session=session, previous=identity)
        was_401 = bool(identity.get("current_is_401"))
        is_401 = bool(confirmed_401["is_401"])
        if is_401 and not was_401 and event_enabled:
            await _write_event(db, site_id=site_id, event_type="401_detected", severity="critical", detected_at=detected_at, account=account, session=session, previous=identity)
        if was_401 and not is_401 and event_enabled:
            await _write_event(db, site_id=site_id, event_type="401_recovered", severity="info", detected_at=detected_at, account=account, session=session, previous=identity)
        if identity_usage["rollover_detected"] and event_enabled:
            await _write_event(
                db,
                site_id=site_id,
                event_type="usage_rollover",
                severity="info",
                detected_at=detected_at,
                account=account,
                session=session,
                previous=identity,
                details=identity_usage["rollover_details"],
            )
    duplicate_changed = False
    if setting.get("record_duplicate_email_warning", True):
        current_remote_ids = _normalized_remote_id_signature(account.get("remote_account_ids") or [account.get("remote_account_id")])
        previous_remote_ids = _normalized_remote_id_signature((identity or {}).get("current_remote_account_ids") or [(identity or {}).get("current_remote_account_id")])
        current_duplicate_count = len(current_remote_ids)
        previous_duplicate_count = int((identity or {}).get("duplicate_remote_count") or len(previous_remote_ids))
        if current_duplicate_count > 1 and (previous_duplicate_count <= 1 or current_remote_ids != previous_remote_ids):
            changed = True
            duplicate_changed = True
            await _write_event(
                db,
                site_id=site_id,
                event_type="duplicate_email_detected",
                severity="warning",
                detected_at=detected_at,
                account=account,
                session=session,
                previous=identity,
                details={
                    "remote_account_ids": current_remote_ids,
                    "previous_remote_account_ids": previous_remote_ids,
                    "count": current_duplicate_count,
                    "previous_count": previous_duplicate_count,
                    "duplicate_state": "active",
                },
            )
        elif previous_duplicate_count > 1 and current_duplicate_count <= 1:
            changed = True
            duplicate_changed = True
            await _write_event(
                db,
                site_id=site_id,
                event_type="duplicate_email_resolved",
                severity="info",
                detected_at=detected_at,
                account=account,
                session=session,
                previous=identity,
                details={
                    "remote_account_ids": current_remote_ids,
                    "previous_remote_account_ids": previous_remote_ids,
                    "count": current_duplicate_count,
                    "previous_count": previous_duplicate_count,
                    "duplicate_state": "resolved",
                },
            )

    updates = {
        "site_id": site_id,
        "normalized_email": account["normalized_email"],
        "email": account.get("email"),
        "name": account.get("name"),
        "last_seen_at": detected_at,
        "last_present_at": detected_at,
        "current_presence": "present",
        "missing_count": 0,
        "current_remote_account_id": account.get("remote_account_id"),
        "current_remote_account_ids": _stable_remote_ids(account.get("remote_account_ids") or [account.get("remote_account_id")]),
        "duplicate_remote_count": account.get("duplicate_remote_count") or 1,
        "current_session_id": session["_id"],
        "current_status": account.get("status"),
        "current_schedulable": account.get("schedulable"),
        "current_error_message": account.get("error_message"),
        "current_is_401": confirmed_401["is_401"],
        "401_recovery_streak": confirmed_401["recovery_streak"],
        "current_group_ids": account.get("group_ids") or [],
        "plan_type": account.get("plan_type"),
        "plan_type_source": account.get("plan_type_source"),
        "last_usage_snapshot": account.get("usage_snapshot") or {},
        "current_subscription_snapshot": account.get("subscription_snapshot") or {},
        "cumulative_usage_totals": identity_usage["totals"],
        "cumulative_usage_snapshot": identity_usage["snapshot"],
        "last_usage_rollover_at": detected_at if identity_usage["rollover_detected"] else (identity or {}).get("last_usage_rollover_at"),
        "missing_confirm_count": int(setting.get("missing_confirm_count") or DEFAULT_MISSING_CONFIRM_COUNT),
        "last_event_at": detected_at if changed else (identity or {}).get("last_event_at"),
        "updated_at": detected_at,
    }
    if history_baseline_override is not None:
        updates["history_baseline_snapshot"] = history_baseline_override
        updates["history_baseline_hash"] = snapshot_hash(history_baseline_override)
        updates["history_baseline_confirmed_at"] = detected_at
    if duplicate_changed:
        updates["duplicate_email_alert_read_at"] = None
        updates["duplicate_email_alert_read_signature"] = None
        updates["duplicate_email_alert_read_note"] = None
    increments: dict[str, int] = {}
    if identity is None:
        updates["first_seen_at"] = detected_at
        increments["total_sessions"] = 1
    elif str(identity.get("current_session_id")) != str(session["_id"]):
        increments["total_sessions"] = 1
    if confirmed_401["is_401"] and not bool((identity or {}).get("current_is_401")):
        if not identity or not identity.get("first_401_at"):
            updates["first_401_at"] = detected_at
        updates["last_401_at"] = detected_at
        increments["total_401_count"] = 1
    if bool((identity or {}).get("current_is_401")) and not confirmed_401["is_401"]:
        if not identity or not identity.get("first_recovered_at"):
            updates["first_recovered_at"] = detected_at
        updates["last_recovered_at"] = detected_at
        increments["total_recovery_count"] = 1

    update_doc: dict[str, Any] = {"$set": updates, "$setOnInsert": {"created_at": detected_at}}
    if increments:
        update_doc["$inc"] = increments
    await db.remote_account_identities.update_one({"_id": identity_id}, update_doc, upsert=True)
    await _update_session_status(db, session_id=session["_id"], account=account, detected_at=detected_at)
    return changed


async def _update_session_status(db: AsyncIOMotorDatabase, *, session_id: str, account: dict[str, Any], detected_at: datetime) -> None:
    session = await db.remote_account_sessions.find_one({"_id": session_id})
    session_usage = _usage_rollover_state(
        previous_snapshot=(session or {}).get("last_usage_snapshot") if session else None,
        current_snapshot=account.get("usage_snapshot") or {},
        previous_totals=(session or {}).get("cumulative_usage_totals") if session else None,
    )
    updates: dict[str, Any] = {
        "group_ids_last": account.get("group_ids") or [],
        "plan_type_last": account.get("plan_type"),
        "error_message_last": account.get("error_message"),
        "last_usage_snapshot": account.get("usage_snapshot") or {},
        "cumulative_usage_totals": session_usage["totals"],
        "cumulative_usage_snapshot": session_usage["snapshot"],
        "last_usage_rollover_at": detected_at if session_usage["rollover_detected"] else (session or {}).get("last_usage_rollover_at"),
        "updated_at": detected_at,
    }
    if _is_normal(account):
        updates["last_active_at"] = detected_at
    if _is_abnormal(account):
        updates["last_abnormal_at"] = detected_at
    if _is_401(account):
        updates["last_401_at"] = detected_at
    await db.remote_account_sessions.update_one({"_id": session_id}, {"$set": updates})
    first_updates = {}
    if _is_normal(account):
        first_updates["first_active_at"] = detected_at
    if _is_abnormal(account):
        first_updates["first_abnormal_at"] = detected_at
    if _is_401(account):
        first_updates["first_401_at"] = detected_at
    for field, value in first_updates.items():
        await db.remote_account_sessions.update_one({"_id": session_id, field: None}, {"$set": {field: value}})


async def _mark_missing_identities(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    seen_identity_ids: set[str],
    group_ids: set[int] | None = None,
    detected_at: datetime,
) -> dict[str, int]:
    suspected = 0
    removed = 0
    query: dict[str, Any] = {"site_id": site_id, "current_presence": {"$in": ["present", "missing_suspected"]}}
    if group_ids:
        query["current_group_ids"] = {"$in": list(group_ids)}
    if seen_identity_ids:
        query["_id"] = {"$nin": list(seen_identity_ids)}
    cursor = db.remote_account_identities.find(query)
    async for identity in cursor:
        missing_count = int(identity.get("missing_count") or 0) + 1
        identity_id = identity["_id"]
        session_id = identity.get("current_session_id")
        if missing_count >= int(identity.get("missing_confirm_count") or DEFAULT_MISSING_CONFIRM_COUNT):
            removed += 1
            updates = {
                "current_presence": "removed",
                "missing_count": missing_count,
                "last_removed_at": detected_at,
                "updated_at": detected_at,
            }
            set_on_insert_or_missing = {}
            if not identity.get("first_removed_at"):
                set_on_insert_or_missing["first_removed_at"] = detected_at
            update_doc: dict[str, Any] = {"$set": updates, "$inc": {"total_removed_count": 1}}
            if set_on_insert_or_missing:
                update_doc["$set"].update(set_on_insert_or_missing)
            await db.remote_account_identities.update_one({"_id": identity_id}, update_doc)
            if session_id:
                await db.remote_account_sessions.update_one({"_id": session_id, "status": "open"}, {"$set": {"status": "closed", "ended_at": detected_at, "end_reason": "remote_removed_confirmed", "updated_at": detected_at}})
            await _write_event(db, site_id=site_id, event_type="remote_removed_confirmed", severity="warning", detected_at=detected_at, identity=identity)
        else:
            suspected += 1
            await db.remote_account_identities.update_one({"_id": identity_id}, {"$set": {"current_presence": "missing_suspected", "missing_count": missing_count, "updated_at": detected_at}})
            await _write_event(db, site_id=site_id, event_type="missing_suspected", severity="warning", detected_at=detected_at, identity=identity, details={"missing_count": missing_count})
    return {"accounts_missing_suspected": suspected, "accounts_removed_confirmed": removed}


async def _write_event(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    event_type: str,
    severity: str,
    detected_at: datetime,
    account: dict[str, Any] | None = None,
    session: dict[str, Any] | None = None,
    previous: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    account = account or {}
    merged_details = dict(details or {})
    if account.get("duplicate_remote_count"):
        merged_details.setdefault("duplicate_remote_count", account.get("duplicate_remote_count"))
        merged_details.setdefault("remote_account_ids", account.get("remote_account_ids"))
    previous_or_empty = previous or {}
    identity_id = identity.get("_id") if identity else _identity_id(site_id, account.get("normalized_email") or previous_or_empty.get("normalized_email")) if (account.get("normalized_email") or previous_or_empty.get("normalized_email")) else None
    doc = {
        "site_id": site_id,
        "identity_id": identity_id,
        "session_id": session.get("_id") if session else (identity or {}).get("current_session_id"),
        "normalized_email": account.get("normalized_email") or (identity or previous or {}).get("normalized_email"),
        "email": account.get("email") or (identity or previous or {}).get("email"),
        "name": account.get("name") or (identity or previous or {}).get("name"),
        "plan_type": account.get("plan_type") or (identity or previous or {}).get("plan_type"),
        "remote_account_id": account.get("remote_account_id") or (identity or previous or {}).get("current_remote_account_id"),
        "event_type": event_type,
        "severity": severity,
        "occurred_at": detected_at,
        "detected_at": detected_at,
        "previous_status": (previous or identity or {}).get("current_status"),
        "current_status": account.get("status"),
        "previous_schedulable": (previous or identity or {}).get("current_schedulable"),
        "current_schedulable": account.get("schedulable"),
        "previous_error_message": (previous or identity or {}).get("current_error_message"),
        "current_error_message": account.get("error_message"),
        "previous_group_ids": (previous or identity or {}).get("current_group_ids"),
        "current_group_ids": account.get("group_ids"),
        "is_401": _is_401(account) if account else bool((identity or previous or {}).get("current_is_401")),
        "error_category": _error_category(account.get("error_message") or (identity or previous or {}).get("current_error_message")),
        "usage_snapshot": account.get("usage_snapshot") or {},
        "details": merged_details,
        "raw_excerpt": str(account.get("error_message") or (identity or previous or {}).get("current_error_message") or "")[:500],
        "created_at": detected_at,
    }
    result = await db.remote_account_status_events.insert_one(doc)
    if event_type == "401_detected":
        event_id = str(result.inserted_id)
        try:
            notification_result = await _notify_401_detected(db, event_id=event_id, event_db_id=result.inserted_id, event_doc=doc)
            await db.remote_account_status_events.update_one(
                {"_id": result.inserted_id},
                {
                    "$set": {
                        "notification_status": notification_result.get("event", {}).get("status"),
                        "notification_event_id": notification_result.get("event", {}).get("id"),
                        "notification_batch_id": notification_result.get("event", {}).get("batch_id"),
                        "notification_channel_count": notification_result.get("total", 0),
                        "notification_success_count": notification_result.get("success", 0),
                        "notification_failed_count": notification_result.get("failed", 0),
                    }
                },
            )
        except Exception as exc:  # noqa: BLE001 - probe event must survive notification failure.
            logger.warning("sub2api_account_probe_401_notification_failed site_id=%s event_id=%s error=%s", site_id, event_id, exc)
            await db.remote_account_status_events.update_one(
                {"_id": result.inserted_id},
                {"$set": {"notification_status": "failed", "notification_error": str(exc) or exc.__class__.__name__}},
            )


async def _notify_401_detected(db: AsyncIOMotorDatabase, *, event_id: str, event_db_id: Any, event_doc: dict[str, Any]) -> dict[str, Any]:
    site_id = str(event_doc.get("site_id") or "")
    email = str(event_doc.get("email") or event_doc.get("normalized_email") or "-")
    remote_account_id = event_doc.get("remote_account_id")
    group_ids = event_doc.get("current_group_ids") or []
    error_message = str(event_doc.get("current_error_message") or event_doc.get("raw_excerpt") or "-")
    detected_at = event_doc.get("detected_at") or event_doc.get("occurred_at")
    group_names = await _notification_group_names(db, site_id=site_id, group_ids=group_ids)
    pro_group_ids = await _notification_pro_group_ids(db, site_id=site_id, group_ids=group_ids)
    is_pro_pool = _is_pro_probe_event(event_doc, group_names=group_names, pro_group_ids=pro_group_ids)
    account_name = str(event_doc.get("name") or event_doc.get("details", {}).get("name") or "-").strip() or "-"
    detail_updates = {
        "details.is_pro_pool": is_pro_pool,
        "details.pro_group_ids": pro_group_ids,
        "details.account_type": "pro" if is_pro_pool else _normalized_account_type(event_doc.get("plan_type")),
        "details.name": account_name,
    }
    await db.remote_account_status_events.update_one({"_id": event_db_id}, {"$set": detail_updates})
    if not is_pro_pool:
        return _notification_skip_result("skipped_non_pro")

    detected_dt = _coerce_datetime(detected_at) or now_utc()
    stats = await _pro_401_stats(db, detected_at=detected_dt)
    group_summary = await _notification_capacity_summary(db, site_id=site_id, group_ids=pro_group_ids or group_ids)
    await db.remote_account_status_events.update_one(
        {"_id": event_db_id},
        {"$set": {"details.ban_count_1h": stats["one_hour"], "details.ban_count_today": stats["today"], "details.capacity_summary": group_summary}},
    )
    usage = event_doc.get("usage_snapshot") if isinstance(event_doc.get("usage_snapshot"), dict) else {}
    batch_id = await _enqueue_401_notification_batch(
        db,
        status_event_id=event_id,
        status_event_db_id=event_db_id,
        site_id=site_id,
        group_ids=group_ids,
        pro_group_ids=pro_group_ids,
        account_name=account_name,
        detected_at=detected_dt,
        event_payload={
        "status_event_id": event_id,
        "site_id": site_id,
        "group_ids": group_ids,
        "group_names": group_names,
        "pro_group_ids": pro_group_ids,
        "account_name": account_name,
        "email": email,
        "remote_account_id": remote_account_id,
        "error_message": error_message,
        "detected_at": serialize_doc(detected_at),
        "usage_snapshot": usage,
        "ban_count_1h": stats["one_hour"],
        "ban_count_today": stats["today"],
        "capacity_summary": group_summary,
        },
    )
    return {"event": {"id": None, "batch_id": batch_id, "status": "batched"}, "total": 0, "success": 0, "failed": 0, "items": []}


async def flush_due_401_notification_batches(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    now = now_utc()
    sent = 0
    failed = 0
    cursor = db.notification_batches.find(
        {"event_type": "sub2api.account.401_detected", "status": "pending", "window_end_at": {"$lte": now}},
        {"_id": 1},
    ).sort("window_start_at", 1).limit(20)
    async for item in cursor:
        batch_id = str(item.get("_id"))
        result = await db.notification_batches.update_one(
            {"_id": batch_id, "status": "pending"},
            {"$set": {"status": "sending", "sending_at": now_utc(), "updated_at": now_utc()}},
        )
        if result.modified_count == 0:
            continue
        try:
            await _send_401_notification_batch(db, batch_id=batch_id)
            sent += 1
        except Exception as exc:  # noqa: BLE001 - keep scheduler alive and retry next loop.
            failed += 1
            logger.warning("sub2api_account_probe_401_batch_send_failed batch_id=%s error=%s", batch_id, exc)
            await db.notification_batches.update_one(
                {"_id": batch_id},
                {"$set": {"status": "pending", "last_error": str(exc) or exc.__class__.__name__, "last_failed_at": now_utc(), "updated_at": now_utc()}, "$inc": {"send_attempts": 1}},
            )
    return {"sent": sent, "failed": failed}


async def _enqueue_401_notification_batch(
    db: AsyncIOMotorDatabase,
    *,
    status_event_id: str,
    status_event_db_id: Any,
    site_id: str,
    group_ids: list[int],
    pro_group_ids: list[int],
    account_name: str,
    detected_at: datetime,
    event_payload: dict[str, Any],
) -> str:
    now = now_utc()
    batch = await db.notification_batches.find_one(
        {"event_type": "sub2api.account.401_detected", "status": "pending", "window_end_at": {"$gt": now}},
        {"_id": 1},
        sort=[("window_start_at", 1)],
    )
    if batch:
        batch_id = str(batch["_id"])
        await db.notification_batches.update_one(
            {"_id": batch_id},
            {
                "$addToSet": {
                    "status_event_ids": status_event_id,
                    "status_event_db_ids": status_event_db_id,
                    "site_ids": site_id,
                    "group_ids": {"$each": group_ids},
                    "pro_group_ids": {"$each": pro_group_ids},
                    "account_names": account_name,
                },
                "$push": {"events": event_payload},
                "$inc": {"event_count": 1},
                "$set": {"last_event_at": detected_at, "updated_at": now},
            },
        )
        return batch_id

    batch_id = secrets.token_hex(12)
    window_start = now
    window_end = window_start + timedelta(seconds=NOTIFICATION_401_THROTTLE_SECONDS)
    await db.notification_batches.insert_one(
        {
            "_id": batch_id,
            "event_type": "sub2api.account.401_detected",
            "source": "sub2api_account_probe",
            "status": "pending",
            "window_start_at": window_start,
            "window_end_at": window_end,
            "first_event_at": detected_at,
            "last_event_at": detected_at,
            "event_count": 1,
            "status_event_ids": [status_event_id],
            "status_event_db_ids": [status_event_db_id],
            "site_ids": [site_id],
            "group_ids": group_ids,
            "pro_group_ids": pro_group_ids,
            "account_names": [account_name],
            "events": [event_payload],
            "created_at": now,
            "updated_at": now,
        }
    )
    return batch_id


async def _send_401_notification_batch(db: AsyncIOMotorDatabase, *, batch_id: str) -> dict[str, Any]:
    batch = await db.notification_batches.find_one({"_id": batch_id})
    if not batch:
        return _notification_skip_result("batch_not_found")
    events = batch.get("events") if isinstance(batch.get("events"), list) else []
    if not events:
        await db.notification_batches.update_one({"_id": batch_id}, {"$set": {"status": "skipped", "updated_at": now_utc(), "skip_reason": "empty_batch"}})
        return _notification_skip_result("empty_batch")

    latest_event = events[-1] if isinstance(events[-1], dict) else {}
    pro_group_ids = batch.get("pro_group_ids") if isinstance(batch.get("pro_group_ids"), list) else []
    capacity_summary = await _notification_batch_capacity_summary(db, events=events)
    detected_at = _coerce_datetime(latest_event.get("detected_at")) or _coerce_datetime(batch.get("last_event_at")) or now_utc()
    stats = await _pro_401_stats(db, detected_at=detected_at)
    names = [str(event.get("account_name") or "-") for event in events if isinstance(event, dict)]
    name_text = "、".join(names[:8])
    if len(names) > 8:
        name_text += f" 等 {len(names)} 个"
    red_lines = _capacity_red_status_lines(capacity_summary)
    lines = [
        "### AIwelink Pro 401 封号告警",
        f"- 本次新增：{len(events)} 个",
        f"- 账号：{name_text or '-'}",
        f"- 1h 内封号：{stats['one_hour']} 个",
        f"- 今日封号：{stats['today']} 个",
        f"- 剩余账号数：{_format_count(capacity_summary.get('available_accounts'))}",
        f"- 5h 动态可用：{_format_usd(capacity_summary.get('dynamic_five_hour_remaining_estimated_usd'))}",
        f"- 5h 实际可用：{_format_usd(capacity_summary.get('five_hour_actual_remaining_usd'))}",
        f"- 7d 动态可用：{_format_usd(capacity_summary.get('seven_day_remaining_estimated_usd'))}",
        f"- 7d 实际可用：{_format_usd(capacity_summary.get('seven_day_actual_remaining_usd'))}",
    ]
    lines.extend(red_lines)
    lines.append(f"- 时间：{_format_shanghai_time(detected_at)}")
    plain_text = "\n".join(line.replace("### ", "") for line in lines)
    channel_ids = await _active_dingtalk_channel_ids(db)
    if not channel_ids:
        await db.notification_batches.update_one(
            {"_id": batch_id},
            {"$set": {"status": "skipped", "notification_status": "skipped_no_dingtalk", "skip_reason": "no active dingtalk channel", "updated_at": now_utc()}},
        )
        await _update_401_batch_status_events(
            db,
            batch=batch,
            batch_id=batch_id,
            notification_status="skipped_no_dingtalk",
            notification_event_id=None,
            total=0,
            success=0,
            failed=0,
        )
        return _notification_skip_result("skipped_no_dingtalk")
    notification_result = await send_notification_event(
        db,
        event_type="sub2api.account.401_detected",
        severity="critical",
        source="sub2api_account_probe",
        resource_type="notification_batch",
        resource_id=batch_id,
        dedupe_key=f"sub2api.account.401_detected.batch:{batch_id}",
        title="AIwelink Pro 401 封号告警",
        text=plain_text,
        markdown_text="\n".join(lines),
        payload={
            "batch_id": batch_id,
            "event_count": len(events),
            "site_ids": batch.get("site_ids") or [],
            "group_ids": batch.get("group_ids") or [],
            "pro_group_ids": pro_group_ids,
            "account_names": names,
            "ban_count_1h": stats["one_hour"],
            "ban_count_today": stats["today"],
            "capacity_summary": capacity_summary,
            "red_status_lines": red_lines,
            "events": events,
        },
        channel_ids=channel_ids,
    )
    status_value = notification_result.get("event", {}).get("status") or "unknown"
    event_status_value = "sent_batch" if status_value in {"success", "partial", "skipped"} else "failed_batch"
    await db.notification_batches.update_one(
        {"_id": batch_id},
        {
            "$set": {
                "status": "sent" if status_value in {"success", "partial", "skipped"} else "failed",
                "notification_event_id": notification_result.get("event", {}).get("id"),
                "notification_status": status_value,
                "notification_channel_count": notification_result.get("total", 0),
                "notification_success_count": notification_result.get("success", 0),
                "notification_failed_count": notification_result.get("failed", 0),
                "sent_at": now_utc(),
                "updated_at": now_utc(),
            }
        },
    )
    await _update_401_batch_status_events(
        db,
        batch=batch,
        batch_id=batch_id,
        notification_status=event_status_value,
        notification_event_id=notification_result.get("event", {}).get("id"),
        total=notification_result.get("total", 0),
        success=notification_result.get("success", 0),
        failed=notification_result.get("failed", 0),
    )
    return notification_result


async def _update_401_batch_status_events(
    db: AsyncIOMotorDatabase,
    *,
    batch: dict[str, Any],
    batch_id: str,
    notification_status: str,
    notification_event_id: str | None,
    total: int,
    success: int,
    failed: int,
) -> None:
    status_event_db_ids = [item for item in batch.get("status_event_db_ids") or [] if item]
    if status_event_db_ids:
        await db.remote_account_status_events.update_many(
            {"_id": {"$in": status_event_db_ids}},
            {
                "$set": {
                    "notification_status": notification_status,
                    "notification_event_id": notification_event_id,
                    "notification_batch_id": batch_id,
                    "notification_channel_count": total,
                    "notification_success_count": success,
                    "notification_failed_count": failed,
                }
            },
        )


async def _active_dingtalk_channel_ids(db: AsyncIOMotorDatabase) -> list[str]:
    return [str(item["_id"]) async for item in db.notification_channels.find({"status": "active", "channel_type": "dingtalk"}, {"_id": 1})]


async def _write_official_refresh_events(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    detected_at: datetime,
    accounts: list[dict[str, Any]],
    consensus: dict[str, Any],
) -> None:
    for account in accounts:
        identity_id = _identity_id(site_id, str(account.get("normalized_email") or ""))
        identity = await db.remote_account_identities.find_one({"_id": identity_id})
        session = None
        if identity and identity.get("current_session_id"):
            session = await db.remote_account_sessions.find_one({"_id": identity["current_session_id"]})
        await _write_event(
            db,
            site_id=site_id,
            event_type="official_usage_refresh",
            severity="info",
            detected_at=detected_at,
            account=account,
            session=session,
            previous=identity,
            details={
                **(account.get("official_refresh") if isinstance(account.get("official_refresh"), dict) else {}),
                "candidate_count": consensus["candidate_count"],
                "eligible_account_count": consensus["eligible_account_count"],
                "confirmed_account_types": consensus["confirmed_account_types"],
                "type_consensus": consensus["type_consensus"],
                "official_refresh_confirmed": True,
            },
        )


async def _notify_official_usage_refresh(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    detected_at: datetime,
    accounts: list[dict[str, Any]],
    consensus: dict[str, Any],
) -> dict[str, Any]:
    dedupe_after = detected_at - timedelta(hours=OFFICIAL_REFRESH_NOTIFICATION_DEDUPE_HOURS)
    account_types = consensus.get("confirmed_account_types") or []
    existing = await db.notification_events.find_one(
        {
            "event_type": "sub2api.account.official_usage_refresh",
            "created_at": {"$gte": dedupe_after},
            "status": {"$in": ["pending", "success", "partial"]},
            "payload.confirmed_account_types": {"$in": account_types},
        },
        {"_id": 1},
    )
    if existing:
        return _notification_skip_result("skipped_duplicate_official_refresh")

    channel_ids = await _active_dingtalk_channel_ids(db)
    if not channel_ids:
        return _notification_skip_result("skipped_no_dingtalk")

    account_names = [str(item.get("name") or item.get("email") or item.get("remote_account_id") or "-") for item in accounts]
    lines = [
        "### Surprise，OpenAI 额度提前刷新了",
        f"- 同步刷新账号：{len(accounts)} 个",
        f"- 账号类型：{'、'.join(account_types)}",
        f"- 账号：{'、'.join(account_names[:8])}",
    ]
    if len(account_names) > 8:
        lines.append(f"- 其余：{len(account_names) - 8} 个")
    lines.append(f"- 时间：{_format_shanghai_time(detected_at)}")
    return await send_notification_event(
        db,
        event_type="sub2api.account.official_usage_refresh",
        severity="info",
        source="sub2api_account_probe",
        resource_type="sub2api_site",
        resource_id=site_id,
        dedupe_key=f"sub2api.account.official_usage_refresh:{','.join(account_types)}:{detected_at.date().isoformat()}",
        title="Surprise，OpenAI 额度提前刷新了",
        text="\n".join(line.replace("### ", "") for line in lines),
        markdown_text="\n".join(lines),
        payload={
            "site_id": site_id,
            "account_count": len(accounts),
            "confirmed_account_types": account_types,
            "type_consensus": consensus.get("type_consensus") or {},
            "account_names": account_names,
            "remote_account_ids": [item.get("remote_account_id") for item in accounts],
            "detected_at": detected_at,
        },
        channel_ids=channel_ids,
    )


async def _notification_group_names(db: AsyncIOMotorDatabase, *, site_id: str, group_ids: Any) -> list[str]:
    result: list[str] = []
    if not site_id or not isinstance(group_ids, list):
        return result
    numeric_ids = []
    for value in group_ids:
        try:
            numeric_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not numeric_ids:
        return result
    cursor = db.sub2api_groups_cache.find({"site_id": site_id, "group_id": {"$in": numeric_ids}}, {"group_id": 1, "group.name": 1})
    names_by_id: dict[int, str] = {}
    async for doc in cursor:
        group = doc.get("group") if isinstance(doc.get("group"), dict) else {}
        group_id = doc.get("group_id")
        if isinstance(group_id, int):
            names_by_id[group_id] = str(group.get("name") or f"#{group_id}")
    for group_id in numeric_ids:
        result.append(names_by_id.get(group_id, f"#{group_id}"))
    return result


async def _notification_pro_group_ids(db: AsyncIOMotorDatabase, *, site_id: str, group_ids: Any) -> list[int]:
    result: list[int] = []
    if not site_id or not isinstance(group_ids, list):
        return result
    numeric_ids: list[int] = []
    for value in group_ids:
        try:
            numeric_ids.append(int(value))
        except (TypeError, ValueError):
            continue
    if not numeric_ids:
        return result
    cursor = db.sub2api_groups_cache.find(
        {"site_id": site_id, "group_id": {"$in": numeric_ids}},
        {"group_id": 1, "group.name": 1, "capacity_summary.account_type": 1, "group.capacity_summary.account_type": 1},
    )
    async for doc in cursor:
        group_id = doc.get("group_id")
        if not isinstance(group_id, int):
            continue
        group = doc.get("group") if isinstance(doc.get("group"), dict) else {}
        summary = doc.get("capacity_summary") if isinstance(doc.get("capacity_summary"), dict) else group.get("capacity_summary")
        account_type = summary.get("account_type") if isinstance(summary, dict) else None
        group_name = str(group.get("name") or "")
        if _normalized_account_type(account_type) == "pro" or _text_mentions_pro(group_name):
            result.append(group_id)
    return result


def _is_pro_probe_event(event_doc: dict[str, Any], *, group_names: list[str], pro_group_ids: list[int]) -> bool:
    if pro_group_ids:
        return True
    plan_type = _normalized_account_type(event_doc.get("plan_type") or (event_doc.get("details") or {}).get("account_type"))
    if plan_type == "pro":
        return True
    return any(_text_mentions_pro(name) for name in group_names)


def _normalized_account_type(value: Any) -> str:
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    if text in {"pro", "plus", "free", "team", "k12"}:
        return text
    if "20x" in text or "pro" in text:
        return "pro"
    return text or "unknown"


def _text_mentions_pro(value: Any) -> bool:
    return bool(PRO_MARKER_PATTERN.search(str(value or "")))


def _notification_skip_result(status: str) -> dict[str, Any]:
    return {"event": {"id": None, "status": status}, "total": 0, "success": 0, "failed": 0, "items": []}


async def _pro_401_stats(db: AsyncIOMotorDatabase, *, detected_at: datetime) -> dict[str, int]:
    detected_at = _coerce_datetime(detected_at) or now_utc()
    one_hour_start = detected_at - timedelta(hours=1)
    today_start = detected_at.astimezone(SHANGHAI_TZ).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
    base_query = {"event_type": "401_detected", "details.is_pro_pool": True}
    one_hour = await db.remote_account_status_events.count_documents({**base_query, "detected_at": {"$gte": one_hour_start, "$lte": detected_at}})
    today = await db.remote_account_status_events.count_documents({**base_query, "detected_at": {"$gte": today_start, "$lte": detected_at}})
    return {"one_hour": one_hour, "today": today}


async def _notification_batch_capacity_summary(db: AsyncIOMotorDatabase, *, events: list[Any]) -> dict[str, Any]:
    keys_by_site: dict[str, set[int]] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        site_id = str(event.get("site_id") or "")
        if not site_id:
            continue
        group_ids = event.get("pro_group_ids") if isinstance(event.get("pro_group_ids"), list) else event.get("group_ids")
        if not isinstance(group_ids, list):
            continue
        for value in group_ids:
            try:
                keys_by_site.setdefault(site_id, set()).add(int(value))
            except (TypeError, ValueError):
                continue
    summaries: list[dict[str, Any]] = []
    for site_id, group_ids in keys_by_site.items():
        for group_id in sorted(group_ids):
            summary = await _notification_capacity_summary(db, site_id=site_id, group_ids=[group_id])
            if summary:
                summaries.append(summary)
    return _merge_notification_capacity_summaries(summaries)


async def _notification_capacity_summary(db: AsyncIOMotorDatabase, *, site_id: str, group_ids: Any) -> dict[str, Any]:
    numeric_ids: list[int] = []
    if isinstance(group_ids, list):
        for value in group_ids:
            try:
                group_id = int(value)
            except (TypeError, ValueError):
                continue
            if group_id not in numeric_ids:
                numeric_ids.append(group_id)
    for group_id in numeric_ids:
        try:
            summary = await _get_or_update_group_capacity_summary(db, site_id, group_id)
        except Exception as exc:  # noqa: BLE001 - notification should still send the 401 core signal.
            logger.warning("sub2api_account_probe_capacity_summary_failed site_id=%s group_id=%s error=%s", site_id, group_id, exc)
            continue
        if _normalized_account_type(summary.get("account_type")) == "pro":
            return summary
    if numeric_ids:
        try:
            return await _get_or_update_group_capacity_summary(db, site_id, numeric_ids[0])
        except Exception as exc:  # noqa: BLE001
            logger.warning("sub2api_account_probe_capacity_summary_fallback_failed site_id=%s group_id=%s error=%s", site_id, numeric_ids[0], exc)
    return {}


def _merge_notification_capacity_summaries(summaries: list[dict[str, Any]]) -> dict[str, Any]:
    if not summaries:
        return {}
    sum_fields = (
        "available_accounts",
        "active_available_accounts",
        "reserve_available_accounts",
        "dynamic_five_hour_remaining_estimated_usd",
        "five_hour_actual_remaining_usd",
        "seven_day_remaining_estimated_usd",
        "seven_day_actual_remaining_usd",
    )
    min_fields = (
        "recent_day_five_hour_peak_multiple",
        "seven_day_five_hour_peak_multiple",
        "five_hour_peak_multiple",
        "current_speed_days",
        "seven_day_peak_speed_days",
    )
    result: dict[str, Any] = {"account_type": "pro", "summary_count": len(summaries)}
    for field in sum_fields:
        result[field] = round(sum(_optional_float(summary.get(field)) or 0 for summary in summaries), 4)
    for field in min_fields:
        values = [_optional_float(summary.get(field)) for summary in summaries]
        numeric = [value for value in values if value is not None]
        result[field] = round(min(numeric), 4) if numeric else None
    return result


def _capacity_red_status_lines(summary: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    recent_day_peak = _optional_float(summary.get("recent_day_five_hour_peak_multiple"))
    if recent_day_peak is not None and recent_day_peak < 1:
        lines.append(f"- 红色峰值容量：最近一天 5h {_format_multiple(recent_day_peak)}")
    seven_day_peak = _optional_float(summary.get("seven_day_five_hour_peak_multiple") or summary.get("five_hour_peak_multiple"))
    if seven_day_peak is not None and seven_day_peak < 1:
        lines.append(f"- 红色峰值容量：7天最高 5h {_format_multiple(seven_day_peak)}")
    current_speed_days = _optional_float(summary.get("current_speed_days"))
    if current_speed_days is not None and current_speed_days < 1:
        lines.append(f"- 红色预估天数：最近 24h {_format_days(current_speed_days)}")
    seven_day_peak_days = _optional_float(summary.get("seven_day_peak_speed_days"))
    if seven_day_peak_days is not None and seven_day_peak_days < 1:
        lines.append(f"- 红色预估天数：7天最高 24h {_format_days(seven_day_peak_days)}")
    return lines


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return _parse_datetime(value)


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _format_count(value: Any) -> str:
    parsed = _optional_float(value)
    if parsed is None:
        return "-"
    return str(int(parsed)) if parsed.is_integer() else f"{parsed:.1f}".rstrip("0").rstrip(".")


def _format_usd(value: Any) -> str:
    parsed = _optional_float(value)
    if parsed is None:
        return "$-"
    return f"${parsed:.2f}".rstrip("0").rstrip(".")


def _format_multiple(value: Any) -> str:
    parsed = _optional_float(value)
    if parsed is None:
        return "-"
    return f"{parsed:.2f}x"


def _format_days(value: Any) -> str:
    parsed = _optional_float(value)
    if parsed is None:
        return "-"
    if parsed < 1:
        hours = max(0.0, parsed * 24)
        return f"{hours:.1f}小时"
    return f"{parsed:.1f}天"


def _format_shanghai_time(value: datetime) -> str:
    return value.astimezone(SHANGHAI_TZ).strftime("%m/%d %H:%M")


def _usage_value(usage: dict[str, Any], key: str, *, suffix: str = "") -> str:
    value = usage.get(key)
    if value is None or value == "":
        return "-"
    if isinstance(value, (int, float)):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    else:
        text = str(value)
    return f"{text}{suffix}"


def _identity_compare_value(field: str, account: dict[str, Any]) -> Any:
    return {
        "current_status": account.get("status"),
        "current_error_message": account.get("error_message"),
        "current_schedulable": account.get("schedulable"),
    }.get(field)


def _identity_id(site_id: str, normalized_email: str) -> str:
    return f"{site_id}:{normalized_email}"


def _is_401(account: dict[str, Any]) -> bool:
    return bool(ERROR_401_PATTERN.search(str(account.get("error_message") or "")))


def _confirmed_401_state(
    *,
    account: dict[str, Any],
    previous_is_401: bool,
    previous_recovery_streak: int,
) -> dict[str, int | bool]:
    if _is_401(account):
        return {"is_401": True, "recovery_streak": 0}
    if not previous_is_401:
        return {"is_401": False, "recovery_streak": 0}
    if not _is_normal(account):
        return {"is_401": True, "recovery_streak": 0}
    recovery_streak = previous_recovery_streak + 1
    return {
        "is_401": recovery_streak < CONFIRMED_401_RECOVERY_COUNT,
        "recovery_streak": recovery_streak,
    }


def _is_normal(account: dict[str, Any]) -> bool:
    status = str(account.get("status") or "").lower()
    return status in STATUS_NORMAL and not account.get("error_message")


def _is_abnormal(account: dict[str, Any]) -> bool:
    status = str(account.get("status") or "").lower()
    return status in STATUS_ABNORMAL or bool(account.get("error_message"))


def _event_severity(account: dict[str, Any]) -> str:
    if _is_401(account):
        return "critical"
    if _is_abnormal(account):
        return "warning"
    return "info"


def _error_category(value: Any) -> str | None:
    text = str(value or "").lower()
    if not text:
        return None
    if "token invalidated" in text or "token_invalidated" in text:
        return "token_invalidated"
    if "token refresh failed" in text or "refresh token" in text or "openai_oauth_token_refresh_failed" in text:
        return "token_refresh_failed"
    if "token revoked" in text or "revoked" in text:
        return "token_revoked"
    if "authentication failed" in text:
        return "authentication_failed"
    if "401" in text:
        return "unknown_401"
    return "unknown"


def _usage_snapshot(account: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "codex_5h_used_percent",
        "codex_7d_used_percent",
        "codex_5h_reset_at",
        "codex_7d_reset_at",
        "codex_5h_reset_after_seconds",
        "codex_7d_reset_after_seconds",
        "codex_5h_window_minutes",
        "codex_7d_window_minutes",
        "codex_5h_actual_cost",
        "codex_7d_actual_cost",
        "codex_5h_total_cost",
        "codex_7d_total_cost",
        "codex_total_cost",
        "codex_total_actual_cost",
        "codex_5h_request_count",
        "codex_7d_request_count",
        "codex_total_request_count",
        "codex_5h_token_count",
        "codex_7d_token_count",
        "codex_total_token_count",
        "codex_usage_updated_at",
        "codex_usage_synced_at",
    )
    result = {}
    for key in keys:
        value = _first_present(account, extra, key)
        if value is not None:
            result[key] = value
    return result


def _compact_raw(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": account.get("id"),
        "email": _first_present(account, account.get("credentials") if isinstance(account.get("credentials"), dict) else {}, account.get("extra") if isinstance(account.get("extra"), dict) else {}, "email"),
        "status": account.get("status"),
        "schedulable": account.get("schedulable"),
        "error_message": account.get("error_message"),
        "groups": account.get("group_ids") or account.get("groups") or account.get("account_groups"),
        "updated_at": account.get("updated_at"),
        "last_used_at": account.get("last_used_at"),
    }


def _stable_hash(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()


def _extract_group_ids(account: dict[str, Any]) -> list[int]:
    values: list[Any] = []
    for key in ("group_ids", "groups", "account_groups", "group_id"):
        value = account.get(key)
        if isinstance(value, list):
            values.extend(value)
        elif value is not None:
            values.append(value)
    result: list[int] = []
    for value in values:
        if isinstance(value, dict):
            value = value.get("id") or value.get("group_id")
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed not in result:
            result.append(parsed)
    return result


def _first_present(*items: Any) -> Any:
    containers = [item for item in items if isinstance(item, dict)]
    keys = [item for item in items if isinstance(item, str)]
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value is not None and value != "":
                return value
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lower = value.strip().lower()
        if lower in {"true", "1", "yes"}:
            return True
        if lower in {"false", "0", "no"}:
            return False
    return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None
