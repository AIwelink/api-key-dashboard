from __future__ import annotations

import re
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from typing import Any


SUPPORTED_TYPES = ("pro", "plus", "k12", "team")
MAX_QUOTA_SOURCE_AGE = timedelta(minutes=5)
RATE_LIMIT_RECOVERY_DELAY = timedelta(minutes=30)

_HTTP_429_PATTERN = re.compile(r"(?<!\d)429(?!\d)")
_RATE_LIMITED_STATUSES = {"429", "rate_limited", "rate-limited", "rate limited"}

DEFAULT_SMART_SCHEDULING_RULES: dict[str, Any] = {
    "account_types": {
        "pro": {
            "manual_priority_min": 1000,
            "manual_priority_max": 1090,
            "system_priority_min": 991,
            "system_priority_max": 999,
            "automatic_priority": 991,
            "normal_concurrency": 30,
            "extreme_entry_percent": 95.0,
            "recovery_percent": 80.0,
            "extreme_concurrency": 100,
        },
        "plus": {
            "manual_priority_min": 200,
            "manual_priority_max": 290,
            "system_priority_min": 191,
            "system_priority_max": 199,
            "automatic_priority": 191,
            "normal_concurrency": 30,
            "extreme_entry_percent": 90.0,
            "recovery_percent": 80.0,
            "extreme_concurrency": 100,
        },
        "k12": {
            "manual_priority_min": 100,
            "manual_priority_max": 190,
            "system_priority_min": 91,
            "system_priority_max": 99,
            "automatic_priority": 91,
            "normal_concurrency": 30,
            "extreme_entry_percent": 90.0,
            "recovery_percent": 80.0,
            "extreme_concurrency": 100,
        },
        "team": {
            "manual_priority_min": 50,
            "manual_priority_max": 90,
            "system_priority_min": 41,
            "system_priority_max": 49,
            "automatic_priority": 41,
            "normal_concurrency": 30,
            "extreme_entry_percent": 90.0,
            "recovery_percent": 80.0,
            "extreme_concurrency": 100,
        },
    },
    "extreme": {
        "priority_min": 1,
        "priority_max": 20,
        "priority": 10,
    },
}

_ACCOUNT_INTEGER_FIELDS = (
    "manual_priority_min",
    "manual_priority_max",
    "system_priority_min",
    "system_priority_max",
    "automatic_priority",
    "normal_concurrency",
    "extreme_concurrency",
)
_ACCOUNT_PERCENT_FIELDS = ("extreme_entry_percent", "recovery_percent")


def default_smart_scheduling_rules() -> dict[str, Any]:
    return deepcopy(DEFAULT_SMART_SCHEDULING_RULES)


def normalize_smart_scheduling_rules(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, dict) else {}
    source_types = source.get("account_types") if isinstance(source.get("account_types"), dict) else {}
    defaults = default_smart_scheduling_rules()
    normalized_types: dict[str, dict[str, int | float]] = {}

    for account_type in SUPPORTED_TYPES:
        default_rule = defaults["account_types"][account_type]
        raw_rule = source_types.get(account_type) if isinstance(source_types.get(account_type), dict) else {}
        rule: dict[str, int | float] = {}
        for field in _ACCOUNT_INTEGER_FIELDS:
            rule[field] = _positive_int(raw_rule.get(field), default_rule[field], field)
        for field in _ACCOUNT_PERCENT_FIELDS:
            rule[field] = _percent(raw_rule.get(field), default_rule[field], field)
        _validate_account_rule(account_type, rule)
        normalized_types[account_type] = rule

    raw_extreme = source.get("extreme") if isinstance(source.get("extreme"), dict) else {}
    default_extreme = defaults["extreme"]
    extreme = {
        field: _positive_int(raw_extreme.get(field), default_extreme[field], field)
        for field in ("priority_min", "priority_max", "priority")
    }
    _validate_cross_rule_priorities(normalized_types, extreme)
    return {"account_types": normalized_types, "extreme": extreme}


def adapted_scheduling_type(value: Any) -> str | None:
    normalized = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if normalized in {"team", "bug_team", "special_team"}:
        return "team"
    if normalized in {"plus", "special_plus"}:
        return "plus"
    return normalized if normalized in {"pro", "k12"} else None


def priority_in_normal_bands(priority: int, rule: dict[str, Any]) -> bool:
    return (
        int(rule["manual_priority_min"]) <= priority <= int(rule["manual_priority_max"])
        or int(rule["system_priority_min"]) <= priority <= int(rule["system_priority_max"])
    )


def evaluate_account(
    *,
    account: dict[str, Any],
    rules: dict[str, Any],
    type_priority_enabled: bool,
    quota_acceleration_enabled: bool,
    state: dict[str, Any] | None,
    now: datetime,
    normal_priority: int | None = None,
) -> dict[str, Any]:
    normalized_rules = normalize_smart_scheduling_rules(rules)
    state = state if isinstance(state, dict) else {}
    account_type = adapted_scheduling_type(account.get("account_type") or account.get("plan_type"))
    rate_limit_detected_at = _parse_datetime(state.get("rate_limit_detected_at"))
    base = {
        "adapted_type": account_type,
        "seven_day_used_percent": None,
        "seven_day_reset_at": None,
        "quota_fresh": False,
        "rate_limit_detected_at": (
            rate_limit_detected_at.isoformat() if rate_limit_detected_at else None
        ),
    }
    if not type_priority_enabled and not quota_acceleration_enabled:
        return base | _result("skipped", reason="strategies_disabled")
    if account_type is None:
        return base | _result("skipped", reason="unsupported_account_type")

    rule = normalized_rules["account_types"][account_type]
    effective_normal_priority = (
        int(normal_priority)
        if normal_priority is not None
        else int(rule["automatic_priority"])
    )
    quota = _seven_day_quota(account, now=now)
    base.update(
        {
            "seven_day_used_percent": quota["percent"],
            "seven_day_reset_at": quota["reset_at"],
            "quota_fresh": quota["fresh"],
        }
    )
    state_mode = str(state.get("mode") or "")
    managed_modes = {"extreme", "rate_limit_pending", "rate_limited_cooldown"}

    if state_mode in managed_modes and not quota_acceleration_enabled:
        return base | _result("held", reason="quota_strategy_disabled_extreme_held")

    if state_mode in managed_modes:
        previous_reset = _datetime_identity(state.get("seven_day_reset_at"))
        reset_changed = bool(
            quota["reason"] == "quota_ready"
            and previous_reset
            and previous_reset != quota["reset_at"]
        )
        recovered = bool(
            quota["reason"] == "quota_ready"
            and float(quota["percent"]) < float(rule["recovery_percent"])
        )
        if reset_changed or recovered:
            return (base | {"rate_limit_detected_at": None}) | _target_result(
                account,
                priority=effective_normal_priority,
                concurrency=int(rule["normal_concurrency"]),
                strategy="quota_recovery",
                mode="normal",
                reason="seven_day_window_reset" if reset_changed else "quota_recovered",
            )
        if state_mode == "rate_limited_cooldown":
            return base | _target_result(
                account,
                priority=effective_normal_priority,
                concurrency=int(rule["normal_concurrency"]),
                strategy="rate_limit_recovery",
                mode="rate_limited_cooldown",
                reason="rate_limit_cooldown_held",
            )
        if state_mode == "rate_limit_pending":
            detected_at = rate_limit_detected_at or now.astimezone(UTC)
            base["rate_limit_detected_at"] = detected_at.isoformat()
            if now.astimezone(UTC) - detected_at >= RATE_LIMIT_RECOVERY_DELAY:
                return base | _target_result(
                    account,
                    priority=effective_normal_priority,
                    concurrency=int(rule["normal_concurrency"]),
                    strategy="rate_limit_recovery",
                    mode="rate_limited_cooldown",
                    reason="rate_limit_delay_elapsed",
                )
            return base | _target_result(
                account,
                priority=int(normalized_rules["extreme"]["priority"]),
                concurrency=int(rule["extreme_concurrency"]),
                strategy="rate_limit_recovery",
                mode="rate_limit_pending",
                reason="rate_limit_delay_pending",
            )
        if _account_is_rate_limited(account):
            base["rate_limit_detected_at"] = now.astimezone(UTC).isoformat()
            return base | _target_result(
                account,
                priority=int(normalized_rules["extreme"]["priority"]),
                concurrency=int(rule["extreme_concurrency"]),
                strategy="rate_limit_recovery",
                mode="rate_limit_pending",
                reason="rate_limit_delay_started",
            )
        if quota["reason"] != "quota_ready":
            suffix = "stale" if quota["reason"] == "quota_stale" else "missing"
            return base | _result("held", reason=f"quota_{suffix}_extreme_held")
        return base | _target_result(
            account,
            priority=int(normalized_rules["extreme"]["priority"]),
            concurrency=int(rule["extreme_concurrency"]),
            strategy="quota_acceleration",
            mode="extreme",
            reason="quota_extreme_continues",
        )

    if (
        quota_acceleration_enabled
        and quota["reason"] == "quota_ready"
        and float(quota["percent"]) >= float(rule["extreme_entry_percent"])
    ):
        return base | _target_result(
            account,
            priority=int(normalized_rules["extreme"]["priority"]),
            concurrency=int(rule["extreme_concurrency"]),
            strategy="quota_acceleration",
            mode="extreme",
            reason="quota_threshold_reached",
        )

    if type_priority_enabled:
        current_priority = _optional_int(account.get("priority"))
        target_priority = (
            effective_normal_priority
            if normal_priority is not None
            else current_priority
            if current_priority is not None and priority_in_normal_bands(current_priority, rule)
            else effective_normal_priority
        )
        reason = (
            "type_queue_positioned"
            if normal_priority is not None
            else "type_normalized"
        )
        if normal_priority is None and quota_acceleration_enabled and quota["reason"] == "quota_stale":
            reason = "quota_stale_type_normalized"
        elif normal_priority is None and quota_acceleration_enabled and quota["reason"] != "quota_ready":
            reason = "quota_missing_type_normalized"
        return base | _target_result(
            account,
            priority=target_priority,
            concurrency=int(rule["normal_concurrency"]),
            strategy="type_priority",
            mode="normal",
            reason=reason,
        )

    if quota["reason"] == "quota_stale":
        return base | _result("skipped", reason="quota_stale")
    if quota["reason"] != "quota_ready":
        return base | _result("skipped", reason="quota_missing")
    return base | _result("skipped", reason="quota_below_threshold")


def build_type_priority_queue(
    entries: list[dict[str, Any]],
    *,
    rules: dict[str, Any],
    now: datetime,
) -> dict[str, dict[str, Any]]:
    normalized_rules = normalize_smart_scheduling_rules(rules)
    by_type: dict[str, list[dict[str, Any]]] = {
        account_type: [] for account_type in SUPPORTED_TYPES
    }

    for entry in entries:
        if entry.get("type_priority_enabled") is not True:
            continue
        account = entry.get("account")
        if not isinstance(account, dict):
            continue
        remote_account_id = entry.get("remote_account_id")
        if remote_account_id is None:
            continue
        account_type = adapted_scheduling_type(
            account.get("account_type") or account.get("plan_type")
        )
        if account_type is None:
            continue
        state = entry.get("state") if isinstance(entry.get("state"), dict) else None
        state_mode = str((state or {}).get("mode") or "")
        preliminary = evaluate_account(
            account=account,
            rules=normalized_rules,
            type_priority_enabled=True,
            quota_acceleration_enabled=(
                entry.get("quota_acceleration_enabled") is True
            ),
            state=state,
            now=now,
        )
        if preliminary.get("mode") in {"extreme", "rate_limit_pending"}:
            continue
        if (
            state_mode in {"extreme", "rate_limit_pending"}
            and preliminary.get("target") is None
        ):
            continue
        created_at = _parse_datetime(account.get("created_at"))
        by_type[account_type].append(
            {
                "remote_account_id": remote_account_id,
                "account": account,
                "created_at": created_at,
                "usable": _queue_account_is_usable(
                    account,
                    preliminary_mode=str(preliminary.get("mode") or state_mode),
                ),
            }
        )

    plan: dict[str, dict[str, Any]] = {}
    for account_type, typed_entries in by_type.items():
        rule = normalized_rules["account_types"][account_type]
        ordered = sorted(typed_entries, key=_queue_sort_key)
        for queue_index, entry in enumerate(ordered):
            created_at = entry["created_at"]
            plan[str(entry["remote_account_id"])] = {
                "priority": min(
                    int(rule["manual_priority_min"]) + queue_index,
                    int(rule["manual_priority_max"]),
                ),
                "queue_index": queue_index,
                "queue_partition": (
                    "usable" if entry["usable"] else "temporarily_unusable"
                ),
                "queue_created_at": (
                    created_at.isoformat() if created_at is not None else None
                ),
            }
    return plan


def _queue_account_is_usable(
    account: dict[str, Any],
    *,
    preliminary_mode: str,
) -> bool:
    if preliminary_mode in {"rate_limit_pending", "rate_limited_cooldown"}:
        return False
    status = str(account.get("status") or "").strip().lower()
    if status not in {"active", "ok", "healthy", "available"}:
        return False
    if account.get("schedulable") is not True:
        return False
    if str(account.get("error_message") or "").strip():
        return False
    return not _account_is_rate_limited(account)


def _queue_sort_key(entry: dict[str, Any]) -> tuple[Any, ...]:
    created_at = entry.get("created_at")
    remote_account_id = entry.get("remote_account_id")
    parsed_id = _optional_int(remote_account_id)
    return (
        0 if entry.get("usable") else 1,
        0 if created_at is not None else 1,
        created_at or datetime.max.replace(tzinfo=UTC),
        0 if parsed_id is not None else 1,
        parsed_id if parsed_id is not None else 0,
        str(remote_account_id),
    )


def _account_is_rate_limited(account: dict[str, Any]) -> bool:
    status = str(account.get("status") or "").strip().lower()
    if status in _RATE_LIMITED_STATUSES:
        return True
    return bool(_HTTP_429_PATTERN.search(str(account.get("error_message") or "")))


def _result(
    status: str,
    *,
    reason: str,
    target: dict[str, int] | None = None,
    strategy: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    return {
        "status": status,
        "reason": reason,
        "target": target,
        "strategy": strategy,
        "mode": mode,
    }


def _target_result(
    account: dict[str, Any],
    *,
    priority: int,
    concurrency: int,
    strategy: str,
    mode: str,
    reason: str,
) -> dict[str, Any]:
    target = {"priority": priority, "concurrency": concurrency}
    current = {
        "priority": _optional_int(account.get("priority")),
        "concurrency": _optional_int(account.get("concurrency")),
    }
    status = "unchanged" if current == target else "change"
    return _result(
        status,
        reason=reason,
        target=target,
        strategy=strategy,
        mode=mode,
    )


def _seven_day_quota(account: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    usage = account.get("usage_snapshot") if isinstance(account.get("usage_snapshot"), dict) else {}
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    percent = _optional_float(
        _first_present(usage, extra, account, field="codex_7d_used_percent")
    )
    sampled_at = _parse_datetime(
        _first_present(
            usage,
            extra,
            account,
            field="codex_usage_synced_at",
            fallback_field="codex_usage_updated_at",
        )
    )
    reset_at = _parse_datetime(
        _first_present(usage, extra, account, field="codex_7d_reset_at")
    )
    reset_identity = reset_at.isoformat() if reset_at is not None else None
    if percent is None or not 0 <= percent <= 100 or sampled_at is None or reset_at is None:
        return {
            "percent": percent,
            "reset_at": reset_identity,
            "fresh": False,
            "reason": "quota_missing",
        }
    sampled_at = sampled_at.astimezone(UTC)
    observed_now = now.astimezone(UTC)
    if observed_now - sampled_at > MAX_QUOTA_SOURCE_AGE:
        return {
            "percent": percent,
            "reset_at": reset_identity,
            "fresh": False,
            "reason": "quota_stale",
        }
    return {
        "percent": percent,
        "reset_at": reset_identity,
        "fresh": True,
        "reason": "quota_ready",
    }


def _validate_account_rule(account_type: str, rule: dict[str, int | float]) -> None:
    if int(rule["manual_priority_min"]) > int(rule["manual_priority_max"]):
        raise ValueError(f"{account_type} manual priority bands are inverted")
    if int(rule["system_priority_min"]) > int(rule["system_priority_max"]):
        raise ValueError(f"{account_type} system priority bands are inverted")
    if int(rule["system_priority_max"]) >= int(rule["manual_priority_min"]):
        raise ValueError(f"{account_type} priority bands must be ordered and non-overlapping")
    if not int(rule["system_priority_min"]) <= int(rule["automatic_priority"]) <= int(
        rule["system_priority_max"]
    ):
        raise ValueError(f"{account_type} automatic priority must be inside its system band")
    if float(rule["recovery_percent"]) >= float(rule["extreme_entry_percent"]):
        raise ValueError(f"{account_type} recovery threshold must be below its extreme entry threshold")


def _validate_cross_rule_priorities(
    rules: dict[str, dict[str, int | float]],
    extreme: dict[str, int],
) -> None:
    if extreme["priority_min"] > extreme["priority_max"]:
        raise ValueError("extreme priority band is inverted")
    if not extreme["priority_min"] <= extreme["priority"] <= extreme["priority_max"]:
        raise ValueError("extreme priority must be inside the extreme band")

    intervals: list[tuple[int, int, str]] = []
    for account_type, rule in rules.items():
        intervals.extend(
            [
                (
                    int(rule["system_priority_min"]),
                    int(rule["system_priority_max"]),
                    f"{account_type} system",
                ),
                (
                    int(rule["manual_priority_min"]),
                    int(rule["manual_priority_max"]),
                    f"{account_type} manual",
                ),
            ]
        )
    intervals.sort()
    if extreme["priority_max"] >= intervals[0][0]:
        raise ValueError("extreme priority band must be ahead of all normal bands")
    for previous, current in zip(intervals, intervals[1:]):
        if previous[1] >= current[0]:
            raise ValueError(f"priority band overlap: {previous[2]} and {current[2]}")


def _positive_int(value: Any, fallback: int, field: str) -> int:
    if value is None:
        return int(fallback)
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a positive integer")
    try:
        parsed_float = float(value)
        parsed = int(parsed_float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be a positive integer") from exc
    if parsed <= 0 or parsed != parsed_float:
        raise ValueError(f"{field} must be a positive integer")
    return parsed


def _percent(value: Any, fallback: float, field: str) -> float:
    if value is None:
        return float(fallback)
    if isinstance(value, bool):
        raise ValueError(f"{field} must be between 0 and 100")
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{field} must be between 0 and 100") from exc
    if not 0 <= parsed <= 100:
        raise ValueError(f"{field} must be between 0 and 100")
    return parsed


def _optional_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed_float = float(value)
        parsed = int(parsed_float)
    except (TypeError, ValueError, OverflowError):
        return None
    return parsed if parsed == parsed_float else None


def _optional_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _datetime_identity(value: Any) -> str | None:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _first_present(
    *sources: dict[str, Any],
    field: str,
    fallback_field: str | None = None,
) -> Any:
    for source in sources:
        if source.get(field) is not None:
            return source[field]
        if fallback_field and source.get(fallback_field) is not None:
            return source[fallback_field]
    return None
