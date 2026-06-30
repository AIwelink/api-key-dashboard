from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.capacity import list_agent_pools, read_pool_capacity
from app.modules.agent.event_stream import read_agent_event_stream_summary
from app.modules.agent.memory import AGENT_DECISIONS_COLLECTION, AGENT_MESSAGES_COLLECTION
from app.modules.agent.probe import read_probe_summary
from app.utils import now_utc, serialize_doc


CONTEXT_PACK_SCHEMA_VERSION = "agent_context_pack.v1"
DEFAULT_RECENT_DECISION_LIMIT = 5
DEFAULT_CONVERSATION_LIMIT = 20


async def build_agent_context_pack(
    db: AsyncIOMotorDatabase,
    *,
    trigger: str,
    pool_id: str | None,
    user_message: str | None,
    conversation_id: str | None,
    actor: dict[str, Any] | None = None,
    recent_decision_limit: int = DEFAULT_RECENT_DECISION_LIMIT,
    conversation_limit: int = DEFAULT_CONVERSATION_LIMIT,
) -> dict[str, Any]:
    """Build the read-only context package for one Agent thinking turn.

    The function only reads existing database/cache state. It does not refresh
    sub2api, start account probes, or modify account-pool business data.
    """

    warnings: list[str] = []
    pools_response = await list_agent_pools(db)
    pools = [item for item in pools_response.get("items", []) if isinstance(item, dict)]
    target_pool = _resolve_target_pool(pools=pools, pool_id=pool_id, user_message=user_message)
    if target_pool is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to resolve target Agent pool")

    resolved_pool_id = _clean_optional_string(target_pool.get("id"))
    if not resolved_pool_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Resolved Agent pool has no id")

    capacity: dict[str, Any] = {}
    probe: dict[str, Any] = {}
    event_stream: dict[str, Any] = {}

    try:
        capacity = await read_pool_capacity(db, resolved_pool_id)
    except Exception as exc:  # noqa: BLE001 - context pack should keep available history/constraints visible.
        warnings.append(f"capacity_unavailable: {exc}")

    pool_from_capacity = capacity.get("pool") if isinstance(capacity.get("pool"), dict) else {}
    target_pool = pool_from_capacity or target_pool
    target_pool_info = _build_target_pool_info(target_pool, capacity)

    site_id = _clean_optional_string(target_pool_info.get("site_id"))
    group_id = _int_or_none(target_pool_info.get("group_id"))
    if site_id and group_id is not None:
        try:
            probe = await read_probe_summary(
                db,
                site_id=site_id,
                group_id=group_id,
                account_type=_clean_optional_string(target_pool_info.get("account_type")),
            )
        except Exception as exc:  # noqa: BLE001 - probe data should not block all Agent context.
            warnings.append(f"probe_unavailable: {exc}")
        try:
            event_stream = await read_agent_event_stream_summary(
                db,
                site_id=site_id,
                group_id=group_id,
                account_type=_clean_optional_string(target_pool_info.get("account_type")),
            )
        except Exception as exc:  # noqa: BLE001 - event stream should not block core capacity context.
            warnings.append(f"event_stream_unavailable: {exc}")
    else:
        warnings.append("probe_unavailable: target pool site_id or group_id is missing")
        warnings.append("event_stream_unavailable: target pool site_id or group_id is missing")

    recent_decisions = await _recent_agent_decisions(db, pool_id=resolved_pool_id, limit=recent_decision_limit)
    conversation = await _recent_conversation_messages(db, conversation_id=conversation_id, limit=conversation_limit)

    context_pack = {
        "schema_version": CONTEXT_PACK_SCHEMA_VERSION,
        "built_at": now_utc(),
        "run": {
            "trigger": _clean_optional_string(trigger) or "manual",
            "user_message": _clean_optional_string(user_message),
            "conversation_id": _clean_optional_string(conversation_id),
            "created_by": _actor_id(actor),
            "created_by_role": _actor_role(actor),
        },
        "target_pool": target_pool_info,
        "api_pool_status": _summarize_api_pool_status(target_pool, capacity),
        "capacity": _summarize_capacity(capacity),
        "probe": _summarize_probe(probe),
        "event_stream": _summarize_event_stream(event_stream),
        "recent_agent_decisions": recent_decisions,
        "conversation": conversation,
        "system_constraints": _system_constraints(),
        "data_quality": {
            "capacity_available": bool(capacity),
            "probe_available": bool(probe),
            "event_stream_available": bool(event_stream),
            "history_available": bool(recent_decisions or conversation),
            "warnings": warnings,
            "source": {
                "pools": "sub2api_groups_cache",
                "capacity": capacity.get("data_source") if capacity else None,
                "probe": probe.get("data_source") if probe else None,
                "event_stream": event_stream.get("data_source") if event_stream else None,
                "decisions": AGENT_DECISIONS_COLLECTION,
                "messages": AGENT_MESSAGES_COLLECTION,
            },
            "refresh_behavior": "read_existing_cache_only",
        },
    }
    return serialize_doc(context_pack)


def _resolve_target_pool(
    *,
    pools: list[dict[str, Any]],
    pool_id: str | None,
    user_message: str | None,
) -> dict[str, Any] | None:
    normalized_pool_id = _clean_optional_string(pool_id)
    if normalized_pool_id:
        matched = next((pool for pool in pools if str(pool.get("id")) == normalized_pool_id), None)
        if matched:
            return matched

    matched = _match_pool(user_message or "", pools)
    if matched:
        return matched
    if len(pools) == 1:
        return pools[0]
    return None


def _build_target_pool_info(pool: dict[str, Any], capacity: dict[str, Any]) -> dict[str, Any]:
    site_id = pool.get("site_id") or capacity.get("site_id")
    group_id = pool.get("active_group_id") or capacity.get("group_id")
    return {
        "pool_id": _clean_optional_string(pool.get("id")),
        "site_id": _clean_optional_string(site_id),
        "group_id": _int_or_none(group_id),
        "name": _clean_optional_string(pool.get("name")),
        "account_type": _clean_optional_string(pool.get("account_type")),
        "source": _clean_optional_string(pool.get("source")),
        "remote_status": pool.get("remote_status"),
        "updated_at": pool.get("updated_at"),
    }


def _summarize_api_pool_status(pool: dict[str, Any], capacity: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": "api_account_pool_status_cache",
        "refresh_behavior": "read_existing_cache_only",
        "pool_id": pool.get("id"),
        "site_id": capacity.get("site_id") or pool.get("site_id"),
        "group_id": capacity.get("group_id") or pool.get("active_group_id"),
        "remote_account_count": pool.get("remote_account_count"),
        "remote_active_account_count": pool.get("remote_active_account_count"),
        "remote_rate_limited_account_count": pool.get("remote_rate_limited_account_count"),
        "active_account_count": capacity.get("active_account_count"),
        "reserve_account_count": capacity.get("reserve_account_count"),
        "local_reserve_account_count": capacity.get("local_reserve_account_count"),
        "total_account_count": capacity.get("total_account_count"),
        "available_accounts": capacity.get("available_accounts"),
        "available_5h_accounts": capacity.get("available_5h_accounts"),
        "health_status": capacity.get("health_status"),
        "health_label": capacity.get("health_label"),
        "cache_fresh": capacity.get("cache_fresh"),
        "last_refreshed_at": capacity.get("last_refreshed_at"),
    }


def _summarize_capacity(capacity: dict[str, Any]) -> dict[str, Any]:
    if not capacity:
        return {}
    return {
        "active_account_count": capacity.get("active_account_count"),
        "reserve_account_count": capacity.get("reserve_account_count"),
        "available_accounts": capacity.get("available_accounts"),
        "available_5h_accounts": capacity.get("available_5h_accounts"),
        "total_account_count": capacity.get("total_account_count"),
        "current_speed_days": capacity.get("current_speed_days"),
        "five_hour_remaining_usd": capacity.get("five_hour_remaining_usd"),
        "seven_day_remaining_usd": capacity.get("seven_day_remaining_usd"),
        "recent_day_five_hour_peak_multiple": capacity.get("recent_day_five_hour_peak_multiple"),
        "seven_day_five_hour_peak_multiple": capacity.get("seven_day_five_hour_peak_multiple"),
        "burst_1h_five_hour_multiple": capacity.get("burst_1h_five_hour_multiple"),
        "active_burst_1h_five_hour_multiple": capacity.get("active_burst_1h_five_hour_multiple"),
        "burst_1h_observed_cost": capacity.get("burst_1h_observed_cost"),
        "burst_1h_elapsed_minutes": capacity.get("burst_1h_elapsed_minutes"),
        "burst_1h_cost": capacity.get("burst_1h_cost"),
        "burst_1h_five_hour_estimated_cost": capacity.get("burst_1h_five_hour_estimated_cost"),
        "burst_1h_trend": capacity.get("burst_1h_trend"),
        "burst_1h_trend_label": capacity.get("burst_1h_trend_label"),
        "burst_1h_trend_strength": capacity.get("burst_1h_trend_strength"),
        "burst_1h_trend_strength_label": capacity.get("burst_1h_trend_strength_label"),
        "burst_1h_trend_change_percent": capacity.get("burst_1h_trend_change_percent"),
        "burst_1h_trend_recent_avg_cost": capacity.get("burst_1h_trend_recent_avg_cost"),
        "burst_1h_trend_baseline_avg_cost": capacity.get("burst_1h_trend_baseline_avg_cost"),
        "burst_1h_trend_recent_hours": capacity.get("burst_1h_trend_recent_hours"),
        "burst_1h_trend_baseline_hours": capacity.get("burst_1h_trend_baseline_hours"),
        "health_status": capacity.get("health_status"),
        "health_label": capacity.get("health_label"),
        "cache_fresh": capacity.get("cache_fresh"),
        "last_refreshed_at": capacity.get("last_refreshed_at"),
        "capacity_calculated_at": capacity.get("capacity_calculated_at"),
    }


def _summarize_probe(probe: dict[str, Any]) -> dict[str, Any]:
    if not probe:
        return {}
    return {
        "probe_fresh": probe.get("probe_fresh"),
        "last_probe_at": probe.get("last_probe_at"),
        "probe_status": probe.get("probe_status"),
        "data_source": probe.get("data_source"),
        "detected_401_1h": probe.get("detected_401_1h"),
        "detected_401_24h": probe.get("detected_401_24h"),
        "detected_401_7d": probe.get("detected_401_7d"),
        "recovered_24h": probe.get("recovered_24h"),
        "detected_401_clusters_24h": probe.get("detected_401_clusters_24h"),
        "largest_401_cluster_24h": probe.get("largest_401_cluster_24h"),
        "concentrated_401_burst_24h": probe.get("concentrated_401_burst_24h"),
        "duplicate_email_alert_count": probe.get("duplicate_email_alert_count"),
        "median_survival_hours_7d": probe.get("median_survival_hours_7d"),
        "recent_events": _summarize_recent_events(probe.get("recent_events")),
        "event_summary_24h": probe.get("event_summary_24h"),
        "event_summary_7d": probe.get("event_summary_7d"),
    }


def _summarize_event_stream(event_stream: dict[str, Any]) -> dict[str, Any]:
    if not event_stream:
        return {}
    return {
        "data_source": event_stream.get("data_source"),
        "range": event_stream.get("range"),
        "total": event_stream.get("total"),
        "summary": event_stream.get("summary"),
        "event_type_counts": event_stream.get("event_type_counts"),
        "status_transition_counts": event_stream.get("status_transition_counts"),
        "error_category_counts": event_stream.get("error_category_counts"),
        "notable_patterns": event_stream.get("notable_patterns"),
        "recent_timeline": event_stream.get("recent_timeline"),
    }


def _summarize_recent_events(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, Any]] = []
    for item in value[:8]:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "event_type": item.get("event_type"),
                "status": item.get("status"),
                "detected_at": item.get("detected_at"),
                "resolved_at": item.get("resolved_at"),
                "account_id": item.get("account_id"),
                "group_id": item.get("group_id"),
                "account_type": item.get("account_type"),
            }
        )
    return items


async def _recent_agent_decisions(db: AsyncIOMotorDatabase, *, pool_id: str, limit: int) -> list[dict[str, Any]]:
    normalized_limit = _normalize_limit(limit, default=DEFAULT_RECENT_DECISION_LIMIT, maximum=20)
    cursor = db[AGENT_DECISIONS_COLLECTION].find({"pool_id": pool_id}).sort("created_at", -1).limit(normalized_limit)
    decisions: list[dict[str, Any]] = []
    async for item in cursor:
        decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
        decisions.append(
            {
                "decision_id": item.get("decision_id") or item.get("_id"),
                "run_id": item.get("run_id"),
                "conversation_id": item.get("conversation_id"),
                "created_at": item.get("created_at"),
                "severity": item.get("severity"),
                "headline": item.get("headline"),
                "summary": item.get("summary"),
                "suggested_add_count": decision.get("suggested_add_count"),
                "confidence": decision.get("confidence"),
                "requires_human_confirm": item.get("requires_human_confirm"),
                "decision_mode": item.get("decision_mode") or decision.get("mode"),
            }
        )
    return serialize_doc(decisions)


async def _recent_conversation_messages(
    db: AsyncIOMotorDatabase,
    *,
    conversation_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    normalized_conversation_id = _clean_optional_string(conversation_id)
    if not normalized_conversation_id:
        return []
    normalized_limit = _normalize_limit(limit, default=DEFAULT_CONVERSATION_LIMIT, maximum=50)
    cursor = (
        db[AGENT_MESSAGES_COLLECTION]
        .find({"conversation_id": normalized_conversation_id})
        .sort("created_at", -1)
        .limit(normalized_limit)
    )
    messages = [
        {
            "message_id": item.get("message_id") or item.get("_id"),
            "run_id": item.get("run_id"),
            "pool_id": item.get("pool_id"),
            "site_id": item.get("site_id"),
            "role": item.get("role"),
            "content": item.get("content"),
            "created_at": item.get("created_at"),
        }
        async for item in cursor
    ]
    messages.reverse()
    return serialize_doc(messages)


def _system_constraints() -> dict[str, Any]:
    return {
        "stage": "stage_3_context_pack_llm_primary_design",
        "read_only": True,
        "can_send_dingtalk": False,
        "can_create_todo": False,
        "can_push_accounts": False,
        "can_delete_accounts": False,
        "can_buy_accounts": False,
        "can_modify_pool_config": False,
        "can_refresh_sub2api": False,
        "can_start_account_probe": False,
        "high_risk_actions_require_human_confirm": True,
        "allowed_outputs": [
            "risk_assessment",
            "replenishment_recommendation",
            "warning_recommendation",
            "human_review_request",
            "next_observation_plan",
        ],
    }


def _match_pool(message: str, pools: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = str(message or "").lower()
    scored: list[tuple[int, dict[str, Any]]] = []
    for pool in pools:
        score = _pool_match_score(normalized, pool)
        if score > 0:
            scored.append((score, pool))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]
    return None


def _pool_match_score(message: str, pool: dict[str, Any]) -> int:
    score = 0
    name = str(pool.get("name") or "").strip().lower()
    account_type = str(pool.get("account_type") or "").strip().lower()
    group_id = str(pool.get("active_group_id") or "").strip().lower()
    pool_id = str(pool.get("id") or "").strip().lower()
    if name and name in message:
        score += 10
    for token in _tokens(name):
        if token and token in message:
            score += 2
    if account_type and account_type in message:
        score += 6
    if group_id and (f"group #{group_id}" in message or f"group{group_id}" in message or f"#{group_id}" in message):
        score += 4
    if pool_id and pool_id in message:
        score += 10
    for account_type_token in ("pro", "plus", "free", "team", "k12"):
        if account_type_token in message and account_type == account_type_token:
            score += 8
    return score


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in value.replace("/", " ").replace("-", " ").replace("_", " ").replace("#", " ").split()
        if len(token) >= 2
    ]


def _normalize_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, maximum))


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _actor_id(actor: dict[str, Any] | None) -> str | None:
    if not actor:
        return None
    value = actor.get("_id") or actor.get("id") or actor.get("user_id")
    return str(value) if value is not None else None


def _actor_role(actor: dict[str, Any] | None) -> str | None:
    if not actor:
        return None
    value = actor.get("role") or actor.get("user_role")
    return str(value) if value is not None else None
