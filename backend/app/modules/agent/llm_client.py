from __future__ import annotations

import json
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.llm import _chat_completion, _parse_json_object
from app.modules.agent.settings import get_agent_llm_runtime_settings


class AgentLlmConfigError(ValueError):
    pass


async def test_agent_llm_connection(settings: dict[str, Any]) -> dict[str, Any]:
    base_url = str(settings.get("base_url") or "").strip()
    api_key = str(settings.get("api_key") or "").strip()
    model = str(settings.get("level1_model") or settings.get("level2_model") or "").strip()
    temperature = float(settings.get("level1_temperature") or 0.2)
    timeout = int(settings.get("timeout_seconds") or 60)
    if not base_url:
        raise AgentLlmConfigError("Agent LLM base_url is not configured")
    if not api_key:
        raise AgentLlmConfigError("Agent LLM api_key is not configured")
    if not model:
        raise AgentLlmConfigError("Agent LLM model is not configured")

    content = await _try_langchain_connection_test(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        timeout=timeout,
    )
    parsed = _parse_json_object(content)
    if isinstance(parsed, dict):
        message = str(parsed.get("message") or "Agent LLM connection is ready")
        return {"ok": True, "message": message, "model": model}
    return {"ok": True, "message": "Agent LLM connection is ready", "model": model}


async def invoke_agent_level1_json(
    db: AsyncIOMotorDatabase,
    *,
    system_prompt: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    settings = await get_agent_llm_runtime_settings(db)
    base_url = str(settings.agent_llm_base_url or "").strip()
    api_key = str(settings.agent_llm_api_key or "").strip()
    model = str(settings.agent_level1_model or "").strip()
    if not getattr(settings, "agent_llm_enabled", True):
        raise AgentLlmConfigError("Agent LLM is disabled")
    if not base_url:
        raise AgentLlmConfigError("Agent LLM base_url is not configured")
    if not api_key:
        raise AgentLlmConfigError("Agent LLM api_key is not configured")
    if not model:
        raise AgentLlmConfigError("Agent LLM level1_model is not configured")

    content = await _try_langchain_json_call(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=float(settings.agent_level1_temperature or 0.2),
        timeout=int(settings.agent_request_timeout_seconds or 60),
        system_prompt=system_prompt,
        payload=payload,
    )
    parsed = _parse_json_object(content)
    if parsed is None:
        raise ValueError("LLM response is not a JSON object")
    return {
        "enabled": True,
        "configured": True,
        "level": "level1",
        "model": model,
        "source": getattr(settings, "agent_llm_source", "database"),
        "framework": "langchain",
        "raw_text": content,
        "data": parsed,
    }


async def _try_langchain_connection_test(
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    timeout: int,
) -> str:
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.runnables import RunnableLambda
    except Exception:
        return await _direct_connection_test(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout=timeout,
        )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "You are a connection test endpoint. Return only one JSON object."),
            ("human", 'Return exactly: {{"ok": true, "message": "agent llm ready"}}'),
        ]
    )

    async def call_model(prompt_value: Any) -> str:
        messages = []
        for message in prompt_value.to_messages():
            role = "system" if getattr(message, "type", "") == "system" else "user"
            messages.append({"role": role, "content": str(message.content)})
        return await _chat_completion(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout=timeout,
            messages=messages,
        )

    chain = prompt | RunnableLambda(call_model)
    return str(await chain.ainvoke({}))


async def _try_langchain_json_call(
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    timeout: int,
    system_prompt: str,
    payload: dict[str, Any],
) -> str:
    try:
        from langchain_core.prompts import ChatPromptTemplate
        from langchain_core.runnables import RunnableLambda
    except Exception:
        return await _chat_completion(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout=timeout,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
            ],
        )

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "{system_prompt}"),
            ("human", "{payload_json}"),
        ]
    )

    async def call_model(prompt_value: Any) -> str:
        messages = []
        for message in prompt_value.to_messages():
            role = "system" if getattr(message, "type", "") == "system" else "user"
            messages.append({"role": role, "content": str(message.content)})
        return await _chat_completion(
            base_url=base_url,
            api_key=api_key,
            model=model,
            temperature=temperature,
            timeout=timeout,
            messages=messages,
        )

    chain = prompt | RunnableLambda(call_model)
    return str(
        await chain.ainvoke(
            {
                "system_prompt": system_prompt,
                "payload_json": json.dumps(payload, ensure_ascii=False, default=str),
            }
        )
    )


async def _direct_connection_test(
    *,
    base_url: str,
    api_key: str,
    model: str,
    temperature: float,
    timeout: int,
) -> str:
    return await _chat_completion(
        base_url=base_url,
        api_key=api_key,
        model=model,
        temperature=temperature,
        timeout=timeout,
        messages=[
            {"role": "system", "content": "You are a connection test endpoint. Return only one JSON object."},
            {"role": "user", "content": json.dumps({"ok": True, "message": "agent llm ready"})},
        ],
    )
