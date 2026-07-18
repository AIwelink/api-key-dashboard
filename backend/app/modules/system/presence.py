from __future__ import annotations

import asyncio
import hashlib
import math
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils import now_utc, serialize_doc


ACTIVE_PRESENCE_SECONDS = 60
PRESENCE_RETENTION_HOURS = 24
PRESENCE_HISTORY_DAYS = 30
PRESENCE_HISTORY_RETENTION_DAYS = 35
PRESENCE_HISTORY_BUCKET_MINUTES = 5
PRESENCE_DISPLAY_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


def presence_document_id(user_id: Any, client_id: str, session_id: str) -> str:
    identity = f"{user_id}:{client_id}:{session_id}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


async def record_frontend_presence(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    payload: dict[str, Any],
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    if actor.get("actor_type") == "api_token":
        raise ValueError("browser user is required")

    observed_at = observed_at or now_utc()
    client_id = str(payload.get("client_id") or "").strip()
    session_id = str(payload.get("session_id") or "").strip()
    user_id = str(actor.get("_id") or "").strip()
    if not user_id or not client_id or not session_id:
        raise ValueError("user_id, client_id and session_id are required")

    foreground_since_at = _bounded_foreground_since(payload.get("foreground_since_at"), observed_at)
    document_id = presence_document_id(user_id, client_id, session_id)
    updates = {
        "user_id": user_id,
        "user_name": actor.get("name") or actor.get("email") or user_id,
        "user_email": actor.get("email"),
        "role": actor.get("role"),
        "client_id": client_id,
        "session_id": session_id,
        "client_label": str(payload.get("client_label") or "Unknown client").strip(),
        "device_type": str(payload.get("device_type") or "unknown").strip(),
        "view": str(payload.get("view") or "").strip(),
        "path": str(payload.get("path") or "").strip(),
        "foreground_since_at": foreground_since_at,
        "last_seen_at": observed_at,
        "expires_at": observed_at + timedelta(hours=PRESENCE_RETENTION_HOURS),
    }
    bucket_at = _history_bucket(observed_at)
    bucket_id = presence_history_document_id(user_id, bucket_at)
    await asyncio.gather(
        db.frontend_presence.update_one(
            {"_id": document_id},
            {
                "$set": updates,
                "$setOnInsert": {"created_at": observed_at},
            },
            upsert=True,
        ),
        db.frontend_presence_minutes.update_one(
            {"_id": bucket_id},
            {
                "$set": {
                    "user_name": updates["user_name"],
                    "user_email": updates["user_email"],
                    "role": updates["role"],
                    "last_seen_at": observed_at,
                    "expires_at": observed_at + timedelta(days=PRESENCE_HISTORY_RETENTION_DAYS),
                },
                "$setOnInsert": {
                    "user_id": user_id,
                    "bucket_at": bucket_at,
                    "created_at": observed_at,
                },
                "$addToSet": {
                    "client_ids": client_id,
                    "views": updates["view"],
                },
                "$inc": {"heartbeat_count": 1},
            },
            upsert=True,
        ),
    )
    return serialize_doc({"_id": document_id, **updates})


async def remove_frontend_presence(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict[str, Any],
    client_id: str,
    session_id: str,
) -> bool:
    if actor.get("actor_type") == "api_token":
        return False
    document_id = presence_document_id(actor.get("_id"), client_id, session_id)
    result = await db.frontend_presence.delete_one({"_id": document_id})
    return bool(result.deleted_count)


async def list_active_frontend_presence(
    db: AsyncIOMotorDatabase,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    observed_at = observed_at or now_utc()
    active_after = observed_at - timedelta(seconds=ACTIVE_PRESENCE_SECONDS)
    cursor = db.frontend_presence.find({"last_seen_at": {"$gte": active_after}}).sort("last_seen_at", -1).limit(500)
    items = [serialize_doc(item) async for item in cursor]
    return {
        "items": items,
        "total": len(items),
        "active_window_seconds": ACTIVE_PRESENCE_SECONDS,
        "observed_at": observed_at,
    }


async def get_frontend_presence_history(
    db: AsyncIOMotorDatabase,
    *,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    observed_at = observed_at or now_utc()
    start_at = _history_start(observed_at, PRESENCE_HISTORY_DAYS)
    active_after = observed_at - timedelta(seconds=ACTIVE_PRESENCE_SECONDS)
    users_cursor = db.users.find(
        {},
        {"_id": 1, "name": 1, "email": 1, "role": 1, "status": 1},
    )
    minute_cursor = db.frontend_presence_minutes.find(
        {"bucket_at": {"$gte": start_at, "$lte": observed_at}},
        {"user_id": 1, "user_name": 1, "user_email": 1, "role": 1, "bucket_at": 1, "last_seen_at": 1},
    ).sort("bucket_at", 1)
    current_cursor = db.frontend_presence.find(
        {"last_seen_at": {"$gte": active_after}},
        {
            "user_id": 1,
            "user_name": 1,
            "user_email": 1,
            "role": 1,
            "client_id": 1,
            "session_id": 1,
            "client_label": 1,
            "device_type": 1,
            "last_seen_at": 1,
        },
    ).sort("last_seen_at", -1)
    users, minute_docs, current_docs = await asyncio.gather(
        _collect_cursor(users_cursor),
        _collect_cursor(minute_cursor),
        _collect_cursor(current_cursor),
    )
    return serialize_doc(
        build_presence_history(
            users=users,
            minute_docs=minute_docs,
            current_docs=current_docs,
            observed_at=observed_at,
            days=PRESENCE_HISTORY_DAYS,
        )
    )


def build_presence_history(
    *,
    users: list[dict[str, Any]],
    minute_docs: list[dict[str, Any]],
    current_docs: list[dict[str, Any]],
    observed_at: datetime,
    days: int = PRESENCE_HISTORY_DAYS,
) -> dict[str, Any]:
    observed_at = _aware_utc(observed_at)
    normalized_days = max(1, min(int(days), PRESENCE_HISTORY_DAYS))
    start_at = _history_start(observed_at, normalized_days)
    observed_local = observed_at.astimezone(PRESENCE_DISPLAY_TIMEZONE)
    start_local = start_at.astimezone(PRESENCE_DISPLAY_TIMEZONE)
    local_dates = [start_local.date() + timedelta(days=index) for index in range(normalized_days)]
    date_indexes = {value: index for index, value in enumerate(local_dates)}

    profiles: dict[str, dict[str, Any]] = {}
    for user in users:
        user_id = str(user.get("_id") or user.get("id") or "").strip()
        if not user_id:
            continue
        profiles[user_id] = {
            "user_id": user_id,
            "user_name": user.get("name") or user.get("email") or user_id,
            "user_email": user.get("email"),
            "role": user.get("role"),
            "status": user.get("status"),
        }

    buckets_by_user: dict[str, set[datetime]] = defaultdict(set)
    last_seen_by_user: dict[str, datetime] = {}
    for document in minute_docs:
        user_id = str(document.get("user_id") or "").strip()
        bucket_at = _datetime_or_none(document.get("bucket_at"))
        if not user_id or bucket_at is None or bucket_at < start_at or bucket_at > observed_at:
            continue
        buckets_by_user[user_id].add(_history_bucket(bucket_at))
        last_seen_at = _datetime_or_none(document.get("last_seen_at")) or bucket_at
        if user_id not in last_seen_by_user or last_seen_at > last_seen_by_user[user_id]:
            last_seen_by_user[user_id] = last_seen_at
        _ensure_presence_profile(profiles, user_id, document)

    active_clients_by_user: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for document in current_docs:
        user_id = str(document.get("user_id") or "").strip()
        if not user_id:
            continue
        client_id = str(document.get("client_id") or "").strip()
        if client_id:
            client = active_clients_by_user[user_id].setdefault(
                client_id,
                {
                    "client_id": client_id,
                    "client_label": document.get("client_label"),
                    "device_type": document.get("device_type"),
                    "session_ids": set(),
                    "last_seen_at": None,
                },
            )
            session_id = str(document.get("session_id") or "").strip()
            if session_id:
                client["session_ids"].add(session_id)
            client_seen_at = _datetime_or_none(document.get("last_seen_at"))
            if client_seen_at is not None and (client["last_seen_at"] is None or client_seen_at > client["last_seen_at"]):
                client["last_seen_at"] = client_seen_at
        last_seen_at = _datetime_or_none(document.get("last_seen_at"))
        if last_seen_at is not None and (user_id not in last_seen_by_user or last_seen_at > last_seen_by_user[user_id]):
            last_seen_by_user[user_id] = last_seen_at
        _ensure_presence_profile(profiles, user_id, document)

    elapsed_minutes = max(1, math.floor((observed_at - start_at).total_seconds() / 60))
    items = []
    for user_id, profile in profiles.items():
        bucket_counts = [[0 for _ in range(48)] for _ in range(normalized_days)]
        for bucket_at in buckets_by_user.get(user_id, set()):
            local_bucket = bucket_at.astimezone(PRESENCE_DISPLAY_TIMEZONE)
            day_index = date_indexes.get(local_bucket.date())
            if day_index is None:
                continue
            slot_index = local_bucket.hour * 2 + local_bucket.minute // 30
            bucket_counts[day_index][slot_index] += 1

        timeline, pattern = _build_timeline(
            local_dates=local_dates,
            bucket_counts=bucket_counts,
            observed_local=observed_local,
        )
        online_minutes = min(elapsed_minutes, len(buckets_by_user.get(user_id, set())) * PRESENCE_HISTORY_BUCKET_MINUTES)
        active_client_details = [
            {
                "client_id": client["client_id"],
                "client_label": client.get("client_label"),
                "device_type": client.get("device_type"),
                "session_count": len(client["session_ids"]),
                "last_seen_at": client.get("last_seen_at"),
            }
            for client in active_clients_by_user.get(user_id, {}).values()
        ]
        active_client_details.sort(key=lambda client: client.get("last_seen_at") or datetime.min.replace(tzinfo=UTC), reverse=True)
        items.append(
            {
                **profile,
                "is_online": bool(active_client_details),
                "active_clients": len(active_client_details),
                "active_client_details": active_client_details,
                "last_seen_at": last_seen_by_user.get(user_id),
                "online_minutes": online_minutes,
                "online_ratio_percent": round(online_minutes / elapsed_minutes * 100, 1),
                "common_pattern": pattern,
                "common_periods": _common_periods(pattern),
                "daily_timeline": timeline,
            }
        )

    items.sort(
        key=lambda item: (
            0 if item["is_online"] else 1,
            -int(item["online_minutes"]),
            str(item.get("user_name") or item.get("user_email") or "").lower(),
        )
    )
    return {
        "items": items,
        "total": len(items),
        "online_users": sum(1 for item in items if item["is_online"]),
        "days": normalized_days,
        "bucket_minutes": PRESENCE_HISTORY_BUCKET_MINUTES,
        "start_at": start_at,
        "end_at": observed_at,
        "timezone": str(PRESENCE_DISPLAY_TIMEZONE),
    }


def presence_history_document_id(user_id: Any, bucket_at: datetime) -> str:
    identity = f"{user_id}:{_history_bucket(bucket_at).isoformat()}".encode("utf-8")
    return hashlib.sha256(identity).hexdigest()


def _build_timeline(
    *,
    local_dates: list[date],
    bucket_counts: list[list[int]],
    observed_local: datetime,
) -> tuple[list[dict[str, Any]], list[int]]:
    pattern_online = [0 for _ in range(48)]
    pattern_possible = [0 for _ in range(48)]
    timeline = []
    for day_index, local_day in enumerate(local_dates):
        segments: list[int | None] = []
        day_online_buckets = 0
        day_possible_buckets = 0
        for slot_index in range(48):
            slot_start = datetime.combine(local_day, time.min, tzinfo=PRESENCE_DISPLAY_TIMEZONE) + timedelta(minutes=slot_index * 30)
            possible_buckets = _possible_slot_buckets(slot_start, observed_local)
            if possible_buckets <= 0:
                segments.append(None)
                continue
            online_buckets = min(possible_buckets, bucket_counts[day_index][slot_index])
            segments.append(round(online_buckets / possible_buckets * 100))
            day_online_buckets += online_buckets
            day_possible_buckets += possible_buckets
            pattern_online[slot_index] += online_buckets
            pattern_possible[slot_index] += possible_buckets
        online_minutes = day_online_buckets * PRESENCE_HISTORY_BUCKET_MINUTES
        timeline.append(
            {
                "date": local_day.isoformat(),
                "online_minutes": online_minutes,
                "online_ratio_percent": round(day_online_buckets / day_possible_buckets * 100, 1) if day_possible_buckets else 0,
                "segments": segments,
            }
        )
    pattern = [round(pattern_online[index] / pattern_possible[index] * 100) if pattern_possible[index] else 0 for index in range(48)]
    return timeline, pattern


def _possible_slot_buckets(slot_start: datetime, observed_local: datetime) -> int:
    if slot_start > observed_local:
        return 0
    slot_end = slot_start + timedelta(minutes=30)
    if slot_end <= observed_local:
        return 30 // PRESENCE_HISTORY_BUCKET_MINUTES
    elapsed_minutes = max(0, (observed_local - slot_start).total_seconds() / 60)
    return min(30 // PRESENCE_HISTORY_BUCKET_MINUTES, max(1, math.ceil(elapsed_minutes / PRESENCE_HISTORY_BUCKET_MINUTES)))


def _common_periods(pattern: list[int]) -> list[dict[str, Any]]:
    peak = max(pattern, default=0)
    if peak < 20:
        return []
    threshold = max(20, round(peak * 0.6))
    ranges: list[tuple[int, int]] = []
    start: int | None = None
    for index, value in enumerate([*pattern, 0]):
        if value >= threshold and start is None:
            start = index
        if value < threshold and start is not None:
            ranges.append((start, index))
            start = None
    return [
        {
            "start": _slot_time(start_index),
            "end": _slot_time(end_index),
            "frequency_percent": round(sum(pattern[start_index:end_index]) / max(1, end_index - start_index)),
        }
        for start_index, end_index in ranges
    ]


def _slot_time(slot_index: int) -> str:
    if slot_index >= 48:
        return "24:00"
    return f"{slot_index // 2:02d}:{'30' if slot_index % 2 else '00'}"


def _ensure_presence_profile(profiles: dict[str, dict[str, Any]], user_id: str, source: dict[str, Any]) -> None:
    if user_id in profiles:
        return
    profiles[user_id] = {
        "user_id": user_id,
        "user_name": source.get("user_name") or source.get("user_email") or user_id,
        "user_email": source.get("user_email"),
        "role": source.get("role"),
        "status": source.get("status"),
    }


async def _collect_cursor(cursor: Any) -> list[dict[str, Any]]:
    return [item async for item in cursor]


def _history_start(observed_at: datetime, days: int) -> datetime:
    observed_local = _aware_utc(observed_at).astimezone(PRESENCE_DISPLAY_TIMEZONE)
    start_date = observed_local.date() - timedelta(days=max(0, days - 1))
    return datetime.combine(start_date, time.min, tzinfo=PRESENCE_DISPLAY_TIMEZONE).astimezone(UTC)


def _history_bucket(value: datetime) -> datetime:
    value = _aware_utc(value)
    minute = value.minute - value.minute % PRESENCE_HISTORY_BUCKET_MINUTES
    return value.replace(minute=minute, second=0, microsecond=0)


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _bounded_foreground_since(value: Any, observed_at: datetime) -> datetime:
    parsed = _datetime_or_none(value)
    if parsed is None:
        return observed_at
    earliest = observed_at - timedelta(hours=PRESENCE_RETENTION_HOURS)
    return min(observed_at, max(earliest, parsed))


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
