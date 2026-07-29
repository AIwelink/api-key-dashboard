from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.modules.agent.capacity import list_agent_pools, read_pool_capacity
from app.modules.agent.controller import run_agent_controller
from app.modules.agent.event_stream import read_agent_event_windows
from app.modules.agent.triggers import TRIGGER_EVENT_SPIKE
from app.utils import now_utc, serialize_doc


AGENT_EVENT_TRIGGERS_COLLECTION = "agent_event_triggers"

SIGNAL_401_BURST = "401_burst"
SIGNAL_BAN_BURST = "ban_burst"
SIGNAL_LIMIT_BURST = "limit_burst"
SIGNAL_CAPACITY_DROP = "capacity_drop"
SIGNAL_ERROR_CATEGORY_SHIFT = "error_category_shift"
SIGNAL_BURST_USAGE_RISING = "burst_usage_rising"

EVENT_SPIKE_SIGNALS = {
    SIGNAL_401_BURST,
    SIGNAL_BAN_BURST,
    SIGNAL_LIMIT_BURST,
    SIGNAL_CAPACITY_DROP,
    SIGNAL_ERROR_CATEGORY_SHIFT,
    SIGNAL_BURST_USAGE_RISING,
}


async def list_agent_event_triggers(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None = None,
    pool_id: str | None = None,
    signal: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    normalized_site_id = _clean_optional_string(site_id)
    normalized_pool_id = _clean_optional_string(pool_id)
    normalized_signal = _clean_optional_string(signal)
    normalized_status = _clean_optional_string(status)
    if normalized_site_id:
        query["site_id"] = normalized_site_id
    if normalized_pool_id:
        query["pool_id"] = normalized_pool_id
    if normalized_signal:
        query["signal"] = normalized_signal
    if normalized_status:
        query["status"] = normalized_status
    normalized_limit = max(1, min(int(limit or 50), 200))
    items = [
        item
        async for item in db[AGENT_EVENT_TRIGGERS_COLLECTION].find(query).sort("created_at", -1).limit(normalized_limit)
    ]
    total = await db[AGENT_EVENT_TRIGGERS_COLLECTION].count_documents(query)
    return {"items": serialize_doc(items), "total": total}


async def detect_agent_event_spikes(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
) -> dict[str, Any]:
    """Detect event-spike wakeup signals from existing cached data only."""

    now = now_utc()
    pools_response = await list_agent_pools(db)
    pools = [item for item in pools_response.get("items", []) if isinstance(item, dict)]
    max_triggers = _non_negative_int(getattr(settings, "max_event_triggers_per_tick", 3), default=3)
    max_pool_scan = max(max_triggers * 4, _non_negative_int(getattr(settings, "max_pool_patrols_per_tick", 3), default=3), 1)
    cooldown_minutes = _non_negative_int(getattr(settings, "event_trigger_cooldown_minutes", 15), default=15)
    excluded_pool_ids = set(_clean_string_list(getattr(settings, "excluded_agent_pool_ids", [])))

    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    scanned = 0
    for pool in pools[:max_pool_scan]:
        if len(created) >= max_triggers:
            break
        pool_id = _clean_optional_string(pool.get("id"))
        site_id = _clean_optional_string(pool.get("site_id"))
        group_id = _int_or_none(pool.get("active_group_id"))
        if not pool_id or not site_id or group_id is None:
            skipped.append({"pool_id": pool_id, "reason": "pool_identity_incomplete"})
            continue
        if pool_id in excluded_pool_ids:
            skipped.append({"pool_id": pool_id, "site_id": site_id, "reason": "agent_pool_excluded"})
            continue
        if _pool_disabled(pool):
            skipped.append({"pool_id": pool_id, "site_id": site_id, "reason": "pool_disabled"})
            continue
        if not _pool_strategy_allows_event_spike(settings, pool_id=pool_id, site_id=site_id):
            skipped.append({"pool_id": pool_id, "site_id": site_id, "reason": "pool_strategy_disabled"})
            continue
        scanned += 1
        try:
            event_windows = await read_agent_event_windows(
                db,
                site_id=site_id,
                group_id=group_id,
                pool_id=pool_id,
                account_type=_clean_optional_string(pool.get("account_type")),
            )
            capacity = await read_pool_capacity(db, pool_id)
            signals = _detect_pool_signals(
                pool=pool,
                capacity=capacity,
                event_windows=event_windows,
                now=now,
            )
            for signal in signals:
                if len(created) >= max_triggers:
                    break
                record = await _create_event_trigger(
                    db,
                    site_id=site_id,
                    pool_id=pool_id,
                    signal=signal["signal"],
                    evidence=signal,
                    now=now,
                    cooldown_minutes=cooldown_minutes,
                    scheduler_tick_id=scheduler_tick_id,
                )
                if record.get("created"):
                    created.append(record["trigger"])
                else:
                    skipped.append(record)
        except Exception as exc:  # noqa: BLE001 - one pool must not stop spike detection.
            skipped.append({"pool_id": pool_id, "reason": str(exc) or exc.__class__.__name__})

    return {"scanned_pools": scanned, "created": created, "skipped": skipped, "total_created": len(created)}


async def process_agent_event_spikes(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    detection = await detect_agent_event_spikes(db, settings=settings, scheduler_tick_id=scheduler_tick_id)
    processed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []

    for trigger in detection.get("created", []):
        trigger_id = _clean_optional_string(trigger.get("trigger_id") or trigger.get("_id"))
        pool_id = _clean_optional_string(trigger.get("pool_id"))
        signal = _clean_optional_string(trigger.get("signal"))
        if not trigger_id or not pool_id or not signal:
            continue
        try:
            report = await run_agent_controller(
                db,
                trigger=TRIGGER_EVENT_SPIKE,
                user_message=None,
                pool_id=pool_id,
                conversation_id=None,
                metadata={
                    "trigger_reason": f"event spike detected: {signal}",
                    "trigger_source": "agent_scheduler",
                    "scheduler_tick_id": scheduler_tick_id,
                    "event_trigger_id": trigger_id,
                    "signal": signal,
                    "pool_id": pool_id,
                    "site_id": trigger.get("site_id"),
                },
                actor=actor,
            )
            await _update_event_trigger_status(
                db,
                trigger_id=trigger_id,
                status="processed",
                run_id=_clean_optional_string(report.get("run_id")),
                error=None,
            )
            processed.append({"trigger_id": trigger_id, "signal": signal, "pool_id": pool_id, "run_id": report.get("run_id")})
        except Exception as exc:  # noqa: BLE001 - event trigger failures are recorded and scheduler continues.
            error = str(exc) or exc.__class__.__name__
            await _update_event_trigger_status(db, trigger_id=trigger_id, status="failed", run_id=None, error=error)
            failed.append({"trigger_id": trigger_id, "signal": signal, "pool_id": pool_id, "error": error})

    return {"detection": detection, "processed": processed, "failed": failed}


def _detect_pool_signals(*, pool: dict[str, Any], capacity: dict[str, Any], event_windows: dict[str, Any], now: datetime) -> list[dict[str, Any]]:
    detail_items = _detail_items(event_windows)
    last_10m = _items_since(detail_items, now=now, minutes=10)
    last_30m = _items_since(detail_items, now=now, minutes=30)
    last_1h = _items_since(detail_items, now=now, minutes=60)
    summary_1h = event_windows.get("summary_1h") if isinstance(event_windows.get("summary_1h"), dict) else {}

    signals: list[dict[str, Any]] = []
    count_401_10m = _count_401(last_10m)
    count_401_30m = _count_401(last_30m)
    if count_401_10m >= 5 or count_401_30m >= 10 or _has_401_time_cluster(summary_1h, minimum=5):
        signals.append(
            _signal(
                SIGNAL_401_BURST,
                "401 events reached burst threshold.",
                {
                    "count_401_10m": count_401_10m,
                    "count_401_30m": count_401_30m,
                    "summary_1h_clusters": summary_1h.get("clusters") if isinstance(summary_1h.get("clusters"), list) else [],
                },
            )
        )

    ban_count_1h = _count_ban_like(last_1h)
    if ban_count_1h >= 5:
        signals.append(_signal(SIGNAL_BAN_BURST, "Ban or disabled events reached 1h threshold.", {"ban_like_1h": ban_count_1h}))

    limit_count_1h = _count_limit_like(last_1h)
    if limit_count_1h >= 10:
        signals.append(_signal(SIGNAL_LIMIT_BURST, "Limit events reached 1h threshold.", {"limit_like_1h": limit_count_1h}))

    active_count = _number_or_none(capacity.get("active_account_count"))
    if active_count is not None and active_count > 0:
        changed_count = ban_count_1h + _count_status_drop_like(last_1h)
        drop_ratio = changed_count / max(active_count + changed_count, 1)
        if changed_count >= 5 and drop_ratio >= 0.2:
            signals.append(
                _signal(
                    SIGNAL_CAPACITY_DROP,
                    "Event-derived active capacity drop reached threshold.",
                    {"active_account_count": active_count, "drop_like_1h": changed_count, "drop_ratio": round(drop_ratio, 4)},
                )
            )

    error_shift = _error_category_shift(summary_1h)
    if error_shift:
        signals.append(_signal(SIGNAL_ERROR_CATEGORY_SHIFT, "One error category dominates the recent 1h event stream.", error_shift))

    burst_trend = str(capacity.get("burst_1h_trend") or "").strip().lower()
    burst_strength = str(capacity.get("burst_1h_trend_strength") or "").strip().lower()
    if burst_trend == "rising" and burst_strength in {"strong", "extreme"}:
        signals.append(
            _signal(
                SIGNAL_BURST_USAGE_RISING,
                "Burst usage trend is rising with strong or extreme strength.",
                {
                    "burst_1h_trend": capacity.get("burst_1h_trend"),
                    "burst_1h_trend_strength": capacity.get("burst_1h_trend_strength"),
                    "burst_1h_trend_change_percent": capacity.get("burst_1h_trend_change_percent"),
                },
            )
        )

    for item in signals:
        item["pool"] = {
            "pool_id": pool.get("id"),
            "site_id": pool.get("site_id"),
            "name": pool.get("name"),
            "account_type": pool.get("account_type"),
        }
    return signals


async def _create_event_trigger(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    pool_id: str,
    signal: str,
    evidence: dict[str, Any],
    now: datetime,
    cooldown_minutes: int,
    scheduler_tick_id: str | None,
) -> dict[str, Any]:
    bucket = _time_bucket(now, cooldown_minutes=max(cooldown_minutes, 1))
    dedupe_key = f"agent_event_spike:{site_id}:{pool_id}:{signal}:{bucket}"
    trigger_id = _new_id()
    document = {
        "_id": trigger_id,
        "trigger_id": trigger_id,
        "trigger_type": "event_spike",
        "site_id": site_id,
        "pool_id": pool_id,
        "signal": signal,
        "dedupe_key": dedupe_key,
        "status": "created",
        "evidence": evidence,
        "scheduler_tick_id": scheduler_tick_id,
        "run_id": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    try:
        await db[AGENT_EVENT_TRIGGERS_COLLECTION].insert_one(document)
        return {"created": True, "trigger": serialize_doc(document)}
    except DuplicateKeyError:
        return {"created": False, "pool_id": pool_id, "signal": signal, "reason": "dedupe_key_exists", "dedupe_key": dedupe_key}


async def _update_event_trigger_status(
    db: AsyncIOMotorDatabase,
    *,
    trigger_id: str,
    status: str,
    run_id: str | None,
    error: str | None,
) -> None:
    await db[AGENT_EVENT_TRIGGERS_COLLECTION].update_one(
        {"_id": trigger_id},
        {"$set": {"status": status, "run_id": run_id, "error": error, "updated_at": now_utc()}},
    )


def _signal(signal: str, reason: str, evidence: dict[str, Any]) -> dict[str, Any]:
    return {"signal": signal, "reason": reason, "evidence": evidence}


def _detail_items(event_windows: dict[str, Any]) -> list[dict[str, Any]]:
    detail_24h = event_windows.get("detail_24h") if isinstance(event_windows.get("detail_24h"), dict) else {}
    return [item for item in detail_24h.get("items", []) if isinstance(item, dict)]


def _items_since(items: list[dict[str, Any]], *, now: datetime, minutes: int) -> list[dict[str, Any]]:
    threshold = now - timedelta(minutes=minutes)
    return [item for item in items if (_event_time(item) and _event_time(item) >= threshold)]


def _event_time(item: dict[str, Any]) -> datetime | None:
    value = item.get("occurred_at") or item.get("detected_at")
    if not value:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _count_401(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if _is_401_item(item))


def _is_401_item(item: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "").lower()
        for value in (item.get("event_type"), item.get("error_category"), item.get("message"))
    )
    return bool(item.get("is_401")) or "401" in text or "authentication" in text


def _has_401_time_cluster(summary_1h: dict[str, Any], *, minimum: int) -> bool:
    clusters = summary_1h.get("clusters") if isinstance(summary_1h.get("clusters"), list) else []
    for cluster in clusters:
        if not isinstance(cluster, dict):
            continue
        label = str(cluster.get("dominant_event_type") or cluster.get("dominant_error_category") or "").lower()
        if ("401" in label or "authentication" in label) and int(_number_or_none(cluster.get("event_count")) or 0) >= minimum:
            return True
    return False


def _count_ban_like(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if _is_ban_like(item))


def _is_ban_like(item: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "").lower()
        for value in (item.get("event_type"), item.get("to_status"), item.get("current_status"), item.get("message"))
    )
    return any(value in text for value in ("remote_removed", "missing_suspected", "disabled", "banned", "invalid", "failed"))


def _count_limit_like(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if _is_limit_like(item))


def _is_limit_like(item: dict[str, Any]) -> bool:
    text = " ".join(
        str(value or "").lower()
        for value in (item.get("event_type"), item.get("to_status"), item.get("current_status"), item.get("message"))
    )
    return any(value in text for value in ("usage_rollover", "limit", "quota", "rate_limited", "rate limit", "429"))


def _count_status_drop_like(items: list[dict[str, Any]]) -> int:
    return sum(1 for item in items if _is_ban_like(item) or _is_401_item(item))


def _error_category_shift(summary_1h: dict[str, Any]) -> dict[str, Any] | None:
    counts = summary_1h.get("error_category_counts") if isinstance(summary_1h.get("error_category_counts"), dict) else {}
    if not counts:
        return None
    category, count = max(counts.items(), key=lambda pair: int(pair[1] or 0))
    total = sum(int(value or 0) for value in counts.values())
    if count >= 5 and total > 0 and count / total >= 0.6:
        return {"error_category": category, "count_1h": count, "total_error_events_1h": total, "dominance_ratio": round(count / total, 4)}
    return None


def _time_bucket(now: datetime, *, cooldown_minutes: int) -> str:
    seconds = max(60, cooldown_minutes * 60)
    epoch = int(now.timestamp())
    bucket_start = epoch - (epoch % seconds)
    return datetime.fromtimestamp(bucket_start, tz=timezone.utc).isoformat()


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        number = int(value)
        return number if number >= 0 else default
    except (TypeError, ValueError):
        return default


def _pool_disabled(pool: dict[str, Any]) -> bool:
    status = str(pool.get("status") or "").strip().lower()
    remote_status = str(pool.get("remote_status") or "").strip().lower()
    return status == "disabled" or remote_status == "disabled"


def _pool_strategy_allows_event_spike(settings: Any, *, pool_id: str, site_id: str | None) -> bool:
    strategies = getattr(settings, "pool_strategies", [])
    if not isinstance(strategies, list):
        return True
    matched = False
    allowed = True
    for item in strategies:
        if not isinstance(item, dict):
            continue
        strategy_pool_id = _clean_optional_string(item.get("pool_id"))
        strategy_site_id = _clean_optional_string(item.get("site_id"))
        if strategy_pool_id and strategy_pool_id != pool_id:
            continue
        if strategy_site_id and strategy_site_id != site_id:
            continue
        if not strategy_pool_id and not strategy_site_id:
            continue
        matched = True
        if item.get("agent_enabled") is False or item.get("event_spike_enabled") is False:
            allowed = False
    return allowed if matched else True


def _clean_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _clean_optional_string(item)
        if not text or text in seen:
            continue
        seen.add(text)
        items.append(text)
    return items


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _new_id() -> str:
    return secrets.token_urlsafe(16)
