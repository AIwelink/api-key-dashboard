from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.api_pools.status_preferences import get_api_pool_status_preferences
from app.modules.sub2api.cache import get_cache_meta
from app.modules.sub2api.capacity_risk import MAX_SAMPLE_AGE
from app.utils import now_utc, serialize_doc

AGENT_POOL_DEFAULTS = {
    "status": "active",
}


async def list_agent_pools(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """Return live sub2api groups as Agent analyzable pools.

    Agent reads cached sub2api groups inside the active client-site scope. This
    does not sync api_pools and does not refresh sub2api.
    """
    site_id = await _agent_default_site_id(db)
    site_scope = await list_agent_site_scope(db)
    analyzable_sites = {
        str(item.get("site_id")): item
        for item in site_scope.get("patrol_sites", [])
        if isinstance(item, dict) and item.get("site_id")
    }
    items = []
    seen: set[str] = set()
    query: dict[str, Any] = {}
    if site_scope.get("sites"):
        query["site_id"] = {"$in": sorted(analyzable_sites)}
    cursor = db.sub2api_groups_cache.find(query).sort([("site_id", 1), ("group_id", 1)])
    async for group_doc in cursor:
        pool = _pool_from_group_doc(group_doc)
        if not pool:
            continue
        pool_site = analyzable_sites.get(str(pool.get("site_id")))
        if pool_site:
            pool["client_site"] = pool_site
        key = str(pool["id"])
        if key in seen:
            continue
        seen.add(key)
        items.append(pool)
    cache_meta = await get_cache_meta(db, site_id) if site_id else {}
    return {
        "items": [serialize_doc(item) for item in items],
        "total": len(items),
        "site_id": site_id,
        "cache_meta": cache_meta,
        "site_scope": serialize_doc(site_scope),
    }


async def list_agent_site_scope(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    """Merge client_sites and operational sub2api sites into one read-only Agent scope."""

    sites: dict[str, dict[str, Any]] = {}
    async for item in db.client_sites.find({"status": "active"}).sort([("created_at", 1), ("_id", 1)]):
        if str(item.get("status") or "active").strip().lower() != "active":
            continue
        site_id = str(item.get("_id") or item.get("id") or "").strip()
        if not site_id:
            continue
        sites[site_id] = {
            "site_id": site_id,
            "name": str(item.get("name") or site_id),
            "client_type": str(item.get("client_type") or "sub2api").strip().lower(),
            "status": "active",
            "source": "client_sites",
            "patrol_eligible": str(item.get("client_type") or "sub2api").strip().lower() == "sub2api",
            "usage_attribution_eligible": True,
        }
    async for item in db.sub2api_sites.find({"status": {"$ne": "deleted"}}).sort([("created_at", 1), ("_id", 1)]):
        site_id = str(item.get("_id") or item.get("id") or "").strip()
        if not site_id or str(item.get("status") or "active").strip().lower() == "disabled":
            continue
        site_type = str(item.get("site_type") or "sub2api").strip().lower()
        if site_type != "sub2api":
            continue
        current = sites.get(site_id, {})
        sites[site_id] = {
            "site_id": site_id,
            "name": str(current.get("name") or item.get("name") or site_id),
            "client_type": "sub2api",
            "status": "active",
            "source": "client_sites+sub2api_sites" if current else "sub2api_sites",
            "patrol_eligible": True,
            "usage_attribution_eligible": True,
        }
    ordered = sorted(sites.values(), key=lambda item: (str(item.get("name") or ""), str(item.get("site_id") or "")))
    return {
        "schema_version": "agent_site_scope.v1",
        "sites": ordered,
        "patrol_sites": [item for item in ordered if item.get("patrol_eligible") is True],
        "usage_attribution_sites": [item for item in ordered if item.get("usage_attribution_eligible") is True],
        "source": "client_sites+sub2api_sites",
    }


async def read_pool_capacity(db: AsyncIOMotorDatabase, pool_id: str) -> dict[str, Any]:
    pool, group_doc = await _resolve_agent_pool(db, pool_id)
    site_id = str(pool.get("site_id") or "default")
    group_id = _int_or_none(pool.get("active_group_id"))
    if group_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Agent pool active_group_id is required")

    if group_doc is None:
        group_doc = await db.sub2api_groups_cache.find_one({"site_id": site_id, "group_id": group_id})
    group = group_doc.get("group") if group_doc and isinstance(group_doc.get("group"), dict) else {}
    capacity_summary = {}
    if group_doc:
        raw_summary = group_doc.get("capacity_summary")
        if isinstance(raw_summary, dict):
            capacity_summary = raw_summary
    if not capacity_summary and isinstance(group.get("capacity_summary"), dict):
        capacity_summary = group["capacity_summary"]

    cache_meta = await get_cache_meta(db, site_id)
    reserve_count = await _count_local_reserve_accounts(db, pool=pool, pool_id=pool_id, site_id=site_id, group_id=group_id)
    active_count = _number_or_none(capacity_summary.get("active_available_accounts"))
    if active_count is None:
        active_count = await _count_cached_group_accounts(db, site_id=site_id, group_id=group_id)

    return serialize_doc(
        {
            "pool": pool,
            "site_id": site_id,
            "group_id": group_id,
            "group": group,
            "cache_meta": cache_meta,
            "capacity_summary": capacity_summary,
            "data_source": "sub2api_groups_cache",
            "refresh_behavior": "read_existing_cache_only",
            "active_account_count": int(active_count or 0),
            "reserve_account_count": int(_number_or_none(capacity_summary.get("reserve_available_accounts")) or reserve_count),
            "local_reserve_account_count": reserve_count,
            "total_account_count": int(_number_or_none(capacity_summary.get("total_accounts")) or _number_or_none(group.get("account_count")) or 0),
            "available_accounts": int(_number_or_none(capacity_summary.get("available_accounts")) or 0),
            "available_5h_accounts": int(_number_or_none(capacity_summary.get("available_5h_accounts")) or 0),
            "current_speed_days": _number_or_none(capacity_summary.get("current_speed_days")),
            "recent_24h_cost": _number_or_none(capacity_summary.get("recent_24h_cost")),
            "seven_day_24h_peak_cost": _number_or_none(capacity_summary.get("seven_day_24h_peak_cost")),
            "estimated_recent_24h_consumed_accounts": _number_or_none(
                capacity_summary.get("estimated_recent_24h_consumed_accounts") or capacity_summary.get("estimated_24h_consumed_accounts")
            ),
            "estimated_seven_day_peak_24h_consumed_accounts": _number_or_none(capacity_summary.get("estimated_seven_day_peak_24h_consumed_accounts")),
            "seven_day_peak_speed_days": _number_or_none(capacity_summary.get("seven_day_peak_speed_days")),
            "recent_day_five_hour_peak_speed_days": _number_or_none(capacity_summary.get("recent_day_five_hour_peak_speed_days")),
            "seven_day_five_hour_peak_speed_days": _number_or_none(capacity_summary.get("seven_day_five_hour_peak_speed_days")),
            "recent_day_five_hour_peak_multiple": _number_or_none(capacity_summary.get("recent_day_five_hour_peak_multiple")),
            "seven_day_five_hour_peak_multiple": _number_or_none(capacity_summary.get("seven_day_five_hour_peak_multiple") or capacity_summary.get("five_hour_peak_multiple")),
            "burst_1h_five_hour_multiple": _number_or_none(capacity_summary.get("burst_1h_five_hour_multiple")),
            "active_burst_1h_five_hour_multiple": _number_or_none(capacity_summary.get("active_burst_1h_five_hour_multiple")),
            "burst_1h_observed_cost": _number_or_none(capacity_summary.get("burst_1h_observed_cost")),
            "burst_1h_elapsed_minutes": _number_or_none(capacity_summary.get("burst_1h_elapsed_minutes")),
            "burst_1h_cost": _number_or_none(capacity_summary.get("burst_1h_cost")),
            "burst_1h_five_hour_estimated_cost": _number_or_none(capacity_summary.get("burst_1h_five_hour_estimated_cost")),
            "burst_1h_trend": capacity_summary.get("burst_1h_trend"),
            "burst_1h_trend_label": capacity_summary.get("burst_1h_trend_label"),
            "burst_1h_trend_strength": capacity_summary.get("burst_1h_trend_strength"),
            "burst_1h_trend_strength_label": capacity_summary.get("burst_1h_trend_strength_label"),
            "burst_1h_trend_change_percent": _number_or_none(capacity_summary.get("burst_1h_trend_change_percent")),
            "burst_1h_trend_recent_avg_cost": _number_or_none(capacity_summary.get("burst_1h_trend_recent_avg_cost")),
            "burst_1h_trend_baseline_avg_cost": _number_or_none(capacity_summary.get("burst_1h_trend_baseline_avg_cost")),
            "burst_1h_trend_recent_hours": _number_or_none(capacity_summary.get("burst_1h_trend_recent_hours")),
            "burst_1h_trend_baseline_hours": _number_or_none(capacity_summary.get("burst_1h_trend_baseline_hours")),
            "five_hour_remaining_usd": _number_or_none(
                capacity_summary.get("dynamic_five_hour_remaining_estimated_usd")
                or capacity_summary.get("five_hour_remaining_estimated_usd")
                or capacity_summary.get("five_hour_actual_remaining_usd")
            ),
            "seven_day_remaining_usd": _number_or_none(
                capacity_summary.get("seven_day_remaining_estimated_usd")
                or capacity_summary.get("seven_day_actual_remaining_usd")
                or capacity_summary.get("seven_day_remaining_usd")
            ),
            "health_status": capacity_summary.get("health_status"),
            "health_label": capacity_summary.get("health_label"),
            "cache_fresh": bool(cache_meta.get("last_refreshed_at")),
            "last_refreshed_at": cache_meta.get("last_refreshed_at"),
            "capacity_calculated_at": group_doc.get("capacity_calculated_at") if group_doc else None,
        }
    )


def build_agent_capacity_status(capacity: dict[str, Any]) -> dict[str, Any]:
    """Normalize the main-system capacity summary into a compact LLM view."""

    if not isinstance(capacity, dict) or not capacity:
        return {}
    summary = _capacity_summary(capacity)
    pool = capacity.get("pool") if isinstance(capacity.get("pool"), dict) else {}
    account_type = _capacity_account_type(capacity)
    five_hour_limit = capacity_account_limit_usd(capacity, window="five_hour")
    seven_day_limit = capacity_account_limit_usd(capacity, window="seven_day")
    limits_available = five_hour_limit is not None or seven_day_limit is not None
    configured_limits = summary.get("capacity_limits") if isinstance(summary.get("capacity_limits"), dict) else {}
    configured_account_limits = configured_limits.get(account_type) if isinstance(configured_limits.get(account_type), dict) else {}
    limits_source = "capacity_summary.capacity_limits" if configured_account_limits else "legacy_capacity_fields" if limits_available else None

    sample_freshness = _realtime_sample_freshness(summary)
    return {
        "schema_version": "agent_capacity_status.v2",
        "site_id": capacity.get("site_id") or pool.get("site_id"),
        "group_id": capacity.get("group_id") or pool.get("active_group_id"),
        "account_type": account_type,
        "account_limits_usd": {
            "five_hour": five_hour_limit,
            "seven_day": seven_day_limit,
            "source": limits_source,
        },
        "accounts": {
            "active": _first_number(capacity.get("active_account_count"), summary.get("active_available_accounts")),
            "reserve": _first_number(capacity.get("reserve_account_count"), summary.get("reserve_available_accounts")),
            "available": _first_number(capacity.get("available_accounts"), summary.get("available_accounts")),
            "available_5h": _first_number(capacity.get("available_5h_accounts"), summary.get("available_5h_accounts")),
            "total": _first_number(capacity.get("total_account_count"), summary.get("total_accounts")),
        },
        "pool_conditions": {
            "normal_accounts": _number_or_none(summary.get("pool_normal_accounts")),
            "active_normal_accounts": _number_or_none(summary.get("pool_active_normal_accounts")),
            "five_hour_rate_limited_accounts": _number_or_none(summary.get("pool_five_hour_rate_limited_accounts")),
            "seven_day_rate_limited_accounts": _number_or_none(summary.get("pool_seven_day_rate_limited_accounts")),
            "abnormal_accounts": _number_or_none(summary.get("pool_abnormal_accounts")),
            "excluded_bug_team_accounts": _number_or_none(summary.get("pool_excluded_bug_team_accounts")),
            "duplicate_email_accounts_collapsed": _number_or_none(summary.get("capacity_duplicate_email_accounts")),
        },
        "five_hour": {
            "dynamic_capacity_usd": _summary_number(capacity, "dynamic_five_hour_capacity_usd", "five_hour_capacity_usd"),
            "dynamic_used_usd": _summary_number(capacity, "dynamic_five_hour_used_estimated_usd", "five_hour_used_estimated_usd"),
            "dynamic_available_usd": _summary_number(capacity, "dynamic_five_hour_remaining_estimated_usd", "five_hour_remaining_estimated_usd"),
            "actual_used_usd": _summary_number(capacity, "five_hour_actual_used_usd"),
            "actual_available_usd": _summary_number(capacity, "five_hour_actual_remaining_usd"),
            "available_percent": _summary_number(capacity, "available_5h_percent"),
            "active_available_percent": _summary_number(capacity, "active_available_5h_percent"),
            "actual_available_percent": _summary_number(capacity, "actual_available_5h_percent"),
            "active_actual_available_percent": _summary_number(capacity, "active_actual_available_5h_percent"),
        },
        "seven_day": {
            "capacity_usd": _summary_number(capacity, "seven_day_capacity_usd"),
            "dynamic_used_usd": _summary_number(capacity, "seven_day_used_estimated_usd"),
            "dynamic_available_usd": _summary_number(capacity, "seven_day_remaining_estimated_usd"),
            "actual_used_usd": _summary_number(capacity, "seven_day_actual_used_usd"),
            "actual_available_usd": _summary_number(capacity, "seven_day_actual_remaining_usd"),
            "available_percent": _summary_number(capacity, "available_7d_percent"),
            "actual_available_percent": _summary_number(capacity, "actual_available_7d_percent"),
        },
        "demand": {
            "recent_5h_cost_usd": _summary_number(capacity, "recent_5h_cost"),
            "recent_24h_cost_usd": _summary_number(capacity, "recent_24h_cost"),
            "seven_day_24h_peak_cost_usd": _summary_number(capacity, "seven_day_24h_peak_cost"),
            "burst_1h_observed_cost_usd": _summary_number(capacity, "burst_1h_observed_cost"),
            "burst_1h_estimated_5h_cost_usd": _summary_number(capacity, "burst_1h_five_hour_estimated_cost"),
        },
        "coverage": {
            "recent_day_5h_peak_multiple": _summary_number(capacity, "recent_day_five_hour_peak_multiple"),
            "seven_day_5h_peak_multiple": _summary_number(capacity, "seven_day_five_hour_peak_multiple", "five_hour_peak_multiple"),
            "burst_1h_estimated_5h_multiple": _summary_number(capacity, "burst_1h_five_hour_multiple"),
            "active_burst_1h_estimated_5h_multiple": _summary_number(capacity, "active_burst_1h_five_hour_multiple"),
            "recent_24h_runway_days": _summary_number(capacity, "current_speed_days"),
            "seven_day_peak_runway_days": _summary_number(capacity, "seven_day_peak_speed_days"),
        },
        "health": {
            "status": capacity.get("health_status") or summary.get("health_status"),
            "label": capacity.get("health_label") or summary.get("health_label"),
            "reason": summary.get("health_reason"),
        },
        "realtime_risk": {
            "ready": summary.get("realtime_risk_ready") is True,
            "pressure_stage": summary.get("pressure_stage"),
            "pressure_stage_label": summary.get("pressure_stage_label"),
            "inventory_risk": summary.get("inventory_risk") is True,
            "demand_ratio": _number_or_none(summary.get("demand_ratio")),
            "tpm_momentum": _number_or_none(summary.get("tpm_momentum")),
            "pressure_tpm": _number_or_none(summary.get("pressure_tpm")),
            "pressure_rpm": _number_or_none(summary.get("pressure_rpm")),
            "burn_usd_per_hour": _number_or_none(summary.get("burn_usd_per_hour")),
            "actual_runway_hours": _number_or_none(summary.get("actual_runway_hours")),
            "dynamic_runway_hours": _number_or_none(summary.get("dynamic_runway_hours")),
            "target_runway_hours": _number_or_none(summary.get("target_runway_hours")),
            "actual_target_hours": _number_or_none(summary.get("actual_target_hours")),
            "sample": sample_freshness,
        },
        "freshness": {
            "cache_fresh": capacity.get("cache_fresh"),
            "last_refreshed_at": capacity.get("last_refreshed_at"),
            "capacity_calculated_at": capacity.get("capacity_calculated_at") or summary.get("calculated_at"),
            "realtime_sample": sample_freshness,
        },
    }


def build_agent_concurrency_status(capacity: dict[str, Any]) -> dict[str, Any]:
    """Return the concurrency fields that are useful for Agent decisions."""

    if not isinstance(capacity, dict) or not capacity:
        return {}
    values = {
        "actual_in_use": _summary_number(capacity, "concurrency_actual_in_use"),
        "actual_available": _summary_number(capacity, "concurrency_actual_available"),
        "safe_available": _summary_number(capacity, "concurrency_safe_available"),
        "near_limit_available": _summary_number(capacity, "concurrency_near_limit_available"),
        "temporarily_unavailable": _summary_number(capacity, "concurrency_temporarily_unavailable"),
        "total_capacity": _summary_number(capacity, "concurrency_total_capacity"),
        "used_percent": _summary_number(capacity, "concurrency_used_percent"),
        "available_percent": _summary_number(capacity, "concurrency_available_percent"),
    }
    accounts = {
        "eligible": _summary_number(capacity, "concurrency_eligible_accounts"),
        "available": _summary_number(capacity, "concurrency_available_accounts"),
        "safe": _summary_number(capacity, "concurrency_safe_accounts"),
        "near_limit": _summary_number(capacity, "concurrency_near_limit_accounts"),
        "temporarily_unavailable": _summary_number(capacity, "concurrency_temporarily_unavailable_accounts"),
        "five_hour_limited": _summary_number(capacity, "concurrency_five_hour_limited_accounts"),
        "short_seven_day_limited": _summary_number(capacity, "concurrency_short_seven_day_limited_accounts"),
        "long_seven_day_limited": _summary_number(capacity, "concurrency_long_seven_day_limited_accounts"),
        "other_unavailable": _summary_number(capacity, "concurrency_other_unavailable_accounts"),
    }
    realtime = {
        "estimated": _summary_number(capacity, "estimated_concurrency"),
        "ema_5": _summary_number(capacity, "concurrency_ema_5"),
        "p90_1h": _summary_number(capacity, "concurrency_p90_1h"),
        "coverage": _summary_number(capacity, "concurrency_coverage"),
        "target_coverage": _summary_number(capacity, "concurrency_target_coverage"),
    }
    return {
        "schema_version": "agent_concurrency_status.v2",
        "available": any(value is not None for value in (*values.values(), *accounts.values(), *realtime.values())),
        **values,
        **realtime,
        "sample": _realtime_sample_freshness(_capacity_summary(capacity)),
        "accounts": accounts,
    }


def build_system_capacity_assessment(capacity: dict[str, Any]) -> dict[str, Any]:
    """Expose the main-system refill calculation as advisory evidence only."""

    if not isinstance(capacity, dict) or not capacity:
        return {}
    summary = _capacity_summary(capacity)
    configured_limits = summary.get("capacity_limits") if isinstance(summary.get("capacity_limits"), dict) else {}
    raw_options = summary.get("recommended_refill_options")
    options: dict[str, dict[str, Any]] = {}
    if isinstance(raw_options, dict):
        for raw_account_type, raw_option in list(raw_options.items())[:12]:
            if not isinstance(raw_option, dict):
                continue
            account_type = str(raw_option.get("account_type") or raw_account_type or "").strip().lower()
            if not account_type:
                continue
            raw_limits = configured_limits.get(account_type) if isinstance(configured_limits.get(account_type), dict) else {}
            five_hour_limit = _number_or_none(raw_limits.get("five_hour_usd"))
            seven_day_limit = _number_or_none(raw_limits.get("seven_day_usd"))
            options[account_type] = {
                "account_type": account_type,
                "quota_refill_accounts": _int_or_none(raw_option.get("quota_refill_accounts")),
                "concurrency_refill_accounts": _int_or_none(raw_option.get("concurrency_refill_accounts")),
                "recommended_refill_accounts": _int_or_none(raw_option.get("recommended_refill_accounts")),
                "limits_usd": {
                    "five_hour": five_hour_limit,
                    "seven_day": seven_day_limit,
                    "source": "capacity_summary.capacity_limits",
                },
                "quota_profile": _quota_profile(
                    five_hour_limit=five_hour_limit,
                    seven_day_limit=seven_day_limit,
                ),
            }
    return {
        "schema_version": "system_capacity_assessment.v1",
        "source": "main_system_single_pool_realtime",
        "advisory_only": True,
        "ready": summary.get("realtime_risk_ready") is True,
        "replenishment_required": summary.get("replenishment_required") is True,
        "primary_account_type": _capacity_account_type(capacity),
        "recommended_refill_accounts": _int_or_none(summary.get("recommended_refill_accounts")),
        "calculation_components": {
            "quota_refill_accounts": _int_or_none(summary.get("quota_refill_accounts")),
            "concurrency_refill_accounts": _int_or_none(summary.get("concurrency_refill_accounts")),
        },
        "account_type_options": options,
        "evidence": {
            "pressure_stage": summary.get("pressure_stage"),
            "inventory_risk": summary.get("inventory_risk") is True,
            "actual_runway_hours": _number_or_none(summary.get("actual_runway_hours")),
            "dynamic_runway_hours": _number_or_none(summary.get("dynamic_runway_hours")),
            "concurrency_coverage": _number_or_none(summary.get("concurrency_coverage")),
            "latest_sampled_at": summary.get("latest_sampled_at"),
        },
        "decision_boundary": "evidence_only_llm_keeps_final_decision",
    }


def compact_agent_pool_capacity(capacity: dict[str, Any]) -> dict[str, Any]:
    """Build the capability response without exposing the raw capacity payload."""

    pool = capacity.get("pool") if isinstance(capacity.get("pool"), dict) else {}
    return {
        "pool": {
            "pool_id": pool.get("id"),
            "site_id": capacity.get("site_id") or pool.get("site_id"),
            "group_id": capacity.get("group_id") or pool.get("active_group_id"),
            "name": pool.get("name"),
            "account_type": _capacity_account_type(capacity),
        },
        "capacity_status": build_agent_capacity_status(capacity),
        "concurrency_status": build_agent_concurrency_status(capacity),
        "system_capacity_assessment": build_system_capacity_assessment(capacity),
        "data_quality": {
            "data_source": capacity.get("data_source"),
            "refresh_behavior": capacity.get("refresh_behavior"),
            "cache_fresh": capacity.get("cache_fresh"),
            "last_refreshed_at": capacity.get("last_refreshed_at"),
        },
    }


def capacity_account_limit_usd(capacity: dict[str, Any], *, window: str) -> float | None:
    """Read the effective site/account-type limit without hard-coded plan values."""

    summary = _capacity_summary(capacity)
    account_type = _capacity_account_type(capacity)
    limits = summary.get("capacity_limits") if isinstance(summary.get("capacity_limits"), dict) else {}
    account_limits = limits.get(account_type) if isinstance(limits.get(account_type), dict) else {}
    key = "five_hour_usd" if window == "five_hour" else "seven_day_usd"
    configured = _number_or_none(account_limits.get(key))
    if configured is not None:
        return configured

    legacy_keys = (
        ("single_account_5h_limit_usd", "account_5h_limit_usd", "five_hour_limit_per_account_usd")
        if window == "five_hour"
        else ("single_account_7d_limit_usd", "account_7d_limit_usd", "seven_day_limit_per_account_usd")
    )
    return _summary_number(capacity, *legacy_keys)


async def _resolve_agent_pool(db: AsyncIOMotorDatabase, pool_id: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    parsed = _parse_live_pool_id(pool_id)
    if parsed is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent pool not found")

    site_id, group_id = parsed
    group_doc = await db.sub2api_groups_cache.find_one({"site_id": site_id, "group_id": group_id})
    if group_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent pool not found")
    pool = _pool_from_group_doc(group_doc)
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent pool not found")
    return pool, group_doc


def _pool_from_group_doc(group_doc: dict[str, Any]) -> dict[str, Any] | None:
    group_id = _int_or_none(group_doc.get("group_id"))
    if group_id is None:
        return None
    site_id = str(group_doc.get("site_id") or "").strip() or "default"
    group = group_doc.get("group") if isinstance(group_doc.get("group"), dict) else {}
    capacity_summary = group_doc.get("capacity_summary") if isinstance(group_doc.get("capacity_summary"), dict) else {}
    return {
        **AGENT_POOL_DEFAULTS,
        "id": _live_pool_id(site_id, group_id),
        "name": str(group.get("name") or f"{site_id} group #{group_id}"),
        "account_type": _infer_account_type(group, capacity_summary),
        "site_id": site_id,
        "active_group_id": group_id,
        "verification_group_id": None,
        "source": "sub2api_groups_cache",
        "remote_status": group.get("status"),
        "remote_account_count": group.get("account_count"),
        "remote_active_account_count": group.get("active_account_count"),
        "remote_rate_limited_account_count": group.get("rate_limited_account_count"),
        "updated_at": group_doc.get("fetched_at"),
    }


def _pool_from_cached_group(site_id: str, group: dict[str, Any]) -> dict[str, Any] | None:
    group_id = _int_or_none(group.get("id"))
    if group_id is None:
        return None
    capacity_summary = group.get("capacity_summary") if isinstance(group.get("capacity_summary"), dict) else {}
    return {
        **AGENT_POOL_DEFAULTS,
        "id": _live_pool_id(site_id, group_id),
        "name": str(group.get("name") or f"{site_id} group #{group_id}"),
        "account_type": _infer_account_type(group, capacity_summary),
        "site_id": site_id,
        "active_group_id": group_id,
        "verification_group_id": None,
        "source": "sub2api_cached_groups_endpoint",
        "remote_status": group.get("status"),
        "remote_account_count": group.get("account_count"),
        "remote_active_account_count": group.get("active_account_count"),
        "remote_rate_limited_account_count": group.get("rate_limited_account_count"),
        "updated_at": group.get("fetched_at"),
    }


def _live_pool_id(site_id: str, group_id: int) -> str:
    return f"sub2api:{site_id}:{group_id}"


def _parse_live_pool_id(pool_id: str) -> tuple[str, int] | None:
    parts = str(pool_id or "").split(":", 2)
    if len(parts) != 3 or parts[0] != "sub2api":
        return None
    group_id = _int_or_none(parts[2])
    if not parts[1] or group_id is None:
        return None
    return parts[1], group_id


def _infer_account_type(group: dict[str, Any], capacity_summary: dict[str, Any]) -> str:
    candidates = [
        capacity_summary.get("account_type"),
        group.get("account_type"),
        group.get("subscription_type"),
        group.get("plan_type"),
        group.get("name"),
    ]
    joined = " ".join(str(item or "").strip().lower() for item in candidates)
    if "pro" in joined:
        return "pro"
    if "team" in joined:
        return "team"
    if "k12" in joined:
        return "k12"
    if "free" in joined:
        return "free"
    if "plus" in joined:
        return "plus"
    return "plus"


async def _agent_default_site_id(db: AsyncIOMotorDatabase) -> str | None:
    preferences = await get_api_pool_status_preferences(db)
    pinned_site_id = str(preferences.get("pinned_site_id") or "").strip()
    if pinned_site_id and await db.sub2api_groups_cache.find_one({"site_id": pinned_site_id}, {"_id": 1}):
        return pinned_site_id

    latest_meta = await db.sub2api_cache_meta.find_one(
        {"last_refreshed_at": {"$exists": True}},
        sort=[("last_refreshed_at", -1), ("finished_at", -1), ("updated_at", -1)],
    )
    if latest_meta and latest_meta.get("site_id"):
        return str(latest_meta["site_id"])
    if latest_meta and latest_meta.get("_id"):
        return str(latest_meta["_id"])

    latest_group = await db.sub2api_groups_cache.find_one({}, sort=[("fetched_at", -1), ("site_id", 1), ("group_id", 1)])
    if latest_group and latest_group.get("site_id"):
        return str(latest_group["site_id"])
    return None


async def _count_cached_group_accounts(db: AsyncIOMotorDatabase, *, site_id: str, group_id: int) -> int:
    return await db.sub2api_accounts_cache.count_documents({"site_id": site_id, "group_ids": group_id})


async def _count_local_reserve_accounts(
    db: AsyncIOMotorDatabase,
    *,
    pool: dict[str, Any],
    pool_id: str,
    site_id: str,
    group_id: int,
) -> int:
    account_type = str(pool.get("account_type") or "").strip().lower()
    query: dict[str, Any] = {
        "metadata.deleted_at": {"$exists": False},
        "metadata.pool_status": "reserve",
        "$or": [
            {"metadata.pool_id": pool_id},
            {"metadata.api_pool_id": pool_id},
            {"metadata.pool_id": str(group_id)},
            {"metadata.sub2api_group_id": group_id},
        ],
    }
    if site_id:
        query["$and"] = [
            {
                "$or": [
                    {"metadata.sub2api_site_id": site_id},
                    {"metadata.sub2api_site_id": {"$exists": False}},
                    {"metadata.sub2api_site_id": ""},
                    {"metadata.sub2api_site_id": None},
                ]
            }
        ]
    if account_type and account_type != "other":
        query["metadata.account_type"] = account_type
    return await db.accounts.count_documents(query)


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _capacity_summary(capacity: dict[str, Any]) -> dict[str, Any]:
    return capacity.get("capacity_summary") if isinstance(capacity.get("capacity_summary"), dict) else {}


def _capacity_account_type(capacity: dict[str, Any]) -> str:
    summary = _capacity_summary(capacity)
    pool = capacity.get("pool") if isinstance(capacity.get("pool"), dict) else {}
    supported = {"free", "plus", "team", "bug_team", "k12", "pro"}
    summary_type = str(summary.get("account_type") or "").strip().lower()
    if summary_type in supported:
        return summary_type
    pool_type = str(pool.get("account_type") or "").strip().lower()
    return pool_type if pool_type in supported else summary_type or pool_type or "unknown"


def _summary_number(capacity: dict[str, Any], *keys: str) -> float | None:
    summary = _capacity_summary(capacity)
    for key in keys:
        value = _number_or_none(capacity.get(key))
        if value is not None:
            return value
        value = _number_or_none(summary.get(key))
        if value is not None:
            return value
    return None


def _first_number(*values: Any) -> float | None:
    for value in values:
        parsed = _number_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _realtime_sample_freshness(summary: dict[str, Any]) -> dict[str, Any]:
    latest_sampled_at = summary.get("latest_sampled_at")
    sampled_at = _datetime_or_none(latest_sampled_at)
    age_minutes = None
    if sampled_at is not None:
        age_minutes = round(max(0.0, (now_utc() - sampled_at).total_seconds() / 60), 2)
    stale_after_minutes = int(MAX_SAMPLE_AGE.total_seconds() / 60)
    return {
        "sample_count": _int_or_none(summary.get("sample_count")) or 0,
        "concurrency_sample_count": _int_or_none(summary.get("concurrency_sample_count")) or 0,
        "latest_sampled_at": latest_sampled_at,
        "age_minutes": age_minutes,
        "fresh": age_minutes is not None and age_minutes <= stale_after_minutes,
        "stale_after_minutes": stale_after_minutes,
    }


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _quota_profile(*, five_hour_limit: float | None, seven_day_limit: float | None) -> str:
    if five_hour_limit is None and seven_day_limit is None:
        return "unknown"
    if seven_day_limit is not None and (five_hour_limit is None or five_hour_limit >= seven_day_limit):
        return "seven_day_only_or_shared_quota"
    return "five_hour_and_seven_day"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
