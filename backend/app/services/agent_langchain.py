from __future__ import annotations

from typing import Any

from app.services.agent_llm import (
    _chat_completion,
    _compose_display_message,
    _level1_model,
    _level1_temperature,
    _naturalize_freeform_text,
    _parse_json_object,
    _string_list,
    _string_or_none,
)


def langchain_available() -> bool:
    try:
        import langchain_core.prompts  # noqa: F401
        import langchain_core.runnables  # noqa: F401
    except Exception:
        return False
    return True


async def run_level1_explanation_chain(
    *,
    settings: Any,
    payload: dict[str, Any],
    system_prompt: str,
) -> dict[str, Any]:
    """Run the Level 1 explanation through a LangChain Runnable chain.

    This keeps the Agent orchestration shape LangChain-native while using the
    existing OpenAI-compatible HTTP client as the model adapter.
    """
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnableLambda

    model = _level1_model(settings)
    if not model:
        raise ValueError("AGENT_LEVEL1_MODEL is not configured")

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "{system_prompt}"),
            ("human", "{payload_json}"),
        ]
    )

    async def call_model(prompt_value: Any) -> str:
        messages = []
        for message in prompt_value.to_messages():
            role = _message_role(message)
            messages.append({"role": role, "content": str(message.content)})
        return await _chat_completion(
            base_url=settings.agent_llm_base_url,
            api_key=settings.agent_llm_api_key,
            model=model,
            temperature=_level1_temperature(settings),
            timeout=settings.agent_request_timeout_seconds,
            messages=messages,
        )

    chain = prompt | RunnableLambda(call_model)
    content = await chain.ainvoke(
        {
            "system_prompt": system_prompt,
            "payload_json": _json_dumps(payload),
        }
    )
    parsed = _parse_json_object(content)
    if parsed is not None:
        result = {
            "enabled": True,
            "configured": True,
            "level": "level1",
            "model": model,
            "framework": "langchain",
            "summary": _string_or_none(parsed.get("summary")),
            "risk_assessment": _string_or_none(parsed.get("risk_assessment")),
            "operator_message": _string_or_none(parsed.get("operator_message")),
            "questions": _string_list(parsed.get("questions")),
            "raw_text": content,
        }
        result["message"] = _compose_display_message(result)
        return result
    message = _naturalize_freeform_text(str(content))
    return {
        "enabled": True,
        "configured": True,
        "level": "level1",
        "model": model,
        "framework": "langchain",
        "operator_message": message,
        "message": message,
        "raw_text": content,
    }


async def run_level1_planning_chain(
    *,
    settings: Any,
    payload: dict[str, Any],
    system_prompt: str,
) -> dict[str, Any]:
    """Run the Level 1 ReAct-style planning step through LangChain."""
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.runnables import RunnableLambda

    model = _level1_model(settings)
    if not model:
        raise ValueError("AGENT_LEVEL1_MODEL is not configured")

    prompt = ChatPromptTemplate.from_messages(
        [
            ("system", "{system_prompt}"),
            ("human", "{payload_json}"),
        ]
    )

    async def call_model(prompt_value: Any) -> str:
        messages = []
        for message in prompt_value.to_messages():
            role = _message_role(message)
            messages.append({"role": role, "content": str(message.content)})
        return await _chat_completion(
            base_url=settings.agent_llm_base_url,
            api_key=settings.agent_llm_api_key,
            model=model,
            temperature=_level1_temperature(settings),
            timeout=settings.agent_request_timeout_seconds,
            messages=messages,
        )

    chain = prompt | RunnableLambda(call_model)
    content = await chain.ainvoke(
        {
            "system_prompt": system_prompt,
            "payload_json": _json_dumps(payload),
        }
    )
    parsed = _parse_json_object(content)
    if parsed is None:
        raise ValueError("planner response is not a JSON object")
    return {
        "enabled": True,
        "configured": True,
        "level": "level1",
        "model": model,
        "framework": "langchain",
        "raw_text": content,
        **parsed,
    }


def _message_role(message: Any) -> str:
    message_type = getattr(message, "type", "")
    if message_type == "system":
        return "system"
    if message_type == "ai":
        return "assistant"
    return "user"


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
