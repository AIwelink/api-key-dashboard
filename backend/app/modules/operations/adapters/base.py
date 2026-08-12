from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class UserSnapshotInput:
    site_id: str
    external_user_id: str
    account_label: str
    registered_at: datetime | None
    account_status: str
    balance_units: Decimal | None
    source_created_at: datetime | None
    source_updated_at: datetime | None


@dataclass(frozen=True)
class UsageFactInput:
    site_id: str
    external_user_id: str
    source_type: str
    source_record_id: str
    successful_call_count: int
    consumed_balance_units: Decimal
    occurred_at: datetime
    source_updated_at: datetime | None
    cost_cny: Decimal = Decimal("0")
    conversion_rate_id: UUID | None = None
    billed_amount_cny: Decimal = Decimal("0")
    model_name: str = ""
    token_count: int = 0


@dataclass(frozen=True)
class SubscriptionEntitlementInput:
    site_id: str
    external_user_id: str
    source_type: str
    source_record_id: str
    starts_at: datetime
    ends_at: datetime
    status: str
    source_updated_at: datetime | None


@dataclass(frozen=True)
class CreditEventInput:
    site_id: str
    external_user_id: str
    source_type: str
    source_record_id: str
    direction: str
    purpose: str | None
    classification_status: str
    balance_units: Decimal
    cash_amount_cny: Decimal
    occurred_at: datetime
    source_updated_at: datetime | None
    source_metadata: dict[str, Any] = field(default_factory=dict)
    conversion_rate_id: UUID | None = None


class OperationsSourceAdapter(Protocol):
    async def read_users(self, *, connection: Any, since: datetime) -> list[UserSnapshotInput]: ...

    async def read_usage(self, *, connection: Any, since: datetime) -> list[UsageFactInput]: ...

    async def read_credit_events(
        self,
        *,
        connection: Any,
        since: datetime,
    ) -> list[CreditEventInput]: ...

    async def read_subscription_entitlements(
        self,
        *,
        connection: Any,
    ) -> list[SubscriptionEntitlementInput]: ...


def decimal_value(value: Any, *, default: str = "0") -> Decimal:
    if value is None or isinstance(value, bool):
        return Decimal(default)
    try:
        return Decimal(str(value))
    except (ValueError, TypeError):
        return Decimal(default)


def datetime_value(value: Any, *, unix: bool = False) -> datetime | None:
    if value in (None, "", 0, "0"):
        return None
    if isinstance(value, datetime):
        parsed = value
    elif unix or isinstance(value, (int, float)):
        try:
            parsed = datetime.fromtimestamp(float(value), tz=UTC)
        except (ValueError, TypeError, OSError):
            return None
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def required_datetime(value: Any, *, unix: bool = False) -> datetime:
    parsed = datetime_value(value, unix=unix)
    if parsed is None:
        raise ValueError("source record has no valid event time")
    return parsed
