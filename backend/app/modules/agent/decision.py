from __future__ import annotations

from math import ceil
from typing import Any


SEVERITY_RANK = {
    "healthy": 0,
    "watch": 1,
    "warning": 2,
    "danger": 3,
    "critical": 4,
}


def decide_pool_action(*, pool: dict[str, Any], capacity: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
    severity = "healthy"
    reasons: list[str] = []
    actions: list[str] = []

    current_speed_days = _number_or_none(capacity.get("current_speed_days"))
    recent_peak = _number_or_none(capacity.get("recent_day_five_hour_peak_multiple"))
    burst_peak = _number_or_none(capacity.get("burst_1h_five_hour_multiple"))
    burst_trend = str(capacity.get("burst_1h_trend") or "").strip().lower()
    burst_trend_label = str(capacity.get("burst_1h_trend_label") or "").strip()
    burst_trend_strength = str(capacity.get("burst_1h_trend_strength") or "").strip().lower()
    burst_trend_strength_label = str(capacity.get("burst_1h_trend_strength_label") or "").strip()
    reserve_count = _int_value(capacity.get("reserve_account_count"))
    available_accounts = _int_value(capacity.get("available_accounts"))
    target_active = _int_value(pool.get("target_active"), default=30)
    min_reserve = _int_value(pool.get("min_reserve"), default=10)
    detected_401_1h = _int_value(probe.get("detected_401_1h", probe.get("pro_401_1h")))
    detected_401_24h = _int_value(probe.get("detected_401_24h", probe.get("pro_401_24h")))

    if current_speed_days is None:
        severity = _max_severity(severity, "watch")
        reasons.append("当前速度可用天数暂无数据，容量判断需要结合缓存刷新结果。")
    elif current_speed_days < 1:
        severity = _max_severity(severity, "critical")
        reasons.append(f"当前速度预计只能撑 {_format_days(current_speed_days)}，低于 1 天红线。")
    elif current_speed_days < 3:
        severity = _max_severity(severity, "danger")
        reasons.append(f"当前速度预计还能撑 {_format_days(current_speed_days)}，低于 3 天安全线。")
    else:
        reasons.append(f"当前速度预计还能撑 {_format_days(current_speed_days)}。")

    if recent_peak is None:
        severity = _max_severity(severity, "watch")
        reasons.append("最近一天 5h 峰值倍数暂无数据。")
    elif recent_peak < 1:
        severity = _max_severity(severity, "danger")
        reasons.append(f"最近一天 5h 峰值容量只有 {recent_peak:.2f}x，已经低于 1x。")
    elif recent_peak < 1.5:
        severity = _max_severity(severity, "warning")
        reasons.append(f"最近一天 5h 峰值容量为 {recent_peak:.2f}x，低于 1.5x。")
    else:
        reasons.append(f"最近一天 5h 峰值容量为 {recent_peak:.2f}x。")

    if burst_peak is None:
        reasons.append("突发 1h 预估峰值暂无数据。")
    elif burst_peak < 1:
        severity = _max_severity(severity, "danger")
        reasons.append(f"突发 1h 预估 5h 峰值容量只有 {burst_peak:.2f}x，短时压力已经低于 1x。")
    elif burst_peak < 1.5:
        severity = _max_severity(severity, "warning")
        reasons.append(f"突发 1h 预估 5h 峰值容量为 {burst_peak:.2f}x，低于 1.5x。")
    else:
        reasons.append(f"突发 1h 预估 5h 峰值容量为 {burst_peak:.2f}x。")

    if burst_trend == "rising" and burst_trend_strength in {"strong", "extreme"}:
        severity = _max_severity(severity, "danger" if burst_trend_strength == "extreme" else "warning")
        label = _joined_label(burst_trend_label or "上涨", burst_trend_strength_label)
        reasons.append(f"突发趋势最近 1h 为{label}，短期消耗可能继续抬升。")
    elif burst_trend == "rising" and burst_trend_strength in {"medium", "weak"}:
        reasons.append(f"突发趋势最近 1h 为{_joined_label(burst_trend_label or '上涨', burst_trend_strength_label)}。")

    if reserve_count < min_reserve:
        severity = _max_severity(severity, "warning")
        reasons.append(f"备用池只有 {reserve_count} 个，低于最小备用线 {min_reserve}。")

    if probe.get("probe_fresh") is False:
        severity = _max_severity(severity, "warning")
        reasons.append("账号探测数据超过 10 分钟未更新，建议先确认探测任务是否正常。")

    if detected_401_1h >= 3:
        severity = _max_severity(severity, "danger")
        reasons.append(f"最近 1h 出现 {detected_401_1h} 个 401，封号速度偏高。")
    elif detected_401_1h > 0:
        severity = _max_severity(severity, "warning")
        reasons.append(f"最近 1h 出现 {detected_401_1h} 个 401。")

    if detected_401_24h >= 5:
        severity = _max_severity(severity, "danger")
        reasons.append(f"最近 24h 出现 {detected_401_24h} 个 401，需要增加风险 buffer。")
    elif detected_401_24h > 0:
        reasons.append(f"最近 24h 出现 {detected_401_24h} 个 401。")

    base_needed = max(0, target_active - available_accounts)
    reserve_gap = max(0, min_reserve - reserve_count)
    risk_buffer = ceil(detected_401_24h * (2.0 if detected_401_1h >= 3 else 1.5))
    burst_buffer = _burst_buffer(
        burst_peak=burst_peak,
        burst_trend=burst_trend,
        burst_trend_strength=burst_trend_strength,
        target_active=target_active,
        available_accounts=available_accounts,
    )
    suggested_add_count = max(base_needed, reserve_gap) + risk_buffer + burst_buffer
    suggested_push_count = min(reserve_count, suggested_add_count)
    suggested_make_count = max(0, suggested_add_count - suggested_push_count)

    if suggested_push_count > 0:
        actions.append(f"可优先从备用池推送 {suggested_push_count} 个账号。")
    if suggested_make_count > 0:
        actions.append(f"建议制作或准备 {suggested_make_count} 个新账号补足安全 buffer。")
    if suggested_add_count == 0:
        actions.append("当前不建议立即补号，保持观察即可。")

    manual_review_required = severity in {"critical", "danger"} and suggested_add_count >= max(10, target_active)
    if manual_review_required:
        actions.append("建议人工复核后再执行补号，避免异常数据导致过量补号。")

    headline = _headline(str(pool.get("name") or "账号池"), severity, suggested_add_count)
    return {
        "severity": severity,
        "headline": headline,
        "suggested_add_count": suggested_add_count,
        "suggested_push_from_reserve_count": suggested_push_count,
        "suggested_make_new_count": suggested_make_count,
        "manual_review_required": manual_review_required,
        "reasons": reasons,
        "suggested_actions": actions,
        "inputs": {
            "target_active": target_active,
            "min_reserve": min_reserve,
            "available_accounts": available_accounts,
            "reserve_account_count": reserve_count,
            "base_needed": base_needed,
            "reserve_gap": reserve_gap,
            "risk_buffer": risk_buffer,
            "burst_buffer": burst_buffer,
            "burst_1h_five_hour_multiple": burst_peak,
            "burst_1h_trend": burst_trend or None,
            "burst_1h_trend_strength": burst_trend_strength or None,
        },
    }


def _headline(pool_name: str, severity: str, suggested_add_count: int) -> str:
    labels = {
        "healthy": "健康",
        "watch": "观察",
        "warning": "预警",
        "danger": "紧张",
        "critical": "危险",
    }
    if suggested_add_count > 0:
        return f"{pool_name} 当前{labels.get(severity, severity)}，建议补 {suggested_add_count} 个号"
    return f"{pool_name} 当前{labels.get(severity, severity)}，暂不建议补号"


def _max_severity(current: str, candidate: str) -> str:
    return candidate if SEVERITY_RANK[candidate] > SEVERITY_RANK[current] else current


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _int_value(value: Any, *, default: int = 0) -> int:
    number = _number_or_none(value)
    return default if number is None else max(0, int(number))


def _format_days(value: float) -> str:
    if value < 1:
        return f"{max(0, value * 24):.1f} 小时"
    return f"{value:.1f} 天"


def _burst_buffer(
    *,
    burst_peak: float | None,
    burst_trend: str,
    burst_trend_strength: str,
    target_active: int,
    available_accounts: int,
) -> int:
    pressure = 0
    if burst_peak is not None:
        if burst_peak < 1:
            pressure += max(3, ceil(target_active * 0.2))
        elif burst_peak < 1.25:
            pressure += max(2, ceil(target_active * 0.12))
        elif burst_peak < 1.5:
            pressure += max(1, ceil(target_active * 0.08))

    if burst_trend == "rising":
        if burst_trend_strength == "extreme":
            pressure += max(3, ceil(target_active * 0.15))
        elif burst_trend_strength == "strong":
            pressure += max(2, ceil(target_active * 0.1))
        elif burst_trend_strength == "medium":
            pressure += max(1, ceil(target_active * 0.05))

    if pressure <= 0:
        return 0
    missing_to_target = max(0, target_active - available_accounts)
    max_extra = max(2, ceil(target_active * 0.25))
    return min(max_extra, max(pressure - missing_to_target, 0))


def _joined_label(primary: str, secondary: str) -> str:
    primary = primary.strip()
    secondary = secondary.strip()
    if primary and secondary:
        return f"{primary} · {secondary}"
    return primary or secondary or "未知"
