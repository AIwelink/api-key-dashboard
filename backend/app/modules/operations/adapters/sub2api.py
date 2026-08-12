from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.modules.operations.adapters.base import (
    CreditEventInput,
    UsageFactInput,
    UserSnapshotInput,
    datetime_value,
    decimal_value,
    required_datetime,
)


USERS_QUERY = """
SELECT id, email, username, status, balance, created_at, updated_at
FROM users
WHERE deleted_at IS NULL
ORDER BY id
"""

USAGE_QUERY = """
SELECT id, user_id, actual_cost, created_at
FROM usage_logs
WHERE user_id IS NOT NULL AND created_at >= :since_at
ORDER BY created_at, id
"""

PAYMENT_ORDERS_QUERY = """
SELECT id, user_id, amount, pay_amount, status, paid_at, completed_at, updated_at,
       refund_amount, refund_at, order_type
FROM payment_orders
WHERE status = 'COMPLETED'
  AND completed_at IS NOT NULL
  AND (
      updated_at >= :since_at
      OR paid_at >= :since_at
      OR completed_at >= :since_at
      OR refund_at >= :since_at
  )
ORDER BY updated_at, id
"""

REDEEM_CODES_QUERY = """
SELECT id, used_by, value, type, used_at, notes
FROM redeem_codes
WHERE used_by IS NOT NULL AND used_at IS NOT NULL AND used_at >= :since_at
ORDER BY used_at, id
"""


class Sub2ApiOperationsAdapter:
    def __init__(self, *, site_id: str) -> None:
        self.site_id = site_id

    def map_user(self, row: dict[str, Any]) -> UserSnapshotInput:
        return UserSnapshotInput(
            site_id=self.site_id,
            external_user_id=str(row["id"]),
            account_label=str(row.get("email") or row.get("username") or row["id"]),
            registered_at=datetime_value(row.get("created_at")),
            account_status=str(row.get("status") or "unknown"),
            balance_units=decimal_value(row.get("balance")),
            source_created_at=datetime_value(row.get("created_at")),
            source_updated_at=datetime_value(row.get("updated_at")),
        )

    def map_usage(self, row: dict[str, Any]) -> UsageFactInput:
        occurred_at = required_datetime(row.get("created_at"))
        return UsageFactInput(
            site_id=self.site_id,
            external_user_id=str(row["user_id"]),
            source_type="usage_logs",
            source_record_id=str(row["id"]),
            successful_call_count=1,
            consumed_balance_units=max(decimal_value(row.get("actual_cost")), Decimal("0")),
            occurred_at=occurred_at,
            source_updated_at=occurred_at,
        )

    def map_payment_order(self, row: dict[str, Any]) -> list[CreditEventInput]:
        paid_at = required_datetime(row.get("paid_at") or row.get("completed_at"))
        source_updated_at = datetime_value(row.get("updated_at")) or paid_at
        facts = [
            CreditEventInput(
                site_id=self.site_id,
                external_user_id=str(row["user_id"]),
                source_type="payment",
                source_record_id=str(row["id"]),
                direction="credit",
                purpose="sale",
                classification_status="classified",
                balance_units=max(decimal_value(row.get("amount")), Decimal("0")),
                cash_amount_cny=max(decimal_value(row.get("pay_amount")), Decimal("0")),
                occurred_at=paid_at,
                source_updated_at=source_updated_at,
                source_metadata={"order_type": str(row.get("order_type") or "")},
            )
        ]
        refund_amount = max(decimal_value(row.get("refund_amount")), Decimal("0"))
        refund_at = datetime_value(row.get("refund_at"))
        if refund_amount > 0 and refund_at is not None:
            facts.append(
                CreditEventInput(
                    site_id=self.site_id,
                    external_user_id=str(row["user_id"]),
                    source_type="refund",
                    source_record_id=f"{row['id']}:refund",
                    direction="debit",
                    purpose="sale",
                    classification_status="classified",
                    balance_units=Decimal("0"),
                    cash_amount_cny=refund_amount,
                    occurred_at=refund_at,
                    source_updated_at=source_updated_at,
                    source_metadata={"payment_order_id": str(row["id"])},
                )
            )
        return facts

    def map_redemption(self, row: dict[str, Any]) -> CreditEventInput:
        used_at = required_datetime(row.get("used_at"))
        return CreditEventInput(
            site_id=self.site_id,
            external_user_id=str(row["used_by"]),
            source_type="redemption",
            source_record_id=str(row["id"]),
            direction="credit",
            purpose=None,
            classification_status="pending",
            balance_units=max(decimal_value(row.get("value")), Decimal("0")),
            cash_amount_cny=Decimal("0"),
            occurred_at=used_at,
            source_updated_at=used_at,
            source_metadata={
                "redemption_type": str(row.get("type") or ""),
                "notes": str(row.get("notes") or ""),
            },
        )

    async def read_users(
        self,
        *,
        connection: Any,
        since: datetime,
    ) -> list[UserSnapshotInput]:
        del since
        result = await connection.execute(text(USERS_QUERY))
        return [self.map_user(dict(row)) for row in result.mappings().all()]

    async def read_usage(
        self,
        *,
        connection: Any,
        since: datetime,
    ) -> list[UsageFactInput]:
        result = await connection.execute(text(USAGE_QUERY), {"since_at": since})
        return [self.map_usage(dict(row)) for row in result.mappings().all()]

    async def read_credit_events(
        self,
        *,
        connection: Any,
        since: datetime,
    ) -> list[CreditEventInput]:
        parameters = {"since_at": since}
        payment_result = await connection.execute(text(PAYMENT_ORDERS_QUERY), parameters)
        redemption_result = await connection.execute(text(REDEEM_CODES_QUERY), parameters)
        facts = [
            fact
            for row in payment_result.mappings().all()
            for fact in self.map_payment_order(dict(row))
        ]
        facts.extend(self.map_redemption(dict(row)) for row in redemption_result.mappings().all())
        return facts
