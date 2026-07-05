from __future__ import annotations

from typing import Any


DECISION_SCHEMA_VERSION = "agent_decision.v1"
DECISION_TYPE = "pool_operation_decision"
MAX_SUGGESTED_ADD_COUNT = 200

ALLOWED_SEVERITIES = {"healthy", "watch", "warning", "danger", "critical"}
ALLOWED_CONFIDENCE = {"low", "medium", "high"}
ALLOWED_ACTION_TYPES = {
    "observe",
    "prepare_accounts",
    "manual_review",
    "notify_draft",
    "investigate_probe",
    "investigate_capacity",
}
BLOCKED_ACTION_TYPES = {
    "push_accounts",
    "delete_accounts",
    "buy_accounts",
    "modify_pool_config",
    "send_dingtalk",
}


def validate_agent_decision(raw: dict[str, Any], *, context_pack: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Level 1 Agent decision without replacing its business judgment."""

    warnings: list[str] = []
    adjustments: list[str] = []
    guardrail = {
        "json_object_valid": isinstance(raw, dict),
        "decision_type_valid": True,
        "schema_version_valid": True,
        "suggested_add_count_clamped": False,
        "blocked_actions": [],
        "downgraded_actions": [],
        "unknown_actions": [],
    }
    if not isinstance(raw, dict):
        raw = {}
        warnings.append("LLM decision was not an object; normalized to empty decision.")
        guardrail["json_object_valid"] = False

    if raw.get("decision_type") not in {None, DECISION_TYPE}:
        warnings.append(f"decision_type={raw.get('decision_type')} is invalid; normalized to {DECISION_TYPE}.")
        adjustments.append("decision_type_normalized")
        guardrail["decision_type_valid"] = False
    if raw.get("schema_version") not in {None, DECISION_SCHEMA_VERSION}:
        warnings.append(f"schema_version={raw.get('schema_version')} is invalid; normalized to {DECISION_SCHEMA_VERSION}.")
        adjustments.append("schema_version_normalized")
        guardrail["schema_version_valid"] = False

    severity = _enum(raw.get("severity"), ALLOWED_SEVERITIES, default="watch", warnings=warnings, field="severity")
    confidence = _enum(raw.get("confidence"), ALLOWED_CONFIDENCE, default="low", warnings=warnings, field="confidence")
    suggested_add_count = _bounded_int(
        raw.get("suggested_add_count"),
        minimum=0,
        maximum=MAX_SUGGESTED_ADD_COUNT,
        default=0,
        warnings=warnings,
        adjustments=adjustments,
        guardrail=guardrail,
        field="suggested_add_count",
    )
    should_add_accounts = _bool(raw.get("should_add_accounts"), default=suggested_add_count > 0)
    should_alert = _bool(raw.get("should_alert"), default=severity in {"danger", "critical"})
    requires_human_confirm = _bool(
        raw.get("requires_human_confirm"),
        default=severity in {"danger", "critical"} or suggested_add_count >= 50 or should_alert,
    )
    if suggested_add_count >= 50 and not requires_human_confirm:
        requires_human_confirm = True
        warnings.append("Large suggested_add_count forced requires_human_confirm=true.")
        adjustments.append("large_add_count_forced_human_confirm")

    recommended_actions = _normalize_actions(raw.get("recommended_actions"), warnings=warnings, adjustments=adjustments, guardrail=guardrail)
    if not recommended_actions:
        recommended_actions = [_default_action(should_add_accounts=should_add_accounts, suggested_add_count=suggested_add_count)]
    if guardrail["blocked_actions"] and not requires_human_confirm:
        requires_human_confirm = True
        warnings.append("Blocked high-risk actions forced requires_human_confirm=true.")
        adjustments.append("blocked_actions_forced_human_confirm")

    data_gaps = _string_list(raw.get("data_gaps"))
    data_quality = context_pack.get("data_quality") if isinstance(context_pack.get("data_quality"), dict) else {}
    if data_quality.get("capacity_available") is False and "容量数据不可用" not in data_gaps:
        data_gaps.append("容量数据不可用")
    if data_quality.get("probe_available") is False and "账号探测数据不可用" not in data_gaps:
        data_gaps.append("账号探测数据不可用")

    summary = _string_or_default(raw.get("summary"), _default_summary(severity, suggested_add_count))
    operator_message = _string_or_default(raw.get("operator_message"), summary)
    main_reasons = _string_list(raw.get("main_reasons"))
    risk_factors = _string_list(raw.get("risk_factors"))
    follow_up_questions = _string_list(raw.get("follow_up_questions"))[:5]
    next_observation_focus = _string_list(raw.get("next_observation_focus"))[:8]
    alert_channels = _string_list(raw.get("alert_channels"))
    if should_alert and not alert_channels:
        alert_channels = ["manual"]
    evidence_summary = _normalize_evidence_summary(raw.get("evidence_summary"))
    event_assessment = _normalize_event_assessment(raw.get("event_assessment"))
    memory_used = _normalize_memory_used(raw.get("memory_used"))

    decision = {
        "decision_type": DECISION_TYPE,
        "schema_version": DECISION_SCHEMA_VERSION,
        "severity": severity,
        "summary": summary,
        "headline": _headline_from_raw(raw, severity=severity, suggested_add_count=suggested_add_count),
        "operator_message": operator_message,
        "should_add_accounts": should_add_accounts,
        "suggested_add_count": suggested_add_count,
        "suggested_push_from_reserve_count": 0,
        "suggested_make_new_count": suggested_add_count,
        "confidence": confidence,
        "main_reasons": main_reasons,
        "risk_factors": risk_factors,
        "data_gaps": data_gaps,
        "should_alert": should_alert,
        "alert_channels": alert_channels,
        "requires_human_confirm": requires_human_confirm,
        "manual_review_required": requires_human_confirm,
        "recommended_actions": recommended_actions,
        "suggested_actions": _action_texts(recommended_actions),
        "reasons": main_reasons or risk_factors or data_gaps,
        "next_observation_focus": next_observation_focus,
        "follow_up_questions": follow_up_questions,
        "continue_decision_loop": _bool(raw.get("continue_decision_loop"), default=False),
        "evidence_summary": evidence_summary,
        "event_assessment": event_assessment,
        "memory_used": memory_used,
        "validator": {
            "status": "adjusted" if warnings or adjustments else "passed",
            "warnings": warnings,
            "adjustments": adjustments,
            "guardrail": guardrail,
            "max_suggested_add_count": MAX_SUGGESTED_ADD_COUNT,
        },
    }
    return decision


def _normalize_evidence_summary(value: Any) -> dict[str, list[str]]:
    result = {"capacity": [], "events": [], "probe": [], "memory": []}
    if not isinstance(value, dict):
        return result
    for key in result:
        result[key] = _string_list(value.get(key))[:8]
    return result


def _normalize_event_assessment(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {
            "has_recent_ban_burst": False,
            "ban_burst_window": None,
            "is_continuous_degradation": False,
            "interpretation": "",
        }
    return {
        "has_recent_ban_burst": _bool(value.get("has_recent_ban_burst"), default=False),
        "ban_burst_window": _optional_string(value.get("ban_burst_window")),
        "is_continuous_degradation": _bool(value.get("is_continuous_degradation"), default=False),
        "interpretation": _optional_string(value.get("interpretation")) or "",
    }


def _normalize_memory_used(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    items: list[dict[str, str]] = []
    for item in value[:10]:
        if not isinstance(item, dict):
            continue
        memory_id = _optional_string(item.get("memory_id"))
        reason = _optional_string(item.get("reason"))
        if memory_id or reason:
            items.append({"memory_id": memory_id or "", "reason": reason or ""})
    return items


def _normalize_actions(
    value: Any,
    *,
    warnings: list[str],
    adjustments: list[str],
    guardrail: dict[str, Any],
) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    actions: list[dict[str, Any]] = []
    for item in value[:10]:
        if isinstance(item, str):
            action = {
                "action_type": "manual_review",
                "title": item.strip(),
                "reason": item.strip(),
                "risk_level": "medium",
                "requires_human_confirm": True,
            }
        elif isinstance(item, dict):
            action_type = str(item.get("action_type") or item.get("type") or "manual_review").strip()
            forced_action_confirm = False
            if action_type in BLOCKED_ACTION_TYPES:
                warnings.append(f"Blocked high-risk action_type={action_type}; converted to manual_review.")
                adjustments.append(f"blocked_action_downgraded:{action_type}")
                guardrail["blocked_actions"].append(action_type)
                guardrail["downgraded_actions"].append({"from": action_type, "to": "manual_review"})
                action_type = "manual_review"
                forced_action_confirm = True
            if action_type not in ALLOWED_ACTION_TYPES:
                warnings.append(f"Unknown action_type={action_type}; converted to manual_review.")
                adjustments.append(f"unknown_action_downgraded:{action_type}")
                guardrail["unknown_actions"].append(action_type)
                guardrail["downgraded_actions"].append({"from": action_type, "to": "manual_review"})
                action_type = "manual_review"
                forced_action_confirm = True
            action = {
                "action_type": action_type,
                "title": _string_or_default(item.get("title"), action_type),
                "reason": _string_or_default(item.get("reason"), ""),
                "risk_level": _enum(item.get("risk_level"), {"low", "medium", "high"}, default="medium", warnings=warnings, field="risk_level"),
                "requires_human_confirm": True
                if forced_action_confirm
                else _bool(item.get("requires_human_confirm"), default=action_type in {"manual_review", "notify_draft"}),
            }
        else:
            continue
        actions.append(action)
    return actions


def _default_action(*, should_add_accounts: bool, suggested_add_count: int) -> dict[str, Any]:
    if should_add_accounts and suggested_add_count > 0:
        return {
            "action_type": "prepare_accounts",
            "title": f"准备 {suggested_add_count} 个账号",
            "reason": "Level 1 Agent 判断需要增加账号缓冲。",
            "risk_level": "medium",
            "requires_human_confirm": True,
        }
    return {
        "action_type": "observe",
        "title": "继续观察账号池状态",
        "reason": "Level 1 Agent 暂未建议立即补号。",
        "risk_level": "low",
        "requires_human_confirm": False,
    }


def _action_texts(actions: list[dict[str, Any]]) -> list[str]:
    texts: list[str] = []
    for action in actions:
        title = str(action.get("title") or "").strip()
        reason = str(action.get("reason") or "").strip()
        if title and reason and title != reason:
            texts.append(f"{title}：{reason}")
        elif title:
            texts.append(title)
        elif reason:
            texts.append(reason)
    return texts


def _headline_from_raw(raw: dict[str, Any], *, severity: str, suggested_add_count: int) -> str:
    for key in ("headline", "summary"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return _default_summary(severity, suggested_add_count)


def _default_summary(severity: str, suggested_add_count: int) -> str:
    if suggested_add_count > 0:
        return f"当前风险等级为 {severity}，建议准备 {suggested_add_count} 个账号。"
    return f"当前风险等级为 {severity}，暂不建议立即补号。"


def _bounded_int(
    value: Any,
    *,
    minimum: int,
    maximum: int,
    default: int,
    warnings: list[str],
    adjustments: list[str],
    guardrail: dict[str, Any],
    field: str,
) -> int:
    number = _strict_int_or_none(value)
    if number is None:
        warnings.append(f"{field} was missing or invalid; defaulted to {default}.")
        adjustments.append(f"{field}_defaulted")
        return default
    if number < minimum:
        warnings.append(f"{field} was below {minimum}; clamped.")
        adjustments.append(f"{field}_clamped_to_min")
        guardrail["suggested_add_count_clamped"] = True
        return minimum
    if number > maximum:
        warnings.append(f"{field} exceeded {maximum}; clamped.")
        adjustments.append(f"{field}_clamped_to_max")
        guardrail["suggested_add_count_clamped"] = True
        return maximum
    return number


def _strict_int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        sign = text[0] if text[0] in {"+", "-"} else ""
        digits = text[1:] if sign else text
        if digits.isdigit():
            return int(text)
    return None


def _enum(value: Any, allowed: set[str], *, default: str, warnings: list[str], field: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in allowed:
        return normalized
    warnings.append(f"{field} was missing or invalid; defaulted to {default}.")
    return default


def _bool(value: Any, *, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1", "是", "需要"}:
            return True
        if normalized in {"false", "no", "0", "否", "不需要"}:
            return False
    return default


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            items.append(text)
    return items


def _string_or_default(value: Any, default: str) -> str:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return default


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
