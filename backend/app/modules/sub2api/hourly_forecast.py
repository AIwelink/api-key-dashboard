from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta, timezone
from typing import Sequence


MODEL_NAME = "robust_seasonal_analog"
MODEL_VERSION = "1"
MINIMUM_HISTORY_HOURS = 7 * 24
PROVISIONAL_HISTORY_HOURS = 14 * 24
ELIGIBLE_HISTORY_HOURS = 56 * 24
MAX_ANALOG_DAYS = 28
CONTEXT_HOURS = 3
INACTIVE_REGIME_GAP_HOURS = 48
SHANGHAI_TZ = timezone(timedelta(hours=8))


class ForecastInputError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class HourlyObservation:
    bucket_at: datetime
    account_cost: float
    requests: float
    total_tokens: float


@dataclass(frozen=True, slots=True)
class ForecastPoint:
    horizon: int
    target_at: datetime
    p50: float
    p90: float
    candidate_count: int
    source: str


@dataclass(frozen=True, slots=True)
class ForecastResult:
    model: str
    version: str
    as_of: datetime
    readiness: str
    history_hours: int
    completeness_ratio: float
    points: tuple[ForecastPoint, ...]


@dataclass(frozen=True, slots=True)
class ForecastRunway:
    hours: float
    capped: bool
    projected_cost_usd: float


@dataclass(frozen=True, slots=True)
class CurrentHourNowcast:
    forecast: ForecastResult
    applied: bool
    observed_cost_usd: float
    model_p50_remaining_usd: float
    model_p90_remaining_usd: float
    realtime_remaining_usd: float
    selected_p90_remaining_usd: float


def forecast_hourly_demand(
    history: Sequence[HourlyObservation],
    *,
    as_of: datetime,
    horizons: int = 24,
) -> ForecastResult:
    normalized_as_of = _natural_utc_hour(as_of, field_name="as_of")
    if not 1 <= int(horizons) <= 168:
        raise ForecastInputError("horizons must be between 1 and 168")
    normalized = _normalize_history(history, as_of=normalized_as_of)
    normalized = _recent_active_regime(normalized)
    if len(normalized) < MINIMUM_HISTORY_HOURS:
        raise ForecastInputError("at least seven complete days are required in the current traffic regime")
    completeness_ratio = _completeness_ratio(normalized)
    if completeness_ratio < 0.95:
        raise ForecastInputError("current traffic regime must be at least 95% complete")
    readiness = _readiness(len(normalized))
    by_bucket = {item.bucket_at: item for item in normalized}
    current_context = _context_before(by_bucket, normalized_as_of)
    latest = normalized[-1]
    points = []
    for horizon in range(1, int(horizons) + 1):
        target_at = normalized_as_of + timedelta(hours=horizon - 1)
        candidates = _analog_candidates(
            by_bucket=by_bucket,
            as_of=normalized_as_of,
            target_at=target_at,
            current_context=current_context,
        )
        if candidates:
            p50 = weighted_quantile(candidates, 0.50)
            p90 = weighted_quantile(candidates, 0.90)
            source = "analog"
            candidate_count = len(candidates)
        else:
            fallback_values = _same_hour_values(normalized, target_at=target_at, as_of=normalized_as_of)
            if fallback_values:
                p50 = statistics.median(fallback_values)
                p90 = _unweighted_quantile(fallback_values, 0.90)
                source = "seasonal_hour"
                candidate_count = len(fallback_values)
            else:
                p50 = latest.account_cost
                p90 = latest.account_cost
                source = "latest_hour"
                candidate_count = 0
        p50 = max(0.0, float(p50))
        p90 = max(p50, float(p90))
        points.append(
            ForecastPoint(
                horizon=horizon,
                target_at=target_at,
                p50=round(p50, 6),
                p90=round(p90, 6),
                candidate_count=candidate_count,
                source=source,
            )
        )
    return ForecastResult(
        model=MODEL_NAME,
        version=MODEL_VERSION,
        as_of=normalized_as_of,
        readiness=readiness,
        history_hours=len(normalized),
        completeness_ratio=round(completeness_ratio, 6),
        points=tuple(points),
    )


def apply_current_hour_nowcast(
    forecast: ForecastResult,
    *,
    now: datetime,
    observed_current_hour_cost_usd: float,
    realtime_cost_per_hour: float,
) -> CurrentHourNowcast:
    current_at = _aware_utc(now, field_name="now")
    observed_cost = _nonnegative(observed_current_hour_cost_usd, field_name="observed_current_hour_cost_usd")
    realtime_rate = _nonnegative(realtime_cost_per_hour, field_name="realtime_cost_per_hour")
    current_index = None
    current_point = None
    for index, point in enumerate(forecast.points):
        point_start = _natural_utc_hour(point.target_at, field_name="forecast target_at")
        if point_start <= current_at < point_start + timedelta(hours=1):
            current_index = index
            current_point = point
            break
    if current_index is None or current_point is None:
        raise ForecastInputError("forecast does not contain the current natural hour")

    remaining_fraction = (
        current_point.target_at + timedelta(hours=1) - current_at
    ).total_seconds() / 3600
    if remaining_fraction <= 0:
        raise ForecastInputError("current forecast hour has already ended")
    model_p50_remaining = max(0.0, current_point.p50 - observed_cost)
    model_p90_remaining = max(model_p50_remaining, current_point.p90 - observed_cost, 0.0)
    realtime_remaining = realtime_rate * remaining_fraction
    selected_p90_remaining = max(model_p90_remaining, realtime_remaining, model_p50_remaining)
    adjusted_point = replace(
        current_point,
        p50=round(model_p50_remaining / remaining_fraction, 6),
        p90=round(selected_p90_remaining / remaining_fraction, 6),
        source=f"{current_point.source}+nowcast",
    )
    points = list(forecast.points)
    points[current_index] = adjusted_point
    return CurrentHourNowcast(
        forecast=replace(forecast, points=tuple(points)),
        applied=True,
        observed_cost_usd=round(observed_cost, 6),
        model_p50_remaining_usd=round(model_p50_remaining, 6),
        model_p90_remaining_usd=round(model_p90_remaining, 6),
        realtime_remaining_usd=round(realtime_remaining, 6),
        selected_p90_remaining_usd=round(selected_p90_remaining, 6),
    )


def forecast_cost_over_window(
    forecast: ForecastResult,
    *,
    now: datetime,
    hours: float,
    quantile: str = "p90",
) -> float:
    segments = _forecast_segments(forecast, now=now, hours=hours, quantile=quantile)
    return round(sum(cost for _, cost in segments), 6)


def forecast_runway(
    forecast: ForecastResult,
    *,
    remaining_usd: float,
    now: datetime,
    quantile: str = "p90",
    max_hours: float = 24,
) -> ForecastRunway:
    remaining = _nonnegative(remaining_usd, field_name="remaining_usd")
    if remaining == 0:
        return ForecastRunway(hours=0.0, capped=False, projected_cost_usd=0.0)

    elapsed_hours = 0.0
    projected_cost = 0.0
    for duration_hours, segment_cost in _forecast_segments(
        forecast,
        now=now,
        hours=max_hours,
        quantile=quantile,
    ):
        if segment_cost > 0 and remaining <= segment_cost:
            used_fraction = remaining / segment_cost
            elapsed_hours += duration_hours * used_fraction
            projected_cost += remaining
            return ForecastRunway(
                hours=round(elapsed_hours, 6),
                capped=False,
                projected_cost_usd=round(projected_cost, 6),
            )
        remaining -= segment_cost
        projected_cost += segment_cost
        elapsed_hours += duration_hours

    return ForecastRunway(
        hours=round(float(max_hours), 6),
        capped=True,
        projected_cost_usd=round(projected_cost, 6),
    )


def weighted_quantile(values: list[tuple[float, float]], q: float) -> float:
    if not 0 <= q <= 1:
        raise ValueError("q must be between 0 and 1")
    ordered = sorted(
        (float(value), float(weight))
        for value, weight in values
        if math.isfinite(float(value)) and math.isfinite(float(weight)) and float(weight) > 0
    )
    if not ordered:
        raise ValueError("at least one positive weighted value is required")
    total_weight = sum(weight for _, weight in ordered)
    threshold = total_weight * q
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= threshold:
            return value
    return ordered[-1][0]


def context_scale(
    current: Sequence[HourlyObservation],
    historical: Sequence[HourlyObservation],
) -> tuple[float, float]:
    ratios = []
    for current_total, historical_total in zip(
        _context_totals(current),
        _context_totals(historical),
        strict=True,
    ):
        if current_total == 0 and historical_total == 0:
            ratios.append(1.0)
        elif historical_total > 0:
            ratios.append(current_total / historical_total)
    if not ratios:
        return 1.0, 0.25
    scale = min(4.0, max(0.25, float(statistics.median(ratios))))
    log_spread = statistics.median(abs(math.log(max(ratio, 1e-9) / scale)) for ratio in ratios)
    consistency_weight = 1 / (1 + log_spread)
    return scale, consistency_weight


def _analog_candidates(
    *,
    by_bucket: dict[datetime, HourlyObservation],
    as_of: datetime,
    target_at: datetime,
    current_context: Sequence[HourlyObservation],
) -> list[tuple[float, float]]:
    candidates = []
    for lag_days in range(1, MAX_ANALOG_DAYS + 1):
        lag = timedelta(days=lag_days)
        candidate_target_at = target_at - lag
        if candidate_target_at >= as_of:
            continue
        candidate = by_bucket.get(candidate_target_at)
        if candidate is None:
            continue
        candidate_context = _context_before(by_bucket, as_of - lag)
        if len(current_context) != CONTEXT_HOURS or len(candidate_context) != CONTEXT_HOURS:
            continue
        scale, consistency_weight = context_scale(current_context, candidate_context)
        recency_weight = 0.5 ** ((lag_days - 1) / 14)
        calendar_weight = _calendar_weight(target_at, candidate_target_at)
        weight = recency_weight * calendar_weight * consistency_weight
        candidates.append((candidate.account_cost * scale, weight))
    return candidates


def _calendar_weight(target_at: datetime, candidate_at: datetime) -> float:
    target_local = target_at.astimezone(SHANGHAI_TZ)
    candidate_local = candidate_at.astimezone(SHANGHAI_TZ)
    if target_local.weekday() == candidate_local.weekday():
        return 2.0
    target_weekend = target_local.weekday() >= 5
    candidate_weekend = candidate_local.weekday() >= 5
    return 1.25 if target_weekend == candidate_weekend else 0.75


def _context_before(
    by_bucket: dict[datetime, HourlyObservation],
    origin: datetime,
) -> tuple[HourlyObservation, ...]:
    values = []
    for hours_ago in range(CONTEXT_HOURS, 0, -1):
        value = by_bucket.get(origin - timedelta(hours=hours_ago))
        if value is not None:
            values.append(value)
    return tuple(values)


def _context_totals(values: Sequence[HourlyObservation]) -> tuple[float, float, float]:
    return (
        sum(item.account_cost for item in values),
        sum(item.requests for item in values),
        sum(item.total_tokens for item in values),
    )


def _same_hour_values(
    history: Sequence[HourlyObservation],
    *,
    target_at: datetime,
    as_of: datetime,
) -> list[float]:
    target_local_hour = target_at.astimezone(SHANGHAI_TZ).hour
    cutoff = as_of - timedelta(days=MAX_ANALOG_DAYS)
    return [
        item.account_cost
        for item in history
        if cutoff <= item.bucket_at < as_of and item.bucket_at.astimezone(SHANGHAI_TZ).hour == target_local_hour
    ]


def _normalize_history(
    history: Sequence[HourlyObservation],
    *,
    as_of: datetime,
) -> list[HourlyObservation]:
    normalized = []
    seen = set()
    for item in history:
        bucket_at = _natural_utc_hour(item.bucket_at, field_name="bucket_at")
        if bucket_at in seen:
            raise ForecastInputError(f"duplicate hourly bucket: {bucket_at.isoformat()}")
        if bucket_at >= as_of:
            raise ForecastInputError("all history buckets must be before as_of")
        seen.add(bucket_at)
        normalized.append(
            HourlyObservation(
                bucket_at=bucket_at,
                account_cost=_nonnegative(item.account_cost, field_name="account_cost"),
                requests=_nonnegative(item.requests, field_name="requests"),
                total_tokens=_nonnegative(item.total_tokens, field_name="total_tokens"),
            )
        )
    normalized.sort(key=lambda item: item.bucket_at)
    if len(normalized) < MINIMUM_HISTORY_HOURS:
        raise ForecastInputError("at least seven complete days of hourly history are required")
    latest_age = as_of - normalized[-1].bucket_at
    if latest_age < timedelta(hours=1) or latest_age > timedelta(hours=2):
        raise ForecastInputError("latest complete hour must be one or two hours before as_of")
    return normalized


def _readiness(history_hours: int) -> str:
    if history_hours >= ELIGIBLE_HISTORY_HOURS:
        return "eligible"
    if history_hours >= PROVISIONAL_HISTORY_HOURS:
        return "provisional"
    return "limited"


def _recent_active_regime(history: Sequence[HourlyObservation]) -> list[HourlyObservation]:
    regime_start = 0
    zero_run = 0
    for index, item in enumerate(history):
        is_zero = item.account_cost == 0 and item.requests == 0 and item.total_tokens == 0
        if is_zero:
            zero_run += 1
            continue
        if zero_run >= INACTIVE_REGIME_GAP_HOURS:
            regime_start = index
        zero_run = 0
    return list(history[regime_start:])


def _completeness_ratio(history: Sequence[HourlyObservation]) -> float:
    if not history:
        return 0.0
    expected = int((history[-1].bucket_at - history[0].bucket_at).total_seconds() // 3600) + 1
    return len(history) / expected if expected > 0 else 0.0


def _natural_utc_hour(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ForecastInputError(f"{field_name} must be timezone-aware")
    normalized = value.astimezone(UTC)
    if normalized.minute or normalized.second or normalized.microsecond:
        raise ForecastInputError(f"{field_name} must be a natural UTC hour")
    return normalized


def _nonnegative(value: float, *, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ForecastInputError(f"{field_name} must be numeric") from exc
    if not math.isfinite(number) or number < 0:
        raise ForecastInputError(f"{field_name} must be finite and non-negative")
    return number


def _unweighted_quantile(values: Sequence[float], q: float) -> float:
    ordered = sorted(float(value) for value in values)
    index = max(0, min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1))
    return ordered[index]


def _forecast_segments(
    forecast: ForecastResult,
    *,
    now: datetime,
    hours: float,
    quantile: str,
) -> list[tuple[float, float]]:
    start_at = _aware_utc(now, field_name="now")
    duration = _positive_duration(hours)
    end_at = start_at + timedelta(hours=duration)
    if quantile not in {"p50", "p90"}:
        raise ForecastInputError("quantile must be p50 or p90")

    segments: list[tuple[float, float]] = []
    covered_seconds = 0.0
    seen_targets: set[datetime] = set()
    for point in sorted(forecast.points, key=lambda item: item.target_at):
        point_start = _natural_utc_hour(point.target_at, field_name="forecast target_at")
        if point_start in seen_targets:
            raise ForecastInputError("forecast contains duplicate target hours")
        seen_targets.add(point_start)
        point_end = point_start + timedelta(hours=1)
        segment_start = max(start_at, point_start)
        segment_end = min(end_at, point_end)
        if segment_end <= segment_start:
            continue
        segment_seconds = (segment_end - segment_start).total_seconds()
        hourly_cost = _nonnegative(getattr(point, quantile), field_name=quantile)
        segments.append((segment_seconds / 3600, hourly_cost * segment_seconds / 3600))
        covered_seconds += segment_seconds

    required_seconds = duration * 3600
    if abs(covered_seconds - required_seconds) > 0.001:
        raise ForecastInputError("forecast does not cover the requested window")
    return segments


def _aware_utc(value: datetime, *, field_name: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ForecastInputError(f"{field_name} must be timezone-aware")
    return value.astimezone(UTC)


def _positive_duration(value: float) -> float:
    try:
        duration = float(value)
    except (TypeError, ValueError) as exc:
        raise ForecastInputError("hours must be numeric") from exc
    if not math.isfinite(duration) or duration <= 0 or duration > 168:
        raise ForecastInputError("hours must be between 0 and 168")
    return duration
