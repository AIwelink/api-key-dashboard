from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Any, Sequence


@dataclass(frozen=True, slots=True)
class DemandRegime:
    stage: str
    strength: float
    confidence: float
    signal_count: int
    cost_source: str
    short_ratio: float
    medium_ratio: float
    robust_z: float
    positive_cusum: float


@dataclass(frozen=True, slots=True)
class NowcastSelection:
    model_remaining: float
    realtime_remaining: float
    realtime_weight: float
    selected_remaining: float
    stage: str
    surge_strength: float


def detect_demand_regime(samples: Sequence[dict[str, Any]]) -> DemandRegime:
    normalized = sorted(
        (sample for sample in samples if isinstance(sample, dict)),
        key=lambda sample: sample.get("sampled_at") or sample.get("bucket_at") or "",
    )
    direct = [_number(sample.get("account_cost_per_minute")) for sample in normalized]
    tpm = [_number(sample.get("tpm")) for sample in normalized]
    rpm = [_number(sample.get("rpm")) for sample in normalized]
    direct_count = sum(value is not None for value in direct[-30:])
    if direct_count >= 15 and direct[-1:] and direct[-1] is not None:
        source = "direct_account_cost"
        primary = direct
    else:
        source = "tpm_fallback"
        primary = tpm

    aligned = [value if value is not None else 0.0 for value in primary]
    if len(aligned) < 15:
        return _empty_regime(source)

    signals = []
    latest_features = _signal_features(aligned, tpm, rpm)
    for trim in (2, 1, 0):
        end = len(aligned) - trim
        if end < 15:
            continue
        features = _signal_features(aligned[:end], tpm[:end], rpm[:end])
        signals.append(features["signal"])
    signal_count = sum(1 for value in signals if value)
    short_ratio = latest_features["short_ratio"]
    medium_ratio = latest_features["medium_ratio"]
    robust_z = latest_features["robust_z"]
    positive_cusum = latest_features["positive_cusum"]
    cooling = _is_cooling(aligned)

    if signal_count >= 2:
        stage = "surge"
    elif signal_count == 1 or (short_ratio >= 1.15 and medium_ratio >= 1.03):
        stage = "warming"
    elif cooling:
        stage = "cooling"
    else:
        stage = "stable"

    strength = _clamp(
        max(
            0.0,
            (short_ratio - 1.0) / 1.0,
            robust_z / 8.0,
            positive_cusum / 20.0,
            signal_count / 3.0,
        )
    )
    if stage == "stable":
        strength = min(strength, 0.09)
    confirmation = 1.0 if latest_features["channel_confirmed"] else 0.5
    confidence = _clamp((signal_count / 3.0) * 0.75 + confirmation * 0.25)
    if stage == "stable":
        confidence = max(0.5, 1.0 - strength)
    return DemandRegime(
        stage=stage,
        strength=round(strength, 6),
        confidence=round(confidence, 6),
        signal_count=signal_count,
        cost_source=source,
        short_ratio=round(short_ratio, 6),
        medium_ratio=round(medium_ratio, 6),
        robust_z=round(robust_z, 6),
        positive_cusum=round(positive_cusum, 6),
    )


def select_nowcast_remaining(
    *,
    model_remaining: float,
    realtime_remaining: float,
    minute: int,
    stage: str,
    surge_strength: float,
) -> NowcastSelection:
    model = _nonnegative(model_remaining)
    realtime = _nonnegative(realtime_remaining)
    normalized_minute = max(0, min(59, int(minute)))
    normalized_strength = _clamp(_nonnegative(surge_strength))
    regime_boost = 0.25 * normalized_strength if stage in {"warming", "surge"} else 0.0
    realtime_weight = max(0.20, min(1.0, normalized_minute / 45 + regime_boost))
    selected = model * (1 - realtime_weight) + realtime * realtime_weight
    return NowcastSelection(
        model_remaining=model,
        realtime_remaining=realtime,
        realtime_weight=realtime_weight,
        selected_remaining=selected,
        stage=str(stage or "stable"),
        surge_strength=normalized_strength,
    )


def estimate_direct_cost_per_minute(
    samples: Sequence[dict[str, Any]],
    *,
    stage: str,
) -> float | None:
    ordered = sorted(
        (sample for sample in samples if isinstance(sample, dict)),
        key=lambda sample: sample.get("sampled_at") or sample.get("bucket_at") or "",
    )
    values = [
        value
        for value in (_number(sample.get("account_cost_per_minute")) for sample in ordered[-15:])
        if value is not None
    ]
    if len(values) < 5:
        return None
    short_rate = _ema(values, min(3, len(values)))
    medium_rate = _ema(values, min(15, len(values)))
    if stage in {"warming", "surge"}:
        return max(short_rate, medium_rate)
    if stage == "cooling":
        return short_rate
    return medium_rate


def _signal_features(
    primary: list[float],
    tpm: Sequence[float | None],
    rpm: Sequence[float | None],
) -> dict[str, float | bool]:
    if len(primary) < 15:
        return {
            "signal": False,
            "short_ratio": 1.0,
            "medium_ratio": 1.0,
            "robust_z": 0.0,
            "positive_cusum": 0.0,
            "channel_confirmed": False,
        }
    recent = primary[-3:]
    baseline_values = primary[max(0, len(primary) - 33):len(primary) - 3]
    baseline = statistics.median(baseline_values) if baseline_values else statistics.median(primary[:-3])
    recent_average = sum(recent) / len(recent)
    short_ratio = _ratio(recent_average, baseline)
    ema3 = _ema(primary, 3)
    ema15 = _ema(primary, 15)
    ema60 = _ema(primary, min(60, len(primary)))
    medium_ratio = _ratio(ema15, ema60)
    deviations = [abs(value - baseline) for value in baseline_values]
    mad = statistics.median(deviations) if deviations else 0.0
    robust_scale = max(1.4826 * mad, abs(baseline) * 0.08, 1e-9)
    robust_z = max(0.0, (recent_average - baseline) / robust_scale)
    positive_cusum = sum(max(0.0, value - baseline) for value in primary[-10:]) / robust_scale
    tpm_ratio = _channel_ratio(tpm)
    rpm_ratio = _channel_ratio(rpm)
    channel_confirmed = max(tpm_ratio, rpm_ratio) >= 1.15
    strong = short_ratio >= 1.35 and robust_z >= 2.5
    moderate = short_ratio >= 1.20 and robust_z >= 1.5 and positive_cusum >= 5 and channel_confirmed
    return {
        "signal": strong or moderate,
        "short_ratio": short_ratio,
        "medium_ratio": medium_ratio,
        "robust_z": robust_z,
        "positive_cusum": positive_cusum,
        "channel_confirmed": channel_confirmed,
        "ema3": ema3,
        "ema15": ema15,
    }


def _channel_ratio(values: Sequence[float | None]) -> float:
    normalized = [value for value in values if value is not None]
    if len(normalized) < 10:
        return 1.0
    recent = sum(normalized[-3:]) / 3
    baseline_values = normalized[max(0, len(normalized) - 33):len(normalized) - 3]
    baseline = statistics.median(baseline_values) if baseline_values else 0.0
    return _ratio(recent, baseline)


def _is_cooling(values: list[float]) -> bool:
    if len(values) < 15:
        return False
    ema3 = _ema(values, 3)
    ema15 = _ema(values, 15)
    recent = sum(values[-5:]) / 5
    prior = sum(values[-15:-5]) / 10
    return prior > 0 and recent <= prior * 0.8 and ema3 < ema15 * 0.9


def _ema(values: Sequence[float], span: int) -> float:
    alpha = 2 / (span + 1)
    result = float(values[0])
    for value in values[1:]:
        result = alpha * float(value) + (1 - alpha) * result
    return result


def _empty_regime(source: str) -> DemandRegime:
    return DemandRegime(
        stage="stable",
        strength=0.0,
        confidence=0.0,
        signal_count=0,
        cost_source=source,
        short_ratio=1.0,
        medium_ratio=1.0,
        robust_z=0.0,
        positive_cusum=0.0,
    )


def _ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 1.0 if numerator <= 0 else 4.0
    return numerator / denominator


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def _nonnegative(value: Any) -> float:
    number = _number(value)
    return number if number is not None else 0.0


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))
