from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Sequence


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
    "project_operations",
]
