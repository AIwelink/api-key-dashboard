from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class OperationsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def test_segment_filter_excludes_banned_risk_accounts(self) -> None:
        from app.modules.operations.repository import _segment_filter

        sql = _segment_filter("snapshot")

        self.assertIn("NOT snapshot.is_risk_excluded", sql)
        self.assertIn("snapshot.is_internal", sql)

    async def test_list_redemption_batch_attributions_returns_safe_join_fields(self) -> None:
        from app.modules.operations.repository import list_redemption_batch_attributions

        connection = _FakeConnection(
            [
                {
                    "redemption_batch_id": uuid4(),
                    "site_id": "aiwelink",
                    "source_batch_id": "101,102",
                    "code_masks": ["rede...lpha", "rede...beta"],
                    "requested_by": "owner-1",
                    "created_at": NOW,
                }
            ]
        )

        rows = await list_redemption_batch_attributions(connection, site_id="aiwelink")

        self.assertEqual(rows[0]["source_batch_id"], "101,102")
        statement, parameters = connection.calls[0]
        self.assertIn("growth.redemption_batches", statement)
        self.assertIn("command_status = 'succeeded'", statement)
        self.assertIn("ORDER BY created_at DESC", statement)
        self.assertNotIn("code_hashes", statement)
        self.assertEqual(parameters, {"site_id": "aiwelink"})

    async def test_create_internal_user_recognizes_unique_snapshot_email(self) -> None:
        from app.modules.operations.repository import create_internal_user
        from app.modules.operations.schemas import InternalUserCreate

        internal_user_id = uuid4()
        connection = _FakeConnection(
            [
                {
                    "internal_user_id": internal_user_id,
                    "site_id": "aigclink",
                    "email": "staff@example.com",
                    "external_user_id": "42",
                    "recognition_status": "recognized",
                },
            ]
        )

        row = await create_internal_user(
            connection,
            InternalUserCreate(site_id="aigclink", email="staff@example.com"),
            actor_id="owner",
            internal_user_id=internal_user_id,
        )

        self.assertEqual(row["site_id"], "aigclink")
        self.assertEqual(row["external_user_id"], "42")
        self.assertEqual(row["recognition_status"], "recognized")
        statement, parameters = connection.calls[0]
        self.assertIn("growth.internal_users", statement)
        self.assertIn("lower(trim(snapshot.account_label))", statement)
        self.assertIn("COUNT(*)", statement)
        self.assertIn("NOT EXISTS", statement)
        self.assertIn("existing.external_user_id = snapshot.external_user_id", statement)
        self.assertIn("BOOL_AND(email_matches.available)", statement)
        self.assertIn("growth.ops_user_snapshots", statement)
        self.assertIn("inserted.active_from <= NOW()", statement)
        self.assertIn("inserted.active_until > NOW()", statement)
        self.assertEqual(parameters["site_id"], "aigclink")
        self.assertEqual(parameters["email"], "staff@example.com")
        self.assertNotIn("aigclink'", statement)

    async def test_create_internal_user_keeps_unknown_email_pending(self) -> None:
        from app.modules.operations.repository import create_internal_user
        from app.modules.operations.schemas import InternalUserCreate

        connection = _FakeConnection(
            [
                {
                    "internal_user_id": uuid4(),
                    "site_id": "aigclink",
                    "email": "later@example.com",
                    "external_user_id": None,
                    "recognized_at": None,
                    "recognition_status": "pending",
                }
            ]
        )

        row = await create_internal_user(
            connection,
            InternalUserCreate(site_id="aigclink", email="later@example.com"),
            actor_id="owner",
        )

        self.assertIsNone(row["external_user_id"])
        self.assertEqual(row["recognition_status"], "pending")

    async def test_delete_internal_user_clears_snapshot_before_removing_configuration(self) -> None:
        from app.modules.operations.repository import delete_internal_user

        internal_user_id = uuid4()
        connection = _FakeConnection(
            [
                {
                    "internal_user_id": internal_user_id,
                    "site_id": "aiwelink",
                    "email": "staff@example.com",
                    "external_user_id": "49",
                }
            ]
        )

        row = await delete_internal_user(connection, internal_user_id)

        statement, parameters = connection.calls[0]
        self.assertIn("UPDATE growth.ops_user_snapshots", statement)
        self.assertIn("SET is_internal = FALSE", statement)
        self.assertIn("internal_user_id = NULL", statement)
        self.assertIn("DELETE FROM growth.internal_users", statement)
        self.assertIn("RETURNING target.*", statement)
        self.assertEqual(parameters["internal_user_id"], internal_user_id)
        self.assertEqual(row["site_id"], "aiwelink")

    async def test_internal_user_search_includes_email_and_recognition_status(self) -> None:
        from app.modules.operations.repository import list_internal_users

        connection = _FakeConnection([None])

        await list_internal_users(
            connection,
            allowed_site_ids=("aigclink",),
            query="staff@example.com",
        )

        statement, parameters = connection.calls[0]
        self.assertIn("internal_user.email ILIKE", statement)
        self.assertIn("AS recognition_status", statement)
        self.assertEqual(parameters["query"], "staff@example.com")

    async def test_updating_internal_user_email_retries_recognition(self) -> None:
        from app.modules.operations.repository import update_internal_user
        from app.modules.operations.schemas import InternalUserUpdate

        internal_user_id = uuid4()
        connection = _FakeConnection(
            [
                {
                    "internal_user_id": internal_user_id,
                    "site_id": "aigclink",
                    "email": "new@example.com",
                    "external_user_id": None,
                    "recognized_at": None,
                    "recognition_status": "pending",
                }
            ]
        )

        row = await update_internal_user(
            connection,
            internal_user_id,
            InternalUserUpdate(email=" New@Example.com "),
            actor_id="owner",
        )

        statement, parameters = connection.calls[0]
        self.assertIn("external_user_id = NULL", statement)
        self.assertIn("recognized_at = NULL", statement)
        self.assertIn("lower(trim(snapshot.account_label))", statement)
        self.assertIn("NOT EXISTS", statement)
        self.assertIn("BOOL_AND(email_matches.available)", statement)
        self.assertEqual(parameters["email"], "new@example.com")
        self.assertEqual(row["recognition_status"], "pending")

    async def test_updating_internal_user_window_reclassifies_snapshot(self) -> None:
        from app.modules.operations.repository import update_internal_user
        from app.modules.operations.schemas import InternalUserUpdate

        internal_user_id = uuid4()
        connection = _FakeConnection(
            [
                {
                    "internal_user_id": internal_user_id,
                    "site_id": "aiwelink",
                    "email": "staff@example.com",
                    "external_user_id": "49",
                    "active_until": NOW,
                    "recognition_status": "recognized",
                }
            ]
        )

        await update_internal_user(
            connection,
            internal_user_id,
            InternalUserUpdate(active_until=NOW),
            actor_id="owner",
        )

        statement, _ = connection.calls[0]
        self.assertIn("UPDATE growth.ops_user_snapshots", statement)
        self.assertIn("updated.active_from <= NOW()", statement)
        self.assertIn("updated.active_until > NOW()", statement)
        self.assertIn("SET is_internal =", statement)

    async def test_create_conversion_rate_closes_current_window_before_insert(self) -> None:
        from app.modules.operations.repository import create_conversion_rate
        from app.modules.operations.schemas import ConversionRateCreate

        rate_id = uuid4()
        connection = _FakeConnection(
            [
                {
                    "conversion_rate_id": rate_id,
                    "site_id": "aiwelink",
                    "balance_units_per_cny": Decimal("10"),
                }
            ]
        )

        row = await create_conversion_rate(
            connection,
            ConversionRateCreate(
                site_id="aiwelink",
                balance_units_per_cny=Decimal("10"),
                effective_from=NOW,
            ),
            actor_id="admin",
            conversion_rate_id=rate_id,
        )

        statement, parameters = connection.calls[0]
        self.assertIn("UPDATE growth.balance_conversion_rates", statement)
        self.assertIn("INSERT INTO growth.balance_conversion_rates", statement)
        self.assertEqual(parameters["balance_units_per_cny"], Decimal("10"))
        self.assertEqual(row["conversion_rate_id"], str(rate_id))

    async def test_aggregate_replacement_is_scoped_to_one_site(self) -> None:
        from app.modules.operations.repository import replace_affected_aggregates

        connection = _FakeConnection([None, None, None, None])

        await replace_affected_aggregates(
            connection,
            site_id="aigclink",
            start_at=NOW,
            end_at=NOW,
        )

        statements = "\n".join(statement for statement, _ in connection.calls)
        self.assertIn("WHERE site_id = :site_id", statements)
        self.assertIn("snapshot.site_id = :site_id", statements)
        self.assertIn("usage.site_id = :site_id", statements)
        self.assertIn("event.site_id = :site_id", statements)
        self.assertIn("usage.site_id = 'aigclink'", statements)
        self.assertIn("NOT snapshot.is_internal", statements)
        self.assertIn("event.site_id <> 'aigclink'", statements)
        self.assertGreaterEqual(statements.count("NOT snapshot.is_risk_excluded"), 3)
        self.assertGreaterEqual(
            statements.count("date_trunc('hour', CAST(:start_at AS TIMESTAMPTZ))"),
            2,
        )
        self.assertGreaterEqual(
            statements.count("date_trunc('hour', CAST(:end_at AS TIMESTAMPTZ))"),
            2,
        )
        self.assertGreaterEqual(statements.count("CAST(:start_at AS TIMESTAMPTZ)"), 2)
        self.assertGreaterEqual(statements.count("CAST(:end_at AS TIMESTAMPTZ)"), 2)
        self.assertGreaterEqual(
            statements.count("(CAST(:start_at AS TIMESTAMPTZ) AT TIME ZONE :timezone)::date"),
            2,
        )
        self.assertGreaterEqual(
            statements.count("(CAST(:end_at AS TIMESTAMPTZ) AT TIME ZONE :timezone)::date + 1"),
            2,
        )
        for _, parameters in connection.calls:
            self.assertEqual(parameters["site_id"], "aigclink")

    async def test_operations_sync_lock_uses_site_scoped_transaction_lock(self) -> None:
        from app.modules.operations.repository import acquire_operations_sync_lock

        connection = _FakeConnection([None])

        await acquire_operations_sync_lock(connection, site_id="aiwelink")

        statement, parameters = connection.calls[0]
        self.assertIn("pg_advisory_xact_lock", statement)
        self.assertIn(":site_id", statement)
        self.assertEqual(parameters["site_id"], "aiwelink")

    async def test_fact_upserts_use_stable_source_identity(self) -> None:
        from app.modules.operations.repository import upsert_credit_events, upsert_usage_facts

        connection = _FakeConnection([None, None])
        await upsert_usage_facts(
            connection,
            [
                {
                    "site_id": "aiwelink",
                    "external_user_id": "42",
                    "source_type": "usage_logs",
                    "source_record_id": "1001",
                    "successful_call_count": 1,
                    "consumed_balance_units": Decimal("2"),
                    "cost_cny": Decimal("0.2"),
                    "billed_amount_cny": Decimal("0.15"),
                    "model_name": "claude-sonnet-4",
                    "token_count": 30,
                    "conversion_rate_id": None,
                    "occurred_at": NOW,
                    "source_updated_at": NOW,
                }
            ],
        )
        await upsert_credit_events(
            connection,
            [
                {
                    "site_id": "aiwelink",
                    "external_user_id": "42",
                    "source_type": "payment",
                    "source_record_id": "order-1",
                    "direction": "credit",
                    "purpose": "sale",
                    "classification_status": "classified",
                    "balance_units": Decimal("100"),
                    "cash_amount_cny": Decimal("10"),
                    "conversion_rate_id": None,
                    "occurred_at": NOW,
                    "source_updated_at": NOW,
                    "source_metadata": {},
                }
            ],
        )

        for statement, parameters in connection.calls:
            self.assertIn("ON CONFLICT (site_id, source_type, source_record_id)", statement)
            self.assertEqual(parameters[0]["source_record_id"], parameters[0]["source_record_id"])
        usage_statement, usage_parameters = connection.calls[0]
        self.assertIn("billed_amount_cny", usage_statement)
        self.assertIn("model_name", usage_statement)
        self.assertIn("token_count", usage_statement)
        self.assertEqual(usage_parameters[0]["billed_amount_cny"], Decimal("0.15"))

    async def test_subscription_entitlements_replace_only_selected_site(self) -> None:
        from app.modules.operations.adapters.base import SubscriptionEntitlementInput
        from app.modules.operations.repository import replace_subscription_entitlements

        connection = _FakeConnection([None, None])
        entitlement = SubscriptionEntitlementInput(
            site_id="aiwelink",
            external_user_id="42",
            source_type="user_subscription",
            source_record_id="subscription-1",
            starts_at=NOW,
            ends_at=datetime(2026, 8, 25, 12, 0, tzinfo=UTC),
            status="active",
            source_updated_at=NOW,
        )

        count = await replace_subscription_entitlements(
            connection,
            site_id="aiwelink",
            records=[entitlement],
        )

        delete_statement, delete_parameters = connection.calls[0]
        insert_statement, insert_parameters = connection.calls[1]
        self.assertIn("DELETE FROM growth.subscription_entitlements", delete_statement)
        self.assertEqual(delete_parameters, {"site_id": "aiwelink"})
        self.assertIn("INSERT INTO growth.subscription_entitlements", insert_statement)
        self.assertIn("ON CONFLICT (site_id, source_type, source_record_id)", insert_statement)
        self.assertEqual(insert_parameters[0]["external_user_id"], "42")
        self.assertEqual(count, 1)

    async def test_delete_source_credit_events_is_scoped_to_site_and_types(self) -> None:
        from app.modules.operations import repository

        self.assertTrue(hasattr(repository, "delete_source_credit_events"))
        connection = _FakeConnection([None])

        await repository.delete_source_credit_events(
            connection,
            site_id="aiwelink",
            source_types=("payment", "refund"),
        )

        statement, parameters = connection.calls[0]
        self.assertIn("DELETE FROM growth.credit_events", statement)
        self.assertIn("site_id = :site_id", statement)
        self.assertIn("source_type = ANY", statement)
        self.assertEqual(parameters["site_id"], "aiwelink")
        self.assertEqual(parameters["source_types"], ("payment", "refund"))

    async def test_pending_source_upsert_preserves_manual_credit_classification(self) -> None:
        from app.modules.operations.repository import upsert_credit_events

        connection = _FakeConnection([None])
        await upsert_credit_events(
            connection,
            [
                {
                    "site_id": "aiwelink",
                    "external_user_id": "42",
                    "source_type": "redemption",
                    "source_record_id": "redeem-1",
                    "direction": "credit",
                    "purpose": None,
                    "classification_status": "pending",
                    "balance_units": Decimal("100"),
                    "cash_amount_cny": Decimal("0"),
                    "conversion_rate_id": None,
                    "occurred_at": NOW,
                    "source_updated_at": NOW,
                    "source_metadata": {},
                }
            ],
        )

        statement, _ = connection.calls[0]
        self.assertIn("existing.classification_status = 'classified'", statement)
        self.assertIn("EXCLUDED.classification_status = 'pending'", statement)
        self.assertIn("THEN existing.purpose", statement)
        self.assertIn("THEN existing.cash_amount_cny", statement)

    async def test_user_snapshot_upsert_recognizes_pending_email_configuration(self) -> None:
        from app.modules.operations.repository import upsert_user_snapshots

        connection = _FakeConnection([None, 1])
        result = await upsert_user_snapshots(
            connection,
            [
                {
                    "site_id": "aigclink",
                    "external_user_id": "7",
                    "account_label": "staff@example.com",
                    "registered_at": NOW,
                    "account_status": "active",
                    "balance_units": Decimal("5"),
                    "source_created_at": NOW,
                    "source_updated_at": NOW,
                }
            ],
        )

        upsert_statement, parameters = connection.calls[0]
        recognition_statement, _ = connection.calls[1]
        self.assertIn("growth.internal_users", upsert_statement)
        self.assertIn("configured.external_user_id = :external_user_id", upsert_statement)
        self.assertIn("ON CONFLICT (site_id, external_user_id)", upsert_statement)
        self.assertEqual(parameters[0]["account_label"], "staff@example.com")
        self.assertIn(
            "lower(trim(configured.email)) = lower(trim(snapshot.account_label))",
            recognition_statement,
        )
        self.assertIn("HAVING COUNT(*) = 1", recognition_statement)
        self.assertIn("recognized_at = NOW()", recognition_statement)
        self.assertIn("NOT EXISTS", recognition_statement)
        self.assertIn("existing.external_user_id = snapshot.external_user_id", recognition_statement)
        self.assertIn("BOOL_AND(matches.available)", recognition_statement)
        self.assertIn("UPDATE growth.ops_user_snapshots", recognition_statement)
        self.assertIn("RETURNING snapshot.external_user_id", recognition_statement)
        self.assertIn("SELECT COUNT(*) FROM attached", recognition_statement)
        self.assertEqual(result, (1, 1))

    async def test_reconcile_internal_user_snapshots_updates_only_changed_windows(self) -> None:
        from app.modules.operations import repository

        self.assertTrue(hasattr(repository, "reconcile_internal_user_snapshots"))
        connection = _FakeConnection([2])

        result = await repository.reconcile_internal_user_snapshots(
            connection,
            site_id="aiwelink",
        )

        statement, parameters = connection.calls[0]
        self.assertIn("LEFT JOIN growth.internal_users", statement)
        self.assertIn("configured.active_from <= NOW()", statement)
        self.assertIn("configured.active_until > NOW()", statement)
        self.assertIn("snapshot.is_internal IS DISTINCT FROM", statement)
        self.assertIn("snapshot.internal_user_id IS DISTINCT FROM", statement)
        self.assertEqual(parameters, {"site_id": "aiwelink"})
        self.assertEqual(result, 2)

    async def test_resolve_classification_updates_task_and_credit_event_together(self) -> None:
        from app.modules.operations.repository import resolve_classification_task
        from app.modules.operations.schemas import ClassificationUpdate

        task_id = uuid4()
        connection = _FakeConnection(
            [
                {
                    "classification_task_id": task_id,
                    "status": "resolved",
                    "resolved_purpose": "sale",
                    "resolved_cash_amount_cny": Decimal("20"),
                }
            ]
        )

        await resolve_classification_task(
            connection,
            task_id,
            ClassificationUpdate(purpose="sale", cash_amount_cny=Decimal("20")),
            actor_id="owner",
        )

        statement, parameters = connection.calls[0]
        self.assertIn("UPDATE growth.classification_tasks", statement)
        self.assertIn("UPDATE growth.credit_events", statement)
        self.assertEqual(parameters["actor_id"], "owner")

    async def test_classification_tasks_sort_by_business_occurrence_time(self) -> None:
        from app.modules.operations.repository import list_classification_tasks

        connection = _FakeConnection([None])

        await list_classification_tasks(
            connection,
            allowed_site_ids=("aiwelink",),
        )

        statement, _ = connection.calls[0]
        normalized_statement = " ".join(statement.split())
        self.assertIn(
            "ORDER BY event.occurred_at DESC, task.created_at DESC, "
            "task.classification_task_id DESC",
            normalized_statement,
        )

    async def test_summary_query_uses_bound_filters(self) -> None:
        from app.modules.operations.repository import get_operations_summary

        connection = _FakeConnection([{"registered_user_count": 1}])
        await get_operations_summary(
            connection,
            allowed_site_ids=("aiwelink",),
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )

        statement, parameters = connection.calls[0]
        self.assertIn("ANY(CAST(:allowed_site_ids AS TEXT[]))", statement)
        self.assertIn(":segment", statement)
        self.assertNotIn("aiwelink'", statement)
        self.assertEqual(parameters["allowed_site_ids"], ("aiwelink",))
        self.assertEqual(parameters["segment"], "ordinary")

    async def test_summary_uses_source_priced_revenue_and_billed_aigclink_customers(self) -> None:
        from app.modules.operations.repository import get_operations_summary

        connection = _FakeConnection([{"gross_income_cny": Decimal("12") }])

        await get_operations_summary(
            connection,
            allowed_site_ids=("aiwelink", "aigclink"),
            segment="all",
            start_at=NOW,
            end_at=NOW,
        )

        statement, _ = connection.calls[0]
        self.assertIn("usage.site_id = 'aigclink'", statement)
        self.assertIn("NOT snapshot.is_internal", statement)
        self.assertIn("SUM(usage.billed_amount_cny)", statement)
        self.assertIn("usage.billed_amount_cny > 0", statement)
        self.assertIn("aigclink_payer_count", statement)
        self.assertIn("event.site_id <> 'aigclink'", statement)

    async def test_trends_use_v3_aggregate_income_without_cost_reinterpretation(self) -> None:
        from app.modules.operations.repository import get_operations_trends

        connection = _FakeConnection([None])

        await get_operations_trends(
            connection,
            allowed_site_ids=("aigclink",),
            segment="all",
            start_at=NOW,
            end_at=NOW,
        )

        statement, _ = connection.calls[0]
        self.assertIn("FROM growth.ops_hourly_stats AS stats", statement)
        self.assertIn("stats.gross_income_cny", statement)
        self.assertNotIn("ordinary.cost_cny", statement)

    async def test_site_breakdown_groups_current_metrics_by_authorized_site(self) -> None:
        from app.modules.operations.repository import get_operations_site_breakdown

        connection = _FakeConnection(
            [
                {
                    "site_id": "aigclink",
                    "registered_user_count": 3,
                    "gross_income_cny": Decimal("12"),
                }
            ]
        )

        rows = await get_operations_site_breakdown(
            connection,
            allowed_site_ids=("aigclink",),
            segment="all",
            start_at=NOW,
            end_at=NOW,
        )

        statement, parameters = connection.calls[0]
        self.assertEqual(rows[0]["site_id"], "aigclink")
        self.assertIn("GROUP BY usage.site_id", statement)
        self.assertIn("event.site_id <> 'aigclink'", statement)
        self.assertIn("NOT snapshot.is_internal", statement)
        self.assertIn("SUM(usage.billed_amount_cny)", statement)
        self.assertIn("usage.billed_amount_cny > 0", statement)
        self.assertEqual(parameters["allowed_site_ids"], ("aigclink",))

    async def test_aggregate_income_uses_billed_usage_only_for_ordinary_aigclink_users(self) -> None:
        from app.modules.operations import repository

        connection = _FakeConnection([None, None, None, None])

        await repository.replace_affected_aggregates(
            connection,
            site_id="aigclink",
            start_at=NOW,
            end_at=NOW,
        )

        statements = "\n".join(statement for statement, _ in connection.calls)
        self.assertIn("usage.billed_amount_cny", statements)
        self.assertIn("usage.billed_amount_cny > 0", statements)
        self.assertIn("NOT snapshot.is_internal", statements)
        self.assertIn("event.site_id <> 'aigclink'", statements)

    async def test_lifecycle_summary_uses_mature_activation_and_usage_churn_boundaries(self) -> None:
        from app.modules.operations.repository import get_operations_lifecycle_summary

        connection = _FakeConnection([{"scope": "all", "site_id": None}])
        rows = await get_operations_lifecycle_summary(
            connection,
            allowed_site_ids=("aiwelink", "aigclink"),
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )

        statement, parameters = connection.calls[0]
        self.assertEqual(rows[0]["scope"], "all")
        self.assertIn("INTERVAL '24 hours'", statement)
        self.assertIn("INTERVAL '7 days'", statement)
        self.assertIn("registered_at <= :end_at - INTERVAL '24 hours'", statement)
        self.assertIn("first_used_at <= registered_at + INTERVAL '24 hours'", statement)
        self.assertIn("INTERVAL '14 days'", statement)
        self.assertIn("INTERVAL '30 days'", statement)
        self.assertIn("LAG(usage.occurred_at)", statement)
        self.assertIn("successful_call_count > 0", statement)
        self.assertIn("CASE WHEN", statement)
        self.assertIn("THEN NULL", statement)
        self.assertEqual(parameters["allowed_site_ids"], ("aiwelink", "aigclink"))
        self.assertEqual(parameters["segment"], "ordinary")

    async def test_lifecycle_summary_preserves_site_specific_payment_semantics(self) -> None:
        from app.modules.operations.repository import get_operations_lifecycle_summary

        connection = _FakeConnection([None])
        await get_operations_lifecycle_summary(
            connection,
            allowed_site_ids=("aiwelink", "aigclink"),
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )

        statement, _ = connection.calls[0]
        self.assertIn("event.site_id <> 'aigclink'", statement)
        self.assertIn("event.cash_amount_cny > 0", statement)
        self.assertIn("usage.site_id = 'aigclink'", statement)
        self.assertIn("usage.billed_amount_cny > 0", statement)
        self.assertIn(
            "site_id = 'aigclink' AND aigclink_window_billed_cny > 0",
            statement,
        )
        self.assertIn("site_id <> 'aigclink' AND first_cash_paid_at IS NOT NULL", statement)
        self.assertIn("growth.subscription_entitlements", statement)
        self.assertIn("balance_units > 0 OR has_active_paid_subscription", statement)
        self.assertIn("classification_status = 'pending'", statement)
        self.assertIn("source_type = 'redemption'", statement)
        self.assertIn("subscription_days", statement)
        self.assertIn("order_type", statement)
        self.assertIn("sale_credit_events AS", statement)
        self.assertIn("event.balance_units > 0", statement)
        self.assertIn("recharge_event_count", statement)
        self.assertIn("recharge_balance_units", statement)
        self.assertIn("subscription_cash_income_cny", statement)

    async def test_retention_uses_shanghai_natural_days_and_null_immature_cells(self) -> None:
        from app.modules.operations.repository import get_operations_retention

        connection = _FakeConnection([{"site_id": "aiwelink", "cohort_date": "2026-07-01"}])
        rows = await get_operations_retention(
            connection,
            allowed_site_ids=("aiwelink",),
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )

        statement, _ = connection.calls[0]
        self.assertEqual(rows[0]["site_id"], "aiwelink")
        self.assertIn("AT TIME ZONE 'Asia/Shanghai'", statement)
        for day in (1, 3, 7, 14, 30):
            self.assertIn(f"INTERVAL '{day} days'", statement)
            self.assertIn(f"d{day}_rate", statement)
        self.assertIn("ELSE NULL", statement)

    async def test_value_rankings_are_source_priced_and_bounded(self) -> None:
        from app.modules.operations.repository import (
            get_operations_customer_breakdown,
            get_operations_model_breakdown,
        )

        connection = _FakeConnection([{"model_name": "gpt"}, {"external_user_id": "7"}])
        await get_operations_model_breakdown(
            connection,
            allowed_site_ids=("aigclink",),
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )
        await get_operations_customer_breakdown(
            connection,
            allowed_site_ids=("aigclink",),
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )

        model_statement, model_parameters = connection.calls[0]
        customer_statement, customer_parameters = connection.calls[1]
        self.assertIn("usage.model_name", model_statement)
        self.assertIn("SUM(usage.token_count)", model_statement)
        self.assertIn("SUM(usage.billed_amount_cny)", model_statement)
        self.assertIn("NOT snapshot.is_internal", model_statement)
        self.assertIn("revenue_share", model_statement)
        self.assertIn("ORDER BY billed_amount_cny DESC", model_statement)
        self.assertIn("snapshot.account_label", customer_statement)
        self.assertIn("SUM(usage.billed_amount_cny)", customer_statement)
        self.assertIn("NOT snapshot.is_internal", customer_statement)
        self.assertIn("ORDER BY billed_amount_cny DESC", customer_statement)
        self.assertEqual(model_parameters["limit"], 20)
        self.assertEqual(customer_parameters["limit"], 20)

    async def test_all_user_facing_reads_require_bound_site_collections(self) -> None:
        from app.modules.operations import repository

        connection = _FakeConnection([None, None, None, None, None, None])
        allowed = ("aiwelink",)
        await repository.get_operations_trends(
            connection,
            allowed_site_ids=allowed,
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )
        await repository.list_operations_users(
            connection,
            allowed_site_ids=allowed,
            segment="ordinary",
            start_at=NOW,
            end_at=NOW,
        )
        await repository.get_sync_status(connection, allowed_site_ids=allowed)
        await repository.list_internal_users(connection, allowed_site_ids=allowed)
        await repository.list_conversion_rates(connection, allowed_site_ids=allowed)
        await repository.list_classification_tasks(connection, allowed_site_ids=allowed)

        self.assertEqual(len(connection.calls), 6)
        for statement, parameters in connection.calls:
            self.assertIn("ANY(CAST(:allowed_site_ids AS TEXT[]))", statement)
            self.assertEqual(parameters["allowed_site_ids"], allowed)
            self.assertNotIn("CAST(:site_id AS TEXT) IS NULL", statement)

    async def test_credit_command_requests_are_persisted_as_pending(self) -> None:
        from app.modules.operations.repository import (
            create_balance_adjustment_request,
            create_redemption_batch_request,
        )
        from app.modules.operations.schemas import BalanceAdjustmentCreate, RedemptionBatchCreate

        batch_id = uuid4()
        adjustment_id = uuid4()
        connection = _FakeConnection(
            [
                {"redemption_batch_id": batch_id, "command_status": "pending"},
                {"adjustment_request_id": adjustment_id, "command_status": "pending"},
            ]
        )

        batch = await create_redemption_batch_request(
            connection,
            RedemptionBatchCreate(
                site_id="aiwelink",
                purpose="internal",
                code_count=2,
                balance_units_per_code=Decimal("100"),
                idempotency_key="batch-1",
            ),
            actor_id="owner",
            redemption_batch_id=batch_id,
        )
        adjustment = await create_balance_adjustment_request(
            connection,
            BalanceAdjustmentCreate(
                site_id="aigclink",
                external_user_id="42",
                purpose="compensation",
                balance_units=Decimal("5"),
                idempotency_key="adjustment-1",
            ),
            actor_id="admin",
            adjustment_request_id=adjustment_id,
        )

        self.assertEqual(batch["command_status"], "pending")
        self.assertEqual(adjustment["command_status"], "pending")
        self.assertIn("growth.redemption_batches", connection.calls[0][0])
        self.assertIn("growth.balance_adjustment_requests", connection.calls[1][0])
        self.assertEqual(connection.calls[0][1]["purpose"], "internal")
        self.assertEqual(connection.calls[1][1]["external_user_id"], "42")

    async def test_redemption_batch_lifecycle_reads_and_completes_without_plaintext(self) -> None:
        from app.modules.operations.repository import (
            complete_redemption_batch,
            get_redemption_batch_by_idempotency,
        )

        batch_id = uuid4()
        connection = _FakeConnection(
            [
                {"redemption_batch_id": batch_id, "command_status": "pending"},
                {
                    "redemption_batch_id": batch_id,
                    "command_status": "succeeded",
                    "code_masks": ["rede...lpha"],
                },
            ]
        )

        existing = await get_redemption_batch_by_idempotency(
            connection,
            site_id="aiwelink",
            idempotency_key="batch-1",
        )
        completed = await complete_redemption_batch(
            connection,
            redemption_batch_id=batch_id,
            source_batch_id="101",
            code_hashes=["digest"],
            code_masks=["rede...lpha"],
        )

        self.assertEqual(existing["command_status"], "pending")
        self.assertEqual(completed["command_status"], "succeeded")
        self.assertIn("site_id = :site_id", connection.calls[0][0])
        self.assertIn("idempotency_key = :idempotency_key", connection.calls[0][0])
        self.assertIn("command_status = 'succeeded'", connection.calls[1][0])
        self.assertEqual(connection.calls[1][1]["code_hashes"], '["digest"]')
        self.assertEqual(connection.calls[1][1]["code_masks"], '["rede...lpha"]')
        self.assertNotIn("redeem-alpha", str(connection.calls))

    async def test_failed_redemption_batch_records_error_without_plaintext(self) -> None:
        from app.modules.operations.repository import fail_redemption_batch

        batch_id = uuid4()
        connection = _FakeConnection(
            [{"redemption_batch_id": batch_id, "command_status": "failed"}]
        )

        failed = await fail_redemption_batch(
            connection,
            redemption_batch_id=batch_id,
            error_code="HTTPException",
            error_message="upstream unavailable",
        )

        self.assertEqual(failed["command_status"], "failed")
        statement, parameters = connection.calls[0]
        self.assertIn("command_status = 'failed'", statement)
        self.assertEqual(parameters["error_code"], "HTTPException")
        self.assertEqual(parameters["error_message"], "upstream unavailable")

    async def test_operations_sync_run_lifecycle_uses_operations_stream(self) -> None:
        from app.modules.operations.repository import (
            finish_operations_sync_run,
            get_operations_sync_cursor,
            start_operations_sync_run,
        )

        run_id = uuid4()
        connection = _FakeConnection(
            [
                {"last_success_at": NOW, "cursor_value": {"aggregate_version": 1}},
                None,
                None,
                {"run_id": run_id, "status": "running"},
                {"run_id": run_id, "status": "succeeded"},
            ]
        )

        cursor = await get_operations_sync_cursor(connection, site_id="aiwelink")
        started = await start_operations_sync_run(
            connection,
            site_id="aiwelink",
            adapter_name="sub2api",
            trigger_type="schedule",
            started_at=NOW,
            run_id=run_id,
        )
        finished = await finish_operations_sync_run(
            connection,
            run_id=run_id,
            site_id="aiwelink",
            adapter_name="sub2api",
            status="succeeded",
            finished_at=NOW,
            rows_scanned=10,
            rows_upserted=9,
            aggregate_version=1,
        )

        self.assertEqual(cursor["last_success_at"], NOW.isoformat())
        self.assertEqual(started["status"], "running")
        self.assertEqual(finished["status"], "succeeded")
        sql = "\n".join(statement for statement, _ in connection.calls)
        self.assertIn("stream_name = 'operations'", sql)
        self.assertIn("growth.sync_cursors", sql)
        self.assertIn("cursor_value", connection.calls[0][0])
        self.assertIn("cursor_value = COALESCE", connection.calls[4][0])
        self.assertIn("|| EXCLUDED.cursor_value", connection.calls[4][0])
        self.assertIn("CAST(:aggregate_version AS INTEGER)", connection.calls[4][0])
        self.assertIn("AND status = 'running'", connection.calls[4][0])
        self.assertEqual(connection.calls[4][1].get("aggregate_version"), 1)

    async def test_start_sync_run_expires_stale_run_and_skips_active_duplicate(self) -> None:
        from app.modules.operations.repository import start_operations_sync_run

        connection = _FakeConnection([None, None, None])

        started = await start_operations_sync_run(
            connection,
            site_id="aiwelink",
            adapter_name="sub2api",
            trigger_type="schedule",
            started_at=NOW,
            stale_after=timedelta(minutes=30),
        )

        self.assertEqual(started, {})
        lock_statement, lock_parameters = connection.calls[0]
        expire_statement, expire_parameters = connection.calls[1]
        insert_statement, _ = connection.calls[2]
        self.assertIn("pg_advisory_xact_lock", lock_statement)
        self.assertEqual(lock_parameters, {"site_id": "aiwelink"})
        self.assertIn("status = 'failed'", expire_statement)
        self.assertIn("error_code = 'SyncTimedOut'", expire_statement)
        self.assertEqual(expire_parameters["stale_before"], NOW - timedelta(minutes=30))
        self.assertIn("ON CONFLICT (site_id)", insert_statement)
        self.assertIn("WHERE stream_name = 'operations' AND status = 'running'", insert_statement)
        self.assertIn("DO NOTHING", insert_statement)

    async def test_sync_status_keeps_last_success_when_latest_run_failed(self) -> None:
        from app.modules.operations.repository import get_sync_status

        connection = _FakeConnection(
            [
                {
                    "site_id": "aiwelink",
                    "status": "failed",
                    "last_success_at": NOW,
                }
            ]
        )

        result = await get_sync_status(connection, allowed_site_ids=("aiwelink",))

        self.assertEqual(result[0]["last_success_at"], NOW.isoformat())
        statement, _ = connection.calls[0]
        self.assertIn("growth.sync_cursors", statement)


class OperationsCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_reuses_value_until_invalidated_for_site(self) -> None:
        from app.modules.operations.cache import OperationsResponseCache

        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            return {"value": calls}

        cache = OperationsResponseCache(ttl_seconds=60, max_entries=4)
        key = ("summary", "aiwelink", "ordinary")

        first = await cache.get_or_load(key, load)
        second = await cache.get_or_load(key, load)
        cache.invalidate(site_id="aiwelink")
        third = await cache.get_or_load(key, load)

        self.assertEqual(first, second)
        self.assertNotEqual(second, third)
        self.assertEqual(calls, 2)

    async def test_cache_invalidates_nested_site_scope(self) -> None:
        from app.modules.operations.cache import OperationsResponseCache

        calls = {"allowed": 0, "other": 0}

        async def load_allowed():
            calls["allowed"] += 1
            return {"value": calls["allowed"]}

        async def load_other():
            calls["other"] += 1
            return {"value": calls["other"]}

        cache = OperationsResponseCache(ttl_seconds=60, max_entries=4)
        allowed_key = ("overview", ("aiwelink", "aigclink"), "ordinary")
        other_key = ("overview", ("other",), "ordinary")

        first_allowed = await cache.get_or_load(allowed_key, load_allowed)
        first_other = await cache.get_or_load(other_key, load_other)
        cache.invalidate(site_id="aiwelink")
        second_allowed = await cache.get_or_load(allowed_key, load_allowed)
        second_other = await cache.get_or_load(other_key, load_other)

        self.assertNotEqual(first_allowed, second_allowed)
        self.assertEqual(first_other, second_other)
        self.assertEqual(calls, {"allowed": 2, "other": 1})

    async def test_cache_invalidation_detaches_matching_inflight_load(self) -> None:
        from app.modules.operations.cache import OperationsResponseCache

        old_started = asyncio.Event()
        release_old = asyncio.Event()
        fresh_calls = 0

        async def load_old():
            old_started.set()
            await release_old.wait()
            return "old"

        async def load_fresh():
            nonlocal fresh_calls
            fresh_calls += 1
            return "fresh"

        cache = OperationsResponseCache(ttl_seconds=60, max_entries=4)
        key = ("trends", ("aiwelink",), "ordinary")
        old_task = asyncio.create_task(cache.get_or_load(key, load_old))
        await old_started.wait()

        cache.invalidate(site_id="aiwelink")
        fresh_task = asyncio.create_task(cache.get_or_load(key, load_fresh))
        await asyncio.sleep(0)
        release_old.set()

        old_value, fresh_value = await asyncio.gather(old_task, fresh_task)
        cached_value = await cache.get_or_load(key, load_fresh)

        self.assertEqual(old_value, "old")
        self.assertEqual(fresh_value, "fresh")
        self.assertEqual(cached_value, "fresh")
        self.assertEqual(fresh_calls, 1)

    async def test_cache_coalesces_concurrent_loads_and_bounds_entries(self) -> None:
        from app.modules.operations.cache import OperationsResponseCache

        calls = 0

        async def load():
            nonlocal calls
            calls += 1
            await asyncio.sleep(0)
            return calls

        cache = OperationsResponseCache(ttl_seconds=60, max_entries=2)
        values = await asyncio.gather(
            cache.get_or_load(("summary", "aiwelink"), load),
            cache.get_or_load(("summary", "aiwelink"), load),
        )
        await cache.get_or_load(("summary", "aigclink"), load)
        await cache.get_or_load(("trends", "aigclink"), load)

        self.assertEqual(values, [1, 1])
        self.assertEqual(calls, 3)
        self.assertEqual(cache.size, 2)


class _FakeMappings:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row

    def all(self):
        return [] if self.row is None else [self.row]


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return _FakeMappings(self.row)

    def scalar_one_or_none(self):
        return self.row


class _FakeConnection:
    def __init__(self, rows: list[dict | None]):
        self.rows = list(rows)
        self.calls: list[tuple[str, object]] = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, statement, parameters=None):
        captured = [dict(item) for item in parameters] if isinstance(parameters, list) else dict(parameters or {})
        self.calls.append((str(statement), captured))
        return _FakeResult(self.rows.pop(0) if self.rows else None)


if __name__ == "__main__":
    unittest.main()
