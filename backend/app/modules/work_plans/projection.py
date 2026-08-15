from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any, Literal, Sequence


OperationType = Literal["activate", "cancel"]
SegmentState = Literal["active", "cancelled"]
SLOT_MINUTES = 30
SLOT_SECONDS = SLOT_MINUTES * 60


@dataclass(frozen=True, slots=True)
class NormalizedOperation:
    operation_id: str
    member_id: str
    operation_type: str
    start_at: datetime
    end_at: datetime
    order_key: tuple[int, int, str]


@dataclass(frozen=True, slots=True)
class EffectiveSegment:
    state: SegmentState
    start_at: datetime
    end_at: datetime
    winning_operation_id: str
    operation_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _SlotState:
    state: SegmentState
    operation_id: str
    order_key: tuple[int, int, str]


def project_operations(
    operations: Sequence[NormalizedOperation],
    window_start: datetime,
    window_end: datetime,
) -> list[EffectiveSegment]:
    _validate_window(window_start, window_end)
    slot_count = int((window_end - window_start).total_seconds() // SLOT_SECONDS)
    slots: list[_SlotState | None] = [None] * slot_count

    for operation in sorted(operations, key=lambda item: item.order_key):
        _validate_operation(operation)
        overlap_start = max(window_start, operation.start_at)
        overlap_end = min(window_end, operation.end_at)
        if overlap_start >= overlap_end:
            continue
        first_slot = int((overlap_start - window_start).total_seconds() // SLOT_SECONDS)
        last_slot = int((overlap_end - window_start).total_seconds() // SLOT_SECONDS)
        state: SegmentState = "active" if operation.operation_type == "activate" else "cancelled"
        slot_state = _SlotState(state, operation.operation_id, operation.order_key)
        for index in range(first_slot, last_slot):
            if state == "cancelled" and (
                slots[index] is None or slots[index].state != "active"
            ):
                continue
            slots[index] = slot_state

    return _merge_slots(slots, window_start)


def clip_cancellation(
    green_segments: Sequence[EffectiveSegment],
    requested_start: datetime,
    requested_end: datetime,
) -> list[tuple[datetime, datetime]]:
    _validate_window(requested_start, requested_end)
    fragments: list[tuple[datetime, datetime]] = []
    for segment in sorted(green_segments, key=lambda item: (item.start_at, item.end_at)):
        if segment.state != "active":
            continue
        start_at = max(requested_start, segment.start_at)
        end_at = min(requested_end, segment.end_at)
        if start_at >= end_at:
            continue
        if fragments and fragments[-1][1] == start_at:
            fragments[-1] = (fragments[-1][0], end_at)
        else:
            fragments.append((start_at, end_at))
    return fragments


def normalize_v2_operation(document: dict[str, Any]) -> NormalizedOperation:
    return NormalizedOperation(
        operation_id=str(document.get("_id") or ""),
        member_id=str(document.get("member_id") or ""),
        operation_type=str(document.get("operation_type") or ""),
        start_at=document["effective_start_at"],
        end_at=document["effective_end_at"],
        order_key=(
            2,
            int(document.get("member_sequence") or 0),
            str(document.get("_id") or ""),
        ),
    )


def normalize_legacy_records(
    documents: Sequence[dict[str, Any]],
    *,
    local_timezone: timezone = timezone(timedelta(hours=8)),
) -> list[NormalizedOperation]:
    operations: list[NormalizedOperation] = []
    fallback_order = 0
    for document in documents:
        if document.get("schema_version") == 2:
            continue
        member_id = str(document.get("member_id") or "").strip()
        operation_id = str(document.get("_id") or "").strip()
        plan_date_text = str(document.get("plan_date") or "").strip()
        if not member_id or not operation_id or not plan_date_text:
            continue
        try:
            plan_date = date.fromisoformat(plan_date_text)
            start_minute = int(document.get("start_minute") or 0)
            end_minute = int(document.get("end_minute") or 0)
            local_midnight = datetime.combine(plan_date, time.min, tzinfo=local_timezone)
            start_at = (local_midnight + timedelta(minutes=start_minute)).astimezone(UTC)
            end_at = (local_midnight + timedelta(minutes=end_minute)).astimezone(UTC)
        except (TypeError, ValueError):
            continue
        if end_at <= start_at:
            continue

        created_order = _datetime_order(document.get("created_at"), fallback_order)
        fallback_order += 1
        plan_type = document.get("plan_type")
        cancelled = document.get("is_cancelled") is True or document.get("status") == "cancelled"
        if plan_type == "work":
            operations.append(
                NormalizedOperation(
                    operation_id=f"legacy:{operation_id}:activate",
                    member_id=member_id,
                    operation_type="activate",
                    start_at=start_at,
                    end_at=end_at,
                    order_key=(0, created_order, operation_id),
                )
            )
            if cancelled:
                operations.append(
                    NormalizedOperation(
                        operation_id=f"legacy:{operation_id}:cancel",
                        member_id=member_id,
                        operation_type="cancel",
                        start_at=start_at,
                        end_at=end_at,
                        order_key=(1, _datetime_order(document.get("cancelled_at"), fallback_order), operation_id),
                    )
                )
                fallback_order += 1
        elif plan_type == "temporary_unavailable" and not cancelled:
            operations.append(
                NormalizedOperation(
                    operation_id=f"legacy:{operation_id}:cancel",
                    member_id=member_id,
                    operation_type="cancel",
                    start_at=start_at,
                    end_at=end_at,
                    order_key=(1, created_order, operation_id),
                )
            )
    return operations


def sort_members(members: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(members, key=_member_sort_key)


def _member_sort_key(member: dict[str, Any]) -> tuple[Any, ...]:
    priority = member.get("work_plan_priority")
    has_priority = isinstance(priority, int) and not isinstance(priority, bool) and priority > 0
    is_zhang = (
        has_priority
        and priority == 1
        and member.get("role") == "owner"
        and str(member.get("member_name") or "").strip() == "张城玮"
    )
    if member.get("current_green") is True:
        schedule_key = (0, 0.0)
    elif isinstance(member.get("next_green_start"), datetime):
        schedule_key = (1, member["next_green_start"].timestamp())
    elif isinstance(member.get("latest_green_end"), datetime):
        schedule_key = (2, -member["latest_green_end"].timestamp())
    else:
        schedule_key = (3, 0.0)
    return (
        0 if is_zhang else 1,
        0 if has_priority else 1,
        int(priority) if has_priority else 0,
        *schedule_key,
        str(member.get("member_name") or "").casefold(),
        str(member.get("member_id") or ""),
    )


def _datetime_order(value: Any, fallback: int) -> int:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=UTC)
        return int(value.astimezone(UTC).timestamp() * 1_000_000)
    return fallback


def _merge_slots(
    slots: Sequence[_SlotState | None],
    window_start: datetime,
) -> list[EffectiveSegment]:
    segments: list[EffectiveSegment] = []
    index = 0
    while index < len(slots):
        first = slots[index]
        if first is None:
            index += 1
            continue
        end_index = index + 1
        operation_ids = [first.operation_id]
        winner = first
        while end_index < len(slots):
            current = slots[end_index]
            if current is None or current.state != first.state:
                break
            if current.operation_id not in operation_ids:
                operation_ids.append(current.operation_id)
            if current.order_key > winner.order_key:
                winner = current
            end_index += 1
        segments.append(
            EffectiveSegment(
                state=first.state,
                start_at=window_start + timedelta(minutes=index * SLOT_MINUTES),
                end_at=window_start + timedelta(minutes=end_index * SLOT_MINUTES),
                winning_operation_id=winner.operation_id,
                operation_ids=tuple(operation_ids),
            )
        )
        index = end_index
    return segments


def _validate_window(start_at: datetime, end_at: datetime) -> None:
    _require_aware(start_at)
    _require_aware(end_at)
    if end_at <= start_at:
        raise ValueError("结束时间必须晚于开始时间")
    if not _is_half_hour_aligned(start_at) or not _is_half_hour_aligned(end_at):
        raise ValueError("时间必须以 30 分钟为间隔")


def _validate_operation(operation: NormalizedOperation) -> None:
    if operation.operation_type not in {"activate", "cancel"}:
        raise ValueError("无法识别工作计划操作类型")
    if not operation.operation_id or not operation.member_id:
        raise ValueError("无法识别工作计划操作")
    _validate_window(operation.start_at, operation.end_at)


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("时间必须包含时区")


def _is_half_hour_aligned(value: datetime) -> bool:
    return value.second == 0 and value.microsecond == 0 and value.minute in {0, 30}


__all__ = [
    "EffectiveSegment",
    "NormalizedOperation",
    "clip_cancellation",
    "normalize_legacy_records",
    "normalize_v2_operation",
    "project_operations",
    "sort_members",
]
