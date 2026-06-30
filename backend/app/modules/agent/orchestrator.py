from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.capacity import list_agent_pools
from app.modules.agent.capabilities import invoke_agent_capability
from app.modules.agent.context_pack import build_agent_context_pack
from app.modules.agent.decision_core import decide_with_context_pack
from app.modules.agent.llm import explain_level1_analysis, plan_level1_capabilities
from app.modules.agent.memory import (
    append_agent_message,
    create_agent_run,
    fail_agent_run,
    finish_agent_run,
    save_agent_decision,
)
from app.utils import now_utc, serialize_doc


ALLOWED_CAPABILITIES = {
    "api_pool_status.get",
    "account_probe.get",
    "refill_decision.calculate",
}

DEFAULT_CAPABILITY_PLAN = [
    {"capability": "api_pool_status.get", "reason": "读取账号池现有容量缓存"},
    {"capability": "account_probe.get", "reason": "读取账号探测摘要"},
    {"capability": "refill_decision.calculate", "reason": "计算补号和预警建议"},
]


async def run_agent_analysis(
    db: AsyncIOMotorDatabase,
    *,
    user_message: str | None,
    pool_id: str | None,
    trigger: str,
    allow_planning: bool = True,
    actor: dict[str, Any] | None = None,
    conversation_id: str | None = None,
) -> dict[str, Any]:
    normalized_message = (user_message or "").strip()
    run = await create_agent_run(
        db,
        trigger=trigger,
        actor=actor,
        pool_id=pool_id,
        user_message=normalized_message or None,
        conversation_id=conversation_id,
    )
    run_id = str(run["run_id"])
    conversation_id = str(run["conversation_id"])
    try:
        if trigger == "manual_chat" and normalized_message:
            await append_agent_message(
                db,
                conversation_id=conversation_id,
                role="user",
                content=normalized_message,
                run_id=run_id,
                pool_id=pool_id,
                actor=actor,
            )
        context_pack = await build_agent_context_pack(
            db,
            trigger=trigger,
            pool_id=pool_id,
            user_message=normalized_message or None,
            conversation_id=conversation_id,
            actor=actor,
        )
        target_pool = context_pack.get("target_pool") if isinstance(context_pack.get("target_pool"), dict) else {}
        resolved_pool_id = str(target_pool.get("pool_id") or pool_id or "").strip() or None
        pools_response = await list_agent_pools(db)
        pools = [pool for pool in pools_response.get("items", []) if isinstance(pool, dict)]
        if not pools:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No analyzable API pools found")

        try:
            report = await _execute_context_pack_decision(
                db,
                context_pack=context_pack,
                trigger=trigger,
                user_message=normalized_message or None,
            )
        except Exception as exc:  # noqa: BLE001 - keep the Agent usable with the deterministic fallback.
            planning = await _build_plan(
                db,
                pools=pools,
                user_message=normalized_message,
                pool_id=resolved_pool_id,
                trigger=trigger,
                allow_planning=allow_planning,
            )
            fallback_planning = _fallback_plan(
                pools=pools,
                pool_id=resolved_pool_id or planning.get("target_pool_id"),
                user_message=normalized_message,
                trigger=trigger,
                reason=f"LLM primary decision failed: {exc}",
                planner=planning.get("planner") if isinstance(planning.get("planner"), dict) else planning,
            )
            report = await _execute_plan(
                db,
                pools=pools,
                user_message=normalized_message or None,
                trigger=trigger,
                planning=fallback_planning,
            )
        report["run_id"] = run_id
        report["conversation_id"] = conversation_id
        report["context_pack_version"] = context_pack.get("schema_version")
        agent_meta = report.get("agent") if isinstance(report.get("agent"), dict) else {}
        agent_meta["context_pack"] = _context_pack_trace_summary(context_pack)
        report["agent"] = agent_meta
        decision_doc = await save_agent_decision(db, run_id=run_id, conversation_id=conversation_id, report=report, actor=actor)
        decision_id = str(decision_doc["decision_id"])
        report["decision_id"] = decision_id
        pool = report.get("pool") if isinstance(report.get("pool"), dict) else {}
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
                "decision_mode": report.get("decision_mode") or (report.get("agent") or {}).get("decision_mode"),
                "validator_status": (report.get("validator") or {}).get("status"),
            },
        )
        await finish_agent_run(db, run_id=run_id, report=report, decision_id=decision_id)
        return report
    except Exception as exc:
        await fail_agent_run(db, run_id=run_id, error=str(exc) or exc.__class__.__name__)
        raise


async def _build_plan(
    db: AsyncIOMotorDatabase,
    *,
    pools: list[dict[str, Any]],
    user_message: str,
    pool_id: str | None,
    trigger: str,
    allow_planning: bool,
) -> dict[str, Any]:
    if allow_planning and user_message:
        plan = await plan_level1_capabilities(
            db=db,
            user_message=user_message,
            pools=pools,
            selected_pool_id=pool_id,
            trigger=trigger,
        )
        normalized = _normalize_plan(plan, pools=pools, selected_pool_id=pool_id, user_message=user_message)
        if normalized is not None:
            return normalized
        return _fallback_plan(
            pools=pools,
            pool_id=pool_id,
            user_message=user_message,
            trigger=trigger,
            reason=str(plan.get("error") or "Level 1 planner returned an invalid plan"),
            planner=plan,
        )
    return _fallback_plan(pools=pools, pool_id=pool_id, user_message=user_message, trigger=trigger)


async def _execute_context_pack_decision(
    db: AsyncIOMotorDatabase,
    *,
    context_pack: dict[str, Any],
    trigger: str,
    user_message: str | None,
) -> dict[str, Any]:
    decision_result = await decide_with_context_pack(db, context_pack=context_pack)
    decision = decision_result["decision"]
    target_pool = context_pack.get("target_pool") if isinstance(context_pack.get("target_pool"), dict) else {}
    capacity = context_pack.get("capacity") if isinstance(context_pack.get("capacity"), dict) else {}
    probe = context_pack.get("probe") if isinstance(context_pack.get("probe"), dict) else {}
    pool = _pool_from_context_pack(context_pack)
    created_at = now_utc()
    return serialize_doc(
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
            "agent": {
                "mode": "llm_primary",
                "planned_by": "context_pack",
                "intent": "make_pool_operation_decision",
                "thought": "后端主动构建 Context Pack，Level 1 模型基于完整上下文输出主决策。",
                "decision_mode": "llm_primary",
                "validator": decision_result.get("validator", {}),
                "capability_plan": [],
                "capability_trace": [
                    {
                        "index": 1,
                        "capability": "context_pack.build",
                        "reason": "构建账号池运营决策上下文",
                        "status": "success",
                        "summary": {
                            "schema_version": context_pack.get("schema_version"),
                            "pool_id": target_pool.get("pool_id"),
                            "capacity_available": _context_data_quality(context_pack).get("capacity_available"),
                            "probe_available": _context_data_quality(context_pack).get("probe_available"),
                        },
                    },
                    {
                        "index": 2,
                        "capability": "level1.llm_primary_decision",
                        "reason": "由 Level 1 模型根据 Context Pack 生成运营主决策",
                        "status": "success",
                        "summary": {
                            "severity": decision.get("severity"),
                            "suggested_add_count": decision.get("suggested_add_count"),
                            "should_alert": decision.get("should_alert"),
                            "requires_human_confirm": decision.get("requires_human_confirm"),
                            "data_gaps": decision.get("data_gaps", []),
                        },
                    },
                ],
            },
            "chat": {
                "intent": "make_pool_operation_decision",
                "matched_pool_id": pool.get("id"),
                "matched_pool_name": pool.get("name"),
            },
            "decision_mode": "llm_primary",
            "validator": decision_result.get("validator", {}),
            "created_at": created_at,
        }
    )


async def _execute_plan(
    db: AsyncIOMotorDatabase,
    *,
    pools: list[dict[str, Any]],
    user_message: str | None,
    trigger: str,
    planning: dict[str, Any],
) -> dict[str, Any]:
    target_pool = _resolve_pool(str(planning.get("target_pool_id") or ""), pools)
    if target_pool is None:
        target_pool = _match_pool(user_message or "", pools)
    if target_pool is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to match an API pool from this message")

    context: dict[str, Any] = {"pool": target_pool}
    trace: list[dict[str, Any]] = []
    capability_plan = _sanitize_capability_plan(planning.get("capability_plan"))

    for index, step in enumerate(capability_plan, start=1):
        capability = step["capability"]
        arguments = _arguments_for_capability(capability, context)
        trace_step = {
            "index": index,
            "capability": capability,
            "reason": step.get("reason"),
            "arguments": _safe_arguments(arguments),
            "status": "running",
        }
        trace.append(trace_step)
        result = await invoke_agent_capability(db, capability, arguments)
        _store_capability_result(context, capability, result)
        trace_step["status"] = "success"
        trace_step["summary"] = _capability_result_summary(capability, result)

    capacity = context.get("capacity") if isinstance(context.get("capacity"), dict) else {}
    pool = context.get("pool") if isinstance(context.get("pool"), dict) else target_pool
    probe = context.get("probe") if isinstance(context.get("probe"), dict) else {}
    decision = context.get("decision") if isinstance(context.get("decision"), dict) else None
    if decision is None:
        decision = await invoke_agent_capability(
            db,
            "refill_decision.calculate",
            {"pool": pool, "capacity": capacity, "probe": probe},
        )
        trace.append(
            {
                "index": len(trace) + 1,
                "capability": "refill_decision.calculate",
                "reason": "补齐最终补号和预警判断",
                "arguments": {"pool": "context.pool", "capacity": "context.capacity", "probe": "context.probe"},
                "status": "success",
                "summary": _capability_result_summary("refill_decision.calculate", decision),
            }
        )

    llm = await explain_level1_analysis(
        db=db,
        pool=pool,
        capacity=capacity,
        probe=probe,
        decision=decision,
        user_message=user_message,
    )
    created_at = now_utc()
    return serialize_doc(
        {
            "report_id": None,
            "read_only": True,
            "trigger": trigger,
            "user_message": user_message,
            "pool": pool,
            "severity": decision["severity"],
            "headline": decision["headline"],
            "decision": decision,
            "reasons": decision["reasons"],
            "suggested_actions": decision["suggested_actions"],
            "capacity": capacity,
            "probe": probe,
            "llm": llm,
            "agent": {
                "mode": "react_read_only" if planning.get("planned_by") == "level1" else "deterministic_fallback",
                "planned_by": planning.get("planned_by"),
                "intent": planning.get("intent"),
                "thought": planning.get("thought"),
                "fallback_reason": planning.get("fallback_reason"),
                "capability_plan": capability_plan,
                "capability_trace": trace,
            },
            "chat": {
                "intent": planning.get("intent"),
                "matched_pool_id": pool.get("id"),
                "matched_pool_name": pool.get("name"),
            },
            "created_at": created_at,
        }
    )


def _normalize_plan(
    plan: dict[str, Any],
    *,
    pools: list[dict[str, Any]],
    selected_pool_id: str | None,
    user_message: str,
) -> dict[str, Any] | None:
    if not plan.get("configured") or plan.get("error"):
        return None
    target_pool_id = str(plan.get("target_pool_id") or selected_pool_id or "").strip()
    if not _resolve_pool(target_pool_id, pools):
        matched = _match_pool(user_message, pools)
        target_pool_id = str(matched.get("id")) if matched else target_pool_id
    if not _resolve_pool(target_pool_id, pools):
        return None

    capability_plan = _sanitize_capability_plan(plan.get("capability_plan"))
    if not capability_plan:
        return None
    return {
        "planned_by": "level1",
        "intent": str(plan.get("intent") or "analyze_pool"),
        "thought": str(plan.get("thought") or ""),
        "target_pool_id": target_pool_id,
        "capability_plan": capability_plan,
        "fallback_allowed": bool(plan.get("fallback_allowed", True)),
        "planner": plan,
    }


def _fallback_plan(
    *,
    pools: list[dict[str, Any]],
    pool_id: str | None,
    user_message: str,
    trigger: str,
    reason: str | None = None,
    planner: dict[str, Any] | None = None,
) -> dict[str, Any]:
    pool = _resolve_pool(str(pool_id or ""), pools) or _match_pool(user_message, pools)
    if pool is None and len(pools) == 1:
        pool = pools[0]
    if pool is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to match an API pool from this message")
    return {
        "planned_by": "deterministic_fallback",
        "intent": "analyze_pool" if trigger != "manual_analyze" else "manual_pool_analysis",
        "thought": "使用固定只读分析流程。",
        "target_pool_id": str(pool.get("id")),
        "capability_plan": DEFAULT_CAPABILITY_PLAN,
        "fallback_allowed": True,
        "fallback_reason": reason,
        "planner": planner,
    }


def _sanitize_capability_plan(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return DEFAULT_CAPABILITY_PLAN
    sanitized: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, str):
            capability = item
            reason = ""
        elif isinstance(item, dict):
            capability = str(item.get("capability") or item.get("name") or "").strip()
            reason = str(item.get("reason") or item.get("thought") or "").strip()
        else:
            continue
        if capability not in ALLOWED_CAPABILITIES:
            continue
        if capability not in [step["capability"] for step in sanitized]:
            sanitized.append({"capability": capability, "reason": reason})

    names = [step["capability"] for step in sanitized]
    if "refill_decision.calculate" in names:
        if "api_pool_status.get" not in names:
            sanitized.insert(0, {"capability": "api_pool_status.get", "reason": "补号判断需要容量数据"})
        if "account_probe.get" not in names:
            insert_at = 1 if sanitized and sanitized[0]["capability"] == "api_pool_status.get" else 0
            sanitized.insert(insert_at, {"capability": "account_probe.get", "reason": "补号判断需要探测数据"})
    elif "account_probe.get" in names and "api_pool_status.get" not in names:
        sanitized.insert(0, {"capability": "api_pool_status.get", "reason": "探测摘要需要先解析站点和分组"})
    return sanitized or DEFAULT_CAPABILITY_PLAN


def _arguments_for_capability(capability: str, context: dict[str, Any]) -> dict[str, Any]:
    pool = context.get("pool") if isinstance(context.get("pool"), dict) else {}
    if capability == "api_pool_status.get":
        return {"pool_id": str(pool.get("id"))}
    if capability == "account_probe.get":
        capacity = context.get("capacity") if isinstance(context.get("capacity"), dict) else {}
        source = capacity or pool
        return {
            "site_id": str(source.get("site_id") or "default"),
            "group_id": int(source.get("group_id") or source.get("active_group_id")),
            "account_type": str(pool.get("account_type") or ""),
        }
    if capability == "refill_decision.calculate":
        return {
            "pool": pool,
            "capacity": context.get("capacity") if isinstance(context.get("capacity"), dict) else {},
            "probe": context.get("probe") if isinstance(context.get("probe"), dict) else {},
        }
    raise ValueError(f"Unknown Agent capability: {capability}")


def _store_capability_result(context: dict[str, Any], capability: str, result: Any) -> None:
    if capability == "api_pool_status.get" and isinstance(result, dict):
        context["capacity"] = result
        if isinstance(result.get("pool"), dict):
            context["pool"] = result["pool"]
    elif capability == "account_probe.get" and isinstance(result, dict):
        context["probe"] = result
    elif capability == "refill_decision.calculate" and isinstance(result, dict):
        context["decision"] = result


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    safe = {}
    for key, value in arguments.items():
        if isinstance(value, dict):
            safe[key] = f"context.{key}"
        else:
            safe[key] = value
    return safe


def _capability_result_summary(capability: str, result: Any) -> dict[str, Any]:
    if not isinstance(result, dict):
        return {"result_type": type(result).__name__}
    if capability == "api_pool_status.get":
        return {
            "available_accounts": result.get("available_accounts"),
            "active_account_count": result.get("active_account_count"),
            "reserve_account_count": result.get("reserve_account_count"),
            "current_speed_days": result.get("current_speed_days"),
            "burst_1h_five_hour_multiple": result.get("burst_1h_five_hour_multiple"),
        }
    if capability == "account_probe.get":
        return {
            "probe_fresh": result.get("probe_fresh"),
            "detected_401_1h": result.get("detected_401_1h"),
            "detected_401_24h": result.get("detected_401_24h"),
            "duplicate_email_alert_count": result.get("duplicate_email_alert_count"),
        }
    if capability == "refill_decision.calculate":
        return {
            "severity": result.get("severity"),
            "suggested_add_count": result.get("suggested_add_count"),
            "manual_review_required": result.get("manual_review_required"),
        }
    return {"keys": list(result.keys())[:10]}


def _context_pack_trace_summary(context_pack: dict[str, Any]) -> dict[str, Any]:
    target_pool = context_pack.get("target_pool") if isinstance(context_pack.get("target_pool"), dict) else {}
    data_quality = context_pack.get("data_quality") if isinstance(context_pack.get("data_quality"), dict) else {}
    system_constraints = context_pack.get("system_constraints") if isinstance(context_pack.get("system_constraints"), dict) else {}
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
            "event_stream_available": data_quality.get("event_stream_available"),
            "history_available": data_quality.get("history_available"),
            "warnings": data_quality.get("warnings") if isinstance(data_quality.get("warnings"), list) else [],
        },
        "system_constraints": {
            "read_only": system_constraints.get("read_only"),
            "can_send_dingtalk": system_constraints.get("can_send_dingtalk"),
            "can_push_accounts": system_constraints.get("can_push_accounts"),
            "can_delete_accounts": system_constraints.get("can_delete_accounts"),
            "can_buy_accounts": system_constraints.get("can_buy_accounts"),
        },
    }


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


def _context_data_quality(context_pack: dict[str, Any]) -> dict[str, Any]:
    value = context_pack.get("data_quality")
    return value if isinstance(value, dict) else {}


def _resolve_pool(pool_id: str, pools: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not pool_id:
        return None
    return next((pool for pool in pools if str(pool.get("id")) == str(pool_id)), None)


def _match_pool(message: str, pools: list[dict[str, Any]]) -> dict[str, Any] | None:
    normalized = message.lower()
    scored = []
    for pool in pools:
        score = _pool_match_score(normalized, pool)
        if score > 0:
            scored.append((score, pool))
    if scored:
        scored.sort(key=lambda item: item[0], reverse=True)
        return scored[0][1]
    if len(pools) == 1:
        return pools[0]
    return None


def _pool_match_score(message: str, pool: dict[str, Any]) -> int:
    score = 0
    name = str(pool.get("name") or "").strip().lower()
    account_type = str(pool.get("account_type") or "").strip().lower()
    group_id = str(pool.get("active_group_id") or "").strip().lower()
    if name and name in message:
        score += 10
    for token in _tokens(name):
        if token and token in message:
            score += 2
    if account_type and account_type in message:
        score += 6
    if group_id and (f"group #{group_id}" in message or f"group{group_id}" in message or f"#{group_id}" in message):
        score += 4
    for account_type_token in ("pro", "plus", "free", "team", "k12"):
        if account_type_token in message and account_type == account_type_token:
            score += 8
    return score


def _tokens(value: str) -> list[str]:
    return [
        token
        for token in value.replace("/", " ").replace("-", " ").replace("_", " ").replace("#", " ").split()
        if len(token) >= 2
    ]


def _assistant_message_from_report(report: dict[str, Any]) -> str:
    llm = report.get("llm") if isinstance(report.get("llm"), dict) else {}
    for value in (llm.get("message"), llm.get("operator_message"), llm.get("summary"), report.get("headline")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    headline = str(report.get("headline") or "").strip()
    severity = str(report.get("severity") or "").strip()
    return headline or (f"Agent analysis finished with severity: {severity}" if severity else "Agent analysis finished.")
