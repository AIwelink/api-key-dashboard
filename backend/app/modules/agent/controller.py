from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.context_pack import build_agent_context_pack
from app.modules.agent.decision_core import decide_with_context_pack
from app.modules.agent.intent_router import (
    INTENT_DECISION_REVIEW,
    INTENT_OPERATOR_FEEDBACK,
    INTENT_POOL_DATA_QUESTION,
    INTENT_POOL_OPERATION_DECISION,
    route_agent_intent,
)
from app.modules.agent.long_term_memory import build_memory_candidates_from_report
from app.modules.agent.memory import (
    append_agent_message,
    create_agent_run,
    fail_agent_run,
    finish_agent_run,
    save_agent_decision,
)
from app.modules.agent.reviewer import review_agent_decision
from app.modules.agent.step_loop import run_agent_step_loop
from app.modules.agent.tasks import append_agent_task_feedback, create_or_update_agent_task, get_agent_task, resolve_agent_task
from app.modules.agent.triggers import is_scheduler_trigger
from app.utils import now_utc, serialize_doc


async def run_agent_controller(
    db: AsyncIOMotorDatabase,
    *,
    trigger: str,
    user_message: str | None,
    pool_id: str | None,
    conversation_id: str | None = None,
    task_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stage 5 Agent controller entrypoint."""

    normalized_message = (user_message or "").strip()
    intent = await route_agent_intent(
        db,
        user_message=normalized_message or None,
        trigger=trigger,
        pool_id=pool_id,
        conversation_id=conversation_id,
        actor=actor,
    )
    target_pool_id = str(intent.get("target_pool_id") or pool_id or "").strip() or None
    run = await create_agent_run(
        db,
        trigger=trigger,
        actor=actor,
        pool_id=target_pool_id,
        user_message=normalized_message or None,
        conversation_id=conversation_id,
        metadata={"controller": "stage5", "intent": intent, **(metadata or {})},
    )
    run_id = str(run["run_id"])
    resolved_conversation_id = str(run["conversation_id"])

    try:
        if trigger == "manual_chat" and normalized_message:
            await append_agent_message(
                db,
                conversation_id=resolved_conversation_id,
                role="user",
                content=normalized_message,
                run_id=run_id,
                pool_id=target_pool_id,
                actor=actor,
                metadata={"intent": intent.get("intent"), "controller": "stage5"},
            )

        task = await get_agent_task(db, task_id=task_id) if task_id else None
        if task is None:
            task_resolution_conversation_id = None if is_scheduler_trigger(trigger) or intent.get("intent") == INTENT_OPERATOR_FEEDBACK else resolved_conversation_id
            task = await resolve_agent_task(
                db,
                intent=intent,
                site_id=None,
                pool_id=target_pool_id,
                conversation_id=task_resolution_conversation_id,
            )
        loop_result = await run_agent_step_loop(
            db,
            run=run,
            intent=intent,
            task=task,
            user_message=normalized_message or None,
            pool_id=target_pool_id,
            conversation_id=resolved_conversation_id,
            actor=actor,
        )

        routed_intent = str(intent.get("intent") or "")
        if routed_intent == INTENT_POOL_OPERATION_DECISION:
            if loop_result.get("mode") == "ask_human":
                report = await _direct_response_report(
                    db,
                    run=run,
                    intent=intent,
                    loop_result=loop_result,
                    pool_id=target_pool_id,
                    message=str(loop_result.get("direct_reply") or "当前上下文不足，需要人工补充信息后再做账号池运营决策。"),
                    severity="watch",
                )
            else:
                report = await _run_operation_decision(
                    db,
                    run_id=run_id,
                    conversation_id=resolved_conversation_id,
                    trigger=trigger,
                    user_message=normalized_message or None,
                    pool_id=target_pool_id,
                    actor=actor,
                    intent=intent,
                    loop_result=loop_result,
                    task=task,
                )
        elif routed_intent == INTENT_POOL_DATA_QUESTION:
            report = await _run_pool_data_question(
                db,
                run_id=run_id,
                conversation_id=resolved_conversation_id,
                trigger=trigger,
                user_message=normalized_message or None,
                pool_id=target_pool_id,
                actor=actor,
                intent=intent,
                loop_result=loop_result,
            )
        elif routed_intent == INTENT_DECISION_REVIEW:
            report = await _run_decision_review(
                db,
                run=run,
                intent=intent,
                loop_result=loop_result,
                pool_id=target_pool_id,
                actor=actor,
            )
        else:
            report = await _direct_response_report(
                db,
                run=run,
                intent=intent,
                loop_result=loop_result,
                pool_id=target_pool_id,
                message=str(loop_result.get("direct_reply") or intent.get("direct_reply") or "收到。"),
                severity="healthy",
            )

        report["run_id"] = run_id
        report["conversation_id"] = resolved_conversation_id
        report["controller_version"] = "stage5.v1"
        await finish_agent_run(db, run_id=run_id, report=report, decision_id=report.get("decision_id"))
        return serialize_doc(report)
    except Exception as exc:
        await fail_agent_run(db, run_id=run_id, error=str(exc) or exc.__class__.__name__, metadata={"intent": intent})
        raise


async def _run_operation_decision(
    db: AsyncIOMotorDatabase,
    *,
    run_id: str,
    conversation_id: str,
    trigger: str,
    user_message: str | None,
    pool_id: str | None,
    actor: dict[str, Any] | None,
    intent: dict[str, Any],
    loop_result: dict[str, Any],
    task: dict[str, Any] | None,
) -> dict[str, Any]:
    if not pool_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to resolve target Agent pool")
    context_pack = loop_result.get("context_pack") if isinstance(loop_result.get("context_pack"), dict) else None
    if context_pack is None:
        context_pack = await build_agent_context_pack(
            db,
            trigger=trigger,
            pool_id=pool_id,
            user_message=user_message,
            conversation_id=conversation_id,
            actor=actor,
        )

    decision_result = await decide_with_context_pack(db, context_pack=context_pack)
    decision = decision_result["decision"]
    capacity = context_pack.get("capacity") if isinstance(context_pack.get("capacity"), dict) else {}
    probe = context_pack.get("probe") if isinstance(context_pack.get("probe"), dict) else {}
    pool = _pool_from_context_pack(context_pack)
    report = serialize_doc(
        {
            "report_id": None,
            "read_only": True,
            "trigger": trigger,
            "user_message": user_message,
            "pool": pool,
            "severity": decision["severity"],
            "headline": decision["headline"],
            "decision": decision,
            "reasons": decision.get("reasons", []),
            "suggested_actions": decision.get("suggested_actions", []),
            "capacity": capacity,
            "probe": probe,
            "llm": decision_result["llm"],
            "validator": decision_result.get("validator", {}),
            "agent": {
                "mode": "stage5_controller",
                "planned_by": "agent_controller",
                "intent": intent.get("intent"),
                "intent_router": intent,
                "decision_mode": "llm_primary",
                "context_pack": _context_pack_trace_summary(context_pack),
                "step_loop": _step_loop_summary(loop_result),
                "task": _task_summary(task),
            },
            "chat": {
                "intent": intent.get("intent"),
                "matched_pool_id": pool.get("id"),
                "matched_pool_name": pool.get("name"),
            },
            "context_pack_version": context_pack.get("schema_version"),
            "created_at": now_utc(),
        }
    )
    decision_doc = await save_agent_decision(db, run_id=run_id, conversation_id=conversation_id, report=report, actor=actor)
    decision_id = str(decision_doc["decision_id"])
    report["decision_id"] = decision_id
    report["decision"]["decision_id"] = decision_id

    agent_meta = report.get("agent") if isinstance(report.get("agent"), dict) else {}
    agent_meta["memory_candidates"] = await build_memory_candidates_from_report(db, report=report)
    updated_task = await create_or_update_agent_task(
        db,
        task=task,
        decision=report["decision"],
        step_result=_task_step_result_from_loop(loop_result=loop_result, decision=report["decision"], pool=pool),
        run_id=run_id,
        site_id=str(pool.get("site_id")) if pool.get("site_id") is not None else None,
        pool_id=str(pool.get("id")) if pool.get("id") is not None else None,
        conversation_id=conversation_id,
        actor=actor,
    )
    if updated_task:
        agent_meta["task"] = _task_summary(updated_task)
    report["agent"] = agent_meta

    await append_agent_message(
        db,
        conversation_id=conversation_id,
        role="assistant",
        content=_assistant_message_from_report(report),
        run_id=run_id,
        pool_id=str(pool.get("id")) if pool.get("id") is not None else pool_id,
        site_id=str(pool.get("site_id")) if pool.get("site_id") is not None else None,
        actor=None,
        metadata={
            "decision_id": decision_id,
            "severity": report.get("severity"),
            "decision_mode": "stage5_controller",
            "intent": intent.get("intent"),
        },
    )
    return report


async def _run_pool_data_question(
    db: AsyncIOMotorDatabase,
    *,
    run_id: str,
    conversation_id: str,
    trigger: str,
    user_message: str | None,
    pool_id: str | None,
    actor: dict[str, Any] | None,
    intent: dict[str, Any],
    loop_result: dict[str, Any],
) -> dict[str, Any]:
    if not pool_id:
        return await _direct_response_report(
            db,
            run={"run_id": run_id, "conversation_id": conversation_id, "trigger": trigger, "user_message": user_message},
            intent=intent,
            loop_result=loop_result,
            pool_id=pool_id,
            message="我识别到这是账号池数据查询，但还没有确定目标账号池。请先选择一个池，或在问题里说明要查询哪一个池。",
            severity="watch",
        )

    context_pack = loop_result.get("context_pack") if isinstance(loop_result.get("context_pack"), dict) else None
    if context_pack is None:
        context_pack = await build_agent_context_pack(
            db,
            trigger=trigger,
            pool_id=pool_id,
            user_message=user_message,
            conversation_id=conversation_id,
            actor=actor,
        )
    capacity = context_pack.get("capacity") if isinstance(context_pack.get("capacity"), dict) else {}
    probe = context_pack.get("probe") if isinstance(context_pack.get("probe"), dict) else {}
    event_windows = context_pack.get("event_windows") if isinstance(context_pack.get("event_windows"), dict) else {}
    message = _pool_data_message(capacity=capacity, probe=probe, event_windows=event_windows)
    report = await _direct_response_report(
        db,
        run={"run_id": run_id, "conversation_id": conversation_id, "trigger": trigger, "user_message": user_message},
        intent=intent,
        loop_result=loop_result,
        pool_id=pool_id,
        message=message,
        severity="watch",
    )
    report["capacity"] = capacity
    report["probe"] = probe
    agent_meta = report.get("agent") if isinstance(report.get("agent"), dict) else {}
    agent_meta["context_pack"] = _context_pack_trace_summary(context_pack)
    agent_meta["data_answer_mode"] = "context_pack_summary"
    report["agent"] = agent_meta
    report["context_pack_version"] = context_pack.get("schema_version")
    return serialize_doc(report)


async def _run_decision_review(
    db: AsyncIOMotorDatabase,
    *,
    run: dict[str, Any],
    intent: dict[str, Any],
    loop_result: dict[str, Any],
    pool_id: str | None,
    actor: dict[str, Any] | None,
) -> dict[str, Any]:
    decision = await _latest_decision_for_review(db, pool_id=pool_id)
    if not decision:
        return await _direct_response_report(
            db,
            run=run,
            intent=intent,
            loop_result=loop_result,
            pool_id=pool_id,
            message="我识别到这是复盘请求，但目前没有找到可复盘的历史 Agent 决策。",
            severity="watch",
        )
    review = await review_agent_decision(
        db,
        decision_id=str(decision.get("decision_id") or decision.get("_id")),
        run_id=str(run.get("run_id")),
        actor=actor,
    )
    message = str(review.get("summary") or "已完成历史决策复盘，并写入 decision_review 长期记忆。")
    report = await _direct_response_report(
        db,
        run=run,
        intent=intent,
        loop_result=loop_result,
        pool_id=pool_id,
        message=message,
        severity="watch",
    )
    agent_meta = report.get("agent") if isinstance(report.get("agent"), dict) else {}
    agent_meta["decision_review"] = review
    report["agent"] = agent_meta
    return serialize_doc(report)


async def _direct_response_report(
    db: AsyncIOMotorDatabase,
    *,
    run: dict[str, Any],
    intent: dict[str, Any],
    loop_result: dict[str, Any],
    pool_id: str | None,
    message: str,
    severity: str,
) -> dict[str, Any]:
    run_id = str(run["run_id"])
    conversation_id = str(run["conversation_id"])
    decision = {
        "decision_type": "agent_direct_response",
        "schema_version": "agent_decision.v1",
        "severity": severity,
        "headline": "Agent 回复",
        "summary": message,
        "operator_message": message,
        "should_add_accounts": False,
        "suggested_add_count": 0,
        "confidence": intent.get("confidence") or "medium",
        "main_reasons": [intent.get("reason")] if intent.get("reason") else [],
        "risk_factors": [],
        "data_gaps": [],
        "should_alert": False,
        "alert_channels": [],
        "requires_human_confirm": False,
        "manual_review_required": False,
        "recommended_actions": [],
        "suggested_actions": [],
        "next_observation_focus": [],
        "follow_up_questions": [],
        "continue_decision_loop": False,
    }
    report = {
        "report_id": None,
        "read_only": True,
        "trigger": run.get("trigger"),
        "user_message": run.get("user_message"),
        "pool": _minimal_pool(pool_id),
        "severity": severity,
        "headline": "Agent 回复",
        "decision": decision,
        "reasons": decision["main_reasons"],
        "suggested_actions": [],
        "capacity": {},
        "probe": {},
        "llm": {
            "enabled": False,
            "configured": False,
            "level": "controller",
            "framework": "stage5_controller",
            "message": message,
            "operator_message": message,
            "summary": message,
        },
        "agent": {
            "mode": "stage5_controller",
            "planned_by": "intent_router",
            "intent": intent.get("intent"),
            "intent_router": intent,
            "decision_mode": "direct_response",
            "step_loop": _step_loop_summary(loop_result),
            "is_operational_decision": False,
        },
        "chat": {"intent": intent.get("intent")},
        "created_at": now_utc(),
    }
    if intent.get("intent") == INTENT_OPERATOR_FEEDBACK:
        feedback_task = await _append_operator_feedback_to_task(
            db,
            loop_result=loop_result,
            message=message,
            run_id=run_id,
            conversation_id=conversation_id,
        )
        if feedback_task:
            report["agent"]["task"] = _task_summary(feedback_task)
            report["agent"]["operator_feedback_task_result"] = feedback_task.get("feedback_result")
    if loop_result.get("mode") == "ask_human" and pool_id:
        updated_task = await create_or_update_agent_task(
            db,
            task=loop_result.get("task") if isinstance(loop_result.get("task"), dict) else None,
            decision={},
            step_result=_ask_human_task_step_result(loop_result=loop_result, message=message),
            run_id=run_id,
            site_id=None,
            pool_id=pool_id,
            conversation_id=conversation_id,
            actor=None,
        )
        if updated_task:
            report["agent"]["task"] = _task_summary(updated_task)
    await append_agent_message(
        db,
        conversation_id=conversation_id,
        role="assistant",
        content=message,
        run_id=run_id,
        pool_id=pool_id,
        actor=None,
        metadata={"intent": intent.get("intent"), "decision_mode": "direct_response"},
    )
    return serialize_doc(report)


def _assistant_message_from_report(report: dict[str, Any]) -> str:
    llm = report.get("llm") if isinstance(report.get("llm"), dict) else {}
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    for value in (
        llm.get("message"),
        llm.get("operator_message"),
        decision.get("operator_message"),
        decision.get("summary"),
        report.get("headline"),
    ):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return "Agent analysis finished."


def _pool_from_context_pack(context_pack: dict[str, Any]) -> dict[str, Any]:
    target_pool = context_pack.get("target_pool") if isinstance(context_pack.get("target_pool"), dict) else {}
    return {
        "id": target_pool.get("pool_id"),
        "name": target_pool.get("name"),
        "account_type": target_pool.get("account_type"),
        "site_id": target_pool.get("site_id"),
        "active_group_id": target_pool.get("group_id"),
        "source": target_pool.get("source"),
        "remote_status": target_pool.get("remote_status"),
        "updated_at": target_pool.get("updated_at"),
    }


def _minimal_pool(pool_id: str | None) -> dict[str, Any]:
    return {
        "id": pool_id,
        "name": pool_id or "Agent",
        "account_type": "other",
        "site_id": None,
        "active_group_id": 0,
        "source": "stage5_controller",
    }


async def _latest_decision_for_review(db: AsyncIOMotorDatabase, *, pool_id: str | None) -> dict[str, Any] | None:
    query = {"pool_id": pool_id} if pool_id else {}
    return await db.agent_decisions.find_one(query, sort=[("created_at", -1)])


def _pool_data_message(*, capacity: dict[str, Any], probe: dict[str, Any], event_windows: dict[str, Any]) -> str:
    parts: list[str] = []
    if capacity:
        active = capacity.get("active_account_count")
        reserve = capacity.get("reserve_account_count")
        available = capacity.get("available_accounts")
        current_speed_days = capacity.get("recent_24h_runway_days") or capacity.get("current_speed_days")
        recent_peak = capacity.get("recent_day_5h_peak_multiple") or capacity.get("recent_day_five_hour_peak_multiple")
        burst = capacity.get("burst_1h_estimated_5h_multiple") or capacity.get("burst_1h_five_hour_multiple")
        parts.append(
            "容量摘要："
            f"active={_display_value(active)}，备用={_display_value(reserve)}，可用账号={_display_value(available)}，"
            f"按最近 24h 速度预计可支撑 {_display_value(current_speed_days)} 天，"
            f"最近一天 5h 峰值覆盖倍数={_display_value(recent_peak)}x，"
            f"突发 1h 折算 5h 覆盖倍数={_display_value(burst)}x。"
        )
    else:
        parts.append("容量摘要：当前没有可用的容量上下文。")

    if probe:
        parts.append(
            "探测摘要："
            f"最近 1h 401={_display_value(probe.get('detected_401_1h'))}，"
            f"最近 24h 401={_display_value(probe.get('detected_401_24h'))}，"
            f"最近 7d 401={_display_value(probe.get('detected_401_7d'))}，"
            f"重复邮箱告警={_display_value(probe.get('duplicate_email_alert_count'))}。"
        )

    summary_1h = event_windows.get("summary_1h") if isinstance(event_windows.get("summary_1h"), dict) else {}
    summary_24h = event_windows.get("summary_24h") if isinstance(event_windows.get("summary_24h"), dict) else {}
    summary_7d = event_windows.get("summary_7d") if isinstance(event_windows.get("summary_7d"), dict) else {}
    if event_windows:
        parts.append(
            "事件摘要："
            f"最近 1h 事件={_display_value(summary_1h.get('total_events'))}，"
            f"最近 24h 事件={_display_value(summary_24h.get('total_events'))}，"
            f"最近 7d 事件={_display_value(summary_7d.get('total_events'))}。"
        )
        notable = event_windows.get("notable_patterns") if isinstance(event_windows.get("notable_patterns"), list) else []
        if notable:
            interpretations = [
                str(item.get("interpretation") or item.get("message") or "").strip()
                for item in notable
                if isinstance(item, dict)
            ]
            interpretations = [item for item in interpretations if item]
            if interpretations:
                parts.append(f"主要事件模式：{interpretations[0]}")

    parts.append("这次按数据查询处理，没有生成新的补号决策。")
    return "\n".join(parts)


def _display_value(value: Any) -> str:
    if value is None or value == "":
        return "未知"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _task_step_result_from_loop(*, loop_result: dict[str, Any], decision: dict[str, Any], pool: dict[str, Any] | None = None) -> dict[str, Any]:
    final_step = loop_result.get("final_step") if isinstance(loop_result.get("final_step"), dict) else {}
    task_update = final_step.get("task_update") if isinstance(final_step.get("task_update"), dict) else {}
    result = {
        **task_update,
        "task_update": task_update,
        "pool": pool if isinstance(pool, dict) else {},
        "should_alert": bool(task_update.get("should_alert") or decision.get("should_alert")),
        "requires_human_confirm": bool(task_update.get("requires_human_confirm") or decision.get("requires_human_confirm")),
        "thought_summary": task_update.get("reason")
        or task_update.get("summary")
        or final_step.get("thought_summary")
        or decision.get("summary"),
    }
    if final_step.get("task_update_warnings"):
        result["task_update_warnings"] = final_step.get("task_update_warnings")
    return result


def _ask_human_task_step_result(*, loop_result: dict[str, Any], message: str) -> dict[str, Any]:
    final_step = loop_result.get("final_step") if isinstance(loop_result.get("final_step"), dict) else {}
    task_update = final_step.get("task_update") if isinstance(final_step.get("task_update"), dict) else {}
    return {
        **task_update,
        "task_update": {**task_update, "next_status": "waiting_human"},
        "next_status": "waiting_human",
        "requires_human_confirm": True,
        "human_confirm_questions": task_update.get("human_confirm_questions") or [message],
        "thought_summary": task_update.get("reason") or task_update.get("summary") or final_step.get("thought_summary") or message,
    }


async def _append_operator_feedback_to_task(
    db: AsyncIOMotorDatabase,
    *,
    loop_result: dict[str, Any],
    message: str,
    run_id: str,
    conversation_id: str,
) -> dict[str, Any] | None:
    task = loop_result.get("task") if isinstance(loop_result.get("task"), dict) else None
    task_id = str(task.get("task_id") or "") if isinstance(task, dict) else ""
    if not task_id:
        return None
    final_step = loop_result.get("final_step") if isinstance(loop_result.get("final_step"), dict) else {}
    memory_result = final_step.get("memory_write_result") if isinstance(final_step.get("memory_write_result"), dict) else {}
    return await append_agent_task_feedback(
        db,
        task_id=task_id,
        feedback=message,
        run_id=run_id,
        conversation_id=conversation_id,
        write_memory=False,
        memory_id=str(memory_result.get("memory_id") or "") or None,
    )


def _context_pack_trace_summary(context_pack: dict[str, Any]) -> dict[str, Any]:
    target_pool = context_pack.get("target_pool") if isinstance(context_pack.get("target_pool"), dict) else {}
    data_quality = context_pack.get("data_quality") if isinstance(context_pack.get("data_quality"), dict) else {}
    return {
        "schema_version": context_pack.get("schema_version"),
        "target_pool": {
            "pool_id": target_pool.get("pool_id"),
            "site_id": target_pool.get("site_id"),
            "group_id": target_pool.get("group_id"),
            "name": target_pool.get("name"),
            "account_type": target_pool.get("account_type"),
        },
        "data_quality": {
            "capacity_available": data_quality.get("capacity_available"),
            "probe_available": data_quality.get("probe_available"),
            "event_windows_available": data_quality.get("event_windows_available"),
            "long_term_memory_available": data_quality.get("long_term_memory_available"),
            "warnings": data_quality.get("warnings") if isinstance(data_quality.get("warnings"), list) else [],
        },
    }


def _step_loop_summary(loop_result: dict[str, Any]) -> dict[str, Any]:
    steps = loop_result.get("steps") if isinstance(loop_result.get("steps"), list) else []
    return {
        "status": loop_result.get("status"),
        "mode": loop_result.get("mode"),
        "continue_loop": loop_result.get("continue_loop"),
        "usage": loop_result.get("usage") if isinstance(loop_result.get("usage"), dict) else {},
        "limits": loop_result.get("limits") if isinstance(loop_result.get("limits"), dict) else {},
        "final_step": loop_result.get("final_step") if isinstance(loop_result.get("final_step"), dict) else {},
        "step_count": len(steps),
        "steps": [
            {
                "step_id": step.get("step_id"),
                "step_index": step.get("step_index"),
                "step_type": step.get("step_type"),
                "intent": step.get("intent"),
            }
            for step in steps
            if isinstance(step, dict)
        ],
    }


def _task_summary(task: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(task, dict):
        return None
    latest_state = _latest_task_state_history(task)
    return {
        "task_id": task.get("task_id"),
        "task_type": task.get("task_type"),
        "status": task.get("status"),
        "severity": task.get("severity"),
        "title": task.get("title"),
        "requires_human_confirm": task.get("requires_human_confirm"),
        "alert_status": task.get("alert_status"),
        "next_check_at": task.get("next_check_at"),
        "review_after": task.get("review_after"),
        "current_decision_id": task.get("current_decision_id"),
        "latest_state_reason": latest_state.get("reason"),
        "latest_state_changed_at": latest_state.get("changed_at"),
        "updated_at": task.get("updated_at"),
    }


def _latest_task_state_history(task: dict[str, Any]) -> dict[str, Any]:
    history = task.get("state_history") if isinstance(task.get("state_history"), list) else []
    for item in reversed(history):
        if isinstance(item, dict):
            return item
    return {}
