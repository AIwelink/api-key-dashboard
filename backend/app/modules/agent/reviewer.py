from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.capacity import build_agent_capacity_status, build_agent_concurrency_status, read_pool_capacity
from app.modules.agent.event_stream import read_agent_event_windows
from app.modules.agent.llm_client import invoke_agent_level1_json
from app.modules.agent.long_term_memory import save_agent_memory_summary
from app.utils import now_utc, serialize_doc


DECISION_REVIEW_SCHEMA_VERSION = "agent_decision_review.v1"
REVIEW_RESULTS = {
    "useful",
    "too_conservative",
    "too_aggressive",
    "insufficient_data",
    "wrong_interpretation",
}


async def review_agent_decision(
    db: AsyncIOMotorDatabase,
    *,
    decision_id: str,
    task_id: str | None = None,
    run_id: str | None = None,
    actor: dict[str, Any] | None = None,
    review_window_hours: int = 24,
) -> dict[str, Any]:
    """Review whether a previous Agent decision was useful.

    This is not a new replenishment decision. It compares the prior decision
    with what happened afterwards, then stores a compact decision_review memory.
    """

    decision = await _load_decision(db, decision_id)
    if not decision:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent decision not found")

    review_pack = await _build_review_pack(
        db,
        decision=decision,
        run_id=run_id,
        review_window_hours=review_window_hours,
    )
    llm_info: dict[str, Any]
    try:
        llm_result = await invoke_agent_level1_json(
            db,
            system_prompt=_review_prompt(),
            payload={"task": "review_agent_decision", "review_pack": review_pack},
        )
        raw_review = llm_result.get("data") if isinstance(llm_result.get("data"), dict) else {}
        review = _validate_review(raw_review, review_pack=review_pack)
        llm_info = {
            "enabled": llm_result.get("enabled"),
            "configured": llm_result.get("configured"),
            "level": llm_result.get("level"),
            "model": llm_result.get("model"),
            "source": llm_result.get("source"),
            "framework": llm_result.get("framework"),
            "raw_text": llm_result.get("raw_text"),
        }
    except Exception as exc:  # noqa: BLE001 - review should still produce an auditable fallback.
        review = _fallback_review(review_pack, error=str(exc))
        llm_info = {"enabled": False, "configured": False, "framework": "fallback", "error": str(exc)}

    memory_payload = _memory_payload_from_review(review=review, review_pack=review_pack, run_id=run_id)
    memory = await save_agent_memory_summary(db, payload=memory_payload, actor=actor)
    review["memory_id"] = memory.get("memory_id")
    review["llm"] = llm_info
    review["review_pack_summary"] = _review_pack_summary(review_pack)
    if task_id:
        await _link_review_memory_to_task(db, task_id=task_id, review=review)
    return serialize_doc(review)


async def _load_decision(db: AsyncIOMotorDatabase, decision_id: str) -> dict[str, Any] | None:
    return await db.agent_decisions.find_one({"_id": decision_id}) or await db.agent_decisions.find_one({"decision_id": decision_id})


async def _link_review_memory_to_task(db: AsyncIOMotorDatabase, *, task_id: str, review: dict[str, Any]) -> None:
    memory_id = _clean_optional_string(review.get("memory_id"))
    if not memory_id:
        return
    now = now_utc()
    await db.agent_tasks.update_one(
        {"$or": [{"_id": task_id}, {"task_id": task_id}]},
        {
            "$set": {
                "last_review_memory_id": memory_id,
                "last_review_result": review.get("review_result"),
                "updated_at": now,
            },
            "$addToSet": {"linked_memory_ids": memory_id},
        },
    )


async def _build_review_pack(
    db: AsyncIOMotorDatabase,
    *,
    decision: dict[str, Any],
    run_id: str | None,
    review_window_hours: int,
) -> dict[str, Any]:
    created_at = _datetime_or_none(decision.get("created_at")) or now_utc()
    now = now_utc()
    period_end = min(now, created_at + timedelta(hours=max(1, int(review_window_hours or 24))))
    pool_id = _clean_optional_string(decision.get("pool_id"))
    site_id = _clean_optional_string(decision.get("site_id"))
    original_payload = decision.get("decision") if isinstance(decision.get("decision"), dict) else {}
    original_capacity = decision.get("capacity_snapshot") if isinstance(decision.get("capacity_snapshot"), dict) else {}
    current_capacity = await _safe_current_capacity(db, pool_id=pool_id)
    target_site_id = site_id or _clean_optional_string(current_capacity.get("site_id"))
    target_group_id = _group_id_from_capacity_or_pool(current_capacity, pool_id)
    target_account_type = _account_type_from_capacity_or_decision(current_capacity, decision)
    event_windows = await _safe_event_windows(
        db,
        site_id=target_site_id,
        group_id=target_group_id,
        pool_id=pool_id,
        account_type=target_account_type,
    )
    later_decisions = await _load_later_decisions(
        db,
        pool_id=pool_id,
        created_at=created_at,
        period_end=period_end,
        exclude_decision_id=str(decision.get("decision_id") or decision.get("_id")),
    )
    feedback_memories = await _load_feedback_memories(db, site_id=target_site_id, pool_id=pool_id, limit=5)
    return serialize_doc(
        {
            "schema_version": "agent_decision_review_pack.v1",
            "run_id": run_id,
            "review_target": {
                "decision_id": decision.get("decision_id") or decision.get("_id"),
                "run_id": decision.get("run_id"),
                "pool_id": pool_id,
                "site_id": site_id,
                "created_at": decision.get("created_at"),
                "severity": decision.get("severity") or original_payload.get("severity"),
                "summary": decision.get("summary") or original_payload.get("summary"),
                "headline": decision.get("headline") or original_payload.get("headline"),
                "operator_message": original_payload.get("operator_message"),
                "should_add_accounts": bool(original_payload.get("should_add_accounts")),
                "suggested_add_count": original_payload.get("suggested_add_count"),
                "should_alert": bool(original_payload.get("should_alert")),
                "requires_human_confirm": bool(
                    original_payload.get("requires_human_confirm") or original_payload.get("manual_review_required")
                ),
                "event_assessment": original_payload.get("event_assessment") if isinstance(original_payload.get("event_assessment"), dict) else {},
                "evidence_summary": original_payload.get("evidence_summary") if isinstance(original_payload.get("evidence_summary"), dict) else {},
                "main_reasons": _list_of_strings(original_payload.get("main_reasons") or decision.get("reasons")),
                "data_gaps": _list_of_strings(original_payload.get("data_gaps")),
            },
            "review_window": {
                "period_start": created_at,
                "period_end": period_end,
                "review_window_hours": review_window_hours,
                "hours_elapsed_until_now": round(max(0, (now - created_at).total_seconds()) / 3600, 2),
            },
            "original_capacity_snapshot": _capacity_review_view(original_capacity),
            "current_capacity_snapshot": _capacity_review_view(current_capacity),
            "capacity_delta": _capacity_delta(original_capacity, current_capacity),
            "post_decision_agent_decisions": [_decision_review_view(item) for item in later_decisions],
            "event_windows": _event_windows_review_view(event_windows),
            "operator_feedback_memory": feedback_memories,
            "review_questions": [
                "上次 Agent 说风险高，后面是否继续恶化？",
                "上次 Agent 建议补号，后面容量是否改善？",
                "上次 Agent 判断为集中封号，后面事件流是否支持？",
                "上次 Agent 是否问了上下文已经有的问题？",
                "上次 Agent 是否低估或高估风险？",
                "哪些经验应该写入长期记忆？",
            ],
            "safety_constraints": {
                "read_only": True,
                "not_a_new_refill_decision": True,
                "do_not_recalculate_current_add_count": True,
                "writes_only_agent_memory": True,
            },
        }
    )


async def _safe_current_capacity(db: AsyncIOMotorDatabase, *, pool_id: str | None) -> dict[str, Any]:
    if not pool_id:
        return {"available": False, "error": "pool_id_missing"}
    try:
        result = await read_pool_capacity(db, pool_id)
        result["available"] = True
        return result
    except Exception as exc:  # noqa: BLE001 - review can continue without current capacity.
        return {"available": False, "error": str(exc)}


async def _safe_event_windows(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    group_id: int | None,
    pool_id: str | None,
    account_type: str | None,
) -> dict[str, Any]:
    try:
        return await read_agent_event_windows(
            db,
            site_id=site_id,
            group_id=group_id,
            pool_id=pool_id,
            account_type=account_type,
            detail_24h_limit=80,
        )
    except Exception as exc:  # noqa: BLE001 - review can continue with a data gap.
        return {"data_quality": {"available": False, "warnings": [str(exc)]}}


async def _load_later_decisions(
    db: AsyncIOMotorDatabase,
    *,
    pool_id: str | None,
    created_at: datetime,
    period_end: datetime,
    exclude_decision_id: str,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"created_at": {"$gt": created_at, "$lte": period_end}}
    if pool_id:
        query["pool_id"] = pool_id
    items = [item async for item in db.agent_decisions.find(query).sort("created_at", 1).limit(20)]
    return [item for item in items if str(item.get("decision_id") or item.get("_id")) != exclude_decision_id]


async def _load_feedback_memories(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    pool_id: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {"memory_type": "operator_feedback_summary"}
    if pool_id:
        query["pool_id"] = pool_id
    elif site_id:
        query["site_id"] = site_id
    else:
        return []
    cursor = db.agent_memory_summaries.find(query).sort("period_end", -1).limit(max(1, min(limit, 20)))
    return [
        {
            "memory_id": item.get("memory_id") or item.get("_id"),
            "summary": item.get("summary"),
            "facts": item.get("facts") if isinstance(item.get("facts"), list) else [],
            "lessons": item.get("lessons") if isinstance(item.get("lessons"), list) else [],
            "period_end": item.get("period_end"),
        }
        async for item in cursor
    ]


def _review_prompt() -> str:
    return (
        "你是 AIwelink 账号池运营 Agent 的自我复盘模型。\n"
        "你的任务不是重新判断当前是否补号，也不是给当前池子重新算补号数。\n"
        "你要评估一个历史 Agent 决策在后续事实面前是否有效。\n"
        "你会收到 review_pack，里面包含原始 decision、原始容量快照、当前容量快照、后续 Agent 决策、事件窗口和人工反馈记忆。\n"
        "必须重点回答：\n"
        "1. 上次 Agent 说风险高，后面是否继续恶化。\n"
        "2. 上次 Agent 建议补号，后面容量是否改善。\n"
        "3. 上次 Agent 判断为集中封号，后面事件流是否支持。\n"
        "4. 上次 Agent 是否问了上下文已经有的问题。\n"
        "5. 上次 Agent 是否低估或高估风险。\n"
        "6. 哪些经验应该写入长期记忆。\n"
        "不要编造未给出的账号、事件、人工操作或补号结果。\n"
        "如果后续容量、事件或人工反馈不足，review_result 应为 insufficient_data，且在 data_gaps 中说明。\n"
        "如果事件窗口已经证明 401 集中发生，不要说还需要人工确认它是否集中。\n"
        "只输出一个 JSON object，不要 Markdown，不要代码块。\n"
        "JSON 必须包含字段：schema_version, review_target_decision_id, review_result, summary, what_happened_after, "
        "accuracy_assessment, lessons, memory_summary_payload, should_update_task, next_status, data_gaps。\n"
        "schema_version 必须是 agent_decision_review.v1。\n"
        "review_result 只能是 useful, too_conservative, too_aggressive, insufficient_data, wrong_interpretation。\n"
        "what_happened_after、accuracy_assessment、lessons、data_gaps 必须是字符串数组。\n"
        "memory_summary_payload 必须能写入 agent_memory_summaries，建议包含 summary、facts、patterns、lessons、risk_baselines。\n"
    )


def _validate_review(raw: dict[str, Any], *, review_pack: dict[str, Any]) -> dict[str, Any]:
    target = review_pack.get("review_target") if isinstance(review_pack.get("review_target"), dict) else {}
    result = str(raw.get("review_result") or "").strip()
    if result not in REVIEW_RESULTS:
        result = "insufficient_data"
    memory_payload = raw.get("memory_summary_payload") if isinstance(raw.get("memory_summary_payload"), dict) else {}
    return {
        "schema_version": DECISION_REVIEW_SCHEMA_VERSION,
        "review_target_decision_id": str(raw.get("review_target_decision_id") or target.get("decision_id") or ""),
        "review_result": result,
        "summary": _clean_optional_string(raw.get("summary")) or _fallback_summary_for_result(result),
        "what_happened_after": _list_of_strings(raw.get("what_happened_after")),
        "accuracy_assessment": _list_of_strings(raw.get("accuracy_assessment")),
        "lessons": _list_of_strings(raw.get("lessons")),
        "memory_summary_payload": memory_payload,
        "should_update_task": bool(raw.get("should_update_task")),
        "next_status": _clean_optional_string(raw.get("next_status")),
        "data_gaps": _list_of_strings(raw.get("data_gaps")),
        "created_at": now_utc(),
    }


def _fallback_review(review_pack: dict[str, Any], *, error: str | None = None) -> dict[str, Any]:
    target = review_pack.get("review_target") if isinstance(review_pack.get("review_target"), dict) else {}
    capacity_delta = review_pack.get("capacity_delta") if isinstance(review_pack.get("capacity_delta"), dict) else {}
    event_windows = review_pack.get("event_windows") if isinstance(review_pack.get("event_windows"), dict) else {}
    post_decisions = review_pack.get("post_decision_agent_decisions") if isinstance(review_pack.get("post_decision_agent_decisions"), list) else []
    facts = _fallback_what_happened(capacity_delta=capacity_delta, event_windows=event_windows, post_decisions=post_decisions)
    data_gaps = []
    current_capacity = review_pack.get("current_capacity_snapshot") if isinstance(review_pack.get("current_capacity_snapshot"), dict) else {}
    if not current_capacity or current_capacity.get("available") is False:
        data_gaps.append("缺少当前容量快照，无法判断补号后容量是否改善。")
    if not event_windows.get("data_quality", {}).get("available", True):
        data_gaps.append("缺少可用事件窗口，无法判断后续事件是否支持原判断。")
    if not post_decisions:
        data_gaps.append("复盘窗口内没有后续 Agent 决策，无法对风险走势做强结论。")
    if error:
        data_gaps.append(f"LLM 复盘不可用，已使用保守 fallback：{error}")
    result = "insufficient_data" if data_gaps else _fallback_result(target=target, capacity_delta=capacity_delta, event_windows=event_windows)
    summary = _fallback_summary(result=result, target=target, facts=facts)
    lessons = _fallback_lessons(result=result, target=target, event_windows=event_windows)
    return {
        "schema_version": DECISION_REVIEW_SCHEMA_VERSION,
        "review_target_decision_id": str(target.get("decision_id") or ""),
        "review_result": result,
        "summary": summary,
        "what_happened_after": facts,
        "accuracy_assessment": _fallback_accuracy(result=result, target=target),
        "lessons": lessons,
        "memory_summary_payload": {
            "summary": summary,
            "facts": facts,
            "patterns": _event_patterns(event_windows),
            "lessons": lessons,
            "risk_baselines": {
                "review_result": result,
                "original_severity": target.get("severity"),
                "original_suggested_add_count": target.get("suggested_add_count"),
            },
        },
        "should_update_task": False,
        "next_status": None,
        "data_gaps": data_gaps,
        "created_at": now_utc(),
    }


def _memory_payload_from_review(
    *,
    review: dict[str, Any],
    review_pack: dict[str, Any],
    run_id: str | None,
) -> dict[str, Any]:
    target = review_pack.get("review_target") if isinstance(review_pack.get("review_target"), dict) else {}
    memory_summary = review.get("memory_summary_payload") if isinstance(review.get("memory_summary_payload"), dict) else {}
    now = now_utc()
    return {
        "memory_id": f"decision_review:{target.get('decision_id')}" if target.get("decision_id") else None,
        "site_id": target.get("site_id"),
        "pool_id": target.get("pool_id"),
        "memory_type": "decision_review",
        "period_start": target.get("created_at") or now,
        "period_end": now,
        "summary": memory_summary.get("summary") or review.get("summary") or "",
        "facts": _list_of_strings(memory_summary.get("facts")) or _list_of_strings(review.get("what_happened_after")),
        "patterns": _list_of_strings(memory_summary.get("patterns")),
        "lessons": _list_of_strings(memory_summary.get("lessons")) or _list_of_strings(review.get("lessons")),
        "risk_baselines": memory_summary.get("risk_baselines") if isinstance(memory_summary.get("risk_baselines"), dict) else {
            "review_result": review.get("review_result"),
            "original_severity": target.get("severity"),
            "original_suggested_add_count": target.get("suggested_add_count"),
        },
        "source_run_ids": [item for item in [target.get("run_id"), run_id] if item],
        "source_decision_ids": [target.get("decision_id")] if target.get("decision_id") else [],
        "metadata": {
            "generator": "stage5_reviewer.v2",
            "schema_version": DECISION_REVIEW_SCHEMA_VERSION,
            "review_result": review.get("review_result"),
        },
    }


def _capacity_review_view(capacity: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(capacity, dict) or not capacity:
        return {}
    return {
        "available": capacity.get("available", True),
        "error": capacity.get("error"),
        "active_account_count": capacity.get("active_account_count"),
        "reserve_account_count": capacity.get("reserve_account_count"),
        "available_accounts": capacity.get("available_accounts"),
        "current_speed_days": capacity.get("current_speed_days") or capacity.get("recent_24h_runway_days"),
        "recent_24h_cost": capacity.get("recent_24h_cost"),
        "estimated_recent_24h_consumed_accounts": capacity.get("estimated_recent_24h_consumed_accounts"),
        "recent_day_five_hour_peak_multiple": capacity.get("recent_day_five_hour_peak_multiple") or capacity.get("recent_day_5h_peak_multiple"),
        "seven_day_five_hour_peak_multiple": capacity.get("seven_day_five_hour_peak_multiple") or capacity.get("seven_day_highest_5h_peak_multiple"),
        "burst_1h_five_hour_multiple": capacity.get("burst_1h_five_hour_multiple") or capacity.get("burst_1h_estimated_5h_multiple"),
        "burst_1h_trend_label": capacity.get("burst_1h_trend_label"),
        "burst_1h_trend_strength": capacity.get("burst_1h_trend_strength"),
        "five_hour_remaining_usd": capacity.get("five_hour_remaining_usd"),
        "seven_day_remaining_usd": capacity.get("seven_day_remaining_usd"),
        "health_status": capacity.get("health_status"),
        "capacity_calculated_at": capacity.get("capacity_calculated_at"),
        "capacity_status": build_agent_capacity_status(capacity),
        "concurrency_status": build_agent_concurrency_status(capacity),
    }


def _capacity_delta(original: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    original_view = _capacity_review_view(original)
    current_view = _capacity_review_view(current)
    fields = [
        "active_account_count",
        "reserve_account_count",
        "available_accounts",
        "current_speed_days",
        "five_hour_remaining_usd",
        "seven_day_remaining_usd",
    ]
    delta = {}
    for field in fields:
        before = _number_or_none(original_view.get(field))
        after = _number_or_none(current_view.get(field))
        if before is not None and after is not None:
            delta[field] = round(after - before, 4)
    return {
        "fields": delta,
        "capacity_improved_signals": _capacity_improved_signals(delta),
        "capacity_worsened_signals": _capacity_worsened_signals(delta),
    }


def _event_windows_review_view(event_windows: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(event_windows, dict):
        return {}
    result = {"data_quality": event_windows.get("data_quality"), "notable_patterns": event_windows.get("notable_patterns")}
    for key in ("summary_1h", "summary_6h", "summary_24h", "summary_7d"):
        summary = event_windows.get(key) if isinstance(event_windows.get(key), dict) else {}
        result[key] = {
            "window": summary.get("window"),
            "total_events": summary.get("total_events"),
            "account_count": summary.get("account_count"),
            "event_type_counts": summary.get("event_type_counts"),
            "status_transition_counts": summary.get("status_transition_counts"),
            "error_category_counts": summary.get("error_category_counts"),
            "special_events": summary.get("special_events"),
            "high_value_event_count": summary.get("high_value_event_count"),
            "busiest_day": summary.get("busiest_day"),
            "busiest_hour": summary.get("busiest_hour"),
            "clusters": summary.get("clusters"),
            "interpretation": summary.get("interpretation"),
        }
    return result


def _decision_review_view(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("decision") if isinstance(item.get("decision"), dict) else {}
    return {
        "decision_id": item.get("decision_id") or item.get("_id"),
        "created_at": item.get("created_at"),
        "severity": item.get("severity") or payload.get("severity"),
        "summary": item.get("summary") or payload.get("summary"),
        "should_add_accounts": payload.get("should_add_accounts"),
        "suggested_add_count": payload.get("suggested_add_count"),
        "should_alert": payload.get("should_alert"),
        "requires_human_confirm": item.get("requires_human_confirm") or payload.get("requires_human_confirm"),
    }


def _review_pack_summary(review_pack: dict[str, Any]) -> dict[str, Any]:
    target = review_pack.get("review_target") if isinstance(review_pack.get("review_target"), dict) else {}
    event_windows = review_pack.get("event_windows") if isinstance(review_pack.get("event_windows"), dict) else {}
    return {
        "target_decision_id": target.get("decision_id"),
        "pool_id": target.get("pool_id"),
        "original_severity": target.get("severity"),
        "original_suggested_add_count": target.get("suggested_add_count"),
        "review_window": review_pack.get("review_window"),
        "capacity_delta": review_pack.get("capacity_delta"),
        "event_summary_24h": event_windows.get("summary_24h") if isinstance(event_windows.get("summary_24h"), dict) else {},
        "post_decision_count": len(review_pack.get("post_decision_agent_decisions") or []),
    }


def _fallback_what_happened(
    *,
    capacity_delta: dict[str, Any],
    event_windows: dict[str, Any],
    post_decisions: list[dict[str, Any]],
) -> list[str]:
    facts: list[str] = []
    improved = capacity_delta.get("capacity_improved_signals") if isinstance(capacity_delta.get("capacity_improved_signals"), list) else []
    worsened = capacity_delta.get("capacity_worsened_signals") if isinstance(capacity_delta.get("capacity_worsened_signals"), list) else []
    if improved:
        facts.append("后续容量出现改善信号：" + "；".join(improved[:4]))
    if worsened:
        facts.append("后续容量出现恶化信号：" + "；".join(worsened[:4]))
    summary_24h = event_windows.get("summary_24h") if isinstance(event_windows.get("summary_24h"), dict) else {}
    if summary_24h:
        facts.append(
            f"当前 24h 事件窗口记录 {summary_24h.get('total_events', 0)} 条事件，涉及 {summary_24h.get('account_count', 0)} 个账号。"
        )
    patterns = _event_patterns(event_windows)
    facts.extend(patterns[:3])
    if post_decisions:
        severities = [str(item.get("severity") or "unknown") for item in post_decisions]
        facts.append(f"复盘窗口内还有 {len(post_decisions)} 条后续 Agent 决策，风险等级序列为 {severities}。")
    if not facts:
        facts.append("后续可用于复盘的容量、事件和决策证据不足。")
    return facts[:8]


def _fallback_result(*, target: dict[str, Any], capacity_delta: dict[str, Any], event_windows: dict[str, Any]) -> str:
    original_severity = str(target.get("severity") or "").lower()
    worsened = capacity_delta.get("capacity_worsened_signals") if isinstance(capacity_delta.get("capacity_worsened_signals"), list) else []
    improved = capacity_delta.get("capacity_improved_signals") if isinstance(capacity_delta.get("capacity_improved_signals"), list) else []
    high_event_count = _high_event_count(event_windows)
    if original_severity in {"danger", "critical", "warning"} and (worsened or high_event_count >= 3):
        return "useful"
    if original_severity in {"healthy", "watch"} and (worsened or high_event_count >= 5):
        return "too_conservative"
    if original_severity in {"danger", "critical"} and improved and high_event_count == 0:
        return "too_aggressive"
    return "insufficient_data"


def _fallback_summary(*, result: str, target: dict[str, Any], facts: list[str]) -> str:
    label = {
        "useful": "原判断整体有参考价值",
        "too_conservative": "原判断可能偏保守，低估了后续风险",
        "too_aggressive": "原判断可能偏激进，高估了后续风险",
        "wrong_interpretation": "原判断可能存在事件解释错误",
        "insufficient_data": "后续证据不足，暂不能评价原判断是否准确",
    }.get(result, "复盘结果不明确")
    return f"{label}。目标决策风险等级为 {target.get('severity') or '未知'}，建议补号数为 {target.get('suggested_add_count') if target.get('suggested_add_count') is not None else '未知'}。{facts[0] if facts else ''}"


def _fallback_accuracy(*, result: str, target: dict[str, Any]) -> list[str]:
    if result == "useful":
        return ["后续容量或事件证据与原风险判断方向一致。"]
    if result == "too_conservative":
        return ["后续证据显示风险高于原判断，原决策可能低估了风险。"]
    if result == "too_aggressive":
        return ["后续证据显示风险没有继续扩大或容量有所改善，原决策可能偏激进。"]
    return [f"原始判断 severity={target.get('severity') or '未知'}，但复盘证据不足，无法给出强结论。"]


def _fallback_lessons(*, result: str, target: dict[str, Any], event_windows: dict[str, Any]) -> list[str]:
    lessons: list[str] = []
    event_assessment = target.get("event_assessment") if isinstance(target.get("event_assessment"), dict) else {}
    if event_assessment.get("has_recent_ban_burst") and _has_burst_pattern(event_windows):
        lessons.append("当原判断指出集中封号且后续事件窗口仍显示聚类时，后续决策应继续把事件聚类作为关键证据。")
    if result == "too_conservative":
        lessons.append("遇到容量恶化和事件高价值异常同时出现时，后续判断不应只按当前静态容量保守处理。")
    if result == "too_aggressive":
        lessons.append("如果后续事件没有继续扩大且容量改善，类似场景下应降低补号动作强度，更多采用观察或人工确认。")
    if not lessons:
        lessons.append("后续需要继续积累容量变化、事件窗口和人工反馈，才能形成更稳定的复盘经验。")
    return lessons[:6]


def _event_patterns(event_windows: dict[str, Any]) -> list[str]:
    patterns: list[str] = []
    for item in event_windows.get("notable_patterns") if isinstance(event_windows.get("notable_patterns"), list) else []:
        if isinstance(item, dict) and item.get("interpretation"):
            window = item.get("window") or "未知窗口"
            patterns.append(f"{window}：{item.get('interpretation')}")
    return patterns[:8]


def _capacity_improved_signals(delta: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    if _number_or_none(delta.get("active_account_count")) and delta["active_account_count"] > 0:
        signals.append(f"active 账号增加 {delta['active_account_count']}")
    if _number_or_none(delta.get("reserve_account_count")) and delta["reserve_account_count"] > 0:
        signals.append(f"备用账号增加 {delta['reserve_account_count']}")
    if _number_or_none(delta.get("available_accounts")) and delta["available_accounts"] > 0:
        signals.append(f"可用账号增加 {delta['available_accounts']}")
    if _number_or_none(delta.get("current_speed_days")) and delta["current_speed_days"] > 0:
        signals.append(f"预计支撑天数增加 {delta['current_speed_days']}")
    return signals


def _capacity_worsened_signals(delta: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    if _number_or_none(delta.get("active_account_count")) and delta["active_account_count"] < 0:
        signals.append(f"active 账号减少 {abs(delta['active_account_count'])}")
    if _number_or_none(delta.get("reserve_account_count")) and delta["reserve_account_count"] < 0:
        signals.append(f"备用账号减少 {abs(delta['reserve_account_count'])}")
    if _number_or_none(delta.get("available_accounts")) and delta["available_accounts"] < 0:
        signals.append(f"可用账号减少 {abs(delta['available_accounts'])}")
    if _number_or_none(delta.get("current_speed_days")) and delta["current_speed_days"] < 0:
        signals.append(f"预计支撑天数减少 {abs(delta['current_speed_days'])}")
    return signals


def _high_event_count(event_windows: dict[str, Any]) -> int:
    summary_24h = event_windows.get("summary_24h") if isinstance(event_windows.get("summary_24h"), dict) else {}
    return int(_number_or_none(summary_24h.get("high_value_event_count")) or 0)


def _has_burst_pattern(event_windows: dict[str, Any]) -> bool:
    for item in event_windows.get("notable_patterns") if isinstance(event_windows.get("notable_patterns"), list) else []:
        if isinstance(item, dict) and item.get("cluster_type") == "time_burst":
            return True
    return False


def _fallback_summary_for_result(result: str) -> str:
    if result == "useful":
        return "复盘认为原判断整体有效。"
    if result == "too_conservative":
        return "复盘认为原判断可能偏保守。"
    if result == "too_aggressive":
        return "复盘认为原判断可能偏激进。"
    if result == "wrong_interpretation":
        return "复盘认为原判断可能存在解释错误。"
    return "复盘证据不足，暂不能评价原判断是否准确。"


def _group_id_from_capacity_or_pool(capacity: dict[str, Any], pool_id: str | None) -> int | None:
    value = _int_or_none(capacity.get("group_id"))
    if value is not None:
        return value
    parts = str(pool_id or "").split(":")
    if len(parts) == 3:
        return _int_or_none(parts[2])
    return None


def _account_type_from_capacity_or_decision(capacity: dict[str, Any], decision: dict[str, Any]) -> str | None:
    pool = capacity.get("pool") if isinstance(capacity.get("pool"), dict) else {}
    value = _clean_optional_string(pool.get("account_type"))
    if value:
        return value
    capacity_snapshot = decision.get("capacity_snapshot") if isinstance(decision.get("capacity_snapshot"), dict) else {}
    pool = capacity_snapshot.get("pool") if isinstance(capacity_snapshot.get("pool"), dict) else {}
    return _clean_optional_string(pool.get("account_type"))


def _datetime_or_none(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except ValueError:
            return None
    return None


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if item is not None and str(item).strip()]


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
