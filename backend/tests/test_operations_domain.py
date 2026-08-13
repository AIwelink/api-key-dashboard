from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class OperationsDomainTests(unittest.TestCase):
    def test_redemption_code_list_query_validates_pagination_and_filters(self) -> None:
        from app.modules.operations.schemas import RedemptionCodeListQuery

        query = RedemptionCodeListQuery(
            site_id=" aiwelink ",
            page=2,
            page_size=100,
            status="unused",
            origin="management_panel",
            search=" alpha ",
        )

        self.assertEqual(query.site_id, "aiwelink")
        self.assertEqual(query.search, "alpha")
        with self.assertRaises(ValidationError):
            RedemptionCodeListQuery(site_id="aiwelink", page=0)
        with self.assertRaises(ValidationError):
            RedemptionCodeListQuery(site_id="aiwelink", page_size=101)

    def test_redemption_batch_delete_requires_unique_positive_ids(self) -> None:
        from app.modules.operations.schemas import RedemptionCodeBatchDelete

        payload = RedemptionCodeBatchDelete(site_id=" aiwelink ", code_ids=[101, 102])

        self.assertEqual(payload.site_id, "aiwelink")
        self.assertEqual(payload.code_ids, [101, 102])
        for invalid_ids in ([], [0], [101, 101], list(range(1, 102))):
            with self.subTest(code_ids=invalid_ids):
                with self.assertRaises(ValidationError):
                    RedemptionCodeBatchDelete(site_id="aiwelink", code_ids=invalid_ids)

    def test_aiwelink_ten_units_cost_one_cny(self) -> None:
        from app.modules.operations.domain import convert_balance_to_cny

        self.assertEqual(
            convert_balance_to_cny(Decimal("25"), Decimal("10")),
            Decimal("2.5"),
        )

    def test_conversion_rate_must_be_positive(self) -> None:
        from app.modules.operations.domain import convert_balance_to_cny

        with self.assertRaisesRegex(ValueError, "greater than zero"):
            convert_balance_to_cny(Decimal("25"), Decimal("0"))

    def test_non_sale_credit_has_zero_cash_income(self) -> None:
        from app.modules.operations.domain import normalized_cash_amount

        self.assertEqual(
            normalized_cash_amount("internal", Decimal("99")),
            Decimal("0"),
        )

    def test_sale_credit_keeps_actual_cash_income(self) -> None:
        from app.modules.operations.domain import normalized_cash_amount

        self.assertEqual(
            normalized_cash_amount("sale", Decimal("99")),
            Decimal("99"),
        )

    def test_sale_enum_keeps_actual_cash_income(self) -> None:
        from app.modules.operations.domain import normalized_cash_amount
        from app.modules.operations.schemas import Purpose

        self.assertEqual(
            normalized_cash_amount(Purpose.SALE, Decimal("99")),
            Decimal("99"),
        )

    def test_internal_user_is_not_ordinary(self) -> None:
        from app.modules.operations.domain import user_segment

        self.assertEqual(user_segment(is_internal=True), "internal")
        self.assertEqual(user_segment(is_internal=False), "ordinary")

    def test_sync_status_is_delayed_after_thirty_minutes(self) -> None:
        from app.modules.operations.domain import sync_health

        self.assertEqual(
            sync_health(
                now=NOW,
                last_success_at=NOW - timedelta(minutes=31),
                running=False,
            ),
            "delayed",
        )

    def test_sync_status_distinguishes_running_healthy_and_never(self) -> None:
        from app.modules.operations.domain import sync_health

        self.assertEqual(sync_health(now=NOW, last_success_at=None, running=True), "running")
        self.assertEqual(sync_health(now=NOW, last_success_at=None, running=False), "never")
        self.assertEqual(
            sync_health(
                now=NOW,
                last_success_at=NOW - timedelta(minutes=15),
                running=False,
            ),
            "healthy",
        )

    def test_sync_status_treats_expired_running_run_as_delayed(self) -> None:
        from app.modules.operations.domain import sync_health

        self.assertEqual(
            sync_health(
                now=NOW,
                last_success_at=NOW - timedelta(minutes=5),
                running=True,
                started_at=NOW - timedelta(minutes=31),
            ),
            "delayed",
        )
        self.assertEqual(
            sync_health(
                now=NOW,
                last_success_at=NOW - timedelta(minutes=5),
                running=True,
                started_at=NOW - timedelta(minutes=2),
            ),
            "running",
        )

    def test_seven_day_range_includes_current_instant_and_previous_period(self) -> None:
        from app.modules.operations.domain import resolve_operations_window

        window = resolve_operations_window("7d", now=NOW)

        self.assertEqual(window.end_at, NOW)
        self.assertEqual(window.start_at, NOW - timedelta(days=7))
        self.assertEqual(window.previous_end_at, window.start_at)
        self.assertEqual(window.previous_start_at, NOW - timedelta(days=14))

    def test_custom_range_requires_an_ordered_window(self) -> None:
        from app.modules.operations.domain import resolve_operations_window

        with self.assertRaisesRegex(ValueError, "custom range"):
            resolve_operations_window("custom", now=NOW)
        with self.assertRaisesRegex(ValueError, "earlier"):
            resolve_operations_window(
                "custom",
                now=NOW,
                start_at=NOW,
                end_at=NOW - timedelta(hours=1),
            )


class OperationsSchemaTests(unittest.TestCase):
    def test_internal_user_create_normalizes_email(self) -> None:
        from app.modules.operations.schemas import InternalUserCreate

        payload = InternalUserCreate(
            site_id="aigclink",
            email=" Staff@Example.com ",
        )

        self.assertEqual(payload.email, "staff@example.com")
        self.assertFalse(hasattr(payload, "external_user_id"))

    def test_internal_user_update_normalizes_email(self) -> None:
        from app.modules.operations.schemas import InternalUserUpdate

        payload = InternalUserUpdate(email=" Staff@Example.com ")

        self.assertEqual(payload.email, "staff@example.com")

    def test_internal_user_rejects_invalid_email(self) -> None:
        from app.modules.operations.schemas import InternalUserCreate

        with self.assertRaises(ValidationError):
            InternalUserCreate(site_id="aigclink", email="not-an-email")

    def test_sale_redemption_without_cash_is_a_credit_fact_but_not_cash_income(self) -> None:
        from app.modules.operations.schemas import RedemptionBatchCreate

        payload = RedemptionBatchCreate(
            site_id="aiwelink",
            purpose="sale",
            code_count=10,
            balance_units_per_code=Decimal("100"),
            cash_amount_cny=Decimal("0"),
            idempotency_key="batch-1",
        )

        self.assertEqual(payload.purpose.value, "sale")
        self.assertEqual(payload.cash_amount_cny, Decimal("0"))

    def test_sale_adjustment_without_cash_is_accepted_as_a_credit_fact(self) -> None:
        from app.modules.operations.schemas import BalanceAdjustmentCreate

        payload = BalanceAdjustmentCreate(
            site_id="aiwelink",
            external_user_id="user-1",
            purpose="sale",
            balance_units=Decimal("100"),
            cash_amount_cny=Decimal("0"),
            idempotency_key="adjustment-1",
        )

        self.assertEqual(payload.purpose.value, "sale")
        self.assertEqual(payload.cash_amount_cny, Decimal("0"))

    def test_non_sale_redemption_rejects_cash_income(self) -> None:
        from app.modules.operations.schemas import RedemptionBatchCreate

        with self.assertRaises(ValidationError):
            RedemptionBatchCreate(
                site_id="aiwelink",
                purpose="promotion",
                code_count=10,
                balance_units_per_code=Decimal("100"),
                cash_amount_cny=Decimal("10"),
                idempotency_key="batch-2",
            )

    def test_sale_classification_without_cash_is_accepted_as_a_credit_fact(self) -> None:
        from app.modules.operations.schemas import ClassificationUpdate

        payload = ClassificationUpdate(purpose="sale", cash_amount_cny=Decimal("0"))

        self.assertEqual(payload.purpose.value, "sale")
        self.assertEqual(payload.cash_amount_cny, Decimal("0"))

    def test_internal_user_window_must_be_ordered(self) -> None:
        from app.modules.operations.schemas import InternalUserCreate

        with self.assertRaises(ValidationError):
            InternalUserCreate(
                site_id="aigclink",
                email="staff@example.com",
                active_from=NOW,
                active_until=NOW - timedelta(days=1),
            )


if __name__ == "__main__":
    unittest.main()
