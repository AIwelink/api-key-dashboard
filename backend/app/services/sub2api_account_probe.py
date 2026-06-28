from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from app.services.sub2api import Sub2ApiClient
from app.services.sub2api_cache import get_site, list_sites
from app.utils import now_utc, serialize_doc


logger = logging.getLogger("app.sub2api_account_probe")

DEFAULT_PROBE_INTERVAL_SECONDS = 180
DEFAULT_SAMPLE_RETENTION_DAYS = 14
DEFAULT_MISSING_CONFIRM_COUNT = 3
PROBE_LOOP_SLEEP_SECONDS = 30
ACCOUNT_LIST_PAGE_SIZE = 200
MAX_ACCOUNT_LIST_PAGES = 100

STATUS_NORMAL = {"active", "ok", "healthy", "normal", "available"}
STATUS_ABNORMAL = {"abnormal", "error", "failed", "disabled", "inactive", "invalid", "revoked"}
ERROR_401_PATTERN = re.compile(r"401|token[_ -]?invalidated|token[_ -]?revoked|authentication failed|invalid_request_error", re.I)


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
    }
    updates = {key: payload[key] for key in allowed if key in payload and payload[key] is not None}
    updates["group_name"] = group_name
    updates["updated_at"] = now
    updates["updated_by"] = actor.get("_id")
    await db.group_observability_settings.update_one(
        {"_id": base["_id"]},
        {"$setOnInsert": base, "$set": updates},
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


async def probe_scheduler_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        try:
            await probe_due_sites(db)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sub2api_account_probe_scheduler_failed")
        await asyncio.sleep(PROBE_LOOP_SLEEP_SECONDS)


async def probe_due_sites(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    sites = (await list_sites(db)).get("items", [])
    results: list[dict[str, Any]] = []
    for site in sites:
        if not site or site.get("status") != "active":
            continue
        site_id = str(site.get("id"))
        try:
            if await _site_probe_due(db, site_id):
                results.append(await probe_site_accounts(db, site_id=site_id))
        except Exception as exc:  # noqa: BLE001 - each site is independent.
            logger.warning("sub2api_account_probe_site_failed site_id=%s error=%s", site_id, exc)
            results.append({"ok": False, "site_id": site_id, "message": str(exc)})
    return {"ok": True, "results": results, "probed": sum(1 for item in results if item.get("ok") is True)}


async def probe_site_accounts(db: AsyncIOMotorDatabase, *, site_id: str) -> dict[str, Any]:
    site = await get_site(db, site_id, include_token=True)
    if not site:
        return {"ok": False, "site_id": site_id, "message": "sub2api site not found"}
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
        accounts = [_normalize_probe_account(item) for item in await _fetch_all_accounts(client)]
        fetched_at = now_utc()
        filtered_accounts = [account for account in accounts if _account_in_enabled_groups(account, enabled_group_ids)]
        counters["accounts_seen"] = len(filtered_accounts)

        by_email: dict[str, list[dict[str, Any]]] = {}
        for account in filtered_accounts:
            email = account.get("normalized_email")
            if email:
                by_email.setdefault(email, []).append(account)
        for email, same_email_accounts in by_email.items():
            if len({str(item.get("remote_account_id")) for item in same_email_accounts}) > 1:
                counters["duplicate_email_count"] += 1
                if any(_setting_for_account(settings, item).get("record_duplicate_email_warning", True) for item in same_email_accounts):
                    await _write_event(
                        db,
                        site_id=site_id,
                        event_type="duplicate_email_detected",
                        severity="warning",
                        detected_at=fetched_at,
                        account=same_email_accounts[0],
                        details={"remote_account_ids": [item.get("remote_account_id") for item in same_email_accounts], "count": len(same_email_accounts)},
                    )

        seen_identity_ids: set[str] = set()
        seen_remote_ids: set[Any] = set()
        sample_ops = []
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
            )
            if is_new:
                counters["accounts_new"] += 1
            if changed:
                counters["accounts_changed"] += 1
            if _is_401(account):
                counters["accounts_401"] += 1

        for account in filtered_accounts:
            remote_id = account.get("remote_account_id")
            normalized_email = account.get("normalized_email")
            if not remote_id or not normalized_email:
                continue
            setting = _setting_for_account(settings, account)
            session = await db.remote_account_sessions.find_one({"identity_id": _identity_id(site_id, normalized_email), "status": "open"})
            if setting.get("detailed_enabled") is not False and setting.get("record_usage_samples") is not False:
                sample_ops.append(_sample_update(site_id, run_id, account, session or {"_id": None}, setting, fetched_at))

        if sample_ops:
            await db.remote_account_probe_samples.bulk_write(sample_ops, ordered=False)

        missing_counts = await _mark_missing_identities(
            db,
            site_id=site_id,
            seen_identity_ids=seen_identity_ids,
            detected_at=fetched_at,
        )
        counters.update(missing_counts)
        finished_at = now_utc()
        await db.remote_account_probe_runs.update_one(
            {"_id": run_id},
            {
                "$set": {
                    "status": "succeeded",
                    "finished_at": finished_at,
                    "duration_ms": int((finished_at - started_at).total_seconds() * 1000),
                    **counters,
                }
            },
        )
        await db.remote_account_probe_meta.update_one(
            {"_id": site_id},
            {"$set": {"site_id": site_id, "last_probe_at": finished_at, "last_run_id": run_id, "status": "succeeded", "updated_at": finished_at}},
            upsert=True,
        )
        logger.info("sub2api_account_probe_finished site_id=%s accounts=%s changed=%s 401=%s", site_id, counters["accounts_seen"], counters["accounts_changed"], counters["accounts_401"])
        return {"ok": True, "site_id": site_id, "run_id": run_id, **counters}
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


async def _site_probe_due(db: AsyncIOMotorDatabase, site_id: str) -> bool:
    meta = await db.remote_account_probe_meta.find_one({"_id": site_id})
    last_probe_at = _parse_datetime(meta.get("last_probe_at")) if meta else None
    interval = await _minimum_probe_interval(db, site_id)
    if not last_probe_at:
        return True
    return now_utc() - last_probe_at >= timedelta(seconds=interval)


async def _minimum_probe_interval(db: AsyncIOMotorDatabase, site_id: str) -> int:
    cursor = db.group_observability_settings.find({"site_id": site_id, "enabled": {"$ne": False}}, {"probe_interval_seconds": 1})
    intervals = [int(doc.get("probe_interval_seconds") or DEFAULT_PROBE_INTERVAL_SECONDS) async for doc in cursor]
    return max(60, min(intervals) if intervals else DEFAULT_PROBE_INTERVAL_SECONDS)


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
        "plan_type": _first_present(account, credentials, extra, "plan_type"),
        "last_used_at": _first_present(account, extra, "last_used_at"),
        "updated_at": _first_present(account, extra, "updated_at"),
        "usage_snapshot": _usage_snapshot(account, extra),
        "raw_hash": _stable_hash(_compact_raw(account)),
    }
    return normalized


def _collapse_probe_accounts_by_email(accounts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_email: dict[str, dict[str, Any]] = {}
    passthrough: list[dict[str, Any]] = []
    for account in accounts:
        email = account.get("normalized_email")
        if not email:
            passthrough.append(account)
            continue
        current = by_email.get(email)
        if current is None:
            collapsed = dict(account)
            collapsed["remote_account_ids"] = [account.get("remote_account_id")]
            collapsed["duplicate_remote_count"] = 1
            by_email[email] = collapsed
            continue
        by_email[email] = _merge_probe_duplicate_account(current, account)
    return [*by_email.values(), *passthrough]


def _merge_probe_duplicate_account(left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    merged = dict(left)
    remote_ids = [*_remote_ids(left), right.get("remote_account_id")]
    merged["remote_account_ids"] = [item for item in remote_ids if item is not None]
    merged["duplicate_remote_count"] = len(set(str(item) for item in merged["remote_account_ids"]))
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
    current_session_id = identity.get("current_session_id") if identity else None
    if current_session_id:
        session = await db.remote_account_sessions.find_one({"_id": current_session_id, "status": "open"})
        if session and str(session.get("remote_account_id")) == str(remote_id):
            return session
    session_index = int((identity or {}).get("total_sessions") or 0) + 1
    session_id = f"{site_id}:{normalized_email}:{session_index}"
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
) -> bool:
    identity_id = _identity_id(site_id, account["normalized_email"])
    event_enabled = setting.get("record_status_events") is not False
    changed = False
    if identity is None:
        changed = True
        if event_enabled:
            await _write_event(db, site_id=site_id, event_type="remote_account_seen_first", severity="info", detected_at=detected_at, account=account, session=session)
    else:
        if identity.get("current_presence") in {"removed", "missing_suspected"} or str(identity.get("current_remote_account_id")) != str(account.get("remote_account_id")):
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
        is_401 = _is_401(account)
        if is_401 and not was_401 and event_enabled:
            await _write_event(db, site_id=site_id, event_type="401_detected", severity="critical", detected_at=detected_at, account=account, session=session, previous=identity)
        if was_401 and _is_normal(account) and event_enabled:
            await _write_event(db, site_id=site_id, event_type="401_recovered", severity="info", detected_at=detected_at, account=account, session=session, previous=identity)

    updates = {
        "site_id": site_id,
        "normalized_email": account["normalized_email"],
        "email": account.get("email"),
        "last_seen_at": detected_at,
        "last_present_at": detected_at,
        "current_presence": "present",
        "missing_count": 0,
        "current_remote_account_id": account.get("remote_account_id"),
        "current_remote_account_ids": account.get("remote_account_ids") or [account.get("remote_account_id")],
        "duplicate_remote_count": account.get("duplicate_remote_count") or 1,
        "current_session_id": session["_id"],
        "current_status": account.get("status"),
        "current_schedulable": account.get("schedulable"),
        "current_error_message": account.get("error_message"),
        "current_is_401": _is_401(account),
        "current_group_ids": account.get("group_ids") or [],
        "plan_type": account.get("plan_type"),
        "last_usage_snapshot": account.get("usage_snapshot") or {},
        "missing_confirm_count": int(setting.get("missing_confirm_count") or DEFAULT_MISSING_CONFIRM_COUNT),
        "last_event_at": detected_at if changed else (identity or {}).get("last_event_at"),
        "updated_at": detected_at,
    }
    increments: dict[str, int] = {}
    if identity is None:
        updates["first_seen_at"] = detected_at
        increments["total_sessions"] = 1
    elif str(identity.get("current_session_id")) != str(session["_id"]):
        increments["total_sessions"] = 1
    if _is_401(account) and not bool((identity or {}).get("current_is_401")):
        if not identity or not identity.get("first_401_at"):
            updates["first_401_at"] = detected_at
        updates["last_401_at"] = detected_at
        increments["total_401_count"] = 1
    if bool((identity or {}).get("current_is_401")) and _is_normal(account):
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
    updates: dict[str, Any] = {
        "group_ids_last": account.get("group_ids") or [],
        "plan_type_last": account.get("plan_type"),
        "error_message_last": account.get("error_message"),
        "last_usage_snapshot": account.get("usage_snapshot") or {},
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
    detected_at: datetime,
) -> dict[str, int]:
    suspected = 0
    removed = 0
    query: dict[str, Any] = {"site_id": site_id, "current_presence": {"$in": ["present", "missing_suspected"]}}
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
    await db.remote_account_status_events.insert_one(doc)


def _sample_update(site_id: str, run_id: str, account: dict[str, Any], session: dict[str, Any], setting: dict[str, Any], sampled_at: datetime) -> UpdateOne:
    retention_days = int(setting.get("sample_retention_days") or DEFAULT_SAMPLE_RETENTION_DAYS)
    sample_id = f"{run_id}:{account.get('remote_account_id')}"
    doc = {
        "_id": sample_id,
        "site_id": site_id,
        "probe_run_id": run_id,
        "identity_id": _identity_id(site_id, account["normalized_email"]),
        "session_id": session["_id"],
        "normalized_email": account.get("normalized_email"),
        "remote_account_id": account.get("remote_account_id"),
        "sampled_at": sampled_at,
        "status": account.get("status"),
        "schedulable": account.get("schedulable"),
        "error_message": account.get("error_message"),
        "group_ids": account.get("group_ids") or [],
        "plan_type": account.get("plan_type"),
        "last_used_at": account.get("last_used_at"),
        "updated_at": account.get("updated_at"),
        **account.get("usage_snapshot", {}),
        "usage_snapshot": account.get("usage_snapshot") or {},
        "raw_hash": account.get("raw_hash"),
        "created_at": sampled_at,
        "expires_at": sampled_at + timedelta(days=retention_days),
    }
    return UpdateOne({"_id": sample_id}, {"$set": doc}, upsert=True)


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
