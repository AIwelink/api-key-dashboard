from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils import now_utc, serialize_doc


SHANGHAI_TZ = timezone(timedelta(hours=8))
MAX_LIMIT = 500
USAGE_FIELDS = (
    "codex_5h_used_percent",
    "codex_7d_used_percent",
    "codex_5h_reset_after_seconds",
    "codex_7d_reset_after_seconds",
    "codex_5h_request_count",
    "codex_7d_request_count",
    "codex_total_request_count",
    "codex_5h_token_count",
    "codex_7d_token_count",
    "codex_total_token_count",
    "codex_5h_actual_cost",
    "codex_7d_actual_cost",
    "codex_5h_total_cost",
    "codex_7d_total_cost",
    "codex_total_actual_cost",
    "codex_total_cost",
    "codex_usage_updated_at",
    "codex_usage_synced_at",
)


def _normalized_limit(value: int) -> int:
    return max(1, min(int(value or 100), MAX_LIMIT))


def _range_start(range_value: str | None) -> datetime | None:
    now = now_utc()
    value = (range_value or "24h").strip().lower()
    if value in {"all", "全部"}:
        return None
    if value in {"1h", "hour"}:
        return now - timedelta(hours=1)
    if value in {"6h"}:
        return now - timedelta(hours=6)
    if value in {"24h", "1d", "day"}:
        return now - timedelta(hours=24)
    if value in {"7d", "week"}:
        return now - timedelta(days=7)
    if value in {"today", "今日"}:
        local_start = now.astimezone(SHANGHAI_TZ).replace(hour=0, minute=0, second=0, microsecond=0)
        return local_start.astimezone(UTC)
    return now - timedelta(hours=24)


def _event_query(
    *,
    site_id: str | None = None,
    group_id: int | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    account_type: str | None = None,
    q: str | None = None,
    range_value: str | None = "24h",
    start_at: datetime | None = None,
    only_401: bool = False,
    only_abnormal: bool = False,
    only_pro: bool = False,
    only_cumulative: bool = False,
    only_delete_archive: bool = False,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    and_clauses: list[dict[str, Any]] = []
    start = start_at if start_at is not None else _range_start(range_value)
    if start:
        query["detected_at"] = {"$gte": start}
    if site_id:
        query["site_id"] = site_id
    if group_id is not None:
        query["current_group_ids"] = group_id
    if severity:
        query["severity"] = severity
    if only_cumulative and not event_type:
        event_type = "usage_rollover"
    if only_delete_archive and not event_type:
        and_clauses.append({"event_type": {"$in": ["remote_removed_confirmed", "missing_suspected"]}})
    elif event_type:
        query["event_type"] = event_type
    if account_type:
        and_clauses.append(
            {
                "$or": [
                    {"plan_type": account_type},
                    {"details.account_type": account_type},
                ]
            }
        )
    if only_401:
        and_clauses.append(
            {
                "$or": [
                    {"is_401": True},
                    {"event_type": "401_detected"},
                    {"error_category": {"$in": ["token_refresh_failed", "token_invalidated", "token_revoked", "authentication_failed", "unknown_401"]}},
                    {"current_error_message": {"$regex": "token refresh failed|refresh token|OPENAI_OAUTH_TOKEN_REFRESH_FAILED", "$options": "i"}},
                ]
            }
        )
    if only_abnormal:
        and_clauses.append(
            {
                "$or": [
                    {"severity": {"$in": ["warning", "critical"]}},
                    {"current_error_message": {"$exists": True, "$nin": [None, ""]}},
                    {"current_status": {"$in": ["error", "failed", "disabled", "invalid", "banned"]}},
                    {"is_401": True},
                ]
            }
        )
    if only_pro:
        and_clauses.append({"$or": [{"details.is_pro_pool": True}, {"plan_type": "pro"}, {"details.account_type": "pro"}]})
    if q and q.strip():
        stripped = q.strip()
        regex = {"$regex": re.escape(stripped), "$options": "i"}
        q_or = [
            {"name": regex},
            {"email": regex},
            {"normalized_email": regex},
            {"remote_account_id": regex},
            {"current_error_message": regex},
            {"raw_excerpt": regex},
        ]
        remote_id = _parse_remote_id_search(stripped)
        if remote_id is not None:
            q_or.append({"remote_account_id": remote_id})
        and_clauses.append({"$or": q_or})
    if and_clauses:
        query["$and"] = and_clauses
    return query


def _identity_query(
    *,
    site_id: str | None = None,
    group_id: int | None = None,
    account_type: str | None = None,
    q: str | None = None,
    presence: str | None = None,
    only_401: bool = False,
    only_abnormal: bool = False,
    only_pro: bool = False,
    only_cumulative: bool = False,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    and_clauses: list[dict[str, Any]] = []
    if site_id:
        query["site_id"] = site_id
    if group_id is not None:
        query["current_group_ids"] = group_id
    if presence:
        query["current_presence"] = presence
    if account_type:
        query["plan_type"] = account_type
    if only_401:
        and_clauses.append(
            {
                "$or": [
                    {"current_is_401": True},
                    {"current_error_message": {"$regex": "token refresh failed|refresh token|OPENAI_OAUTH_TOKEN_REFRESH_FAILED", "$options": "i"}},
                ]
            }
        )
    if only_abnormal:
        and_clauses.append(
            {
                "$or": [
                    {"current_is_401": True},
                    {"current_error_message": {"$exists": True, "$nin": [None, ""]}},
                    {"current_status": {"$in": ["error", "failed", "disabled", "invalid", "banned"]}},
                ]
            }
        )
    if only_pro:
        query["plan_type"] = "pro"
    if only_cumulative:
        and_clauses.append(
            {
                "$or": [
                    {"cumulative_usage_totals.codex_7d_actual_cost": {"$gt": 0}},
                    {"cumulative_usage_totals.codex_total_actual_cost": {"$gt": 0}},
                    {"cumulative_usage_snapshot.codex_7d_actual_cost_cumulative": {"$gt": 0}},
                ]
            }
        )
    if q and q.strip():
        stripped = q.strip()
        regex = {"$regex": re.escape(stripped), "$options": "i"}
        q_or = [
            {"email": regex},
            {"normalized_email": regex},
            {"current_error_message": regex},
            {"current_remote_account_id": regex},
        ]
        remote_id = _parse_remote_id_search(stripped)
        if remote_id is not None:
            q_or.append({"current_remote_account_id": remote_id})
            q_or.append({"current_remote_account_ids": remote_id})
        and_clauses.append({"$or": q_or})
    if and_clauses:
        query["$and"] = and_clauses
    return query


async def list_event_records(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None = None,
    group_id: int | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    account_type: str | None = None,
    q: str | None = None,
    range_value: str | None = "24h",
    only_401: bool = False,
    only_abnormal: bool = False,
    only_pro: bool = False,
    only_cumulative: bool = False,
    only_delete_archive: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    limit = _normalized_limit(limit)
    skip = max(0, int(skip or 0))
    query = _event_query(
        site_id=site_id,
        group_id=group_id,
        event_type=event_type,
        severity=severity,
        account_type=account_type,
        q=q,
        range_value=range_value,
        only_401=only_401,
        only_abnormal=only_abnormal,
        only_pro=only_pro,
        only_cumulative=only_cumulative,
        only_delete_archive=only_delete_archive,
    )
    total = await db.remote_account_status_events.count_documents(query)
    cursor = db.remote_account_status_events.find(query).sort([("detected_at", -1), ("created_at", -1)]).skip(skip).limit(limit)
    docs = _dedupe_redundant_duplicate_email_events([doc async for doc in cursor])
    context = await _event_context(db, docs)
    items = [_event_item(doc, context) for doc in docs]
    return {
        "items": items,
        "total": total,
        "skip": skip,
        "limit": limit,
        "summary": await event_records_summary(
            db,
            site_id=site_id,
            group_id=group_id,
            event_type=event_type,
            severity=severity,
            account_type=account_type,
            q=q,
            range_value=range_value,
            only_401=only_401,
            only_abnormal=only_abnormal,
            only_pro=only_pro,
            only_cumulative=only_cumulative,
            only_delete_archive=only_delete_archive,
        ),
    }


async def list_event_accounts(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None = None,
    group_id: int | None = None,
    account_type: str | None = None,
    q: str | None = None,
    presence: str | None = None,
    only_401: bool = False,
    only_abnormal: bool = False,
    only_pro: bool = False,
    only_cumulative: bool = False,
    skip: int = 0,
    limit: int = 100,
) -> dict[str, Any]:
    limit = _normalized_limit(limit)
    skip = max(0, int(skip or 0))
    query = _identity_query(
        site_id=site_id,
        group_id=group_id,
        account_type=account_type,
        q=q,
        presence=presence,
        only_401=only_401,
        only_abnormal=only_abnormal,
        only_pro=only_pro,
        only_cumulative=only_cumulative,
    )
    total = await db.remote_account_identities.count_documents(query)
    cursor = db.remote_account_identities.find(query).sort([("last_event_at", -1), ("updated_at", -1), ("last_seen_at", -1)]).skip(skip).limit(limit)
    docs = [doc async for doc in cursor]
    context = await _identity_context(db, docs)
    items = [_identity_item(doc, context) for doc in docs]
    return {
        "items": items,
        "total": total,
        "skip": skip,
        "limit": limit,
        "summary": await event_records_summary(
            db,
            site_id=site_id,
            group_id=group_id,
            account_type=account_type,
            q=q,
            range_value="24h",
            only_401=only_401,
            only_abnormal=only_abnormal,
            only_pro=only_pro,
            only_cumulative=only_cumulative,
        ),
    }


async def get_event_account_detail(db: AsyncIOMotorDatabase, identity_id: str) -> dict[str, Any]:
    identity = await db.remote_account_identities.find_one({"_id": identity_id})
    if not identity:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="event account not found")
    sessions = [doc async for doc in db.remote_account_sessions.find({"identity_id": identity_id}).sort("session_index", -1).limit(30)]
    events = _dedupe_redundant_duplicate_email_events([doc async for doc in db.remote_account_status_events.find({"identity_id": identity_id}).sort("detected_at", -1).limit(120)])
    samples = [doc async for doc in db.remote_account_probe_samples.find({"identity_id": identity_id}).sort("sampled_at", -1).limit(40)]
    context = await _event_context(db, events, identities=[identity], sessions=sessions)
    account_context = await _identity_context(db, [identity])
    return {
        "identity": _identity_item(identity, account_context),
        "sessions": [serialize_doc(_session_item(item)) for item in sessions],
        "events": [_event_item(item, context) for item in events],
        "samples": [serialize_doc(item) for item in samples],
        "raw": {
            "identity": serialize_doc(identity),
            "sessions": serialize_doc(sessions),
            "events": serialize_doc(events),
            "samples": serialize_doc(samples),
        },
    }


async def event_records_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None = None,
    group_id: int | None = None,
    event_type: str | None = None,
    severity: str | None = None,
    account_type: str | None = None,
    q: str | None = None,
    range_value: str | None = "24h",
    only_401: bool = False,
    only_abnormal: bool = False,
    only_pro: bool = False,
    only_cumulative: bool = False,
    only_delete_archive: bool = False,
) -> dict[str, Any]:
    query = _event_query(
        site_id=site_id,
        group_id=group_id,
        event_type=event_type,
        severity=severity,
        account_type=account_type,
        q=q,
        range_value=range_value,
        only_401=only_401,
        only_abnormal=only_abnormal,
        only_pro=only_pro,
        only_cumulative=only_cumulative,
        only_delete_archive=only_delete_archive,
    )
    today_start = _range_start("today")
    one_hour_start = now_utc() - timedelta(hours=1)
    identity_query = _identity_query(site_id=site_id, group_id=group_id, account_type=account_type, q=q, only_pro=only_pro)
    abnormal_identity_query = {
        **identity_query,
        "$or": [
            {"current_is_401": True},
            {"current_error_message": {"$exists": True, "$nin": [None, ""]}},
            {"current_status": {"$in": ["error", "failed", "disabled", "invalid", "banned"]}},
        ],
    }
    summary = {
        "total_events": await _distinct_event_account_count(db, query),
        "critical_events": await _distinct_event_account_count(db, {**query, "severity": "critical"}),
        "warning_events": await _distinct_event_account_count(db, {**query, "severity": "warning"}),
        "detected_401": await _distinct_event_account_count(db, {**query, "event_type": "401_detected"}),
        "recovered_401": await _distinct_event_account_count(db, {**query, "event_type": "401_recovered"}),
        "usage_rollovers": await _distinct_event_account_count(db, {**query, "event_type": "usage_rollover"}),
        "duplicate_email_events": await _distinct_event_account_count(db, {**query, "event_type": {"$in": ["duplicate_email_detected", "duplicate_email_resolved"]}}),
        "removed_events": await _distinct_event_account_count(db, {**query, "event_type": "remote_removed_confirmed"}),
        "today_events": await _distinct_event_account_count(db, {**query, "detected_at": {"$gte": today_start}}) if today_start else 0,
        "today_401": await _distinct_event_account_count(db, {**query, "event_type": "401_detected", "detected_at": {"$gte": today_start}}) if today_start else 0,
        "one_hour_401": await _distinct_event_account_count(db, {**query, "event_type": "401_detected", "detected_at": {"$gte": one_hour_start}}),
        "current_abnormal_accounts": await db.remote_account_identities.count_documents(abnormal_identity_query),
    }
    summary.update(await _cumulative_identity_totals(db, identity_query))
    last_event = await db.remote_account_status_events.find_one(query, sort=[("detected_at", -1)])
    summary["last_event_at"] = serialize_doc(last_event.get("detected_at")) if last_event else None
    return summary


async def _event_context(
    db: AsyncIOMotorDatabase,
    docs: list[dict[str, Any]],
    *,
    identities: list[dict[str, Any]] | None = None,
    sessions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    identities = identities or []
    sessions = sessions or []
    site_ids = {str(doc.get("site_id")) for doc in docs if doc.get("site_id")}
    site_ids.update(str(doc.get("site_id")) for doc in identities if doc.get("site_id"))
    identity_ids = {str(doc.get("identity_id")) for doc in docs if doc.get("identity_id")}
    identity_ids.update(str(doc.get("_id")) for doc in identities if doc.get("_id"))
    session_ids = {str(doc.get("session_id")) for doc in docs if doc.get("session_id")}
    session_ids.update(str(doc.get("_id")) for doc in sessions if doc.get("_id"))
    group_ids_by_site: dict[str, set[int]] = {}
    cache_keys: set[tuple[str, Any]] = set()
    cache_emails_by_site: dict[str, set[str]] = {}
    for doc in docs:
        site_id = str(doc.get("site_id") or "")
        remote_id = doc.get("remote_account_id")
        if site_id and remote_id is not None:
            cache_keys.add((site_id, remote_id))
        email = _normalize_email(doc.get("normalized_email") or doc.get("email"))
        if site_id and email:
            cache_emails_by_site.setdefault(site_id, set()).add(email)
        for group_id in _int_list(doc.get("current_group_ids") or doc.get("previous_group_ids")):
            group_ids_by_site.setdefault(site_id, set()).add(group_id)
    identities_map = {str(doc["_id"]): doc for doc in identities if doc.get("_id")}
    if identity_ids:
        async for doc in db.remote_account_identities.find({"_id": {"$in": list(identity_ids)}}):
            identities_map[str(doc["_id"])] = doc
            site_id = str(doc.get("site_id") or "")
            email = _normalize_email(doc.get("normalized_email") or doc.get("email"))
            if site_id and email:
                cache_emails_by_site.setdefault(site_id, set()).add(email)
            for remote_id in doc.get("current_remote_account_ids") or [doc.get("current_remote_account_id")]:
                if site_id and remote_id is not None:
                    cache_keys.add((site_id, remote_id))
    sessions_map = {str(doc["_id"]): doc for doc in sessions if doc.get("_id")}
    if session_ids:
        async for doc in db.remote_account_sessions.find({"_id": {"$in": list(session_ids)}}):
            sessions_map[str(doc["_id"])] = doc
            site_id = str(doc.get("site_id") or "")
            if site_id and doc.get("remote_account_id") is not None:
                cache_keys.add((site_id, doc.get("remote_account_id")))
    return {
        "sites": await _site_map(db, site_ids),
        "groups": await _group_name_map(db, group_ids_by_site),
        "identities": identities_map,
        "sessions": sessions_map,
        "local_accounts": await _local_account_map(db, [doc.get("normalized_email") or doc.get("email") for doc in docs]),
        "usage_cache": await _usage_cache_map(db, cache_keys=cache_keys, emails_by_site=cache_emails_by_site),
    }


async def _identity_context(db: AsyncIOMotorDatabase, docs: list[dict[str, Any]]) -> dict[str, Any]:
    site_ids = {str(doc.get("site_id")) for doc in docs if doc.get("site_id")}
    group_ids_by_site: dict[str, set[int]] = {}
    cache_keys: set[tuple[str, Any]] = set()
    cache_emails_by_site: dict[str, set[str]] = {}
    for doc in docs:
        site_id = str(doc.get("site_id") or "")
        email = _normalize_email(doc.get("normalized_email") or doc.get("email"))
        if site_id and email:
            cache_emails_by_site.setdefault(site_id, set()).add(email)
        for remote_id in doc.get("current_remote_account_ids") or [doc.get("current_remote_account_id")]:
            if site_id and remote_id is not None:
                cache_keys.add((site_id, remote_id))
        for group_id in _int_list(doc.get("current_group_ids")):
            group_ids_by_site.setdefault(site_id, set()).add(group_id)
    return {
        "sites": await _site_map(db, site_ids),
        "groups": await _group_name_map(db, group_ids_by_site),
        "local_accounts": await _local_account_map(db, [doc.get("normalized_email") or doc.get("email") for doc in docs]),
        "usage_cache": await _usage_cache_map(db, cache_keys=cache_keys, emails_by_site=cache_emails_by_site),
    }


async def _site_map(db: AsyncIOMotorDatabase, site_ids: set[str]) -> dict[str, dict[str, Any]]:
    if not site_ids:
        return {}
    sites: dict[str, dict[str, Any]] = {}
    async for doc in db.sub2api_sites.find({"_id": {"$in": list(site_ids)}}, {"token": 0}):
        site_id = str(doc.get("_id") or "")
        if site_id:
            sites[site_id] = serialize_doc(doc | {"id": site_id})
    return sites


async def _group_name_map(db: AsyncIOMotorDatabase, group_ids_by_site: dict[str, set[int]]) -> dict[tuple[str, int], str]:
    result: dict[tuple[str, int], str] = {}
    for site_id, group_ids in group_ids_by_site.items():
        if not site_id or not group_ids:
            continue
        async for doc in db.sub2api_groups_cache.find({"site_id": site_id, "group_id": {"$in": list(group_ids)}}, {"group_id": 1, "group.name": 1}):
            group = doc.get("group") if isinstance(doc.get("group"), dict) else {}
            group_id = doc.get("group_id")
            if isinstance(group_id, int):
                result[(site_id, group_id)] = str(group.get("name") or f"#{group_id}")
    return result


async def _local_account_map(db: AsyncIOMotorDatabase, emails: list[Any]) -> dict[str, dict[str, Any]]:
    normalized_emails = sorted({_normalize_email(email) for email in emails if _normalize_email(email)})
    if not normalized_emails:
        return {}
    query = {
        "$or": [
            {"metadata.email": {"$in": normalized_emails}},
            {"account_json.credentials.email": {"$in": normalized_emails}},
            {"account_json.extra.email": {"$in": normalized_emails}},
        ],
        "metadata.deleted_at": {"$exists": False},
    }
    result: dict[str, dict[str, Any]] = {}
    async for doc in db.accounts.find(query, {"account_json": 1, "metadata": 1}).collation({"locale": "en", "strength": 2}):
        metadata = doc.get("metadata") if isinstance(doc.get("metadata"), dict) else {}
        account_json = doc.get("account_json") if isinstance(doc.get("account_json"), dict) else {}
        credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else {}
        extra = account_json.get("extra") if isinstance(account_json.get("extra"), dict) else {}
        email = _normalize_email(metadata.get("email") or credentials.get("email") or extra.get("email"))
        if email:
            result[email] = serialize_doc(
                {
                    "id": doc.get("_id"),
                    "uploader_name": metadata.get("uploader_name"),
                    "uploaded_by_name": metadata.get("uploaded_by_name"),
                    "updated_by_name": metadata.get("updated_by_name"),
                    "last_operation_by_name": metadata.get("last_operation_by_name"),
                    "last_operation_name": metadata.get("last_operation_name"),
                    "last_operation_at": metadata.get("last_operation_at"),
                    "pool_status": metadata.get("pool_status"),
                    "account_type": metadata.get("account_type"),
                    "purchase_source": metadata.get("purchase_source"),
                    "remark": metadata.get("remark"),
                }
            )
    return result


async def _usage_cache_map(
    db: AsyncIOMotorDatabase,
    *,
    cache_keys: set[tuple[str, Any]],
    emails_by_site: dict[str, set[str]],
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    remote_conditions = [
        {"site_id": site_id, "sub2api_account_id": {"$in": [remote_id, str(remote_id)]}}
        for site_id, remote_id in cache_keys
        if site_id and remote_id is not None
    ]
    email_conditions = [
        {"site_id": site_id, "email": {"$in": sorted(emails)}}
        for site_id, emails in emails_by_site.items()
        if site_id and emails
    ]
    if not remote_conditions and not email_conditions:
        return result
    query = {"$or": [*remote_conditions, *email_conditions]}
    async for doc in db.sub2api_accounts_cache.find(query).collation({"locale": "en", "strength": 2}):
        site_id = str(doc.get("site_id") or "")
        remote_id = doc.get("sub2api_account_id")
        account = doc.get("account") if isinstance(doc.get("account"), dict) else {}
        extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
        usage = _usage_snapshot_from_sources(doc, account, extra)
        email = _normalize_email(doc.get("email") or account.get("email") or extra.get("email"))
        payload = {"usage_snapshot": usage, "cumulative_usage_snapshot": usage}
        if site_id and remote_id is not None:
            result[_usage_cache_key(site_id, remote_id)] = payload
            result[_usage_cache_key(site_id, str(remote_id))] = payload
        if site_id and email:
            current = result.get(_usage_cache_key(site_id, email), {})
            result[_usage_cache_key(site_id, email)] = {
                "usage_snapshot": _merge_usage_maps(current.get("usage_snapshot"), usage),
                "cumulative_usage_snapshot": _merge_usage_maps(current.get("cumulative_usage_snapshot"), usage),
            }
    return result


def _event_item(doc: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    identity = context.get("identities", {}).get(str(doc.get("identity_id"))) or {}
    session = context.get("sessions", {}).get(str(doc.get("session_id"))) or {}
    details = doc.get("details") if isinstance(doc.get("details"), dict) else {}
    site_id = str(doc.get("site_id") or "")
    group_ids = _int_list(doc.get("current_group_ids") or identity.get("current_group_ids"))
    email = _normalize_email(doc.get("normalized_email") or doc.get("email") or identity.get("normalized_email") or identity.get("email"))
    cache_usage = _lookup_usage_cache(context, site_id=site_id, remote_id=doc.get("remote_account_id") or identity.get("current_remote_account_id"), email=email)
    usage = _merge_usage_maps(
        cache_usage.get("usage_snapshot"),
        session.get("last_usage_snapshot"),
        identity.get("last_usage_snapshot"),
        doc.get("usage_snapshot"),
    )
    cumulative = _merge_usage_maps(
        cache_usage.get("cumulative_usage_snapshot"),
        _cumulative_snapshot(identity, session),
    )
    local = context.get("local_accounts", {}).get(email) or {}
    return serialize_doc(
        {
            "id": doc.get("_id"),
            "event_type": doc.get("event_type"),
            "severity": doc.get("severity"),
            "occurred_at": doc.get("occurred_at") or doc.get("detected_at"),
            "detected_at": doc.get("detected_at"),
            "site_id": site_id,
            "site_name": (context.get("sites", {}).get(site_id) or {}).get("name") or site_id,
            "identity_id": doc.get("identity_id"),
            "session_id": doc.get("session_id"),
            "remote_account_id": doc.get("remote_account_id"),
            "remote_account_ids": details.get("remote_account_ids") or identity.get("current_remote_account_ids"),
            "name": doc.get("name") or identity.get("name"),
            "email": doc.get("email") or identity.get("email"),
            "normalized_email": email,
            "plan_type": doc.get("plan_type") or identity.get("plan_type"),
            "group_ids": group_ids,
            "group_names": [context.get("groups", {}).get((site_id, group_id), f"#{group_id}") for group_id in group_ids],
            "previous_status": doc.get("previous_status"),
            "current_status": doc.get("current_status") or identity.get("current_status"),
            "previous_schedulable": doc.get("previous_schedulable"),
            "current_schedulable": doc.get("current_schedulable") if doc.get("current_schedulable") is not None else identity.get("current_schedulable"),
            "previous_error_message": doc.get("previous_error_message"),
            "current_error_message": doc.get("current_error_message") or identity.get("current_error_message"),
            "error_category": doc.get("error_category"),
            "is_401": bool(doc.get("is_401") or identity.get("current_is_401")),
            "usage_snapshot": usage,
            "cumulative_usage_snapshot": cumulative,
            "usage_duration_seconds": _duration_seconds(session.get("started_at") or identity.get("first_seen_at"), session.get("ended_at") or doc.get("detected_at") or identity.get("last_seen_at")),
            "normal_use_seconds": _duration_seconds(session.get("first_active_at") or session.get("started_at") or identity.get("first_seen_at"), session.get("first_401_at") or session.get("ended_at") or identity.get("last_seen_at")),
            "session_started_at": session.get("started_at"),
            "session_ended_at": session.get("ended_at"),
            "identity_first_seen_at": identity.get("first_seen_at"),
            "identity_last_seen_at": identity.get("last_seen_at"),
            "notification_status": doc.get("notification_status"),
            "notification_event_id": doc.get("notification_event_id"),
            "notification_batch_id": doc.get("notification_batch_id"),
            "notification_success_count": doc.get("notification_success_count"),
            "notification_failed_count": doc.get("notification_failed_count"),
            "uploader_name": local.get("uploader_name") or local.get("uploaded_by_name"),
            "last_operation_by_name": local.get("last_operation_by_name"),
            "last_operation_name": local.get("last_operation_name"),
            "last_operation_at": local.get("last_operation_at"),
            "local_account": local,
            "details": details,
            "raw_excerpt": doc.get("raw_excerpt"),
        }
    )


def _identity_item(doc: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    site_id = str(doc.get("site_id") or "")
    group_ids = _int_list(doc.get("current_group_ids"))
    email = _normalize_email(doc.get("normalized_email") or doc.get("email"))
    local = context.get("local_accounts", {}).get(email) or {}
    cache_usage = _lookup_usage_cache(context, site_id=site_id, remote_id=doc.get("current_remote_account_id"), email=email)
    last_usage = _merge_usage_maps(cache_usage.get("usage_snapshot"), doc.get("last_usage_snapshot"))
    cumulative_usage = _merge_usage_maps(cache_usage.get("cumulative_usage_snapshot"), doc.get("cumulative_usage_snapshot"))
    current_session_start = None
    current_session_end = None
    return serialize_doc(
        {
            "id": doc.get("_id"),
            "site_id": site_id,
            "site_name": (context.get("sites", {}).get(site_id) or {}).get("name") or site_id,
            "identity_id": doc.get("_id"),
            "email": doc.get("email"),
            "normalized_email": email,
            "name": doc.get("name"),
            "plan_type": doc.get("plan_type"),
            "current_presence": doc.get("current_presence"),
            "current_status": doc.get("current_status"),
            "current_schedulable": doc.get("current_schedulable"),
            "current_error_message": doc.get("current_error_message"),
            "current_is_401": doc.get("current_is_401"),
            "current_remote_account_id": doc.get("current_remote_account_id"),
            "current_remote_account_ids": doc.get("current_remote_account_ids"),
            "duplicate_remote_count": doc.get("duplicate_remote_count"),
            "current_group_ids": group_ids,
            "group_names": [context.get("groups", {}).get((site_id, group_id), f"#{group_id}") for group_id in group_ids],
            "first_seen_at": doc.get("first_seen_at"),
            "last_seen_at": doc.get("last_seen_at"),
            "first_401_at": doc.get("first_401_at"),
            "last_401_at": doc.get("last_401_at"),
            "first_recovered_at": doc.get("first_recovered_at"),
            "last_recovered_at": doc.get("last_recovered_at"),
            "last_removed_at": doc.get("last_removed_at"),
            "last_event_at": doc.get("last_event_at"),
            "total_sessions": doc.get("total_sessions"),
            "total_401_count": doc.get("total_401_count"),
            "total_recovery_count": doc.get("total_recovery_count"),
            "total_removed_count": doc.get("total_removed_count"),
            "last_usage_snapshot": last_usage,
            "cumulative_usage_snapshot": cumulative_usage,
            "cumulative_usage_totals": doc.get("cumulative_usage_totals") or {},
            "last_usage_rollover_at": doc.get("last_usage_rollover_at"),
            "lifetime_seconds": _duration_seconds(doc.get("first_seen_at"), doc.get("last_seen_at")),
            "current_session_seconds": _duration_seconds(current_session_start, current_session_end),
            "uploader_name": local.get("uploader_name") or local.get("uploaded_by_name"),
            "last_operation_by_name": local.get("last_operation_by_name"),
            "last_operation_name": local.get("last_operation_name"),
            "last_operation_at": local.get("last_operation_at"),
            "local_account": local,
        }
    )


def _dedupe_redundant_duplicate_email_events(docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for doc in docs:
        identity_key = str(doc.get("identity_id") or doc.get("normalized_email") or doc.get("email") or "")
        if doc.get("event_type") == "duplicate_email_resolved":
            result.append(doc)
            details = doc.get("details") if isinstance(doc.get("details"), dict) else {}
            seen.discard((identity_key, _duplicate_event_signature_from_values(details.get("previous_remote_account_ids"))))
            seen.discard((identity_key, _duplicate_event_signature(doc)))
            continue
        if doc.get("event_type") != "duplicate_email_detected":
            result.append(doc)
            continue
        key = (identity_key, _duplicate_event_signature(doc))
        if key in seen:
            continue
        seen.add(key)
        result.append(doc)
    return result


def _duplicate_event_signature(doc: dict[str, Any]) -> str:
    details = doc.get("details") if isinstance(doc.get("details"), dict) else {}
    values = details.get("remote_account_ids")
    if not isinstance(values, list) or not values:
        values = doc.get("remote_account_ids")
    if not isinstance(values, list) or not values:
        values = [doc.get("remote_account_id")]
    return _duplicate_event_signature_from_values(values)


def _duplicate_event_signature_from_values(values: Any) -> str:
    if not isinstance(values, list):
        values = [values]
    return ",".join(sorted({str(item) for item in values if item is not None and str(item) != ""}))


def _session_item(doc: dict[str, Any]) -> dict[str, Any]:
    return {
        **doc,
        "duration_seconds": _duration_seconds(doc.get("started_at"), doc.get("ended_at") or doc.get("updated_at")),
        "normal_use_seconds": _duration_seconds(doc.get("first_active_at") or doc.get("started_at"), doc.get("first_401_at") or doc.get("ended_at") or doc.get("updated_at")),
    }


async def _cumulative_identity_totals(db: AsyncIOMotorDatabase, query: dict[str, Any]) -> dict[str, Any]:
    pipeline = [
        {"$match": query},
        {
            "$group": {
                "_id": None,
                "cumulative_actual_cost": {"$sum": {"$ifNull": ["$cumulative_usage_totals.codex_total_actual_cost", 0]}},
                "cumulative_total_cost": {"$sum": {"$ifNull": ["$cumulative_usage_totals.codex_total_cost", 0]}},
                "cumulative_7d_actual_cost": {"$sum": {"$ifNull": ["$cumulative_usage_totals.codex_7d_actual_cost", 0]}},
                "cumulative_request_count": {"$sum": {"$ifNull": ["$cumulative_usage_totals.codex_total_request_count", 0]}},
                "cumulative_token_count": {"$sum": {"$ifNull": ["$cumulative_usage_totals.codex_total_token_count", 0]}},
            }
        },
    ]
    result = [doc async for doc in db.remote_account_identities.aggregate(pipeline)]
    if not result:
        return {
            "cumulative_actual_cost": 0,
            "cumulative_total_cost": 0,
            "cumulative_7d_actual_cost": 0,
            "cumulative_request_count": 0,
            "cumulative_token_count": 0,
        }
    doc = result[0]
    return {key: round(float(doc.get(key) or 0), 4) for key in doc if key != "_id"}


def _cumulative_snapshot(identity: dict[str, Any], session: dict[str, Any]) -> dict[str, Any]:
    snapshot = identity.get("cumulative_usage_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        totals = identity.get("cumulative_usage_totals")
        return _merge_usage_maps(snapshot, _cumulative_totals_as_snapshot(totals))
    snapshot = session.get("cumulative_usage_snapshot")
    if isinstance(snapshot, dict) and snapshot:
        totals = session.get("cumulative_usage_totals")
        return _merge_usage_maps(snapshot, _cumulative_totals_as_snapshot(totals))
    return {}


def _cumulative_totals_as_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    result = {}
    for key, item in value.items():
        if key.endswith("_rollover_base"):
            continue
        result[key] = item
        result[f"{key}_cumulative"] = item
    return result


def _usage_snapshot_from_sources(*sources: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in USAGE_FIELDS:
            value = source.get(key)
            if value is not None and value != "":
                result[key] = value
    return result


def _merge_usage_maps(*values: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for value in values:
        if not isinstance(value, dict):
            continue
        for key, item in value.items():
            if item is not None and item != "":
                result[key] = item
    return result


def _lookup_usage_cache(context: dict[str, Any], *, site_id: str, remote_id: Any, email: str) -> dict[str, Any]:
    usage_cache = context.get("usage_cache") if isinstance(context.get("usage_cache"), dict) else {}
    if site_id and remote_id is not None:
        cached = usage_cache.get(_usage_cache_key(site_id, remote_id))
        if isinstance(cached, dict):
            return cached
    if site_id and email:
        cached = usage_cache.get(_usage_cache_key(site_id, email))
        if isinstance(cached, dict):
            return cached
    return {}


def _usage_cache_key(site_id: str, value: Any) -> str:
    return f"{site_id}:{str(value).strip().lower()}"


async def _distinct_event_account_count(db: AsyncIOMotorDatabase, query: dict[str, Any]) -> int:
    pipeline = [
        {"$match": query},
        {
            "$addFields": {
                "_event_account_email": {
                    "$ifNull": [
                        "$normalized_email",
                        {"$ifNull": ["$email", ""]},
                    ]
                },
            }
        },
        {
            "$project": {
                "account_key": {
                    "$cond": [
                        {"$ne": [{"$ifNull": ["$identity_id", ""]}, ""]},
                        "$identity_id",
                        {
                            "$cond": [
                                {"$ne": ["$_event_account_email", ""]},
                                {"$concat": [{"$ifNull": ["$site_id", ""]}, ":email:", "$_event_account_email"]},
                                {"$concat": [{"$ifNull": ["$site_id", ""]}, ":remote:", {"$toString": {"$ifNull": ["$remote_account_id", "$_id"]}}]},
                            ]
                        },
                    ]
                }
            }
        },
        {"$group": {"_id": "$account_key"}},
        {"$count": "total"},
    ]
    result = [doc async for doc in db.remote_account_status_events.aggregate(pipeline)]
    return int(result[0].get("total") or 0) if result else 0


def _int_list(value: Any) -> list[int]:
    values = value if isinstance(value, list) else ([] if value in (None, "") else [value])
    result: list[int] = []
    for item in values:
        try:
            parsed = int(item)
        except (TypeError, ValueError):
            continue
        if parsed not in result:
            result.append(parsed)
    return result


def _normalize_email(value: Any) -> str:
    return str(value or "").strip().lower()


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


def _parse_remote_id_search(value: str) -> int | None:
    normalized = value.strip().lstrip("#")
    return int(normalized) if normalized.isdigit() else None


def _duration_seconds(start: Any, end: Any) -> int | None:
    start_dt = _parse_datetime(start)
    end_dt = _parse_datetime(end)
    if not start_dt or not end_dt:
        return None
    return max(0, int((end_dt - start_dt).total_seconds()))
