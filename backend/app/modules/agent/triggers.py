from __future__ import annotations

from typing import Any


TRIGGER_MANUAL_ANALYZE = "manual_analyze"
TRIGGER_MANUAL_CHAT = "manual_chat"
TRIGGER_SCHEDULER_PATROL = "scheduler_patrol"
TRIGGER_SCHEDULER_TASK_DUE = "scheduler_task_due"
TRIGGER_SCHEDULER_REVIEW_DUE = "scheduler_review_due"
TRIGGER_EVENT_SPIKE = "event_spike"
TRIGGER_MEMORY_DAILY_SUMMARY = "memory_daily_summary"
TRIGGER_MEMORY_WEEKLY_SUMMARY = "memory_weekly_summary"
TRIGGER_NOTIFICATION_DISPATCH = "notification_dispatch"

SCHEDULER_TRIGGERS = {
    TRIGGER_SCHEDULER_PATROL,
    TRIGGER_SCHEDULER_TASK_DUE,
    TRIGGER_SCHEDULER_REVIEW_DUE,
    TRIGGER_EVENT_SPIKE,
    TRIGGER_MEMORY_DAILY_SUMMARY,
    TRIGGER_MEMORY_WEEKLY_SUMMARY,
    TRIGGER_NOTIFICATION_DISPATCH,
}

AGENT_RUN_TRIGGERS = {
    TRIGGER_MANUAL_ANALYZE,
    TRIGGER_MANUAL_CHAT,
    *SCHEDULER_TRIGGERS,
}

TRIGGER_CONTRACTS: dict[str, dict[str, Any]] = {
    TRIGGER_SCHEDULER_PATROL: {
        "creates_agent_run": True,
        "calls_llm_decision": True,
        "updates_task": "maybe",
        "writes_long_term_memory": False,
        "allows_notification_send": False,
        "required_metadata": ["site_id", "pool_id", "scheduler_tick_id"],
    },
    TRIGGER_SCHEDULER_TASK_DUE: {
        "creates_agent_run": True,
        "calls_llm_decision": True,
        "updates_task": True,
        "writes_long_term_memory": False,
        "allows_notification_send": False,
        "required_metadata": ["task_id", "pool_id", "scheduler_tick_id"],
    },
    TRIGGER_SCHEDULER_REVIEW_DUE: {
        "creates_agent_run": True,
        "calls_llm_decision": "optional",
        "updates_task": True,
        "writes_long_term_memory": "decision_review",
        "allows_notification_send": False,
        "required_metadata": ["task_id", "current_decision_id", "scheduler_tick_id"],
    },
    TRIGGER_EVENT_SPIKE: {
        "creates_agent_run": True,
        "calls_llm_decision": True,
        "updates_task": True,
        "writes_long_term_memory": False,
        "allows_notification_send": False,
        "required_metadata": ["event_trigger_id", "signal", "pool_id", "scheduler_tick_id"],
    },
    TRIGGER_MEMORY_DAILY_SUMMARY: {
        "creates_agent_run": "optional",
        "calls_llm_decision": True,
        "updates_task": False,
        "writes_long_term_memory": "pool_daily_summary",
        "allows_notification_send": False,
        "required_metadata": ["memory_type", "period_start", "period_end"],
    },
    TRIGGER_MEMORY_WEEKLY_SUMMARY: {
        "creates_agent_run": "optional",
        "calls_llm_decision": True,
        "updates_task": False,
        "writes_long_term_memory": "pool_weekly_summary_or_survival_pattern",
        "allows_notification_send": False,
        "required_metadata": ["memory_type", "period_start", "period_end"],
    },
    TRIGGER_NOTIFICATION_DISPATCH: {
        "creates_agent_run": "optional",
        "calls_llm_decision": False,
        "updates_task": True,
        "writes_long_term_memory": False,
        "allows_notification_send": "policy_allowed",
        "required_metadata": ["task_id", "source_decision_id"],
        "required_any_metadata": [["notification_event_id", "dispatch_error"]],
    },
}

TRIGGER_SAFETY_BOUNDARY = {
    "writes_business_tables": False,
    "refreshes_sub2api": False,
    "starts_account_probe": False,
    "pushes_accounts": False,
    "buys_accounts": False,
    "deletes_accounts": False,
    "bypasses_llm_business_decision": False,
    "bypasses_task_state_machine": False,
    "bypasses_notification_policy": False,
}


def normalize_agent_trigger(trigger: str | None) -> str:
    normalized = str(trigger or "").strip()
    return normalized or "manual"


def is_scheduler_trigger(trigger: str | None) -> bool:
    return normalize_agent_trigger(trigger) in SCHEDULER_TRIGGERS


def trigger_contract(trigger: str | None) -> dict[str, Any]:
    normalized = normalize_agent_trigger(trigger)
    contract = TRIGGER_CONTRACTS.get(normalized, {})
    return {
        "trigger": normalized,
        "known_trigger": normalized in AGENT_RUN_TRIGGERS,
        "is_scheduler_trigger": normalized in SCHEDULER_TRIGGERS,
        "safety_boundary": dict(TRIGGER_SAFETY_BOUNDARY),
        "audit_required": bool(normalized in SCHEDULER_TRIGGERS),
        **contract,
    }


def build_trigger_metadata(
    *,
    trigger: str | None,
    metadata: dict[str, Any] | None = None,
    pool_id: str | None = None,
    site_id: str | None = None,
    task_id: str | None = None,
    scheduler_tick_id: str | None = None,
    event_trigger_id: str | None = None,
    signal: str | None = None,
    memory_type: str | None = None,
    period_start: str | None = None,
    period_end: str | None = None,
    current_decision_id: str | None = None,
    source_decision_id: str | None = None,
    notification_event_id: str | None = None,
    dispatch_error: str | None = None,
) -> dict[str, Any]:
    normalized = normalize_agent_trigger(trigger)
    merged = dict(metadata or {})
    supplied = {
        "pool_id": pool_id,
        "site_id": site_id,
        "task_id": task_id,
        "scheduler_tick_id": scheduler_tick_id,
        "event_trigger_id": event_trigger_id,
        "signal": signal,
        "memory_type": memory_type,
        "period_start": period_start,
        "period_end": period_end,
        "current_decision_id": current_decision_id,
        "source_decision_id": source_decision_id,
        "notification_event_id": notification_event_id,
        "dispatch_error": dispatch_error,
    }
    for key, value in supplied.items():
        if _present(value) and not _present(merged.get(key)):
            merged[key] = value

    contract = trigger_contract(normalized)
    missing = _missing_required_metadata(contract, merged)
    merged.setdefault("trigger", normalized)
    merged.setdefault("trigger_source", "agent_scheduler" if contract["is_scheduler_trigger"] else "manual")
    merged.setdefault("auto_started", bool(contract["is_scheduler_trigger"]))
    merged.setdefault("trigger_contract", contract)
    merged.setdefault("trigger_contract_valid", not missing)
    if missing:
        merged["trigger_contract_missing"] = missing
    return merged


def validate_trigger_metadata(trigger: str | None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = normalize_agent_trigger(trigger)
    contract = trigger_contract(normalized)
    payload = metadata or {}
    missing = _missing_required_metadata(contract, payload)
    return {
        "ok": not missing,
        "trigger": normalized,
        "missing": missing,
        "contract": contract,
    }


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _missing_required_metadata(contract: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    missing = [key for key in contract.get("required_metadata", []) if not _present(payload.get(key))]
    for group in contract.get("required_any_metadata", []) or []:
        if isinstance(group, list) and group and not any(_present(payload.get(key)) for key in group):
            missing.append("|".join(str(key) for key in group))
    return missing
