from __future__ import annotations

from typing import Any


def build_capacity_dictionary(account_type: str | None = None) -> dict[str, str]:
    """Return stable explanations for capacity metrics.

    This dictionary teaches the LLM how to read metrics. It is not a judgement
    about the current pool.
    """

    normalized_type = (account_type or "account").strip().lower()
    return {
        "account_type": f"当前账号池识别为 {normalized_type} 类型；不同类型账号的额度含义应结合主系统容量数据理解。",
        "single_account_5h_limit_usd": "当前站点为该账号类型配置的单账号 5h 可用额度，必须读取 capacity_status.account_limits_usd，不能按账号类型硬编码。",
        "single_account_7d_limit_usd": "当前站点为该账号类型配置的单账号 7d 可用额度，必须读取 capacity_status.account_limits_usd，不能按账号类型硬编码。",
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
    }


def build_operational_facts(
    *,
    capacity: dict[str, Any],
    capacity_status: dict[str, Any] | None = None,
    concurrency_status: dict[str, Any] | None = None,
    system_capacity_assessment: dict[str, Any] | None = None,
    probe: dict[str, Any],
    event_windows: dict[str, Any],
    recent_decisions: list[dict[str, Any]],
    long_term_memory: dict[str, Any],
) -> dict[str, Any]:
    """Build deterministic operating facts for the LLM.

    This function explains and organizes data. It must not decide final
    severity, replenishment count, alerting, or next action.
    """

    return {
        "capacity_facts": _capacity_facts(capacity) + _normalized_capacity_facts(capacity_status or {}),
        "concurrency_facts": _concurrency_facts(concurrency_status or {}),
        "system_capacity_assessment_facts": _system_capacity_assessment_facts(system_capacity_assessment or {}),
        "usage_facts": _usage_facts(capacity),
        "burst_facts": _burst_facts(capacity),
        "event_facts": _event_facts(event_windows),
        "probe_facts": _probe_facts(probe),
        "memory_facts": _memory_facts(recent_decisions, long_term_memory),
        "data_quality_facts": _data_quality_facts(capacity, probe, event_windows),
        "risk_signals": _risk_signals(capacity, probe, event_windows),
        "data_gaps": _data_gaps(capacity, probe, event_windows),
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
            f"当前 5h 动态可用额度约为 {dynamic_5h_available if dynamic_5h_available is not None else '未知'} 美元，"
            f"实际可用额度约为 {actual_5h_available if actual_5h_available is not None else '未知'} 美元。"
        )
    dynamic_7d = capacity.get("dynamic_7d_total_usd")
    if dynamic_7d is not None:
        facts.append(f"当前 7d 动态总容量约为 {dynamic_7d} 美元。")
    dynamic_7d_available = capacity.get("dynamic_7d_available_usd")
    actual_7d_available = capacity.get("actual_7d_available_usd")
    if dynamic_7d_available is not None or actual_7d_available is not None:
        facts.append(
            f"当前 7d 动态可用额度约为 {dynamic_7d_available if dynamic_7d_available is not None else '未知'} 美元，"
            f"实际可用额度约为 {actual_7d_available if actual_7d_available is not None else '未知'} 美元。"
        )
    recent_peak = _number_or_none(capacity.get("recent_day_5h_peak_multiple") or capacity.get("recent_day_five_hour_peak_multiple"))
    if recent_peak is not None:
        if recent_peak < 1:
            facts.append(f"最近一天 5h 峰值容量倍数为 {recent_peak}x，表示当前池子低于最近峰值需求。")
        else:
            facts.append(f"最近一天 5h 峰值容量倍数为 {recent_peak}x，表示当前池子可以覆盖最近峰值需求。")
    return facts


def _normalized_capacity_facts(capacity_status: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    if not capacity_status:
        return facts
    limits = capacity_status.get("account_limits_usd") if isinstance(capacity_status.get("account_limits_usd"), dict) else {}
    if limits.get("five_hour") is not None or limits.get("seven_day") is not None:
        facts.append(
            f"当前站点 {capacity_status.get('account_type') or '未知'} 账号额度配置为 5h {limits.get('five_hour') if limits.get('five_hour') is not None else '未知'} 美元、"
            f"7d {limits.get('seven_day') if limits.get('seven_day') is not None else '未知'} 美元。"
        )
    conditions = capacity_status.get("pool_conditions") if isinstance(capacity_status.get("pool_conditions"), dict) else {}
    if any(value is not None for value in conditions.values()):
        facts.append(
            "账号状态分类："
            f"正常 {conditions.get('normal_accounts') if conditions.get('normal_accounts') is not None else '未知'}，"
            f"5h 限流 {conditions.get('five_hour_rate_limited_accounts') if conditions.get('five_hour_rate_limited_accounts') is not None else '未知'}，"
            f"7d 限流 {conditions.get('seven_day_rate_limited_accounts') if conditions.get('seven_day_rate_limited_accounts') is not None else '未知'}，"
            f"异常 {conditions.get('abnormal_accounts') if conditions.get('abnormal_accounts') is not None else '未知'}。"
        )
    return facts


def _concurrency_facts(concurrency_status: dict[str, Any]) -> list[str]:
    if not concurrency_status or concurrency_status.get("available") is not True:
        return []
    facts = [
        "当前并发容量："
        f"总容量 {concurrency_status.get('total_capacity') if concurrency_status.get('total_capacity') is not None else '未知'}，"
        f"已占用 {concurrency_status.get('actual_in_use') if concurrency_status.get('actual_in_use') is not None else '未知'}，"
        f"安全可用 {concurrency_status.get('safe_available') if concurrency_status.get('safe_available') is not None else '未知'}，"
        f"临界可用 {concurrency_status.get('near_limit_available') if concurrency_status.get('near_limit_available') is not None else '未知'}，"
        f"暂时不可用 {concurrency_status.get('temporarily_unavailable') if concurrency_status.get('temporarily_unavailable') is not None else '未知'}。"
    ]
    accounts = concurrency_status.get("accounts") if isinstance(concurrency_status.get("accounts"), dict) else {}
    if accounts.get("temporarily_unavailable"):
        facts.append(
            f"暂时不可用并发涉及 {accounts.get('temporarily_unavailable')} 个账号，其中 5h 限流 {accounts.get('five_hour_limited') or 0} 个，"
            f"短期 7d 限流 {accounts.get('short_seven_day_limited') or 0} 个。"
        )
    return facts


def _system_capacity_assessment_facts(assessment: dict[str, Any]) -> list[str]:
    if not assessment or assessment.get("ready") is not True:
        return []
    facts = [
        "主系统实时容量模型给出的结果只作为证据，Agent 仍需结合事件、人工反馈和长期记忆独立决策。"
    ]
    recommended = assessment.get("recommended_refill_accounts")
    if recommended is not None:
        facts.append(
            f"主系统实时容量测算建议补充 {recommended} 个账号，replenishment_required="
            f"{bool(assessment.get('replenishment_required'))}。"
        )
    options = assessment.get("account_type_options") if isinstance(assessment.get("account_type_options"), dict) else {}
    if options:
        compact = {
            account_type: item.get("recommended_refill_accounts")
            for account_type, item in options.items()
            if isinstance(item, dict)
        }
        facts.append(f"按账号类型拆分的主系统补号参考为 {compact}。")
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
    return facts


def _burst_facts(capacity: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    burst_multiple = _number_or_none(capacity.get("burst_1h_estimated_5h_multiple") or capacity.get("burst_1h_five_hour_multiple"))
    if burst_multiple is not None:
        if burst_multiple < 1:
            facts.append(f"突发 1h 预估 5h 容量倍数为 {burst_multiple}x，表示按当前小时速度折算后容量不足。")
        else:
            facts.append(f"突发 1h 预估 5h 容量倍数为 {burst_multiple}x，表示按当前小时速度折算后短时压力可覆盖。")
    trend_label = capacity.get("burst_trend_label") or capacity.get("burst_1h_trend_label") or capacity.get("burst_1h_trend")
    trend_strength = capacity.get("burst_trend_strength_label") or capacity.get("burst_trend_strength") or capacity.get("burst_1h_trend_strength")
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
    notable = event_windows.get("notable_patterns") if isinstance(event_windows.get("notable_patterns"), list) else []
    for item in notable[:5]:
        if isinstance(item, dict) and item.get("interpretation"):
            facts.append(f"{item.get('window') or '事件窗口'}：{item.get('interpretation')}")
    return facts[:12]


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
            f"最近一次 Agent 决策风险等级为 {last.get('severity') or '未知'}，"
            f"建议补号数为 {last.get('suggested_add_count') if last.get('suggested_add_count') is not None else '未知'}。"
        )
    if any(bool(value) for value in long_term_memory.values()):
        facts.append("存在当前池相关长期记忆摘要，可作为经验参考。")
    feedback = long_term_memory.get("operator_feedback_summaries") if isinstance(long_term_memory.get("operator_feedback_summaries"), list) else []
    if feedback:
        facts.append(f"存在 {len(feedback)} 条近期人工反馈摘要，后续判断应优先参考其中的纠正信息。")
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
    recent_peak = _number_or_none(capacity.get("recent_day_5h_peak_multiple") or capacity.get("recent_day_five_hour_peak_multiple"))
    if recent_peak is not None and recent_peak < 1:
        signals.append({"signal": "recent_peak_capacity_gap", "level": "high", "evidence": f"recent_day_5h_peak_multiple={recent_peak}"})
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


def _data_gaps(capacity: dict[str, Any], probe: dict[str, Any], event_windows: dict[str, Any]) -> list[str]:
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


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None
