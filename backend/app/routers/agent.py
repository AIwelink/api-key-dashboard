from pydantic import BaseModel, Field

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.modules.system.permissions import require_any_view_permission, require_view_permission
from app.modules.agent.capacity import list_agent_pools
from app.modules.agent.chat import analyze_pool, chat
from app.modules.agent.memory import get_agent_latest_state, list_agent_messages, list_agent_runs, list_agent_steps
from app.modules.agent.evals import get_agent_eval_run, run_agent_eval_suite
from app.modules.agent.eval_runner import list_agent_eval_cases, list_agent_eval_results, list_agent_eval_runs
from app.modules.agent.event_triggers import list_agent_event_triggers
from app.modules.agent.notification_dispatcher import dispatch_agent_alert_draft, list_agent_notifications
from app.modules.agent.patrol import list_agent_patrol_runs, run_agent_patrol_once
from app.modules.agent.reviewer import review_agent_decision
from app.modules.agent.scheduler import get_agent_scheduler_status, list_agent_scheduler_ticks, run_agent_scheduler_tick, set_agent_scheduler_enabled
from app.modules.agent.long_term_memory import (
    generate_pool_daily_memory_summary,
    generate_pool_weekly_memory_summary,
    get_agent_memory_summary,
    list_agent_memory_summaries,
)
from app.modules.agent.task_scheduler import run_task_followup
from app.modules.agent.tasks import (
    append_agent_task_feedback,
    get_agent_task,
    list_agent_tasks,
    mark_due_agent_tasks_review_due,
    review_agent_task,
    transition_agent_task,
)
from app.modules.system.audit import write_audit_log
from app.modules.agent.tools import tool_manifest


router = APIRouter(prefix="/agent", tags=["agent"])


class AgentChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    pool_id: str | None = None
    conversation_id: str | None = None


class AgentDecisionReviewRequest(BaseModel):
    review_window_hours: int = Field(default=24, ge=1, le=168)


class AgentTaskFeedbackRequest(BaseModel):
    feedback: str | None = Field(default=None, min_length=1, max_length=2000)
    message: str | None = Field(default=None, min_length=1, max_length=2000)
    feedback_type: str | None = Field(default=None, max_length=80)
    target_status: str | None = Field(default=None, max_length=80)
    next_status: str | None = Field(default=None, max_length=80)
    reason: str | None = Field(default=None, max_length=500)
    conversation_id: str | None = None
    run_id: str | None = None


class AgentTaskTransitionRequest(BaseModel):
    target_status: str | None = Field(default=None, min_length=1, max_length=80)
    next_status: str | None = Field(default=None, min_length=1, max_length=80)
    reason: str = Field(min_length=1, max_length=500)
    run_id: str | None = None
    decision_id: str | None = None
    next_check_minutes: int | None = Field(default=None, ge=5, le=1440)
    review_after_hours: int | None = Field(default=None, ge=1, le=168)


class AgentTaskReviewRequest(BaseModel):
    review_window_hours: int = Field(default=24, ge=1, le=168)


class AgentTaskDispatchAlertRequest(BaseModel):
    force: bool = False


class AgentMarkReviewDueRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=200)


class AgentMemoryDailyRequest(BaseModel):
    site_id: str | None = None
    pool_id: str = Field(min_length=1, max_length=200)
    date: str = Field(min_length=4, max_length=32)


class AgentMemoryWeeklyRequest(BaseModel):
    site_id: str | None = None
    pool_id: str = Field(min_length=1, max_length=200)
    week_start: str = Field(min_length=4, max_length=32)
    week_end: str = Field(min_length=4, max_length=32)


class AgentTaskRunFollowupRequest(BaseModel):
    trigger: str = Field(default="scheduler_task_due", min_length=1, max_length=80)
    lock_ttl_seconds: int = Field(default=300, ge=30, le=3600)


class AgentEvalRunRequest(BaseModel):
    suite: str = Field(default="default", min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=120)
    case_id: str | None = Field(default=None, max_length=200)
    case_ids: list[str] | None = None
    mode: str = Field(default="llm_live", pattern="^(llm_live|llm_mock)$")
    persist: bool = True


class AgentPatrolRunRequest(BaseModel):
    pool_id: str | None = Field(default=None, max_length=200)
    site_id: str | None = Field(default=None, max_length=120)
    limit: int = Field(default=3, ge=0, le=100)


@router.get("/tools")
async def get_agent_tools(
    _: dict = Depends(require_any_view_permission("agent-analysis", "agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await tool_manifest(db)


@router.get("/pools")
async def get_agent_pools(
    _: dict = Depends(require_any_view_permission("agent-analysis", "system-management")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_pools(db)


@router.get("/state")
async def get_agent_state(
    pool_id: str | None = None,
    _: dict = Depends(require_view_permission("agent-analysis")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_agent_latest_state(db, pool_id=pool_id)


@router.get("/runs")
async def get_agent_runs(
    pool_id: str | None = None,
    trigger: str | None = None,
    limit: int = 20,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_runs(db, pool_id=pool_id, trigger=trigger, limit=max(1, min(limit, 100)))


@router.get("/conversations/{conversation_id}/messages")
async def get_agent_conversation_messages(
    conversation_id: str,
    limit: int = 50,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_messages(db, conversation_id=conversation_id, limit=max(1, min(limit, 200)))


@router.get("/tasks")
async def get_agent_tasks(
    pool_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_tasks(db, pool_id=pool_id, status=status, limit=max(1, min(limit, 200)))


@router.get("/scheduler/status")
async def get_agent_scheduler_status_route(
    _: dict = Depends(require_view_permission("agent-analysis")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_agent_scheduler_status(db)


@router.get("/scheduler/ticks")
async def get_agent_scheduler_ticks(
    status: str | None = None,
    reason: str | None = None,
    limit: int = 50,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_scheduler_ticks(db, status=status, reason=reason, limit=max(1, min(limit, 200)))


@router.post("/scheduler/tick")
async def post_agent_scheduler_tick(
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await run_agent_scheduler_tick(db, reason="manual", actor=actor)
    await write_audit_log(
        db,
        actor=actor,
        action="agent.scheduler.tick",
        resource_type="agent_scheduler_tick",
        resource_id=result.get("tick_id") or result.get("_id"),
        after={
            "tick_id": result.get("tick_id"),
            "status": result.get("status"),
            "skip_reason": result.get("skip_reason"),
            "errors": result.get("errors"),
        },
    )
    return result


@router.post("/scheduler/pause")
async def post_agent_scheduler_pause(
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await set_agent_scheduler_enabled(db, enabled=False, actor=actor)
    await write_audit_log(
        db,
        actor=actor,
        action="agent.scheduler.pause",
        resource_type="agent_scheduler",
        resource_id="agent_loop",
        after={"enabled": result.get("enabled")},
    )
    return result


@router.post("/scheduler/resume")
async def post_agent_scheduler_resume(
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await set_agent_scheduler_enabled(db, enabled=True, actor=actor)
    await write_audit_log(
        db,
        actor=actor,
        action="agent.scheduler.resume",
        resource_type="agent_scheduler",
        resource_id="agent_loop",
        after={"enabled": result.get("enabled")},
    )
    return result


@router.post("/tasks/review-due/mark")
async def post_agent_mark_review_due_tasks(
    payload: AgentMarkReviewDueRequest | None = None,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await mark_due_agent_tasks_review_due(
        db,
        limit=(payload.limit if payload else 50),
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="agent.task.mark_review_due",
        resource_type="agent_task",
        resource_id=None,
        after=result,
    )
    return result


@router.post("/patrol/run")
async def post_agent_patrol_run(
    payload: AgentPatrolRunRequest | None = None,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    request = payload or AgentPatrolRunRequest()
    result = await run_agent_patrol_once(
        db,
        pool_id=request.pool_id,
        site_id=request.site_id,
        limit=request.limit,
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="agent.patrol.run",
        resource_type="agent_patrol",
        resource_id=request.pool_id or request.site_id,
        after={
            "pool_id": request.pool_id,
            "site_id": request.site_id,
            "limit": request.limit,
            "total_processed": result.get("total_processed"),
            "total_skipped": result.get("total_skipped"),
            "total_errors": result.get("total_errors"),
        },
    )
    return result


@router.get("/patrol/runs")
async def get_agent_patrol_runs(
    pool_id: str | None = None,
    site_id: str | None = None,
    status: str | None = None,
    limit: int = 50,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_patrol_runs(
        db,
        pool_id=pool_id,
        site_id=site_id,
        status=status,
        limit=max(1, min(limit, 200)),
    )


@router.get("/event-triggers")
async def get_agent_event_triggers(
    site_id: str | None = None,
    pool_id: str | None = None,
    signal: str | None = None,
    status: str | None = None,
    limit: int = 50,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_event_triggers(
        db,
        site_id=site_id,
        pool_id=pool_id,
        signal=signal,
        status=status,
        limit=max(1, min(limit, 200)),
    )


@router.get("/notifications")
async def get_agent_notifications(
    status: str | None = None,
    limit: int = 50,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_notifications(db, status=status, limit=max(1, min(limit, 200)))


@router.get("/evals/cases")
async def get_agent_eval_cases(
    category: str | None = None,
    case_id: str | None = None,
    _: dict = Depends(require_view_permission("agent-workbench")),
) -> dict:
    return await list_agent_eval_cases(category=category, case_id=case_id)


@router.post("/evals/run")
async def post_agent_eval_run(
    payload: AgentEvalRunRequest | None = None,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    request = payload or AgentEvalRunRequest()
    case_ids = request.case_ids or ([request.case_id] if request.case_id else None)
    result = await run_agent_eval_suite(
        db,
        suite=request.suite,
        case_ids=case_ids,
        category=request.category,
        mode=request.mode,
        persist=request.persist,
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="agent.eval.run",
        resource_type="agent_eval_run",
        resource_id=result.get("eval_run_id"),
        after={"summary": result.get("summary"), "status": result.get("status"), "persist": request.persist},
    )
    return result


@router.get("/evals/runs")
async def get_agent_eval_runs(
    status: str | None = None,
    category: str | None = None,
    limit: int = 50,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_eval_runs(db, status=status, category=category, limit=max(1, min(limit, 200)))


@router.get("/evals/runs/{eval_run_id}")
async def get_agent_eval_run_route(
    eval_run_id: str,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await get_agent_eval_run(db, eval_run_id=eval_run_id)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent eval run not found")
    return result


@router.get("/evals/results")
async def get_agent_eval_results(
    eval_run_id: str | None = None,
    case_id: str | None = None,
    category: str | None = None,
    status: str | None = None,
    limit: int = 100,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_eval_results(
        db,
        eval_run_id=eval_run_id,
        case_id=case_id,
        category=category,
        status=status,
        limit=max(1, min(limit, 500)),
    )


@router.post("/memory/daily")
async def post_agent_memory_daily(
    payload: AgentMemoryDailyRequest,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await generate_pool_daily_memory_summary(
        db,
        site_id=payload.site_id,
        pool_id=payload.pool_id,
        date=payload.date,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="agent.memory.daily",
        resource_type="agent_memory_summary",
        resource_id=result.get("memory_id") or result.get("_id"),
        after={
            "memory_id": result.get("memory_id"),
            "memory_type": result.get("memory_type"),
            "pool_id": result.get("pool_id"),
            "period_start": result.get("period_start"),
            "period_end": result.get("period_end"),
        },
    )
    return result


@router.get("/memory")
async def get_agent_memory(
    site_id: str | None = None,
    pool_id: str | None = None,
    memory_type: str | None = None,
    limit: int = 50,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_memory_summaries(
        db,
        site_id=site_id,
        pool_id=pool_id,
        memory_type=memory_type,
        limit=max(1, min(limit, 200)),
    )


@router.get("/memory/{memory_id}")
async def get_agent_memory_detail(
    memory_id: str,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    memory = await get_agent_memory_summary(db, memory_id=memory_id)
    if not memory:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent memory summary not found")
    return memory


@router.post("/memory/weekly")
async def post_agent_memory_weekly(
    payload: AgentMemoryWeeklyRequest,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await generate_pool_weekly_memory_summary(
        db,
        site_id=payload.site_id,
        pool_id=payload.pool_id,
        week_start=payload.week_start,
        week_end=payload.week_end,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="agent.memory.weekly",
        resource_type="agent_memory_summary",
        resource_id=result.get("memory_id") or result.get("_id"),
        after={
            "memory_id": result.get("memory_id"),
            "memory_type": result.get("memory_type"),
            "pool_id": result.get("pool_id"),
            "period_start": result.get("period_start"),
            "period_end": result.get("period_end"),
        },
    )
    return result


@router.get("/tasks/{task_id}")
async def get_agent_task_detail(
    task_id: str,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    task = await get_agent_task(db, task_id=task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent task not found")
    return task


@router.post("/tasks/{task_id}/feedback")
async def post_agent_task_feedback(
    task_id: str,
    payload: AgentTaskFeedbackRequest,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    feedback_message = (payload.feedback or payload.message or "").strip()
    next_status = (payload.target_status or payload.next_status or "").strip() or None
    if not feedback_message:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="feedback or message is required")
    try:
        task = await append_agent_task_feedback(
            db,
            task_id=task_id,
            feedback=feedback_message,
            feedback_type=payload.feedback_type,
            target_status=next_status,
            reason=payload.reason,
            run_id=payload.run_id,
            conversation_id=payload.conversation_id,
            actor=actor,
            write_memory=True,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent task not found")
    await write_audit_log(
        db,
        actor=actor,
        action="agent.task.feedback",
        resource_type="agent_task",
        resource_id=task_id,
        after={
            "task_id": task_id,
            "status": task.get("status"),
            "feedback_result": task.get("feedback_result"),
            "next_status": next_status,
        },
    )
    return task


@router.post("/tasks/{task_id}/transition")
async def post_agent_task_transition(
    task_id: str,
    payload: AgentTaskTransitionRequest,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    next_status = (payload.target_status or payload.next_status or "").strip()
    if not next_status:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="next_status or target_status is required")
    extra_step_result = {
        "next_check_minutes": payload.next_check_minutes,
        "review_after_hours": payload.review_after_hours,
    }
    try:
        task = await transition_agent_task(
            db,
            task_id=task_id,
            target_status=next_status,
            reason=payload.reason,
            run_id=payload.run_id,
            decision_id=payload.decision_id,
            actor=actor,
            extra_step_result=extra_step_result,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent task not found")
    await write_audit_log(
        db,
        actor=actor,
        action="agent.task.transition",
        resource_type="agent_task",
        resource_id=task_id,
        after={
            "task_id": task_id,
            "status": task.get("status"),
            "reason": payload.reason,
            "next_status": next_status,
        },
    )
    return task


@router.post("/tasks/{task_id}/review")
async def post_agent_task_review(
    task_id: str,
    payload: AgentTaskReviewRequest | None = None,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    try:
        result = await review_agent_task(
            db,
            task_id=task_id,
            actor=actor,
            review_window_hours=(payload.review_window_hours if payload else 24),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent task not found")
    task = result.get("task") if isinstance(result.get("task"), dict) else {}
    review = result.get("review") if isinstance(result.get("review"), dict) else {}
    await write_audit_log(
        db,
        actor=actor,
        action="agent.task.review",
        resource_type="agent_task",
        resource_id=task_id,
        after={
            "task_id": task_id,
            "status": task.get("status"),
            "review_result": review.get("review_result"),
            "memory_id": review.get("memory_id"),
        },
    )
    return result


@router.post("/tasks/{task_id}/run-followup")
async def post_agent_task_run_followup(
    task_id: str,
    payload: AgentTaskRunFollowupRequest | None = None,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    task = await get_agent_task(db, task_id=task_id)
    if not task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent task not found")
    result = await run_task_followup(
        db,
        task=task,
        trigger=(payload.trigger if payload else "scheduler_task_due"),
        scheduler_tick_id="manual_followup",
        lock_ttl_seconds=(payload.lock_ttl_seconds if payload else 300),
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="agent.task.run_followup",
        resource_type="agent_task",
        resource_id=task_id,
        after=result,
    )
    if not result.get("processed"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=result.get("reason") or "Agent task follow-up was not processed")
    return result


@router.post("/tasks/{task_id}/dispatch-alert")
async def post_agent_task_dispatch_alert(
    task_id: str,
    payload: AgentTaskDispatchAlertRequest | None = None,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await dispatch_agent_alert_draft(
        db,
        task_id=task_id,
        actor=actor,
        manual_confirmed=True,
        force=bool(payload.force) if payload else False,
    )
    if result.get("reason") == "task_not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent task not found")
    if not result.get("sent"):
        detail = result.get("reason") or "Agent alert draft was not dispatched"
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
    await write_audit_log(
        db,
        actor=actor,
        action="agent.task.dispatch_alert",
        resource_type="agent_task",
        resource_id=task_id,
        after={
            "task_id": task_id,
            "notification_event_id": result.get("notification_event_id"),
            "sent": result.get("sent"),
        },
    )
    return result


@router.get("/runs/{run_id}/steps")
async def get_agent_run_steps(
    run_id: str,
    limit: int = 50,
    _: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_agent_steps(db, run_id=run_id, limit=max(1, min(limit, 200)))


@router.post("/decisions/{decision_id}/review")
async def post_agent_decision_review(
    decision_id: str,
    payload: AgentDecisionReviewRequest | None = None,
    actor: dict = Depends(require_view_permission("agent-workbench")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    review = await review_agent_decision(
        db,
        decision_id=decision_id,
        actor=actor,
        review_window_hours=(payload.review_window_hours if payload else 24),
    )
    await write_audit_log(
        db,
        actor=actor,
        action="agent.decision.review",
        resource_type="agent_decision",
        resource_id=decision_id,
        after={
            "decision_id": decision_id,
            "memory_id": review.get("memory_id"),
            "review_result": review.get("review_result"),
        },
    )
    return review


@router.post("/pools/{pool_id}/analyze")
async def analyze_agent_pool(
    pool_id: str,
    actor: dict = Depends(require_view_permission("agent-analysis")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await analyze_pool(db, pool_id=pool_id, actor=actor)
    await _write_agent_run_audit(db, actor=actor, action="agent.analyze", result=result)
    return result


@router.post("/chat")
async def post_agent_chat(
    payload: AgentChatRequest,
    actor: dict = Depends(require_view_permission("agent-analysis")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await chat(db, message=payload.message, pool_id=payload.pool_id, actor=actor, conversation_id=payload.conversation_id)
    await _write_agent_run_audit(db, actor=actor, action="agent.chat", result=result)
    return result


async def _write_agent_run_audit(
    db: AsyncIOMotorDatabase,
    *,
    actor: dict,
    action: str,
    result: dict,
) -> None:
    run_id = str(result.get("run_id") or "")
    pool = result.get("pool") if isinstance(result.get("pool"), dict) else {}
    await write_audit_log(
        db,
        actor=actor,
        action=action,
        resource_type="agent_run",
        resource_id=run_id or None,
        after={
            "run_id": run_id or None,
            "conversation_id": result.get("conversation_id"),
            "decision_id": result.get("decision_id"),
            "pool_id": pool.get("id") or result.get("pool_id"),
            "severity": result.get("severity"),
            "trigger": result.get("trigger"),
        },
    )
