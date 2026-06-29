from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.capacity import list_agent_pools
from app.modules.agent.capabilities import invoke_agent_capability
from app.modules.agent.llm import explain_level1_analysis, plan_level1_capabilities
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
) -> dict[str, Any]:
    pools_response = await list_agent_pools(db)
    pools = [pool for pool in pools_response.get("items", []) if isinstance(pool, dict)]
    if not pools:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No analyzable API pools found")

    normalized_message = (user_message or "").strip()
    planning = await _build_plan(
        db,
        pools=pools,
        user_message=normalized_message,
        pool_id=pool_id,
        trigger=trigger,
        allow_planning=allow_planning,
    )
    try:
        report = await _execute_plan(
            db,
            pools=pools,
            user_message=normalized_message or None,
            trigger=trigger,
            planning=planning,
        )
        return report
    except Exception as exc:  # noqa: BLE001 - keep the Agent usable with the deterministic fallback.
        if not planning.get("fallback_allowed", True):
            raise
        fallback_planning = _fallback_plan(
            pools=pools,
            pool_id=pool_id or planning.get("target_pool_id"),
            user_message=normalized_message,
            trigger=trigger,
            reason=f"orchestrated plan failed: {exc}",
        )
        return await _execute_plan(
            db,
            pools=pools,
            user_message=normalized_message or None,
            trigger=trigger,
            planning=fallback_planning,
        )


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
