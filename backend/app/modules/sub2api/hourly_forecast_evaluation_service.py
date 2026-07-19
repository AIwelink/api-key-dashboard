from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.sub2api.hourly_forecast import HourlyObservation
from app.modules.sub2api.hourly_forecast_evaluation import (
    build_hourly_evaluation,
    build_nowcast_evaluation,
    capacity_constraint_from_metrics,
    summarize_forecast_accuracy,
)
from app.modules.sub2api.hourly_forecast_repository import fetch_group_hourly_observations
from app.utils import now_utc


logger = logging.getLogger("app.sub2api_forecast_accuracy")

PROVISIONAL_DELAY = timedelta(minutes=15)
FINAL_DELAY = timedelta(minutes=90)
INITIAL_LOOKBACK = timedelta(days=7)
INCREMENTAL_LOOKBACK = timedelta(hours=4)
EVALUATOR_INTERVAL_SECONDS = 10 * 60
FORECAST_MAX_HORIZON = timedelta(hours=25)

ObservationFetcher = Callable[..., Awaitable[list[HourlyObservation]]]


async def settle_group_forecast_accuracy(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    sql_dsn: str,
    forecasts: list[dict[str, Any]],
    capacity_samples: list[dict[str, Any]],
    now: datetime,
    observation_fetcher: ObservationFetcher = fetch_group_hourly_observations,
) -> dict[str, Any]:
    evaluated_at = _as_utc(now)
    candidates = [
        *_hourly_candidates(forecasts, now=evaluated_at),
        *_nowcast_candidates(capacity_samples, now=evaluated_at),
    ]
    if not candidates:
        return _settlement_result(site_id, group_id)

    existing = await _existing_statuses(db, [candidate["evaluation_id"] for candidate in candidates])
    pending = [
        candidate
        for candidate in candidates
        if existing.get(candidate["evaluation_id"]) != "final"
        and not (
            existing.get(candidate["evaluation_id"]) == "provisional"
            and candidate["status"] == "provisional"
        )
    ]
    if not pending:
        return _settlement_result(site_id, group_id)

    target_hours = sorted({candidate["target_at"] for candidate in pending})
    observations = await observation_fetcher(
        str(sql_dsn),
        group_id=int(group_id),
        start_at=target_hours[0],
        end_at=target_hours[-1] + timedelta(hours=1),
    )
    actual_by_hour = {observation.bucket_at: observation for observation in observations}
    capacity_context = _hourly_capacity_context(capacity_samples)
    documents = []
    for candidate in pending:
        target_at = candidate["target_at"]
        actual = actual_by_hour.get(
            target_at,
            HourlyObservation(target_at, account_cost=0.0, requests=0.0, total_tokens=0.0),
        )
        if candidate["kind"] == "hourly":
            forecast_context = {
                **candidate["forecast"],
                **capacity_context.get(target_at, {}),
            }
            document = build_hourly_evaluation(
                forecast_context,
                candidate["point"],
                actual_account_cost=actual.account_cost,
                actual_requests=actual.requests,
                actual_total_tokens=actual.total_tokens,
                evaluated_at=evaluated_at,
                status=candidate["status"],
            )
        else:
            document = build_nowcast_evaluation(
                candidate["sample"],
                actual_account_cost=actual.account_cost,
                actual_requests=actual.requests,
                actual_total_tokens=actual.total_tokens,
                evaluated_at=evaluated_at,
                status=candidate["status"],
            )
        documents.append(document)

    await asyncio.gather(
        *(
            db.sub2api_forecast_evaluations.replace_one(
                {"_id": document["_id"]},
                document,
                upsert=True,
            )
            for document in documents
        )
    )
    await refresh_forecast_accuracy_summary(
        db,
        site_id=site_id,
        group_id=group_id,
        now=evaluated_at,
    )
    return {
        **_settlement_result(site_id, group_id),
        "settled": len(documents),
        "final": sum(1 for document in documents if document["status"] == "final"),
        "provisional": sum(1 for document in documents if document["status"] == "provisional"),
    }


async def evaluate_forecast_accuracy_once(
    db: AsyncIOMotorDatabase,
    *,
    lookback: timedelta = INCREMENTAL_LOOKBACK,
    now: datetime | None = None,
    observation_fetcher: ObservationFetcher = fetch_group_hourly_observations,
) -> dict[str, Any]:
    evaluated_at = _as_utc(now or now_utc())
    target_start = evaluated_at - lookback
    forecast_start = target_start - FORECAST_MAX_HORIZON
    forecasts = [
        document
        async for document in db.sub2api_hourly_forecasts.find(
            {"as_of": {"$gte": forecast_start}}
        )
    ]
    capacity_samples = [
        document
        async for document in db.sub2api_capacity_samples.find(
            {"bucket_at": {"$gte": target_start}}
        )
    ]
    grouped_forecasts: defaultdict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    grouped_samples: defaultdict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for forecast in forecasts:
        key = _site_group_key(forecast)
        if key is not None:
            grouped_forecasts[key].append(forecast)
    for sample in capacity_samples:
        key = _site_group_key(sample)
        if key is not None:
            grouped_samples[key].append(sample)
    keys = sorted(set(grouped_forecasts) | set(grouped_samples))
    site_documents: dict[str, dict[str, Any] | None] = {}

    async def settle_one(key: tuple[str, int]) -> dict[str, Any]:
        site_id, group_id = key
        if site_id not in site_documents:
            site_documents[site_id] = await db.sub2api_sites.find_one(
                {"_id": site_id, "status": {"$ne": "deleted"}}
            )
        site = site_documents[site_id]
        sql_dsn = str((site or {}).get("sql_dsn") or "").strip()
        if not sql_dsn:
            return {
                **_settlement_result(site_id, group_id),
                "ok": False,
                "message": "Sub2API SQL_DSN is required for forecast evaluation",
            }
        try:
            return await settle_group_forecast_accuracy(
                db,
                site_id=site_id,
                group_id=group_id,
                sql_dsn=sql_dsn,
                forecasts=[
                    forecast
                    for forecast in grouped_forecasts[key]
                    if _forecast_has_target_since(forecast, target_start)
                ],
                capacity_samples=grouped_samples[key],
                now=evaluated_at,
                observation_fetcher=observation_fetcher,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - one group must not block settlement for other groups.
            logger.warning(
                "sub2api_forecast_accuracy_group_failed site_id=%s group_id=%s error=%s",
                site_id,
                group_id,
                exc,
            )
            return {**_settlement_result(site_id, group_id), "ok": False, "message": str(exc)}

    results = await asyncio.gather(*(settle_one(key) for key in keys))
    summary = {
        "ok": True,
        "groups": len(keys),
        "failed": sum(1 for result in results if result.get("ok") is False),
        "settled": sum(int(result.get("settled") or 0) for result in results),
        "final": sum(int(result.get("final") or 0) for result in results),
        "provisional": sum(int(result.get("provisional") or 0) for result in results),
        "results": results,
    }
    logger.info(
        "sub2api_forecast_accuracy_finished groups=%s settled=%s final=%s provisional=%s failed=%s",
        summary["groups"],
        summary["settled"],
        summary["final"],
        summary["provisional"],
        summary["failed"],
    )
    return summary


async def refresh_forecast_accuracy_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    evaluated_at = _as_utc(now or now_utc())
    evaluations = [
        document
        async for document in db.sub2api_forecast_evaluations.find(
            {
                "site_id": site_id,
                "group_id": int(group_id),
                "status": "final",
                "target_at": {"$gte": evaluated_at - timedelta(days=28)},
            }
        )
    ]
    summary = summarize_forecast_accuracy(
        evaluations,
        site_id=site_id,
        group_id=group_id,
        now=evaluated_at,
    )
    document = {"_id": f"{site_id}:{int(group_id)}", **summary}
    await db.sub2api_forecast_accuracy_summaries.replace_one(
        {"_id": document["_id"]},
        document,
        upsert=True,
    )
    return summary


async def get_forecast_accuracy_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
) -> dict[str, Any]:
    collection = getattr(db, "sub2api_forecast_accuracy_summaries", None)
    if collection is not None:
        document = await collection.find_one({"site_id": site_id, "group_id": int(group_id)})
        if isinstance(document, dict):
            return {key: value for key, value in document.items() if key != "_id"}
    return summarize_forecast_accuracy(
        [],
        site_id=site_id,
        group_id=group_id,
        now=now_utc(),
    )


async def forecast_accuracy_evaluator_loop(db: AsyncIOMotorDatabase) -> None:
    lookback = INITIAL_LOOKBACK
    while True:
        started = time.monotonic()
        try:
            await evaluate_forecast_accuracy_once(db, lookback=lookback)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("sub2api_forecast_accuracy_evaluator_failed")
        lookback = INCREMENTAL_LOOKBACK
        elapsed_seconds = time.monotonic() - started
        await asyncio.sleep(max(0.0, EVALUATOR_INTERVAL_SECONDS - elapsed_seconds))


def _hourly_candidates(forecasts: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    candidates = []
    for forecast in forecasts:
        forecast_id = str(forecast.get("_id") or "").strip()
        points = forecast.get("points") if isinstance(forecast.get("points"), list) else []
        for point in points:
            if not isinstance(point, dict):
                continue
            target_at = _optional_utc(point.get("target_at"))
            try:
                horizon = int(point.get("horizon"))
            except (TypeError, ValueError):
                continue
            status = _settlement_status(target_at, now=now)
            if not forecast_id or target_at is None or status is None:
                continue
            candidates.append(
                {
                    "evaluation_id": f"hourly:{forecast_id}:{horizon}",
                    "kind": "hourly",
                    "status": status,
                    "target_at": target_at,
                    "forecast": forecast,
                    "point": point,
                }
            )
    return candidates


def _nowcast_candidates(samples: list[dict[str, Any]], *, now: datetime) -> list[dict[str, Any]]:
    candidates = []
    for sample in samples:
        metrics = sample.get("metrics") if isinstance(sample.get("metrics"), dict) else {}
        sample_id = str(sample.get("_id") or "").strip()
        issued_at = _optional_utc(sample.get("sampled_at") or sample.get("bucket_at"))
        if metrics.get("forecast_nowcast_applied") is not True or not sample_id or issued_at is None:
            continue
        target_at = issued_at.replace(minute=0, second=0, microsecond=0)
        status = _settlement_status(target_at, now=now)
        if status is None:
            continue
        candidates.append(
            {
                "evaluation_id": f"nowcast:{sample_id}",
                "kind": "nowcast",
                "status": status,
                "target_at": target_at,
                "sample": sample,
            }
        )
    return candidates


async def _existing_statuses(db: AsyncIOMotorDatabase, evaluation_ids: list[str]) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for start in range(0, len(evaluation_ids), 1_000):
        chunk = evaluation_ids[start:start + 1_000]
        async for document in db.sub2api_forecast_evaluations.find(
            {"_id": {"$in": chunk}},
            {"_id": 1, "status": 1},
        ):
            statuses[str(document.get("_id"))] = str(document.get("status") or "")
    return statuses


def _settlement_status(target_at: datetime | None, *, now: datetime) -> str | None:
    if target_at is None:
        return None
    hour_end = target_at + timedelta(hours=1)
    if hour_end + FINAL_DELAY <= now:
        return "final"
    if hour_end + PROVISIONAL_DELAY <= now:
        return "provisional"
    return None


def _forecast_has_target_since(forecast: dict[str, Any], target_start: datetime) -> bool:
    points = forecast.get("points") if isinstance(forecast.get("points"), list) else []
    return any(
        isinstance(point, dict)
        and (_optional_utc(point.get("target_at")) or datetime.min.replace(tzinfo=UTC)) >= target_start
        for point in points
    )


def _site_group_key(document: dict[str, Any]) -> tuple[str, int] | None:
    site_id = str(document.get("site_id") or "").strip()
    try:
        group_id = int(document.get("group_id"))
    except (TypeError, ValueError):
        return None
    if not site_id or group_id <= 0:
        return None
    return site_id, group_id


def _hourly_capacity_context(samples: list[dict[str, Any]]) -> dict[datetime, dict[str, Any]]:
    result: dict[datetime, dict[str, Any]] = {}
    ordered = sorted(
        samples,
        key=lambda sample: _optional_utc(sample.get("sampled_at") or sample.get("bucket_at"))
        or datetime.min.replace(tzinfo=UTC),
    )
    for sample in ordered:
        sampled_at = _optional_utc(sample.get("sampled_at") or sample.get("bucket_at"))
        metrics = sample.get("metrics") if isinstance(sample.get("metrics"), dict) else {}
        if sampled_at is None:
            continue
        target_at = sampled_at.replace(minute=0, second=0, microsecond=0)
        constrained, _reasons = capacity_constraint_from_metrics(metrics)
        current = result.setdefault(
            target_at,
            {"pressure_stage": "unknown", "capacity_constrained": False},
        )
        pressure_stage = str(metrics.get("pressure_stage") or "").strip()
        if pressure_stage:
            current["pressure_stage"] = pressure_stage
        current["capacity_constrained"] = bool(current["capacity_constrained"] or constrained)
    return result


def _settlement_result(site_id: str, group_id: int) -> dict[str, Any]:
    return {
        "ok": True,
        "site_id": site_id,
        "group_id": int(group_id),
        "settled": 0,
        "final": 0,
        "provisional": 0,
    }


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _optional_utc(value: Any) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    return _as_utc(value)
