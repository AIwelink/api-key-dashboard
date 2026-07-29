from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.sub2api.cache import get_site
from app.modules.sub2api.hourly_forecast import (
    CONTEXT_HOURS,
    MAX_ANALOG_DAYS,
    ForecastResult,
    HourlyObservation,
    forecast_hourly_demand,
)
from app.modules.sub2api.hourly_forecast_backtest import (
    BacktestResult,
    evaluate_records,
    forecast_persistence,
    forecast_seasonal_24h,
    forecast_seasonal_168h,
    rolling_origin_backtest,
)
from app.modules.sub2api.hourly_forecast_repository import fetch_group_hourly_observations


SCHEMA_VERSION = "hourly_capacity_forecast_backtest.v1"
HORIZONS = 24
MINIMUM_HISTORY_HOURS = 7 * 24
BASELINE_FORECASTERS = {
    "persistence": forecast_persistence,
    "seasonal_24h": forecast_seasonal_24h,
    "seasonal_168h": forecast_seasonal_168h,
}


def build_report(
    *,
    site_id: str,
    group_id: int,
    history: list[HourlyObservation],
    forecast: ForecastResult,
    candidate: BacktestResult,
    baselines: dict[str, BacktestResult],
    evaluation_start: datetime,
    origin_step_hours: int,
) -> dict[str, Any]:
    comparison, promotion_gates = _comparison(candidate, baselines, readiness=forecast.readiness)
    return {
        "schema_version": SCHEMA_VERSION,
        "site_id": site_id,
        "group_id": int(group_id),
        "timezone": "Asia/Shanghai",
        "model": {
            "name": forecast.model,
            "version": forecast.version,
            "readiness": forecast.readiness,
        },
        "history": {
            "start_at": history[0].bucket_at,
            "end_at": history[-1].bucket_at + timedelta(hours=1),
            "hours": len(history),
            "nonzero_hours": sum(1 for item in history if item.account_cost > 0),
            "evaluation_start": evaluation_start,
        },
        "parameters": {
            "horizons": HORIZONS,
            "minimum_history_hours": MINIMUM_HISTORY_HOURS,
            "origin_step_hours": int(origin_step_hours),
            "maximum_analog_days": MAX_ANALOG_DAYS,
            "context_hours": CONTEXT_HOURS,
            "underforecast_weight": 5,
        },
        "forecast": {
            "as_of": forecast.as_of,
            "readiness": forecast.readiness,
            "history_hours": forecast.history_hours,
            "completeness_ratio": forecast.completeness_ratio,
            "points": [asdict(point) for point in forecast.points],
        },
        "candidate": _backtest_summary(candidate),
        "baselines": {
            name: _backtest_summary(result)
            for name, result in sorted(baselines.items())
        },
        "comparison": comparison,
        "promotion_gates": promotion_gates,
    }


def _backtest_summary(result: BacktestResult) -> dict[str, Any]:
    return {
        "model": result.model,
        "origins": result.origins,
        "records": len(result.records),
        **result.metrics,
    }


def _comparison(
    candidate: BacktestResult,
    baselines: dict[str, BacktestResult],
    *,
    readiness: str,
) -> tuple[dict[str, Any], dict[str, bool]]:
    candidate_overall = candidate.metrics["overall"]
    baseline_overall = {name: result.metrics["overall"] for name, result in baselines.items()}
    candidate_short = evaluate_records([record for record in candidate.records if record.horizon <= 3])["overall"]
    baseline_short = {
        name: evaluate_records([record for record in result.records if record.horizon <= 3])["overall"]
        for name, result in baselines.items()
    }
    best_short_name, best_short_wape = _best_metric(baseline_short, "wape")
    best_overall_name, best_overall_wape = _best_metric(baseline_overall, "wape")
    best_pinball_name, best_p90_pinball = _best_metric(baseline_overall, "p90_pinball")
    best_risk_name, best_risk_loss = _best_metric(baseline_overall, "risk_loss")
    short_improvement = _relative_improvement(best_short_wape, candidate_short.get("wape"))
    overall_improvement = _relative_improvement(best_overall_wape, candidate_overall.get("wape"))
    risk_improvement = _relative_improvement(best_risk_loss, candidate_overall.get("risk_loss"))
    weekly_folds, weekly_wins = _weekly_wins(candidate, baselines)
    p90_coverage = candidate_overall.get("p90_coverage")
    gates = {
        "history_eligible": readiness == "eligible",
        "short_horizon_wape_improvement": short_improvement is not None and short_improvement >= 0.10,
        "full_horizon_not_materially_worse": overall_improvement is not None and overall_improvement >= -0.03,
        "p90_calibrated": p90_coverage is not None and 0.85 <= p90_coverage <= 0.95,
        "p90_pinball_better": (
            best_p90_pinball is not None
            and candidate_overall.get("p90_pinball") is not None
            and candidate_overall["p90_pinball"] < best_p90_pinball
        ),
        "risk_loss_improvement": risk_improvement is not None and risk_improvement >= 0.10,
        "weekly_stability": weekly_folds >= 4 and weekly_wins >= 3,
    }
    gates["eligible_for_shadow"] = all(gates.values())
    comparison = {
        "candidate_short_horizon_wape": candidate_short.get("wape"),
        "best_short_horizon_baseline": best_short_name,
        "best_short_horizon_baseline_wape": best_short_wape,
        "short_horizon_wape_improvement": short_improvement,
        "candidate_overall_wape": candidate_overall.get("wape"),
        "best_overall_baseline": best_overall_name,
        "best_overall_baseline_wape": best_overall_wape,
        "overall_wape_improvement": overall_improvement,
        "best_p90_pinball_baseline": best_pinball_name,
        "best_p90_pinball": best_p90_pinball,
        "best_risk_baseline": best_risk_name,
        "best_risk_loss": best_risk_loss,
        "risk_loss_improvement": risk_improvement,
        "weekly_folds": weekly_folds,
        "weekly_wins": weekly_wins,
    }
    return comparison, gates


def _best_metric(metrics: dict[str, dict[str, Any]], field: str) -> tuple[str | None, float | None]:
    available = [
        (name, float(values[field]))
        for name, values in metrics.items()
        if values.get(field) is not None
    ]
    return min(available, key=lambda item: item[1]) if available else (None, None)


def _relative_improvement(baseline: float | None, candidate: float | None) -> float | None:
    if baseline is None or candidate is None or baseline <= 0:
        return None
    return (baseline - candidate) / baseline


def _weekly_wins(
    candidate: BacktestResult,
    baselines: dict[str, BacktestResult],
) -> tuple[int, int]:
    candidate_weeks = candidate.metrics.get("calendar_weeks", {})
    wins = 0
    comparable = 0
    for week, candidate_metrics in candidate_weeks.items():
        candidate_wape = candidate_metrics.get("wape")
        baseline_values = [
            result.metrics.get("calendar_weeks", {}).get(week, {}).get("wape")
            for result in baselines.values()
        ]
        baseline_values = [float(value) for value in baseline_values if value is not None]
        if candidate_wape is None or not baseline_values:
            continue
        comparable += 1
        if float(candidate_wape) < min(baseline_values):
            wins += 1
    return comparable, wins


async def run_backtest(
    site_id: str,
    group_id: int,
    *,
    history_days: int,
    holdout_days: int,
    origin_step_hours: int,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    as_of = now.replace(minute=0, second=0, microsecond=0)
    start_at = as_of - timedelta(days=history_days)
    evaluation_start = as_of - timedelta(days=holdout_days)
    await connect_to_mongo()
    try:
        db = get_db()
        site = await get_site(db, site_id, include_token=True)
        if site is None:
            raise LookupError("sub2api site not found")
        sql_dsn = str(site.get("sql_dsn") or "").strip()
        if not sql_dsn:
            raise ValueError("SQL_DSN is not configured")
        history = await fetch_group_hourly_observations(
            sql_dsn,
            group_id=group_id,
            start_at=start_at,
            end_at=as_of,
        )
        forecast = forecast_hourly_demand(history, as_of=as_of, horizons=HORIZONS)
        candidate = rolling_origin_backtest(
            history,
            forecaster=forecast_hourly_demand,
            horizons=HORIZONS,
            minimum_history_hours=MINIMUM_HISTORY_HOURS,
            origin_step_hours=origin_step_hours,
            evaluation_start=evaluation_start,
        )
        baselines = {
            name: rolling_origin_backtest(
                history,
                forecaster=forecaster,
                horizons=HORIZONS,
                minimum_history_hours=MINIMUM_HISTORY_HOURS,
                origin_step_hours=origin_step_hours,
                evaluation_start=evaluation_start,
            )
            for name, forecaster in BASELINE_FORECASTERS.items()
        }
        return build_report(
            site_id=site_id,
            group_id=group_id,
            history=history,
            forecast=forecast,
            candidate=candidate,
            baselines=baselines,
            evaluation_start=evaluation_start,
            origin_step_hours=origin_step_hours,
        )
    finally:
        await close_mongo_connection()


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise TypeError(f"cannot serialize {type(value).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest a non-deep-learning hourly Sub2API group demand forecast")
    parser.add_argument("site_id", help="Configured Sub2API account-pool site ID")
    parser.add_argument("group_id", type=int, help="Sub2API group ID")
    parser.add_argument("--history-days", type=int, default=60)
    parser.add_argument("--holdout-days", type=int, default=7)
    parser.add_argument("--origin-step-hours", type=int, default=1)
    arguments = parser.parse_args()
    if arguments.history_days < 8:
        parser.error("--history-days must be at least 8")
    if arguments.holdout_days < 1 or arguments.holdout_days >= arguments.history_days:
        parser.error("--holdout-days must be positive and shorter than history")
    if arguments.origin_step_hours < 1:
        parser.error("--origin-step-hours must be positive")
    report = asyncio.run(
        run_backtest(
            arguments.site_id,
            arguments.group_id,
            history_days=arguments.history_days,
            holdout_days=arguments.holdout_days,
            origin_step_hours=arguments.origin_step_hours,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default, sort_keys=True))


if __name__ == "__main__":
    main()
