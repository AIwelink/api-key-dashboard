from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.sub2api.cache import get_site
from app.modules.sub2api.hourly_forecast import forecast_hourly_demand
from app.modules.sub2api.minute_forecast_repository import (
    MinuteObservation,
    fetch_group_minute_observations,
)
from app.modules.sub2api.regime_nowcast_backtest import (
    RegimeNowcastBacktestResult,
    rolling_minute_nowcast_backtest,
)


SCHEMA_VERSION = "regime_nowcast_backtest.v1"
MINIMUM_HISTORY_HOURS = 7 * 24


def build_report(
    *,
    site_id: str,
    group_id: int,
    history: list[MinuteObservation],
    backtest: RegimeNowcastBacktestResult,
    evaluation_start: datetime,
    issue_step_minutes: int,
) -> dict[str, Any]:
    candidate = backtest.strategies["regime_aware"].metrics["overall"]
    current_max = backtest.strategies["current_max"].metrics["overall"]
    realtime = backtest.strategies["realtime_only"].metrics["overall"]
    risk_improvement = _relative_improvement(current_max.get("risk_loss"), candidate.get("risk_loss"))
    wape_change = _relative_change(realtime.get("wape"), candidate.get("wape"))
    coverage = candidate.get("coverage")
    median_delay = backtest.surge_metrics.get("median_detection_delay_minutes")
    gates = {
        "p90_calibrated": coverage is not None and 0.85 <= float(coverage) <= 0.95,
        "risk_loss_improvement": risk_improvement is not None and risk_improvement >= 0.30,
        "wape_not_worse_than_realtime": wape_change is not None and wape_change <= 0,
        "surge_detection_timely": (
            backtest.surge_metrics.get("events", 0) > 0
            and median_delay is not None
            and float(median_delay) <= 5
        ),
        "no_future_data": backtest.no_future_data,
    }
    gates["eligible_for_v2"] = all(gates.values())
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(UTC),
        "site_id": site_id,
        "group_id": int(group_id),
        "timezone": "Asia/Shanghai",
        "history": {
            "start_at": history[0].bucket_at,
            "end_at": history[-1].bucket_at + timedelta(minutes=1),
            "minutes": len(history),
            "nonzero_minutes": sum(1 for item in history if item.account_cost > 0),
            "evaluation_start": evaluation_start,
            "origins": backtest.origins,
        },
        "parameters": {
            "minimum_history_hours": MINIMUM_HISTORY_HOURS,
            "issue_step_minutes": int(issue_step_minutes),
            "underforecast_weight": 5,
            "surge_confirmation": "2_of_3_minutes",
            "surge_match_window_minutes": backtest.surge_metrics.get("match_window_minutes"),
        },
        "strategies": {
            name: {
                "records": len(result.records),
                **result.metrics,
            }
            for name, result in backtest.strategies.items()
        },
        "surge_detection": backtest.surge_metrics,
        "comparison": {
            "risk_loss_improvement_vs_current_max": risk_improvement,
            "wape_change_vs_realtime_only": wape_change,
            "candidate_coverage": coverage,
            "candidate_wape": candidate.get("wape"),
            "candidate_risk_loss": candidate.get("risk_loss"),
            "current_max_risk_loss": current_max.get("risk_loss"),
            "realtime_only_wape": realtime.get("wape"),
        },
        "promotion_gates": gates,
    }


async def run_backtest(
    site_id: str,
    group_id: int,
    *,
    history_days: int,
    holdout_days: int,
    issue_step_minutes: int,
) -> dict[str, Any]:
    end_at = datetime.now(UTC).replace(second=0, microsecond=0)
    start_at = end_at - timedelta(days=history_days)
    evaluation_start = end_at - timedelta(days=holdout_days)
    await connect_to_mongo()
    try:
        db = get_db()
        site = await get_site(db, site_id, include_token=True)
        if site is None:
            raise LookupError("sub2api site not found")
        sql_dsn = str(site.get("sql_dsn") or "").strip()
        if not sql_dsn:
            raise ValueError("SQL_DSN is not configured")
        history = await fetch_group_minute_observations(
            sql_dsn,
            group_id=group_id,
            start_at=start_at,
            end_at=end_at,
        )
        backtest = rolling_minute_nowcast_backtest(
            history,
            forecaster=forecast_hourly_demand,
            minimum_history_hours=MINIMUM_HISTORY_HOURS,
            evaluation_start=evaluation_start,
            issue_step_minutes=issue_step_minutes,
        )
        return build_report(
            site_id=site_id,
            group_id=group_id,
            history=history,
            backtest=backtest,
            evaluation_start=evaluation_start,
            issue_step_minutes=issue_step_minutes,
        )
    finally:
        await close_mongo_connection()


def _relative_improvement(baseline: Any, candidate: Any) -> float | None:
    if baseline is None or candidate is None or float(baseline) <= 0:
        return None
    return (float(baseline) - float(candidate)) / float(baseline)


def _relative_change(baseline: Any, candidate: Any) -> float | None:
    if baseline is None or candidate is None or float(baseline) <= 0:
        return None
    return (float(candidate) - float(baseline)) / float(baseline)


def _json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    raise TypeError(f"cannot serialize {type(value).__name__}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest the regime-aware Sub2API current-hour Nowcast")
    parser.add_argument("site_id", help="Configured Sub2API account-pool site ID")
    parser.add_argument("group_id", type=int, help="Sub2API group ID")
    parser.add_argument("--history-days", type=int, default=60)
    parser.add_argument("--holdout-days", type=int, default=7)
    parser.add_argument("--issue-step-minutes", type=int, default=5)
    arguments = parser.parse_args()
    if arguments.history_days < 8:
        parser.error("--history-days must be at least 8")
    if arguments.holdout_days < 1 or arguments.holdout_days >= arguments.history_days:
        parser.error("--holdout-days must be positive and shorter than history")
    if arguments.issue_step_minutes < 1 or 60 % arguments.issue_step_minutes:
        parser.error("--issue-step-minutes must divide 60")
    report = asyncio.run(
        run_backtest(
            arguments.site_id,
            arguments.group_id,
            history_days=arguments.history_days,
            holdout_days=arguments.holdout_days,
            issue_step_minutes=arguments.issue_step_minutes,
        )
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, default=_json_default, sort_keys=True))


if __name__ == "__main__":
    main()
