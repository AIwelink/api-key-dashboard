import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReplaceOne, UpdateOne

from app.config import get_settings
from app.services.sub2api import Sub2ApiClient
from app.utils import now_utc, serialize_doc


logger = logging.getLogger("app.sub2api_cache")

DEFAULT_SITE_ID = "default"
DEFAULT_REFRESH_INTERVAL_MINUTES = 5
REFRESH_DEBOUNCE_SECONDS = 3

_refresh_tasks: dict[str, asyncio.Task] = {}
_refresh_tasks_lock = asyncio.Lock()
_site_locks: dict[str, asyncio.Lock] = {}


def _default_site_base() -> dict[str, Any]:
    settings = get_settings()
    return {
        "_id": DEFAULT_SITE_ID,
        "id": DEFAULT_SITE_ID,
        "name": "sub2api 5002",
        "base_url": settings.sub2api_base_url,
        "status": "active" if settings.sub2api_base_url else "disabled",
        "token_configured": bool(settings.sub2api_token),
        "source": "env",
    }


async def get_site(db: AsyncIOMotorDatabase, site_id: str = DEFAULT_SITE_ID) -> dict[str, Any] | None:
    if site_id != DEFAULT_SITE_ID:
        return None
    doc = await db.sub2api_sites.find_one({"_id": site_id}) or {}
    site = _default_site_base()
    site.update(doc)
    site.setdefault("refresh_interval_minutes", DEFAULT_REFRESH_INTERVAL_MINUTES)
    site["id"] = site["_id"]
    return serialize_doc(site)


async def list_sites(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    site = await get_site(db, DEFAULT_SITE_ID)
    return {"items": [site] if site else [], "total": 1 if site else 0}


async def update_site_config(db: AsyncIOMotorDatabase, site_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    if site_id != DEFAULT_SITE_ID:
        return {}
    updates: dict[str, Any] = {"updated_at": now_utc()}
    if "refresh_interval_minutes" in payload:
        interval = int(payload["refresh_interval_minutes"])
        updates["refresh_interval_minutes"] = max(1, min(interval, 1440))
    await db.sub2api_sites.update_one({"_id": site_id}, {"$set": updates, "$setOnInsert": {"created_at": now_utc()}}, upsert=True)
    return await get_site(db, site_id) or {}


async def get_cache_meta(db: AsyncIOMotorDatabase, site_id: str) -> dict[str, Any]:
    meta = await db.sub2api_cache_meta.find_one({"_id": site_id}) or {"_id": site_id, "site_id": site_id}
    return serialize_doc(meta)


async def list_cached_groups(
    db: AsyncIOMotorDatabase,
    site_id: str,
    *,
    page: int = 1,
    page_size: int = 100,
) -> dict[str, Any]:
    query = {"site_id": site_id}
    total = await db.sub2api_groups_cache.count_documents(query)
    cursor = db.sub2api_groups_cache.find(query).sort("group_id", 1).skip((page - 1) * page_size).limit(page_size)
    docs = [doc async for doc in cursor]
    return {
        "items": [serialize_doc(doc.get("group", {})) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "cache_meta": await get_cache_meta(db, site_id),
    }


async def list_cached_group_accounts(
    db: AsyncIOMotorDatabase,
    site_id: str,
    group_id: int,
    *,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    query: dict[str, Any] = {"site_id": site_id, "group_ids": group_id}
    if status_filter:
        query["status"] = status_filter
    total = await db.sub2api_accounts_cache.count_documents(query)
    cursor = (
        db.sub2api_accounts_cache.find(query)
        .sort([("status", 1), ("sub2api_account_id", 1)])
        .skip((page - 1) * page_size)
        .limit(page_size)
    )
    docs = [doc async for doc in cursor]
    return {
        "items": [serialize_doc(_account_snapshot_with_cache_sync(doc)) for doc in docs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "pages": max(1, (total + page_size - 1) // page_size),
        "cache_meta": await get_cache_meta(db, site_id),
        "capacity_summary": await _get_or_update_group_capacity_summary(db, site_id, group_id),
    }


async def refresh_site_cache(db: AsyncIOMotorDatabase, site_id: str = DEFAULT_SITE_ID) -> dict[str, Any]:
    site = await get_site(db, site_id)
    if not site:
        logger.warning("sub2api_refresh_skipped site_id=%s reason=site_not_found", site_id)
        return {"ok": False, "message": "sub2api site not found"}

    lock = _site_locks.setdefault(site_id, asyncio.Lock())
    async with lock:
        started_at = now_utc()
        logger.info("sub2api_refresh_start site_id=%s", site_id)
        try:
            await db.sub2api_cache_meta.update_one(
                {"_id": site_id},
                {"$set": {"site_id": site_id, "status": "refreshing", "started_at": started_at, "updated_at": started_at}},
                upsert=True,
            )

            client = Sub2ApiClient()
            groups_data = await client.list_groups(page=1, page_size=500)
            groups = groups_data.get("items", [])
            accounts = [_normalize_account_snapshot(account) for account in await _fetch_all_accounts(client)]
            usage_records = await _fetch_all_usage_records(client)
            fetched_at = now_utc()
            _apply_usage_aggregates(accounts, usage_records, fetched_at)
            group_capacity_summaries = _group_capacity_summaries(accounts)

            group_ops = []
            for group in groups:
                group_id = group.get("id")
                if group_id is None:
                    continue
                capacity_summary = group_capacity_summaries.get(group_id, _capacity_summary_for_accounts([]))
                group_ops.append(
                    ReplaceOne(
                        {"_id": f"{site_id}:{group_id}"},
                        {
                            "_id": f"{site_id}:{group_id}",
                            "site_id": site_id,
                            "group_id": group_id,
                            "group": _group_with_capacity_summary(group, capacity_summary),
                            "capacity_summary": capacity_summary,
                            "fetched_at": fetched_at,
                        },
                        upsert=True,
                    )
                )
            account_ops = [
                ReplaceOne(
                    {"_id": f"{site_id}:{account.get('id')}"},
                    {
                        "_id": f"{site_id}:{account.get('id')}",
                        "site_id": site_id,
                        "sub2api_account_id": account.get("id"),
                        "group_ids": _extract_group_ids(account),
                        "status": account.get("status"),
                        "schedulable": account.get("schedulable"),
                        "account": account,
                        "fetched_at": fetched_at,
                    },
                    upsert=True,
                )
                for account in accounts
                if account.get("id") is not None
            ]
            if group_ops:
                await db.sub2api_groups_cache.bulk_write(group_ops, ordered=False)
            if account_ops:
                await db.sub2api_accounts_cache.bulk_write(account_ops, ordered=False)

            group_ids = [group.get("id") for group in groups if group.get("id") is not None]
            account_ids = [account.get("id") for account in accounts if account.get("id") is not None]
            await db.sub2api_groups_cache.delete_many({"site_id": site_id, "group_id": {"$nin": group_ids}})
            await db.sub2api_accounts_cache.delete_many({"site_id": site_id, "sub2api_account_id": {"$nin": account_ids}})

            summary = {
                "ok": True,
                "site_id": site_id,
                "status": "succeeded",
                "groups": len(groups),
                "accounts": len(accounts),
                "started_at": started_at,
                "finished_at": now_utc(),
            }
            await db.sub2api_cache_meta.update_one(
                {"_id": site_id},
                {"$set": {**summary, "last_refreshed_at": summary["finished_at"], "updated_at": summary["finished_at"]}},
                upsert=True,
            )
            logger.info(
                "sub2api_refresh_finished site_id=%s groups=%s accounts=%s",
                site_id,
                len(groups),
                len(accounts),
            )
            return serialize_doc(summary)
        except asyncio.CancelledError:
            logger.info("sub2api_refresh_cancelled site_id=%s", site_id)
            raise


async def request_debounced_refresh(db: AsyncIOMotorDatabase, site_id: str = DEFAULT_SITE_ID) -> dict[str, Any]:
    async with _refresh_tasks_lock:
        current = _refresh_tasks.get(site_id)
        if current and not current.done():
            task = current
        else:
            task = asyncio.create_task(_delayed_refresh(db, site_id))
            _refresh_tasks[site_id] = task
    return await task


async def refresh_scheduler_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        try:
            site = await get_site(db, DEFAULT_SITE_ID)
            if site and site.get("status") == "active":
                interval = int(site.get("refresh_interval_minutes") or DEFAULT_REFRESH_INTERVAL_MINUTES)
                meta = await get_cache_meta(db, DEFAULT_SITE_ID)
                last_refreshed_at = meta.get("last_refreshed_at")
                if _is_due(last_refreshed_at, interval):
                    await request_debounced_refresh(db, DEFAULT_SITE_ID)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sub2api_refresh_scheduler_failed")
        await asyncio.sleep(30)


async def _delayed_refresh(db: AsyncIOMotorDatabase, site_id: str) -> dict[str, Any]:
    requested_at = now_utc()
    await db.sub2api_cache_meta.update_one(
        {"_id": site_id},
        {"$set": {"site_id": site_id, "status": "scheduled", "requested_at": requested_at, "updated_at": requested_at}},
        upsert=True,
    )
    await asyncio.sleep(REFRESH_DEBOUNCE_SECONDS)
    return await refresh_site_cache(db, site_id)


async def _fetch_all_accounts(client: Sub2ApiClient) -> list[dict[str, Any]]:
    page = 1
    page_size = 200
    accounts: list[dict[str, Any]] = []
    total: int | None = None
    while page <= 100:
        data = await client.list_accounts(page=page, page_size=page_size)
        items = data.get("items", [])
        if total is None:
            total_value = data.get("total")
            total = int(total_value) if isinstance(total_value, int) else None
        accounts.extend(items)
        if not items:
            break
        if total is not None and page * page_size >= total:
            break
        page += 1
    return accounts


async def _fetch_all_usage_records(client: Sub2ApiClient) -> list[dict[str, Any]]:
    page = 1
    page_size = 200
    records: list[dict[str, Any]] = []
    total: int | None = None
    while page <= 100:
        try:
            payload = await client.request_admin("GET", "/usage", params={"page": page, "page_size": page_size})
        except Exception:
            logger.exception("sub2api_usage_fetch_failed page=%s", page)
            return records
        data = payload.get("data", payload)
        items = data.get("items", []) if isinstance(data, dict) else []
        if total is None and isinstance(data, dict):
            total_value = data.get("total")
            total = int(total_value) if isinstance(total_value, int) else None
        records.extend(item for item in items if isinstance(item, dict))
        if not items:
            break
        if total is not None and page * page_size >= total:
            break
        page += 1
    return records


def _apply_usage_aggregates(accounts: list[dict[str, Any]], usage_records: list[dict[str, Any]], synced_at: datetime) -> None:
    accounts_by_id = {account.get("id"): account for account in accounts if account.get("id") is not None}
    windows = {
        "5h": synced_at - timedelta(hours=5),
        "7d": synced_at - timedelta(days=7),
    }
    empty = {
        "request_count": 0,
        "token_count": 0,
        "actual_cost": 0.0,
        "total_cost": 0.0,
    }
    aggregates: dict[Any, dict[str, dict[str, float]]] = {
        account_id: {window: dict(empty) for window in windows}
        for account_id in accounts_by_id
    }

    for record in usage_records:
        account_id = record.get("account_id")
        if account_id not in accounts_by_id:
            continue
        created_at = _parse_datetime(record.get("created_at"))
        if created_at is None:
            continue
        for window, starts_at in windows.items():
            if created_at < starts_at:
                continue
            aggregate = aggregates.setdefault(account_id, {name: dict(empty) for name in windows})[window]
            aggregate["request_count"] += 1
            aggregate["token_count"] += _usage_record_tokens(record)
            aggregate["actual_cost"] += float(_number_or_none(record.get("actual_cost")) or 0)
            aggregate["total_cost"] += float(_number_or_none(record.get("total_cost")) or 0)

    for account_id, account in accounts_by_id.items():
        extra = dict(account.get("extra") if isinstance(account.get("extra"), dict) else {})
        account["codex_usage_synced_at"] = synced_at
        extra["codex_usage_synced_at"] = synced_at
        for window in windows:
            aggregate = aggregates.get(account_id, {}).get(window, dict(empty))
            prefix = f"codex_{window}"
            values = {
                f"{prefix}_request_count": int(aggregate["request_count"]),
                f"{prefix}_token_count": int(aggregate["token_count"]),
                f"{prefix}_actual_cost": round(float(aggregate["actual_cost"]), 6),
                f"{prefix}_total_cost": round(float(aggregate["total_cost"]), 6),
            }
            for key, value in values.items():
                account[key] = value
                extra[key] = value
        account["extra"] = extra


def _usage_record_tokens(record: dict[str, Any]) -> int:
    token_fields = (
        "input_tokens",
        "output_tokens",
        "cache_creation_tokens",
        "cache_read_tokens",
    )
    return int(sum(_number_or_none(record.get(field)) or 0 for field in token_fields))


async def _get_or_update_group_capacity_summary(db: AsyncIOMotorDatabase, site_id: str, group_id: int) -> dict[str, Any]:
    cursor = db.sub2api_accounts_cache.find({"site_id": site_id, "group_ids": group_id})
    accounts: list[dict[str, Any]] = []
    account_ops: list[UpdateOne] = []
    normalized_at = now_utc()
    async for doc in cursor:
        original_account = doc.get("account", {})
        normalized_account = _normalize_account_snapshot(original_account)
        accounts.append(normalized_account)
        if normalized_account != original_account:
            account_ops.append(
                UpdateOne(
                    {"_id": doc["_id"]},
                    {
                        "$set": {
                            "account": normalized_account,
                            "group_ids": _extract_group_ids(normalized_account),
                            "status": normalized_account.get("status"),
                            "schedulable": normalized_account.get("schedulable"),
                            "normalized_at": normalized_at,
                        }
                    },
                )
            )
    if account_ops:
        await db.sub2api_accounts_cache.bulk_write(account_ops, ordered=False)

    summary = _capacity_summary_for_accounts(accounts)
    await db.sub2api_groups_cache.update_one(
        {"site_id": site_id, "group_id": group_id},
        {"$set": {"capacity_summary": summary, "group.capacity_summary": summary, "capacity_calculated_at": now_utc()}},
    )
    return serialize_doc(summary)


def _group_capacity_summaries(accounts: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = {}
    for account in accounts:
        for group_id in _extract_group_ids(account):
            grouped.setdefault(group_id, []).append(account)
    return {group_id: _capacity_summary_for_accounts(group_accounts) for group_id, group_accounts in grouped.items()}


def _group_with_capacity_summary(group: dict[str, Any], capacity_summary: dict[str, Any] | None) -> dict[str, Any]:
    snapshot = dict(group)
    if capacity_summary is not None:
        snapshot["capacity_summary"] = capacity_summary
    return snapshot


def _capacity_summary_for_accounts(accounts: list[dict[str, Any]]) -> dict[str, Any]:
    capacity_accounts = [account for account in accounts if _is_capacity_account(account)]
    used_5h = _average_percent(_usage_number(account, "codex_5h_used_percent") for account in capacity_accounts)
    used_7d = _average_percent(_usage_number(account, "codex_7d_used_percent") for account in capacity_accounts)
    return {
        "available_accounts": len(capacity_accounts),
        "used_5h_percent": used_5h,
        "available_5h_percent": _clamp_percent(100 - used_5h),
        "used_7d_percent": used_7d,
        "available_7d_percent": _clamp_percent(100 - used_7d),
        "total_accounts": len(accounts),
        "calculated_at": now_utc(),
    }


def _is_capacity_account(account: dict[str, Any]) -> bool:
    status = str(account.get("status") or "").lower()
    if _is_temporary_rate_limit(account):
        return True
    return status == "active" and account.get("schedulable") is True and not account.get("error_message")


def _is_temporary_rate_limit(account: dict[str, Any]) -> bool:
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    rate_limit_reset_at = _parse_datetime(account.get("rate_limit_reset_at") or extra.get("rate_limit_reset_at"))
    temp_unschedulable_until = _parse_datetime(account.get("temp_unschedulable_until") or extra.get("temp_unschedulable_until"))
    has_active_until = any(
        value is not None and value > now_utc()
        for value in (rate_limit_reset_at, temp_unschedulable_until)
    )
    values = [
        account.get("status"),
        account.get("error_message"),
        account.get("temp_unschedulable_reason"),
        extra.get("error_message"),
        extra.get("last_error"),
    ]
    combined = " ".join(str(value).lower() for value in values if value is not None)
    return has_active_until or "429" in combined or "529" in combined


def _usage_number(account: dict[str, Any], key: str) -> int | float | None:
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    return _number_or_none(account.get(key) if account.get(key) is not None else extra.get(key))


def _average_percent(values: Any) -> int:
    numeric = [value for value in values if isinstance(value, (int, float))]
    if not numeric:
        return 0
    return _clamp_percent(round(sum(numeric) / len(numeric)))


def _clamp_percent(value: int | float) -> int:
    return max(0, min(100, round(value)))


def _extract_group_ids(account: dict[str, Any]) -> list[int]:
    ids: set[int] = set()
    group_ids = account.get("group_ids")
    if isinstance(group_ids, list):
        ids.update(item for item in group_ids if isinstance(item, int))
    groups = account.get("groups")
    if isinstance(groups, list):
        ids.update(group.get("id") for group in groups if isinstance(group, dict) and isinstance(group.get("id"), int))
    account_groups = account.get("account_groups")
    if isinstance(account_groups, list):
        ids.update(item.get("group_id") for item in account_groups if isinstance(item, dict) and isinstance(item.get("group_id"), int))
    return sorted(ids)


def _account_snapshot_with_cache_sync(doc: dict[str, Any]) -> dict[str, Any]:
    account = _normalize_account_snapshot(doc.get("account", {}))
    if account.get("codex_usage_synced_at") is None and doc.get("fetched_at") is not None:
        extra = dict(account.get("extra") if isinstance(account.get("extra"), dict) else {})
        account["codex_usage_synced_at"] = doc.get("fetched_at")
        extra["codex_usage_synced_at"] = doc.get("fetched_at")
        account["extra"] = extra
    return account


def _normalize_account_snapshot(account: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(account)
    extra = dict(normalized.get("extra") if isinstance(normalized.get("extra"), dict) else {})

    normalized["group_ids"] = _extract_group_ids(normalized)
    normalized["status"] = _first_present(
        normalized,
        extra,
        "status",
        "account_status",
        "state",
        "sub2api_status",
    )
    normalized["schedulable"] = _bool_or_none(
        _first_present(
            normalized,
            extra,
            "schedulable",
            "is_schedulable",
            "sub2api_schedulable",
        )
    )
    normalized["error_message"] = _first_present(
        normalized,
        extra,
        "error_message",
        "last_error",
        "error",
        "message",
    )
    normalized["current_concurrency"] = _number_or_none(_first_present(normalized, extra, "current_concurrency", "used_concurrency"))
    normalized["concurrency"] = _number_or_none(_first_present(normalized, extra, "concurrency", "max_concurrency"))
    normalized["load_factor"] = _number_or_none(_first_present(normalized, extra, "load_factor"))
    normalized["priority"] = _number_or_none(_first_present(normalized, extra, "priority"))

    for field in (
        "last_used_at",
        "rate_limited_at",
        "rate_limit_reset_at",
        "temp_unschedulable_until",
        "temp_unschedulable_reason",
        "expires_at",
    ):
        value = _first_present(normalized, extra, field)
        if value is not None:
            normalized[field] = value

    usage_values = {
        "codex_5h_used_percent": _usage_value(normalized, extra, "codex_5h_used_percent", "5h", "used_percent"),
        "codex_7d_used_percent": _usage_value(normalized, extra, "codex_7d_used_percent", "7d", "used_percent"),
        "codex_5h_reset_after_seconds": _usage_value(normalized, extra, "codex_5h_reset_after_seconds", "5h", "reset_after_seconds"),
        "codex_7d_reset_after_seconds": _usage_value(normalized, extra, "codex_7d_reset_after_seconds", "7d", "reset_after_seconds"),
        "codex_5h_request_count": _usage_value(normalized, extra, "codex_5h_request_count", "5h", "request_count"),
        "codex_7d_request_count": _usage_value(normalized, extra, "codex_7d_request_count", "7d", "request_count"),
        "codex_5h_token_count": _usage_value(normalized, extra, "codex_5h_token_count", "5h", "token_count"),
        "codex_7d_token_count": _usage_value(normalized, extra, "codex_7d_token_count", "7d", "token_count"),
        "codex_5h_actual_cost": _usage_value(normalized, extra, "codex_5h_actual_cost", "5h", "actual_cost"),
        "codex_7d_actual_cost": _usage_value(normalized, extra, "codex_7d_actual_cost", "7d", "actual_cost"),
        "codex_5h_total_cost": _usage_value(normalized, extra, "codex_5h_total_cost", "5h", "total_cost"),
        "codex_7d_total_cost": _usage_value(normalized, extra, "codex_7d_total_cost", "7d", "total_cost"),
        "codex_5h_reset_at": _first_present(normalized, extra, "codex_5h_reset_at", "5h_reset_at"),
        "codex_7d_reset_at": _first_present(normalized, extra, "codex_7d_reset_at", "7d_reset_at"),
        "codex_usage_updated_at": _first_present(normalized, extra, "codex_usage_updated_at", "usage_updated_at"),
        "codex_usage_synced_at": _first_present(normalized, extra, "codex_usage_synced_at", "usage_synced_at"),
    }
    for key, value in usage_values.items():
        if value is not None:
            normalized[key] = value
            extra[key] = value

    _refresh_window_from_reset_at(normalized, extra, "5h")
    _refresh_window_from_reset_at(normalized, extra, "7d")
    _clear_expired_transient_limits(normalized, extra)

    normalized["extra"] = extra
    return normalized


def _clear_expired_transient_limits(account: dict[str, Any], extra: dict[str, Any]) -> None:
    rate_limit_reset_at = _parse_datetime(account.get("rate_limit_reset_at") or extra.get("rate_limit_reset_at"))
    temp_unschedulable_until = _parse_datetime(account.get("temp_unschedulable_until") or extra.get("temp_unschedulable_until"))
    now = now_utc()

    if rate_limit_reset_at is not None and rate_limit_reset_at <= now:
        for key in ("rate_limited_at", "rate_limit_reset_at"):
            account.pop(key, None)
            extra.pop(key, None)

    if temp_unschedulable_until is not None and temp_unschedulable_until <= now:
        for key in ("temp_unschedulable_until", "temp_unschedulable_reason"):
            account.pop(key, None)
            extra.pop(key, None)


def _first_present(*items: Any) -> Any:
    containers = [item for item in items if isinstance(item, dict)]
    keys = [item for item in items if isinstance(item, str)]
    for container in containers:
        for key in keys:
            value = container.get(key)
            if value is not None and value != "":
                return value
    return None


def _number_or_none(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        try:
            parsed = float(value)
        except ValueError:
            return None
        return int(parsed) if parsed.is_integer() else parsed
    return None


def _bool_or_none(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return None


def _usage_value(account: dict[str, Any], extra: dict[str, Any], canonical_key: str, window: str, metric: str) -> Any:
    direct = _first_present(account, extra, canonical_key)
    if direct is not None:
        return direct

    compact_key = f"{window}_{metric}"
    codex_compact_key = f"codex_{compact_key}"
    for container in (account, extra):
        for key in (compact_key, codex_compact_key):
            value = container.get(key)
            if value is not None:
                return value

    for container in (account, extra):
        usage = container.get("usage") or container.get("usage_windows") or container.get("windows")
        if isinstance(usage, dict):
            window_data = usage.get(window) or usage.get(window.lower()) or usage.get(window.upper())
            if isinstance(window_data, dict):
                for key in (metric, metric.replace("_seconds", "")):
                    value = window_data.get(key)
                    if value is not None:
                        return value
    return None


def _refresh_window_from_reset_at(account: dict[str, Any], extra: dict[str, Any], window: str) -> None:
    reset_at_key = f"codex_{window}_reset_at"
    reset_after_key = f"codex_{window}_reset_after_seconds"
    used_key = f"codex_{window}_used_percent"
    reset_at = _parse_datetime(account.get(reset_at_key) or extra.get(reset_at_key))
    if reset_at is None:
        return
    remaining_seconds = max(0, int((reset_at - now_utc()).total_seconds()))
    account[reset_after_key] = remaining_seconds
    extra[reset_after_key] = remaining_seconds
    if remaining_seconds == 0:
        account[used_key] = 0
        extra[used_key] = 0


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _is_due(last_refreshed_at: Any, interval_minutes: int) -> bool:
    if not last_refreshed_at:
        return True
    if isinstance(last_refreshed_at, str):
        try:
            last_refreshed_at = datetime.fromisoformat(last_refreshed_at)
        except ValueError:
            return True
    if isinstance(last_refreshed_at, datetime) and last_refreshed_at.tzinfo is None:
        last_refreshed_at = last_refreshed_at.replace(tzinfo=UTC)
    return now_utc() - last_refreshed_at >= timedelta(minutes=interval_minutes)
