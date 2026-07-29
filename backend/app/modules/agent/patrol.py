from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from app.modules.agent.capacity import list_agent_pools
from app.modules.agent.controller import run_agent_controller
from app.modules.agent.memory import AGENT_RUNS_COLLECTION
from app.modules.agent.settings import get_agent_llm_runtime_settings, get_agent_scheduler_runtime_settings
from app.modules.agent.tasks import (
    ACTIVE_TASK_STATUSES,
    AGENT_TASKS_COLLECTION,
    TASK_STATUS_OBSERVING,
)
from app.modules.agent.triggers import TRIGGER_SCHEDULER_PATROL
from app.utils import now_utc, serialize_doc


AGENT_SCHEDULER_LOCKS_COLLECTION = "agent_scheduler_locks"
PATROL_LOCK_PREFIX = "agent_patrol"
DEFAULT_PATROL_COOLDOWN_MINUTES = 30
DEFAULT_PATROL_INTERVAL_MINUTES = 30


async def run_agent_patrol_once(
    db: AsyncIOMotorDatabase,
    *,
    pool_id: str | None = None,
    site_id: str | None = None,
    limit: int = 3,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Manually run one patrol round for debugging and release validation."""

    base_settings = await get_agent_scheduler_runtime_settings(db)
    settings = _settings_with_overrides(
        base_settings,
        agent_loop_enabled=True,
        patrol_enabled=True,
        max_pool_patrols_per_tick=max(0, min(int(limit or 3), 100)),
    )
    pools_response = await list_agent_pools(db)
    raw_pools = pools_response.get("items") if isinstance(pools_response, dict) else []
    pools = [item for item in raw_pools if isinstance(item, dict)]
    normalized_pool_id = _clean_optional_string(pool_id)
    normalized_site_id = _clean_optional_string(site_id)
    if normalized_pool_id:
        pools = [item for item in pools if _clean_optional_string(item.get("id") or item.get("pool_id")) == normalized_pool_id]
    if normalized_site_id:
        pools = [item for item in pools if _clean_optional_string(item.get("site_id")) == normalized_site_id]

    llm_ready = await _llm_config_available(db)
    candidates = await select_patrol_candidates(
        db,
        settings=settings,
        pools=pools,
        llm_ready=llm_ready,
    )
    processed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = list(candidates.get("skipped", []))
    errors: list[dict[str, Any]] = []
    for candidate in candidates.get("selected", []):
        if len(processed) >= settings.max_pool_patrols_per_tick:
            skipped.append(_skip(candidate, "manual_patrol_limit_reached"))
            continue
        result = await run_pool_patrol(
            db,
            candidate=candidate,
            settings=settings,
            scheduler_tick_id="manual_patrol",
            actor=actor,
        )
        if result.get("processed"):
            processed.append(result)
        elif result.get("status") == "failed":
            errors.append(result)
        else:
            skipped.append(result)

    return _patrol_result(
        enabled=True,
        total_candidates=len(pools),
        selected=len(candidates.get("selected", [])),
        processed=processed,
        skipped=skipped,
        errors=errors,
        scanned_pools=len(pools),
        eligible=len(candidates.get("eligible", [])),
        extra={
            "manual": True,
            "pool_id": normalized_pool_id,
            "site_id": normalized_site_id,
            "limit": settings.max_pool_patrols_per_tick,
        },
    )


async def list_agent_patrol_runs(
    db: AsyncIOMotorDatabase,
    *,
    pool_id: str | None = None,
    site_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """List scheduler_patrol run history without requiring agent_patrol_runs writes."""

    query: dict[str, Any] = {"trigger": TRIGGER_SCHEDULER_PATROL}
    normalized_pool_id = _clean_optional_string(pool_id)
    normalized_site_id = _clean_optional_string(site_id)
    normalized_status = _clean_optional_string(status)
    if normalized_pool_id:
        query["pool_id"] = normalized_pool_id
    if normalized_site_id:
        query["site_id"] = normalized_site_id
    if normalized_status:
        query["status"] = "success" if normalized_status == "processed" else normalized_status
    normalized_limit = max(1, min(int(limit or 50), 200))
    runs = [item async for item in db[AGENT_RUNS_COLLECTION].find(query).sort("started_at", -1).limit(normalized_limit)]
    total = await db[AGENT_RUNS_COLLECTION].count_documents(query)
    return {
        "items": [_patrol_run_view(item) for item in runs],
        "total": total,
        "source": "agent_runs",
        "agent_patrol_runs_enabled": False,
    }


async def process_pool_patrols(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Select pools for scheduled patrol and wake the Agent controller.

    Patrol is intentionally only a wake-up selector. It never writes account-pool
    business collections and never computes the final risk level or refill count.
    """

    if not bool(getattr(settings, "agent_loop_enabled", False)):
        return _disabled_result("agent_loop_disabled")
    if not bool(getattr(settings, "patrol_enabled", False)):
        return _disabled_result("patrol_disabled")

    max_patrols = _non_negative_int(getattr(settings, "max_pool_patrols_per_tick", 3), default=3)
    if max_patrols <= 0:
        return _patrol_result(
            enabled=True,
            total_candidates=0,
            selected=0,
            processed=[],
            skipped=[{"status": "skipped", "reason": "max_pool_patrols_per_tick_is_zero"}],
            errors=[],
        )

    llm_ready = await _llm_config_available(db)
    candidates = await select_patrol_candidates(
        db,
        settings=settings,
        llm_ready=llm_ready,
    )

    processed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = list(candidates.get("skipped", []))
    errors: list[dict[str, Any]] = []
    for candidate in candidates.get("selected", []):
        if len(processed) >= max_patrols:
            skipped.append(_skip(candidate, "max_pool_patrols_per_tick_reached"))
            continue
        result = await run_pool_patrol(
            db,
            candidate=candidate,
            settings=settings,
            scheduler_tick_id=scheduler_tick_id,
            actor=actor,
        )
        if result.get("processed"):
            processed.append(result)
        elif result.get("status") == "failed":
            errors.append(result)
        else:
            skipped.append(result)

    return _patrol_result(
        enabled=True,
        total_candidates=_non_negative_int(candidates.get("total_candidates"), default=0),
        selected=len(candidates.get("selected", [])),
        processed=processed,
        skipped=skipped,
        errors=errors,
        scanned_pools=_non_negative_int(candidates.get("scanned_pools"), default=0),
        eligible=len(candidates.get("eligible", [])),
    )


async def select_patrol_candidates(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    now: datetime | None = None,
    pools: list[dict[str, Any]] | None = None,
    llm_ready: bool | None = None,
) -> dict[str, Any]:
    """Return eligible and selected patrol candidates with skip reasons."""

    current_time = _as_aware_utc(now) or now_utc()
    if pools is None:
        pools_response = await list_agent_pools(db)
        raw_pools = pools_response.get("items") if isinstance(pools_response, dict) else []
        pools = [item for item in raw_pools if isinstance(item, dict)]
    if llm_ready is None:
        llm_ready = await _llm_config_available(db)

    max_patrols = _non_negative_int(getattr(settings, "max_pool_patrols_per_tick", 3), default=3)
    min_interval_minutes = _patrol_min_interval_minutes(settings)
    cooldown_cutoff = current_time - timedelta(minutes=min_interval_minutes)
    required_pool_ids = _required_patrol_pool_ids(settings)
    required_order = {pool_id: index for index, pool_id in enumerate(required_pool_ids)}
    excluded_pool_ids = set(_excluded_agent_pool_ids(settings))

    eligible: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for pool in pools:
        candidate = _candidate_from_pool(pool)
        pool_id = candidate.get("pool_id")
        site_id = candidate.get("site_id")
        if not pool_id:
            skipped.append(_skip(candidate, "missing_pool_id"))
            continue
        if pool_id in excluded_pool_ids:
            skipped.append(_skip(candidate, "agent_pool_excluded"))
            continue
        is_required_patrol = pool_id in required_order
        candidate["required_patrol"] = is_required_patrol
        if not site_id:
            skipped.append(_skip(candidate, "missing_site_id"))
            continue
        if _pool_disabled(pool):
            skipped.append(_skip(candidate, "pool_disabled"))
            continue
        if not _pool_strategy_allows_patrol(settings, pool_id=pool_id, site_id=site_id):
            skipped.append(_skip(candidate, "pool_strategy_disabled"))
            continue
        if not llm_ready:
            skipped.append(_skip(candidate, "agent_llm_config_unavailable"))
            continue

        active_task = await _active_task_for_pool(db, pool_id=pool_id)
        if active_task and _active_task_should_skip(active_task, now=current_time):
            skipped.append(
                _skip(
                    candidate,
                    "active_task_not_due",
                    task_id=_clean_optional_string(active_task.get("task_id") or active_task.get("_id")),
                    task_status=_clean_optional_string(active_task.get("status")),
                    next_check_at=active_task.get("next_check_at"),
                    review_after=active_task.get("review_after"),
                )
            )
            continue
        if active_task:
            skipped.append(
                _skip(
                    candidate,
                    "active_task_due_handled_by_task_scheduler",
                    task_id=_clean_optional_string(active_task.get("task_id") or active_task.get("_id")),
                    task_status=_clean_optional_string(active_task.get("status")),
                )
            )
            continue

        latest_patrol = await _latest_patrol_run(db, pool_id=pool_id)
        latest_patrol_at = _latest_run_time(latest_patrol)
        if not is_required_patrol and min_interval_minutes > 0 and latest_patrol_at and latest_patrol_at > cooldown_cutoff:
            skipped.append(
                _skip(
                    candidate,
                    "patrol_cooldown",
                    last_patrol_at=latest_patrol_at,
                    cooldown_minutes=min_interval_minutes,
                )
            )
            continue

        candidate["last_patrol_at"] = latest_patrol_at
        candidate["patrol_priority"] = await _patrol_priority(
            db,
            candidate=candidate,
            latest_patrol_at=latest_patrol_at,
            now=current_time,
        )
        eligible.append(candidate)

    required_eligible = sorted(
        [item for item in eligible if item.get("required_patrol")],
        key=lambda item: (required_order.get(str(item.get("pool_id")), 10_000), str(item.get("pool_id") or "")),
    )
    regular_eligible = sorted(
        [item for item in eligible if not item.get("required_patrol")],
        key=lambda item: (-_number(item.get("patrol_priority")), str(item.get("pool_id") or "")),
    )
    selected = (required_eligible + regular_eligible)[:max_patrols]
    selected_keys = {_clean_optional_string(item.get("pool_id")) for item in selected}
    for item in required_eligible:
        if _clean_optional_string(item.get("pool_id")) not in selected_keys:
            skipped.append(_skip(item, "required_patrol_limit_reached", max_pool_patrols_per_tick=max_patrols))
    return {
        "total_candidates": len(pools),
        "scanned_pools": len(pools),
        "eligible": eligible,
        "selected": selected,
        "skipped": skipped,
    }


async def run_pool_patrol(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    pool: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one selected pool through the Agent controller as scheduler_patrol."""

    candidate = candidate if isinstance(candidate, dict) else _candidate_from_pool(pool or {})
    pool_id = _clean_optional_string(candidate.get("pool_id"))
    site_id = _clean_optional_string(candidate.get("site_id"))
    if not pool_id or not site_id:
        return {"processed": False, "status": "skipped", "pool_id": pool_id, "site_id": site_id, "reason": "pool_identity_incomplete"}

    lock_owner = scheduler_tick_id or f"manual:{now_utc().timestamp()}"
    lock = await acquire_pool_patrol_lock(
        db,
        site_id=site_id,
        pool_id=pool_id,
        owner=lock_owner,
        ttl_seconds=_patrol_lock_ttl_seconds(settings),
    )
    if not lock.get("acquired"):
        return {"processed": False, "status": "skipped", "pool_id": pool_id, "site_id": site_id, "reason": "patrol_lock_busy", "lock": lock.get("lock")}

    try:
        report = await run_agent_controller(
            db,
            trigger=TRIGGER_SCHEDULER_PATROL,
            user_message=None,
            pool_id=pool_id,
            conversation_id=None,
            metadata={
                "trigger_reason": "scheduled pool patrol",
                "trigger_source": "agent_scheduler",
                "scheduler_tick_id": scheduler_tick_id,
                "site_id": site_id,
                "pool_id": pool_id,
                "patrol_reason": _patrol_reason(candidate),
                "patrol_priority": candidate.get("patrol_priority"),
                "patrol_priority_components": candidate.get("patrol_priority_components"),
                "required_patrol": bool(candidate.get("required_patrol")),
                "last_patrol_at": candidate.get("last_patrol_at"),
                "auto_started": True,
            },
            actor=actor,
        )
        task_view = (report.get("agent") or {}).get("task") if isinstance(report.get("agent"), dict) else None
        return {
            "processed": True,
            "status": "processed",
            "pool_id": pool_id,
            "site_id": site_id,
            "run_id": report.get("run_id"),
            "decision_id": report.get("decision_id"),
            "task_id": _task_id_from_view(task_view),
            "severity": report.get("severity"),
            "task": task_view,
            "patrol_priority": candidate.get("patrol_priority"),
            "patrol_reason": _patrol_reason(candidate),
            "required_patrol": bool(candidate.get("required_patrol")),
        }
    except Exception as exc:  # noqa: BLE001 - one patrol must not stop the scheduler tick.
        return {"processed": False, "status": "failed", "pool_id": pool_id, "site_id": site_id, "error": str(exc) or exc.__class__.__name__}
    finally:
        await release_pool_patrol_lock(db, site_id=site_id, pool_id=pool_id, owner=lock_owner)


async def acquire_pool_patrol_lock(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str,
    owner: str,
    ttl_seconds: int,
) -> dict[str, Any]:
    now = now_utc()
    normalized_site_id = _clean_optional_string(site_id) or "default"
    lock_id = _patrol_lock_id(site_id=normalized_site_id, pool_id=pool_id)
    expires_at = now + timedelta(seconds=max(30, int(ttl_seconds or 300)))
    try:
        document = await db[AGENT_SCHEDULER_LOCKS_COLLECTION].find_one_and_update(
            {
                "_id": lock_id,
                "$or": [
                    {"expires_at": {"$lte": now}},
                    {"expires_at": {"$exists": False}},
                    {"owner": owner},
                ],
            },
            {
                "$set": {
                    "owner": owner,
                    "site_id": normalized_site_id,
                    "pool_id": pool_id,
                    "lock_type": "agent_pool_patrol",
                    "locked_at": now,
                    "expires_at": expires_at,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
    except DuplicateKeyError:
        document = await db[AGENT_SCHEDULER_LOCKS_COLLECTION].find_one({"_id": lock_id})
    acquired = bool(document and document.get("owner") == owner)
    return {"acquired": acquired, "owner": owner if acquired else None, "lock": serialize_doc(document) if document else None}


async def release_pool_patrol_lock(db: AsyncIOMotorDatabase, *, site_id: str | None, pool_id: str, owner: str) -> bool:
    if not owner:
        return False
    normalized_site_id = _clean_optional_string(site_id) or "default"
    result = await db[AGENT_SCHEDULER_LOCKS_COLLECTION].delete_one({"_id": _patrol_lock_id(site_id=normalized_site_id, pool_id=pool_id), "owner": owner})
    return result.deleted_count > 0


def _patrol_result(
    *,
    enabled: bool,
    total_candidates: int,
    selected: int,
    processed: list[dict[str, Any]],
    skipped: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    scanned_pools: int | None = None,
    eligible: int | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "ok": not errors,
        "implemented": True,
        "trigger": TRIGGER_SCHEDULER_PATROL,
        "enabled": enabled,
        "total_candidates": total_candidates,
        "selected": selected,
        "processed": processed,
        "skipped": skipped,
        "errors": errors,
        "total_processed": len(processed),
        "total_skipped": len(skipped),
        "total_errors": len(errors),
    }
    if scanned_pools is not None:
        result["scanned_pools"] = scanned_pools
    if eligible is not None:
        result["eligible"] = eligible
    if extra:
        result.update(extra)
    return result


def _disabled_result(reason: str) -> dict[str, Any]:
    return _patrol_result(
        enabled=False,
        total_candidates=0,
        selected=0,
        processed=[],
        skipped=[{"status": "skipped", "reason": reason}],
        errors=[],
    )


def _candidate_from_pool(pool: dict[str, Any]) -> dict[str, Any]:
    return {
        "pool_id": _clean_optional_string(pool.get("id") or pool.get("pool_id")),
        "site_id": _clean_optional_string(pool.get("site_id")),
        "name": _clean_optional_string(pool.get("name")),
        "account_type": _clean_optional_string(pool.get("account_type")),
        "source": _clean_optional_string(pool.get("source")),
        "pool": {
            "id": pool.get("id") or pool.get("pool_id"),
            "name": pool.get("name"),
            "site_id": pool.get("site_id"),
            "active_group_id": pool.get("active_group_id"),
            "status": pool.get("status"),
            "remote_status": pool.get("remote_status"),
            "remote_account_count": pool.get("remote_account_count"),
            "remote_active_account_count": pool.get("remote_active_account_count"),
            "remote_rate_limited_account_count": pool.get("remote_rate_limited_account_count"),
        },
    }


def _pool_disabled(pool: dict[str, Any]) -> bool:
    status = str(pool.get("status") or "").strip().lower()
    remote_status = str(pool.get("remote_status") or "").strip().lower()
    return status == "disabled" or remote_status == "disabled"


def _pool_strategy_allows_patrol(settings: Any, *, pool_id: str, site_id: str | None) -> bool:
    strategies = getattr(settings, "pool_strategies", [])
    if not isinstance(strategies, list):
        return True
    matched = False
    allowed = True
    for item in strategies:
        if not isinstance(item, dict):
            continue
        strategy_pool_id = _clean_optional_string(item.get("pool_id"))
        strategy_site_id = _clean_optional_string(item.get("site_id"))
        if strategy_pool_id and strategy_pool_id != pool_id:
            continue
        if strategy_site_id and strategy_site_id != site_id:
            continue
        if not strategy_pool_id and not strategy_site_id:
            continue
        matched = True
        if item.get("agent_enabled") is False or item.get("patrol_enabled") is False:
            allowed = False
    return allowed if matched else True


async def _active_task_for_pool(db: AsyncIOMotorDatabase, *, pool_id: str) -> dict[str, Any] | None:
    document = await db[AGENT_TASKS_COLLECTION].find_one(
        {
            "$or": [{"owner_scope": "agent"}, {"owner_scope": {"$exists": False}}],
            "pool_id": pool_id,
            "status": {"$in": list(ACTIVE_TASK_STATUSES)},
        },
        sort=[("updated_at", -1), ("created_at", -1)],
    )
    return document


def _active_task_should_skip(task: dict[str, Any], *, now: datetime) -> bool:
    status = _clean_optional_string(task.get("status"))
    if status != TASK_STATUS_OBSERVING:
        return True
    next_check_at = _as_aware_utc(task.get("next_check_at"))
    if next_check_at is None:
        return True
    return next_check_at > _as_aware_utc(now)


async def _latest_patrol_run(db: AsyncIOMotorDatabase, *, pool_id: str) -> dict[str, Any] | None:
    return await db[AGENT_RUNS_COLLECTION].find_one(
        {"trigger": TRIGGER_SCHEDULER_PATROL, "pool_id": pool_id},
        sort=[("started_at", -1), ("created_at", -1)],
    )


def _latest_run_time(run: dict[str, Any] | None) -> datetime | None:
    if not isinstance(run, dict):
        return None
    return _as_aware_utc(run.get("started_at") or run.get("created_at"))


async def _patrol_priority(
    db: AsyncIOMotorDatabase,
    *,
    candidate: dict[str, Any],
    latest_patrol_at: datetime | None,
    now: datetime,
) -> float:
    components: dict[str, Any] = {}
    if latest_patrol_at is None:
        minutes_since = 10080
        components["never_patrolled"] = 5000
    else:
        minutes_since = max(0, int((now - latest_patrol_at).total_seconds() // 60))
        components["minutes_since_last_patrol"] = minutes_since

    pool = candidate.get("pool") if isinstance(candidate.get("pool"), dict) else {}
    light_signal = 0
    if _number_or_none(pool.get("remote_rate_limited_account_count")):
        light_signal += 25
    if (_number_or_none(pool.get("remote_active_account_count")) or 0) <= 0 and (_number_or_none(pool.get("remote_account_count")) or 0) > 0:
        light_signal += 20
    if light_signal:
        components["light_pool_signal"] = light_signal

    recent_decision_signal = await _recent_decision_signal(db, pool_id=str(candidate.get("pool_id") or ""))
    if recent_decision_signal:
        components["recent_decision_signal"] = recent_decision_signal

    memory_signal = await _memory_quality_signal(db, pool_id=str(candidate.get("pool_id") or ""))
    if memory_signal:
        components["memory_quality_signal"] = memory_signal

    priority = float(minutes_since + sum(_number(value) for value in components.values()))
    candidate["patrol_priority_components"] = components
    return priority


async def _recent_decision_signal(db: AsyncIOMotorDatabase, *, pool_id: str) -> int:
    if not pool_id:
        return 0
    decision = await db.agent_decisions.find_one({"pool_id": pool_id}, sort=[("created_at", -1)])
    severity = str((decision or {}).get("severity") or "").strip().lower()
    return {"watch": 5, "warning": 15, "danger": 35, "critical": 50}.get(severity, 0)


async def _memory_quality_signal(db: AsyncIOMotorDatabase, *, pool_id: str) -> int:
    if not pool_id:
        return 0
    memory = await db.agent_memory_summaries.find_one({"pool_id": pool_id}, sort=[("period_end", -1), ("created_at", -1)])
    if not memory:
        return 0
    haystack = " ".join(
        [
            str(memory.get("summary") or ""),
            " ".join(str(item) for item in memory.get("facts", []) if item is not None) if isinstance(memory.get("facts"), list) else "",
            " ".join(str(item) for item in memory.get("patterns", []) if item is not None) if isinstance(memory.get("patterns"), list) else "",
            " ".join(str(item) for item in memory.get("lessons", []) if item is not None) if isinstance(memory.get("lessons"), list) else "",
        ]
    )
    keywords = ("质量变差", "下降", "封号", "401", "限额", "异常", "失效")
    return 20 if any(keyword in haystack for keyword in keywords) else 0


async def _llm_config_available(db: AsyncIOMotorDatabase) -> bool:
    try:
        settings = await get_agent_llm_runtime_settings(db)
    except Exception:  # noqa: BLE001 - patrol should skip cleanly when config cannot be read.
        return False
    return bool(
        getattr(settings, "agent_llm_enabled", False)
        and getattr(settings, "agent_llm_base_url", None)
        and getattr(settings, "agent_llm_api_key", None)
        and getattr(settings, "agent_level1_model", None)
    )


def _patrol_reason(candidate: dict[str, Any]) -> str:
    if candidate.get("required_patrol"):
        return "pool is configured as required patrol"
    if not candidate.get("last_patrol_at"):
        return "pool has not been patrolled before"
    minutes = int((candidate.get("patrol_priority_components") or {}).get("minutes_since_last_patrol") or 0)
    return f"pool has not been checked for {minutes} minutes"


def _skip(candidate: dict[str, Any], reason: str, **extra: Any) -> dict[str, Any]:
    return {
        "status": "skipped",
        "pool_id": candidate.get("pool_id"),
        "site_id": candidate.get("site_id"),
        "reason": reason,
        **extra,
    }


def _settings_with_overrides(settings: Any, **overrides: Any) -> Any:
    class _SettingsView:
        pass

    view = _SettingsView()
    source_items = vars(settings).items() if hasattr(settings, "__dict__") else []
    for key, value in source_items:
        setattr(view, key, value)
    for key, value in overrides.items():
        setattr(view, key, value)
    return view


def _patrol_run_view(run: dict[str, Any]) -> dict[str, Any]:
    metadata = run.get("trigger_metadata") if isinstance(run.get("trigger_metadata"), dict) else run.get("metadata") if isinstance(run.get("metadata"), dict) else {}
    agent = run.get("agent") if isinstance(run.get("agent"), dict) else {}
    task = agent.get("task") if isinstance(agent.get("task"), dict) else {}
    return serialize_doc(
        {
            "_id": run.get("_id"),
            "patrol_id": run.get("run_id") or run.get("_id"),
            "scheduler_tick_id": metadata.get("scheduler_tick_id"),
            "site_id": run.get("site_id") or metadata.get("site_id"),
            "pool_id": run.get("pool_id") or metadata.get("pool_id"),
            "status": "processed" if run.get("status") == "success" else run.get("status"),
            "reason": metadata.get("trigger_reason") or metadata.get("patrol_reason") or "scheduled_pool_patrol",
            "skip_reason": run.get("error"),
            "required_patrol": bool(metadata.get("required_patrol")),
            "run_id": run.get("run_id") or run.get("_id"),
            "decision_id": run.get("decision_id"),
            "task_id": task.get("task_id") or task.get("_id"),
            "severity": run.get("severity"),
            "started_at": run.get("started_at"),
            "finished_at": run.get("finished_at"),
            "created_at": run.get("created_at"),
        }
    )


def _task_id_from_view(task_view: Any) -> str | None:
    if not isinstance(task_view, dict):
        return None
    return _clean_optional_string(task_view.get("task_id") or task_view.get("_id"))


def _patrol_cooldown_minutes(settings: Any) -> int:
    fallback = _non_negative_int(getattr(settings, "task_cooldown_minutes", DEFAULT_PATROL_COOLDOWN_MINUTES), default=DEFAULT_PATROL_COOLDOWN_MINUTES)
    return _non_negative_int(getattr(settings, "pool_patrol_cooldown_minutes", fallback), default=fallback)


def _required_patrol_pool_ids(settings: Any) -> list[str]:
    value = getattr(settings, "required_patrol_pool_ids", [])
    return _clean_pool_id_list(value)


def _excluded_agent_pool_ids(settings: Any) -> list[str]:
    value = getattr(settings, "excluded_agent_pool_ids", [])
    return _clean_pool_id_list(value)


def _clean_pool_id_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    pool_ids: list[str] = []
    seen: set[str] = set()
    for item in value:
        pool_id = _clean_optional_string(item)
        if not pool_id or pool_id in seen:
            continue
        seen.add(pool_id)
        pool_ids.append(pool_id)
    return pool_ids


def _patrol_min_interval_minutes(settings: Any) -> int:
    interval = _positive_int(getattr(settings, "pool_patrol_interval_minutes", DEFAULT_PATROL_INTERVAL_MINUTES), default=DEFAULT_PATROL_INTERVAL_MINUTES)
    cooldown = _patrol_cooldown_minutes(settings)
    return max(interval, cooldown)


def _patrol_lock_ttl_seconds(settings: Any) -> int:
    interval = _positive_int(getattr(settings, "scheduler_interval_seconds", 300), default=300)
    patrol_interval = _positive_int(getattr(settings, "pool_patrol_interval_minutes", DEFAULT_PATROL_INTERVAL_MINUTES), default=DEFAULT_PATROL_INTERVAL_MINUTES) * 60
    return max(60, min(max(interval * 2, patrol_interval), 3600))


def _patrol_lock_id(*, site_id: str, pool_id: str) -> str:
    return f"{PATROL_LOCK_PREFIX}:{site_id}:{pool_id}"


def _as_aware_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return None


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _number(value: Any) -> float:
    number = _number_or_none(value)
    return float(number or 0)


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _non_negative_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
        return parsed if parsed >= 0 else default
    except (TypeError, ValueError):
        return default
