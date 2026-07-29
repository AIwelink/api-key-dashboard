from __future__ import annotations

import argparse
import asyncio
import json
import secrets
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.agent import eval_runner
from app.modules.agent.eval_runner import (
    AGENT_EVAL_RESULTS_COLLECTION,
    AGENT_EVAL_RUNS_COLLECTION,
    AGENT_EVAL_RUN_SCHEMA_VERSION,
)
from app.utils import now_utc, serialize_doc


async def run_agent_eval_suite(
    db: AsyncIOMotorDatabase,
    *,
    suite: str = "default",
    case_ids: list[str] | None = None,
    category: str | None = None,
    actor: dict[str, Any] | None = None,
    mode: str = "llm_live",
    persist: bool = True,
) -> dict[str, Any]:
    """Run an Agent eval suite and optionally persist run/results."""

    started_at = now_utc()
    eval_run_id = _new_id()
    cases = load_agent_eval_cases(suite=suite)
    if category:
        cases = [item for item in cases if str(item.get("category") or "") == str(category)]
    if case_ids:
        allowed = {str(item) for item in case_ids if str(item).strip()}
        cases = [item for item in cases if str(item.get("case_id") or "") in allowed]

    results = [
        await run_agent_eval_case(
            db,
            case=case,
            actor=actor,
            eval_run_id=eval_run_id,
            mode=mode,
        )
        for case in cases
    ]
    passed = sum(1 for item in results if item.get("status") == "passed")
    failed = sum(1 for item in results if item.get("status") == "failed")
    finished_at = now_utc()
    run_doc = {
        "_id": eval_run_id,
        "eval_run_id": eval_run_id,
        "schema_version": AGENT_EVAL_RUN_SCHEMA_VERSION,
        "suite": suite,
        "category": category,
        "mode": mode,
        "status": "success" if failed == 0 else "failed",
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_ms": _duration_ms(started_at, finished_at),
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "score": round(passed / len(results), 4) if results else 0,
        },
        "results": results,
        "created_by": _actor_id(actor),
        "created_at": started_at,
        "updated_at": finished_at,
    }
    if persist:
        await db[AGENT_EVAL_RUNS_COLLECTION].insert_one({**run_doc, "results": [_result_ref(item) for item in results]})
        if results:
            await db[AGENT_EVAL_RESULTS_COLLECTION].insert_many(results)
    return serialize_doc(run_doc)


async def get_agent_eval_run(
    db: AsyncIOMotorDatabase,
    *,
    eval_run_id: str,
) -> dict[str, Any] | None:
    normalized_eval_run_id = str(eval_run_id or "").strip()
    if not normalized_eval_run_id:
        return None
    run = await db[AGENT_EVAL_RUNS_COLLECTION].find_one(
        {"eval_run_id": normalized_eval_run_id}
    ) or await db[AGENT_EVAL_RUNS_COLLECTION].find_one({"_id": normalized_eval_run_id})
    if not run:
        return None
    results = [
        item
        async for item in db[AGENT_EVAL_RESULTS_COLLECTION]
        .find({"eval_run_id": normalized_eval_run_id})
        .sort([("category", 1), ("case_id", 1)])
    ]
    return serialize_doc({**run, "results": results})


async def run_agent_eval_case(
    db: AsyncIOMotorDatabase,
    *,
    case: dict[str, Any],
    actor: dict[str, Any] | None = None,
    eval_run_id: str | None = None,
    mode: str = "llm_live",
) -> dict[str, Any]:
    """Run one Agent eval case."""

    del actor
    normalized_mode = _normalize_mode(mode)
    if normalized_mode == "llm_live":
        return await eval_runner._run_eval_case(db, case=case, eval_run_id=eval_run_id or _new_id())  # noqa: SLF001 - public facade over local runner internals.

    mock_output = ((case.get("input") or {}).get("mock_output") if isinstance(case.get("input"), dict) else None) or case.get("mock_output")
    if not isinstance(mock_output, dict):
        return _mock_missing_result(case=case, eval_run_id=eval_run_id or _new_id(), mode=normalized_mode)
    evaluated = evaluate_agent_output(case=case, output=mock_output)
    started_at = now_utc()
    return serialize_doc(
        {
            "_id": f"{eval_run_id or _new_id()}:{case.get('case_id') or _new_id()}",
            "eval_run_id": eval_run_id,
            "schema_version": eval_runner.AGENT_EVAL_RESULT_SCHEMA_VERSION,
            "case_id": case.get("case_id"),
            "category": case.get("category"),
            "description": case.get("description"),
            "status": evaluated["status"],
            "score": evaluated["score"],
            "mode": normalized_mode,
            "assertions": evaluated["assertions"],
            "output_summary": evaluated["output_summary"],
            "failure_reasons": evaluated["failure_reasons"],
            "started_at": started_at,
            "finished_at": started_at,
            "duration_ms": 0,
            "created_at": started_at,
        }
    )


def load_agent_eval_cases(*, suite: str = "default") -> list[dict[str, Any]]:
    """Load eval cases from backend/app/modules/agent/eval_cases."""

    normalized_suite = str(suite or "default").strip() or "default"
    cases = eval_runner._load_eval_cases()  # noqa: SLF001 - shared case loader.
    if normalized_suite == "all":
        return cases
    if normalized_suite == "default":
        return [item for item in cases if _case_suite(item) == "default"]
    return [item for item in cases if _case_suite(item) == normalized_suite]


def evaluate_agent_output(*, case: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    """Evaluate one already-produced output against a case definition."""

    assertions = eval_runner._evaluate_assertions(case=case, output=output)  # noqa: SLF001 - shared assertion engine.
    failure_reasons = [str(item.get("reason")) for item in assertions if not item.get("passed")]
    score = eval_runner._case_score(assertions)  # noqa: SLF001
    min_score = eval_runner._case_min_score(case)  # noqa: SLF001
    critical_ok = eval_runner._critical_assertions_ok(case=case, assertions=assertions)  # noqa: SLF001
    status = "passed" if score >= min_score and critical_ok else "failed"
    return {
        "status": status,
        "score": score,
        "assertions": assertions,
        "output_summary": eval_runner._output_summary(output),  # noqa: SLF001
        "failure_reasons": failure_reasons,
    }


def _case_suite(case: dict[str, Any]) -> str:
    suite = case.get("suite")
    if isinstance(suite, str) and suite.strip():
        return suite.strip()
    return "default"


def _normalize_mode(mode: str | None) -> str:
    normalized = str(mode or "llm_live").strip()
    return normalized if normalized in {"llm_live", "llm_mock"} else "llm_live"


def _mock_missing_result(*, case: dict[str, Any], eval_run_id: str, mode: str) -> dict[str, Any]:
    started_at = now_utc()
    reason = "llm_mock requires input.mock_output or case.mock_output"
    return serialize_doc(
        {
            "_id": f"{eval_run_id}:{case.get('case_id') or _new_id()}",
            "eval_run_id": eval_run_id,
            "schema_version": eval_runner.AGENT_EVAL_RESULT_SCHEMA_VERSION,
            "case_id": case.get("case_id"),
            "category": case.get("category"),
            "description": case.get("description"),
            "status": "failed",
            "score": 0,
            "mode": mode,
            "assertions": [{"name": "mock_output_present", "type": "llm_mock", "passed": False, "reason": reason}],
            "output_summary": {},
            "failure_reasons": [reason],
            "started_at": started_at,
            "finished_at": started_at,
            "duration_ms": 0,
            "created_at": started_at,
        }
    )


def _result_ref(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": item.get("case_id"),
        "category": item.get("category"),
        "status": item.get("status"),
        "score": item.get("score"),
    }


def _duration_ms(started_at: Any, finished_at: Any) -> int | None:
    if not started_at or not finished_at:
        return None
    try:
        return int((finished_at - started_at).total_seconds() * 1000)
    except Exception:
        return None


def _actor_id(actor: dict[str, Any] | None) -> str | None:
    if not actor:
        return None
    value = actor.get("_id") or actor.get("email")
    return str(value).strip() if value else None


def _new_id() -> str:
    return secrets.token_hex(12)


async def _main_async() -> int:
    parser = argparse.ArgumentParser(description="Run Agent eval suites.")
    parser.add_argument("--suite", default="default")
    parser.add_argument("--category", default=None)
    parser.add_argument("--case-id", action="append", default=None)
    parser.add_argument("--mode", choices=["llm_live", "llm_mock"], default="llm_live")
    parser.add_argument("--no-persist", action="store_true")
    parser.add_argument("--list-cases", action="store_true")
    args = parser.parse_args()

    if args.list_cases:
        print(json.dumps({"items": [eval_runner._case_view(item) for item in load_agent_eval_cases(suite=args.suite)]}, ensure_ascii=False, default=str, indent=2))  # noqa: SLF001
        return 0

    await connect_to_mongo()
    try:
        result = await run_agent_eval_suite(
            get_db(),
            suite=args.suite,
            category=args.category,
            case_ids=args.case_id,
            mode=args.mode,
            persist=not args.no_persist,
            actor={"_id": "agent_eval_cli"},
        )
        print(json.dumps(result, ensure_ascii=False, default=str, indent=2))
        return 0 if result.get("status") == "success" else 1
    finally:
        await close_mongo_connection()


def main() -> None:
    raise SystemExit(asyncio.run(_main_async()))


if __name__ == "__main__":
    main()
