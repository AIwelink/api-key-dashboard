from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any

from app.modules.sub2api.hourly_forecast import (
    ForecastInputError,
    ForecastResult,
    apply_current_hour_nowcast,
    forecast_cost_over_window,
    forecast_runway,
)
from app.modules.sub2api.regime_nowcast import (
    detect_demand_regime,
    estimate_direct_cost_per_minute,
    select_nowcast_remaining,
)


MIN_SAMPLE_COUNT = 15
REGIME_NOWCAST_V2_ENABLED = False
MAX_SAMPLE_AGE = timedelta(minutes=3)
ACTUAL_RUNWAY_TARGET_HOURS = 1.0
DYNAMIC_RUNWAY_TARGET_HOURS = 3.0
EXHAUSTED_RUNWAY_HOURS = 0.5
INVENTORY_RISK_RUNWAY_HOURS = 6.0
SAFE_CONCURRENCY_TARGET = 1.2
TOTAL_CONCURRENCY_TARGET = 1 + SAFE_CONCURRENCY_TARGET
FORECAST_RUNWAY_HOURS = 24.0
FORECAST_BURN_WINDOW_HOURS = 3.0

PRESSURE_STAGE_LABELS = {
    "waiting_data": "等待数据",
    "stable": "稳定",
    "transmission": "压力传导",
    "accelerating": "加速上涨",
    "peak_guard": "峰值保底",
    "recovering": "回落观察",
    "inventory_risk": "库存风险",
}

HEALTH_META = {
    "pending": ("等待数据", "muted"),
    "exhausted": ("耗尽", "danger"),
    "danger": ("危险", "danger"),
    "tight": ("需要补号", "warning"),
    "healthy": ("健康", "success"),
    "abundant": ("充裕", "info"),
}


def calculate_capacity_risk(
    *,
    samples: list[dict[str, Any]],
    now: datetime,
    cost_per_token: float | None,
    actual_five_hour_remaining_usd: float,
    dynamic_five_hour_remaining_usd: float,
    actual_seven_day_remaining_usd: float,
    dynamic_seven_day_remaining_usd: float,
    available_accounts: int,
    safe_concurrency_available: float,
    per_account_five_hour_usd: float,
    per_account_seven_day_usd: float,
    average_account_concurrency: float,
    cost_per_request: float | None = None,
    refill_account_options: dict[str, dict[str, Any]] | None = None,
    primary_refill_account_type: str | None = None,
    demand_forecast: ForecastResult | None = None,
    current_hour_observed_cost_usd: float | None = None,
) -> dict[str, Any]:
    now = _as_utc(now)
    normalized_cost_per_token = _positive(cost_per_token)
    normalized_cost_per_request = _positive(cost_per_request)
    normalized = _normalized_samples(samples)
    demand_regime = detect_demand_regime(samples)
    latest_at = normalized[-1]["sampled_at"] if normalized else None
    latest_tpm = normalized[-1]["tpm"] if normalized else None
    latest_rpm = normalized[-1]["rpm"] if normalized else None
    recent_samples = normalized[-MIN_SAMPLE_COUNT:]
    continuous_minutes = (
        len(recent_samples) >= MIN_SAMPLE_COUNT
        and recent_samples[-1]["sampled_at"] - recent_samples[0]["sampled_at"] <= timedelta(minutes=20)
    )
    concurrency_sample_count = sum(
        1
        for item in recent_samples
        if item["current_concurrency"] is not None
    )
    rpm_sample_count = sum(1 for item in recent_samples if item["rpm"] is not None)
    direct_cost_values = [
        item["account_cost_per_minute"]
        for item in normalized[-15:]
        if item["account_cost_per_minute"] is not None
    ]
    direct_cost_ready = len(direct_cost_values) >= 5
    cost_channel_ready = direct_cost_ready or normalized_cost_per_token is not None or (
        normalized_cost_per_request is not None and rpm_sample_count >= 5
    )
    ready = (
        len(normalized) >= MIN_SAMPLE_COUNT
        and continuous_minutes
        and concurrency_sample_count >= 5
        and latest_at is not None
        and timedelta(0) <= now - latest_at <= MAX_SAMPLE_AGE
        and cost_channel_ready
    )
    if not ready:
        return _pending_summary(
            sample_count=len(normalized),
            concurrency_sample_count=concurrency_sample_count,
            rpm_sample_count=rpm_sample_count,
            latest_sampled_at=latest_at,
            latest_tpm=latest_tpm,
            latest_rpm=latest_rpm,
        )

    tpm_values = [item["tpm"] for item in normalized]
    rpm_values = [item["rpm"] for item in normalized if item["rpm"] is not None]
    duration_values = [item["average_duration_ms"] for item in normalized[-15:] if item["average_duration_ms"] is not None]
    concurrency_values = [
        item["current_concurrency"]
        for item in normalized[-60:]
        if item["current_concurrency"] is not None
    ]
    tpm_ema_5 = _ema(tpm_values, 5)
    tpm_ema_15 = _ema(tpm_values, 15)
    tpm_ema_60 = _ema(tpm_values, 60)
    tpm_p90_2h = _percentile(tpm_values[-120:], 0.90)
    rpm_ema_5 = _ema(rpm_values, 5) if rpm_values else 0.0
    rpm_ema_15 = _ema(rpm_values, 15) if rpm_values else 0.0
    rpm_ema_60 = _ema(rpm_values, 60) if rpm_values else 0.0
    average_duration_ms = sum(duration_values) / len(duration_values) if duration_values else 0.0
    concurrency_ema_5 = _ema(concurrency_values, 5) if concurrency_values else 0.0
    concurrency_p90_1h = _percentile(concurrency_values, 0.90) if concurrency_values else 0.0

    tpm_momentum = _ratio(tpm_ema_5, tpm_ema_15)
    tpm_medium_ratio = _ratio(tpm_ema_15, tpm_ema_60)
    rpm_momentum = _ratio(rpm_ema_5, rpm_ema_15)
    rpm_medium_ratio = _ratio(rpm_ema_15, rpm_ema_60)
    demand_ratio = max(tpm_momentum, tpm_medium_ratio, rpm_momentum, rpm_medium_ratio)
    trend_multiplier = min(1.5, max(1.0, tpm_momentum))
    falling = _is_falling(tpm_values, tpm_ema_5=tpm_ema_5, tpm_ema_15=tpm_ema_15)
    if falling:
        pressure_tpm = max(tpm_ema_5, tpm_ema_15)
    else:
        pressure_tpm = max(tpm_ema_15, tpm_p90_2h, tpm_ema_5 * trend_multiplier)
    pressure_rpm = rpm_ema_5
    estimated_concurrency = max(concurrency_ema_5, concurrency_p90_1h)
    if 0 < estimated_concurrency < 1:
        estimated_concurrency = 1.0
    concurrency_spare_coverage = _coverage(safe_concurrency_available, estimated_concurrency)
    concurrency_coverage = None if concurrency_spare_coverage is None else 1 + concurrency_spare_coverage

    realtime_tpm_burn_usd_per_hour = pressure_tpm * 60 * float(normalized_cost_per_token or 0)
    realtime_rpm_burn_usd_per_hour = pressure_rpm * 60 * float(normalized_cost_per_request or 0)
    direct_pressure_per_minute = estimate_direct_cost_per_minute(
        samples,
        stage=demand_regime.stage,
    ) or 0.0
    realtime_direct_burn_usd_per_hour = direct_pressure_per_minute * 60
    if direct_cost_ready:
        realtime_burn_usd_per_hour = realtime_direct_burn_usd_per_hour
        realtime_burn_source = "direct_account_cost"
    else:
        realtime_burn_usd_per_hour = max(
            realtime_tpm_burn_usd_per_hour,
            realtime_rpm_burn_usd_per_hour,
        )
        realtime_burn_source = (
            "rpm"
            if realtime_rpm_burn_usd_per_hour > realtime_tpm_burn_usd_per_hour
            else "tpm"
            if realtime_tpm_burn_usd_per_hour > 0
            else "rpm"
        )
    burn_usd_per_hour = realtime_burn_usd_per_hour
    actual_remaining_usd = min(
        max(0.0, actual_five_hour_remaining_usd),
        max(0.0, actual_seven_day_remaining_usd),
    )
    dynamic_remaining_usd = min(
        max(0.0, dynamic_five_hour_remaining_usd),
        max(0.0, dynamic_seven_day_remaining_usd),
    )
    actual_runway_hours = _runway(actual_remaining_usd, burn_usd_per_hour)
    dynamic_runway_hours = _runway(dynamic_remaining_usd, burn_usd_per_hour)
    runway_source = "tpm_pressure"
    forecast_status = "fallback"
    forecast_fallback_reason = "forecast_unavailable"
    forecast_p50_runway_hours = None
    forecast_p90_runway_hours = None
    forecast_actual_runway_capped = False
    forecast_dynamic_runway_capped = False
    forecast_nowcast_applied = False
    forecast_current_hour_observed_usd = None
    forecast_current_hour_model_remaining_usd = None
    forecast_current_hour_realtime_remaining_usd = None
    forecast_current_hour_selected_remaining_usd = None
    forecast_current_hour_candidate_remaining_usd = None
    forecast_nowcast_realtime_weight = None
    forecast_meta: dict[str, Any] = {}
    if demand_forecast is not None:
        try:
            effective_forecast = demand_forecast
            observed_cost = _nonnegative(current_hour_observed_cost_usd)
            if observed_cost is not None:
                base_nowcast = apply_current_hour_nowcast(
                    demand_forecast,
                    now=now,
                    observed_current_hour_cost_usd=observed_cost,
                    realtime_cost_per_hour=realtime_burn_usd_per_hour,
                )
                selection = select_nowcast_remaining(
                    model_remaining=base_nowcast.model_p90_remaining_usd,
                    realtime_remaining=base_nowcast.realtime_remaining_usd,
                    minute=now.minute,
                    stage=demand_regime.stage,
                    surge_strength=demand_regime.strength,
                )
                nowcast = apply_current_hour_nowcast(
                    demand_forecast,
                    now=now,
                    observed_current_hour_cost_usd=observed_cost,
                    realtime_cost_per_hour=realtime_burn_usd_per_hour,
                    selected_remaining_usd=(
                        selection.selected_remaining
                        if REGIME_NOWCAST_V2_ENABLED
                        else None
                    ),
                )
                effective_forecast = nowcast.forecast
                forecast_nowcast_applied = nowcast.applied
                forecast_current_hour_observed_usd = nowcast.observed_cost_usd
                forecast_current_hour_model_remaining_usd = nowcast.model_p90_remaining_usd
                forecast_current_hour_realtime_remaining_usd = nowcast.realtime_remaining_usd
                forecast_current_hour_selected_remaining_usd = nowcast.selected_p90_remaining_usd
                forecast_current_hour_candidate_remaining_usd = selection.selected_remaining
                forecast_nowcast_realtime_weight = selection.realtime_weight
            actual_forecast = forecast_runway(
                effective_forecast,
                remaining_usd=actual_remaining_usd,
                now=now,
                quantile="p90",
                max_hours=FORECAST_RUNWAY_HOURS,
            )
            dynamic_forecast = forecast_runway(
                effective_forecast,
                remaining_usd=dynamic_remaining_usd,
                now=now,
                quantile="p90",
                max_hours=FORECAST_RUNWAY_HOURS,
            )
            p50_forecast = forecast_runway(
                effective_forecast,
                remaining_usd=dynamic_remaining_usd,
                now=now,
                quantile="p50",
                max_hours=FORECAST_RUNWAY_HOURS,
            )
            burn_usd_per_hour = forecast_cost_over_window(
                effective_forecast,
                now=now,
                hours=FORECAST_BURN_WINDOW_HOURS,
                quantile="p90",
            ) / FORECAST_BURN_WINDOW_HOURS
            actual_runway_hours = actual_forecast.hours
            dynamic_runway_hours = dynamic_forecast.hours
            forecast_p50_runway_hours = p50_forecast.hours
            forecast_p90_runway_hours = dynamic_forecast.hours
            forecast_actual_runway_capped = actual_forecast.capped
            forecast_dynamic_runway_capped = dynamic_forecast.capped
            runway_source = "hourly_forecast_p90_nowcast" if forecast_nowcast_applied else "hourly_forecast_p90"
            forecast_status = "active"
            forecast_fallback_reason = None
            forecast_meta = {
                "forecast_model": demand_forecast.model,
                "forecast_version": demand_forecast.version,
                "forecast_as_of": demand_forecast.as_of,
                "forecast_readiness": demand_forecast.readiness,
                "forecast_history_hours": demand_forecast.history_hours,
                "forecast_completeness_ratio": demand_forecast.completeness_ratio,
                "forecast_horizon_hours": FORECAST_RUNWAY_HOURS,
            }
        except ForecastInputError as exc:
            forecast_fallback_reason = str(exc)

    health_status, health_reason = _health_status(
        available_accounts=available_accounts,
        actual_runway_hours=actual_runway_hours,
        dynamic_runway_hours=dynamic_runway_hours,
        concurrency_spare_coverage=concurrency_spare_coverage,
    )
    inventory_risk = falling and dynamic_runway_hours is not None and dynamic_runway_hours > INVENTORY_RISK_RUNWAY_HOURS
    pressure_stage = _pressure_stage(
        health_status=health_status,
        demand_ratio=demand_ratio,
        tpm_momentum=tpm_momentum,
        falling=falling,
        inventory_risk=inventory_risk,
        regime_stage=demand_regime.stage,
    )

    quota_refill_accounts = _quota_refill_accounts(
        burn_usd_per_hour=burn_usd_per_hour,
        actual_five_hour_remaining_usd=actual_five_hour_remaining_usd,
        dynamic_five_hour_remaining_usd=dynamic_five_hour_remaining_usd,
        dynamic_seven_day_remaining_usd=dynamic_seven_day_remaining_usd,
        per_account_five_hour_usd=per_account_five_hour_usd,
        per_account_seven_day_usd=per_account_seven_day_usd,
    )
    concurrency_refill_accounts = _concurrency_refill_accounts(
        estimated_concurrency=estimated_concurrency,
        safe_concurrency_available=safe_concurrency_available,
        average_account_concurrency=average_account_concurrency,
    )
    account_floor_refill = max(0, 3 - max(0, int(available_accounts)))
    recommended_refill_accounts = max(quota_refill_accounts, concurrency_refill_accounts, account_floor_refill)
    recommended_refill_options = _recommended_refill_options(
        refill_account_options=refill_account_options,
        burn_usd_per_hour=burn_usd_per_hour,
        actual_five_hour_remaining_usd=actual_five_hour_remaining_usd,
        dynamic_five_hour_remaining_usd=dynamic_five_hour_remaining_usd,
        dynamic_seven_day_remaining_usd=dynamic_seven_day_remaining_usd,
        estimated_concurrency=estimated_concurrency,
        safe_concurrency_available=safe_concurrency_available,
        average_account_concurrency=average_account_concurrency,
        account_floor_refill=account_floor_refill,
    )
    primary_option = recommended_refill_options.get(str(primary_refill_account_type or "").strip().lower())
    if primary_option is not None:
        recommended_refill_accounts = primary_option["recommended_refill_accounts"]
    replenishment_required = not inventory_risk and (
        health_status in {"tight", "danger", "exhausted"} or recommended_refill_accounts > 0
    )
    if not replenishment_required:
        recommended_refill_accounts = 0
        recommended_refill_options = {}

    health_label, health_tone = HEALTH_META[health_status]
    return {
        "ready": True,
        "sample_count": len(normalized),
        "concurrency_sample_count": len(concurrency_values),
        "rpm_sample_count": len(rpm_values),
        "latest_sampled_at": latest_at,
        "latest_tpm": _rounded(latest_tpm),
        "latest_rpm": _rounded(latest_rpm),
        "tpm_ema_5": _rounded(tpm_ema_5),
        "tpm_ema_15": _rounded(tpm_ema_15),
        "tpm_ema_60": _rounded(tpm_ema_60),
        "tpm_p90_2h": _rounded(tpm_p90_2h),
        "rpm_ema_5": _rounded(rpm_ema_5),
        "average_duration_ms": _rounded(average_duration_ms),
        "concurrency_ema_5": _rounded(concurrency_ema_5),
        "concurrency_p90_1h": _rounded(concurrency_p90_1h),
        "tpm_momentum": _rounded(tpm_momentum),
        "demand_ratio": _rounded(demand_ratio),
        "pressure_tpm": _rounded(pressure_tpm),
        "pressure_rpm": _rounded(pressure_rpm),
        "estimated_concurrency": _rounded(estimated_concurrency),
        "concurrency_coverage": _rounded(concurrency_coverage),
        "burn_usd_per_hour": _rounded(burn_usd_per_hour),
        "realtime_burn_usd_per_hour": _rounded(realtime_burn_usd_per_hour),
        "realtime_tpm_burn_usd_per_hour": _rounded(realtime_tpm_burn_usd_per_hour),
        "realtime_rpm_burn_usd_per_hour": _rounded(realtime_rpm_burn_usd_per_hour),
        "realtime_direct_burn_usd_per_hour": _rounded(realtime_direct_burn_usd_per_hour),
        "realtime_burn_source": realtime_burn_source,
        "demand_regime_stage": demand_regime.stage,
        "demand_regime_strength": _rounded(demand_regime.strength),
        "demand_regime_confidence": _rounded(demand_regime.confidence),
        "demand_regime_signal_count": demand_regime.signal_count,
        "demand_regime_cost_source": demand_regime.cost_source,
        "demand_regime_short_ratio": _rounded(demand_regime.short_ratio),
        "demand_regime_medium_ratio": _rounded(demand_regime.medium_ratio),
        "demand_regime_robust_z": _rounded(demand_regime.robust_z),
        "demand_regime_positive_cusum": _rounded(demand_regime.positive_cusum),
        "actual_runway_hours": _rounded(actual_runway_hours),
        "dynamic_runway_hours": _rounded(dynamic_runway_hours),
        "runway_source": runway_source,
        "forecast_status": forecast_status,
        "forecast_fallback_reason": forecast_fallback_reason,
        "forecast_p50_runway_hours": _rounded(forecast_p50_runway_hours),
        "forecast_p90_runway_hours": _rounded(forecast_p90_runway_hours),
        "forecast_actual_runway_capped": forecast_actual_runway_capped,
        "forecast_dynamic_runway_capped": forecast_dynamic_runway_capped,
        "forecast_nowcast_applied": forecast_nowcast_applied,
        "forecast_current_hour_observed_usd": _rounded(forecast_current_hour_observed_usd),
        "forecast_current_hour_model_remaining_usd": _rounded(forecast_current_hour_model_remaining_usd),
        "forecast_current_hour_realtime_remaining_usd": _rounded(forecast_current_hour_realtime_remaining_usd),
        "forecast_current_hour_selected_remaining_usd": _rounded(forecast_current_hour_selected_remaining_usd),
        "forecast_current_hour_candidate_remaining_usd": _rounded(forecast_current_hour_candidate_remaining_usd),
        "forecast_nowcast_realtime_weight": _rounded(forecast_nowcast_realtime_weight),
        "forecast_nowcast_selector": "regime_aware_v2" if REGIME_NOWCAST_V2_ENABLED else "current_max_v1",
        "target_runway_hours": DYNAMIC_RUNWAY_TARGET_HOURS,
        "actual_target_hours": ACTUAL_RUNWAY_TARGET_HOURS,
        "concurrency_target_coverage": TOTAL_CONCURRENCY_TARGET,
        "pressure_stage": pressure_stage,
        "pressure_stage_label": PRESSURE_STAGE_LABELS[pressure_stage],
        "inventory_risk": inventory_risk,
        "health_status": health_status,
        "health_label": health_label,
        "health_tone": health_tone,
        "health_reason": health_reason,
        "replenishment_required": replenishment_required,
        "quota_refill_accounts": quota_refill_accounts,
        "concurrency_refill_accounts": concurrency_refill_accounts,
        "recommended_refill_accounts": recommended_refill_accounts,
        "recommended_refill_options": recommended_refill_options,
        **forecast_meta,
    }


def _pending_summary(
    *,
    sample_count: int,
    concurrency_sample_count: int,
    rpm_sample_count: int,
    latest_sampled_at: datetime | None,
    latest_tpm: float | None,
    latest_rpm: float | None,
) -> dict[str, Any]:
    label, tone = HEALTH_META["pending"]
    return {
        "ready": False,
        "sample_count": sample_count,
        "concurrency_sample_count": concurrency_sample_count,
        "rpm_sample_count": rpm_sample_count,
        "latest_sampled_at": latest_sampled_at,
        "latest_tpm": _rounded(latest_tpm),
        "latest_rpm": _rounded(latest_rpm),
        "pressure_stage": "waiting_data",
        "pressure_stage_label": PRESSURE_STAGE_LABELS["waiting_data"],
        "inventory_risk": False,
        "health_status": "pending",
        "health_label": label,
        "health_tone": tone,
        "health_reason": "分钟 TPM/RPM/并发数据仍在积累，暂不进行容量告警",
        "replenishment_required": False,
        "quota_refill_accounts": 0,
        "concurrency_refill_accounts": 0,
        "recommended_refill_accounts": 0,
        "recommended_refill_options": {},
        "target_runway_hours": DYNAMIC_RUNWAY_TARGET_HOURS,
        "actual_target_hours": ACTUAL_RUNWAY_TARGET_HOURS,
        "concurrency_target_coverage": TOTAL_CONCURRENCY_TARGET,
    }


def _health_status(
    *,
    available_accounts: int,
    actual_runway_hours: float | None,
    dynamic_runway_hours: float | None,
    concurrency_spare_coverage: float | None,
) -> tuple[str, str]:
    if available_accounts <= 2:
        return "exhausted", "可用账号不超过 2 个"
    if dynamic_runway_hours is not None and dynamic_runway_hours < EXHAUSTED_RUNWAY_HOURS:
        return "exhausted", f"动态容量预计仅可用 {_hours_text(dynamic_runway_hours)}"
    if (
        (actual_runway_hours is not None and actual_runway_hours < ACTUAL_RUNWAY_TARGET_HOURS)
        or (dynamic_runway_hours is not None and dynamic_runway_hours < ACTUAL_RUNWAY_TARGET_HOURS)
        or (concurrency_spare_coverage is not None and concurrency_spare_coverage < 1.0)
    ):
        return "danger", "实际额度不足 1 小时，或并发已经低于当前压力需求"
    if (
        (dynamic_runway_hours is not None and dynamic_runway_hours < DYNAMIC_RUNWAY_TARGET_HOURS)
        or (concurrency_spare_coverage is not None and concurrency_spare_coverage < SAFE_CONCURRENCY_TARGET)
    ):
        return "tight", "动态容量不足 3 小时，或安全并发余量不足 1.2 倍"
    if dynamic_runway_hours is not None and dynamic_runway_hours >= INVENTORY_RISK_RUNWAY_HOURS:
        return "abundant", "当前容量超过 6 小时目标上限"
    return "healthy", "实际额度、动态容量和安全并发均达到目标"


def _pressure_stage(
    *,
    health_status: str,
    demand_ratio: float,
    tpm_momentum: float,
    falling: bool,
    inventory_risk: bool,
    regime_stage: str,
) -> str:
    if inventory_risk:
        return "inventory_risk"
    if health_status in {"exhausted", "danger"}:
        return "peak_guard"
    if regime_stage == "cooling" or falling:
        return "recovering"
    if regime_stage == "surge" or demand_ratio >= 1.5 or tpm_momentum >= 1.2:
        return "accelerating"
    if regime_stage == "warming" or demand_ratio >= 1.2:
        return "transmission"
    return "stable"


def _quota_refill_accounts(
    *,
    burn_usd_per_hour: float,
    actual_five_hour_remaining_usd: float,
    dynamic_five_hour_remaining_usd: float,
    dynamic_seven_day_remaining_usd: float,
    per_account_five_hour_usd: float,
    per_account_seven_day_usd: float,
) -> int:
    actual_5h_gap = max(0.0, burn_usd_per_hour * ACTUAL_RUNWAY_TARGET_HOURS - max(0.0, actual_five_hour_remaining_usd))
    dynamic_5h_gap = max(0.0, burn_usd_per_hour * DYNAMIC_RUNWAY_TARGET_HOURS - max(0.0, dynamic_five_hour_remaining_usd))
    dynamic_7d_gap = max(0.0, burn_usd_per_hour * DYNAMIC_RUNWAY_TARGET_HOURS - max(0.0, dynamic_seven_day_remaining_usd))
    five_hour_accounts = _ceil_ratio(max(actual_5h_gap, dynamic_5h_gap), per_account_five_hour_usd)
    seven_day_accounts = _ceil_ratio(dynamic_7d_gap, per_account_seven_day_usd)
    return max(five_hour_accounts, seven_day_accounts)


def _concurrency_refill_accounts(
    *,
    estimated_concurrency: float,
    safe_concurrency_available: float,
    average_account_concurrency: float,
) -> int:
    gap = max(0.0, estimated_concurrency * SAFE_CONCURRENCY_TARGET - max(0.0, safe_concurrency_available))
    return _ceil_ratio(gap, average_account_concurrency)


def _recommended_refill_options(
    *,
    refill_account_options: dict[str, dict[str, Any]] | None,
    burn_usd_per_hour: float,
    actual_five_hour_remaining_usd: float,
    dynamic_five_hour_remaining_usd: float,
    dynamic_seven_day_remaining_usd: float,
    estimated_concurrency: float,
    safe_concurrency_available: float,
    average_account_concurrency: float,
    account_floor_refill: int,
) -> dict[str, dict[str, Any]]:
    if not isinstance(refill_account_options, dict):
        return {}
    concurrency_refill = _concurrency_refill_accounts(
        estimated_concurrency=estimated_concurrency,
        safe_concurrency_available=safe_concurrency_available,
        average_account_concurrency=average_account_concurrency,
    )
    result: dict[str, dict[str, Any]] = {}
    for raw_account_type, raw_limits in refill_account_options.items():
        account_type = str(raw_account_type or "").strip().lower()
        if not account_type or not isinstance(raw_limits, dict):
            continue
        five_hour_limit = _positive(raw_limits.get("five_hour_usd"))
        seven_day_limit = _positive(raw_limits.get("seven_day_usd"))
        if five_hour_limit is None or seven_day_limit is None:
            continue
        quota_refill = _quota_refill_accounts(
            burn_usd_per_hour=burn_usd_per_hour,
            actual_five_hour_remaining_usd=actual_five_hour_remaining_usd,
            dynamic_five_hour_remaining_usd=dynamic_five_hour_remaining_usd,
            dynamic_seven_day_remaining_usd=dynamic_seven_day_remaining_usd,
            per_account_five_hour_usd=five_hour_limit,
            per_account_seven_day_usd=seven_day_limit,
        )
        result[account_type] = {
            "account_type": account_type,
            "quota_refill_accounts": quota_refill,
            "concurrency_refill_accounts": concurrency_refill,
            "recommended_refill_accounts": max(quota_refill, concurrency_refill, account_floor_refill),
        }
    return result


def _normalized_samples(samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for sample in samples:
        sampled_at = _datetime_value(sample.get("sampled_at"))
        tpm = _nonnegative(sample.get("tpm"))
        if sampled_at is None or tpm is None:
            continue
        normalized.append(
            {
                "sampled_at": sampled_at,
                "tpm": tpm,
                "rpm": _nonnegative(sample.get("rpm")),
                "average_duration_ms": _nonnegative(sample.get("average_duration_ms")),
                "current_concurrency": _nonnegative(sample.get("current_concurrency")),
                "account_cost_per_minute": _nonnegative(sample.get("account_cost_per_minute")),
            }
        )
    return sorted(normalized, key=lambda item: item["sampled_at"])


def _ema(values: list[float], span: int) -> float:
    alpha = 2 / (span + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(percentile * len(ordered)) - 1))
    return ordered[index]


def _is_falling(values: list[float], *, tpm_ema_5: float, tpm_ema_15: float) -> bool:
    if len(values) < 10:
        return False
    recent = sum(values[-5:]) / 5
    previous = sum(values[-10:-5]) / 5
    return previous > 0 and recent <= previous * 0.8 and tpm_ema_5 < tpm_ema_15 * 0.9


def _coverage(available: float, demand: float) -> float | None:
    if demand <= 0:
        return None
    return max(0.0, available) / demand


def _runway(remaining: float, burn_per_hour: float) -> float | None:
    if burn_per_hour <= 0:
        return None
    return max(0.0, remaining) / burn_per_hour


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 1.0
    return numerator / denominator


def _ceil_ratio(numerator: float, denominator: float) -> int:
    if numerator <= 0 or denominator <= 0:
        return 0
    return math.ceil(numerator / denominator)


def _positive(value: Any) -> float | None:
    parsed = _nonnegative(value)
    return parsed if parsed is not None and parsed > 0 else None


def _nonnegative(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _rounded(value: float | None) -> float | None:
    return None if value is None else round(value, 4)


def _hours_text(value: float) -> str:
    if value < 1:
        return f"{round(value * 60)} 分钟"
    return f"{round(value, 1)} 小时"
