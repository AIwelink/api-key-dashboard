from __future__ import annotations

import time
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.capabilities import invoke_agent_capability
from app.modules.agent.context_pack import build_agent_context_pack
from app.modules.agent.intent_router import (
    INTENT_AGENT_USAGE_QUESTION,
    INTENT_DECISION_REVIEW,
    INTENT_OPERATOR_FEEDBACK,
    INTENT_POOL_DATA_QUESTION,
    INTENT_POOL_OPERATION_DECISION,
    INTENT_SMALLTALK_OR_OUT_OF_SCOPE,
    INTENT_UNAUTHORIZED_ACTION_REQUEST,
)
from app.modules.agent.llm_client import invoke_agent_level1_json
from app.modules.agent.long_term_memory import save_agent_memory_summary
from app.modules.agent.memory import create_agent_step, fail_agent_step, finish_agent_step
from app.modules.agent.tasks import append_agent_task_step_link, create_or_update_agent_task
from app.utils import now_utc, serialize_doc


STEP_SCHEMA_VERSION = "agent_step.v1"

STEP_OBSERVE_CONTEXT = "observe_context"
STEP_ANSWER_DIRECTLY = "answer_directly"
STEP_BUILD_DECISION = "build_decision"
STEP_READ_MORE = "read_more"
STEP_WRITE_MEMORY = "write_memory"
STEP_REVIEW_PREVIOUS_DECISION = "review_previous_decision"
STEP_UPDATE_TASK_STATE = "update_task_state"
STEP_ASK_HUMAN = "ask_human"
STEP_STOP = "stop"

ALLOWED_STEP_TYPES = {
    STEP_OBSERVE_CONTEXT,
    STEP_ANSWER_DIRECTLY,
    STEP_BUILD_DECISION,
    STEP_READ_MORE,
    STEP_WRITE_MEMORY,
    STEP_REVIEW_PREVIOUS_DECISION,
    STEP_UPDATE_TASK_STATE,
    STEP_ASK_HUMAN,
    STEP_STOP,
}

READ_ONLY_CAPABILITIES = {
    "api_pool_status.get",
    "account_probe.get",
}

MAX_STAGE5_STEPS = 4
MAX_STAGE5_LLM_CALLS = 4
MAX_STAGE5_RUNTIME_SECONDS = 60
MAX_STAGE5_CAPABILITY_CALLS = 3


async def run_agent_step_loop(
    db: AsyncIOMotorDatabase,
    *,
    run: dict[str, Any],
    intent: dict[str, Any],
    task: dict[str, Any] | None,
    user_message: str | None,
    pool_id: str | None,
    conversation_id: str,
    actor: dict[str, Any] | None = None,
    max_steps: int = MAX_STAGE5_STEPS,
) -> dict[str, Any]:
    """Run a bounded observe-think-act loop inside one Agent run."""

    started = time.monotonic()
    run_id = str(run.get("run_id") or "")
    task_id = str(task.get("task_id")) if isinstance(task, dict) and task.get("task_id") else None
    routed_intent = str(intent.get("intent") or "")
    target_pool_id = str(intent.get("target_pool_id") or pool_id or "").strip() or None
    max_steps = max(1, min(int(max_steps or MAX_STAGE5_STEPS), MAX_STAGE5_STEPS))

    steps: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    llm_calls = 0
    capability_calls = 0
    context_pack: dict[str, Any] | None = None

    route_output = _step_output(
        step_type=STEP_OBSERVE_CONTEXT,
        thought_summary="已完成意图路由，进入 Agent 控制中枢。",
        final_decision_ready=False,
        continue_loop=_intent_needs_more_loop(routed_intent),
        stop_reason=None if _intent_needs_more_loop(routed_intent) else routed_intent,
        extra={
            "routed_intent": routed_intent,
            "confidence": intent.get("confidence"),
            "requires_pool_context": intent.get("requires_pool_context"),
            "should_create_decision": intent.get("should_create_decision"),
            "target_pool_id": target_pool_id,
        },
    )
    route_step = await _record_step(
        db,
        run_id=run_id,
        conversation_id=conversation_id,
        task_id=task_id,
        step_index=1,
        step_type=STEP_OBSERVE_CONTEXT,
        intent=routed_intent,
        input_summary={"phase": "route_intent", "trigger": run.get("trigger"), "pool_id": target_pool_id},
        output_summary=route_output,
    )
    steps.append(route_step)
    observations.append({"step_type": STEP_OBSERVE_CONTEXT, "summary": route_output})

    if routed_intent in {
        INTENT_AGENT_USAGE_QUESTION,
        INTENT_SMALLTALK_OR_OUT_OF_SCOPE,
        INTENT_UNAUTHORIZED_ACTION_REQUEST,
    }:
        direct_reply = str(intent.get("direct_reply") or _default_direct_reply(routed_intent))
        answer_output = _step_output(
            step_type=STEP_ANSWER_DIRECTLY,
            thought_summary="该请求不需要账号池上下文，直接回复并停止。",
            final_decision_ready=False,
            continue_loop=False,
            stop_reason=routed_intent,
            extra={"reply": direct_reply, "direct_reply": direct_reply},
        )
        steps.append(
            await _record_step(
                db,
                run_id=run_id,
                conversation_id=conversation_id,
                task_id=task_id,
                step_index=2,
                step_type=STEP_ANSWER_DIRECTLY,
                intent=routed_intent,
                input_summary={"reply_directly": True},
                output_summary=answer_output,
            )
        )
        return _loop_result(
            status="finished",
            mode="direct_response",
            intent=intent,
            task=task,
            steps=steps,
            observations=observations,
            direct_reply=direct_reply,
            context_pack=None,
            final_step=answer_output,
            llm_calls=llm_calls,
            capability_calls=capability_calls,
        )

    if routed_intent == INTENT_OPERATOR_FEEDBACK:
        direct_reply = str(intent.get("direct_reply") or _default_direct_reply(routed_intent))
        memory_payload = {
            "site_id": None,
            "pool_id": target_pool_id,
            "memory_type": "operator_feedback_summary",
            "summary": user_message,
            "facts": [user_message] if user_message else [],
        }
        memory_output = _step_output(
            step_type=STEP_WRITE_MEMORY,
            thought_summary="该消息是人工反馈，应沉淀为长期记忆，不默认触发补号决策。",
            memory_to_write=memory_payload,
            final_decision_ready=False,
            continue_loop=False,
            stop_reason="operator_feedback_recorded",
            extra={"reply": direct_reply, "direct_reply": direct_reply},
        )
        memory_output["memory_write_result"] = await _write_memory_from_step(
            db,
            payload=memory_payload,
            context_pack=None,
            run_id=run_id,
            actor=actor,
        )
        steps.append(
            await _record_step(
                db,
                run_id=run_id,
                conversation_id=conversation_id,
                task_id=task_id,
                step_index=2,
                step_type=STEP_WRITE_MEMORY,
                intent=routed_intent,
                input_summary={"has_feedback": bool(user_message), "pool_id": target_pool_id},
                output_summary=memory_output,
            )
        )
        return _loop_result(
            status="finished",
            mode="direct_response",
            intent=intent,
            task=task,
            steps=steps,
            observations=observations,
            direct_reply=direct_reply,
            context_pack=None,
            final_step=memory_output,
            llm_calls=llm_calls,
            capability_calls=capability_calls,
        )

    if intent.get("requires_pool_context"):
        if not target_pool_id:
            reply = "我需要先确定目标账号池，才能继续查询数据或形成运营判断。"
            ask_output = _step_output(
                step_type=STEP_ASK_HUMAN,
                thought_summary="需要账号池上下文，但当前没有解析到目标池。",
                final_decision_ready=False,
                continue_loop=False,
                stop_reason="missing_target_pool",
                requires_human_confirm=True,
                extra={"reply": reply, "direct_reply": reply},
            )
            steps.append(
                await _record_step(
                    db,
                    run_id=run_id,
                    conversation_id=conversation_id,
                    task_id=task_id,
                    step_index=2,
                    step_type=STEP_ASK_HUMAN,
                    intent=routed_intent,
                    input_summary={"pool_id": target_pool_id},
                    output_summary=ask_output,
                )
            )
            return _loop_result(
                status="finished",
                mode="ask_human",
                intent=intent,
                task=task,
                steps=steps,
                observations=observations,
                direct_reply=reply,
                context_pack=None,
                final_step=ask_output,
                llm_calls=llm_calls,
                capability_calls=capability_calls,
            )

        context_pack = await build_agent_context_pack(
            db,
            trigger=str(run.get("trigger") or ""),
            pool_id=target_pool_id,
            user_message=user_message,
            conversation_id=conversation_id,
            actor=actor,
        )
        observe_output = _step_output(
            step_type=STEP_OBSERVE_CONTEXT,
            thought_summary="已构建 Context Pack v2，获得容量、事件窗口、探测摘要和长期记忆。",
            final_decision_ready=False,
            continue_loop=True,
            extra={
                "schema_version": context_pack.get("schema_version"),
                "data_quality": context_pack.get("data_quality"),
                "target_pool": context_pack.get("target_pool"),
            },
        )
        observe_step = await _record_step(
            db,
            run_id=run_id,
            conversation_id=conversation_id,
            task_id=task_id,
            step_index=len(steps) + 1,
            step_type=STEP_OBSERVE_CONTEXT,
            intent=routed_intent,
            input_summary={"pool_id": target_pool_id, "needs_context_pack": True},
            output_summary=observe_output,
        )
        steps.append(observe_step)
        observations.append({"step_type": STEP_OBSERVE_CONTEXT, "summary": observe_output})

    if routed_intent == INTENT_DECISION_REVIEW:
        review_output = _step_output(
            step_type=STEP_REVIEW_PREVIOUS_DECISION,
            thought_summary="用户要求复盘历史判断，交给 reviewer 读取历史 decision 并沉淀 decision_review 记忆。",
            final_decision_ready=False,
            continue_loop=False,
            stop_reason="review_requested",
        )
        steps.append(
            await _record_step(
                db,
                run_id=run_id,
                conversation_id=conversation_id,
                task_id=task_id,
                step_index=len(steps) + 1,
                step_type=STEP_REVIEW_PREVIOUS_DECISION,
                intent=routed_intent,
                input_summary={"pool_id": target_pool_id},
                output_summary=review_output,
            )
        )
        return _loop_result(
            status="finished",
            mode="review_previous_decision",
            intent=intent,
            task=task,
            steps=steps,
            observations=observations,
            direct_reply=None,
            context_pack=context_pack,
            final_step=review_output,
            llm_calls=llm_calls,
            capability_calls=capability_calls,
        )

    final_step: dict[str, Any] | None = None
    while len(steps) < max_steps and _runtime_ok(started):
        llm_calls += 1
        if llm_calls > MAX_STAGE5_LLM_CALLS:
            final_step = _step_output(
                step_type=STEP_STOP,
                thought_summary="已达到 LLM 调用上限，停止本轮循环。",
                final_decision_ready=False,
                continue_loop=False,
                stop_reason="max_llm_calls",
            )
            break

        try:
            controller_output, llm_summary = await _call_controller_step(
                db,
                intent=intent,
                task=task,
                user_message=user_message,
                context_pack=context_pack,
                observations=observations,
                steps=steps,
                limits={
                    "max_steps": max_steps,
                    "max_llm_calls": MAX_STAGE5_LLM_CALLS,
                    "max_runtime_seconds": MAX_STAGE5_RUNTIME_SECONDS,
                    "max_capability_calls": MAX_STAGE5_CAPABILITY_CALLS,
                },
            )
        except Exception as exc:  # noqa: BLE001 - the controller loop must degrade safely.
            controller_output = _fallback_step_output_for_intent(routed_intent, error=str(exc))
            llm_summary = {"enabled": False, "configured": False, "error": str(exc), "framework": "fallback"}

        controller_output = _validate_step_output(controller_output, intent=intent)
        step_type = str(controller_output.get("step_type") or STEP_STOP)
        step_capability_calls: list[dict[str, Any]] = []

        if step_type == STEP_READ_MORE:
            capability_name = _requested_capability_name(controller_output)
            if capability_name not in READ_ONLY_CAPABILITIES or capability_calls >= MAX_STAGE5_CAPABILITY_CALLS:
                controller_output = _step_output(
                    step_type=STEP_STOP,
                    thought_summary="请求的补充能力不可用，或已经达到能力调用上限。",
                    final_decision_ready=False,
                    continue_loop=False,
                    stop_reason="capability_not_allowed_or_limited",
                    extra={"requested_capability": capability_name},
                )
                step_type = STEP_STOP
            else:
                capability_calls += 1
                capability_result = await _execute_read_only_capability(
                    db,
                    capability_name=capability_name,
                    target_pool_id=target_pool_id,
                    context_pack=context_pack,
                )
                controller_output["capability_result_summary"] = _capability_result_summary(capability_name, capability_result)
                step_capability_calls.append(
                    {
                        "name": capability_name,
                        "kind": "read_only",
                        "status": "success",
                        "summary": controller_output["capability_result_summary"],
                    }
                )
                observations.append({"step_type": STEP_READ_MORE, "summary": controller_output})

        if step_type == STEP_WRITE_MEMORY and controller_output.get("memory_to_write"):
            memory_result = await _write_memory_from_step(
                db,
                payload=controller_output.get("memory_to_write"),
                context_pack=context_pack,
                run_id=run_id,
                actor=actor,
            )
            controller_output["memory_write_result"] = memory_result

        if step_type == STEP_UPDATE_TASK_STATE and controller_output.get("task_update"):
            task_result = await _update_task_from_step(
                db,
                task=task,
                payload=controller_output.get("task_update"),
                context_pack=context_pack,
                run_id=run_id,
                conversation_id=conversation_id,
                actor=actor,
            )
            controller_output["task_update_result"] = task_result
            if task_result:
                task = task_result
                task_id = str(task_result.get("task_id")) if task_result.get("task_id") else task_id

        steps.append(
            await _record_step(
                db,
                run_id=run_id,
                conversation_id=conversation_id,
                task_id=task_id,
                step_index=len(steps) + 1,
                step_type=step_type,
                intent=routed_intent,
                input_summary={
                    "llm_step": True,
                    "has_context_pack": context_pack is not None,
                    "observation_count": len(observations),
                },
                output_summary=controller_output,
                llm=llm_summary,
                capability_calls=step_capability_calls,
            )
        )
        observations.append({"step_type": step_type, "summary": controller_output})
        final_step = controller_output

        if _should_stop(controller_output):
            break

    if final_step is None:
        final_step = _step_output(
            step_type=STEP_STOP,
            thought_summary="循环没有得到有效下一步，安全停止。",
            final_decision_ready=False,
            continue_loop=False,
            stop_reason="no_valid_step",
        )

    mode = _mode_from_final_step(final_step, routed_intent)
    return _loop_result(
        status="finished" if not final_step.get("continue_loop") else "stopped_by_limit",
        mode=mode,
        intent=intent,
        task=task,
        steps=steps,
        observations=observations,
        direct_reply=_direct_reply_from_step(final_step),
        context_pack=context_pack,
        final_step=final_step,
        llm_calls=llm_calls,
        capability_calls=capability_calls,
    )


async def _call_controller_step(
    db: AsyncIOMotorDatabase,
    *,
    intent: dict[str, Any],
    task: dict[str, Any] | None,
    user_message: str | None,
    context_pack: dict[str, Any] | None,
    observations: list[dict[str, Any]],
    steps: list[dict[str, Any]],
    limits: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    result = await invoke_agent_level1_json(
        db,
        system_prompt=_controller_step_prompt(),
        payload={
            "task": "agent_controller_step",
            "expected_schema": STEP_SCHEMA_VERSION,
            "intent": intent,
            "task_state": _task_view(task),
            "user_message": user_message,
            "context_pack_summary": _context_pack_view(context_pack),
            "observations": observations[-6:],
            "executed_steps": [_step_view(step) for step in steps],
            "allowed_step_types": sorted(ALLOWED_STEP_TYPES),
            "allowed_read_more_capabilities": sorted(READ_ONLY_CAPABILITIES),
            "limits": limits,
            "safety_boundary": {
                "read_only": True,
                "forbidden": [
                    "write_business_tables",
                    "refresh_sub2api",
                    "start_account_probe",
                    "push_accounts",
                    "buy_accounts",
                    "delete_accounts",
                    "send_dingtalk",
                ],
            },
        },
    )
    data = result.get("data") if isinstance(result.get("data"), dict) else {}
    return data, {
        "enabled": result.get("enabled"),
        "configured": result.get("configured"),
        "level": result.get("level"),
        "model": result.get("model"),
        "source": result.get("source"),
        "framework": result.get("framework"),
        "raw_text": result.get("raw_text"),
    }


async def _record_step(
    db: AsyncIOMotorDatabase,
    *,
    run_id: str,
    conversation_id: str,
    task_id: str | None,
    step_index: int,
    step_type: str,
    intent: str,
    input_summary: dict[str, Any],
    output_summary: dict[str, Any],
    llm: dict[str, Any] | None = None,
    capability_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    step = await create_agent_step(
        db,
        run_id=run_id,
        conversation_id=conversation_id,
        task_id=task_id,
        step_index=step_index,
        step_type=step_type,
        intent=intent,
        input_summary=input_summary,
    )
    try:
        finished = await finish_agent_step(
            db,
            step_id=str(step["step_id"]),
            output_summary=output_summary,
            llm=llm,
            capability_calls=capability_calls,
        )
        result = finished or step
        await append_agent_task_step_link(
            db,
            task_id=task_id,
            step_id=str(result.get("step_id") or step.get("step_id")),
            run_id=run_id,
        )
        return result
    except Exception as exc:  # noqa: BLE001 - step failure should be persisted.
        failed = await fail_agent_step(db, step_id=str(step["step_id"]), error=str(exc), output_summary=output_summary)
        return failed or step


async def _execute_read_only_capability(
    db: AsyncIOMotorDatabase,
    *,
    capability_name: str,
    target_pool_id: str | None,
    context_pack: dict[str, Any] | None,
) -> dict[str, Any]:
    if capability_name == "api_pool_status.get":
        return await invoke_agent_capability(db, capability_name, {"pool_id": target_pool_id})
    if capability_name == "account_probe.get":
        target_pool = context_pack.get("target_pool") if isinstance(context_pack, dict) and isinstance(context_pack.get("target_pool"), dict) else {}
        return await invoke_agent_capability(
            db,
            capability_name,
            {
                "site_id": str(target_pool.get("site_id") or ""),
                "group_id": int(target_pool.get("group_id") or 0),
                "account_type": str(target_pool.get("account_type") or ""),
            },
        )
    raise ValueError(f"Unsupported read-only capability: {capability_name}")


async def _write_memory_from_step(
    db: AsyncIOMotorDatabase,
    *,
    payload: Any,
    context_pack: dict[str, Any] | None,
    run_id: str,
    actor: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    target_pool = context_pack.get("target_pool") if isinstance(context_pack, dict) and isinstance(context_pack.get("target_pool"), dict) else {}
    memory_payload = {
        "site_id": payload.get("site_id") or target_pool.get("site_id"),
        "pool_id": payload.get("pool_id") or target_pool.get("pool_id"),
        "memory_type": payload.get("memory_type") or "operator_feedback_summary",
        "period_start": payload.get("period_start") or now_utc(),
        "period_end": payload.get("period_end") or now_utc(),
        "summary": payload.get("summary") or "",
        "facts": payload.get("facts") if isinstance(payload.get("facts"), list) else [],
        "patterns": payload.get("patterns") if isinstance(payload.get("patterns"), list) else [],
        "lessons": payload.get("lessons") if isinstance(payload.get("lessons"), list) else [],
        "risk_baselines": payload.get("risk_baselines") if isinstance(payload.get("risk_baselines"), dict) else {},
        "source_run_ids": [run_id],
    }
    return await save_agent_memory_summary(db, payload=memory_payload, actor=actor)


async def _update_task_from_step(
    db: AsyncIOMotorDatabase,
    *,
    task: dict[str, Any] | None,
    payload: Any,
    context_pack: dict[str, Any] | None,
    run_id: str,
    conversation_id: str,
    actor: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(payload, dict):
        return None
    target_pool = context_pack.get("target_pool") if isinstance(context_pack, dict) and isinstance(context_pack.get("target_pool"), dict) else {}
    step_result = {
        **payload,
        "task_update": payload,
        "requires_human_confirm": bool(payload.get("requires_human_confirm")),
        "should_alert": bool(payload.get("should_alert")),
        "thought_summary": payload.get("reason") or payload.get("summary") or payload.get("title"),
    }
    return await create_or_update_agent_task(
        db,
        task=task,
        decision={},
        step_result=step_result,
        run_id=run_id,
        site_id=str(target_pool.get("site_id")) if target_pool.get("site_id") is not None else None,
        pool_id=str(target_pool.get("pool_id")) if target_pool.get("pool_id") is not None else None,
        conversation_id=conversation_id,
        actor=actor,
    )


def _validate_step_output(raw: dict[str, Any], *, intent: dict[str, Any]) -> dict[str, Any]:
    step_type = str(raw.get("step_type") or "").strip()
    if step_type not in ALLOWED_STEP_TYPES:
        step_type = _default_step_type_for_intent(str(intent.get("intent") or ""))
    output = _step_output(
        step_type=step_type,
        thought_summary=str(raw.get("thought_summary") or raw.get("summary") or "Agent controller selected the next step.").strip(),
        needs_context_pack=bool(raw.get("needs_context_pack")),
        requested_capability=raw.get("requested_capability"),
        memory_to_write=raw.get("memory_to_write") if isinstance(raw.get("memory_to_write"), dict) else None,
        task_update=raw.get("task_update") if isinstance(raw.get("task_update"), dict) else None,
        final_decision_ready=bool(raw.get("final_decision_ready")),
        requires_human_confirm=bool(raw.get("requires_human_confirm")),
        continue_loop=bool(raw.get("continue_loop")),
        stop_reason=str(raw.get("stop_reason") or "").strip() or None,
    )
    if step_type in {STEP_BUILD_DECISION, STEP_ANSWER_DIRECTLY, STEP_REVIEW_PREVIOUS_DECISION, STEP_ASK_HUMAN, STEP_STOP}:
        output["continue_loop"] = False
    if step_type == STEP_BUILD_DECISION:
        output["final_decision_ready"] = True
        output["stop_reason"] = output.get("stop_reason") or "decision_ready"
    if step_type == STEP_ANSWER_DIRECTLY:
        output["direct_reply"] = str(raw.get("direct_reply") or "").strip() or None
        output["stop_reason"] = output.get("stop_reason") or "direct_answer_ready"
    return output


def _step_output(
    *,
    step_type: str,
    thought_summary: str,
    needs_context_pack: bool = False,
    requested_capability: Any = None,
    memory_to_write: dict[str, Any] | None = None,
    task_update: dict[str, Any] | None = None,
    final_decision_ready: bool = False,
    requires_human_confirm: bool = False,
    continue_loop: bool = False,
    stop_reason: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    output = {
        "schema_version": STEP_SCHEMA_VERSION,
        "step_type": step_type,
        "thought_summary": thought_summary[:1000],
        "needs_context_pack": bool(needs_context_pack),
        "requested_capability": requested_capability,
        "memory_to_write": memory_to_write,
        "task_update": task_update,
        "final_decision_ready": bool(final_decision_ready),
        "requires_human_confirm": bool(requires_human_confirm),
        "continue_loop": bool(continue_loop),
        "stop_reason": stop_reason,
    }
    if extra:
        output.update(extra)
    return output


def _loop_result(
    *,
    status: str,
    mode: str,
    intent: dict[str, Any],
    task: dict[str, Any] | None,
    steps: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    direct_reply: str | None,
    context_pack: dict[str, Any] | None,
    final_step: dict[str, Any],
    llm_calls: int,
    capability_calls: int,
) -> dict[str, Any]:
    return serialize_doc(
        {
            "status": status,
            "mode": mode,
            "intent": intent,
            "task": task,
            "steps": steps,
            "observations": observations,
            "direct_reply": direct_reply,
            "context_pack": context_pack,
            "final_step": final_step,
            "continue_loop": bool(final_step.get("continue_loop")),
            "limits": {
                "max_steps": MAX_STAGE5_STEPS,
                "max_llm_calls": MAX_STAGE5_LLM_CALLS,
                "max_runtime_seconds": MAX_STAGE5_RUNTIME_SECONDS,
                "max_capability_calls": MAX_STAGE5_CAPABILITY_CALLS,
            },
            "usage": {
                "step_count": len(steps),
                "llm_calls": llm_calls,
                "capability_calls": capability_calls,
            },
            "created_at": now_utc(),
        }
    )


def _controller_step_prompt() -> str:
    return (
        "你是账号池运营 Agent 的控制中枢 step planner。你只决定下一步，不直接执行高风险动作。\n"
        "你必须只输出一个 JSON object，不要 Markdown，不要代码块。\n"
        "输出 schema_version 必须是 agent_step.v1。\n"
        "step_type 只能是 observe_context, answer_directly, build_decision, read_more, write_memory, "
        "review_previous_decision, update_task_state, ask_human, stop。\n"
        "你会收到 intent、task 状态、Context Pack 摘要、已执行 steps 和 observations。\n"
        "如果 intent 是 pool_operation_decision 且已有 Context Pack，通常输出 build_decision。\n"
        "如果 intent 是 pool_data_question，通常输出 answer_directly 或 stop，不要生成补号决策。\n"
        "如果 intent 是 decision_review，输出 review_previous_decision。\n"
        "如果缺少目标池或关键数据无法补齐，输出 ask_human 或 stop。\n"
        "如果需要补充只读信息，只能请求 api_pool_status.get 或 account_probe.get。\n"
        "禁止请求刷新 sub2api、启动探测、推号、买号、删号、发送正式钉钉通知或写账号池业务表。\n"
        "thought_summary 只写简短摘要，不要输出隐藏推理链。\n"
        "必须包含字段：schema_version, step_type, thought_summary, needs_context_pack, requested_capability, "
        "memory_to_write, task_update, final_decision_ready, requires_human_confirm, continue_loop, stop_reason。\n"
    )


def _context_pack_view(context_pack: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(context_pack, dict):
        return {}
    event_windows = context_pack.get("event_windows") if isinstance(context_pack.get("event_windows"), dict) else {}
    return {
        "schema_version": context_pack.get("schema_version"),
        "target_pool": context_pack.get("target_pool"),
        "capacity_status": context_pack.get("capacity_status"),
        "concurrency_status": context_pack.get("concurrency_status"),
        "system_capacity_assessment": context_pack.get("system_capacity_assessment"),
        "capacity": context_pack.get("capacity"),
        "operational_facts": context_pack.get("operational_facts"),
        "event_windows_summary": {
            "summary_1h": event_windows.get("summary_1h"),
            "summary_24h": event_windows.get("summary_24h"),
            "summary_7d": event_windows.get("summary_7d"),
            "notable_patterns": event_windows.get("notable_patterns"),
        },
        "probe": context_pack.get("probe"),
        "long_term_memory": context_pack.get("long_term_memory"),
        "data_quality": context_pack.get("data_quality"),
    }


def _task_view(task: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(task, dict):
        return {}
    return {
        "task_id": task.get("task_id"),
        "task_type": task.get("task_type"),
        "status": task.get("status"),
        "severity": task.get("severity"),
        "summary": task.get("summary"),
        "requires_human_confirm": task.get("requires_human_confirm"),
    }


def _step_view(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_index": step.get("step_index"),
        "step_type": step.get("step_type"),
        "status": step.get("status"),
        "intent": step.get("intent"),
        "output_summary": step.get("output_summary"),
    }


def _intent_needs_more_loop(intent: str) -> bool:
    return intent in {
        INTENT_POOL_OPERATION_DECISION,
        INTENT_POOL_DATA_QUESTION,
        INTENT_DECISION_REVIEW,
        INTENT_OPERATOR_FEEDBACK,
    }


def _runtime_ok(started: float) -> bool:
    return (time.monotonic() - started) < MAX_STAGE5_RUNTIME_SECONDS


def _should_stop(output: dict[str, Any]) -> bool:
    if output.get("requires_human_confirm"):
        return True
    if output.get("final_decision_ready"):
        return True
    if not output.get("continue_loop"):
        return True
    return False


def _mode_from_final_step(output: dict[str, Any], intent: str) -> str:
    step_type = str(output.get("step_type") or "")
    if step_type == STEP_BUILD_DECISION:
        return "operation_decision"
    if step_type == STEP_REVIEW_PREVIOUS_DECISION:
        return "review_previous_decision"
    if step_type == STEP_ASK_HUMAN:
        return "ask_human"
    if step_type == STEP_ANSWER_DIRECTLY or intent == INTENT_POOL_DATA_QUESTION:
        return "direct_response"
    return "controller_loop"


def _direct_reply_from_step(output: dict[str, Any]) -> str | None:
    value = output.get("direct_reply") or output.get("reply")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _default_step_type_for_intent(intent: str) -> str:
    if intent == INTENT_POOL_OPERATION_DECISION:
        return STEP_BUILD_DECISION
    if intent == INTENT_POOL_DATA_QUESTION:
        return STEP_ANSWER_DIRECTLY
    if intent == INTENT_DECISION_REVIEW:
        return STEP_REVIEW_PREVIOUS_DECISION
    if intent == INTENT_OPERATOR_FEEDBACK:
        return STEP_WRITE_MEMORY
    return STEP_STOP


def _fallback_step_output_for_intent(intent: str, *, error: str) -> dict[str, Any]:
    if intent == INTENT_POOL_OPERATION_DECISION:
        return _step_output(
            step_type=STEP_BUILD_DECISION,
            thought_summary="Controller Step LLM 不可用，回退到已有 Context Pack 主决策流程。",
            final_decision_ready=True,
            continue_loop=False,
            stop_reason="controller_step_llm_unavailable_use_primary_decision",
            extra={"error": error},
        )
    if intent == INTENT_POOL_DATA_QUESTION:
        return _step_output(
            step_type=STEP_ANSWER_DIRECTLY,
            thought_summary="Controller Step LLM 不可用，回退到后端 Context Pack 数据摘要回答。",
            final_decision_ready=False,
            continue_loop=False,
            stop_reason="controller_step_llm_unavailable_use_data_summary",
            extra={"error": error},
        )
    if intent == INTENT_DECISION_REVIEW:
        return _step_output(
            step_type=STEP_REVIEW_PREVIOUS_DECISION,
            thought_summary="Controller Step LLM 不可用，回退到 reviewer 复盘入口。",
            final_decision_ready=False,
            continue_loop=False,
            stop_reason="controller_step_llm_unavailable_use_reviewer",
            extra={"error": error},
        )
    return _step_output(
        step_type=STEP_STOP,
        thought_summary="Controller Step LLM 不可用，安全停止本轮循环。",
        final_decision_ready=False,
        continue_loop=False,
        stop_reason="controller_step_llm_unavailable",
        extra={"error": error},
    )


def _requested_capability_name(output: dict[str, Any]) -> str | None:
    value = output.get("requested_capability")
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return str(value.get("name") or value.get("capability") or "").strip() or None
    return None


def _capability_result_summary(capability_name: str, result: dict[str, Any]) -> dict[str, Any]:
    if capability_name == "api_pool_status.get":
        return {
            "active_account_count": result.get("active_account_count"),
            "reserve_account_count": result.get("reserve_account_count"),
            "available_accounts": result.get("available_accounts"),
            "current_speed_days": result.get("current_speed_days"),
        }
    if capability_name == "account_probe.get":
        return {
            "probe_fresh": result.get("probe_fresh"),
            "detected_401_1h": result.get("detected_401_1h"),
            "detected_401_24h": result.get("detected_401_24h"),
            "detected_401_7d": result.get("detected_401_7d"),
        }
    return {"keys": list(result.keys())[:10]}


def _default_direct_reply(intent: str) -> str:
    if intent == INTENT_OPERATOR_FEEDBACK:
        return "收到，我会把这条反馈作为后续判断的参考。"
    if intent == INTENT_UNAUTHORIZED_ACTION_REQUEST:
        return "当前 Agent 只能给出建议或草稿，不能直接执行推号、买号、删号、刷新缓存或发送正式通知。"
    if intent == INTENT_AGENT_USAGE_QUESTION:
        return "我负责账号池运营分析，可以判断风险、解释容量和事件、给出补号建议、沉淀反馈，并在只读边界内辅助复盘。"
    return "我主要负责账号池运营分析。你可以问我某个池子的风险、容量、事件、复盘或补号建议。"
