from __future__ import annotations

import math
from collections import defaultdict
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Iterable


EVALUATION_RETENTION_DAYS = 180
SHANGHAI_TZ = timezone(timedelta(hours=8))
SUMMARY_WINDOWS = (("24h", timedelta(hours=24)), ("7d", timedelta(days=7)), ("28d", timedelta(days=28)))
HORIZON_BUCKETS = (
    ("1h", "1h", 1, 1),
    ("2-3h", "2-3h", 2, 3),
    ("4-6h", "4-6h", 4, 6),
    ("7-12h", "7-12h", 7, 12),
    ("13-24h", "13-24h", 13, 24),
)


def build_hourly_evaluation(
    forecast: dict[str, Any],
    point: dict[str, Any],
    *,
    actual_account_cost: float,
    actual_requests: float,
    actual_total_tokens: float,
    evaluated_at: datetime,
    status: str,
) -> dict[str, Any]:
    target_at = _as_utc(point.get("target_at"), field_name="target_at")
    evaluated_at = _as_utc(evaluated_at, field_name="evaluated_at")
    horizon = _positive_int(point.get("horizon"), field_name="horizon")
    predicted_p50 = _nonnegative(point.get("p50"), field_name="p50")
    predicted_p90 = max(predicted_p50, _nonnegative(point.get("p90"), field_name="p90"))
    actual = _nonnegative(actual_account_cost, field_name="actual_account_cost")
    normalized_status = _status(status)
    error_p50 = predicted_p50 - actual
    error_p90 = predicted_p90 - actual
    forecast_id = str(forecast.get("_id") or "").strip()
    if not forecast_id:
        raise ValueError("forecast _id is required")
    local_target = target_at.astimezone(SHANGHAI_TZ)
    document = {
        "_id": f"hourly:{forecast_id}:{horizon}",
        "kind": "hourly",
        "status": normalized_status,
        "site_id": str(forecast.get("site_id") or ""),
        "group_id": _positive_int(forecast.get("group_id"), field_name="group_id"),
        "forecast_id": forecast_id,
        "model": str(forecast.get("model") or "unknown"),
        "version": str(forecast.get("version") or "unknown"),
        "issued_at": _as_utc(
            forecast.get("generated_at") or forecast.get("as_of"),
            field_name="issued_at",
        ),
        "target_at": target_at,
        "horizon": horizon,
        "predicted_p50": _rounded(predicted_p50),
        "predicted_p90": _rounded(predicted_p90),
        "candidate_count": max(0, int(point.get("candidate_count") or 0)),
        "forecast_source": str(point.get("source") or "unknown"),
        "actual_account_cost": _rounded(actual),
        "actual_requests": _rounded(_nonnegative(actual_requests, field_name="actual_requests")),
        "actual_total_tokens": _rounded(_nonnegative(actual_total_tokens, field_name="actual_total_tokens")),
        "error_p50": _rounded(error_p50),
        "error_p90": _rounded(error_p90),
        "absolute_error_p50": _rounded(abs(error_p50)),
        "absolute_error_p90": _rounded(abs(error_p90)),
        "p90_covered": actual <= predicted_p90,
        "pinball_loss_p50": _rounded(_pinball_loss(actual, predicted_p50, 0.5)),
        "pinball_loss_p90": _rounded(_pinball_loss(actual, predicted_p90, 0.9)),
        "target_local_hour": local_target.hour,
        "day_type": "weekend" if local_target.weekday() >= 5 else "weekday",
        "pressure_stage": str(forecast.get("pressure_stage") or "unknown"),
        "capacity_constrained": bool(forecast.get("capacity_constrained", False)),
        "evaluated_at": evaluated_at,
        "expires_at": target_at + timedelta(days=EVALUATION_RETENTION_DAYS),
    }
    if normalized_status == "final":
        document["finalized_at"] = evaluated_at
    return document


def build_nowcast_evaluation(
    sample: dict[str, Any],
    *,
    actual_account_cost: float,
    actual_requests: float,
    actual_total_tokens: float,
    evaluated_at: datetime,
    status: str,
) -> dict[str, Any]:
    metrics = sample.get("metrics") if isinstance(sample.get("metrics"), dict) else {}
    if metrics.get("forecast_nowcast_applied") is not True:
        raise ValueError("capacity sample does not contain an applied nowcast")
    sample_id = str(sample.get("_id") or "").strip()
    if not sample_id:
        raise ValueError("capacity sample _id is required")
    issued_at = _as_utc(sample.get("sampled_at") or sample.get("bucket_at"), field_name="issued_at")
    target_at = issued_at.replace(minute=0, second=0, microsecond=0)
    evaluated_at = _as_utc(evaluated_at, field_name="evaluated_at")
    observed = _nonnegative(
        metrics.get("forecast_current_hour_observed_usd"),
        field_name="forecast_current_hour_observed_usd",
    )
    predicted_model = _nonnegative(
        metrics.get("forecast_current_hour_model_remaining_usd"),
        field_name="forecast_current_hour_model_remaining_usd",
    )
    predicted_realtime = _nonnegative(
        metrics.get("forecast_current_hour_realtime_remaining_usd"),
        field_name="forecast_current_hour_realtime_remaining_usd",
    )
    predicted_selected = _nonnegative(
        metrics.get("forecast_current_hour_selected_remaining_usd"),
        field_name="forecast_current_hour_selected_remaining_usd",
    )
    actual = _nonnegative(actual_account_cost, field_name="actual_account_cost")
    actual_remaining = max(0.0, actual - observed)
    error_model = predicted_model - actual_remaining
    error_realtime = predicted_realtime - actual_remaining
    error_selected = predicted_selected - actual_remaining
    normalized_status = _status(status)
    constrained, constraint_reasons = capacity_constraint_from_metrics(metrics)
    local_target = target_at.astimezone(SHANGHAI_TZ)
    document = {
        "_id": f"nowcast:{sample_id}",
        "kind": "nowcast",
        "status": normalized_status,
        "site_id": str(sample.get("site_id") or ""),
        "group_id": _positive_int(sample.get("group_id"), field_name="group_id"),
        "capacity_sample_id": sample_id,
        "model": str(metrics.get("forecast_model") or "unknown"),
        "version": str(metrics.get("forecast_version") or "unknown"),
        "forecast_as_of": _optional_utc(metrics.get("forecast_as_of")),
        "issued_at": issued_at,
        "target_at": target_at,
        "observed_cost_at_issue": _rounded(observed),
        "predicted_model_remaining": _rounded(predicted_model),
        "predicted_realtime_remaining": _rounded(predicted_realtime),
        "predicted_selected_remaining": _rounded(predicted_selected),
        "realtime_burn_source": str(metrics.get("realtime_burn_source") or "unknown"),
        "actual_account_cost": _rounded(actual),
        "actual_requests": _rounded(_nonnegative(actual_requests, field_name="actual_requests")),
        "actual_total_tokens": _rounded(_nonnegative(actual_total_tokens, field_name="actual_total_tokens")),
        "actual_remaining": _rounded(actual_remaining),
        "error_model_remaining": _rounded(error_model),
        "error_realtime_remaining": _rounded(error_realtime),
        "error_selected_remaining": _rounded(error_selected),
        "absolute_error_model_remaining": _rounded(abs(error_model)),
        "absolute_error_realtime_remaining": _rounded(abs(error_realtime)),
        "absolute_error_selected_remaining": _rounded(abs(error_selected)),
        "target_local_hour": local_target.hour,
        "day_type": "weekend" if local_target.weekday() >= 5 else "weekday",
        "pressure_stage": str(metrics.get("pressure_stage") or "unknown"),
        "capacity_constrained": constrained,
        "constraint_reasons": constraint_reasons,
        "evaluated_at": evaluated_at,
        "expires_at": target_at + timedelta(days=EVALUATION_RETENTION_DAYS),
    }
    if document["forecast_as_of"] is None:
        document.pop("forecast_as_of")
    if normalized_status == "final":
        document["finalized_at"] = evaluated_at
    return document


def summarize_forecast_accuracy(
    evaluations: Iterable[dict[str, Any]],
    *,
    site_id: str,
    group_id: int,
    now: datetime,
) -> dict[str, Any]:
    evaluated_at = _as_utc(now, field_name="now")
    final_items = [item for item in evaluations if item.get("status") == "final"]
    latest = max(final_items, key=_model_sort_at, default=None)
    model = str((latest or {}).get("model") or "")
    version = str((latest or {}).get("version") or "")
    current_items = [
        item
        for item in final_items
        if str(item.get("model") or "") == model and str(item.get("version") or "") == version
    ] if latest else []
    windows = {
        key: _summarize_window(
            [item for item in current_items if _target_at(item) >= evaluated_at - duration]
        )
        for key, duration in SUMMARY_WINDOWS
    }
    model_versions = _model_version_counts(final_items)
    last_finalized_at = max(
        (_optional_utc(item.get("finalized_at")) for item in current_items),
        default=None,
        key=lambda value: value or datetime.min.replace(tzinfo=UTC),
    )
    return {
        "status": "ready" if current_items else "waiting",
        "site_id": str(site_id),
        "group_id": int(group_id),
        "model": model or None,
        "version": version or None,
        "updated_at": evaluated_at,
        "last_finalized_at": last_finalized_at,
        "model_versions": model_versions,
        "windows": windows,
    }


def _summarize_window(items: list[dict[str, Any]]) -> dict[str, Any]:
    hourly = [item for item in items if item.get("kind") == "hourly"]
    nowcast = [item for item in items if item.get("kind") == "nowcast"]
    hourly_metrics = _hourly_metrics(hourly)
    nowcast_metrics = _nowcast_metrics(nowcast)
    horizon_buckets = []
    for key, label, start, end in HORIZON_BUCKETS:
        bucket_items = [item for item in hourly if start <= int(item.get("horizon") or 0) <= end]
        horizon_buckets.append({"key": key, "label": label, **_hourly_metrics(bucket_items)})
    return {
        "hourly_sample_count": len(hourly),
        "nowcast_sample_count": len(nowcast),
        **hourly_metrics,
        **nowcast_metrics,
        "horizon_buckets": horizon_buckets,
        "segments": {
            "local_hours": _segment_hourly(hourly, lambda item: f"{int(item.get('target_local_hour') or 0):02d}"),
            "day_types": _segment_hourly(hourly, lambda item: str(item.get("day_type") or "unknown")),
            "pressure_stages": _segment_hourly(hourly, lambda item: str(item.get("pressure_stage") or "unknown")),
            "capacity_constraint": _segment_hourly(
                hourly,
                lambda item: "constrained" if item.get("capacity_constrained") is True else "unconstrained",
            ),
        },
    }


def _hourly_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    actual_total = sum(_number(item.get("actual_account_cost")) for item in items)
    absolute_total = sum(_number(item.get("absolute_error_p50")) for item in items)
    signed_total = sum(_number(item.get("error_p50")) for item in items)
    count = len(items)
    return {
        "sample_count": count,
        "p50_wape_percent": _percent_ratio(absolute_total, actual_total),
        "p50_bias_percent": _percent_ratio(signed_total, actual_total),
        "p50_mae_usd": _mean(item.get("absolute_error_p50") for item in items),
        "p90_coverage_percent": _rounded(
            100 * sum(1 for item in items if item.get("p90_covered") is True) / count
        ) if count else None,
        "p90_pinball_loss_usd": _mean(item.get("pinball_loss_p90") for item in items),
    }


def _nowcast_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    actual_total = sum(_number(item.get("actual_remaining")) for item in items)
    return {
        "nowcast_selected_wape_percent": _percent_ratio(
            sum(_number(item.get("absolute_error_selected_remaining")) for item in items),
            actual_total,
        ),
        "nowcast_model_wape_percent": _percent_ratio(
            sum(_number(item.get("absolute_error_model_remaining")) for item in items),
            actual_total,
        ),
        "nowcast_realtime_wape_percent": _percent_ratio(
            sum(_number(item.get("absolute_error_realtime_remaining")) for item in items),
            actual_total,
        ),
        "nowcast_selected_bias_percent": _percent_ratio(
            sum(_number(item.get("error_selected_remaining")) for item in items),
            actual_total,
        ),
    }


def _segment_hourly(items: list[dict[str, Any]], key_fn: Any) -> dict[str, dict[str, Any]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[key_fn(item)].append(item)
    return {key: _hourly_metrics(values) for key, values in sorted(grouped.items())}


def _model_version_counts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: defaultdict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        grouped[(str(item.get("model") or "unknown"), str(item.get("version") or "unknown"))].append(item)
    result = []
    for (model, version), values in grouped.items():
        result.append(
            {
                "model": model,
                "version": version,
                "sample_count": len(values),
                "last_evaluated_at": max(_evaluation_sort_at(item) for item in values),
            }
        )
    return sorted(result, key=lambda item: item["last_evaluated_at"], reverse=True)


def capacity_constraint_from_metrics(metrics: dict[str, Any]) -> tuple[bool, list[str]]:
    reasons = []
    coverage = _optional_number(metrics.get("concurrency_coverage"))
    if coverage is not None and coverage < 1:
        reasons.append("concurrency_saturated")
    if _number(metrics.get("pool_five_hour_rate_limited_accounts")) > 0:
        reasons.append("five_hour_rate_limited")
    if _number(metrics.get("pool_seven_day_rate_limited_accounts")) > 0:
        reasons.append("seven_day_rate_limited")
    if str(metrics.get("health_status") or "") in {"danger", "exhausted"}:
        reasons.append("capacity_health_risk")
    return bool(reasons), reasons


def _pinball_loss(actual: float, predicted: float, quantile: float) -> float:
    residual = actual - predicted
    return quantile * residual if residual >= 0 else (1 - quantile) * -residual


def _mean(values: Iterable[Any]) -> float | None:
    normalized = [_number(value) for value in values]
    return _rounded(sum(normalized) / len(normalized)) if normalized else None


def _percent_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0:
        return None
    return _rounded(100 * numerator / denominator)


def _evaluation_sort_at(item: dict[str, Any]) -> datetime:
    return _optional_utc(item.get("evaluated_at")) or datetime.min.replace(tzinfo=UTC)


def _model_sort_at(item: dict[str, Any]) -> datetime:
    return _optional_utc(item.get("issued_at")) or _evaluation_sort_at(item)


def _target_at(item: dict[str, Any]) -> datetime:
    return _optional_utc(item.get("target_at")) or datetime.min.replace(tzinfo=UTC)


def _status(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized not in {"provisional", "final"}:
        raise ValueError("status must be provisional or final")
    return normalized


def _positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field_name} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if number <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return number


def _nonnegative(value: Any, *, field_name: str) -> float:
    number = _optional_number(value)
    if number is None or number < 0:
        raise ValueError(f"{field_name} must be finite and non-negative")
    return number


def _number(value: Any) -> float:
    number = _optional_number(value)
    return number if number is not None else 0.0


def _optional_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_utc(value: Any, *, field_name: str) -> datetime:
    normalized = _optional_utc(value)
    if normalized is None:
        raise ValueError(f"{field_name} must be a datetime")
    return normalized


def _optional_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _rounded(value: float | None) -> float | None:
    return round(float(value), 6) if value is not None else None
