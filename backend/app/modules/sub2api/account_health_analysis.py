from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.sub2api.cache import _quota_detection_account_type
from app.modules.sub2api.quota_detection import KNOWN_ACCOUNT_TYPES
from app.utils import now_utc


ANALYSIS_REFRESH_INTERVAL = timedelta(hours=1)
ANALYSIS_CACHE_RETENTION = timedelta(days=2)
ANALYSIS_SCHEMA_VERSION = 1
ANALYSIS_WINDOWS = {
    "one_day": timedelta(days=1),
    "seven_days": timedelta(days=7),
}
ANALYSIS_EVENT_TYPES = (
    "remote_account_seen_first",
    "remote_account_reappeared",
    "status_changed",
    "error_changed",
    "401_detected",
    "401_recovered",
)

_AUTH_FAILURE_PATTERN = re.compile(
    r"401|unauthorized|authentication failed|token[_ -]?(?:invalidated|revoked)|"
    r"invalid(?:ated)? (?:oauth )?token|refresh token|openai_oauth_token_refresh_failed|凭证失效|认证失败",
    re.I,
)
_BAN_PATTERN = re.compile(
    r"account (?:banned|deactivated|suspended)|账号(?:封禁|停用)|被封禁|已封禁",
    re.I,
)
_BAD_GATEWAY_PATTERN = re.compile(r"(?:^|\D)502(?:\D|$)|bad gateway|upstream[^\n]{0,80}502", re.I)
_UNAVAILABLE_STATUSES = {"banned", "revoked", "invalid", "deactivated", "suspended"}
_analysis_locks: dict[str, asyncio.Lock] = {}


def is_unavailable_state(status: Any, error_message: Any) -> bool:
    normalized_status = str(status or "").strip().lower()
    message = str(error_message or "").strip()
    if normalized_status in _UNAVAILABLE_STATUSES:
        return True
    return bool(
        _AUTH_FAILURE_PATTERN.search(message)
        or _BAN_PATTERN.search(message)
        or _BAD_GATEWAY_PATTERN.search(message)
    )


def build_account_period(
    identity: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    start_at = _as_utc(start_at)
    end_at = _as_utc(end_at)
    first_seen_at = _datetime_or_none(identity.get("first_seen_at")) or start_at
    observed_from = max(start_at, first_seen_at)
    last_seen_at = _datetime_or_none(identity.get("last_seen_at"))
    if last_seen_at is not None and last_seen_at < observed_from:
        return _empty_account_period(observed_from, last_seen_at)
    present = str(identity.get("current_presence") or "present") == "present"
    last_observed_at = (
        end_at
        if present
        else _datetime_or_none(identity.get("last_present_at") or identity.get("last_seen_at")) or end_at
    )
    observed_until = min(end_at, last_observed_at)
    if observed_until <= observed_from:
        return _empty_account_period(observed_from, observed_until)

    transitions = _period_transitions(events, observed_from, observed_until)
    current_unavailable = is_unavailable_state(
        identity.get("current_status"),
        identity.get("current_error_message"),
    )
    unavailable = transitions[0][1] if transitions else current_unavailable
    episode_started_at = observed_from if unavailable else None
    episode_durations: list[float] = []

    for detected_at, _previous_unavailable, next_unavailable in transitions:
        if next_unavailable == unavailable:
            continue
        if next_unavailable:
            episode_started_at = detected_at
        elif episode_started_at is not None:
            episode_durations.append(max(0.0, (detected_at - episode_started_at).total_seconds()))
            episode_started_at = None
        unavailable = next_unavailable

    if unavailable != current_unavailable:
        if unavailable and episode_started_at is not None:
            episode_durations.append(max(0.0, (observed_until - episode_started_at).total_seconds()))
            episode_started_at = None
        unavailable = current_unavailable

    if unavailable and episode_started_at is None:
        episode_started_at = observed_until
    if episode_started_at is not None:
        episode_durations.append(max(0.0, (observed_until - episode_started_at).total_seconds()))

    ongoing = bool(present and observed_until == end_at and current_unavailable and episode_started_at is not None)
    return {
        "observed": True,
        "observed_from": observed_from,
        "observed_until": observed_until,
        "unavailable": bool(episode_durations),
        "episode_count": len(episode_durations),
        "episode_durations_seconds": episode_durations,
        "unavailable_seconds": sum(episode_durations),
        "ongoing": ongoing,
    }


def summarize_period(
    identities: list[dict[str, Any]],
    events_by_identity: dict[str, list[dict[str, Any]]],
    *,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    overall = _new_bucket("all")
    by_type = {account_type: _new_bucket(account_type) for account_type in (*KNOWN_ACCOUNT_TYPES, "unknown")}

    for identity in identities:
        identity_id = str(identity.get("_id") or "")
        period = build_account_period(
            identity,
            events_by_identity.get(identity_id, []),
            start_at=start_at,
            end_at=end_at,
        )
        if not period["observed"]:
            continue
        account_type = _analysis_account_type(identity)
        bucket = by_type.setdefault(account_type, _new_bucket(account_type))
        _add_account(overall, identity, period)
        _add_account(bucket, identity, period)

    return {
        "start_at": _as_utc(start_at),
        "end_at": _as_utc(end_at),
        "overall": _finalize_bucket(overall),
        "items": [
            _finalize_bucket(by_type[account_type])
            for account_type in (*KNOWN_ACCOUNT_TYPES, "unknown")
            if by_type[account_type]["observed_accounts"] > 0
        ],
    }


async def compute_account_health_analysis(
    db: AsyncIOMotorDatabase,
    site_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    computed_at = _as_utc(now or now_utc())
    earliest = computed_at - ANALYSIS_WINDOWS["seven_days"]
    identity_cursor = db.remote_account_identities.find(
        {
            "site_id": site_id,
            "first_seen_at": {"$lt": computed_at},
            "last_seen_at": {"$gte": earliest},
        },
        {
            "plan_type": 1,
            "account_type": 1,
            "name": 1,
            "first_seen_at": 1,
            "last_seen_at": 1,
            "last_present_at": 1,
            "current_presence": 1,
            "current_status": 1,
            "current_error_message": 1,
            "last_usage_snapshot": 1,
        },
    )
    identities = [identity async for identity in identity_cursor]
    identity_ids = [str(identity.get("_id")) for identity in identities if identity.get("_id")]
    events_by_identity: dict[str, list[dict[str, Any]]] = {}
    if identity_ids:
        event_cursor = db.remote_account_status_events.find(
            {
                "site_id": site_id,
                "identity_id": {"$in": identity_ids},
                "event_type": {"$in": list(ANALYSIS_EVENT_TYPES)},
                "detected_at": {"$gte": earliest, "$lte": computed_at},
            },
            {
                "identity_id": 1,
                "event_type": 1,
                "detected_at": 1,
                "previous_status": 1,
                "current_status": 1,
                "previous_error_message": 1,
                "current_error_message": 1,
            },
        ).sort("detected_at", 1)
        async for event in event_cursor:
            identity_id = str(event.get("identity_id") or "")
            if identity_id:
                events_by_identity.setdefault(identity_id, []).append(event)

    return {
        "site_id": site_id,
        "computed_at": computed_at,
        "periods": {
            name: summarize_period(
                identities,
                events_by_identity,
                start_at=computed_at - duration,
                end_at=computed_at,
            )
            for name, duration in ANALYSIS_WINDOWS.items()
        },
    }


async def get_account_health_analysis(
    db: AsyncIOMotorDatabase,
    site_id: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    requested_at = _as_utc(now or now_utc())
    cached = await db.sub2api_account_health_analyses.find_one({"_id": site_id})
    if _is_fresh(cached, requested_at):
        return _public_analysis(cached, requested_at=requested_at, stale=False)

    lock = _analysis_locks.setdefault(site_id, asyncio.Lock())
    async with lock:
        cached = await db.sub2api_account_health_analyses.find_one({"_id": site_id})
        if _is_fresh(cached, requested_at):
            return _public_analysis(cached, requested_at=requested_at, stale=False)
        try:
            computed = await compute_account_health_analysis(db, site_id, now=requested_at)
            document = {
                "_id": site_id,
                "schema_version": ANALYSIS_SCHEMA_VERSION,
                **computed,
                "expires_at": requested_at + ANALYSIS_CACHE_RETENTION,
            }
            await db.sub2api_account_health_analyses.replace_one(
                {"_id": site_id},
                document,
                upsert=True,
            )
            return _public_analysis(document, requested_at=requested_at, stale=False)
        except Exception:
            if cached:
                return _public_analysis(cached, requested_at=requested_at, stale=True)
            raise


def _period_transitions(
    events: list[dict[str, Any]],
    observed_from: datetime,
    observed_until: datetime,
) -> list[tuple[datetime, bool, bool]]:
    transitions: list[tuple[datetime, bool, bool]] = []
    seen: set[tuple[datetime, bool, bool]] = set()
    for event in sorted(events, key=lambda item: _datetime_or_none(item.get("detected_at")) or observed_from):
        detected_at = _datetime_or_none(event.get("detected_at"))
        if detected_at is None or detected_at < observed_from or detected_at > observed_until:
            continue
        previous = is_unavailable_state(
            event.get("previous_status"),
            event.get("previous_error_message"),
        )
        current = is_unavailable_state(
            event.get("current_status"),
            event.get("current_error_message"),
        )
        signature = (detected_at, previous, current)
        if signature in seen:
            continue
        seen.add(signature)
        transitions.append(signature)
    return transitions


def _analysis_account_type(identity: dict[str, Any]) -> str:
    account_type = str(_quota_detection_account_type(identity) or "unknown").strip().lower()
    return account_type if account_type in {*KNOWN_ACCOUNT_TYPES, "unknown"} else "unknown"


def _new_bucket(account_type: str) -> dict[str, Any]:
    return {
        "account_type": account_type,
        "observed_accounts": 0,
        "unavailable_accounts": 0,
        "episode_count": 0,
        "ongoing_unavailable_accounts": 0,
        "episode_durations_seconds": [],
        "usage_values": {
            "five_hour_used_percent": [],
            "seven_day_used_percent": [],
            "five_hour_actual_cost_usd": [],
            "seven_day_actual_cost_usd": [],
        },
    }


def _add_account(bucket: dict[str, Any], identity: dict[str, Any], period: dict[str, Any]) -> None:
    bucket["observed_accounts"] += 1
    if period["unavailable"]:
        bucket["unavailable_accounts"] += 1
    bucket["episode_count"] += int(period["episode_count"])
    if period["ongoing"]:
        bucket["ongoing_unavailable_accounts"] += 1
    bucket["episode_durations_seconds"].extend(period["episode_durations_seconds"])

    usage = identity.get("last_usage_snapshot")
    usage = usage if isinstance(usage, dict) else {}
    field_map = {
        "five_hour_used_percent": "codex_5h_used_percent",
        "seven_day_used_percent": "codex_7d_used_percent",
        "five_hour_actual_cost_usd": "codex_5h_actual_cost",
        "seven_day_actual_cost_usd": "codex_7d_actual_cost",
    }
    for output_field, source_field in field_map.items():
        value = _finite_number(usage.get(source_field))
        if value is not None:
            bucket["usage_values"][output_field].append(value)


def _finalize_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    observed = int(bucket["observed_accounts"])
    durations = bucket["episode_durations_seconds"]
    result = {
        "account_type": bucket["account_type"],
        "observed_accounts": observed,
        "unavailable_accounts": int(bucket["unavailable_accounts"]),
        "unavailable_probability": round(bucket["unavailable_accounts"] / observed, 6) if observed else None,
        "episode_count": int(bucket["episode_count"]),
        "ongoing_unavailable_accounts": int(bucket["ongoing_unavailable_accounts"]),
        "average_unavailable_duration_seconds": round(sum(durations) / len(durations), 3) if durations else None,
    }
    for field, values in bucket["usage_values"].items():
        result[f"average_{field}"] = round(sum(values) / len(values), 4) if values else None
        result[f"{field}_sample_count"] = len(values)
    return result


def _empty_account_period(observed_from: datetime, observed_until: datetime) -> dict[str, Any]:
    return {
        "observed": False,
        "observed_from": observed_from,
        "observed_until": observed_until,
        "unavailable": False,
        "episode_count": 0,
        "episode_durations_seconds": [],
        "unavailable_seconds": 0.0,
        "ongoing": False,
    }


def _is_fresh(document: dict[str, Any] | None, requested_at: datetime) -> bool:
    computed_at = _datetime_or_none((document or {}).get("computed_at"))
    return bool(computed_at is not None and requested_at - computed_at < ANALYSIS_REFRESH_INTERVAL)


def _public_analysis(
    document: dict[str, Any],
    *,
    requested_at: datetime,
    stale: bool,
) -> dict[str, Any]:
    computed_at = _datetime_or_none(document.get("computed_at"))
    return {
        key: value
        for key, value in {
            **document,
            "stale": stale,
            "next_refresh_at": computed_at + ANALYSIS_REFRESH_INTERVAL if computed_at else requested_at,
        }.items()
        if key not in {"_id", "schema_version", "expires_at"}
    }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed or parsed in {float("inf"), float("-inf")}:
        return None
    return parsed


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_utc(value: datetime) -> datetime:
    return (_datetime_or_none(value) or value.replace(tzinfo=UTC)).astimezone(UTC)
