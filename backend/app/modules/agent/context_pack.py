from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.capacity import list_agent_pools, read_pool_capacity
from app.modules.agent.event_stream import read_agent_event_stream_summary, read_agent_event_windows
from app.modules.agent.long_term_memory import get_agent_long_term_memory
from app.modules.agent.memory import AGENT_DECISIONS_COLLECTION, AGENT_MESSAGES_COLLECTION
from app.modules.agent.operational_facts import build_capacity_dictionary, build_operational_facts
from app.modules.agent.probe import read_probe_summary
from app.utils import now_utc, serialize_doc


CONTEXT_PACK_SCHEMA_VERSION = "agent_context_pack.v2"
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
    event_windows: dict[str, Any] = {}

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
            event_windows = await read_agent_event_windows(
                db,
                site_id=site_id,
                group_id=group_id,
                account_type=_clean_optional_string(target_pool_info.get("account_type")),
                detail_24h_limit=80,
            )
        except Exception as exc:  # noqa: BLE001 - event windows should not block core capacity context.
            warnings.append(f"event_windows_unavailable: {exc}")
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
    try:
        long_term_memory = await get_agent_long_term_memory(db, site_id=site_id, pool_id=resolved_pool_id)
    except Exception as exc:  # noqa: BLE001 - long-term memory should not block a live analysis turn.
        warnings.append(f"long_term_memory_unavailable: {exc}")
        long_term_memory = _empty_long_term_memory()

    capacity_summary = _summarize_capacity(capacity)
    probe_summary = _summarize_probe(probe)
    event_stream_summary = _summarize_event_stream(event_stream)
    event_windows_summary = _summarize_event_windows(event_windows, event_stream_summary)
    recent_conversation = conversation
    capacity_dictionary = build_capacity_dictionary(_clean_optional_string(target_pool_info.get("account_type")))
    operational_facts = build_operational_facts(
        capacity=capacity_summary,
        probe=probe_summary,
        event_windows=event_windows_summary,
        recent_decisions=recent_decisions,
        long_term_memory=long_term_memory,
    )

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
        "capacity": capacity_summary,
        "capacity_dictionary": capacity_dictionary,
        "operational_facts": operational_facts,
        "event_windows": event_windows_summary,
        "probe": probe_summary,
        "event_stream": event_stream_summary,
        "recent_agent_decisions": recent_decisions,
        "recent_conversation": recent_conversation,
        "conversation": recent_conversation,
        "long_term_memory": long_term_memory,
        "system_constraints": _system_constraints(),
        "data_quality": {
            "capacity_available": bool(capacity),
            "probe_available": bool(probe),
            "event_stream_available": bool(event_stream),
            "event_windows_available": bool(event_windows_summary.get("detail_24h", {}).get("items"))
            or any(bool(event_windows_summary.get(key, {}).get("total_events")) for key in ("summary_1h", "summary_6h", "summary_24h", "summary_7d")),
            "history_available": bool(recent_decisions or recent_conversation),
            "long_term_memory_available": any(bool(value) for value in long_term_memory.values()),
            "operational_facts_available": any(bool(value) for value in operational_facts.values()),
            "warnings": warnings,
            "source": {
                "pools": "sub2api_groups_cache",
                "capacity": capacity.get("data_source") if capacity else None,
                "probe": probe.get("data_source") if probe else None,
                "event_stream": event_stream.get("data_source") if event_stream else None,
                "event_windows": event_windows.get("data_source") if event_windows else event_stream.get("data_source") if event_stream else None,
                "decisions": AGENT_DECISIONS_COLLECTION,
                "messages": AGENT_MESSAGES_COLLECTION,
                "long_term_memory": "agent_memory_summaries",
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
    current_speed_days = capacity.get("current_speed_days")
    seven_day_peak_speed_days = _from_capacity_summary(capacity, "seven_day_peak_speed_days")
    recent_24h_cost = _from_capacity_summary(capacity, "recent_24h_cost")
    seven_day_24h_peak_cost = _from_capacity_summary(capacity, "seven_day_24h_peak_cost")
    return {
        "account_type": _clean_optional_string((capacity.get("pool") or {}).get("account_type") if isinstance(capacity.get("pool"), dict) else None),
        "single_account_5h_limit_usd": _single_account_5h_limit_usd(capacity),
        "single_account_7d_limit_usd": _single_account_7d_limit_usd(capacity),
        "active_account_count": capacity.get("active_account_count"),
        "reserve_account_count": capacity.get("reserve_account_count"),
        "available_accounts": capacity.get("available_accounts"),
        "available_5h_accounts": capacity.get("available_5h_accounts"),
        "total_account_count": capacity.get("total_account_count"),
        "dynamic_5h_total_usd": _from_capacity_summary(capacity, "dynamic_five_hour_capacity_usd", "five_hour_capacity_usd", "dynamic_five_hour_total_estimated_usd", "five_hour_total_estimated_usd"),
        "dynamic_5h_used_usd": _from_capacity_summary(capacity, "dynamic_five_hour_used_estimated_usd", "five_hour_used_estimated_usd"),
        "dynamic_5h_available_usd": _from_capacity_summary(capacity, "dynamic_five_hour_remaining_estimated_usd", "five_hour_remaining_estimated_usd"),
        "actual_5h_used_usd": _from_capacity_summary(capacity, "five_hour_actual_used_usd", "actual_five_hour_used_usd"),
        "actual_5h_available_usd": _from_capacity_summary(capacity, "five_hour_actual_remaining_usd", "actual_five_hour_remaining_usd"),
        "dynamic_7d_total_usd": _from_capacity_summary(capacity, "seven_day_capacity_usd", "seven_day_total_estimated_usd", "dynamic_seven_day_total_estimated_usd"),
        "dynamic_7d_used_usd": _from_capacity_summary(capacity, "seven_day_used_estimated_usd", "dynamic_seven_day_used_estimated_usd"),
        "dynamic_7d_available_usd": _from_capacity_summary(capacity, "seven_day_remaining_estimated_usd", "dynamic_seven_day_remaining_estimated_usd"),
        "actual_7d_used_usd": _from_capacity_summary(capacity, "seven_day_actual_used_usd", "actual_seven_day_used_usd"),
        "actual_7d_available_usd": _from_capacity_summary(capacity, "seven_day_actual_remaining_usd", "actual_seven_day_remaining_usd"),
        "recent_5h_cost": _from_capacity_summary(capacity, "recent_5h_cost"),
        "recent_24h_cost": recent_24h_cost,
        "seven_day_24h_peak_cost": seven_day_24h_peak_cost,
        "recent_24h_remaining_usd": _from_capacity_summary(capacity, "recent_24h_remaining_usd"),
        "current_speed_days": current_speed_days,
        "recent_24h_runway_days": current_speed_days,
        "seven_day_highest_24h_runway_hours": seven_day_peak_speed_days * 24 if seven_day_peak_speed_days is not None else None,
        "five_hour_remaining_usd": capacity.get("five_hour_remaining_usd"),
        "seven_day_remaining_usd": capacity.get("seven_day_remaining_usd"),
        "recent_day_five_hour_peak_multiple": capacity.get("recent_day_five_hour_peak_multiple"),
        "recent_day_5h_peak_multiple": capacity.get("recent_day_five_hour_peak_multiple"),
        "seven_day_five_hour_peak_multiple": capacity.get("seven_day_five_hour_peak_multiple"),
        "seven_day_highest_five_hour_peak_multiple": capacity.get("seven_day_five_hour_peak_multiple"),
        "seven_day_highest_5h_peak_multiple": capacity.get("seven_day_five_hour_peak_multiple"),
        "burst_1h_five_hour_multiple": capacity.get("burst_1h_five_hour_multiple"),
        "burst_1h_estimated_5h_multiple": capacity.get("burst_1h_five_hour_multiple"),
        "active_burst_1h_five_hour_multiple": capacity.get("active_burst_1h_five_hour_multiple"),
        "burst_1h_observed_cost": capacity.get("burst_1h_observed_cost"),
        "burst_1h_elapsed_minutes": capacity.get("burst_1h_elapsed_minutes"),
        "burst_1h_projection_multiplier": _from_capacity_summary(capacity, "burst_1h_projection_multiplier"),
        "burst_1h_cost": capacity.get("burst_1h_cost"),
        "burst_1h_five_hour_estimated_cost": capacity.get("burst_1h_five_hour_estimated_cost"),
        "burst_1h_trend": capacity.get("burst_1h_trend"),
        "burst_1h_trend_label": capacity.get("burst_1h_trend_label"),
        "burst_trend_label": capacity.get("burst_1h_trend_label"),
        "burst_1h_trend_strength": capacity.get("burst_1h_trend_strength"),
        "burst_1h_trend_strength_label": capacity.get("burst_1h_trend_strength_label"),
        "burst_trend_strength": capacity.get("burst_1h_trend_strength"),
        "burst_trend_strength_label": capacity.get("burst_1h_trend_strength_label"),
        "burst_1h_trend_change_percent": capacity.get("burst_1h_trend_change_percent"),
        "burst_1h_trend_recent_avg_cost": capacity.get("burst_1h_trend_recent_avg_cost"),
        "burst_1h_trend_baseline_avg_cost": capacity.get("burst_1h_trend_baseline_avg_cost"),
        "burst_1h_trend_recent_hours": capacity.get("burst_1h_trend_recent_hours"),
        "burst_1h_trend_baseline_hours": capacity.get("burst_1h_trend_baseline_hours"),
        "recent_24h_estimated_account_consumption": _from_capacity_summary(capacity, "estimated_recent_24h_consumed_accounts", "estimated_24h_consumed_accounts"),
        "seven_day_highest_24h_estimated_account_consumption": _from_capacity_summary(capacity, "estimated_seven_day_peak_24h_consumed_accounts"),
        "recent_day_five_hour_peak_speed_days": _from_capacity_summary(capacity, "recent_day_five_hour_peak_speed_days"),
        "seven_day_five_hour_peak_speed_days": _from_capacity_summary(capacity, "seven_day_five_hour_peak_speed_days"),
        "seven_day_peak_speed_days": seven_day_peak_speed_days,
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


def _summarize_event_windows(event_windows: dict[str, Any], event_stream: dict[str, Any]) -> dict[str, Any]:
    if event_windows:
        return event_windows

    if not event_stream:
        return {
            "detail_24h": {"window_hours": 24, "max_items": 80, "items": []},
            "summary_1h": _empty_event_summary("1h"),
            "summary_6h": _empty_event_summary("6h"),
            "summary_24h": _empty_event_summary("24h"),
            "summary_7d": _empty_event_summary("7d"),
            "notable_patterns": [],
            "data_quality": {"available": False, "warnings": ["event_stream_unavailable"]},
        }

    timeline = event_stream.get("recent_timeline") if isinstance(event_stream.get("recent_timeline"), list) else []
    summary_24h = _event_summary_from_stream(event_stream, window="24h")
    return {
        "detail_24h": {
            "window_hours": 24,
            "max_items": 80,
            "items": timeline[:80],
        },
        "summary_1h": _empty_event_summary("1h", source="fallback_without_multi_window_event_summary"),
        "summary_6h": _empty_event_summary("6h", source="fallback_without_multi_window_event_summary"),
        "summary_24h": summary_24h,
        "summary_7d": _empty_event_summary("7d", source="fallback_without_multi_window_event_summary"),
        "notable_patterns": event_stream.get("notable_patterns") if isinstance(event_stream.get("notable_patterns"), list) else [],
        "data_quality": {
            "available": True,
            "detail_24h_limited_to": 80,
            "multi_window_summaries": "summary_24h_available_1h_6h_7d_unavailable",
            "source": event_stream.get("data_source"),
        },
    }


def _event_summary_from_stream(event_stream: dict[str, Any], *, window: str) -> dict[str, Any]:
    return {
        "window": window,
        "total_events": event_stream.get("total"),
        "summary": event_stream.get("summary") if isinstance(event_stream.get("summary"), dict) else {},
        "event_type_counts": event_stream.get("event_type_counts") if isinstance(event_stream.get("event_type_counts"), dict) else {},
        "status_transition_counts": event_stream.get("status_transition_counts") if isinstance(event_stream.get("status_transition_counts"), dict) else {},
        "error_category_counts": event_stream.get("error_category_counts") if isinstance(event_stream.get("error_category_counts"), dict) else {},
        "clusters": _clusters_from_notable_patterns(event_stream.get("notable_patterns")),
        "interpretation": _interpret_event_patterns(event_stream.get("notable_patterns")),
        "source": event_stream.get("data_source"),
    }


def _empty_event_summary(window: str, *, source: str | None = None) -> dict[str, Any]:
    return {
        "window": window,
        "total_events": 0,
        "summary": {},
        "event_type_counts": {},
        "status_transition_counts": {},
        "error_category_counts": {},
        "clusters": [],
        "interpretation": [],
        "source": source,
    }


def _clusters_from_notable_patterns(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    clusters: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        pattern_type = str(item.get("type") or "")
        if "window" not in pattern_type and "cluster" not in pattern_type:
            continue
        clusters.append(
            {
                "cluster_type": pattern_type,
                "event_type": item.get("event_type"),
                "event_count": item.get("count"),
                "window_start": item.get("started_at"),
                "window_end": item.get("ended_at"),
                "duration_minutes": item.get("duration_minutes"),
                "interpretation": item.get("message"),
            }
        )
    return clusters[:10]


def _interpret_event_patterns(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    interpretations: list[str] = []
    for item in value:
        if isinstance(item, dict) and isinstance(item.get("message"), str) and item["message"].strip():
            interpretations.append(item["message"].strip())
    return interpretations[:10]


def _capacity_dictionary(account_type: str | None) -> dict[str, str]:
    normalized_type = (account_type or "account").strip().lower()
    return {
        "account_type": f"当前账号池识别为 {normalized_type} 类型；不同类型账号的额度含义应结合主系统容量数据理解。",
        "single_account_5h_limit_usd": "单个账号在 5h 窗口内可用额度。pro 账号当前通常按 360 美元理解。",
        "single_account_7d_limit_usd": "单个账号在 7d 窗口内可用额度。pro 账号当前通常按 2100 美元理解。",
        "dynamic_5h_total_usd": "综合账号当前已用比例和未来刷新恢复额度后，估算出的 5h 动态总容量。",
        "dynamic_5h_used_usd": "当前账号池按动态 5h 口径估算的已使用金额。",
        "dynamic_5h_available_usd": "当前账号池按动态 5h 口径估算的剩余可用金额。",
        "actual_5h_available_usd": "当前账号池按真实已用口径计算出的 5h 实际剩余金额。",
        "dynamic_7d_total_usd": "综合账号当前 7d 已用比例和未来刷新恢复额度后，估算出的 7d 动态总容量。",
        "dynamic_7d_used_usd": "当前账号池按动态 7d 口径估算的已使用金额。",
        "dynamic_7d_available_usd": "当前账号池按动态 7d 口径估算的剩余可用金额。",
        "actual_7d_available_usd": "当前账号池按真实已用口径计算出的 7d 实际剩余金额。",
        "recent_day_five_hour_peak_multiple": "最近一天最高 5h 峰值需求与当前 5h 容量的覆盖关系。小于 1 通常表示当前容量低于峰值需求，大于 1 通常表示当前容量可以覆盖峰值需求。",
        "recent_day_5h_peak_multiple": "recent_day_five_hour_peak_multiple 的短别名，含义相同。",
        "seven_day_highest_five_hour_peak_multiple": "最近 7d 最高 5h 峰值需求与当前 5h 容量的覆盖关系，用于观察极端历史峰值压力。",
        "seven_day_highest_5h_peak_multiple": "seven_day_highest_five_hour_peak_multiple 的短别名，含义相同。",
        "burst_1h_estimated_5h_multiple": "把当前小时已用按当前小时进度折算为完整 1h，再折算成 5h 压力后，与当前 5h 容量的覆盖关系。",
        "burst_trend_label": "最近 1h 突发趋势方向，例如上涨、下降或平稳。",
        "burst_trend_strength": "最近 1h 突发趋势强度，例如弱、中、强。",
        "recent_24h_runway_days": "按最近 24h 消耗速度估算当前 7d 可用容量还能支撑多久。",
        "seven_day_highest_24h_runway_hours": "按最近 7d 内最高 24h 消耗压力估算，当前 7d 可用容量在极端日压力下还能支撑多少小时。",
        "recent_24h_cost": "最近 24h 实际消耗金额。",
        "seven_day_24h_peak_cost": "最近 7d 内最高 24h 消耗金额。",
        "recent_24h_estimated_account_consumption": "按最近 24h 消耗金额除以单账号 7d 限额折算出的账号消耗速度。",
        "seven_day_highest_24h_estimated_account_consumption": "按最近 7d 内最高 24h 消耗金额除以单账号 7d 限额折算出的极端账号消耗速度。",
        "estimated_account_consumption": "按 24h 消耗金额除以单账号 7d 限额折算出的账号消耗速度。",
    }


def _build_operational_facts(
    *,
    capacity: dict[str, Any],
    probe: dict[str, Any],
    event_windows: dict[str, Any],
    recent_decisions: list[dict[str, Any]],
    long_term_memory: dict[str, Any],
) -> dict[str, Any]:
    capacity_facts = _capacity_facts(capacity)
    usage_facts = _usage_facts(capacity)
    burst_facts = _burst_facts(capacity)
    event_facts = _event_facts(event_windows)
    probe_facts = _probe_facts(probe)
    memory_facts = _memory_facts(recent_decisions, long_term_memory)
    data_quality_facts = _data_quality_facts(capacity, probe, event_windows)
    return {
        "capacity_facts": capacity_facts,
        "usage_facts": usage_facts,
        "burst_facts": burst_facts,
        "event_facts": event_facts,
        "probe_facts": probe_facts,
        "memory_facts": memory_facts,
        "data_quality_facts": data_quality_facts,
        "risk_signals": _risk_signals(capacity, probe, event_windows),
        "data_gaps": _operational_data_gaps(capacity, probe, event_windows),
    }


def _capacity_facts(capacity: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    if not capacity:
        return facts
    active = capacity.get("active_account_count")
    reserve = capacity.get("reserve_account_count")
    if active is not None or reserve is not None:
        facts.append(f"当前使用池 active 账号数为 {active if active is not None else '未知'}，备用池账号数为 {reserve if reserve is not None else '未知'}。")
    dynamic_5h = capacity.get("dynamic_5h_total_usd")
    if dynamic_5h is not None:
        facts.append(f"当前 5h 动态总容量约为 {dynamic_5h} 美元。")
    dynamic_5h_available = capacity.get("dynamic_5h_available_usd")
    actual_5h_available = capacity.get("actual_5h_available_usd")
    if dynamic_5h_available is not None or actual_5h_available is not None:
        facts.append(
            f"当前 5h 动态可用额度约为 {dynamic_5h_available if dynamic_5h_available is not None else '未知'} 美元，实际可用额度约为 {actual_5h_available if actual_5h_available is not None else '未知'} 美元。"
        )
    dynamic_7d = capacity.get("dynamic_7d_total_usd")
    if dynamic_7d is not None:
        facts.append(f"当前 7d 动态总容量约为 {dynamic_7d} 美元。")
    dynamic_7d_available = capacity.get("dynamic_7d_available_usd")
    actual_7d_available = capacity.get("actual_7d_available_usd")
    if dynamic_7d_available is not None or actual_7d_available is not None:
        facts.append(
            f"当前 7d 动态可用额度约为 {dynamic_7d_available if dynamic_7d_available is not None else '未知'} 美元，实际可用额度约为 {actual_7d_available if actual_7d_available is not None else '未知'} 美元。"
        )
    recent_peak = _number_or_none(capacity.get("recent_day_five_hour_peak_multiple"))
    if recent_peak is not None:
        if recent_peak < 1:
            facts.append(f"最近一天 5h 峰值容量倍数为 {recent_peak}x，表示当前池子低于最近峰值需求。")
        else:
            facts.append(f"最近一天 5h 峰值容量倍数为 {recent_peak}x，表示当前池子可覆盖最近峰值需求。")
    return facts


def _usage_facts(capacity: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    recent_24h_cost = capacity.get("recent_24h_cost")
    if recent_24h_cost is not None:
        facts.append(f"最近 24h 消耗约为 {recent_24h_cost} 美元。")
    seven_day_24h_peak_cost = capacity.get("seven_day_24h_peak_cost")
    if seven_day_24h_peak_cost is not None:
        facts.append(f"最近 7d 内最高 24h 消耗约为 {seven_day_24h_peak_cost} 美元。")
    recent_24h_runway_days = capacity.get("recent_24h_runway_days") or capacity.get("current_speed_days")
    if recent_24h_runway_days is not None:
        facts.append(f"按最近 24h 消耗速度估算，当前容量可支撑约 {recent_24h_runway_days} 天。")
    peak_runway_hours = capacity.get("seven_day_highest_24h_runway_hours")
    if peak_runway_hours is not None:
        facts.append(f"按最近 7d 最高 24h 消耗压力估算，当前容量可支撑约 {peak_runway_hours} 小时。")
    recent_consumption = capacity.get("recent_24h_estimated_account_consumption")
    if recent_consumption is not None:
        facts.append(f"按最近 24h 消耗折算，约消耗 {recent_consumption} 个单账号 7d 额度。")
    peak_consumption = capacity.get("seven_day_highest_24h_estimated_account_consumption")
    if peak_consumption is not None:
        facts.append(f"按最近 7d 最高 24h 消耗折算，约消耗 {peak_consumption} 个单账号 7d 额度。")
    five_hour_remaining = capacity.get("five_hour_remaining_usd")
    if five_hour_remaining is not None:
        facts.append(f"5h 剩余额度约为 {five_hour_remaining} 美元。")
    seven_day_remaining = capacity.get("seven_day_remaining_usd")
    if seven_day_remaining is not None:
        facts.append(f"7d 剩余额度约为 {seven_day_remaining} 美元。")
    return facts


def _burst_facts(capacity: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    burst_multiple = _number_or_none(capacity.get("burst_1h_estimated_5h_multiple") or capacity.get("burst_1h_five_hour_multiple"))
    if burst_multiple is not None:
        if burst_multiple < 1:
            facts.append(f"突发 1h 预估 5h 容量倍数为 {burst_multiple}x，表示按当前小时速度折算后容量不足。")
        else:
            facts.append(f"突发 1h 预估 5h 容量倍数为 {burst_multiple}x，表示按当前小时速度折算后短时压力可覆盖。")
    trend_label = capacity.get("burst_1h_trend_label") or capacity.get("burst_1h_trend")
    trend_strength = capacity.get("burst_1h_trend_strength_label") or capacity.get("burst_1h_trend_strength")
    change_percent = capacity.get("burst_1h_trend_change_percent")
    if trend_label or trend_strength or change_percent is not None:
        facts.append(f"最近 1h 突发趋势为 {trend_label or '未知'}，强度 {trend_strength or '未知'}，变化约 {change_percent if change_percent is not None else '未知'}%。")
    return facts


def _event_facts(event_windows: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    summary_24h = event_windows.get("summary_24h") if isinstance(event_windows.get("summary_24h"), dict) else {}
    total = summary_24h.get("total_events")
    if total:
        facts.append(f"最近 24h 事件流记录到 {total} 条相关事件。")
    for text in summary_24h.get("interpretation") if isinstance(summary_24h.get("interpretation"), list) else []:
        if isinstance(text, str) and text.strip():
            facts.append(text.strip())
    return facts[:10]


def _probe_facts(probe: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    if not probe:
        return facts
    detected_401_24h = probe.get("detected_401_24h")
    if detected_401_24h is not None:
        facts.append(f"账号探测最近 24h 检测到 {detected_401_24h} 个 401。")
    largest_cluster = probe.get("largest_401_cluster_24h")
    if isinstance(largest_cluster, dict) and largest_cluster.get("count"):
        facts.append(f"账号探测显示最近 24h 最大 401 聚类包含 {largest_cluster.get('count')} 个事件。")
    if probe.get("concentrated_401_burst_24h"):
        facts.append("账号探测显示最近 24h 存在集中 401 爆发。")
    duplicate_count = probe.get("duplicate_email_alert_count")
    if duplicate_count:
        facts.append(f"账号探测存在 {duplicate_count} 条重复邮箱告警。")
    return facts


def _memory_facts(recent_decisions: list[dict[str, Any]], long_term_memory: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    if recent_decisions:
        last = recent_decisions[0]
        facts.append(
            f"最近一次 Agent 决策风险等级为 {last.get('severity') or '未知'}，建议补号数为 {last.get('suggested_add_count') if last.get('suggested_add_count') is not None else '未知'}。"
        )
    if any(bool(value) for value in long_term_memory.values()):
        facts.append("存在当前池相关长期记忆摘要，可作为经验参考。")
    return facts


def _data_quality_facts(capacity: dict[str, Any], probe: dict[str, Any], event_windows: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    if capacity.get("cache_fresh") is not None:
        facts.append(f"容量缓存新鲜度标记为 {capacity.get('cache_fresh')}，最近刷新时间为 {capacity.get('last_refreshed_at') or '未知'}。")
    if probe.get("probe_fresh") is not None:
        facts.append(f"探测数据新鲜度标记为 {probe.get('probe_fresh')}，最近探测时间为 {probe.get('last_probe_at') or '未知'}。")
    event_quality = event_windows.get("data_quality") if isinstance(event_windows.get("data_quality"), dict) else {}
    if event_quality:
        facts.append(f"事件窗口数据可用性为 {event_quality.get('available')}。")
    return facts


def _risk_signals(capacity: dict[str, Any], probe: dict[str, Any], event_windows: dict[str, Any]) -> list[dict[str, Any]]:
    signals: list[dict[str, Any]] = []
    recent_peak = _number_or_none(capacity.get("recent_day_five_hour_peak_multiple"))
    if recent_peak is not None and recent_peak < 1:
        signals.append({"signal": "recent_peak_capacity_gap", "level": "high", "evidence": f"recent_day_five_hour_peak_multiple={recent_peak}"})
    reserve = _number_or_none(capacity.get("reserve_account_count"))
    if reserve is not None and reserve <= 0:
        signals.append({"signal": "no_reserve_pool", "level": "medium", "evidence": f"reserve_account_count={reserve}"})
    detected_401_24h = _number_or_none(probe.get("detected_401_24h"))
    if detected_401_24h is not None and detected_401_24h > 0:
        level = "high" if detected_401_24h >= 10 else "medium"
        signals.append({"signal": "recent_401_detected", "level": level, "evidence": f"detected_401_24h={detected_401_24h}"})
    summary_24h = event_windows.get("summary_24h") if isinstance(event_windows.get("summary_24h"), dict) else {}
    clusters = summary_24h.get("clusters") if isinstance(summary_24h.get("clusters"), list) else []
    if clusters:
        signals.append({"signal": "event_cluster_detected", "level": "medium", "evidence": clusters[:3]})
    return signals


def _operational_data_gaps(capacity: dict[str, Any], probe: dict[str, Any], event_windows: dict[str, Any]) -> list[str]:
    gaps: list[str] = []
    if not capacity:
        gaps.append("缺少账号池容量数据。")
    if not probe:
        gaps.append("缺少账号探测摘要。")
    event_quality = event_windows.get("data_quality") if isinstance(event_windows.get("data_quality"), dict) else {}
    if not event_quality.get("available"):
        gaps.append("缺少事件流窗口数据。")
    elif event_quality.get("multi_window_summaries") == "summary_24h_available_1h_6h_7d_unavailable":
        gaps.append("当前事件窗口降级为 24h 摘要，1h、6h、7d 聚合摘要暂不可用。")
    return gaps


def _empty_long_term_memory() -> dict[str, list[Any]]:
    return {
        "pool_daily_summaries": [],
        "pool_weekly_summaries": [],
        "decision_reviews": [],
        "operator_feedback_summaries": [],
        "survival_patterns": [],
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
                "summary": _recent_decision_summary_view(item.get("summary")),
                "suggested_add_count": decision.get("suggested_add_count"),
                "confidence": decision.get("confidence"),
                "requires_human_confirm": item.get("requires_human_confirm"),
                "decision_mode": item.get("decision_mode") or decision.get("mode"),
            }
        )
    return serialize_doc(decisions)


def _recent_decision_summary_view(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    legacy_markers = (
        "target_active",
        "min_reserve",
        "目标活跃",
        "目标 active",
        "最小备用",
        "最低备用",
        "规则引擎",
        "按规则",
    )
    if any(marker in text for marker in legacy_markers):
        return None
    return text[:500]


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
        "stage": "stage_4_context_pack_v2_data_memory_design",
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


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _from_capacity_summary(capacity: dict[str, Any], *keys: str) -> float | None:
    summary = capacity.get("capacity_summary") if isinstance(capacity.get("capacity_summary"), dict) else {}
    for key in keys:
        value = _number_or_none(capacity.get(key))
        if value is not None:
            return value
        value = _number_or_none(summary.get(key))
        if value is not None:
            return value
    return None


def _single_account_5h_limit_usd(capacity: dict[str, Any]) -> float | None:
    summary = capacity.get("capacity_summary") if isinstance(capacity.get("capacity_summary"), dict) else {}
    configured = _number_or_none(
        summary.get("single_account_5h_limit_usd")
        or summary.get("account_5h_limit_usd")
        or summary.get("five_hour_limit_per_account_usd")
    )
    if configured is not None:
        return configured
    pool = capacity.get("pool") if isinstance(capacity.get("pool"), dict) else {}
    if str(pool.get("account_type") or "").strip().lower() == "pro":
        return 360.0
    return None


def _single_account_7d_limit_usd(capacity: dict[str, Any]) -> float | None:
    summary = capacity.get("capacity_summary") if isinstance(capacity.get("capacity_summary"), dict) else {}
    configured = _number_or_none(
        summary.get("single_account_7d_limit_usd")
        or summary.get("account_7d_limit_usd")
        or summary.get("seven_day_limit_per_account_usd")
    )
    if configured is not None:
        return configured
    pool = capacity.get("pool") if isinstance(capacity.get("pool"), dict) else {}
    if str(pool.get("account_type") or "").strip().lower() == "pro":
        return 2100.0
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
