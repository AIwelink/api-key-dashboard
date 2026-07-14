from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.capacity import list_agent_pools
from app.modules.agent.llm_client import invoke_agent_level1_json
from app.modules.agent.triggers import TRIGGER_EVENT_SPIKE, TRIGGER_SCHEDULER_PATROL, TRIGGER_SCHEDULER_TASK_DUE
from app.utils import now_utc, serialize_doc


INTENT_SCHEMA_VERSION = "agent_intent.v1"

INTENT_POOL_OPERATION_DECISION = "pool_operation_decision"
INTENT_POOL_DATA_QUESTION = "pool_data_question"
INTENT_DECISION_REVIEW = "decision_review"
INTENT_OPERATOR_FEEDBACK = "operator_feedback"
INTENT_AGENT_USAGE_QUESTION = "agent_usage_question"
INTENT_SMALLTALK_OR_OUT_OF_SCOPE = "smalltalk_or_out_of_scope"
INTENT_UNAUTHORIZED_ACTION_REQUEST = "unauthorized_action_request"
INTENT_UNKNOWN = "unknown"

ALLOWED_INTENTS = {
    INTENT_POOL_OPERATION_DECISION,
    INTENT_POOL_DATA_QUESTION,
    INTENT_DECISION_REVIEW,
    INTENT_OPERATOR_FEEDBACK,
    INTENT_AGENT_USAGE_QUESTION,
    INTENT_SMALLTALK_OR_OUT_OF_SCOPE,
    INTENT_UNAUTHORIZED_ACTION_REQUEST,
    INTENT_UNKNOWN,
}

DECISION_INTENTS = {INTENT_POOL_OPERATION_DECISION}
DIRECT_REPLY_INTENTS = {
    INTENT_AGENT_USAGE_QUESTION,
    INTENT_SMALLTALK_OR_OUT_OF_SCOPE,
    INTENT_UNAUTHORIZED_ACTION_REQUEST,
}

CONFIDENCE_VALUES = {"low", "medium", "high"}


async def route_agent_intent(
    db: AsyncIOMotorDatabase,
    *,
    user_message: str | None,
    trigger: str,
    pool_id: str | None,
    conversation_id: str | None,
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Classify an Agent turn before the controller builds a decision context."""

    del actor  # Reserved for later per-user routing policy.
    normalized_trigger = _clean_string(trigger) or "manual"
    message = _clean_string(user_message) or ""

    if normalized_trigger == "manual_analyze":
        return _intent(
            intent=INTENT_POOL_OPERATION_DECISION,
            confidence="high",
            target_pool_id=pool_id,
            requires_pool_context=True,
            should_create_decision=True,
            should_update_task=True,
            reason="点击分析固定进入账号池运营决策。",
            trigger=normalized_trigger,
            conversation_id=conversation_id,
            router="direct_manual_analyze",
        )

    if normalized_trigger in {TRIGGER_SCHEDULER_PATROL, TRIGGER_SCHEDULER_TASK_DUE, TRIGGER_EVENT_SPIKE}:
        return _intent(
            intent=INTENT_POOL_OPERATION_DECISION,
            confidence="high",
            target_pool_id=pool_id,
            requires_pool_context=True,
            should_create_decision=True,
            should_update_task=True,
            reason=f"{normalized_trigger} 自动触发账号池运营决策。",
            trigger=normalized_trigger,
            conversation_id=conversation_id,
            router="direct_scheduler_decision",
        )

    if not message:
        return _intent(
            intent=INTENT_UNKNOWN,
            confidence="low",
            target_pool_id=pool_id,
            requires_pool_context=False,
            should_create_decision=False,
            should_update_task=False,
            reason="用户消息为空。",
            trigger=normalized_trigger,
            conversation_id=conversation_id,
            direct_reply="请输入你想让 Agent 判断、查询或复盘的问题。",
            router="deterministic_empty_message",
        )

    pools = await _safe_list_pools(db)
    selected_pool_id = await _resolve_pool_id_from_pools(pools, message=message, pool_id=pool_id)
    hard_guard = _hard_guard_route(
        message=message,
        pool_id=selected_pool_id,
        trigger=normalized_trigger,
        conversation_id=conversation_id,
    )
    if hard_guard is not None:
        return hard_guard

    deterministic_hint = _deterministic_route(
        message=message,
        pool_id=selected_pool_id,
        trigger=normalized_trigger,
        conversation_id=conversation_id,
    )

    try:
        llm_result = await invoke_agent_level1_json(
            db,
            system_prompt=_intent_router_system_prompt(),
            payload={
                "task": "route_agent_intent",
                "schema_version": INTENT_SCHEMA_VERSION,
                "trigger": normalized_trigger,
                "user_message": message,
                "selected_pool_id": selected_pool_id,
                "conversation_id": _clean_string(conversation_id),
                "available_intents": sorted(ALLOWED_INTENTS),
                "intent_definitions": _intent_definitions(),
                "available_pools": [_pool_view(pool) for pool in pools[:50]],
                "deterministic_hint": deterministic_hint,
                "safety_boundary": {
                    "read_only": True,
                    "can_push_accounts": False,
                    "can_buy_accounts": False,
                    "can_delete_accounts": False,
                    "can_refresh_sub2api": False,
                    "can_start_probe": False,
                    "can_send_dingtalk": False,
                },
            },
        )
        raw_intent = llm_result.get("data") if isinstance(llm_result.get("data"), dict) else {}
        return _normalize_llm_intent(
            raw_intent,
            fallback=deterministic_hint,
            trigger=normalized_trigger,
            conversation_id=conversation_id,
            selected_pool_id=selected_pool_id,
            llm_result=llm_result,
        )
    except Exception as exc:  # noqa: BLE001 - routing should keep Agent usable when LLM routing fails.
        fallback = {**deterministic_hint}
        fallback["router"] = "deterministic_fallback"
        fallback["llm_error"] = str(exc)
        fallback.setdefault("safety_notes", [])
        fallback["safety_notes"] = [*fallback["safety_notes"], "intent_llm_router_unavailable"]
        return serialize_doc(fallback)


def _normalize_llm_intent(
    raw: dict[str, Any],
    *,
    fallback: dict[str, Any],
    trigger: str,
    conversation_id: str | None,
    selected_pool_id: str | None,
    llm_result: dict[str, Any],
) -> dict[str, Any]:
    intent = _clean_string(raw.get("intent"))
    if intent not in ALLOWED_INTENTS:
        intent = str(fallback.get("intent") or INTENT_UNKNOWN)

    confidence = _clean_string(raw.get("confidence")) or str(fallback.get("confidence") or "medium")
    if confidence not in CONFIDENCE_VALUES:
        confidence = "medium"

    target_pool_id = _clean_string(raw.get("target_pool_id")) or _clean_string(fallback.get("target_pool_id")) or selected_pool_id
    direct_reply = _clean_string(raw.get("direct_reply"))
    reason = _clean_string(raw.get("reason")) or str(fallback.get("reason") or "Level 1 routed the Agent intent.")
    safety_notes = _string_list(raw.get("safety_notes"))

    normalized = _intent(
        intent=intent,
        confidence=confidence,
        target_pool_id=target_pool_id,
        requires_pool_context=_bool_or(raw.get("requires_pool_context"), _default_requires_pool_context(intent, bool(target_pool_id))),
        should_create_decision=_bool_or(raw.get("should_create_decision"), intent == INTENT_POOL_OPERATION_DECISION),
        should_update_task=_bool_or(raw.get("should_update_task"), intent in {INTENT_POOL_OPERATION_DECISION, INTENT_DECISION_REVIEW, INTENT_OPERATOR_FEEDBACK}),
        is_operator_feedback=_bool_or(raw.get("is_operator_feedback"), intent == INTENT_OPERATOR_FEEDBACK),
        is_unauthorized_action=_bool_or(raw.get("is_unauthorized_action"), intent == INTENT_UNAUTHORIZED_ACTION_REQUEST),
        reason=reason,
        trigger=trigger,
        conversation_id=conversation_id,
        direct_reply=direct_reply or _default_direct_reply(intent),
        safety_notes=safety_notes,
        router="level1_langchain",
        llm=_llm_summary(llm_result),
    )
    return _apply_safety_normalization(normalized, fallback=fallback)


def _apply_safety_normalization(intent: dict[str, Any], *, fallback: dict[str, Any]) -> dict[str, Any]:
    fallback_intent = str(fallback.get("intent") or "")
    if fallback_intent == INTENT_UNAUTHORIZED_ACTION_REQUEST:
        return {
            **fallback,
            "router": "deterministic_hard_guard",
            "llm": intent.get("llm"),
            "safety_notes": sorted(set([*fallback.get("safety_notes", []), "llm_route_overridden_by_safety_guard"])),
        }
    if fallback_intent in {
        INTENT_AGENT_USAGE_QUESTION,
        INTENT_SMALLTALK_OR_OUT_OF_SCOPE,
        INTENT_OPERATOR_FEEDBACK,
        INTENT_POOL_DATA_QUESTION,
    } and fallback.get("confidence") in {"medium", "high"}:
        routed_intent = str(intent.get("intent") or "")
        if routed_intent == INTENT_POOL_OPERATION_DECISION:
            return {
                **fallback,
                "router": "deterministic_confident_override",
                "llm": intent.get("llm"),
                "safety_notes": sorted(set([*fallback.get("safety_notes", []), "llm_operation_route_overridden_by_intent_guard"])),
            }

    routed_intent = str(intent.get("intent") or "")
    if routed_intent == INTENT_UNAUTHORIZED_ACTION_REQUEST:
        intent["requires_pool_context"] = False
        intent["should_create_decision"] = False
        intent["should_update_task"] = False
        intent["reply_directly"] = True
        intent["direct_reply"] = intent.get("direct_reply") or _default_direct_reply(INTENT_UNAUTHORIZED_ACTION_REQUEST)
        intent["is_unauthorized_action"] = True
    elif routed_intent in DIRECT_REPLY_INTENTS:
        intent["requires_pool_context"] = False
        intent["should_create_decision"] = False
        intent["reply_directly"] = True
        intent["direct_reply"] = intent.get("direct_reply") or _default_direct_reply(routed_intent)
    elif routed_intent == INTENT_OPERATOR_FEEDBACK:
        intent["should_create_decision"] = False
        intent["reply_directly"] = True
        intent["direct_reply"] = intent.get("direct_reply") or _default_direct_reply(routed_intent)
        intent["is_operator_feedback"] = True
    elif routed_intent == INTENT_POOL_DATA_QUESTION:
        intent["should_create_decision"] = False
    elif routed_intent == INTENT_POOL_OPERATION_DECISION:
        intent["requires_pool_context"] = True
        intent["should_create_decision"] = True
        intent["should_update_task"] = True
        intent["reply_directly"] = False
        intent["direct_reply"] = None
    return serialize_doc(intent)


def _hard_guard_route(
    *,
    message: str,
    pool_id: str | None,
    trigger: str,
    conversation_id: str | None,
) -> dict[str, Any] | None:
    lowered = message.lower()
    if not _contains_any(lowered, _UNAUTHORIZED_KEYWORDS):
        return None
    return _intent(
        intent=INTENT_UNAUTHORIZED_ACTION_REQUEST,
        confidence="high",
        target_pool_id=pool_id,
        requires_pool_context=False,
        should_create_decision=False,
        should_update_task=False,
        is_unauthorized_action=True,
        reason="用户请求了当前只读边界之外的动作。",
        trigger=trigger,
        conversation_id=conversation_id,
        direct_reply=_default_direct_reply(INTENT_UNAUTHORIZED_ACTION_REQUEST),
        safety_notes=["read_only_boundary_enforced"],
        router="deterministic_hard_guard",
    )


def _deterministic_route(
    *,
    message: str,
    pool_id: str | None,
    trigger: str,
    conversation_id: str | None,
) -> dict[str, Any]:
    lowered = message.lower()
    if _contains_any(lowered, _AGENT_USAGE_KEYWORDS):
        return _intent(
            intent=INTENT_AGENT_USAGE_QUESTION,
            confidence="high",
            target_pool_id=pool_id,
            requires_pool_context=False,
            should_create_decision=False,
            should_update_task=False,
            reason="用户询问 Agent 自身能力、数据来源或流程。",
            trigger=trigger,
            conversation_id=conversation_id,
            direct_reply=_default_direct_reply(INTENT_AGENT_USAGE_QUESTION),
            router="deterministic_hint",
        )

    if _looks_like_operator_feedback(lowered):
        return _intent(
            intent=INTENT_OPERATOR_FEEDBACK,
            confidence="medium",
            target_pool_id=pool_id,
            requires_pool_context=bool(pool_id),
            should_create_decision=False,
            should_update_task=True,
            is_operator_feedback=True,
            reason="用户消息像是在纠正或补充运营事实。",
            trigger=trigger,
            conversation_id=conversation_id,
            direct_reply=_default_direct_reply(INTENT_OPERATOR_FEEDBACK),
            router="deterministic_hint",
        )

    if _contains_any(lowered, _REVIEW_KEYWORDS):
        return _intent(
            intent=INTENT_DECISION_REVIEW,
            confidence="medium",
            target_pool_id=pool_id,
            requires_pool_context=bool(pool_id),
            should_create_decision=False,
            should_update_task=True,
            reason="用户要求复盘历史判断。",
            trigger=trigger,
            conversation_id=conversation_id,
            router="deterministic_hint",
        )

    if _looks_like_data_question(lowered):
        return _intent(
            intent=INTENT_POOL_DATA_QUESTION,
            confidence="medium",
            target_pool_id=pool_id,
            requires_pool_context=True,
            should_create_decision=False,
            should_update_task=False,
            reason="用户询问账号池数据，不一定需要运营决策。",
            trigger=trigger,
            conversation_id=conversation_id,
            router="deterministic_hint",
        )

    if _contains_any(lowered, _DECISION_KEYWORDS):
        return _intent(
            intent=INTENT_POOL_OPERATION_DECISION,
            confidence="high",
            target_pool_id=pool_id,
            requires_pool_context=True,
            should_create_decision=True,
            should_update_task=True,
            reason="用户询问补号、风险、告警、容量支撑或账号池运营动作。",
            trigger=trigger,
            conversation_id=conversation_id,
            router="deterministic_hint",
        )

    if _contains_any(lowered, _SMALLTALK_KEYWORDS) or len(message) <= 12:
        return _intent(
            intent=INTENT_SMALLTALK_OR_OUT_OF_SCOPE,
            confidence="medium",
            target_pool_id=pool_id,
            requires_pool_context=False,
            should_create_decision=False,
            should_update_task=False,
            reason="用户消息不像账号池运营任务。",
            trigger=trigger,
            conversation_id=conversation_id,
            direct_reply=_default_direct_reply(INTENT_SMALLTALK_OR_OUT_OF_SCOPE),
            router="deterministic_hint",
        )

    return _intent(
        intent=INTENT_UNKNOWN,
        confidence="low",
        target_pool_id=pool_id,
        requires_pool_context=bool(pool_id),
        should_create_decision=False,
        should_update_task=False,
        reason="无法稳定判断用户意图，需要进一步澄清。",
        trigger=trigger,
        conversation_id=conversation_id,
        direct_reply="我还不确定你想让我做账号池决策、查询数据、复盘历史，还是记录人工反馈。请再具体说明一下。",
        router="deterministic_hint",
    )


async def _safe_list_pools(db: AsyncIOMotorDatabase) -> list[dict[str, Any]]:
    try:
        pools_response = await list_agent_pools(db)
    except Exception:
        return []
    return [pool for pool in pools_response.get("items", []) if isinstance(pool, dict)]


async def _resolve_pool_id_from_pools(pools: list[dict[str, Any]], *, message: str, pool_id: str | None) -> str | None:
    if _clean_string(pool_id):
        return _clean_string(pool_id)
    if len(pools) == 1:
        return _clean_string(pools[0].get("id"))
    normalized = message.lower()
    scored: list[tuple[int, str]] = []
    for pool in pools:
        score = 0
        pool_name = str(pool.get("name") or "").strip().lower()
        account_type = str(pool.get("account_type") or "").strip().lower()
        group_id = str(pool.get("active_group_id") or "").strip().lower()
        if pool_name and pool_name in normalized:
            score += 10
        if account_type and account_type in normalized:
            score += 5
        if group_id and (f"group #{group_id}" in normalized or f"group{group_id}" in normalized or f"#{group_id}" in normalized):
            score += 4
        pool_id_value = _clean_string(pool.get("id"))
        if score > 0 and pool_id_value:
            scored.append((score, pool_id_value))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[0][1]


def _intent(
    *,
    intent: str,
    confidence: str,
    target_pool_id: str | None,
    requires_pool_context: bool,
    should_create_decision: bool,
    should_update_task: bool,
    reason: str,
    trigger: str,
    conversation_id: str | None,
    direct_reply: str | None = None,
    is_operator_feedback: bool = False,
    is_unauthorized_action: bool = False,
    safety_notes: list[str] | None = None,
    router: str = "deterministic",
    llm: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return serialize_doc(
        {
            "schema_version": INTENT_SCHEMA_VERSION,
            "intent": intent if intent in ALLOWED_INTENTS else INTENT_UNKNOWN,
            "confidence": confidence if confidence in CONFIDENCE_VALUES else "medium",
            "target_pool_id": _clean_string(target_pool_id),
            "requires_pool_context": bool(requires_pool_context),
            "should_create_decision": bool(should_create_decision),
            "should_update_task": bool(should_update_task),
            "is_operator_feedback": bool(is_operator_feedback),
            "is_unauthorized_action": bool(is_unauthorized_action),
            "reason": reason,
            "reply_directly": direct_reply is not None,
            "direct_reply": direct_reply,
            "safety_notes": safety_notes or [],
            "trigger": trigger,
            "conversation_id": _clean_string(conversation_id),
            "routed_at": now_utc(),
            "router": router,
            "llm": llm or {},
        }
    )


def _intent_router_system_prompt() -> str:
    return (
        "你是账号池运营 Agent 的意图路由器。你只负责分类，不负责做最终运营决策。\n"
        "你必须只输出一个 JSON object，不要 Markdown，不要代码块。\n"
        "允许的 intent 只有：pool_operation_decision, pool_data_question, decision_review, operator_feedback, "
        "agent_usage_question, smalltalk_or_out_of_scope, unauthorized_action_request, unknown。\n"
        "分类规则：\n"
        "- 用户要求判断要不要补号、风险高不高、还能撑多久、要不要告警、准备多少账号，归为 pool_operation_decision。\n"
        "- 用户只是问数据、状态、最近 401 数量、容量是多少、事件是否集中，归为 pool_data_question，不要强行生成补号决策。\n"
        "- 用户要求复盘上次或历史判断，归为 decision_review。\n"
        "- 用户纠正 Agent、补充运营事实、说明负责人要求或让 Agent 记住经验，归为 operator_feedback。\n"
        "- 用户问 Agent 能力、流程、数据来源、会不会自动执行，归为 agent_usage_question。\n"
        "- 闲聊或无关问题归为 smalltalk_or_out_of_scope。\n"
        "- 直接要求推号、买号、删号、刷新 sub2api、启动探测、发送正式钉钉，归为 unauthorized_action_request。\n"
        "输出字段必须包含：schema_version, intent, confidence, target_pool_id, requires_pool_context, "
        "should_create_decision, should_update_task, is_operator_feedback, is_unauthorized_action, reason, "
        "reply_directly, direct_reply, safety_notes。\n"
        "schema_version 必须是 agent_intent.v1。confidence 只能是 low、medium、high。\n"
        "如果 intent 是 smalltalk_or_out_of_scope、agent_usage_question、operator_feedback 或 unauthorized_action_request，"
        "通常 reply_directly=true，并给出简短中文 direct_reply。\n"
        "如果 intent 是 pool_operation_decision，should_create_decision=true，requires_pool_context=true。\n"
        "如果 intent 是 pool_data_question，should_create_decision=false。\n"
        "不要把'是否需要人工确认'误判为 operator_feedback；只有用户明确纠正或补充事实时才是 operator_feedback。\n"
    )


def _intent_definitions() -> dict[str, str]:
    return {
        INTENT_POOL_OPERATION_DECISION: "用户要 Agent 判断账号池运营动作，如补号、风险、告警、支撑时间。",
        INTENT_POOL_DATA_QUESTION: "用户只是查询账号池数据或事件，不一定要形成运营决策。",
        INTENT_DECISION_REVIEW: "用户要求复盘历史 Agent 判断或补号建议是否有效。",
        INTENT_OPERATOR_FEEDBACK: "用户纠正 Agent 或补充运营事实，需要写入长期记忆。",
        INTENT_AGENT_USAGE_QUESTION: "用户询问 Agent 自身能力、流程、数据来源或安全边界。",
        INTENT_SMALLTALK_OR_OUT_OF_SCOPE: "闲聊或与账号池运营无关的问题。",
        INTENT_UNAUTHORIZED_ACTION_REQUEST: "用户要求执行当前阶段禁止的动作。",
        INTENT_UNKNOWN: "无法稳定分类，需要澄清。",
    }


def _pool_view(pool: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": pool.get("id"),
        "name": pool.get("name"),
        "account_type": pool.get("account_type"),
        "site_id": pool.get("site_id"),
        "active_group_id": pool.get("active_group_id"),
        "status": pool.get("status"),
    }


def _default_requires_pool_context(intent: str, has_pool: bool) -> bool:
    if intent in {INTENT_POOL_OPERATION_DECISION, INTENT_POOL_DATA_QUESTION}:
        return True
    if intent in {INTENT_DECISION_REVIEW, INTENT_OPERATOR_FEEDBACK}:
        return has_pool
    return False


def _default_direct_reply(intent: str) -> str | None:
    if intent == INTENT_UNAUTHORIZED_ACTION_REQUEST:
        return "当前 Agent 只能做只读分析、生成建议或告警草稿，不能直接推号、买号、删号、刷新 sub2api、启动探测或发送正式钉钉通知。"
    if intent == INTENT_AGENT_USAGE_QUESTION:
        return (
            "我现在是账号池运营 Agent，可以读取现有账号池缓存、容量、事件窗口、探测摘要、历史决策和长期记忆，"
            "然后给出风险、补号、告警和下一步观察建议。当前阶段我不会直接执行推号、买号、删号、刷新或发送正式通知。"
        )
    if intent == INTENT_OPERATOR_FEEDBACK:
        return "收到，我会把这条人工反馈沉淀到 Agent 记忆里，后续判断会优先参考这类纠正信息。"
    if intent == INTENT_SMALLTALK_OR_OUT_OF_SCOPE:
        return "我主要负责账号池运营分析。你可以问我某个池子要不要补号、风险高不高、最近事件是否异常，或让我复盘一次历史判断。"
    return None


def _llm_summary(llm_result: dict[str, Any]) -> dict[str, Any]:
    return {
        "enabled": llm_result.get("enabled"),
        "configured": llm_result.get("configured"),
        "level": llm_result.get("level"),
        "model": llm_result.get("model"),
        "source": llm_result.get("source"),
        "framework": llm_result.get("framework"),
    }


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)


def _looks_like_data_question(text: str) -> bool:
    return _contains_any(text, _DATA_QUERY_HINTS) and not _contains_any(text, _DECISION_ACTION_HINTS)


def _looks_like_operator_feedback(text: str) -> bool:
    return _contains_any(text, _FEEDBACK_KEYWORDS) and not _contains_any(text, _DECISION_ACTION_HINTS)


def _bool_or(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    return fallback


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    items: list[str] = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            items.append(text)
    return items[:10]


def _clean_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


_DECISION_KEYWORDS = (
    "补号",
    "补多少",
    "要不要补",
    "需要补",
    "风险",
    "告警",
    "预警",
    "还能撑",
    "撑多久",
    "容量不足",
    "封号",
    "掉号",
    "401",
    "限额",
    "高风险",
    "critical",
    "danger",
)

_DATA_QUERY_HINTS = (
    "多少",
    "几个",
    "有哪些",
    "列出",
    "明细",
    "数据",
    "状态",
    "最近 24h",
    "最近24h",
    "最近 1h",
    "最近1h",
    "当前 5h",
    "当前5h",
    "容量是多少",
    "为什么建议",
)

_DECISION_ACTION_HINTS = (
    "要不要",
    "需不需要",
    "是否需要",
    "该不该",
    "补号",
    "告警",
    "预警",
    "怎么办",
)

_REVIEW_KEYWORDS = (
    "复盘",
    "回顾",
    "上次判断",
    "准不准",
    "有没有偏",
    "判断是否有效",
    "后来准不准",
)

_FEEDBACK_KEYWORDS = (
    "不是异常",
    "不是封号",
    "不是问题",
    "不是误报",
    "纠正",
    "更正",
    "负责人说",
    "以后",
    "记住",
    "这次是",
    "实际是",
    "我确认",
    "人工已确认",
    "批量任务导致",
    "质量不好",
)

_AGENT_USAGE_KEYWORDS = (
    "你现在会做什么",
    "你会做什么",
    "你怎么工作",
    "你的流程",
    "数据来源",
    "你能不能",
    "你会不会",
    "你是什么",
    "你怎么判断",
    "agent 怎么",
)

_UNAUTHORIZED_KEYWORDS = (
    "直接推号",
    "帮我推号",
    "自动推号",
    "买号",
    "购买账号",
    "删除账号",
    "删号",
    "禁用账号",
    "修改账号池",
    "刷新 sub2api",
    "刷新sub2api",
    "启动探测",
    "重新探测",
    "发送钉钉",
    "发钉钉",
)

_SMALLTALK_KEYWORDS = (
    "你好",
    "hello",
    "hi",
    "谢谢",
    "辛苦",
    "讲个笑话",
    "天气",
    "你好吗",
)
