from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Literal


OperationsRangeValue = Literal["today", "7d", "30d", "custom"]
SyncHealth = Literal["running", "healthy", "delayed", "never"]


@dataclass(frozen=True)
class OperationsWindow:
    start_at: datetime
    end_at: datetime
    previous_start_at: datetime
    previous_end_at: datetime


def convert_balance_to_cny(balance_units: Decimal, units_per_cny: Decimal) -> Decimal:
    if units_per_cny <= 0:
        raise ValueError("balance conversion rate must be greater than zero")
    return balance_units / units_per_cny


def normalized_cash_amount(purpose: str | Enum, cash_amount_cny: Decimal) -> Decimal:
    purpose_value = purpose.value if isinstance(purpose, Enum) else purpose
    return cash_amount_cny if purpose_value == "sale" else Decimal("0")


def user_segment(*, is_internal: bool) -> Literal["ordinary", "internal"]:
    return "internal" if is_internal else "ordinary"


def sync_health(
    *,
    now: datetime,
    last_success_at: datetime | None,
    running: bool,
    started_at: datetime | None = None,
    delay_after: timedelta = timedelta(minutes=30),
    running_timeout: timedelta = timedelta(minutes=15),
) -> SyncHealth:
    if running:
        if started_at is not None and now - started_at > running_timeout:
            return "delayed"
        return "running"
    if last_success_at is None:
        return "never"
    if now - last_success_at > delay_after:
        return "delayed"
    return "healthy"


def resolve_operations_window(
    range_value: OperationsRangeValue,
    *,
    now: datetime,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> OperationsWindow:
    if range_value == "custom":
        if start_at is None or end_at is None:
            raise ValueError("custom range requires start_at and end_at")
    elif range_value == "today":
        start_at = now.replace(hour=0, minute=0, second=0, microsecond=0)
        end_at = now
    elif range_value == "7d":
        start_at = now - timedelta(days=7)
        end_at = now
    elif range_value == "30d":
        start_at = now - timedelta(days=30)
        end_at = now
    else:
        raise ValueError("unsupported operations range")

    if start_at >= end_at:
        raise ValueError("start_at must be earlier than end_at")
    duration = end_at - start_at
    return OperationsWindow(
        start_at=start_at,
        end_at=end_at,
        previous_start_at=start_at - duration,
        previous_end_at=start_at,
    )
