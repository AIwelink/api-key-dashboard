from __future__ import annotations

from datetime import date, datetime, time
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


PlanType = Literal["work", "temporary_unavailable"]


class WorkPlanCreate(BaseModel):
    plan_type: PlanType
    dates: list[date] = Field(min_length=1, max_length=366)
    start_time: time
    end_time: time
    note: str | None = Field(default=None, max_length=500)
    idempotency_key: UUID


class WorkPlanUpdate(BaseModel):
    plan_type: PlanType | None = None
    start_time: time | None = None
    end_time: time | None = None
    note: str | None = Field(default=None, max_length=500)
    expected_updated_at: datetime | None = None
