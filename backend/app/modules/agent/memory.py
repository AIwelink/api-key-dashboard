from __future__ import annotations

import secrets
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.agent.triggers import build_trigger_metadata, normalize_agent_trigger
from app.utils import now_utc, serialize_doc


AGENT_RUNS_COLLECTION = "agent_runs"
AGENT_MESSAGES_COLLECTION = "agent_messages"
AGENT_DECISIONS_COLLECTION = "agent_decisions"
AGENT_RUN_STEPS_COLLECTION = "agent_run_steps"
AGENT_RUN_STEP_SCHEMA_VERSION = "agent_run_step.v1"

RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"
STEP_STATUS_RUNNING = "running"
STEP_STATUS_SUCCESS = "success"
STEP_STATUS_FAILED = "failed"

MESSAGE_ROLES = {"user", "assistant", "system"}
DEFAULT_MESSAGE_LIMIT = 50
DEFAULT_RUN_LIMIT = 20
MAX_MESSAGE_LIMIT = 200
MAX_RUN_LIMIT = 100


async def create_agent_run(
    db: AsyncIOMotorDatabase,
    *,
    trigger: str,
    actor: dict[str, Any] | None,
    pool_id: str | None = None,
    site_id: str | None = None,
    user_message: str | None = None,
    conversation_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now_utc()
    run_id = _new_id()
    normalized_conversation_id = _clean_optional_string(conversation_id) or run_id
    normalized_trigger = normalize_agent_trigger(trigger)
    trigger_metadata = build_trigger_metadata(
        trigger=normalized_trigger,
        metadata=metadata,
        pool_id=pool_id,
        site_id=site_id,
    )
    document = {
        "_id": run_id,
        "run_id": run_id,
        "trigger": normalized_trigger,
        "status": RUN_STATUS_RUNNING,
        "conversation_id": normalized_conversation_id,
        "pool_id": _clean_optional_string(pool_id),
        "site_id": _clean_optional_string(site_id),
        "user_message": _clean_optional_string(user_message),
        "started_at": now,
        "finished_at": None,
        "duration_ms": None,
        "llm": {},
        "agent": {},
        "context_pack_summary": {},
        "decision_mode": None,
        "validator": {},
        "summary": None,
        "severity": None,
        "decision_id": None,
        "error": None,
        "metadata": trigger_metadata,
        "trigger_metadata": trigger_metadata,
        "created_by": _actor_id(actor),
        "created_at": now,
        "updated_at": now,
    }
    await _runs(db).insert_one(document)
    return serialize_doc(document)


async def finish_agent_run(
    db: AsyncIOMotorDatabase,
    *,
    run_id: str,
    report: dict[str, Any],
    decision_id: str | None = None,
) -> dict[str, Any] | None:
    existing = await _runs(db).find_one({"_id": run_id})
    if not existing:
        return None
    finished_at = now_utc()
    resolved_decision_id = decision_id or _clean_optional_string(report.get("decision_id"))
    updates = {
        "status": RUN_STATUS_SUCCESS,
        "finished_at": finished_at,
        "duration_ms": _duration_ms(existing.get("started_at"), finished_at),
        "llm": report.get("llm") if isinstance(report.get("llm"), dict) else {},
        "agent": report.get("agent") if isinstance(report.get("agent"), dict) else {},
        "context_pack_summary": _context_pack_summary_from_report(report),
        "decision_mode": _decision_mode_from_report(report),
        "validator": report.get("validator") if isinstance(report.get("validator"), dict) else _validator_from_report(report),
        "summary": _report_summary(report),
        "severity": report.get("severity"),
        "decision_id": resolved_decision_id,
        "pool_id": _pool_id_from_report(report) or existing.get("pool_id"),
        "site_id": _site_id_from_report(report) or existing.get("site_id"),
        "error": None,
        "updated_at": finished_at,
    }
    await _runs(db).update_one({"_id": run_id}, {"$set": updates})
    document = await _runs(db).find_one({"_id": run_id})
    return serialize_doc(document) if document else None


async def fail_agent_run(
    db: AsyncIOMotorDatabase,
    *,
    run_id: str,
    error: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    existing = await _runs(db).find_one({"_id": run_id})
    if not existing:
        return None
    finished_at = now_utc()
    updates: dict[str, Any] = {
        "status": RUN_STATUS_FAILED,
        "finished_at": finished_at,
        "duration_ms": _duration_ms(existing.get("started_at"), finished_at),
        "error": _clean_optional_string(error) or "Agent run failed",
        "updated_at": finished_at,
    }
    if metadata:
        updates["failure_metadata"] = metadata
    await _runs(db).update_one({"_id": run_id}, {"$set": updates})
    document = await _runs(db).find_one({"_id": run_id})
    return serialize_doc(document) if document else None


async def append_agent_message(
    db: AsyncIOMotorDatabase,
    *,
    conversation_id: str,
    role: str,
    content: str,
    run_id: str | None = None,
    pool_id: str | None = None,
    site_id: str | None = None,
    actor: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_conversation_id = _clean_optional_string(conversation_id)
    normalized_content = _clean_optional_string(content)
    normalized_role = _clean_optional_string(role)
    if not normalized_conversation_id:
        raise ValueError("conversation_id is required")
    if normalized_role not in MESSAGE_ROLES:
        raise ValueError(f"Unsupported Agent message role: {role}")
    if not normalized_content:
        raise ValueError("message content is required")

    now = now_utc()
    message_id = _new_id()
    document = {
        "_id": message_id,
        "message_id": message_id,
        "conversation_id": normalized_conversation_id,
        "run_id": _clean_optional_string(run_id),
        "pool_id": _clean_optional_string(pool_id),
        "site_id": _clean_optional_string(site_id),
        "role": normalized_role,
        "content": normalized_content,
        "metadata": metadata or {},
        "created_by": _actor_id(actor),
        "created_at": now,
    }
    await _messages(db).insert_one(document)
    return serialize_doc(document)


async def save_agent_decision(
    db: AsyncIOMotorDatabase,
    *,
    run_id: str,
    conversation_id: str,
    report: dict[str, Any],
    actor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now_utc()
    decision_id = _new_id()
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    llm_output = report.get("llm") if isinstance(report.get("llm"), dict) else {}
    document = {
        "_id": decision_id,
        "decision_id": decision_id,
        "run_id": _clean_optional_string(run_id),
        "conversation_id": _clean_optional_string(conversation_id),
        "pool_id": _pool_id_from_report(report),
        "site_id": _site_id_from_report(report),
        "severity": report.get("severity"),
        "headline": report.get("headline"),
        "summary": _report_summary(report),
        "decision": decision,
        "decision_mode": _decision_mode_from_report(report),
        "context_pack_summary": _context_pack_summary_from_report(report),
        "validator": report.get("validator") if isinstance(report.get("validator"), dict) else _validator_from_report(report),
        "reasons": report.get("reasons") if isinstance(report.get("reasons"), list) else [],
        "suggested_actions": report.get("suggested_actions") if isinstance(report.get("suggested_actions"), list) else [],
        "capacity_snapshot": report.get("capacity") if isinstance(report.get("capacity"), dict) else {},
        "probe_snapshot": report.get("probe") if isinstance(report.get("probe"), dict) else {},
        "llm_output": llm_output,
        "agent": report.get("agent") if isinstance(report.get("agent"), dict) else {},
        "chat": report.get("chat") if isinstance(report.get("chat"), dict) else {},
        "read_only": bool(report.get("read_only", True)),
        "trigger": report.get("trigger"),
        "requires_human_confirm": bool(decision.get("manual_review_required")),
        "created_by": _actor_id(actor),
        "created_at": now,
    }
    await _decisions(db).insert_one(document)
    return serialize_doc(document)


async def get_agent_latest_state(db: AsyncIOMotorDatabase, *, pool_id: str | None = None) -> dict[str, Any]:
    normalized_pool_id = _clean_optional_string(pool_id)
    query = {"pool_id": normalized_pool_id} if normalized_pool_id else {}
    latest_run = await _runs(db).find_one(query, sort=[("created_at", -1)])
    latest_decision = await _decisions(db).find_one(query, sort=[("created_at", -1)])
    messages: list[dict[str, Any]] = []
    conversation_id = None
    if latest_decision and latest_decision.get("conversation_id"):
        conversation_id = latest_decision.get("conversation_id")
    if not conversation_id:
        latest_message = await _messages(db).find_one(query, sort=[("created_at", -1)])
        conversation_id = latest_message.get("conversation_id") if latest_message else None
    if conversation_id:
        messages = [
            item
            async for item in _messages(db).find({"conversation_id": conversation_id}).sort("created_at", -1).limit(50)
        ]
        messages.reverse()
    running_count = await _runs(db).count_documents({"status": RUN_STATUS_RUNNING, **query})
    return {
        "latest_run": serialize_doc(latest_run) if latest_run else None,
        "latest_decision": serialize_doc(latest_decision) if latest_decision else None,
        "messages": serialize_doc(messages),
        "running": running_count > 0,
        "running_count": running_count,
    }


async def list_agent_messages(db: AsyncIOMotorDatabase, *, conversation_id: str, limit: int = 50) -> dict[str, Any]:
    normalized_conversation_id = _clean_optional_string(conversation_id)
    if not normalized_conversation_id:
        return {"items": [], "total": 0}
    normalized_limit = _normalize_limit(limit, default=DEFAULT_MESSAGE_LIMIT, maximum=MAX_MESSAGE_LIMIT)
    query = {"conversation_id": normalized_conversation_id}
    items = [item async for item in _messages(db).find(query).sort("created_at", -1).limit(normalized_limit)]
    items.reverse()
    total = await _messages(db).count_documents(query)
    return {"items": serialize_doc(items), "total": total}


async def list_agent_runs(
    db: AsyncIOMotorDatabase,
    *,
    pool_id: str | None = None,
    trigger: str | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    normalized_pool_id = _clean_optional_string(pool_id)
    normalized_trigger = _clean_optional_string(trigger)
    query: dict[str, Any] = {}
    if normalized_pool_id:
        query["pool_id"] = normalized_pool_id
    if normalized_trigger:
        query["trigger"] = normalized_trigger
    normalized_limit = _normalize_limit(limit, default=DEFAULT_RUN_LIMIT, maximum=MAX_RUN_LIMIT)
    items = [item async for item in _runs(db).find(query).sort("created_at", -1).limit(normalized_limit)]
    total = await _runs(db).count_documents(query)
    return {"items": serialize_doc(items), "total": total}


async def create_agent_step(
    db: AsyncIOMotorDatabase,
    *,
    run_id: str,
    step_index: int,
    step_type: str,
    conversation_id: str | None = None,
    task_id: str | None = None,
    intent: str | None = None,
    input_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized_run_id = _clean_optional_string(run_id)
    if not normalized_run_id:
        raise ValueError("run_id is required")
    now = now_utc()
    step_id = _new_id()
    document = {
        "_id": step_id,
        "schema_version": AGENT_RUN_STEP_SCHEMA_VERSION,
        "step_id": step_id,
        "run_id": normalized_run_id,
        "conversation_id": _clean_optional_string(conversation_id),
        "task_id": _clean_optional_string(task_id),
        "step_index": int(step_index),
        "step_type": _clean_optional_string(step_type) or "unknown",
        "status": STEP_STATUS_RUNNING,
        "intent": _clean_optional_string(intent),
        "input_summary": input_summary or {},
        "output_summary": {},
        "llm": {},
        "capability_calls": [],
        "started_at": now,
        "finished_at": None,
        "duration_ms": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
    }
    await _steps(db).insert_one(document)
    return serialize_doc(document)


async def finish_agent_step(
    db: AsyncIOMotorDatabase,
    *,
    step_id: str,
    output_summary: dict[str, Any] | None = None,
    llm: dict[str, Any] | None = None,
    capability_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    existing = await _steps(db).find_one({"_id": step_id})
    if not existing:
        return None
    finished_at = now_utc()
    updates = {
        "status": STEP_STATUS_SUCCESS,
        "output_summary": output_summary or {},
        "llm": llm or {},
        "capability_calls": capability_calls or [],
        "finished_at": finished_at,
        "duration_ms": _duration_ms(existing.get("started_at"), finished_at),
        "error": None,
        "updated_at": finished_at,
    }
    await _steps(db).update_one({"_id": step_id}, {"$set": updates})
    document = await _steps(db).find_one({"_id": step_id})
    return serialize_doc(document) if document else None


async def fail_agent_step(
    db: AsyncIOMotorDatabase,
    *,
    step_id: str,
    error: str,
    output_summary: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    existing = await _steps(db).find_one({"_id": step_id})
    if not existing:
        return None
    finished_at = now_utc()
    updates = {
        "status": STEP_STATUS_FAILED,
        "output_summary": output_summary or {},
        "finished_at": finished_at,
        "duration_ms": _duration_ms(existing.get("started_at"), finished_at),
        "error": _clean_optional_string(error) or "Agent step failed",
        "updated_at": finished_at,
    }
    await _steps(db).update_one({"_id": step_id}, {"$set": updates})
    document = await _steps(db).find_one({"_id": step_id})
    return serialize_doc(document) if document else None


async def list_agent_steps(db: AsyncIOMotorDatabase, *, run_id: str, limit: int = 50) -> dict[str, Any]:
    normalized_run_id = _clean_optional_string(run_id)
    if not normalized_run_id:
        return {"items": [], "total": 0}
    normalized_limit = _normalize_limit(limit, default=50, maximum=200)
    query = {"run_id": normalized_run_id}
    items = [item async for item in _steps(db).find(query).sort("step_index", 1).limit(normalized_limit)]
    total = await _steps(db).count_documents(query)
    return {"items": serialize_doc(items), "total": total}


def _runs(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_RUNS_COLLECTION]


def _messages(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_MESSAGES_COLLECTION]


def _decisions(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_DECISIONS_COLLECTION]


def _steps(db: AsyncIOMotorDatabase) -> Any:
    return db[AGENT_RUN_STEPS_COLLECTION]


def _actor_id(actor: dict[str, Any] | None) -> Any:
    actor_id = actor.get("_id") if actor else None
    return str(actor_id) if actor_id is not None else None


def _new_id() -> str:
    return secrets.token_hex(12)


def _clean_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _normalize_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, maximum))


def _duration_ms(started_at: Any, finished_at: Any) -> int | None:
    if not started_at:
        return None
    try:
        return int((finished_at - started_at).total_seconds() * 1000)
    except Exception:
        return None


def _report_summary(report: dict[str, Any]) -> str | None:
    llm = report.get("llm") if isinstance(report.get("llm"), dict) else {}
    for value in (llm.get("message"), llm.get("operator_message"), report.get("headline")):
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _context_pack_summary_from_report(report: dict[str, Any]) -> dict[str, Any]:
    agent = report.get("agent") if isinstance(report.get("agent"), dict) else {}
    context_pack = agent.get("context_pack") if isinstance(agent.get("context_pack"), dict) else {}
    if context_pack:
        return context_pack
    return {
        "schema_version": report.get("context_pack_version"),
        "target_pool": _pool_summary_from_report(report),
    }


def _pool_summary_from_report(report: dict[str, Any]) -> dict[str, Any]:
    pool = report.get("pool") if isinstance(report.get("pool"), dict) else {}
    return {
        "pool_id": pool.get("id"),
        "site_id": pool.get("site_id"),
        "group_id": pool.get("active_group_id") or pool.get("group_id"),
        "name": pool.get("name"),
        "account_type": pool.get("account_type"),
    }


def _decision_mode_from_report(report: dict[str, Any]) -> str | None:
    if _clean_optional_string(report.get("decision_mode")):
        return _clean_optional_string(report.get("decision_mode"))
    agent = report.get("agent") if isinstance(report.get("agent"), dict) else {}
    return _clean_optional_string(agent.get("decision_mode") or agent.get("mode"))


def _validator_from_report(report: dict[str, Any]) -> dict[str, Any]:
    decision = report.get("decision") if isinstance(report.get("decision"), dict) else {}
    validator = decision.get("validator") if isinstance(decision.get("validator"), dict) else {}
    if validator:
        return validator
    agent = report.get("agent") if isinstance(report.get("agent"), dict) else {}
    validator = agent.get("validator") if isinstance(agent.get("validator"), dict) else {}
    return validator


def _pool_id_from_report(report: dict[str, Any]) -> str | None:
    pool = report.get("pool") if isinstance(report.get("pool"), dict) else {}
    pool_id = pool.get("id") or report.get("pool_id")
    return str(pool_id) if pool_id is not None else None


def _site_id_from_report(report: dict[str, Any]) -> str | None:
    pool = report.get("pool") if isinstance(report.get("pool"), dict) else {}
    capacity = report.get("capacity") if isinstance(report.get("capacity"), dict) else {}
    site_id = pool.get("site_id") or capacity.get("site_id") or report.get("site_id")
    return str(site_id) if site_id is not None else None
