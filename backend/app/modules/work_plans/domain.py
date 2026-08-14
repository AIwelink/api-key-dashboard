from __future__ import annotations

import json
from datetime import UTC, date, datetime, time, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from app.modules.work_plans.schemas import WorkPlanCreate, WorkPlanUpdate


SHANGHAI_TIMEZONE = timezone(timedelta(hours=8), name="Asia/Shanghai")


class WorkPlanRuleError(ValueError):
    """Raised when a work plan violates a business rule."""


class WorkPlanConflictError(WorkPlanRuleError):
    """Raised when an existing plan can no longer be changed as requested."""


def time_to_minute(value: time) -> int:
    if value.second != 0 or value.microsecond != 0 or value.minute not in {0, 30}:
        raise WorkPlanRuleError("时间必须以 30 分钟为间隔，且不能包含秒")
    return value.hour * 60 + value.minute


def deterministic_plan_id(
    member_id: str,
    idempotency_key: UUID | str,
    plan_date: date,
) -> str:
    key_text = str(idempotency_key).strip()
    try:
        key_text = str(UUID(key_text))
    except ValueError:
        pass
    identity = json.dumps(
        [str(member_id), key_text, plan_date.isoformat()],
        ensure_ascii=True,
        separators=(",", ":"),
    )
    return str(uuid5(NAMESPACE_URL, identity))


def is_plan_manager(actor: dict) -> bool:
    return actor.get("role") in {"owner", "admin"}


def build_plan_drafts(
    actor: dict,
    payload: WorkPlanCreate,
    observed_at: datetime,
) -> list[dict]:
    actor_id = _actor_id(actor)
    actor_name = _actor_name(actor, actor_id)
    observed_utc = _as_utc(observed_at, field_name="observed_at")
    start_minute = time_to_minute(payload.start_time)
    end_minute = time_to_minute(payload.end_time)
    _validate_time_window(start_minute, end_minute)

    dates = _normalize_dates(payload.dates)
    if payload.plan_type == "temporary_unavailable":
        if len(dates) != 1:
            raise WorkPlanRuleError("临时不可用计划只能选择 1 个日期")
        _validate_temporary_unavailable_start(
            plan_date=dates[0],
            start_time=payload.start_time,
            observed_at=observed_utc,
        )

    note = _normalize_note(payload.note)
    idempotency_key = str(payload.idempotency_key)
    return [
        {
            "_id": deterministic_plan_id(actor_id, payload.idempotency_key, plan_date),
            "member_id": actor_id,
            "member_name": actor_name,
            "plan_date": plan_date.isoformat(),
            "plan_type": payload.plan_type,
            "start_minute": start_minute,
            "end_minute": end_minute,
            "note": note,
            "status": "active",
            "is_cancelled": False,
            "idempotency_key": idempotency_key,
            "created_by": actor_id,
            "updated_by": actor_id,
            "created_at": observed_utc,
            "updated_at": observed_utc,
        }
        for plan_date in dates
    ]


def validate_update(
    existing: dict,
    payload: WorkPlanUpdate,
    observed_at: datetime,
) -> dict:
    if existing.get("is_cancelled") is True or existing.get("status") == "cancelled":
        raise WorkPlanConflictError("已取消的计划不能更新")

    if payload.expected_updated_at is not None:
        current_updated_at = existing.get("updated_at")
        if not isinstance(current_updated_at, datetime) or _as_utc(
            payload.expected_updated_at,
            field_name="expected_updated_at",
        ) != _as_utc(current_updated_at, field_name="updated_at"):
            raise WorkPlanConflictError("计划已被更新，请刷新后重试")

    fields_set = payload.model_fields_set
    plan_type = (
        payload.plan_type
        if "plan_type" in fields_set and payload.plan_type is not None
        else existing["plan_type"]
    )
    start_minute = (
        time_to_minute(payload.start_time)
        if "start_time" in fields_set and payload.start_time is not None
        else existing["start_minute"]
    )
    end_minute = (
        time_to_minute(payload.end_time)
        if "end_time" in fields_set and payload.end_time is not None
        else existing["end_minute"]
    )
    _validate_time_window(start_minute, end_minute)

    if plan_type == "temporary_unavailable":
        _validate_temporary_unavailable_start(
            plan_date=date.fromisoformat(existing["plan_date"]),
            start_time=_minute_to_time(start_minute),
            observed_at=_as_utc(observed_at, field_name="observed_at"),
        )

    note = (
        _normalize_note(payload.note)
        if "note" in fields_set
        else _normalize_note(existing.get("note"))
    )
    return {
        "plan_type": plan_type,
        "start_minute": start_minute,
        "end_minute": end_minute,
        "note": note,
        "updated_at": _as_utc(observed_at, field_name="observed_at"),
    }


def _actor_id(actor: dict) -> str:
    actor_id = str(actor.get("_id") or actor.get("id") or actor.get("email") or "").strip()
    if not actor_id:
        raise WorkPlanRuleError("无法识别计划成员")
    return actor_id


def _actor_name(actor: dict, actor_id: str) -> str:
    return str(actor.get("name") or actor.get("email") or actor_id).strip() or actor_id


def _as_utc(value: datetime, *, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise WorkPlanRuleError(f"{field_name} 必须包含时区")
    return value.astimezone(UTC)


def _normalize_dates(values: list[date]) -> list[date]:
    if len(set(values)) != len(values):
        raise WorkPlanRuleError("计划日期不能重复")
    if len(values) > 5:
        raise WorkPlanRuleError("一次最多添加 5 天计划，请缩小日期范围")
    if not values:
        raise WorkPlanRuleError("请至少选择 1 个计划日期")
    return sorted(values)


def _normalize_note(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _validate_time_window(start_minute: int, end_minute: int) -> None:
    if end_minute <= start_minute:
        raise WorkPlanRuleError("结束时间必须晚于开始时间")


def _validate_temporary_unavailable_start(
    *,
    plan_date: date,
    start_time: time,
    observed_at: datetime,
) -> None:
    local_start = datetime.combine(plan_date, start_time, tzinfo=SHANGHAI_TIMEZONE)
    if local_start < observed_at + timedelta(hours=1):
        raise WorkPlanRuleError("临时不可用计划的开始时间至少晚于当前时间 1 小时")


def _minute_to_time(value: int) -> time:
    hour, minute = divmod(value, 60)
    return time(hour, minute)
