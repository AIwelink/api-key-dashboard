from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Callable, Sequence

from app.modules.sub2api.hourly_forecast import (
    ELIGIBLE_HISTORY_HOURS,
    PROVISIONAL_HISTORY_HOURS,
    ForecastInputError,
    ForecastPoint,
    ForecastResult,
    HourlyObservation,
    weighted_quantile,
)


SHANGHAI_TZ = timezone(timedelta(hours=8))
HORIZON_BANDS = ("1h", "2-3h", "4-6h", "7-12h", "13-24h")

ForecastCallable = Callable[..., ForecastResult]


@dataclass(frozen=True, slots=True)
class BacktestRecord:
    model: str
    origin: datetime
    horizon: int
    target_at: datetime
    actual: float
    p50: float
    p90: float
    source: str = ""
    traffic_stage: str = "unknown"
    data_quality: str = "complete"


@dataclass(frozen=True, slots=True)
class BacktestResult:
    model: str
    origins: int
    records: tuple[BacktestRecord, ...]
    metrics: dict[str, Any]


def evaluate_records(records: Sequence[BacktestRecord]) -> dict[str, Any]:
    normalized = tuple(records)
    by_band = _group_records(normalized, lambda item: horizon_band(item.horizon))
    by_hour = _group_records(
        normalized,
        lambda item: f"{item.target_at.astimezone(SHANGHAI_TZ).hour:02d}",
    )
    by_day_type = _group_records(
        normalized,
        lambda item: "weekend" if item.target_at.astimezone(SHANGHAI_TZ).weekday() >= 5 else "weekday",
    )
    by_week = _group_records(normalized, _calendar_week)
    by_stage = _group_records(normalized, lambda item: item.traffic_stage)
    by_quality = _group_records(normalized, lambda item: item.data_quality)
    return {
        "overall": _metric_summary(normalized),
        "horizon_bands": {
            band: _metric_summary(by_band.get(band, ()))
            for band in HORIZON_BANDS
        },
        "shanghai_hours": {
            key: _metric_summary(values)
            for key, values in sorted(by_hour.items())
        },
        "day_types": {
            key: _metric_summary(values)
            for key, values in sorted(by_day_type.items())
        },
        "calendar_weeks": {
            key: _metric_summary(values)
            for key, values in sorted(by_week.items())
        },
        "traffic_stages": {
            key: _metric_summary(values)
            for key, values in sorted(by_stage.items())
        },
        "data_quality": {
            key: _metric_summary(values)
            for key, values in sorted(by_quality.items())
        },
    }


def horizon_band(horizon: int) -> str:
    if horizon == 1:
        return "1h"
    if 2 <= horizon <= 3:
        return "2-3h"
    if 4 <= horizon <= 6:
        return "4-6h"
    if 7 <= horizon <= 12:
        return "7-12h"
    if 13 <= horizon <= 24:
        return "13-24h"
    raise ValueError("horizon must be between 1 and 24")


def rolling_origin_backtest(
    history: Sequence[HourlyObservation],
    *,
    forecaster: ForecastCallable,
    horizons: int = 24,
    minimum_history_hours: int = 7 * 24,
    origin_step_hours: int = 1,
    evaluation_start: datetime | None = None,
) -> BacktestResult:
    if not 1 <= int(horizons) <= 24:
        raise ValueError("horizons must be between 1 and 24")
    if minimum_history_hours < 1:
        raise ValueError("minimum_history_hours must be positive")
    if origin_step_hours < 1:
        raise ValueError("origin_step_hours must be positive")
    ordered = sorted(history, key=lambda item: item.bucket_at)
    if len(ordered) < minimum_history_hours + horizons:
        raise ForecastInputError("history is too short for the requested rolling backtest")
    normalized_evaluation_start = _as_utc(evaluation_start) if evaluation_start else None
    records: list[BacktestRecord] = []
    origin_count = 0
    model_name = getattr(forecaster, "__name__", "forecast")
    final_origin_index = len(ordered) - horizons
    for origin_index in range(minimum_history_hours, final_origin_index + 1, origin_step_hours):
        origin = _as_utc(ordered[origin_index].bucket_at)
        if normalized_evaluation_start is not None and origin < normalized_evaluation_start:
            continue
        training = ordered[:origin_index]
        forecast = forecaster(training, as_of=origin, horizons=horizons)
        model_name = forecast.model
        if len(forecast.points) != horizons:
            raise ValueError("forecaster returned an unexpected number of points")
        origin_count += 1
        traffic_stage = _traffic_stage(training)
        for offset, point in enumerate(forecast.points):
            truth = ordered[origin_index + offset]
            target_at = _as_utc(truth.bucket_at)
            if point.target_at != target_at:
                raise ValueError("forecast target does not match backtest truth bucket")
            records.append(
                BacktestRecord(
                    model=forecast.model,
                    origin=origin,
                    horizon=point.horizon,
                    target_at=target_at,
                    actual=float(truth.account_cost),
                    p50=float(point.p50),
                    p90=float(point.p90),
                    source=point.source,
                    traffic_stage=traffic_stage,
                    data_quality="complete",
                )
            )
    if not records:
        raise ForecastInputError("no rolling origins remain after applying the evaluation window")
    return BacktestResult(
        model=model_name,
        origins=origin_count,
        records=tuple(records),
        metrics=evaluate_records(records),
    )


def forecast_persistence(
    history: Sequence[HourlyObservation],
    *,
    as_of: datetime,
    horizons: int = 24,
) -> ForecastResult:
    ordered, normalized_as_of = _baseline_inputs(history, as_of=as_of, horizons=horizons)
    latest = ordered[-1].account_cost
    p90_margin = _residual_p90_margin(ordered, lag_hours=1)
    return _baseline_result(
        model="persistence",
        ordered=ordered,
        as_of=normalized_as_of,
        horizons=horizons,
        values=[(latest, "latest_hour") for _ in range(horizons)],
        p90_margin=p90_margin,
    )


def forecast_seasonal_24h(
    history: Sequence[HourlyObservation],
    *,
    as_of: datetime,
    horizons: int = 24,
) -> ForecastResult:
    return _seasonal_baseline(history, as_of=as_of, horizons=horizons, lag_hours=24)


def forecast_seasonal_168h(
    history: Sequence[HourlyObservation],
    *,
    as_of: datetime,
    horizons: int = 24,
) -> ForecastResult:
    return _seasonal_baseline(history, as_of=as_of, horizons=horizons, lag_hours=168)


def _seasonal_baseline(
    history: Sequence[HourlyObservation],
    *,
    as_of: datetime,
    horizons: int,
    lag_hours: int,
) -> ForecastResult:
    ordered, normalized_as_of = _baseline_inputs(history, as_of=as_of, horizons=horizons)
    by_bucket = {_as_utc(item.bucket_at): item for item in ordered}
    latest = ordered[-1].account_cost
    values = []
    for offset in range(horizons):
        target_at = normalized_as_of + timedelta(hours=offset)
        seasonal = by_bucket.get(target_at - timedelta(hours=lag_hours))
        if seasonal is not None:
            values.append((seasonal.account_cost, f"seasonal_{lag_hours}h"))
        else:
            values.append((latest, "latest_hour_fallback"))
    p90_margin = _residual_p90_margin(ordered, lag_hours=lag_hours)
    return _baseline_result(
        model=f"seasonal_{lag_hours}h",
        ordered=ordered,
        as_of=normalized_as_of,
        horizons=horizons,
        values=values,
        p90_margin=p90_margin,
    )


def _baseline_inputs(
    history: Sequence[HourlyObservation],
    *,
    as_of: datetime,
    horizons: int,
) -> tuple[list[HourlyObservation], datetime]:
    if not history:
        raise ForecastInputError("baseline history must not be empty")
    if not 1 <= int(horizons) <= 24:
        raise ValueError("horizons must be between 1 and 24")
    normalized_as_of = _as_utc(as_of)
    if normalized_as_of.minute or normalized_as_of.second or normalized_as_of.microsecond:
        raise ForecastInputError("as_of must be a natural UTC hour")
    ordered = sorted(history, key=lambda item: item.bucket_at)
    latest_at = _as_utc(ordered[-1].bucket_at)
    if latest_at >= normalized_as_of:
        raise ForecastInputError("baseline history must be before as_of")
    return ordered, normalized_as_of


def _baseline_result(
    *,
    model: str,
    ordered: Sequence[HourlyObservation],
    as_of: datetime,
    horizons: int,
    values: Sequence[tuple[float, str]],
    p90_margin: float,
) -> ForecastResult:
    points = tuple(
        ForecastPoint(
            horizon=index + 1,
            target_at=as_of + timedelta(hours=index),
            p50=round(max(0.0, float(value)), 6),
            p90=round(max(0.0, float(value) + p90_margin), 6),
            candidate_count=1,
            source=source,
        )
        for index, (value, source) in enumerate(values)
    )
    return ForecastResult(
        model=model,
        version="1",
        as_of=as_of,
        readiness=_readiness(len(ordered)),
        history_hours=len(ordered),
        completeness_ratio=_history_completeness(ordered),
        points=points,
    )


def _residual_p90_margin(
    history: Sequence[HourlyObservation],
    *,
    lag_hours: int,
) -> float:
    by_bucket = {_as_utc(item.bucket_at): item for item in history}
    residuals = []
    lag = timedelta(hours=lag_hours)
    for item in history:
        previous = by_bucket.get(_as_utc(item.bucket_at) - lag)
        if previous is not None:
            residuals.append(float(item.account_cost) - float(previous.account_cost))
    if not residuals:
        return 0.0
    return max(0.0, weighted_quantile([(value, 1.0) for value in residuals], 0.90))


def _traffic_stage(history: Sequence[HourlyObservation]) -> str:
    if len(history) < 9:
        return "unknown"
    recent = sum(float(item.account_cost) for item in history[-3:]) / 3
    baseline = sum(float(item.account_cost) for item in history[-9:-3]) / 6
    if baseline <= 0:
        return "rising" if recent > 0 else "stable"
    ratio = recent / baseline
    if ratio >= 1.2:
        return "rising"
    if ratio <= 0.8:
        return "falling"
    return "stable"


def _history_completeness(history: Sequence[HourlyObservation]) -> float:
    if not history:
        return 0.0
    first = _as_utc(history[0].bucket_at)
    last = _as_utc(history[-1].bucket_at)
    expected = int((last - first).total_seconds() // 3600) + 1
    return len(history) / expected if expected > 0 else 0.0


def _metric_summary(records: Sequence[BacktestRecord]) -> dict[str, Any]:
    count = len(records)
    if count == 0:
        return {
            "count": 0,
            "mae": None,
            "wape": None,
            "bias": None,
            "p50_pinball": None,
            "p90_pinball": None,
            "p50_coverage": None,
            "p90_coverage": None,
            "p90_exceedance_rate": None,
            "mean_interval_width": None,
            "normalized_interval_width": None,
            "risk_loss": None,
        }
    actual_sum = sum(item.actual for item in records)
    absolute_errors = [abs(item.actual - item.p50) for item in records]
    interval_widths = [max(0.0, item.p90 - item.p50) for item in records]
    p50_coverage = sum(1 for item in records if item.actual <= item.p50) / count
    p90_coverage = sum(1 for item in records if item.actual <= item.p90) / count
    mean_actual = actual_sum / count
    under = sum(max(item.actual - item.p90, 0.0) for item in records)
    over = sum(max(item.p90 - item.actual, 0.0) for item in records)
    return {
        "count": count,
        "mae": sum(absolute_errors) / count,
        "wape": sum(absolute_errors) / actual_sum if actual_sum > 0 else None,
        "bias": sum(item.p50 - item.actual for item in records) / actual_sum if actual_sum > 0 else None,
        "p50_pinball": sum(_pinball(item.actual, item.p50, 0.50) for item in records) / count,
        "p90_pinball": sum(_pinball(item.actual, item.p90, 0.90) for item in records) / count,
        "p50_coverage": p50_coverage,
        "p90_coverage": p90_coverage,
        "p90_exceedance_rate": 1 - p90_coverage,
        "mean_interval_width": sum(interval_widths) / count,
        "normalized_interval_width": (sum(interval_widths) / count) / mean_actual if mean_actual > 0 else None,
        "risk_loss": (5 * under + over) / actual_sum if actual_sum > 0 else None,
    }


def _pinball(actual: float, prediction: float, quantile: float) -> float:
    error = actual - prediction
    return max(quantile * error, (quantile - 1) * error)


def _group_records(
    records: Sequence[BacktestRecord],
    key: Callable[[BacktestRecord], str],
) -> dict[str, tuple[BacktestRecord, ...]]:
    grouped: dict[str, list[BacktestRecord]] = defaultdict(list)
    for record in records:
        grouped[key(record)].append(record)
    return {group: tuple(values) for group, values in grouped.items()}


def _calendar_week(record: BacktestRecord) -> str:
    local_date = record.target_at.astimezone(SHANGHAI_TZ).date()
    iso = local_date.isocalendar()
    return f"{iso.year}-W{iso.week:02d}"


def _readiness(history_hours: int) -> str:
    if history_hours >= ELIGIBLE_HISTORY_HOURS:
        return "eligible"
    if history_hours >= PROVISIONAL_HISTORY_HOURS:
        return "provisional"
    return "limited"


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ForecastInputError("datetime values must be timezone-aware")
    normalized = value.astimezone(UTC)
    if not math.isfinite(normalized.timestamp()):
        raise ForecastInputError("datetime is invalid")
    return normalized
