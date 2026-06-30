from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.schemas import AgentLlmSettingsUpdate
from app.utils import now_utc, serialize_doc


AGENT_LLM_SETTINGS_ID = "agent_llm"
AGENT_LLM_SETTINGS_COLLECTION = "agent_llm_settings"

DEFAULT_AGENT_LLM_SETTINGS: dict[str, Any] = {
    "_id": AGENT_LLM_SETTINGS_ID,
    "enabled": False,
    "base_url": None,
    "api_key": None,
    "level1_model": None,
    "level1_temperature": 0.2,
    "level2_model": None,
    "level2_temperature": 0.2,
    "timeout_seconds": 60,
    "loop_enabled": False,
    "loop_interval_seconds": 900,
    "last_test_at": None,
    "last_test_status": None,
    "last_test_message": None,
}


class AgentLlmSettingsValidationError(ValueError):
    pass


def redact_api_key(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 12:
        return f"{value[:4]}..."
    return f"{value[:6]}...{value[-4:]}"


def public_agent_llm_settings(document: dict[str, Any] | None) -> dict[str, Any]:
    merged = {**DEFAULT_AGENT_LLM_SETTINGS, **(document or {})}
    data = serialize_doc(merged)
    api_key = str(data.get("api_key") or "")
    data["api_key_configured"] = bool(api_key)
    data["api_key_preview"] = redact_api_key(api_key)
    data.pop("api_key", None)
    return data


async def get_agent_llm_settings(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    document = await _find_agent_llm_settings_document(db)
    return public_agent_llm_settings(document)


async def get_agent_llm_settings_private(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    document = await _find_agent_llm_settings_document(db)
    return {**DEFAULT_AGENT_LLM_SETTINGS, **(document or {})}


async def get_agent_llm_runtime_settings(db: AsyncIOMotorDatabase | None = None) -> Any:
    env = get_settings()
    document = await _find_agent_llm_settings_document(db) if db is not None else None
    has_database_config = document is not None
    config = {**DEFAULT_AGENT_LLM_SETTINGS, **(document or {})}
    enabled = bool(config.get("enabled")) if has_database_config else True

    base_url = _choose_config_value(config.get("base_url"), env.agent_llm_base_url, enabled=enabled)
    api_key = _choose_config_value(config.get("api_key"), env.agent_llm_api_key, enabled=enabled)
    level1_model = _choose_config_value(config.get("level1_model"), env.agent_level1_model, enabled=enabled)
    level2_model = _choose_config_value(config.get("level2_model"), getattr(env, "agent_level2_model", None) or env.agent_level0_model, enabled=enabled)
    return SimpleNamespace(
        agent_llm_enabled=enabled,
        agent_llm_source="database" if has_database_config else "environment",
        agent_llm_base_url=base_url,
        agent_llm_api_key=api_key,
        agent_level1_model=level1_model,
        agent_level1_temperature=_choose_float(config.get("level1_temperature"), env.agent_level1_temperature),
        agent_level2_model=level2_model,
        agent_level2_temperature=_choose_float(config.get("level2_temperature"), getattr(env, "agent_level2_temperature", None) or env.agent_level0_temperature),
        # Compatibility names used by the current Agent LLM layer.
        agent_level0_model=level2_model,
        agent_level0_temperature=_choose_float(config.get("level2_temperature"), getattr(env, "agent_level2_temperature", None) or env.agent_level0_temperature),
        agent_request_timeout_seconds=_choose_int(config.get("timeout_seconds"), env.agent_request_timeout_seconds),
    )


async def update_agent_llm_settings(
    db: AsyncIOMotorDatabase,
    *,
    payload: AgentLlmSettingsUpdate,
    actor: dict[str, Any],
) -> dict[str, Any]:
    existing = await get_agent_llm_settings_private(db)
    now = now_utc()
    updates: dict[str, Any] = {
        "enabled": payload.enabled,
        "base_url": _clean_optional(payload.base_url),
        "level1_model": _clean_optional(payload.level1_model),
        "level1_temperature": payload.level1_temperature,
        "level2_model": _clean_optional(payload.level2_model),
        "level2_temperature": payload.level2_temperature,
        "timeout_seconds": payload.timeout_seconds,
        "loop_enabled": payload.loop_enabled,
        "loop_interval_seconds": payload.loop_interval_seconds,
        "updated_by": actor.get("_id"),
        "updated_at": now,
    }
    api_key = _clean_optional(payload.api_key)
    final_api_key = api_key or existing.get("api_key")
    if payload.enabled:
        _validate_enabled_settings(
            base_url=updates["base_url"],
            api_key=final_api_key,
            level1_model=updates["level1_model"],
        )
    if api_key:
        updates["api_key"] = api_key
    elif existing.get("api_key"):
        updates["api_key"] = existing.get("api_key")

    await _agent_llm_settings_collection(db).update_one(
        {"_id": AGENT_LLM_SETTINGS_ID},
        {
            "$set": updates,
            "$setOnInsert": {"created_at": now, "created_by": actor.get("_id")},
        },
        upsert=True,
    )
    return await get_agent_llm_settings(db)


async def update_agent_llm_test_result(
    db: AsyncIOMotorDatabase,
    *,
    ok: bool,
    message: str,
) -> dict[str, Any]:
    now = now_utc()
    await _agent_llm_settings_collection(db).update_one(
        {"_id": AGENT_LLM_SETTINGS_ID},
        {
            "$set": {
                "last_test_at": now,
                "last_test_status": "success" if ok else "failed",
                "last_test_message": message,
                "updated_at": now,
            },
            "$setOnInsert": {"created_at": now},
        },
        upsert=True,
    )
    return await get_agent_llm_settings(db)


def _agent_llm_settings_collection(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_LLM_SETTINGS_COLLECTION]


async def _find_agent_llm_settings_document(db: AsyncIOMotorDatabase) -> dict[str, Any] | None:
    document = await _agent_llm_settings_collection(db).find_one({"_id": AGENT_LLM_SETTINGS_ID})
    if document:
        return document
    # Compatibility fallback for configs saved before Agent got its own collection.
    return await db.app_settings.find_one({"_id": AGENT_LLM_SETTINGS_ID})


def _clean_optional(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _choose_config_value(database_value: Any, env_value: Any, *, enabled: bool) -> str | None:
    if not enabled:
        return None
    database_text = _clean_optional(str(database_value)) if database_value is not None else None
    if database_text:
        return database_text
    env_text = _clean_optional(str(env_value)) if env_value is not None else None
    return env_text


def _choose_float(database_value: Any, env_value: Any) -> float:
    try:
        if database_value is not None:
            return float(database_value)
    except (TypeError, ValueError):
        pass
    try:
        return float(env_value)
    except (TypeError, ValueError):
        return 0.2


def _choose_int(database_value: Any, env_value: Any) -> int:
    try:
        if database_value is not None:
            return int(database_value)
    except (TypeError, ValueError):
        pass
    try:
        return int(env_value)
    except (TypeError, ValueError):
        return 60


def _validate_enabled_settings(*, base_url: Any, api_key: Any, level1_model: Any) -> None:
    missing: list[str] = []
    if not str(base_url or "").strip():
        missing.append("base_url")
    if not str(api_key or "").strip():
        missing.append("api_key")
    if not str(level1_model or "").strip():
        missing.append("level1_model")
    if missing:
        raise AgentLlmSettingsValidationError(f"Agent LLM enabled requires: {', '.join(missing)}")
