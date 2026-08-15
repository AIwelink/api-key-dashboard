from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PlanType = Literal["work", "temporary_unavailable"]
OperationType = Literal["activate", "cancel"]
EndTime = time | Literal["24:00"]
_MUTABLE_UPDATE_FIELDS = {"plan_type", "start_time", "end_time", "note"}


def _trim_note(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


def _reject_timezone(value: time | str | None) -> time | str | None:
    if isinstance(value, time) and value.tzinfo is not None:
        raise ValueError("时间不能包含时区，请使用 Asia/Shanghai 当地时间")
    return value


class WorkPlanCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_type: PlanType
    dates: list[date] = Field(min_length=1, max_length=366)
    start_time: time
    end_time: EndTime
    note: str | None = Field(default=None, max_length=500)
    idempotency_key: UUID

    @field_validator("note", mode="before")
    @classmethod
    def trim_note(cls, value: object) -> object:
        return _trim_note(value)

    @field_validator("start_time", "end_time")
    @classmethod
    def reject_timezone(cls, value: time | str) -> time | str:
        return _reject_timezone(value)


class WorkPlanOperationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation_type: OperationType
    anchor_dates: list[date] = Field(min_length=1, max_length=366)
    start_offset_minute: int = Field(ge=0, le=2_850)
    end_offset_minute: int = Field(ge=30, le=2_880)
    note: str | None = Field(default=None, max_length=500)
    idempotency_key: UUID

    @field_validator("note", mode="before")
    @classmethod
    def trim_note(cls, value: object) -> object:
        return _trim_note(value)

    @model_validator(mode="after")
    def validate_interval(self) -> "WorkPlanOperationCreate":
        if self.start_offset_minute % 30 or self.end_offset_minute % 30:
            raise ValueError("时间必须以 30 分钟为间隔")
        if self.end_offset_minute <= self.start_offset_minute:
            raise ValueError("结束时间必须晚于开始时间")
        return self


class WorkPlanUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_type: PlanType | None = None
    start_time: time | None = None
    end_time: EndTime | None = None
    note: str | None = Field(default=None, max_length=500)
    expected_updated_at: datetime | None = None

    @field_validator("note", mode="before")
    @classmethod
    def trim_note(cls, value: object) -> object:
        return _trim_note(value)

    @field_validator("plan_type", "start_time", "end_time", mode="before")
    @classmethod
    def reject_explicit_null(cls, value: object) -> object:
        if value is None:
            raise ValueError("更新字段不能为 null")
        return value

    @field_validator("start_time", "end_time")
    @classmethod
    def reject_timezone(cls, value: time | str | None) -> time | str | None:
        return _reject_timezone(value)

    @model_validator(mode="after")
    def require_mutable_field(self) -> "WorkPlanUpdate":
        if not (_MUTABLE_UPDATE_FIELDS & self.model_fields_set):
            raise ValueError("至少提供一个可更新字段")
        return self
