from __future__ import annotations

import unittest
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class Sub2ApiOperationsAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.modules.operations.adapters.sub2api import Sub2ApiOperationsAdapter

        self.adapter = Sub2ApiOperationsAdapter(site_id="aiwelink")

    def test_user_mapping_excludes_credentials(self) -> None:
        fact = self.adapter.map_user(
            {
                "id": "user-1",
                "email": "user@example.com",
                "username": "user",
                "status": "active",
                "balance": Decimal("20"),
                "created_at": NOW,
                "updated_at": NOW,
                "password_hash": "must-not-leak",
                "totp_secret_encrypted": "must-not-leak",
            }
        )

        mapped = asdict(fact)
        self.assertEqual(mapped["external_user_id"], "user-1")
        self.assertEqual(mapped["balance_units"], Decimal("20"))
        self.assertNotIn("password_hash", mapped)
        self.assertNotIn("totp_secret_encrypted", mapped)

    def test_usage_row_becomes_one_successful_call(self) -> None:
        fact = self.adapter.map_usage(
            {
                "id": 1001,
                "user_id": "user-1",
                "actual_cost": Decimal("2.5"),
                "created_at": NOW,
            }
        )

        self.assertEqual(fact.successful_call_count, 1)
        self.assertEqual(fact.consumed_balance_units, Decimal("2.5"))
        self.assertEqual(fact.source_record_id, "1001")

    def test_paid_order_is_sale_and_refund_is_separate_debit(self) -> None:
        facts = self.adapter.map_payment_order(
            {
                "id": "order-1",
                "user_id": "user-1",
                "amount": Decimal("100"),
                "pay_amount": Decimal("10"),
                "paid_at": NOW,
                "updated_at": NOW,
                "refund_amount": Decimal("3"),
                "refund_at": NOW,
            }
        )

        self.assertEqual(len(facts), 2)
        self.assertEqual(facts[0].purpose, "sale")
        self.assertEqual(facts[0].classification_status, "classified")
        self.assertEqual(facts[0].cash_amount_cny, Decimal("10"))
        self.assertEqual(facts[1].direction, "debit")
        self.assertEqual(facts[1].source_type, "refund")

    def test_used_redeem_code_without_managed_batch_is_pending(self) -> None:
        fact = self.adapter.map_redemption(
            {
                "id": "redeem-1",
                "used_by": "user-1",
                "value": Decimal("100"),
                "type": "balance",
                "used_at": NOW,
                "notes": "offline",
            }
        )

        self.assertEqual(fact.source_type, "redemption")
        self.assertEqual(fact.classification_status, "pending")
        self.assertIsNone(fact.purpose)


class Sub2ApiOperationsAdapterReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_whitelisted_incremental_source_rows(self) -> None:
        from app.modules.operations.adapters.sub2api import Sub2ApiOperationsAdapter

        connection = _FakeConnection(
            [
                [{"id": "u1", "email": "u@example.com", "username": "u", "status": "active", "balance": 1, "created_at": NOW, "updated_at": NOW}],
                [{"id": 1, "user_id": "u1", "actual_cost": 1, "created_at": NOW}],
                [{"id": "p1", "user_id": "u1", "amount": 10, "pay_amount": 1, "paid_at": NOW, "updated_at": NOW, "refund_amount": 0, "refund_at": None}],
                [{"id": "r1", "used_by": "u1", "value": 10, "type": "balance", "used_at": NOW, "notes": ""}],
            ]
        )
        adapter = Sub2ApiOperationsAdapter(site_id="aiwelink")

        users = await adapter.read_users(connection=connection, since=NOW)
        usage = await adapter.read_usage(connection=connection, since=NOW)
        credits = await adapter.read_credit_events(connection=connection, since=NOW)

        self.assertEqual((len(users), len(usage), len(credits)), (1, 1, 2))
        sql = "\n".join(statement for statement, _ in connection.calls).lower()
        self.assertNotIn("password_hash", sql)
        self.assertNotIn("totp_secret", sql)
        for _, parameters in connection.calls[1:]:
            self.assertEqual(parameters["since_at"], NOW)


class NewApiOperationsAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        from app.modules.operations.adapters.newapi import NewApiOperationsAdapter

        self.adapter = NewApiOperationsAdapter(
            site_id="aigclink",
            quota_per_unit=Decimal("500000"),
        )

    def test_user_quota_is_normalized_to_display_balance_units(self) -> None:
        fact = self.adapter.map_user(
            {
                "id": 7,
                "username": "user",
                "email": "user@example.com",
                "status": 1,
                "quota": 1_000_000,
                "created_at": int(NOW.timestamp()),
                "last_login_at": int(NOW.timestamp()),
                "password": "must-not-leak",
                "access_token": "must-not-leak",
            }
        )

        mapped = asdict(fact)
        self.assertEqual(mapped["external_user_id"], "7")
        self.assertEqual(mapped["balance_units"], Decimal("2"))
        self.assertNotIn("password", mapped)
        self.assertNotIn("access_token", mapped)

    def test_quota_data_maps_count_and_normalized_cost(self) -> None:
        fact = self.adapter.map_usage(
            {
                "id": 11,
                "user_id": 7,
                "count": 3,
                "quota": 750_000,
                "created_at": int(NOW.timestamp()),
            }
        )

        self.assertEqual(fact.successful_call_count, 3)
        self.assertEqual(fact.consumed_balance_units, Decimal("1.5"))

    def test_completed_topup_is_classified_sale(self) -> None:
        fact = self.adapter.map_top_up(
            {
                "id": 22,
                "user_id": 7,
                "amount": 5_000_000,
                "money": Decimal("10"),
                "complete_time": int(NOW.timestamp()),
                "create_time": int(NOW.timestamp()),
            }
        )

        self.assertEqual(fact.balance_units, Decimal("10"))
        self.assertEqual(fact.cash_amount_cny, Decimal("10"))
        self.assertEqual(fact.purpose, "sale")

    def test_used_redemption_without_managed_batch_is_pending(self) -> None:
        fact = self.adapter.map_redemption(
            {
                "id": 33,
                "used_user_id": 7,
                "quota": 500_000,
                "redeemed_time": int(NOW.timestamp()),
                "name": "offline",
            }
        )

        self.assertEqual(fact.balance_units, Decimal("1"))
        self.assertEqual(fact.classification_status, "pending")


class NewApiOperationsAdapterReadTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_whitelisted_incremental_source_rows(self) -> None:
        from app.modules.operations.adapters.newapi import NewApiOperationsAdapter

        timestamp = int(NOW.timestamp())
        connection = _FakeConnection(
            [
                [{"id": 7, "username": "u", "email": "u@example.com", "display_name": "", "status": 1, "quota": 500000, "created_at": timestamp, "last_login_at": timestamp}],
                [{"id": 1, "user_id": 7, "count": 1, "quota": 500000, "created_at": timestamp}],
                [{"id": 2, "user_id": 7, "amount": 500000, "money": 1, "complete_time": timestamp, "create_time": timestamp}],
                [],
                [{"id": 3, "used_user_id": 7, "quota": 500000, "redeemed_time": timestamp, "name": "offline"}],
            ]
        )
        adapter = NewApiOperationsAdapter(site_id="aigclink", quota_per_unit=Decimal("500000"))

        users = await adapter.read_users(connection=connection, since=NOW)
        usage = await adapter.read_usage(connection=connection, since=NOW)
        credits = await adapter.read_credit_events(connection=connection, since=NOW)

        self.assertEqual((len(users), len(usage), len(credits)), (1, 1, 2))
        sql = "\n".join(statement for statement, _ in connection.calls).lower()
        self.assertNotIn("password", sql)
        self.assertNotIn("access_token", sql)
        for _, parameters in connection.calls[1:]:
            self.assertEqual(parameters["since_at"], NOW)


class _FakeMappings:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class _FakeResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return _FakeMappings(self.rows)


class _FakeConnection:
    def __init__(self, row_sets):
        self.row_sets = list(row_sets)
        self.calls = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, statement, parameters=None):
        self.calls.append((str(statement), dict(parameters or {})))
        return _FakeResult(self.row_sets.pop(0))


if __name__ == "__main__":
    unittest.main()
