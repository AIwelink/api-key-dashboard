from __future__ import annotations

import math
import re
import statistics
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from pymongo import UpdateOne


WINDOW_FIELDS = {
    "five_hour": {
        "percent": "codex_5h_used_percent",
        "reset_at": "codex_5h_reset_at",
        "window_minutes": "codex_5h_window_minutes",
        "cost": "codex_5h_actual_cost",
    },
    "seven_day": {
        "percent": "codex_7d_used_percent",
        "reset_at": "codex_7d_reset_at",
        "window_minutes": "codex_7d_window_minutes",
        "cost": "codex_7d_actual_cost",
    },
}

MAX_SOURCE_AGE = timedelta(minutes=5)
RESET_JITTER = timedelta(minutes=2)
MAX_WINDOW_MINUTES = 31 * 24 * 60
ACCOUNT_TYPES = {"free", "plus", "special_plus", "team", "special_team", "bug_team", "k12", "pro", "unknown"}
KNOWN_ACCOUNT_TYPES = ("free", "plus", "special_plus", "team", "special_team", "bug_team", "k12", "pro")
ACCOUNT_TYPE_RECLASSIFICATIONS = {
    ("team", "bug_team"),
    ("plus", "special_plus"),
    ("team", "special_team"),
    ("bug_team", "special_team"),
}
STATE_RETENTION = timedelta(days=30)
SAMPLE_RETENTION = timedelta(days=90)
RECENT_SAMPLE_LIMIT = 100
SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")

_INVALID_CREDENTIAL_STATUSES = {
    "error",
    "expired",
    "failed",
    "invalid",
    "missing",
    "revoked",
}
_VALID_CREDENTIAL_STATUSES = {"active", "ok", "valid"}
_CREDENTIAL_ERROR_PATTERN = re.compile(
    r"401|unauthori[sz]ed|authentication failed|credential|"
    r"refresh[_ -]?token|token[_ -]?(?:invalid|invalidated|revoked|expired)|"
    r"token[_ -]?refresh[_ -]?failed|oauth[_ -]?(?:error|failed)",
    re.IGNORECASE,
)


def build_window_observation(
    account: dict[str, Any],
    *,
    window_type: str,
    account_type: str,
    observed_at: datetime,
) -> dict[str, Any]:
    fields = WINDOW_FIELDS.get(window_type) if type(window_type) is str else None
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    canonical_observed_at = _utc_datetime(observed_at)
    remote_account_id = account.get("id")

    if fields is None:
        return _observation_result(
            reason="unsupported_window_type",
            remote_account_id=remote_account_id,
            window_type=window_type,
            account_type=account_type,
            observed_at=canonical_observed_at,
        )

    raw_reset_at = _first(account, extra, fields["reset_at"])
    raw_synced_at = _first(account, extra, "codex_usage_synced_at")
    used_percent = _number(_first(account, extra, fields["percent"]))
    reset_at = _utc_datetime(raw_reset_at)
    window_minutes = _number(_first(account, extra, fields["window_minutes"]))
    cost_usd = _number(_first(account, extra, fields["cost"]))
    synced_at = _utc_datetime(raw_synced_at)
    invalid_reason = _observation_invalid_reason(
        account=account,
        extra=extra,
        remote_account_id=remote_account_id,
        account_type=account_type,
        used_percent=used_percent,
        reset_at=reset_at,
        raw_reset_at=raw_reset_at,
        window_minutes=window_minutes,
        cost_usd=cost_usd,
        synced_at=synced_at,
        raw_synced_at=raw_synced_at,
        observed_at=canonical_observed_at,
    )
    return _observation_result(
        reason=invalid_reason,
        remote_account_id=remote_account_id,
        window_type=window_type,
        window_reset_at=reset_at,
        window_minutes=window_minutes,
        used_percent=used_percent,
        cost_usd=cost_usd,
        usage_synced_at=synced_at,
        observed_at=canonical_observed_at,
        account_type=account_type,
    )


def state_from_observation(observation: dict[str, Any]) -> dict[str, Any]:
    used_percent = observation["used_percent"]
    sample_eligible = used_percent < 100
    return {
        "remote_account_id": observation.get("remote_account_id"),
        "window_type": observation.get("window_type"),
        "window_reset_at": observation["window_reset_at"],
        "last_under_limit_percent": used_percent if sample_eligible else None,
        "last_under_limit_cost_usd": observation["cost_usd"] if sample_eligible else None,
        "last_observed_at": observation["observed_at"],
        "account_type": observation["account_type"],
        "hit_recorded": False,
        "sample_eligible": sample_eligible,
    }


def evaluate_transition(
    state: dict[str, Any] | None,
    observation: dict[str, Any],
) -> dict[str, Any]:
    if observation.get("quality") != "valid":
        return {
            "action": "invalid",
            "reason": observation.get("reason") or "invalid_observation",
            "state": state,
        }

    observed_at = _utc_datetime(observation.get("observed_at"))
    used_percent = _number(observation.get("used_percent"))
    cost_usd = _number(observation.get("cost_usd"))
    if observed_at is None:
        return {"action": "invalid", "reason": "invalid_observed_at", "state": state}
    if used_percent is None or not 0 <= used_percent <= 100:
        return {"action": "invalid", "reason": "invalid_percent", "state": state}
    if cost_usd is None or cost_usd < 0:
        return {"action": "invalid", "reason": "invalid_cost", "state": state}

    if state is None:
        return {"action": "baseline", "state": state_from_observation(observation)}

    last_observed_at = _utc_datetime(state.get("last_observed_at"))
    if last_observed_at is not None and observed_at <= last_observed_at:
        return {"action": "ignore", "reason": "late_observation", "state": state}

    if _identity_changed(state, observation):
        return {"action": "invalid", "reason": "observation_identity_changed", "state": state}

    previous_account_type = state.get("account_type")
    observed_account_type = observation.get("account_type")
    if (previous_account_type, observed_account_type) in ACCOUNT_TYPE_RECLASSIFICATIONS:
        return {
            "action": "baseline",
            "reason": "account_type_reclassified",
            "state": state_from_observation(observation),
        }
    if previous_account_type != observed_account_type:
        return {"action": "invalid", "reason": "account_type_changed", "state": state}

    state_reset_at = _utc_datetime(state.get("window_reset_at"))
    observation_reset_at = _utc_datetime(observation.get("window_reset_at"))
    if state_reset_at is None or observation_reset_at is None:
        return {"action": "invalid", "reason": "invalid_reset_at", "state": state}
    if observation_reset_at < state_reset_at - RESET_JITTER:
        return {"action": "ignore", "reason": "reset_regression", "state": state}
    if observation_reset_at > state_reset_at + RESET_JITTER:
        return {"action": "baseline", "state": state_from_observation(observation)}

    if state.get("hit_recorded"):
        return {"action": "ignore", "reason": "window_already_recorded", "state": state}

    sample_eligible = _sample_eligible(state)
    previous_percent = _number(state.get("last_under_limit_percent"))
    previous_cost = _number(state.get("last_under_limit_cost_usd"))
    if previous_cost is not None and cost_usd < previous_cost:
        return {"action": "invalid", "reason": "cost_rollback", "state": state}

    if used_percent < 100:
        return {
            "action": "update",
            "state": _state_with_under_limit_observation(
                state,
                observation,
                observed_at=observed_at,
                sample_eligible=sample_eligible,
            ),
        }

    if not sample_eligible:
        return {"action": "ignore", "reason": "window_ineligible", "state": state}
    if previous_percent is None or previous_cost is None:
        return {"action": "ignore", "reason": "no_under_limit_baseline", "state": state}
    if cost_usd <= 0:
        return {"action": "invalid", "reason": "invalid_cost", "state": state}

    return {
        "action": "candidate",
        "previous_percent": previous_percent,
        "previous_cost_usd": previous_cost,
        "observed_limit_usd": cost_usd,
        "state": {
            **state,
            "hit_recorded": True,
            "last_observed_at": observed_at,
        },
    }


def classify_candidate(value: Any, *, accepted_values: Any) -> dict[str, Any]:
    candidate = _safe_number(value)
    if candidate is None:
        return _classification_result("invalid", "invalid_candidate")

    try:
        history = list(accepted_values)
    except Exception:
        return _classification_result("invalid", "invalid_baseline")

    baseline: list[float] = []
    for accepted_value in history[-100:]:
        normalized = _safe_number(accepted_value)
        if normalized is None:
            return _classification_result("invalid", "invalid_baseline")
        baseline.append(normalized)

    if len(history) < 5:
        return _classification_result("accepted", "baseline_establishing")

    baseline_median = float(statistics.median(baseline))
    baseline_mad = float(
        statistics.median(abs(accepted_value - baseline_median) for accepted_value in baseline)
    )
    if not math.isfinite(baseline_median) or not math.isfinite(baseline_mad):
        return _classification_result(
            "invalid",
            "invalid_baseline",
            median=baseline_median,
            mad=baseline_mad,
        )
    if baseline_median == 0:
        return _classification_result(
            "invalid",
            "zero_median",
            median=baseline_median,
            mad=baseline_mad,
        )

    tolerance = max(0.25, 3 * baseline_mad / abs(baseline_median))
    deviation = abs(candidate - baseline_median) / abs(baseline_median)
    direction = (
        "above"
        if candidate > baseline_median
        else "below"
        if candidate < baseline_median
        else "same"
    )
    within_tolerance = deviation <= tolerance
    return _classification_result(
        "accepted" if within_tolerance else "outlier",
        "within_tolerance" if within_tolerance else "outside_tolerance",
        direction=direction,
        median=baseline_median,
        mad=baseline_mad,
        tolerance=tolerance,
        deviation=deviation,
    )


def new_generation_candidate(outliers: Any) -> dict[str, Any]:
    try:
        samples = list(outliers)
    except Exception:
        return _generation_result(False, "invalid_outliers")

    if len(samples) < 5:
        return _generation_result(False, "insufficient_outliers")
    if any(not isinstance(sample, dict) for sample in samples):
        return _generation_result(False, "invalid_outliers")

    directions = [sample.get("direction") for sample in samples]
    if any(direction not in {"above", "below"} for direction in directions):
        return _generation_result(False, "invalid_direction")
    if len(set(directions)) != 1:
        return _generation_result(False, "mixed_direction")
    direction = directions[0]

    values: list[float] = []
    for sample in samples:
        raw_value = sample.get("value", sample.get("observed_limit_usd"))
        normalized = _strict_finite_number(raw_value)
        if normalized is None or normalized <= 0:
            return _generation_result(False, "invalid_values", direction=direction)
        values.append(normalized)

    account_ids = {
        account_id
        for sample in samples
        if (account_id := _remote_account_key(sample.get("remote_account_id"))) is not None
    }
    if len(account_ids) < 3:
        return _generation_result(False, "insufficient_accounts", direction=direction)

    representative = float(statistics.median(values))
    value_range = max(values) - min(values)
    if not math.isfinite(representative) or not math.isfinite(value_range):
        return _generation_result(False, "invalid_values", direction=direction)
    relative_spread = value_range / representative
    spread_at_boundary = math.isclose(
        relative_spread,
        0.10,
        rel_tol=1e-12,
        abs_tol=1e-12,
    )
    if not math.isfinite(relative_spread) or (
        relative_spread > 0.10 and not spread_at_boundary
    ):
        return _generation_result(
            False,
            "dispersed_values",
            direction=direction,
            relative_spread=relative_spread,
        )

    return _generation_result(
        True,
        "tight_cluster",
        direction=direction,
        representative_value=representative,
        relative_spread=relative_spread,
    )


async def observe_account_quota_limits(
    db: Any,
    *,
    site_id: str,
    accounts: Any,
    observed_at: datetime,
    account_type_for: Any,
) -> dict[str, Any]:
    canonical_observed_at = _utc_datetime(observed_at)
    if canonical_observed_at is None:
        return _observation_counts(site_id=site_id, status="invalid")

    observations: list[tuple[str | None, dict[str, Any]]] = []
    for account in accounts:
        account_type = account_type_for(account)
        for window_type in WINDOW_FIELDS:
            observation = build_window_observation(
                account,
                window_type=window_type,
                account_type=account_type,
                observed_at=canonical_observed_at,
            )
            account_key = _remote_account_key(observation.get("remote_account_id"))
            state_id = (
                _state_id(site_id, account_key, window_type)
                if account_key is not None
                else None
            )
            observations.append((state_id, observation))

    state_ids = list(dict.fromkeys(state_id for state_id, _ in observations if state_id))
    states_by_id: dict[str, dict[str, Any]] = {}
    async for state in db.sub2api_quota_detection_states.find(
        {"_id": {"$in": state_ids}}
    ):
        state_id = state.get("_id")
        if isinstance(state_id, str):
            states_by_id[state_id] = state

    counts = _observation_counts(site_id=site_id, status="ok")
    counts["observed"] = len(observations)
    pending_states: dict[str, tuple[dict[str, Any], datetime | None]] = {}
    inserted_classifications: dict[str, str] = {}
    affected_rollups: set[tuple[str, str, str, int, str]] = set()

    for state_id, observation in observations:
        existing_state = states_by_id.get(state_id) if state_id is not None else None
        decision = evaluate_transition(existing_state, observation)
        action = decision["action"]
        if action == "invalid":
            counts["invalid"] += 1
            continue
        if action == "ignore":
            counts["ignored"] += 1
            continue

        next_state = decision.get("state")
        if state_id is not None and isinstance(next_state, dict):
            states_by_id[state_id] = next_state
            pending_states[state_id] = (
                _compact_state_document(
                    site_id=site_id,
                    state=next_state,
                    expires_at=canonical_observed_at + STATE_RETENTION,
                ),
                _utc_datetime(existing_state.get("last_observed_at"))
                if isinstance(existing_state, dict)
                else None,
            )

        if action == "baseline":
            counts["baseline"] += 1
            continue
        if action == "update":
            counts["updated"] += 1
            continue

        inserted = await _persist_candidate_sample(
            db,
            site_id=site_id,
            observation=observation,
            decision=decision,
            observed_at=canonical_observed_at,
        )
        sample_id = inserted["sample_id"]
        sample = inserted["sample"]
        classification = sample.get("classification")
        if inserted["inserted"]:
            inserted_classifications[sample_id] = classification
        else:
            counts["ignored"] += 1

        if classification == "accepted":
            affected_rollups.add(
                (
                    sample["site_id"],
                    sample["account_type"],
                    sample["window_type"],
                    sample["generation"],
                    _shanghai_date(sample["hit_at"]),
                )
            )
        elif classification == "outlier" and (
            inserted["inserted"]
            or sample.get("generation") == inserted["profile"].get("current_generation")
        ):
            promotion = await _promote_generation_if_ready(
                db,
                profile=inserted["profile"],
                observed_at=canonical_observed_at,
            )
            if promotion["promoted"]:
                for promoted_id in promotion["candidate_ids"]:
                    if promoted_id in inserted_classifications:
                        inserted_classifications[promoted_id] = "accepted"
                for local_date in promotion["local_dates"]:
                    affected_rollups.add(
                        (
                            site_id,
                            observation["account_type"],
                            observation["window_type"],
                            promotion["generation"],
                            local_date,
                        )
                    )

    for classification in inserted_classifications.values():
        if classification in {"accepted", "outlier", "invalid"}:
            counts[classification] += 1

    for rollup in sorted(affected_rollups):
        await _rebuild_daily_rollup(
            db,
            site_id=rollup[0],
            account_type=rollup[1],
            window_type=rollup[2],
            generation=rollup[3],
            local_date=rollup[4],
        )

    if pending_states:
        await db.sub2api_quota_detection_states.bulk_write(
            [
                _state_update_operation(state_id, state, previous_observed_at)
                for state_id, (state, previous_observed_at) in pending_states.items()
            ],
            ordered=False,
        )
    return counts


async def get_quota_detection_summary(db: Any, site_id: str) -> dict[str, Any]:
    profiles = [profile async for profile in db.sub2api_quota_limit_profiles.find({"site_id": site_id})]
    profile_by_dimension = {
        (profile.get("account_type"), profile.get("window_type")): profile
        for profile in profiles
        if profile.get("window_type") in WINDOW_FIELDS
    }
    totals: dict[tuple[str, str], dict[str, Any]] = {}
    async for rollup in db.sub2api_quota_limit_daily_rollups.find({"site_id": site_id}):
        dimension = (rollup.get("account_type"), rollup.get("window_type"))
        profile = profile_by_dimension.get(dimension)
        if not profile or rollup.get("generation") != profile.get("current_generation"):
            continue
        bucket = totals.setdefault(
            dimension,
            {"sample_count": 0, "sample_sum_usd": 0.0, "minimum_usd": None, "maximum_usd": None},
        )
        bucket["sample_count"] += _nonnegative_integer(rollup.get("sample_count"))
        bucket["sample_sum_usd"] += _strict_finite_number(rollup.get("sample_sum_usd")) or 0.0
        minimum = _strict_finite_number(rollup.get("sample_min_usd"))
        maximum = _strict_finite_number(rollup.get("sample_max_usd"))
        if minimum is not None:
            bucket["minimum_usd"] = minimum if bucket["minimum_usd"] is None else min(bucket["minimum_usd"], minimum)
        if maximum is not None:
            bucket["maximum_usd"] = maximum if bucket["maximum_usd"] is None else max(bucket["maximum_usd"], maximum)

    account_types = list(KNOWN_ACCOUNT_TYPES)
    if any(profile.get("account_type") == "unknown" for profile in profiles):
        account_types.append("unknown")
    items = [
        {
            "account_type": account_type,
            "five_hour": _summary_window(profile_by_dimension.get((account_type, "five_hour")), totals.get((account_type, "five_hour"))),
            "seven_day": _summary_window(profile_by_dimension.get((account_type, "seven_day")), totals.get((account_type, "seven_day"))),
        }
        for account_type in account_types
    ]
    evaluated = [
        parsed
        for parsed in (_utc_datetime(profile.get("last_evaluated_at")) for profile in profiles)
        if parsed is not None
    ]
    return {"site_id": site_id, "items": items, "last_evaluated_at": max(evaluated) if evaluated else None}


def _summary_window(profile: dict[str, Any] | None, totals: dict[str, Any] | None) -> dict[str, Any]:
    count = int((totals or {}).get("sample_count") or 0)
    sample_sum = float((totals or {}).get("sample_sum_usd") or 0.0)
    return {
        "average_usd": sample_sum / count if count else None,
        "minimum_usd": (totals or {}).get("minimum_usd"),
        "maximum_usd": (totals or {}).get("maximum_usd"),
        "sample_count": count,
        "generation": profile.get("current_generation") if profile else None,
        "generation_started_at": profile.get("generation_started_at") if profile else None,
    }


async def _profile_for_dimension(
    db: Any,
    *,
    site_id: str,
    account_type: str,
    window_type: str,
    observed_at: datetime,
) -> dict[str, Any]:
    profile_id = _profile_id(site_id, account_type, window_type)
    defaults = {
        "site_id": site_id,
        "account_type": account_type,
        "window_type": window_type,
        "current_generation": 1,
        "generation_started_at": observed_at,
    }
    await db.sub2api_quota_limit_profiles.update_one(
        {"_id": profile_id},
        {
            "$setOnInsert": defaults,
            "$set": {"last_evaluated_at": observed_at},
        },
        upsert=True,
    )
    profile = await db.sub2api_quota_limit_profiles.find_one({"_id": profile_id})
    profile = profile or {
        "_id": profile_id,
        **defaults,
        "last_evaluated_at": observed_at,
    }
    if isinstance(profile.get("pending_promotion"), dict):
        await _resume_pending_promotion(db, profile=profile)
        profile = {**profile, "pending_promotion": None}
    return profile


async def _recent_dimension_samples(
    db: Any,
    *,
    site_id: str,
    account_type: str,
    window_type: str,
    generation: int,
) -> list[float]:
    cursor = db.sub2api_quota_limit_samples.find(
        {
            "site_id": site_id,
            "account_type": account_type,
            "window_type": window_type,
            "generation": generation,
            "classification": "accepted",
        },
        {"observed_limit_usd": 1, "hit_at": 1},
    ).sort([("hit_at", -1), ("_id", -1)]).limit(RECENT_SAMPLE_LIMIT)
    newest_first: list[float] = []
    async for sample in cursor:
        value = _strict_finite_number(sample.get("observed_limit_usd"))
        if value is not None:
            newest_first.append(value)
    newest_first.reverse()
    return newest_first


async def _promote_generation_if_ready(
    db: Any,
    *,
    profile: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    generation = profile.get("current_generation")
    if isinstance(generation, bool) or not isinstance(generation, int) or generation < 1:
        return _promotion_result(False, "invalid_generation")

    samples = db.sub2api_quota_limit_samples
    cursor = samples.find(
        {
            "site_id": profile["site_id"],
            "account_type": profile["account_type"],
            "window_type": profile["window_type"],
            "generation": generation,
        },
        {
            "_id": 1,
            "remote_account_id": 1,
            "observed_limit_usd": 1,
            "classification": 1,
            "direction": 1,
            "hit_at": 1,
        },
    ).sort([("hit_at", -1), ("_id", -1)]).limit(5)
    candidates = [sample async for sample in cursor]
    if len(candidates) < 5 or any(
        sample.get("classification") != "outlier" for sample in candidates
    ):
        return _promotion_result(False, "insufficient_consecutive_outliers")

    generation_decision = new_generation_candidate(candidates)
    if not generation_decision["promote"]:
        return _promotion_result(False, generation_decision["reason"])

    new_generation = generation + 1
    candidate_ids = [str(sample["_id"]) for sample in candidates]
    local_dates = sorted(
        {
            _shanghai_date(sample.get("hit_at"))
            for sample in candidates
            if _utc_datetime(sample.get("hit_at")) is not None
        }
    )
    pending_promotion = {
        "from_generation": generation,
        "to_generation": new_generation,
        "candidate_ids": candidate_ids,
        "local_dates": local_dates,
    }
    result = await db.sub2api_quota_limit_profiles.update_one(
        {"_id": profile["_id"], "current_generation": generation},
        {
            "$set": {
                "current_generation": new_generation,
                "generation_started_at": observed_at,
                "last_evaluated_at": observed_at,
                "pending_promotion": pending_promotion,
            }
        },
    )
    if not _modified_one(result):
        return _promotion_result(False, "generation_changed")

    promoted_profile = {**profile, "current_generation": new_generation, "pending_promotion": pending_promotion}
    await _resume_pending_promotion(db, profile=promoted_profile)
    return _promotion_result(
        True,
        "promoted",
        generation=new_generation,
        candidate_ids=candidate_ids,
        local_dates=local_dates,
    )


async def _resume_pending_promotion(db: Any, *, profile: dict[str, Any]) -> None:
    pending = profile.get("pending_promotion")
    if not isinstance(pending, dict):
        return
    candidate_ids = pending.get("candidate_ids")
    from_generation = pending.get("from_generation")
    to_generation = pending.get("to_generation")
    if not isinstance(candidate_ids, list) or not candidate_ids or not isinstance(to_generation, int):
        raise ValueError("invalid pending quota promotion")
    result = await db.sub2api_quota_limit_samples.update_many(
        {
            "_id": {"$in": candidate_ids},
        },
        {
            "$set": {
                "classification": "accepted",
                "reason": "generation_promoted",
                "generation": to_generation,
            }
        },
    )
    if getattr(result, "matched_count", 0) != len(candidate_ids):
        raise RuntimeError("quota promotion samples incomplete")
    await db.sub2api_quota_limit_profiles.update_one(
        {
            "_id": profile["_id"],
            "current_generation": to_generation,
            "pending_promotion": pending,
        },
        {"$unset": {"pending_promotion": ""}},
    )


async def _rebuild_daily_rollup(
    db: Any,
    *,
    site_id: str,
    account_type: str,
    window_type: str,
    generation: int,
    local_date: str,
) -> dict[str, Any]:
    local_day = datetime.fromisoformat(local_date).date()
    local_start = datetime.combine(local_day, datetime.min.time(), tzinfo=SHANGHAI_TZ)
    utc_start = local_start.astimezone(UTC)
    utc_end = (local_start + timedelta(days=1)).astimezone(UTC)
    cursor = db.sub2api_quota_limit_samples.find(
        {
            "site_id": site_id,
            "account_type": account_type,
            "window_type": window_type,
            "generation": generation,
            "classification": "accepted",
            "hit_at": {"$gte": utc_start, "$lt": utc_end},
        },
        {"_id": 1, "observed_limit_usd": 1},
    )
    values_by_id: dict[str, float] = {}
    async for sample in cursor:
        sample_id = sample.get("_id")
        value = _strict_finite_number(sample.get("observed_limit_usd"))
        if sample_id is not None and value is not None:
            values_by_id[str(sample_id)] = value

    values = list(values_by_id.values())
    rollup_id = _rollup_id(
        site_id,
        account_type,
        window_type,
        generation,
        local_date,
    )
    document = {
        "_id": rollup_id,
        "site_id": site_id,
        "account_type": account_type,
        "window_type": window_type,
        "generation": generation,
        "local_date": local_date,
        "sample_count": len(values),
        "sample_sum_usd": float(math.fsum(values)),
        "sample_min_usd": min(values) if values else None,
        "sample_max_usd": max(values) if values else None,
    }
    await db.sub2api_quota_limit_daily_rollups.update_one(
        {"_id": rollup_id},
        {
            "$setOnInsert": {
                "site_id": site_id,
                "account_type": account_type,
                "window_type": window_type,
                "generation": generation,
                "local_date": local_date,
            },
            "$max": {
                "sample_count": document["sample_count"],
                "sample_sum_usd": document["sample_sum_usd"],
                "sample_max_usd": document["sample_max_usd"],
            },
            "$min": {"sample_min_usd": document["sample_min_usd"]},
        },
        upsert=True,
    )
    return document


async def _persist_candidate_sample(
    db: Any,
    *,
    site_id: str,
    observation: dict[str, Any],
    decision: dict[str, Any],
    observed_at: datetime,
) -> dict[str, Any]:
    profile = await _profile_for_dimension(
        db,
        site_id=site_id,
        account_type=observation["account_type"],
        window_type=observation["window_type"],
        observed_at=observed_at,
    )
    raw_generation = profile.get("current_generation")
    generation = (
        raw_generation
        if isinstance(raw_generation, int)
        and not isinstance(raw_generation, bool)
        and raw_generation >= 1
        else 1
    )
    accepted_values = await _recent_dimension_samples(
        db,
        site_id=site_id,
        account_type=observation["account_type"],
        window_type=observation["window_type"],
        generation=generation,
    )
    classification = classify_candidate(
        decision["observed_limit_usd"],
        accepted_values=accepted_values,
    )
    canonical_reset_at = decision["state"]["window_reset_at"]
    sample_id = _sample_id(
        site_id,
        observation["remote_account_id"],
        observation["window_type"],
        canonical_reset_at,
    )
    document = {
        "site_id": site_id,
        "remote_account_id": observation["remote_account_id"],
        "account_type": observation["account_type"],
        "window_type": observation["window_type"],
        "window_reset_at": canonical_reset_at,
        "hit_at": observation["observed_at"],
        "observed_limit_usd": decision["observed_limit_usd"],
        "previous_percent": decision["previous_percent"],
        "previous_cost_usd": decision["previous_cost_usd"],
        "classification": classification["classification"],
        "reason": classification["reason"],
        "direction": classification["direction"],
        "generation": generation,
        "expires_at": observed_at + SAMPLE_RETENTION,
    }
    result = await db.sub2api_quota_limit_samples.bulk_write(
        [
            UpdateOne(
                {"_id": sample_id},
                {"$setOnInsert": document},
                upsert=True,
            )
        ],
        ordered=False,
    )
    was_inserted = _bulk_inserted(result)
    persisted = (
        {"_id": sample_id, **document}
        if was_inserted
        else await db.sub2api_quota_limit_samples.find_one({"_id": sample_id})
    )
    if not isinstance(persisted, dict):
        persisted = {"_id": sample_id, **document}
    return {
        "inserted": was_inserted,
        "sample_id": sample_id,
        "sample": persisted,
        "profile": profile,
    }


def _compact_state_document(
    *,
    site_id: str,
    state: dict[str, Any],
    expires_at: datetime,
) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "remote_account_id": state.get("remote_account_id"),
        "window_type": state.get("window_type"),
        "window_reset_at": state.get("window_reset_at"),
        "last_under_limit_percent": state.get("last_under_limit_percent"),
        "last_under_limit_cost_usd": state.get("last_under_limit_cost_usd"),
        "last_observed_at": state.get("last_observed_at"),
        "account_type": state.get("account_type"),
        "hit_recorded": bool(state.get("hit_recorded")),
        "sample_eligible": bool(state.get("sample_eligible")),
        "expires_at": expires_at,
    }


def _state_update_operation(
    state_id: str,
    state: dict[str, Any],
    previous_observed_at: datetime | None,
) -> UpdateOne:
    if previous_observed_at is None:
        return UpdateOne(
            {"_id": state_id},
            {"$setOnInsert": state},
            upsert=True,
        )
    return UpdateOne(
        {"_id": state_id, "last_observed_at": previous_observed_at},
        {"$set": state},
        upsert=False,
    )


def _observation_counts(*, site_id: str, status: str) -> dict[str, Any]:
    return {
        "site_id": site_id,
        "status": status,
        "observed": 0,
        "accepted": 0,
        "outlier": 0,
        "invalid": 0,
        "ignored": 0,
        "baseline": 0,
        "updated": 0,
    }


def _promotion_result(
    promoted: bool,
    reason: str,
    *,
    generation: int | None = None,
    candidate_ids: list[str] | None = None,
    local_dates: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "promoted": promoted,
        "reason": reason,
        "generation": generation,
        "candidate_ids": candidate_ids or [],
        "local_dates": local_dates or [],
    }


def _state_id(site_id: str, account_key: str, window_type: str) -> str:
    return f"{site_id}:{account_key}:{window_type}"


def _profile_id(site_id: str, account_type: str, window_type: str) -> str:
    return f"{site_id}:{account_type}:{window_type}"


def _sample_id(
    site_id: str,
    remote_account_id: Any,
    window_type: str,
    reset_at: datetime,
) -> str:
    return f"{site_id}:{remote_account_id}:{window_type}:{_iso_z(reset_at)}"


def _rollup_id(
    site_id: str,
    account_type: str,
    window_type: str,
    generation: int,
    local_date: str,
) -> str:
    return f"{site_id}:{account_type}:{window_type}:{generation}:{local_date}"


def _iso_z(value: datetime) -> str:
    canonical = _utc_datetime(value)
    if canonical is None:
        raise ValueError("datetime is required")
    return canonical.isoformat().replace("+00:00", "Z")


def _shanghai_date(value: Any) -> str:
    canonical = _utc_datetime(value)
    if canonical is None:
        raise ValueError("datetime is required")
    return canonical.astimezone(SHANGHAI_TZ).date().isoformat()


def _modified_one(result: Any) -> bool:
    value = getattr(result, "modified_count", 0)
    return isinstance(value, int) and not isinstance(value, bool) and value == 1


def _bulk_inserted(result: Any) -> bool:
    upserted_ids = getattr(result, "upserted_ids", None)
    if isinstance(upserted_ids, dict) and upserted_ids:
        return True
    count = getattr(result, "upserted_count", 0)
    return isinstance(count, int) and not isinstance(count, bool) and count > 0


def _classification_result(
    classification: str,
    reason: str,
    *,
    direction: str | None = None,
    median: float | None = None,
    mad: float | None = None,
    tolerance: float | None = None,
    deviation: float | None = None,
) -> dict[str, Any]:
    return {
        "classification": classification,
        "reason": reason,
        "direction": direction,
        "median": median,
        "mad": mad,
        "tolerance": tolerance,
        "deviation": deviation,
    }


def _generation_result(
    promote: bool,
    reason: str,
    *,
    direction: str | None = None,
    representative_value: float | None = None,
    relative_spread: float | None = None,
) -> dict[str, Any]:
    return {
        "promote": promote,
        "reason": reason,
        "direction": direction,
        "representative_value": representative_value,
        "relative_spread": relative_spread,
    }


def _safe_number(value: Any) -> float | None:
    try:
        return _number(value)
    except Exception:
        return None


def _strict_finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        normalized = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return normalized if math.isfinite(normalized) else None


def _nonnegative_integer(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, parsed)


def _remote_account_key(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _observation_result(
    *,
    reason: str | None,
    remote_account_id: Any,
    window_type: Any,
    account_type: Any,
    window_reset_at: datetime | None = None,
    window_minutes: float | None = None,
    used_percent: float | None = None,
    cost_usd: float | None = None,
    usage_synced_at: datetime | None = None,
    observed_at: datetime | None = None,
) -> dict[str, Any]:
    return {
        "quality": "invalid" if reason else "valid",
        "reason": reason,
        "remote_account_id": remote_account_id,
        "window_type": window_type,
        "window_reset_at": window_reset_at,
        "window_minutes": window_minutes,
        "used_percent": used_percent,
        "cost_usd": cost_usd,
        "usage_synced_at": usage_synced_at,
        "observed_at": observed_at,
        "account_type": account_type,
    }


def _observation_invalid_reason(
    *,
    account: dict[str, Any],
    extra: dict[str, Any],
    remote_account_id: Any,
    account_type: Any,
    used_percent: float | None,
    reset_at: datetime | None,
    raw_reset_at: Any,
    window_minutes: float | None,
    cost_usd: float | None,
    synced_at: datetime | None,
    raw_synced_at: Any,
    observed_at: datetime | None,
) -> str | None:
    if remote_account_id is None:
        return "missing_remote_id"
    if isinstance(remote_account_id, bool) or not isinstance(remote_account_id, int) or remote_account_id <= 0:
        return "invalid_remote_id"
    if not isinstance(account_type, str) or account_type not in ACCOUNT_TYPES:
        return "invalid_account_type"
    if observed_at is None:
        return "invalid_observed_at"
    if _has_credential_error(account, extra):
        return "credential_error"
    if used_percent is None or not 0 <= used_percent <= 100:
        return "invalid_percent"
    if cost_usd is None or cost_usd < 0:
        return "invalid_cost"
    if reset_at is None:
        return "missing_window" if raw_reset_at is None else "invalid_reset_at"
    if window_minutes is None:
        return "missing_window"
    if window_minutes <= 0 or window_minutes > MAX_WINDOW_MINUTES:
        return "invalid_window_minutes"
    if synced_at is None:
        return "stale_usage" if raw_synced_at is None else "invalid_usage_synced_at"
    if observed_at - synced_at > MAX_SOURCE_AGE:
        return "stale_usage"
    if synced_at - observed_at > RESET_JITTER:
        return "stale_usage"
    if reset_at <= observed_at:
        return "expired_window"
    if reset_at - observed_at > timedelta(minutes=window_minutes) + RESET_JITTER:
        return "reset_outside_window"
    return None


def _has_credential_error(account: dict[str, Any], extra: dict[str, Any]) -> bool:
    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    status = _first(account, extra, "credentials_status")
    if status is None:
        status = credentials.get("status")
    if isinstance(status, dict):
        if _credential_status_mapping_is_invalid(status):
            return True
    elif str(status or "").strip().lower() in _INVALID_CREDENTIAL_STATUSES:
        return True
    error_text = " ".join(
        str(value)
        for value in (
            account.get("error_message"),
            extra.get("error_message"),
            credentials.get("error"),
        )
        if value
    )
    return bool(_CREDENTIAL_ERROR_PATTERN.search(error_text))


def _credential_status_mapping_is_invalid(status: dict[str, Any]) -> bool:
    declared_status = status.get("status", status.get("state"))
    normalized_status = str(declared_status or "").strip().lower()
    if normalized_status in _INVALID_CREDENTIAL_STATUSES:
        return True

    token_flags = [
        status[key]
        for key in ("has_access_token", "has_refresh_token")
        if key in status
    ]
    if token_flags:
        return any(not isinstance(flag, bool) for flag in token_flags) or not any(token_flags)
    return normalized_status not in _VALID_CREDENTIAL_STATUSES


def _identity_changed(state: dict[str, Any], observation: dict[str, Any]) -> bool:
    for key in ("remote_account_id", "window_type"):
        previous = state.get(key)
        current = observation.get(key)
        if previous is not None and current is not None and previous != current:
            return True
    return False


def _sample_eligible(state: dict[str, Any]) -> bool:
    marker = state.get("sample_eligible")
    if isinstance(marker, bool):
        return marker
    return state.get("last_under_limit_percent") is not None


def _state_with_under_limit_observation(
    state: dict[str, Any],
    observation: dict[str, Any],
    *,
    observed_at: datetime,
    sample_eligible: bool,
) -> dict[str, Any]:
    updated = {
        **state,
        "last_observed_at": observed_at,
        "sample_eligible": sample_eligible,
    }
    if sample_eligible:
        updated["last_under_limit_percent"] = observation["used_percent"]
        updated["last_under_limit_cost_usd"] = observation["cost_usd"]
    else:
        updated["last_under_limit_percent"] = None
        updated["last_under_limit_cost_usd"] = None
    return updated


def _first(primary: dict[str, Any], secondary: dict[str, Any], key: str) -> Any:
    value = primary.get(key)
    return value if value is not None else secondary.get(key)


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _utc_datetime(value: Any) -> datetime | None:
    try:
        if isinstance(value, datetime):
            parsed = value
        elif isinstance(value, str) and value:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except Exception:
        return None
