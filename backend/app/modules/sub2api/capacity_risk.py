from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import Any


MIN_SAMPLE_COUNT = 15
MAX_SAMPLE_AGE = timedelta(minutes=3)
ACTUAL_RUNWAY_TARGET_HOURS = 1.0
DYNAMIC_RUNWAY_TARGET_HOURS = 3.0
EXHAUSTED_RUNWAY_HOURS = 0.5
INVENTORY_RISK_RUNWAY_HOURS = 6.0
SAFE_CONCURRENCY_TARGET = 1.2

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
    refill_account_options: dict[str, dict[str, Any]] | None = None,
    primary_refill_account_type: str | None = None,
) -> dict[str, Any]:
    now = _as_utc(now)
    normalized = _normalized_samples(samples)
    latest_at = normalized[-1]["sampled_at"] if normalized else None
    recent_samples = normalized[-MIN_SAMPLE_COUNT:]
    continuous_minutes = (
        len(recent_samples) >= MIN_SAMPLE_COUNT
        and recent_samples[-1]["sampled_at"] - recent_samples[0]["sampled_at"] <= timedelta(minutes=20)
    )
    concurrency_sample_count = sum(
        1
        for item in recent_samples
        if item["rpm"] is not None
        and item["average_duration_ms"] is not None
        and item["average_duration_ms"] > 0
    )
    ready = (
        len(normalized) >= MIN_SAMPLE_COUNT
        and continuous_minutes
        and concurrency_sample_count >= 5
        and latest_at is not None
        and timedelta(0) <= now - latest_at <= MAX_SAMPLE_AGE
        and _positive(cost_per_token) is not None
    )
    if not ready:
        return _pending_summary(sample_count=len(normalized), latest_sampled_at=latest_at)

    tpm_values = [item["tpm"] for item in normalized]
    rpm_values = [item["rpm"] for item in normalized if item["rpm"] is not None]
    duration_values = [item["average_duration_ms"] for item in normalized[-15:] if item["average_duration_ms"] is not None]
    tpm_ema_5 = _ema(tpm_values, 5)
    tpm_ema_15 = _ema(tpm_values, 15)
    tpm_ema_60 = _ema(tpm_values, 60)
    tpm_p90_2h = _percentile(tpm_values[-120:], 0.90)
    rpm_ema_5 = _ema(rpm_values, 5) if rpm_values else 0.0
    rpm_ema_15 = _ema(rpm_values, 15) if rpm_values else 0.0
    rpm_ema_60 = _ema(rpm_values, 60) if rpm_values else 0.0
    average_duration_ms = sum(duration_values) / len(duration_values) if duration_values else 0.0

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
    estimated_concurrency = pressure_rpm * average_duration_ms / 60_000
    concurrency_coverage = _coverage(safe_concurrency_available, estimated_concurrency)

    burn_usd_per_hour = pressure_tpm * 60 * float(cost_per_token)
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

    health_status, health_reason = _health_status(
        available_accounts=available_accounts,
        actual_runway_hours=actual_runway_hours,
        dynamic_runway_hours=dynamic_runway_hours,
        concurrency_coverage=concurrency_coverage,
    )
    inventory_risk = falling and dynamic_runway_hours is not None and dynamic_runway_hours > INVENTORY_RISK_RUNWAY_HOURS
    pressure_stage = _pressure_stage(
        health_status=health_status,
        demand_ratio=demand_ratio,
        tpm_momentum=tpm_momentum,
        falling=falling,
        inventory_risk=inventory_risk,
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
        "latest_sampled_at": latest_at,
        "tpm_ema_5": _rounded(tpm_ema_5),
        "tpm_ema_15": _rounded(tpm_ema_15),
        "tpm_ema_60": _rounded(tpm_ema_60),
        "tpm_p90_2h": _rounded(tpm_p90_2h),
        "rpm_ema_5": _rounded(rpm_ema_5),
        "average_duration_ms": _rounded(average_duration_ms),
        "tpm_momentum": _rounded(tpm_momentum),
        "demand_ratio": _rounded(demand_ratio),
        "pressure_tpm": _rounded(pressure_tpm),
        "pressure_rpm": _rounded(pressure_rpm),
        "estimated_concurrency": _rounded(estimated_concurrency),
        "concurrency_coverage": _rounded(concurrency_coverage),
        "burn_usd_per_hour": _rounded(burn_usd_per_hour),
        "actual_runway_hours": _rounded(actual_runway_hours),
        "dynamic_runway_hours": _rounded(dynamic_runway_hours),
        "target_runway_hours": DYNAMIC_RUNWAY_TARGET_HOURS,
        "actual_target_hours": ACTUAL_RUNWAY_TARGET_HOURS,
        "concurrency_target_coverage": SAFE_CONCURRENCY_TARGET,
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
    }


def _pending_summary(*, sample_count: int, latest_sampled_at: datetime | None) -> dict[str, Any]:
    label, tone = HEALTH_META["pending"]
    return {
        "ready": False,
        "sample_count": sample_count,
        "latest_sampled_at": latest_sampled_at,
        "pressure_stage": "waiting_data",
        "pressure_stage_label": PRESSURE_STAGE_LABELS["waiting_data"],
        "inventory_risk": False,
        "health_status": "pending",
        "health_label": label,
        "health_tone": tone,
        "health_reason": "分钟 TPM/RPM 数据不足，暂用历史容量判断",
        "replenishment_required": False,
        "quota_refill_accounts": 0,
        "concurrency_refill_accounts": 0,
        "recommended_refill_accounts": 0,
        "recommended_refill_options": {},
        "target_runway_hours": DYNAMIC_RUNWAY_TARGET_HOURS,
        "actual_target_hours": ACTUAL_RUNWAY_TARGET_HOURS,
        "concurrency_target_coverage": SAFE_CONCURRENCY_TARGET,
    }


def _health_status(
    *,
    available_accounts: int,
    actual_runway_hours: float | None,
    dynamic_runway_hours: float | None,
    concurrency_coverage: float | None,
) -> tuple[str, str]:
    if available_accounts <= 2:
        return "exhausted", "可用账号不超过 2 个"
    if dynamic_runway_hours is not None and dynamic_runway_hours < EXHAUSTED_RUNWAY_HOURS:
        return "exhausted", f"动态容量预计仅可用 {_hours_text(dynamic_runway_hours)}"
    if (
        (actual_runway_hours is not None and actual_runway_hours < ACTUAL_RUNWAY_TARGET_HOURS)
        or (dynamic_runway_hours is not None and dynamic_runway_hours < ACTUAL_RUNWAY_TARGET_HOURS)
        or (concurrency_coverage is not None and concurrency_coverage < 1.0)
    ):
        return "danger", "实际额度不足 1 小时，或并发已经低于当前压力需求"
    if (
        (dynamic_runway_hours is not None and dynamic_runway_hours < DYNAMIC_RUNWAY_TARGET_HOURS)
        or (concurrency_coverage is not None and concurrency_coverage < SAFE_CONCURRENCY_TARGET)
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
) -> str:
    if inventory_risk:
        return "inventory_risk"
    if health_status in {"exhausted", "danger"}:
        return "peak_guard"
    if falling:
        return "recovering"
    if demand_ratio >= 1.5 or tpm_momentum >= 1.2:
        return "accelerating"
    if demand_ratio >= 1.2:
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
