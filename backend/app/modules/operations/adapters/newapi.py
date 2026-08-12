from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text

from app.modules.operations.adapters.base import (
    CreditEventInput,
    SubscriptionEntitlementInput,
    UsageFactInput,
    UserSnapshotInput,
    datetime_value,
    decimal_value,
    required_datetime,
)


USERS_QUERY = """
SELECT id, username, email, display_name, status, quota, created_at, last_login_at
FROM users
WHERE deleted_at IS NULL
ORDER BY id
"""

USAGE_QUERY = """
SELECT id, user_id, count, quota, model_name, token_used, created_at
FROM quota_data
WHERE user_id IS NOT NULL AND created_at >= UNIX_TIMESTAMP(:since_at)
ORDER BY created_at, id
"""

SUBSCRIPTION_ENTITLEMENTS_QUERY = """
SELECT id, user_id, start_time, end_time, status, updated_at
FROM user_subscriptions
WHERE start_time > 0 AND end_time > start_time
ORDER BY id
"""

TOP_UPS_QUERY = """
SELECT id, user_id, amount, money, complete_time, create_time
FROM top_ups
WHERE complete_time > 0 AND complete_time >= UNIX_TIMESTAMP(:since_at)
ORDER BY complete_time, id
"""

SUBSCRIPTION_ORDERS_QUERY = """
SELECT id, user_id, money, complete_time, create_time
FROM subscription_orders
WHERE complete_time > 0 AND complete_time >= UNIX_TIMESTAMP(:since_at)
ORDER BY complete_time, id
"""

REDEMPTIONS_QUERY = """
SELECT id, used_user_id, quota, redeemed_time, name
FROM redemptions
WHERE used_user_id > 0 AND redeemed_time > 0
  AND redeemed_time >= UNIX_TIMESTAMP(:since_at)
ORDER BY redeemed_time, id
"""


class NewApiOperationsAdapter:
    def __init__(self, *, site_id: str, quota_per_unit: Decimal) -> None:
        if quota_per_unit <= 0:
            raise ValueError("quota_per_unit must be greater than zero")
        self.site_id = site_id
        self.quota_per_unit = quota_per_unit

    def _balance_units(self, quota: Any) -> Decimal:
        return decimal_value(quota) / self.quota_per_unit

    def map_user(self, row: dict[str, Any]) -> UserSnapshotInput:
        created_at = datetime_value(row.get("created_at"), unix=True)
        return UserSnapshotInput(
            site_id=self.site_id,
            external_user_id=str(row["id"]),
            account_label=str(
                row.get("email") or row.get("display_name") or row.get("username") or row["id"]
            ),
            registered_at=created_at,
            account_status=str(row.get("status") if row.get("status") is not None else "unknown"),
            balance_units=max(self._balance_units(row.get("quota")), Decimal("0")),
            source_created_at=created_at,
            source_updated_at=datetime_value(row.get("last_login_at"), unix=True) or created_at,
        )

    def map_usage(self, row: dict[str, Any]) -> UsageFactInput:
        occurred_at = required_datetime(row.get("created_at"), unix=True)
        return UsageFactInput(
            site_id=self.site_id,
            external_user_id=str(row["user_id"]),
            source_type="quota_data",
            source_record_id=str(row["id"]),
            successful_call_count=max(int(row.get("count") or 1), 1),
            consumed_balance_units=max(self._balance_units(row.get("quota")), Decimal("0")),
            occurred_at=occurred_at,
            source_updated_at=occurred_at,
            billed_amount_cny=max(self._balance_units(row.get("quota")), Decimal("0")),
            model_name=str(row.get("model_name") or ""),
            token_count=max(int(row.get("token_used") or 0), 0),
        )

    def map_subscription_entitlement(
        self,
        row: dict[str, Any],
    ) -> SubscriptionEntitlementInput:
        return SubscriptionEntitlementInput(
            site_id=self.site_id,
            external_user_id=str(row["user_id"]),
            source_type="user_subscription",
            source_record_id=str(row["id"]),
            starts_at=required_datetime(row.get("start_time"), unix=True),
            ends_at=required_datetime(row.get("end_time"), unix=True),
            status=str(row.get("status") or "unknown"),
            source_updated_at=datetime_value(row.get("updated_at"), unix=True),
        )

    def map_top_up(self, row: dict[str, Any]) -> CreditEventInput:
        complete_time = required_datetime(row.get("complete_time"), unix=True)
        return CreditEventInput(
            site_id=self.site_id,
            external_user_id=str(row["user_id"]),
            source_type="payment",
            source_record_id=f"top_up:{row['id']}",
            direction="credit",
            purpose="sale",
            classification_status="classified",
            balance_units=max(self._balance_units(row.get("amount")), Decimal("0")),
            cash_amount_cny=max(decimal_value(row.get("money")), Decimal("0")),
            occurred_at=complete_time,
            source_updated_at=complete_time,
            source_metadata={"payment_source": "top_up"},
        )

    def map_subscription_order(self, row: dict[str, Any]) -> CreditEventInput:
        complete_time = required_datetime(row.get("complete_time"), unix=True)
        return CreditEventInput(
            site_id=self.site_id,
            external_user_id=str(row["user_id"]),
            source_type="payment",
            source_record_id=f"subscription_order:{row['id']}",
            direction="credit",
            purpose="sale",
            classification_status="classified",
            balance_units=Decimal("0"),
            cash_amount_cny=max(decimal_value(row.get("money")), Decimal("0")),
            occurred_at=complete_time,
            source_updated_at=complete_time,
            source_metadata={"payment_source": "subscription_order"},
        )

    def map_redemption(self, row: dict[str, Any]) -> CreditEventInput:
        redeemed_time = required_datetime(row.get("redeemed_time"), unix=True)
        return CreditEventInput(
            site_id=self.site_id,
            external_user_id=str(row["used_user_id"]),
            source_type="redemption",
            source_record_id=str(row["id"]),
            direction="credit",
            purpose=None,
            classification_status="pending",
            balance_units=max(self._balance_units(row.get("quota")), Decimal("0")),
            cash_amount_cny=Decimal("0"),
            occurred_at=redeemed_time,
            source_updated_at=redeemed_time,
            source_metadata={"name": str(row.get("name") or "")},
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
        top_up_result = await connection.execute(text(TOP_UPS_QUERY), parameters)
        subscription_result = await connection.execute(
            text(SUBSCRIPTION_ORDERS_QUERY), parameters
        )
        redemption_result = await connection.execute(text(REDEMPTIONS_QUERY), parameters)
        facts = [self.map_top_up(dict(row)) for row in top_up_result.mappings().all()]
        facts.extend(
            self.map_subscription_order(dict(row))
            for row in subscription_result.mappings().all()
        )
        facts.extend(self.map_redemption(dict(row)) for row in redemption_result.mappings().all())
        return facts

    async def read_subscription_entitlements(
        self,
        *,
        connection: Any,
    ) -> list[SubscriptionEntitlementInput]:
        result = await connection.execute(text(SUBSCRIPTION_ENTITLEMENTS_QUERY))
        return [
            self.map_subscription_entitlement(dict(row))
            for row in result.mappings().all()
        ]
