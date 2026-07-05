from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.modules.agent.settings import get_agent_llm_runtime_settings


logger = logging.getLogger("app.agent_llm")


def level1_config(settings: Any | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    model = _level1_model(settings)
    enabled = bool(getattr(settings, "agent_llm_enabled", True))
    configured = bool(enabled and settings.agent_llm_base_url and settings.agent_llm_api_key and model)
    return {
        "provider": "openai_compatible",
        "level": "level1",
        "enabled": enabled,
        "configured": configured,
        "base_url_configured": bool(settings.agent_llm_base_url),
        "api_key_configured": bool(settings.agent_llm_api_key),
        "model": model,
        "temperature": _level1_temperature(settings),
        "timeout_seconds": settings.agent_request_timeout_seconds,
        "source": getattr(settings, "agent_llm_source", "environment"),
    }


def level0_config(settings: Any | None = None) -> dict[str, Any]:
    return level1_config(settings)


async def level1_config_from_database(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    return level1_config(await get_agent_llm_runtime_settings(db))


async def explain_level1_analysis(
    *,
    pool: dict[str, Any],
    capacity: dict[str, Any],
    probe: dict[str, Any],
    decision: dict[str, Any],
    user_message: str | None = None,
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any]:
    settings = await get_agent_llm_runtime_settings(db)
    model = _level1_model(settings)
    if not (getattr(settings, "agent_llm_enabled", True) and settings.agent_llm_base_url and settings.agent_llm_api_key and model):
        return {"enabled": False, "configured": False}

    payload = _analysis_payload(pool=pool, capacity=capacity, probe=probe, decision=decision, user_message=user_message)
    try:
        langchain_result = await _try_langchain_level1(settings=settings, payload=payload, model=model)
        if langchain_result is not None:
            return langchain_result

        content = await _chat_completion(
            base_url=settings.agent_llm_base_url,
            api_key=settings.agent_llm_api_key,
            model=model,
            temperature=_level1_temperature(settings),
            timeout=settings.agent_request_timeout_seconds,
            messages=[
                {"role": "system", "content": _system_prompt()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
        )
        parsed = _parse_json_object(content)
        if parsed is not None:
            result = {
                "enabled": True,
                "configured": True,
                "level": "level1",
                "model": model,
                "summary": _string_or_none(parsed.get("summary")),
                "risk_assessment": _string_or_none(parsed.get("risk_assessment")),
                "operator_message": _string_or_none(parsed.get("operator_message")),
                "questions": _string_list(parsed.get("questions")),
                "raw_text": content,
            }
            result["message"] = _compose_display_message(result)
            return result
        message = _naturalize_freeform_text(content)
        return {
            "enabled": True,
            "configured": True,
            "level": "level1",
            "model": model,
            "operator_message": message,
            "message": message,
            "raw_text": content,
        }
    except Exception as exc:  # noqa: BLE001 - LLM explanation must not block deterministic analysis.
        logger.warning("agent_level1_explanation_failed model=%s error=%s", model, exc)
        return {
            "enabled": True,
            "configured": True,
            "level": "level1",
            "model": model,
            "error": str(exc),
        }


async def explain_level0_analysis(**kwargs: Any) -> dict[str, Any]:
    return await explain_level1_analysis(**kwargs)


async def plan_level1_capabilities(
    *,
    user_message: str,
    pools: list[dict[str, Any]],
    selected_pool_id: str | None = None,
    trigger: str = "manual_chat",
    db: AsyncIOMotorDatabase | None = None,
) -> dict[str, Any]:
    settings = await get_agent_llm_runtime_settings(db)
    model = _level1_model(settings)
    if not (getattr(settings, "agent_llm_enabled", True) and settings.agent_llm_base_url and settings.agent_llm_api_key and model):
        return {"enabled": False, "configured": False}

    payload = {
        "user_message": user_message,
        "trigger": trigger,
        "selected_pool_id": selected_pool_id,
        "available_capabilities": [
            {
                "name": "api_pool_status.get",
                "description": "Read existing cached API pool capacity status by pool_id.",
            },
            {
                "name": "account_probe.get",
                "description": "Read existing account probe summary after capacity identifies site_id/group_id.",
            },
            {
                "name": "refill_decision.calculate",
                "description": "Calculate deterministic refill and warning advice from pool, capacity, and probe.",
            },
        ],
        "pools": [
            _pick(
                pool,
                "id",
                "name",
                "account_type",
                "site_id",
                "active_group_id",
            )
            for pool in pools[:50]
        ],
    }
    try:
        langchain_result = await _try_langchain_level1_plan(settings=settings, payload=payload, model=model)
        if langchain_result is not None:
            return langchain_result

        content = await _chat_completion(
            base_url=settings.agent_llm_base_url,
            api_key=settings.agent_llm_api_key,
            model=model,
            temperature=_level1_temperature(settings),
            timeout=settings.agent_request_timeout_seconds,
            messages=[
                {"role": "system", "content": _planner_system_prompt()},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
        )
        parsed = _parse_json_object(content)
        if parsed is None:
            raise ValueError("planner response is not a JSON object")
        return {
            "enabled": True,
            "configured": True,
            "level": "level1",
            "model": model,
            "framework": "http_fallback",
            "raw_text": content,
            **parsed,
        }
    except Exception as exc:  # noqa: BLE001 - planner failure falls back to deterministic flow.
        logger.warning("agent_level1_planning_failed model=%s error=%s", model, exc)
        return {
            "enabled": True,
            "configured": True,
            "level": "level1",
            "model": model,
            "error": str(exc),
        }


async def _try_langchain_level1(*, settings: Any, payload: dict[str, Any], model: str) -> dict[str, Any] | None:
    try:
        from app.modules.agent.langchain_adapter import langchain_available, run_level1_explanation_chain

        if not langchain_available():
            return None
        return await run_level1_explanation_chain(settings=settings, payload=payload, system_prompt=_system_prompt())
    except Exception as exc:  # noqa: BLE001 - fall back to direct OpenAI-compatible call.
        logger.warning("agent_level1_langchain_chain_failed model=%s error=%s", model, exc)
        return None


async def _try_langchain_level1_plan(*, settings: Any, payload: dict[str, Any], model: str) -> dict[str, Any] | None:
    try:
        from app.modules.agent.langchain_adapter import langchain_available, run_level1_planning_chain

        if not langchain_available():
            return None
        return await run_level1_planning_chain(settings=settings, payload=payload, system_prompt=_planner_system_prompt())
    except Exception as exc:  # noqa: BLE001 - fall back to direct OpenAI-compatible call.
        logger.warning("agent_level1_langchain_planning_failed model=%s error=%s", model, exc)
        return None


async def _chat_completion(
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    timeout: int,
    messages: list[dict[str, str]],
) -> str:
    url = f"{base_url.rstrip('/')}/chat/completions"
    body = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=body,
        )
        if response.status_code == 400:
            body.pop("response_format", None)
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json=body,
            )
    response.raise_for_status()
    data = response.json()
    choices = data.get("choices") if isinstance(data, dict) else None
    if not choices or not isinstance(choices, list):
        raise ValueError("LLM response missing choices")
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        raise ValueError("LLM response missing message content")
    return content


def _system_prompt() -> str:
    return (
        "你是账号池运营 Agent 的 Level 1 决策解释模型。"
        "你只能基于输入的规则引擎结果、容量数据、账号探测数据和用户问题生成运营解释。"
        "不要改写 suggested_add_count、severity、补号数量或任何数值决策。"
        "不要在面向运营人员的回答里说“规则引擎建议”或“按规则引擎”。"
        "如果输入中已经有 detected_401_clusters_24h、largest_401_cluster_24h 或 concentrated_401_burst_24h，"
        "要直接说明 401 是否集中在某一时间段，不要再询问人工确认这一点。"
        "不要建议自动执行推号、删号、买号等高风险动作，只能给人工可读建议。"
        "输出必须是一个 JSON object，字段只能包含 summary、risk_assessment、operator_message、questions。"
        "summary、risk_assessment、operator_message 必须是自然语言字符串，不能是 JSON 字符串或对象。"
        "questions 必须是自然语言字符串数组，最多 3 条。"
        "不要输出 Markdown 代码块，不要在任何字段里嵌套 JSON。"
        "operator_message 要适合直接展示给运营人员，语气简洁、明确、中文。"
    )


def _planner_system_prompt() -> str:
    return (
        "You are the Level 1 planning model for a read-only account-pool operations Agent. "
        "Use a ReAct style internally: reason about the user's intent, then choose only the capabilities needed. "
        "You may choose only these capabilities: api_pool_status.get, account_probe.get, refill_decision.calculate. "
        "All capabilities are read-only. Do not plan refresh, probe execution, database writes, notifications, buying accounts, deleting accounts, or pushing accounts. "
        "If the user asks whether to add accounts, warnings, risk, survival time, quota pressure, 401 risk, or operational advice, include refill_decision.calculate. "
        "If refill_decision.calculate is included, account_probe.get and api_pool_status.get are also needed. "
        "If account_probe.get is included, api_pool_status.get is also needed because site_id and group_id come from capacity data. "
        "Select target_pool_id from the provided pools when possible. "
        "Return exactly one JSON object with fields: intent, thought, target_pool_id, capability_plan, fallback_allowed. "
        "capability_plan must be an array of objects with fields: capability, reason. "
        "Do not include Markdown or nested JSON strings."
    )


def _analysis_payload(
    *,
    pool: dict[str, Any],
    capacity: dict[str, Any],
    probe: dict[str, Any],
    decision: dict[str, Any],
    user_message: str | None = None,
) -> dict[str, Any]:
    return {
        "user_message": user_message,
        "pool": _pick(
            pool,
            "id",
            "name",
            "account_type",
            "site_id",
            "active_group_id",
        ),
        "decision": _pick(
            decision,
            "severity",
            "headline",
            "suggested_add_count",
            "suggested_push_from_reserve_count",
            "suggested_make_new_count",
            "manual_review_required",
            "reasons",
            "suggested_actions",
            "inputs",
        ),
        "capacity": _pick(
            capacity,
            "active_account_count",
            "reserve_account_count",
            "available_accounts",
            "current_speed_days",
            "recent_day_five_hour_peak_multiple",
            "seven_day_five_hour_peak_multiple",
            "burst_1h_five_hour_multiple",
            "active_burst_1h_five_hour_multiple",
            "burst_1h_observed_cost",
            "burst_1h_elapsed_minutes",
            "burst_1h_cost",
            "burst_1h_five_hour_estimated_cost",
            "burst_1h_trend",
            "burst_1h_trend_label",
            "burst_1h_trend_strength",
            "burst_1h_trend_strength_label",
            "burst_1h_trend_change_percent",
            "burst_1h_trend_recent_avg_cost",
            "burst_1h_trend_baseline_avg_cost",
            "burst_1h_trend_recent_hours",
            "burst_1h_trend_baseline_hours",
            "five_hour_remaining_usd",
            "seven_day_remaining_usd",
            "health_status",
            "health_label",
            "cache_fresh",
            "last_refreshed_at",
        ),
        "probe": _pick(
            probe,
            "probe_fresh",
            "last_probe_at",
            "detected_401_1h",
            "detected_401_24h",
            "detected_401_7d",
            "pro_401_1h",
            "pro_401_24h",
            "pro_401_7d",
            "recovered_24h",
            "duplicate_email_alert_count",
            "median_survival_hours_7d",
            "data_source",
        ),
    }


def _pick(source: dict[str, Any], *keys: str) -> dict[str, Any]:
    return {key: source.get(key) for key in keys}


def _parse_json_object(content: str) -> dict[str, Any] | None:
    text = content.strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def _level1_model(settings: Any) -> str | None:
    return settings.agent_level1_model or getattr(settings, "agent_level2_model", None) or settings.agent_level0_model


def _level1_temperature(settings: Any) -> float:
    if settings.agent_level1_model:
        return settings.agent_level1_temperature
    return settings.agent_level0_temperature


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, default=str)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [_string_or_none(item) or "" for item in value if item is not None]


def _compose_display_message(result: dict[str, Any]) -> str:
    parts = [
        _display_text(result.get("operator_message")),
        _display_text(result.get("summary")),
        _display_text(result.get("risk_assessment")),
    ]
    questions = [_display_text(question) for question in _string_list(result.get("questions"))]
    questions = [question for question in questions if question]
    if questions:
        parts.append(f"需要人工确认：{'；'.join(questions)}。")
    message = "\n\n".join(_dedupe([part for part in parts if part]))
    return message or _display_text(result.get("raw_text"))


def _display_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _naturalize_freeform_text(value)
    if isinstance(value, dict):
        return _dict_to_natural_text(value)
    if isinstance(value, list):
        return "；".join(part for part in (_display_text(item) for item in value) if part)
    return str(value).strip()


def _naturalize_freeform_text(value: str) -> str:
    text = value.strip().strip("`").strip()
    parsed = _parse_json_object(text)
    if parsed is not None:
        return _dict_to_natural_text(parsed)
    return _remove_json_blocks(text)


def _dict_to_natural_text(value: dict[str, Any]) -> str:
    operator_message = _display_text(value.get("operator_message"))
    summary = _display_text(value.get("summary"))
    risk_text = _risk_to_natural_text(value.get("risk_assessment"))
    main_risk_text = _list_to_sentence("主要风险", value.get("main_risks"))
    capacity_status = _display_text(value.get("capacity_status"))
    data_freshness = _display_text(value.get("data_freshness"))
    severity = _display_text(value.get("severity"))

    parts = [operator_message, summary, risk_text, main_risk_text, capacity_status, data_freshness]
    if not any(parts) and severity:
        parts.append(f"当前风险等级为 {severity}。")
    if not any(parts):
        parts = [f"{key}：{_display_text(item)}" for key, item in value.items() if _display_text(item)]
    return " ".join(_dedupe([part for part in parts if part])).strip()


def _risk_to_natural_text(value: Any) -> str:
    if isinstance(value, dict):
        return _dict_to_natural_text(value)
    if isinstance(value, list):
        return _list_to_sentence("风险判断", value)
    return _display_text(value)


def _list_to_sentence(label: str, value: Any) -> str:
    if not isinstance(value, list):
        return ""
    items = [_display_text(item).rstrip("。") for item in value]
    items = [item for item in items if item]
    if not items:
        return ""
    return f"{label}：{'；'.join(items)}。"


def _remove_json_blocks(value: str) -> str:
    text = value
    while True:
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            break
        candidate = text[start : end + 1]
        if _parse_json_object(candidate) is None:
            break
        text = f"{text[:start].strip()}\n{text[end + 1:].strip()}".strip()
    return text.strip()


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for value in values:
        normalized = " ".join(value.split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(value.strip())
    return deduped
