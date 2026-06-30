from __future__ import annotations

from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.decision_validator import validate_agent_decision
from app.modules.agent.llm_client import invoke_agent_level1_json


async def decide_with_context_pack(
    db: AsyncIOMotorDatabase,
    *,
    context_pack: dict[str, Any],
) -> dict[str, Any]:
    """Ask the Level 1 model to make the primary pool operation decision."""

    payload = {
        "task": "make_pool_operation_decision",
        "context_pack": context_pack,
    }
    llm_result = await invoke_agent_level1_json(db, system_prompt=_system_prompt(), payload=payload)
    raw_decision = llm_result.get("data") if isinstance(llm_result.get("data"), dict) else {}
    decision = validate_agent_decision(raw_decision, context_pack=context_pack)
    return {
        "decision": decision,
        "llm": {
            "enabled": llm_result.get("enabled"),
            "configured": llm_result.get("configured"),
            "level": llm_result.get("level"),
            "model": llm_result.get("model"),
            "source": llm_result.get("source"),
            "framework": llm_result.get("framework"),
            "raw_text": llm_result.get("raw_text"),
            "message": decision.get("operator_message"),
            "operator_message": decision.get("operator_message"),
            "summary": decision.get("summary"),
        },
        "validator": decision.get("validator") if isinstance(decision.get("validator"), dict) else {},
    }


def _system_prompt() -> str:
    return (
        "你是 AIwelink 账号池运营 Agent 的 Level 1 主决策模型。"
        "你不是普通聊天助手，你的任务是根据账号池 Context Pack 做运营决策。"
        "后端不会用固定公式替代你的最终判断；补多少号、风险等级、是否告警都由你基于上下文判断。"
        "但你必须遵守当前系统约束，尤其是只读约束。"
        "当前阶段你只能提出建议、告警草案、人工确认要求和下一步观察计划。"
        "你不能直接推号、删除账号、购买账号、修改账号池配置、发送钉钉通知或触发 sub2api 刷新。"
        "不要假设不存在的数据，不要编造账号数量、额度、401 事件或历史决策。"
        "如果数据不足，必须在 data_gaps 中明确指出。"
        "补号数量不要机械等同于 target_active 缺口，要综合当前可用账号、active 与 reserve、5h/7d 剩余额度、"
        "当前速度可支撑时间、近期 5h 峰值、7d 峰值、突发 1h 预估、突发趋势、账号探测异常、"
        "事件记录事件流、最近 Agent 决策、最近对话和数据新鲜度。"
        "Context Pack 中的 event_stream 是理解账号池过程变化的关键依据，里面包含账号状态事件、401、5h/7d 限额、"
        "错误变化、状态变化和最近时间线。判断封号是否同批发生、是否存在连续错误恶化、是否只是额度到达，"
        "要优先参考 event_stream.notable_patterns、event_stream.recent_timeline、event_stream.event_type_counts 和 status_transition_counts。"
        "如果 Context Pack 中 probe.largest_401_cluster_24h 或 probe.concentrated_401_burst_24h 显示 401 已经集中在同一时间段，"
        "你必须直接把它作为事实纳入判断，不要再向人工提问“这些 401 是否集中在同一批或同一时间段”。"
        "如果 401 聚集信息显示集中批量封禁，要用类似“今天某时间段集中出现一批 401”这样的运营语言描述。"
        "不要说“按规则引擎建议”，不要把 deterministic fallback 或规则基线当作你的主判断来源。"
        "你必须只输出一个 JSON object，不要输出 Markdown，不要输出代码块，不要在字段中嵌套 JSON 字符串。"
        "JSON 字段必须包含："
        "decision_type, schema_version, severity, summary, operator_message, should_add_accounts, "
        "suggested_add_count, confidence, main_reasons, risk_factors, data_gaps, should_alert, alert_channels, "
        "requires_human_confirm, recommended_actions, next_observation_focus, follow_up_questions, continue_decision_loop。"
        "decision_type 必须是 pool_operation_decision。schema_version 必须是 agent_decision.v1。"
        "severity 只能是 healthy、watch、warning、danger、critical。"
        "confidence 只能是 low、medium、high。suggested_add_count 必须是 0 到 200 的整数。"
        "recommended_actions 只能使用这些 action_type：observe、prepare_accounts、manual_review、notify_draft、"
        "investigate_probe、investigate_capacity。"
        "不要输出 push_accounts、delete_accounts、buy_accounts、modify_pool_config、send_dingtalk 作为动作类型。"
        "operator_message 要适合直接展示给运营人员，中文、自然语言、简洁明确。"
    )
