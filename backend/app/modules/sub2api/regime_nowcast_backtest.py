from __future__ import annotations

import math
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Sequence

from app.modules.sub2api.hourly_forecast import (
    ForecastInputError,
    ForecastResult,
    HourlyObservation,
)
from app.modules.sub2api.minute_forecast_repository import MinuteObservation
from app.modules.sub2api.regime_nowcast import (
    detect_demand_regime,
    estimate_direct_cost_per_minute,
    select_nowcast_remaining,
)


ForecastCallable = Callable[..., ForecastResult]
STRATEGIES = (
    "current_max",
    "model_only",
    "realtime_only",
    "fixed_25",
    "fixed_50",
    "fixed_75",
    "regime_aware",
)


@dataclass(frozen=True, slots=True)
class NowcastBacktestRecord:
    strategy: str
    issued_at: datetime
    target_at: datetime
    actual_remaining: float
    predicted_remaining: float
    regime_stage: str
    surge_strength: float


@dataclass(frozen=True, slots=True)
class StrategyBacktestResult:
    strategy: str
    records: tuple[NowcastBacktestRecord, ...]
    metrics: dict[str, Any]


@dataclass(frozen=True, slots=True)
class RegimeNowcastBacktestResult:
    strategies: dict[str, StrategyBacktestResult]
    surge_metrics: dict[str, Any]
    origins: int
    no_future_data: bool = True


def rolling_minute_nowcast_backtest(
    observations: Sequence[MinuteObservation],
    *,
    forecaster: ForecastCallable,
    minimum_history_hours: int = 7 * 24,
    evaluation_start: datetime | None = None,
    issue_step_minutes: int = 5,
) -> RegimeNowcastBacktestResult:
    if minimum_history_hours < 1:
        raise ValueError("minimum_history_hours must be positive")
    if issue_step_minutes < 1 or issue_step_minutes > 30 or 60 % issue_step_minutes:
        raise ValueError("issue_step_minutes must divide 60 and be between 1 and 30")
    ordered = sorted(observations, key=lambda item: item.bucket_at)
    if not ordered:
        raise ForecastInputError("minute history must not be empty")
    for item in ordered:
        _as_utc(item.bucket_at)
    hourly = _aggregate_complete_hours(ordered)
    if len(hourly) <= minimum_history_hours:
        raise ForecastInputError("minute history is too short for the requested backtest")

    data_end = _as_utc(ordered[-1].bucket_at) + timedelta(minutes=1)
    first_origin = _as_utc(hourly[minimum_history_hours].bucket_at)
    normalized_evaluation_start = _as_utc(evaluation_start) if evaluation_start else first_origin
    first_origin = max(first_origin, normalized_evaluation_start.replace(minute=0, second=0, microsecond=0))
    by_minute = {_as_utc(item.bucket_at): item for item in ordered}
    minute_positions = {_as_utc(item.bucket_at): index for index, item in enumerate(ordered)}
    strategy_records: dict[str, list[NowcastBacktestRecord]] = {name: [] for name in STRATEGIES}
    origins = 0
    origin = first_origin
    while origin + timedelta(hours=1) <= data_end:
        training = [item for item in hourly if item.bucket_at < origin]
        if len(training) < minimum_history_hours:
            origin += timedelta(hours=1)
            continue
        forecast = forecaster(training, as_of=origin, horizons=1)
        if not forecast.points or forecast.points[0].target_at != origin:
            raise ValueError("forecaster current-hour target does not match the backtest origin")
        origins += 1
        model_p90 = float(forecast.points[0].p90)
        hour_end = origin + timedelta(hours=1)
        for minute in range(issue_step_minutes, 60, issue_step_minutes):
            issued_at = origin + timedelta(minutes=minute)
            if issued_at < normalized_evaluation_start or issued_at >= data_end:
                continue
            issue_index = minute_positions.get(issued_at)
            if issue_index is None:
                continue
            visible = _sample_dicts(ordered[max(0, issue_index - 60):issue_index])
            regime = detect_demand_regime(visible)
            direct_rate = estimate_direct_cost_per_minute(visible, stage=regime.stage)
            if direct_rate is None:
                continue
            observed = _cost_between(by_minute, origin, issued_at)
            actual_remaining = _cost_between(by_minute, issued_at, hour_end)
            model_remaining = max(0.0, model_p90 - observed)
            realtime_remaining = max(0.0, direct_rate * (60 - minute))
            selection = select_nowcast_remaining(
                model_remaining=model_remaining,
                realtime_remaining=realtime_remaining,
                minute=minute,
                stage=regime.stage,
                surge_strength=regime.strength,
            )
            predictions = {
                "current_max": max(model_remaining, realtime_remaining),
                "model_only": model_remaining,
                "realtime_only": realtime_remaining,
                "fixed_25": _blend(model_remaining, realtime_remaining, 0.25),
                "fixed_50": _blend(model_remaining, realtime_remaining, 0.50),
                "fixed_75": _blend(model_remaining, realtime_remaining, 0.75),
                "regime_aware": selection.selected_remaining,
            }
            for strategy, predicted in predictions.items():
                strategy_records[strategy].append(
                    NowcastBacktestRecord(
                        strategy=strategy,
                        issued_at=issued_at,
                        target_at=hour_end,
                        actual_remaining=actual_remaining,
                        predicted_remaining=predicted,
                        regime_stage=regime.stage,
                        surge_strength=regime.strength,
                    )
                )
        origin += timedelta(hours=1)
    if not any(strategy_records.values()):
        raise ForecastInputError("no minute nowcast origins remain after applying the evaluation window")

    truth_events = _surge_event_starts(ordered, start_at=normalized_evaluation_start)
    detections = _surge_detection_times(ordered, start_at=normalized_evaluation_start)
    strategies = {
        name: StrategyBacktestResult(
            strategy=name,
            records=tuple(records),
            metrics=evaluate_nowcast_records(records),
        )
        for name, records in strategy_records.items()
    }
    return RegimeNowcastBacktestResult(
        strategies=strategies,
        surge_metrics=evaluate_surge_detections(truth_events, detections),
        origins=origins,
    )


def evaluate_nowcast_records(records: Sequence[NowcastBacktestRecord]) -> dict[str, Any]:
    normalized = tuple(records)
    by_issue_band = _group_records(normalized, lambda item: _issue_minute_band(item.issued_at.minute))
    by_stage = _group_records(normalized, lambda item: item.regime_stage)
    return {
        "overall": _metric_summary(normalized),
        "issue_minute_bands": {
            key: _metric_summary(values)
            for key, values in sorted(by_issue_band.items())
        },
        "regime_stages": {
            key: _metric_summary(values)
            for key, values in sorted(by_stage.items())
        },
    }


def evaluate_surge_detections(
    truth_event_starts: Sequence[datetime],
    detection_times: Sequence[datetime],
    *,
    match_window_minutes: int = 10,
) -> dict[str, Any]:
    events = sorted(_as_utc(value) for value in truth_event_starts)
    detections = sorted(_as_utc(value) for value in detection_times)
    window = timedelta(minutes=max(1, int(match_window_minutes)))
    delays = []
    detected_events = 0
    for event_at in events:
        matching = [value for value in detections if event_at <= value <= event_at + window]
        if matching:
            detected_events += 1
            delays.append((matching[0] - event_at).total_seconds() / 60)
    matched_detections = sum(
        1
        for detected_at in detections
        if any(event_at <= detected_at <= event_at + window for event_at in events)
    )
    return {
        "events": len(events),
        "detections": len(detections),
        "detected_events": detected_events,
        "recall": detected_events / len(events) if events else None,
        "precision": matched_detections / len(detections) if detections else None,
        "median_detection_delay_minutes": statistics.median(delays) if delays else None,
        "match_window_minutes": int(match_window_minutes),
    }


def _aggregate_complete_hours(observations: Sequence[MinuteObservation]) -> list[HourlyObservation]:
    grouped: dict[datetime, list[MinuteObservation]] = defaultdict(list)
    data_end = _as_utc(observations[-1].bucket_at) + timedelta(minutes=1)
    for item in observations:
        bucket_at = _as_utc(item.bucket_at)
        grouped[bucket_at.replace(minute=0, second=0, microsecond=0)].append(item)
    result = []
    for bucket_at, values in sorted(grouped.items()):
        if bucket_at + timedelta(hours=1) > data_end:
            continue
        actual_minutes = {_as_utc(item.bucket_at) for item in values}
        expected_minutes = {bucket_at + timedelta(minutes=minute) for minute in range(60)}
        if actual_minutes != expected_minutes:
            continue
        result.append(
            HourlyObservation(
                bucket_at=bucket_at,
                account_cost=sum(float(item.account_cost) for item in values),
                requests=sum(float(item.requests) for item in values),
                total_tokens=sum(float(item.total_tokens) for item in values),
            )
        )
    return result


def _sample_dicts(observations: Sequence[MinuteObservation]) -> list[dict[str, Any]]:
    return [
        {
            "sampled_at": item.bucket_at,
            "account_cost_per_minute": float(item.account_cost),
            "tpm": float(item.total_tokens),
            "rpm": float(item.requests),
        }
        for item in observations
    ]


def _cost_between(
    by_minute: dict[datetime, MinuteObservation],
    start_at: datetime,
    end_at: datetime,
) -> float:
    total = 0.0
    bucket_at = start_at
    while bucket_at < end_at:
        item = by_minute.get(bucket_at)
        if item is not None:
            total += float(item.account_cost)
        bucket_at += timedelta(minutes=1)
    return total


def _surge_event_starts(
    observations: Sequence[MinuteObservation],
    *,
    start_at: datetime,
) -> list[datetime]:
    costs = [float(item.account_cost) for item in observations]
    flagged = []
    for index in range(30, len(observations) - 4):
        event_at = _as_utc(observations[index].bucket_at)
        if event_at < start_at:
            continue
        baseline_values = costs[index - 30:index]
        baseline = statistics.median(baseline_values)
        mad = statistics.median(abs(value - baseline) for value in baseline_values)
        scale = max(1.4826 * mad, baseline * 0.08, 0.01)
        forward_average = sum(costs[index:index + 5]) / 5
        threshold = max(baseline * 1.5, baseline + 2.5 * scale)
        if forward_average >= threshold:
            flagged.append(event_at)
    starts = []
    previous = None
    for event_at in flagged:
        if previous is None or event_at - previous > timedelta(minutes=1):
            starts.append(event_at)
        previous = event_at
    return starts


def _surge_detection_times(
    observations: Sequence[MinuteObservation],
    *,
    start_at: datetime,
) -> list[datetime]:
    detections = []
    for index in range(15, len(observations) + 1):
        detected_at = _as_utc(observations[index - 1].bucket_at) + timedelta(minutes=1)
        if detected_at < start_at:
            continue
        visible = _sample_dicts(observations[max(0, index - 60):index])
        if detect_demand_regime(visible).stage == "surge":
            detections.append(detected_at)
    return detections


def _metric_summary(records: Sequence[NowcastBacktestRecord]) -> dict[str, Any]:
    count = len(records)
    if count == 0:
        return {
            "count": 0,
            "mae": None,
            "wape": None,
            "bias": None,
            "coverage": None,
            "pinball": None,
            "risk_loss": None,
        }
    actual_sum = sum(item.actual_remaining for item in records)
    absolute_error = sum(abs(item.actual_remaining - item.predicted_remaining) for item in records)
    under = sum(max(item.actual_remaining - item.predicted_remaining, 0.0) for item in records)
    over = sum(max(item.predicted_remaining - item.actual_remaining, 0.0) for item in records)
    return {
        "count": count,
        "mae": absolute_error / count,
        "wape": absolute_error / actual_sum if actual_sum > 0 else None,
        "bias": (
            sum(item.predicted_remaining - item.actual_remaining for item in records) / actual_sum
            if actual_sum > 0
            else None
        ),
        "coverage": sum(1 for item in records if item.actual_remaining <= item.predicted_remaining) / count,
        "pinball": sum(
            _pinball(item.actual_remaining, item.predicted_remaining, 0.90)
            for item in records
        ) / count,
        "risk_loss": (5 * under + over) / actual_sum if actual_sum > 0 else None,
    }


def _group_records(
    records: Sequence[NowcastBacktestRecord],
    key: Callable[[NowcastBacktestRecord], str],
) -> dict[str, tuple[NowcastBacktestRecord, ...]]:
    grouped: dict[str, list[NowcastBacktestRecord]] = defaultdict(list)
    for record in records:
        grouped[key(record)].append(record)
    return {name: tuple(values) for name, values in grouped.items()}


def _issue_minute_band(minute: int) -> str:
    if minute < 20:
        return "00-19"
    if minute < 40:
        return "20-39"
    return "40-59"


def _blend(model: float, realtime: float, realtime_weight: float) -> float:
    return model * (1 - realtime_weight) + realtime * realtime_weight


def _pinball(actual: float, prediction: float, quantile: float) -> float:
    error = actual - prediction
    return max(quantile * error, (quantile - 1) * error)


def _as_utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ForecastInputError("backtest datetimes must be timezone-aware")
    normalized = value.astimezone(UTC)
    if not math.isfinite(normalized.timestamp()):
        raise ForecastInputError("backtest datetime is invalid")
    return normalized
