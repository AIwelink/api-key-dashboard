from __future__ import annotations

import secrets
from collections import Counter
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.capacity import list_agent_pools
from app.modules.agent.event_stream import read_agent_event_windows
from app.modules.agent.memory import AGENT_DECISIONS_COLLECTION, AGENT_MESSAGES_COLLECTION, AGENT_RUNS_COLLECTION
from app.utils import now_utc, serialize_doc


AGENT_MEMORY_SUMMARIES_COLLECTION = "agent_memory_summaries"
AGENT_TASKS_COLLECTION = "agent_tasks"

MEMORY_TYPE_POOL_DAILY = "pool_daily_summary"
MEMORY_TYPE_POOL_WEEKLY = "pool_weekly_summary"
MEMORY_TYPE_DECISION_REVIEW = "decision_review"
MEMORY_TYPE_OPERATOR_FEEDBACK = "operator_feedback_summary"
MEMORY_TYPE_SURVIVAL_PATTERN = "survival_pattern"

ALLOWED_MEMORY_TYPES = {
    MEMORY_TYPE_POOL_DAILY,
    MEMORY_TYPE_POOL_WEEKLY,
    MEMORY_TYPE_DECISION_REVIEW,
    MEMORY_TYPE_OPERATOR_FEEDBACK,
    MEMORY_TYPE_SURVIVAL_PATTERN,
}

FEEDBACK_KEYWORDS = (
    "不是",
    "不对",
    "纠正",
    "误判",
    "负责人",
    "人工",
    "确认",
    "正常",
    "正常流量",
    "异常流量",
    "活动",
    "批量任务",
    "批量",
    "不用补",
    "需要补",
    "夜间",
    "白天",
    "策略",
)


async def save_agent_memory_summary(
    db: AsyncIOMotorDatabase,
    *,
    payload: dict[str, Any],
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist one Agent long-term memory summary.

    The memory collection belongs to Agent only. It never mutates account-pool
    business collections.
    """

    now = now_utc()
    memory_id = _clean_optional_string(payload.get("memory_id")) or _new_id()
    memory_type = _normalize_memory_type(payload.get("memory_type"))
    document = {
        "_id": memory_id,
        "memory_id": memory_id,
        "site_id": _clean_optional_string(payload.get("site_id")),
        "pool_id": _clean_optional_string(payload.get("pool_id")),
        "memory_type": memory_type,
        "period_start": _datetime_or_none(payload.get("period_start")),
        "period_end": _datetime_or_none(payload.get("period_end")) or now,
        "summary": _clean_optional_string(payload.get("summary")) or "",
        "facts": _list_of_values(payload.get("facts")),
        "patterns": _list_of_values(payload.get("patterns")),
        "lessons": _list_of_values(payload.get("lessons")),
        "risk_baselines": payload.get("risk_baselines") if isinstance(payload.get("risk_baselines"), dict) else {},
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "created_by": _actor_id(actor) or _clean_optional_string(payload.get("created_by")) or "agent",
        "created_at": now,
        "updated_at": now,
        "source_run_ids": _list_of_strings(payload.get("source_run_ids")),
        "source_decision_ids": _list_of_strings(payload.get("source_decision_ids")),
    }
    await _memory_summaries(db).update_one({"_id": memory_id}, {"$set": document}, upsert=True)
    saved = await _memory_summaries(db).find_one({"_id": memory_id})
    return serialize_doc(saved or document)


async def get_agent_long_term_memory(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    daily_limit: int = 3,
    weekly_limit: int = 2,
    decision_review_limit: int = 3,
    feedback_limit: int = 5,
    survival_pattern_limit: int = 3,
) -> dict[str, Any]:
    """Return the compact memory pack used by Context Pack v2."""

    return {
        "pool_daily_summaries": await _list_memory(
            db,
            site_id=site_id,
            pool_id=pool_id,
            memory_type=MEMORY_TYPE_POOL_DAILY,
            limit=_normalize_limit(daily_limit, default=3, maximum=10),
        ),
        "pool_weekly_summaries": await _list_memory(
            db,
            site_id=site_id,
            pool_id=pool_id,
            memory_type=MEMORY_TYPE_POOL_WEEKLY,
            limit=_normalize_limit(weekly_limit, default=2, maximum=10),
        ),
        "decision_reviews": await _list_memory(
            db,
            site_id=site_id,
            pool_id=pool_id,
            memory_type=MEMORY_TYPE_DECISION_REVIEW,
            limit=_normalize_limit(decision_review_limit, default=3, maximum=10),
        ),
        "operator_feedback_summaries": await _list_memory(
            db,
            site_id=site_id,
            pool_id=pool_id,
            memory_type=MEMORY_TYPE_OPERATOR_FEEDBACK,
            limit=_normalize_limit(feedback_limit, default=5, maximum=20),
        ),
        "survival_patterns": await _list_memory(
            db,
            site_id=site_id,
            pool_id=pool_id,
            memory_type=MEMORY_TYPE_SURVIVAL_PATTERN,
            limit=_normalize_limit(survival_pattern_limit, default=3, maximum=10),
        ),
    }


async def list_agent_memory_summaries(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None = None,
    pool_id: str | None = None,
    memory_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    normalized_site_id = _clean_optional_string(site_id)
    normalized_pool_id = _clean_optional_string(pool_id)
    normalized_memory_type = _clean_optional_string(memory_type)
    if normalized_site_id:
        query["site_id"] = normalized_site_id
    if normalized_pool_id:
        query["pool_id"] = normalized_pool_id
    if normalized_memory_type:
        query["memory_type"] = normalized_memory_type
    normalized_limit = _normalize_limit(limit, default=50, maximum=200)
    cursor = _memory_summaries(db).find(query).sort([("period_end", -1), ("created_at", -1)]).limit(normalized_limit)
    items = [_memory_view(item) async for item in cursor]
    total = await _memory_summaries(db).count_documents(query)
    return {"items": serialize_doc(items), "total": total, "memory_types": sorted(ALLOWED_MEMORY_TYPES | {"future_playbook"})}


async def get_agent_memory_summary(db: AsyncIOMotorDatabase, *, memory_id: str) -> dict[str, Any] | None:
    normalized_memory_id = _clean_optional_string(memory_id)
    if not normalized_memory_id:
        return None
    document = await _memory_summaries(db).find_one(
        {"$or": [{"_id": normalized_memory_id}, {"memory_id": normalized_memory_id}]}
    )
    return serialize_doc(_memory_view(document)) if document else None


async def generate_daily_memory_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    date: str,
) -> dict[str, Any]:
    """Generate a daily pool operation memory.

    It summarizes Agent runs, decisions, conversation feedback, and event
    windows for one day. This is deterministic in stage four; later stages can
    add an LLM summarizer on top of the same payload.
    """

    period_start, period_end = _day_bounds_from_date(date)
    decisions = await _load_decisions(db, site_id=site_id, pool_id=pool_id, start=period_start, end=period_end, limit=80)
    runs = await _load_runs(db, site_id=site_id, pool_id=pool_id, start=period_start, end=period_end, limit=80)
    messages = await _load_messages(db, site_id=site_id, pool_id=pool_id, start=period_start, end=period_end, limit=120)
    tasks = await _load_tasks(db, site_id=site_id, pool_id=pool_id, start=period_start, end=period_end, limit=120)
    event_windows = await _safe_event_windows(db, site_id=site_id, pool_id=pool_id, decisions=decisions)
    operational_samples = await _load_operational_sample_summary(
        db, site_id=site_id, pool_id=pool_id, start=period_start, end=period_end
    )

    facts = [
        *_run_and_decision_facts(runs=runs, decisions=decisions),
        *_capacity_memory_facts(decisions),
        *_event_memory_facts(event_windows),
        *_feedback_memory_facts(messages),
        *_task_memory_facts(tasks),
        *_operational_sample_facts(operational_samples),
    ]
    patterns = [
        *_risk_patterns(decisions),
        *_event_patterns(event_windows),
        *_feedback_patterns(messages),
        *_task_patterns(tasks),
        *_operational_sample_patterns(operational_samples),
    ]
    lessons = _daily_lessons(decisions=decisions, messages=messages, event_windows=event_windows)
    payload = {
        "memory_id": _memory_id(
            memory_type=MEMORY_TYPE_POOL_DAILY,
            site_id=site_id,
            pool_id=pool_id,
            period_start=period_start,
            period_end=period_end,
        ),
        "site_id": site_id,
        "pool_id": pool_id,
        "memory_type": MEMORY_TYPE_POOL_DAILY,
        "period_start": period_start,
        "period_end": period_end,
        "summary": _daily_summary_text(date=date, runs=runs, decisions=decisions, event_windows=event_windows),
        "facts": facts,
        "patterns": patterns,
        "lessons": lessons,
        "risk_baselines": {
            **_risk_baselines(decisions=decisions, event_windows=event_windows),
            "operational_samples": operational_samples,
        },
        "created_by": "agent",
        "source_run_ids": [item.get("run_id") or item.get("_id") for item in runs],
        "source_decision_ids": [item.get("decision_id") or item.get("_id") for item in decisions],
        "metadata": {
            "generator": "daily_memory_summary.v4",
            "event_windows_included": bool(event_windows),
            "message_count": len(messages),
            "task_count": len(tasks),
            "operational_samples_included": operational_samples.get("available") is True,
        },
    }
    payload = await _refine_memory_payload_with_llm(db, payload=payload)
    return await save_agent_memory_summary(
        db,
        payload=payload,
    )


async def generate_pool_daily_memory_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str,
    date: str,
) -> dict[str, Any]:
    return await generate_daily_memory_summary(db, site_id=site_id, pool_id=pool_id, date=date)


async def generate_weekly_memory_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    week_end_date: str,
) -> dict[str, Any]:
    period_end = _day_bounds_from_date(week_end_date)[1]
    period_start = period_end - timedelta(days=7)
    return await _generate_weekly_memory_summary_for_period(
        db,
        site_id=site_id,
        pool_id=pool_id,
        period_start=period_start,
        period_end=period_end,
    )


async def generate_pool_weekly_memory_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str,
    week_start: str,
    week_end: str,
) -> dict[str, Any]:
    period_start = _day_bounds_from_date(week_start)[0]
    period_end = _day_bounds_from_date(week_end)[0]
    if period_end <= period_start:
        period_end = period_start + timedelta(days=7)
    return await _generate_weekly_memory_summary_for_period(
        db,
        site_id=site_id,
        pool_id=pool_id,
        period_start=period_start,
        period_end=period_end,
    )


async def _generate_weekly_memory_summary_for_period(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    period_start: datetime,
    period_end: datetime,
) -> dict[str, Any]:
    decisions = await _load_decisions(db, site_id=site_id, pool_id=pool_id, start=period_start, end=period_end, limit=300)
    runs = await _load_runs(db, site_id=site_id, pool_id=pool_id, start=period_start, end=period_end, limit=300)
    messages = await _load_messages(db, site_id=site_id, pool_id=pool_id, start=period_start, end=period_end, limit=300)
    tasks = await _load_tasks(db, site_id=site_id, pool_id=pool_id, start=period_start, end=period_end, limit=300)
    event_windows = await _safe_event_windows(db, site_id=site_id, pool_id=pool_id, decisions=decisions)
    operational_samples = await _load_operational_sample_summary(
        db, site_id=site_id, pool_id=pool_id, start=period_start, end=period_end
    )

    facts = [
        f"最近 7 天 Agent 运行 {len(runs)} 次，形成 {len(decisions)} 条决策。",
        *_risk_distribution_facts(decisions),
        *_event_memory_facts(event_windows),
        *_survival_facts_from_decisions(decisions),
        *_task_memory_facts(tasks),
        *_operational_sample_facts(operational_samples),
    ]
    patterns = [
        *_risk_patterns(decisions),
        *_event_patterns(event_windows),
        *_decision_bias_patterns(decisions),
        *_feedback_patterns(messages),
        *_task_patterns(tasks),
        *_operational_sample_patterns(operational_samples),
    ]
    lessons = [
        *_weekly_lessons(decisions=decisions, event_windows=event_windows),
        *_feedback_memory_facts(messages),
    ]
    payload = {
        "memory_id": _memory_id(
            memory_type=MEMORY_TYPE_POOL_WEEKLY,
            site_id=site_id,
            pool_id=pool_id,
            period_start=period_start,
            period_end=period_end,
        ),
        "site_id": site_id,
        "pool_id": pool_id,
        "memory_type": MEMORY_TYPE_POOL_WEEKLY,
        "period_start": period_start,
        "period_end": period_end,
        "summary": _weekly_summary_text(runs=runs, decisions=decisions, event_windows=event_windows),
        "facts": facts,
        "patterns": patterns,
        "lessons": lessons,
        "risk_baselines": {
            **_risk_baselines(decisions=decisions, event_windows=event_windows),
            "operational_samples": operational_samples,
        },
        "created_by": "agent",
        "source_run_ids": [item.get("run_id") or item.get("_id") for item in runs],
        "source_decision_ids": [item.get("decision_id") or item.get("_id") for item in decisions],
        "metadata": {
            "generator": "weekly_memory_summary.v3",
            "message_count": len(messages),
            "task_count": len(tasks),
            "operational_samples_included": operational_samples.get("available") is True,
        },
    }
    payload = await _refine_memory_payload_with_llm(db, payload=payload)
    return await save_agent_memory_summary(
        db,
        payload=payload,
    )


async def generate_decision_review(
    db: AsyncIOMotorDatabase,
    *,
    decision_id: str,
    review_window_hours: int = 24,
) -> dict[str, Any] | None:
    decision = await _decisions(db).find_one({"$or": [{"_id": decision_id}, {"decision_id": decision_id}]})
    if not decision:
        return None
    created_at = _datetime_or_none(decision.get("created_at")) or now_utc()
    period_end = created_at + timedelta(hours=max(1, int(review_window_hours or 24)))
    site_id = _clean_optional_string(decision.get("site_id"))
    pool_id = _clean_optional_string(decision.get("pool_id"))
    later_decisions = await _load_decisions(db, site_id=site_id, pool_id=pool_id, start=created_at, end=period_end, limit=30)

    original = decision.get("decision") if isinstance(decision.get("decision"), dict) else {}
    facts = [
        f"原始决策风险等级为 {decision.get('severity') or original.get('severity') or '未知'}。",
        f"原始建议补号数为 {original.get('suggested_add_count') if original.get('suggested_add_count') is not None else '未知'}。",
        f"复盘窗口为决策后 {review_window_hours} 小时。",
    ]
    later_severities = Counter(str(item.get("severity") or "unknown") for item in later_decisions if item.get("decision_id") != decision.get("decision_id"))
    if later_severities:
        facts.append(f"后续风险等级分布为 {dict(later_severities.most_common())}。")
    lessons = _decision_review_lessons(original=decision, later_decisions=later_decisions)
    return await save_agent_memory_summary(
        db,
        payload={
            "site_id": site_id,
            "pool_id": pool_id,
            "memory_type": MEMORY_TYPE_DECISION_REVIEW,
            "period_start": created_at,
            "period_end": period_end,
            "summary": _decision_review_summary(decision=decision, later_decisions=later_decisions),
            "facts": facts,
            "patterns": _risk_patterns(later_decisions),
            "lessons": lessons,
            "risk_baselines": _risk_baselines(decisions=later_decisions, event_windows={}),
            "created_by": "agent",
            "source_run_ids": [decision.get("run_id")],
            "source_decision_ids": [decision.get("decision_id") or decision.get("_id")],
            "metadata": {"generator": "decision_review.v1", "review_window_hours": review_window_hours},
        },
    )


async def generate_operator_feedback_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    start_at: datetime | str,
    end_at: datetime | str | None = None,
) -> dict[str, Any]:
    start = _datetime_or_none(start_at) or (now_utc() - timedelta(days=7))
    end = _datetime_or_none(end_at) or now_utc()
    messages = await _load_messages(db, site_id=site_id, pool_id=pool_id, start=start, end=end, limit=300)
    feedback_messages = _feedback_messages(messages)
    facts = _feedback_memory_facts(feedback_messages)
    return await save_agent_memory_summary(
        db,
        payload={
            "site_id": site_id,
            "pool_id": pool_id,
            "memory_type": MEMORY_TYPE_OPERATOR_FEEDBACK,
            "period_start": start,
            "period_end": end,
            "summary": _operator_feedback_summary_text(feedback_messages),
            "facts": facts,
            "patterns": _feedback_patterns(feedback_messages),
            "lessons": [item.get("content") for item in feedback_messages[:8] if item.get("content")],
            "risk_baselines": {},
            "created_by": "agent",
            "source_run_ids": [item.get("run_id") for item in feedback_messages],
            "source_decision_ids": [],
            "metadata": {"generator": "operator_feedback_summary.v1", "message_count": len(feedback_messages)},
        },
    )


async def record_operator_feedback_if_present(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    message: str | None,
    run_id: str | None = None,
    conversation_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Persist an immediate long-term memory when a user correction is detected.

    This is intentionally conservative: it only writes to Agent's own memory
    collection and never changes account-pool business data.
    """

    content = _clean_optional_string(message)
    if not content:
        return None
    message_doc = {
        "content": content,
        "run_id": run_id,
        "conversation_id": conversation_id,
        "pool_id": pool_id,
        "site_id": site_id,
        "created_at": now_utc(),
    }
    if not _feedback_messages([message_doc]):
        return None

    now = now_utc()
    source_run_ids = [run_id] if run_id else []
    return await save_agent_memory_summary(
        db,
        payload={
            "memory_id": f"operator_feedback:{run_id}" if run_id else None,
            "site_id": site_id,
            "pool_id": pool_id,
            "memory_type": MEMORY_TYPE_OPERATOR_FEEDBACK,
            "period_start": now,
            "period_end": now,
            "summary": _operator_feedback_immediate_summary(content),
            "facts": [f"用户明确反馈：{_short_text(content, limit=180)}"],
            "patterns": _feedback_patterns([message_doc]),
            "lessons": _operator_feedback_lessons(content),
            "risk_baselines": {},
            "created_by": "operator",
            "source_run_ids": source_run_ids,
            "source_decision_ids": [],
            "metadata": {
                "generator": "operator_feedback_message.v1",
                "conversation_id": conversation_id,
                "write_timing": "manual_chat_user_message",
            },
        },
        actor=actor,
    )


async def generate_survival_pattern_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    days: int = 7,
    period_start: datetime | str | None = None,
    period_end: datetime | str | None = None,
) -> dict[str, Any]:
    end = _datetime_or_none(period_end) or now_utc()
    start = _datetime_or_none(period_start) or (end - timedelta(days=max(1, int(days or 7))))
    decisions = await _load_decisions(db, site_id=site_id, pool_id=pool_id, start=start, end=end, limit=300)
    event_windows = await _safe_event_windows(db, site_id=site_id, pool_id=pool_id, decisions=decisions)
    facts = [
        *_survival_facts_from_decisions(decisions),
        *_event_memory_facts(event_windows),
    ]
    patterns = [
        *_survival_patterns_from_decisions(decisions),
        *_event_patterns(event_windows),
    ]
    return await save_agent_memory_summary(
        db,
        payload={
            "memory_id": _memory_id(
                memory_type=MEMORY_TYPE_SURVIVAL_PATTERN,
                site_id=site_id,
                pool_id=pool_id,
                period_start=start,
                period_end=end,
            ),
            "site_id": site_id,
            "pool_id": pool_id,
            "memory_type": MEMORY_TYPE_SURVIVAL_PATTERN,
            "period_start": start,
            "period_end": end,
            "summary": _survival_summary_text(facts=facts, patterns=patterns),
            "facts": facts,
            "patterns": patterns,
            "lessons": _survival_lessons(facts=facts, patterns=patterns),
            "risk_baselines": _risk_baselines(decisions=decisions, event_windows=event_windows),
            "created_by": "agent",
            "source_run_ids": [],
            "source_decision_ids": [item.get("decision_id") or item.get("_id") for item in decisions],
            "metadata": {"generator": "survival_pattern_summary.v2", "days": days},
        },
    )


async def process_due_memory_summaries(
    db: AsyncIOMotorDatabase,
    *,
    settings: Any,
    scheduler_tick_id: str | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Generate due long-term memories from scheduler ticks.

    This only writes Agent-owned memory summaries. It does not mutate account
    pools, refresh sub2api, or start probes.
    """

    pools_response = await list_agent_pools(db)
    raw_pools = pools_response.get("items") if isinstance(pools_response, dict) else []
    pools = [item for item in raw_pools if isinstance(item, dict)]
    limit = _normalize_non_negative_limit(
        getattr(settings, "max_memory_summaries_per_tick", getattr(settings, "max_pool_patrols_per_tick", 3)),
        default=3,
        maximum=100,
    )
    today = now_utc().replace(hour=0, minute=0, second=0, microsecond=0)
    daily_date = (today - timedelta(days=1)).date().isoformat()
    week_end = today - timedelta(days=today.weekday())
    week_start = week_end - timedelta(days=7)

    generated: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    daily_enabled = bool(getattr(settings, "daily_memory_enabled", True))
    weekly_enabled = bool(getattr(settings, "weekly_memory_enabled", True))
    catchup_enabled = bool(getattr(settings, "memory_summary_catchup_enabled", True))

    daily_start, daily_end = _day_bounds_from_date(daily_date)
    daily_candidates = (
        await _due_memory_summary_candidates(
            db,
            pools=pools,
            default_site_id=_clean_optional_string(pools_response.get("site_id")) if isinstance(pools_response, dict) else None,
            memory_type=MEMORY_TYPE_POOL_DAILY,
            period_start=daily_start,
            period_end=daily_end,
            skipped=skipped,
        )
        if daily_enabled
        else []
    )
    weekly_candidates: list[dict[str, Any]] = []
    if weekly_enabled:
        weekly_candidates.extend(
            await _due_memory_summary_candidates(
                db,
                pools=pools,
                default_site_id=_clean_optional_string(pools_response.get("site_id")) if isinstance(pools_response, dict) else None,
                memory_type=MEMORY_TYPE_POOL_WEEKLY,
                period_start=week_start,
                period_end=week_end,
                skipped=skipped,
            )
        )
        weekly_candidates.extend(
            await _due_memory_summary_candidates(
                db,
                pools=pools,
                default_site_id=_clean_optional_string(pools_response.get("site_id")) if isinstance(pools_response, dict) else None,
                memory_type=MEMORY_TYPE_SURVIVAL_PATTERN,
                period_start=week_start,
                period_end=week_end,
                skipped=skipped,
            )
        )

    due_candidates = sorted([*daily_candidates, *weekly_candidates], key=_memory_candidate_sort_key)
    selected = due_candidates[:limit] if catchup_enabled and limit > 0 else []
    if due_candidates and not catchup_enabled:
        skipped.append({"status": "skipped", "reason": "memory_summary_catchup_disabled", "due_count": len(due_candidates)})
    if due_candidates and limit <= 0:
        skipped.append({"status": "skipped", "reason": "max_memory_summaries_per_tick_is_zero", "due_count": len(due_candidates)})

    for candidate in selected:
        try:
            if await _memory_exists(
                db,
                site_id=candidate.get("site_id"),
                pool_id=candidate.get("pool_id"),
                memory_type=candidate["memory_type"],
                period_start=candidate["period_start"],
                period_end=candidate["period_end"],
            ):
                skipped.append(_memory_skip(candidate, "already_exists"))
                continue
            summary = await _generate_due_memory_summary(db, candidate=candidate, daily_date=daily_date, week_start=week_start, week_end=week_end)
            generated.append(_memory_result_view(summary, scheduler_tick_id=scheduler_tick_id))
        except Exception as exc:  # noqa: BLE001 - one pool should not stop the scheduler tick.
            error_item = _memory_skip(candidate, str(exc) or exc.__class__.__name__, status="failed")
            skipped.append(error_item)
            errors.append(error_item)

    selected_daily = sum(1 for item in selected if item.get("memory_type") == MEMORY_TYPE_POOL_DAILY)
    selected_weekly = sum(1 for item in selected if item.get("memory_type") in {MEMORY_TYPE_POOL_WEEKLY, MEMORY_TYPE_SURVIVAL_PATTERN})
    pending_daily = max(0, len(daily_candidates) - selected_daily)
    pending_weekly = max(0, len(weekly_candidates) - selected_weekly)
    return {
        "ok": not errors,
        "implemented": True,
        "total_pools": len(pools),
        "selected_daily": selected_daily,
        "selected_weekly": selected_weekly,
        "selected_total": len(selected),
        "daily_enabled": daily_enabled,
        "weekly_enabled": weekly_enabled,
        "max_memory_summaries_per_tick": limit,
        "memory_summary_catchup_enabled": catchup_enabled,
        "generated": generated,
        "skipped": skipped,
        "errors": errors,
        "pending": {
            "daily": pending_daily,
            "weekly": pending_weekly,
        },
    }


async def _due_memory_summary_candidates(
    db: AsyncIOMotorDatabase,
    *,
    pools: list[dict[str, Any]],
    default_site_id: str | None,
    memory_type: str,
    period_start: datetime,
    period_end: datetime,
    skipped: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for pool in pools:
        pool_id = _clean_optional_string(pool.get("id") or pool.get("pool_id"))
        site_id = _clean_optional_string(pool.get("site_id") or default_site_id)
        if not pool_id:
            skipped.append({"status": "skipped", "memory_type": memory_type, "reason": "missing_pool_id"})
            continue
        if _pool_disabled_for_memory(pool):
            skipped.append({"status": "skipped", "pool_id": pool_id, "site_id": site_id, "memory_type": memory_type, "reason": "pool_disabled"})
            continue
        if await _memory_exists(db, site_id=site_id, pool_id=pool_id, memory_type=memory_type, period_start=period_start, period_end=period_end):
            continue
        candidates.append(
            {
                "status": "pending",
                "memory_type": memory_type,
                "site_id": site_id,
                "pool_id": pool_id,
                "period_start": period_start,
                "period_end": period_end,
                "last_memory_period_end": await _latest_memory_period_end(db, site_id=site_id, pool_id=pool_id, memory_type=memory_type),
            }
        )
    return candidates


async def _generate_due_memory_summary(
    db: AsyncIOMotorDatabase,
    *,
    candidate: dict[str, Any],
    daily_date: str,
    week_start: datetime,
    week_end: datetime,
) -> dict[str, Any]:
    memory_type = candidate.get("memory_type")
    site_id = _clean_optional_string(candidate.get("site_id"))
    pool_id = _clean_optional_string(candidate.get("pool_id"))
    if memory_type == MEMORY_TYPE_POOL_DAILY:
        return await generate_pool_daily_memory_summary(db, site_id=site_id, pool_id=pool_id, date=daily_date)
    if memory_type == MEMORY_TYPE_POOL_WEEKLY:
        return await generate_pool_weekly_memory_summary(
            db,
            site_id=site_id,
            pool_id=pool_id,
            week_start=week_start.date().isoformat(),
            week_end=week_end.date().isoformat(),
        )
    if memory_type == MEMORY_TYPE_SURVIVAL_PATTERN:
        return await generate_survival_pattern_summary(
            db,
            site_id=site_id,
            pool_id=pool_id,
            days=7,
            period_start=week_start,
            period_end=week_end,
        )
    raise ValueError(f"Unsupported memory summary type: {memory_type}")


async def _latest_memory_period_end(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    memory_type: str,
) -> datetime | None:
    query = {"memory_type": memory_type}
    if pool_id:
        query["pool_id"] = pool_id
    elif site_id:
        query["site_id"] = site_id
    else:
        return None
    document = await _memory_summaries(db).find_one(query, sort=[("period_end", -1), ("created_at", -1)])
    if not document:
        return None
    return _datetime_or_none(document.get("period_end") or document.get("created_at"))


def _memory_candidate_sort_key(candidate: dict[str, Any]) -> tuple[datetime, str, str]:
    last_period_end = _datetime_or_none(candidate.get("last_memory_period_end")) or datetime.min.replace(tzinfo=UTC)
    return (last_period_end, str(candidate.get("pool_id") or ""), str(candidate.get("memory_type") or ""))


def _memory_skip(candidate: dict[str, Any], reason: str, *, status: str = "skipped") -> dict[str, Any]:
    return {
        "status": status,
        "pool_id": candidate.get("pool_id"),
        "site_id": candidate.get("site_id"),
        "memory_type": candidate.get("memory_type"),
        "period_start": candidate.get("period_start"),
        "period_end": candidate.get("period_end"),
        "reason": reason,
    }


def _pool_disabled_for_memory(pool: dict[str, Any]) -> bool:
    status = str(pool.get("status") or "").strip().lower()
    remote_status = str(pool.get("remote_status") or "").strip().lower()
    return status == "disabled" or remote_status == "disabled"


async def _refine_memory_payload_with_llm(db: AsyncIOMotorDatabase, *, payload: dict[str, Any]) -> dict[str, Any]:
    """Ask Level 1 to refine a memory summary, with deterministic fallback."""

    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    try:
        from app.modules.agent.llm_client import invoke_agent_level1_json

        llm_result = await invoke_agent_level1_json(
            db,
            system_prompt=_memory_summary_system_prompt(),
            payload=_compact_memory_payload(payload),
        )
        data = llm_result.get("data") if isinstance(llm_result.get("data"), dict) else {}
        refined = dict(payload)
        summary = _clean_optional_string(data.get("summary"))
        if summary:
            refined["summary"] = summary
        facts = _list_of_strings(data.get("facts"))
        if facts:
            refined["facts"] = facts[:20]
        patterns = _list_of_strings(data.get("patterns"))
        if patterns:
            refined["patterns"] = patterns[:20]
        lessons = _list_of_strings(data.get("lessons"))
        if lessons:
            refined["lessons"] = lessons[:20]
        risk_baselines = data.get("risk_baselines")
        if isinstance(risk_baselines, dict):
            deterministic_baselines = payload.get("risk_baselines") if isinstance(payload.get("risk_baselines"), dict) else {}
            refined["risk_baselines"] = {
                **deterministic_baselines,
                **risk_baselines,
                "operational_samples": deterministic_baselines.get("operational_samples", {}),
            }
        refined["metadata"] = {
            **metadata,
            "llm_refined": True,
            "llm_summary": _llm_result_view(llm_result),
        }
        return refined
    except Exception as exc:  # noqa: BLE001 - summary generation must survive LLM downtime.
        return {
            **payload,
            "metadata": {
                **metadata,
                "llm_refined": False,
                "llm_error": str(exc) or exc.__class__.__name__,
            },
        }


async def build_memory_candidates_from_report(db: AsyncIOMotorDatabase, *, report: dict[str, Any]) -> list[str]:
    """Build lightweight candidates that are stored inside the run trace."""

    candidates: list[str] = []
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    severity = report.get("severity") or decision.get("severity")
    suggested = decision.get("suggested_add_count")
    if severity:
        candidates.append(f"本轮风险等级为 {severity}。")
    if suggested is not None:
        candidates.append(f"本轮建议补号数为 {suggested}。")
    for reason in decision.get("main_reasons") or decision.get("reasons") or []:
        if isinstance(reason, str) and reason.strip():
            candidates.append(reason.strip())
    event_assessment = decision.get("event_assessment") if isinstance(decision.get("event_assessment"), dict) else {}
    if event_assessment.get("interpretation"):
        candidates.append(str(event_assessment["interpretation"]))
    return candidates[:12]


async def _list_memory(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    memory_type: str,
    limit: int,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"memory_type": memory_type}
    if pool_id:
        query["pool_id"] = pool_id
    elif site_id:
        query["site_id"] = site_id
    else:
        return []
    cursor = _memory_summaries(db).find(query).sort("period_end", -1).limit(limit)
    return serialize_doc([_memory_view(item) async for item in cursor])


async def _load_runs(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    query = _time_bound_query(site_id=site_id, pool_id=pool_id, start=start, end=end)
    return [item async for item in _runs(db).find(query).sort("created_at", -1).limit(limit)]


async def _load_decisions(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    query = _time_bound_query(site_id=site_id, pool_id=pool_id, start=start, end=end)
    return [item async for item in _decisions(db).find(query).sort("created_at", -1).limit(limit)]


async def _load_messages(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    query = _time_bound_query(site_id=site_id, pool_id=pool_id, start=start, end=end)
    query["role"] = "user"
    return [item async for item in _messages(db).find(query).sort("created_at", -1).limit(limit)]


async def _load_tasks(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "$or": [
            {"created_at": {"$gte": start, "$lt": end}},
            {"updated_at": {"$gte": start, "$lt": end}},
            {"state_history.changed_at": {"$gte": start, "$lt": end}},
        ]
    }
    if pool_id:
        query["pool_id"] = pool_id
    elif site_id:
        query["site_id"] = site_id
    return [item async for item in _tasks(db).find(query).sort("updated_at", -1).limit(limit)]


async def _load_operational_sample_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    start: datetime,
    end: datetime,
) -> dict[str, Any]:
    resolved_site_id = _clean_optional_string(site_id) or _site_id_from_pool_id(pool_id)
    group_id = _group_id_from_pool_id(pool_id)
    if not resolved_site_id or group_id is None:
        return {
            "schema_version": "agent_operational_sample_summary.v1",
            "available": False,
            "data_gaps": ["site_id_or_group_id_missing"],
        }
    query = {
        "site_id": resolved_site_id,
        "group_id": group_id,
        "sampled_at": {"$gte": start, "$lt": end},
    }
    data_gaps: list[str] = []
    try:
        capacity_samples = [
            item
            async for item in db.sub2api_capacity_samples.find(query).sort("sampled_at", 1).limit(2200)
        ]
    except Exception as exc:  # noqa: BLE001 - memory generation keeps a deterministic fallback.
        capacity_samples = []
        data_gaps.append(f"capacity_samples_unavailable:{exc}")
    try:
        tpm_samples = [
            item
            async for item in db.sub2api_tpm_samples.find(query).sort("sampled_at", 1).limit(12000)
        ]
    except Exception as exc:  # noqa: BLE001 - memory generation keeps a deterministic fallback.
        tpm_samples = []
        data_gaps.append(f"tpm_samples_unavailable:{exc}")
    if not capacity_samples:
        data_gaps.append("capacity_samples_empty")
    if not tpm_samples:
        data_gaps.append("tpm_samples_empty")
    return {
        "schema_version": "agent_operational_sample_summary.v1",
        "available": bool(capacity_samples or tpm_samples),
        "site_id": resolved_site_id,
        "group_id": group_id,
        "period_start": start,
        "period_end": end,
        "capacity": _aggregate_capacity_samples(capacity_samples),
        "usage_pressure": _aggregate_tpm_samples(tpm_samples),
        "data_gaps": data_gaps,
        "source": "sub2api_capacity_samples+sub2api_tpm_samples",
    }


def _aggregate_capacity_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    rows = [item.get("capacity_summary") for item in samples if isinstance(item.get("capacity_summary"), dict)]
    available_accounts = _sample_numbers(rows, "available_accounts")
    actual_runway = _sample_numbers(rows, "actual_runway_hours")
    dynamic_runway = _sample_numbers(rows, "dynamic_runway_hours")
    concurrency_coverage = _sample_numbers(rows, "concurrency_coverage")
    burn_rates = _sample_numbers(rows, "burn_usd_per_hour")
    refill_counts = _sample_numbers(rows, "recommended_refill_accounts")
    health_counts = Counter(str(row.get("health_status") or "unknown") for row in rows)
    pressure_counts = Counter(str(row.get("pressure_stage") or "unknown") for row in rows)
    return {
        "sample_count": len(rows),
        "first_sampled_at": samples[0].get("sampled_at") if samples else None,
        "latest_sampled_at": samples[-1].get("sampled_at") if samples else None,
        "health_status_counts": dict(health_counts.most_common()),
        "pressure_stage_counts": dict(pressure_counts.most_common()),
        "available_accounts": _series_change(available_accounts),
        "actual_runway_hours": _series_stats(actual_runway),
        "dynamic_runway_hours": _series_stats(dynamic_runway),
        "concurrency_coverage": _series_stats(concurrency_coverage),
        "burn_usd_per_hour": _series_stats(burn_rates),
        "replenishment_required_samples": sum(1 for row in rows if row.get("replenishment_required") is True),
        "recommended_refill_accounts_max": int(max(refill_counts)) if refill_counts else None,
        "recommended_refill_by_account_type_max": _max_refill_options(rows),
    }


def _aggregate_tpm_samples(samples: list[dict[str, Any]]) -> dict[str, Any]:
    tpm = _sample_numbers(samples, "tpm")
    rpm = _sample_numbers(samples, "rpm")
    concurrency = _sample_numbers(samples, "current_concurrency")
    source_counts = Counter(str(item.get("source") or "unknown") for item in samples)
    return {
        "sample_count": len(samples),
        "first_sampled_at": samples[0].get("sampled_at") if samples else None,
        "latest_sampled_at": samples[-1].get("sampled_at") if samples else None,
        "tpm": _series_stats(tpm),
        "rpm": _series_stats(rpm),
        "concurrency": _series_stats(concurrency),
        "source_counts": dict(source_counts.most_common()),
        "missing_tpm_samples": max(0, len(samples) - len(tpm)),
    }


def _sample_numbers(items: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for item in items:
        value = _number_or_none(item.get(key))
        if value is not None:
            values.append(value)
    return values


def _series_stats(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "min": None, "max": None, "avg": None, "p90": None, "latest": None}
    ordered = sorted(values)
    p90_index = min(len(ordered) - 1, max(0, ceil(len(ordered) * 0.9) - 1))
    return {
        "count": len(values),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
        "avg": round(sum(values) / len(values), 4),
        "p90": round(ordered[p90_index], 4),
        "latest": round(values[-1], 4),
    }


def _series_change(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"count": 0, "start": None, "end": None, "change": None, "min": None, "max": None}
    return {
        "count": len(values),
        "start": round(values[0], 4),
        "end": round(values[-1], 4),
        "change": round(values[-1] - values[0], 4),
        "min": round(min(values), 4),
        "max": round(max(values), 4),
    }


def _max_refill_options(rows: list[dict[str, Any]]) -> dict[str, int]:
    result: dict[str, int] = {}
    for row in rows:
        options = row.get("recommended_refill_options") if isinstance(row.get("recommended_refill_options"), dict) else {}
        for raw_type, option in options.items():
            if not isinstance(option, dict):
                continue
            count = _int_or_none(option.get("recommended_refill_accounts"))
            if count is not None:
                account_type = str(option.get("account_type") or raw_type)
                result[account_type] = max(result.get(account_type, 0), count)
    return result


def _operational_sample_facts(summary: dict[str, Any]) -> list[str]:
    if summary.get("available") is not True:
        return []
    capacity = summary.get("capacity") if isinstance(summary.get("capacity"), dict) else {}
    pressure = summary.get("usage_pressure") if isinstance(summary.get("usage_pressure"), dict) else {}
    facts = [
        f"历史采样包含 {capacity.get('sample_count', 0)} 个容量快照和 {pressure.get('sample_count', 0)} 个 TPM/RPM 样本。"
    ]
    accounts = capacity.get("available_accounts") if isinstance(capacity.get("available_accounts"), dict) else {}
    if accounts.get("change") is not None:
        facts.append(
            f"采样窗口内可用账号从 {accounts.get('start')} 变为 {accounts.get('end')}，变化 {accounts.get('change')}。"
        )
    tpm = pressure.get("tpm") if isinstance(pressure.get("tpm"), dict) else {}
    if tpm.get("max") is not None:
        facts.append(f"采样窗口 TPM 平均 {tpm.get('avg')}，P90 {tpm.get('p90')}，峰值 {tpm.get('max')}。")
    coverage = capacity.get("concurrency_coverage") if isinstance(capacity.get("concurrency_coverage"), dict) else {}
    if coverage.get("min") is not None:
        facts.append(f"采样窗口并发覆盖率最低 {coverage.get('min')}，平均 {coverage.get('avg')}。")
    return facts


def _operational_sample_patterns(summary: dict[str, Any]) -> list[str]:
    if summary.get("available") is not True:
        return []
    capacity = summary.get("capacity") if isinstance(summary.get("capacity"), dict) else {}
    patterns: list[str] = []
    health_counts = capacity.get("health_status_counts") if isinstance(capacity.get("health_status_counts"), dict) else {}
    if health_counts:
        patterns.append(f"容量采样健康状态分布为 {health_counts}。")
    if int(capacity.get("replenishment_required_samples") or 0) > 0:
        patterns.append(
            f"主系统在 {capacity.get('replenishment_required_samples')} 个容量采样点标记需要补充容量。"
        )
    refill_options = capacity.get("recommended_refill_by_account_type_max")
    if isinstance(refill_options, dict) and refill_options:
        patterns.append(f"按账号类型统计的窗口内最大补号参考为 {refill_options}，仅作历史证据。")
    return patterns


async def _safe_event_windows(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    decisions: list[dict[str, Any]],
) -> dict[str, Any]:
    if not site_id:
        site_id = next((_clean_optional_string(item.get("site_id")) for item in decisions if item.get("site_id")), None)
    group_id = _group_id_from_pool_id(pool_id) or _group_id_from_decisions(decisions)
    account_type = _account_type_from_decisions(decisions)
    if not site_id or group_id is None:
        return {}
    try:
        return await read_agent_event_windows(db, site_id=site_id, group_id=group_id, account_type=account_type)
    except Exception:
        return {}


def _time_bound_query(*, site_id: str | None, pool_id: str | None, start: datetime, end: datetime) -> dict[str, Any]:
    query: dict[str, Any] = {"created_at": {"$gte": start, "$lt": end}}
    if pool_id:
        query["pool_id"] = pool_id
    elif site_id:
        query["site_id"] = site_id
    return query


async def _memory_exists(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    memory_type: str,
    period_start: datetime,
    period_end: datetime,
) -> bool:
    memory_id = _memory_id(memory_type=memory_type, site_id=site_id, pool_id=pool_id, period_start=period_start, period_end=period_end)
    query = {
        "$or": [
            {"_id": memory_id},
            {
                "site_id": _clean_optional_string(site_id),
                "pool_id": _clean_optional_string(pool_id),
                "memory_type": memory_type,
                "period_start": period_start,
                "period_end": period_end,
            },
        ]
    }
    return bool(await _memory_summaries(db).find_one(query, {"_id": 1}))


def _memory_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "memory_id": item.get("memory_id") or item.get("_id"),
        "site_id": item.get("site_id"),
        "pool_id": item.get("pool_id"),
        "memory_type": item.get("memory_type"),
        "period_start": item.get("period_start"),
        "period_end": item.get("period_end"),
        "summary": item.get("summary"),
        "facts": item.get("facts") if isinstance(item.get("facts"), list) else [],
        "patterns": item.get("patterns") if isinstance(item.get("patterns"), list) else [],
        "lessons": item.get("lessons") if isinstance(item.get("lessons"), list) else [],
        "risk_baselines": item.get("risk_baselines") if isinstance(item.get("risk_baselines"), dict) else {},
        "source_run_ids": item.get("source_run_ids") if isinstance(item.get("source_run_ids"), list) else [],
        "source_decision_ids": item.get("source_decision_ids") if isinstance(item.get("source_decision_ids"), list) else [],
        "created_at": item.get("created_at"),
    }


def _memory_result_view(item: dict[str, Any], *, scheduler_tick_id: str | None) -> dict[str, Any]:
    return {
        "memory_id": item.get("memory_id") or item.get("_id"),
        "memory_type": item.get("memory_type"),
        "site_id": item.get("site_id"),
        "pool_id": item.get("pool_id"),
        "period_start": item.get("period_start"),
        "period_end": item.get("period_end"),
        "scheduler_tick_id": scheduler_tick_id,
    }


def _memory_summary_system_prompt() -> str:
    return (
        "You summarize historical account-pool operations for long-term Agent memory. "
        "You are not making a current refill decision. Do not invent events, numbers, "
        "capacity, notifications, or operator feedback. If evidence is insufficient, "
        "say so. Return one JSON object with summary, facts, patterns, lessons, and "
        "risk_baselines. facts, patterns, and lessons must be arrays of short strings."
    )


def _compact_memory_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "agent_memory_summary_input.v1",
        "memory_type": payload.get("memory_type"),
        "site_id": payload.get("site_id"),
        "pool_id": payload.get("pool_id"),
        "period_start": payload.get("period_start"),
        "period_end": payload.get("period_end"),
        "deterministic_summary": payload.get("summary"),
        "facts": _list_of_values(payload.get("facts"))[:30],
        "patterns": _list_of_values(payload.get("patterns"))[:30],
        "lessons": _list_of_values(payload.get("lessons"))[:30],
        "risk_baselines": payload.get("risk_baselines") if isinstance(payload.get("risk_baselines"), dict) else {},
        "metadata": payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
        "output_contract": {
            "summary": "string",
            "facts": ["string"],
            "patterns": ["string"],
            "lessons": ["string"],
            "risk_baselines": {},
        },
    }


def _llm_result_view(llm_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": llm_result.get("enabled"),
        "configured": llm_result.get("configured"),
        "level": llm_result.get("level"),
        "model": llm_result.get("model"),
        "source": llm_result.get("source"),
        "framework": llm_result.get("framework"),
    }


def _run_and_decision_facts(*, runs: list[dict[str, Any]], decisions: list[dict[str, Any]]) -> list[str]:
    facts: list[str] = []
    facts.append(f"当天 Agent 运行 {len(runs)} 次，形成 {len(decisions)} 条决策。")
    facts.extend(_risk_distribution_facts(decisions))
    suggested_counts = _suggested_counts(decisions)
    if suggested_counts:
        facts.append(f"当天建议补号数范围为 {min(suggested_counts)} 到 {max(suggested_counts)}。")
    return facts


def _risk_distribution_facts(decisions: list[dict[str, Any]]) -> list[str]:
    severities = Counter(str(item.get("severity") or "unknown") for item in decisions)
    return [f"风险等级分布为 {dict(severities.most_common())}。"] if severities else []


def _capacity_memory_facts(decisions: list[dict[str, Any]]) -> list[str]:
    facts: list[str] = []
    latest = decisions[0] if decisions else {}
    latest_decision = _decision_payload(latest)
    if _int_or_none(latest_decision.get("suggested_add_count")):
        facts.append(
            f"最近一次补号建议为 {latest_decision.get('suggested_account_type') or '未指定类型'} "
            f"{latest_decision.get('suggested_add_count')} 个。"
        )
    capacity = latest.get("capacity_snapshot") if isinstance(latest.get("capacity_snapshot"), dict) else {}
    if capacity.get("current_speed_days") is not None:
        facts.append(f"最近一次决策记录的可支撑时间为 {capacity.get('current_speed_days')} 天。")
    if capacity.get("recent_day_five_hour_peak_multiple") is not None:
        facts.append(f"最近一次决策记录的最近一天 5h 峰值容量倍数为 {capacity.get('recent_day_five_hour_peak_multiple')}x。")
    if capacity.get("burst_1h_five_hour_multiple") is not None:
        facts.append(f"最近一次决策记录的突发 1h 预估 5h 容量倍数为 {capacity.get('burst_1h_five_hour_multiple')}x。")
    return facts


def _event_memory_facts(event_windows: dict[str, Any]) -> list[str]:
    facts: list[str] = []
    for key, label in (("summary_1h", "最近 1h"), ("summary_6h", "最近 6h"), ("summary_24h", "最近 24h"), ("summary_7d", "最近 7d")):
        summary = event_windows.get(key) if isinstance(event_windows.get(key), dict) else {}
        total = summary.get("total_events")
        account_count = summary.get("account_count")
        if total:
            facts.append(f"{label} 事件数为 {total}，样本涉及 {account_count} 个账号。")
        inner = summary.get("summary") if isinstance(summary.get("summary"), dict) else {}
        if inner.get("detected_401"):
            facts.append(f"{label} 检测到 {inner.get('detected_401')} 个 401 账号。")
        if inner.get("recovered_401"):
            facts.append(f"{label} 恢复 {inner.get('recovered_401')} 个 401 账号。")
    consensus = event_windows.get("consensus_evidence") if isinstance(event_windows.get("consensus_evidence"), dict) else {}
    capacity_consensus = consensus.get("capacity_notifications") if isinstance(consensus.get("capacity_notifications"), dict) else {}
    if capacity_consensus.get("event_count_7d"):
        facts.append(
            f"主系统容量通知当前状态为 {capacity_consensus.get('current_state')}，"
            f"最近 7d 有 {capacity_consensus.get('event_count_7d')} 条容量告警或恢复证据。"
        )
    if capacity_consensus.get("last_recovered_at"):
        facts.append(f"主系统最近一次容量恢复确认时间为 {capacity_consensus.get('last_recovered_at')}。")
    return facts[:12]


def _feedback_memory_facts(messages: list[dict[str, Any]]) -> list[str]:
    feedback = _feedback_messages(messages)
    return [f"人工反馈：{_short_text(item.get('content'), limit=120)}" for item in feedback[:8]]


def _task_memory_facts(tasks: list[dict[str, Any]]) -> list[str]:
    if not tasks:
        return []
    statuses = Counter(str(item.get("status") or "unknown") for item in tasks)
    alert_drafts = sum(1 for item in tasks if item.get("alert_draft") or item.get("alert_status") == "drafted")
    feedback_count = sum(len(item.get("human_feedback_history") or []) for item in tasks if isinstance(item.get("human_feedback_history"), list))
    facts = [f"Agent task status distribution in window: {dict(statuses.most_common())}."]
    if alert_drafts:
        facts.append(f"Agent kept {alert_drafts} alert draft task(s) in this window.")
    if feedback_count:
        facts.append(f"Agent task history contains {feedback_count} human feedback record(s) in this window.")
    return facts


def _risk_patterns(decisions: list[dict[str, Any]]) -> list[str]:
    patterns: list[str] = []
    alert_count = sum(1 for item in decisions if _decision_payload(item).get("should_alert"))
    confirm_count = sum(1 for item in decisions if item.get("requires_human_confirm") or _decision_payload(item).get("requires_human_confirm"))
    if alert_count:
        patterns.append(f"有 {alert_count} 条决策建议告警。")
    if confirm_count:
        patterns.append(f"有 {confirm_count} 条决策要求人工确认。")
    danger_count = sum(1 for item in decisions if str(item.get("severity") or "") in {"danger", "critical"})
    if danger_count:
        patterns.append(f"出现 {danger_count} 次 danger/critical 等级风险。")
    return patterns


def _event_patterns(event_windows: dict[str, Any]) -> list[str]:
    patterns: list[str] = []
    for item in event_windows.get("notable_patterns") if isinstance(event_windows.get("notable_patterns"), list) else []:
        if isinstance(item, dict) and item.get("interpretation"):
            window = item.get("window")
            patterns.append(f"{window} 窗口：{item.get('interpretation')}")
    return patterns[:10]


def _feedback_patterns(messages: list[dict[str, Any]]) -> list[str]:
    feedback = _feedback_messages(messages)
    patterns: list[str] = []
    if feedback:
        patterns.append(f"发现 {len(feedback)} 条疑似人工反馈或纠正信息。")
    if any("夜间" in str(item.get("content") or "") for item in feedback):
        patterns.append("人工反馈中提到夜间策略，需要在后续决策中关注。")
    if any("活动" in str(item.get("content") or "") or "批量任务" in str(item.get("content") or "") for item in feedback):
        patterns.append("人工反馈中提到活动或批量任务，后续判断流量上涨时需要区分正常业务流量。")
    return patterns


def _decision_bias_patterns(decisions: list[dict[str, Any]]) -> list[str]:
    suggested_counts = _suggested_counts(decisions)
    if not suggested_counts:
        return []
    avg = sum(suggested_counts) / len(suggested_counts)
    if avg >= 50:
        return [f"最近 7 天平均建议补号数约 {avg:.1f}，整体偏激进或处于高压状态。"]
    if 0 < avg <= 5:
        return [f"最近 7 天平均建议补号数约 {avg:.1f}，整体偏保守或池子相对平稳。"]
    return [f"最近 7 天平均建议补号数约 {avg:.1f}。"]


def _task_patterns(tasks: list[dict[str, Any]]) -> list[str]:
    transitions: Counter[str] = Counter()
    for item in tasks:
        history = item.get("state_history")
        if not isinstance(history, list):
            continue
        for change in history:
            if not isinstance(change, dict):
                continue
            from_status = change.get("from_status") or "unknown"
            to_status = change.get("to_status") or "unknown"
            transitions[f"{from_status}->{to_status}"] += 1
    if not transitions:
        return []
    return [f"Agent task state transition pattern: {dict(transitions.most_common(6))}."]


def _survival_facts_from_decisions(decisions: list[dict[str, Any]]) -> list[str]:
    facts: list[str] = []
    survival_values = []
    for item in decisions:
        probe = item.get("probe_snapshot") if isinstance(item.get("probe_snapshot"), dict) else {}
        value = _number_or_none(probe.get("median_survival_hours_7d"))
        if value is not None:
            survival_values.append(value)
    if survival_values:
        facts.append(f"最近记录的 7d 中位存活时间范围为 {min(survival_values):.1f} 到 {max(survival_values):.1f} 小时。")
    return facts


def _survival_patterns_from_decisions(decisions: list[dict[str, Any]]) -> list[str]:
    patterns: list[str] = []
    duplicate_alerts = []
    for item in decisions:
        probe = item.get("probe_snapshot") if isinstance(item.get("probe_snapshot"), dict) else {}
        value = _number_or_none(probe.get("duplicate_email_alert_count"))
        if value is not None:
            duplicate_alerts.append(value)
    if duplicate_alerts and max(duplicate_alerts) > 0:
        patterns.append(f"最近出现过重复邮箱告警，最高记录为 {int(max(duplicate_alerts))} 条。")
    return patterns


def _daily_lessons(*, decisions: list[dict[str, Any]], messages: list[dict[str, Any]], event_windows: dict[str, Any]) -> list[str]:
    lessons: list[str] = []
    lessons.extend(_decision_summary_lessons(decisions, limit=5))
    lessons.extend(_feedback_memory_facts(messages)[:3])
    lessons.extend(_event_patterns(event_windows)[:3])
    return lessons[:12]


def _weekly_lessons(*, decisions: list[dict[str, Any]], event_windows: dict[str, Any]) -> list[str]:
    lessons = _decision_summary_lessons(decisions, limit=8)
    lessons.extend(_event_patterns(event_windows)[:5])
    lessons.extend(_decision_bias_patterns(decisions))
    return lessons[:12]


def _decision_summary_lessons(decisions: list[dict[str, Any]], *, limit: int) -> list[str]:
    lessons: list[str] = []
    for item in decisions[:limit]:
        summary = _clean_optional_string(item.get("summary") or item.get("headline"))
        if summary:
            lessons.append(summary)
    return lessons


def _decision_review_lessons(*, original: dict[str, Any], later_decisions: list[dict[str, Any]]) -> list[str]:
    lessons: list[str] = []
    original_severity = str(original.get("severity") or "")
    later_severities = [str(item.get("severity") or "") for item in later_decisions if item.get("decision_id") != original.get("decision_id")]
    if original_severity in {"danger", "critical"} and any(value in {"healthy", "watch"} for value in later_severities):
        lessons.append("原始高风险决策后续风险下降，可能说明处理建议或外部补救有效。")
    if original_severity in {"healthy", "watch"} and any(value in {"danger", "critical"} for value in later_severities):
        lessons.append("原始低风险决策后续出现高风险，需要复盘是否低估了风险。")
    if not lessons:
        lessons.append("后续记录不足，暂不能判断该决策是否有效。")
    return lessons


def _daily_summary_text(*, date: str, runs: list[dict[str, Any]], decisions: list[dict[str, Any]], event_windows: dict[str, Any]) -> str:
    summary_24h = event_windows.get("summary_24h") if isinstance(event_windows.get("summary_24h"), dict) else {}
    event_count = summary_24h.get("total_events")
    return f"{date} 运营摘要：Agent 运行 {len(runs)} 次，形成 {len(decisions)} 条决策，最近 24h 事件数为 {event_count if event_count is not None else '未知'}。"


def _weekly_summary_text(*, runs: list[dict[str, Any]], decisions: list[dict[str, Any]], event_windows: dict[str, Any]) -> str:
    summary_7d = event_windows.get("summary_7d") if isinstance(event_windows.get("summary_7d"), dict) else {}
    return f"最近 7 天运营摘要：Agent 运行 {len(runs)} 次，形成 {len(decisions)} 条决策，7d 事件数为 {summary_7d.get('total_events') if summary_7d else '未知'}。"


def _decision_review_summary(*, decision: dict[str, Any], later_decisions: list[dict[str, Any]]) -> str:
    payload = _decision_payload(decision)
    return (
        f"决策复盘：原始风险等级 {decision.get('severity') or payload.get('severity') or '未知'}，"
        f"建议补号 {payload.get('suggested_add_count') if payload.get('suggested_add_count') is not None else '未知'}，"
        f"后续观察到 {max(0, len(later_decisions) - 1)} 条相关决策。"
    )


def _operator_feedback_summary_text(messages: list[dict[str, Any]]) -> str:
    if not messages:
        return "本周期未识别到明确人工反馈。"
    return f"本周期识别到 {len(messages)} 条人工反馈或纠正信息，后续决策应优先参考。"


def _operator_feedback_immediate_summary(content: str) -> str:
    return f"人工反馈：{_short_text(content, limit=160)}"


def _operator_feedback_lessons(content: str) -> list[str]:
    lessons = [f"后续同类判断应优先参考该人工反馈：{_short_text(content, limit=160)}"]
    if "批量任务" in content or "批量" in content or "活动" in content:
        lessons.append("后续遇到中午或活动期间流量上涨时，需要区分正常业务批量任务和异常流量。")
    if "不是" in content and "异常流量" in content:
        lessons.append("人工已纠正某次上涨不属于异常流量，后续不要仅凭上涨趋势直接归因为异常。")
    if "不用补" in content:
        lessons.append("人工明确表示当前不需要补号时，后续建议应降低补号动作强度并说明观察条件。")
    if "需要补" in content:
        lessons.append("人工明确表示需要补号时，后续建议应结合容量和事件证据给出更积极的准备方案。")
    return lessons[:6]


def _survival_summary_text(*, facts: list[str], patterns: list[str]) -> str:
    if facts or patterns:
        return "账号存活规律摘要：" + " ".join([*facts[:3], *patterns[:3]])
    return "账号存活规律摘要：当前缺少足够存活数据，暂不能形成稳定规律。"


def _survival_lessons(*, facts: list[str], patterns: list[str]) -> list[str]:
    lessons = [*facts, *patterns]
    return lessons[:10] if lessons else ["后续需要继续积累 401、恢复、重复邮箱和存活时长数据。"]


def _risk_baselines(*, decisions: list[dict[str, Any]], event_windows: dict[str, Any]) -> dict[str, Any]:
    severities = Counter(str(item.get("severity") or "unknown") for item in decisions)
    suggested_counts = _suggested_counts(decisions)
    summary_7d = event_windows.get("summary_7d") if isinstance(event_windows.get("summary_7d"), dict) else {}
    inner_7d = summary_7d.get("summary") if isinstance(summary_7d.get("summary"), dict) else {}
    return {
        "severity_counts": dict(severities.most_common()),
        "suggested_add_count_min": min(suggested_counts) if suggested_counts else None,
        "suggested_add_count_max": max(suggested_counts) if suggested_counts else None,
        "suggested_add_count_avg": round(sum(suggested_counts) / len(suggested_counts), 2) if suggested_counts else None,
        "normal_401_7d": inner_7d.get("detected_401"),
        "event_count_7d": summary_7d.get("total_events"),
    }


def _feedback_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in messages:
        content = str(item.get("content") or "")
        if any(keyword in content for keyword in FEEDBACK_KEYWORDS):
            result.append(item)
    return result


def _decision_payload(item: dict[str, Any]) -> dict[str, Any]:
    return item.get("decision") if isinstance(item.get("decision"), dict) else {}


def _suggested_counts(decisions: list[dict[str, Any]]) -> list[int]:
    values = []
    for item in decisions:
        value = _int_or_none(_decision_payload(item).get("suggested_add_count"))
        if value is not None:
            values.append(value)
    return values


def _group_id_from_pool_id(pool_id: str | None) -> int | None:
    parts = str(pool_id or "").split(":")
    if len(parts) == 3 and parts[0] == "sub2api":
        return _int_or_none(parts[2])
    return None


def _site_id_from_pool_id(pool_id: str | None) -> str | None:
    parts = str(pool_id or "").split(":")
    if len(parts) == 3 and parts[0] == "sub2api":
        return _clean_optional_string(parts[1])
    return None


def _group_id_from_decisions(decisions: list[dict[str, Any]]) -> int | None:
    for item in decisions:
        capacity = item.get("capacity_snapshot") if isinstance(item.get("capacity_snapshot"), dict) else {}
        value = _int_or_none(capacity.get("group_id"))
        if value is not None:
            return value
    return None


def _account_type_from_decisions(decisions: list[dict[str, Any]]) -> str | None:
    for item in decisions:
        capacity = item.get("capacity_snapshot") if isinstance(item.get("capacity_snapshot"), dict) else {}
        pool = capacity.get("pool") if isinstance(capacity.get("pool"), dict) else {}
        value = _clean_optional_string(pool.get("account_type"))
        if value:
            return value
    return None


def _day_bounds_from_date(value: str) -> tuple[datetime, datetime]:
    date_text = str(value or "").strip()
    try:
        start = datetime.fromisoformat(date_text)
    except ValueError:
        start = now_utc()
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    start = start.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _memory_summaries(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_MEMORY_SUMMARIES_COLLECTION]


def _runs(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_RUNS_COLLECTION]


def _decisions(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_DECISIONS_COLLECTION]


def _messages(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_MESSAGES_COLLECTION]


def _tasks(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_TASKS_COLLECTION]


def _new_id() -> str:
    return secrets.token_hex(12)


def _memory_id(
    *,
    memory_type: str,
    site_id: str | None,
    pool_id: str | None,
    period_start: datetime,
    period_end: datetime,
) -> str:
    return ":".join(
        [
            "agent_memory",
            _safe_id_part(memory_type),
            _safe_id_part(site_id),
            _safe_id_part(pool_id),
            _date_key(period_start),
            _date_key(period_end),
        ]
    )


def _safe_id_part(value: Any) -> str:
    text = _clean_optional_string(value) or "-"
    return text.replace(":", "_").replace("/", "_").replace("\\", "_").replace(" ", "_")


def _date_key(value: datetime) -> str:
    return value.date().isoformat()


def _normalize_memory_type(value: Any) -> str:
    text = _clean_optional_string(value) or MEMORY_TYPE_POOL_DAILY
    return text if text in ALLOWED_MEMORY_TYPES else MEMORY_TYPE_POOL_DAILY


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _list_of_values(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    return [item for item in value if item is not None]


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None and str(item).strip()]


def _normalize_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, maximum))


def _normalize_non_negative_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(0, min(number, maximum))


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _short_text(value: Any, *, limit: int) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _actor_id(actor: dict[str, Any] | None) -> str | None:
    if not actor:
        return None
    value = actor.get("_id") or actor.get("id") or actor.get("user_id")
    return str(value) if value is not None else None
