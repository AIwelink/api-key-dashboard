from __future__ import annotations

import hashlib
import hmac
import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from sqlalchemy import DateTime, Integer, Text, Uuid

from app.modules.growth.analytics_repository import (
    list_milestone_users,
    load_amounts,
    load_link_performance,
    load_source_breakdown,
    load_traffic_summary,
    load_traffic_trends,
)
from app.modules.growth.analytics_schemas import (
    TrafficAnalyticsFilters,
    TrafficUsersQuery,
    resolve_traffic_window,
    safe_rate,
)


class TrafficAnalyticsDomainTests(unittest.TestCase):
    def test_resolves_supported_traffic_windows(self) -> None:
        now = datetime(2026, 7, 25, 8, 30, tzinfo=UTC)

        last_day = resolve_traffic_window("24h", now=now)
        last_week = resolve_traffic_window("7d", now=now)
        last_month = resolve_traffic_window("30d", now=now)
        last_quarter = resolve_traffic_window("90d", now=now)

        self.assertEqual(last_day.start_at, datetime(2026, 7, 24, 8, 30, tzinfo=UTC))
        self.assertEqual(last_day.bucket, "hour")
        self.assertEqual(last_week.start_at, datetime(2026, 7, 18, 8, 30, tzinfo=UTC))
        self.assertEqual(last_month.start_at, datetime(2026, 6, 25, 8, 30, tzinfo=UTC))
        self.assertEqual(last_quarter.start_at, datetime(2026, 4, 26, 8, 30, tzinfo=UTC))
        self.assertEqual(last_week.bucket, "day")
        self.assertEqual(last_week.end_at, now)

    def test_safe_rate_preserves_missing_denominator(self) -> None:
        self.assertIsNone(safe_rate(3, 0))
        self.assertEqual(safe_rate(1, 4), 0.25)
        self.assertEqual(safe_rate(4, 3), 1.333333)

    def test_filters_trim_site_and_force_promotion_for_link_dimensions(self) -> None:
        filters = TrafficAnalyticsFilters(
            site_id=" aiwelink ",
            source_kind="direct",
            channel_id=UUID("11111111-1111-1111-1111-111111111111"),
        )

        self.assertEqual(filters.site_id, "aiwelink")
        self.assertEqual(filters.source_kind, "promotion")

    def test_user_query_enforces_pagination_bounds(self) -> None:
        with self.assertRaises(ValueError):
            TrafficUsersQuery(limit=101)


class TrafficAnalyticsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.window = resolve_traffic_window(
            "7d",
            now=datetime(2026, 7, 25, 8, 30, tzinfo=UTC),
        )

    async def test_summary_combines_anonymous_traffic_and_registration_cohort(self) -> None:
        connection = _FakeConnection(
            [
                {"homepage_pv": 20, "homepage_uv": 12, "link_pv": 9, "link_uv": 7},
                {
                    "registered_accounts": 3,
                    "promotion_registered_accounts": 2,
                    "called_accounts": 2,
                    "paid_accounts": 1,
                    "second_paid_accounts": 1,
                    "continued_accounts": 2,
                    "refunded_accounts": 0,
                },
            ]
        )

        result = await load_traffic_summary(
            connection,
            TrafficAnalyticsFilters(segment="ordinary", site_id="aiwelink"),
            self.window,
        )

        self.assertEqual(result["homepage_pv"], 20)
        self.assertEqual(result["registered_accounts"], 3)
        sql = "\n".join(statement for statement, _ in connection.calls)
        self.assertIn("growth.homepage_visits", sql)
        self.assertIn("growth.internal_users", sql)
        self.assertIn("internal.internal_user_id IS NULL", sql)
        self.assertIn("COALESCE(facts.is_excluded, FALSE) = FALSE", sql)
        self.assertIn("excluded.external_user_id IS NULL", sql)
        self.assertIn("attribution.source_kind", sql)
        self.assertNotIn("'promotion'::TEXT AS source_kind", sql)
        self.assertTrue(all(params["site_id"] == "aiwelink" for _, params in connection.calls))

    async def test_all_statements_type_nullable_analytics_filters(self) -> None:
        connection = _FakeConnection(
            [
                {"homepage_pv": 0, "homepage_uv": 0, "link_pv": 0, "link_uv": 0},
                {
                    "registered_accounts": 0,
                    "promotion_registered_accounts": 0,
                    "called_accounts": 0,
                    "paid_accounts": 0,
                    "second_paid_accounts": 0,
                    "continued_accounts": 0,
                    "refunded_accounts": 0,
                },
                [],
                [],
                [],
                [],
                {"total": 0},
                [],
            ]
        )
        filters = TrafficAnalyticsFilters(
            segment="ordinary",
            site_id="aiwelink-main",
        )
        user_query = TrafficUsersQuery(
            segment="ordinary",
            site_id="aiwelink-main",
        )

        await load_traffic_summary(connection, filters, self.window)
        await load_traffic_trends(connection, filters, self.window)
        await load_source_breakdown(connection, filters, self.window)
        await load_link_performance(connection, filters, self.window)
        await load_amounts(connection, filters, self.window)
        await list_milestone_users(connection, user_query, self.window)

        self.assertEqual(len(connection.statements), 8)
        common_bind_names = {
            "start_at",
            "end_at",
            "site_id",
            "source_kind",
            "channel_id",
            "campaign_id",
            "tracking_link_id",
        }
        statement_markers = {
            "traffic_summary": "WITH homepage AS",
            "cohort_summary": "promotion_registered_accounts",
            "traffic_trends": "homepage_trend AS",
            "source_breakdown": "traffic_sources AS",
            "link_performance": "link_traffic AS",
            "amounts": "SUM(facts.payment_total_minor)",
            "user_count": "SELECT COUNT(*)::BIGINT AS total FROM cohort AS facts",
            "user_list": "LIMIT :limit OFFSET :offset",
        }
        captured_kinds = set()
        for statement in connection.statements:
            sql = str(statement)
            matching_kinds = [
                kind for kind, marker in statement_markers.items() if marker in sql
            ]
            self.assertEqual(len(matching_kinds), 1, sql[:80])
            kind = matching_kinds[0]
            captured_kinds.add(kind)
            expected_bind_names = set(common_bind_names)
            if kind == "traffic_trends":
                expected_bind_names.add("bucket")
            if kind == "user_list":
                expected_bind_names.update(("limit", "offset"))

            with self.subTest(kind=kind):
                self.assertEqual(set(statement._bindparams), expected_bind_names)
                self.assertIsInstance(statement._bindparams["start_at"].type, DateTime)
                self.assertTrue(statement._bindparams["start_at"].type.timezone)
                self.assertIsInstance(statement._bindparams["end_at"].type, DateTime)
                self.assertTrue(statement._bindparams["end_at"].type.timezone)
                self.assertIsInstance(statement._bindparams["site_id"].type, Text)
                self.assertIsInstance(statement._bindparams["source_kind"].type, Text)
                self.assertIsInstance(statement._bindparams["channel_id"].type, Uuid)
                self.assertIsInstance(statement._bindparams["campaign_id"].type, Uuid)
                self.assertIsInstance(statement._bindparams["tracking_link_id"].type, Uuid)
                if kind == "traffic_trends":
                    self.assertIsInstance(statement._bindparams["bucket"].type, Text)
                if kind == "user_list":
                    self.assertIsInstance(statement._bindparams["limit"].type, Integer)
                    self.assertIsInstance(statement._bindparams["offset"].type, Integer)

        self.assertEqual(captured_kinds, set(statement_markers))

    async def test_non_promotion_cohort_uses_runtime_attribution_source(self) -> None:
        connection = _FakeConnection(
            [
                {"homepage_pv": 5, "homepage_uv": 4, "link_pv": 0, "link_uv": 0},
                {
                    "registered_accounts": 1,
                    "promotion_registered_accounts": 0,
                    "called_accounts": 0,
                    "paid_accounts": 0,
                    "second_paid_accounts": 0,
                    "continued_accounts": 0,
                    "refunded_accounts": 0,
                },
            ]
        )

        await load_traffic_summary(
            connection,
            TrafficAnalyticsFilters(source_kind="direct"),
            self.window,
        )

        cohort_sql, params = connection.calls[1]
        self.assertIn("attribution.source_kind AS source_kind", cohort_sql)
        self.assertIn("attribution.source_kind = :source_kind", cohort_sql)
        self.assertEqual(params["source_kind"], "direct")

    async def test_promotion_dimensions_filter_homepage_summary_trends_and_sources(self) -> None:
        connection = _FakeConnection(
            [
                {"homepage_pv": 0, "homepage_uv": 0, "link_pv": 0, "link_uv": 0},
                {
                    "registered_accounts": 0,
                    "promotion_registered_accounts": 0,
                    "called_accounts": 0,
                    "paid_accounts": 0,
                    "second_paid_accounts": 0,
                    "continued_accounts": 0,
                    "refunded_accounts": 0,
                },
                [],
                [],
            ]
        )
        filters = TrafficAnalyticsFilters(
            site_id="aiwelink",
            channel_id=UUID("11111111-1111-1111-1111-111111111111"),
            campaign_id=UUID("22222222-2222-2222-2222-222222222222"),
            tracking_link_id=UUID("33333333-3333-3333-3333-333333333333"),
        )

        await load_traffic_summary(connection, filters, self.window)
        await load_traffic_trends(connection, filters, self.window)
        await load_source_breakdown(connection, filters, self.window)

        homepage_statements = [connection.calls[index][0] for index in (0, 2, 3)]
        for statement in homepage_statements:
            self.assertIn("homepage.active_site_id = :site_id", statement)
            self.assertIn("homepage.active_tracking_link_id = :tracking_link_id", statement)
            self.assertIn("active_link.campaign_id = :campaign_id", statement)
            self.assertIn("active_campaign.channel_id = :channel_id", statement)

    async def test_internal_segment_keeps_internal_accounts(self) -> None:
        connection = _FakeConnection(
            [
                {"homepage_pv": 0, "homepage_uv": 0, "link_pv": 0, "link_uv": 0},
                {
                    "registered_accounts": 1,
                    "promotion_registered_accounts": 0,
                    "called_accounts": 1,
                    "paid_accounts": 0,
                    "second_paid_accounts": 0,
                    "continued_accounts": 1,
                    "refunded_accounts": 0,
                },
            ]
        )

        await load_traffic_summary(
            connection,
            TrafficAnalyticsFilters(segment="internal"),
            self.window,
        )

        cohort_sql = connection.calls[1][0]
        self.assertIn("internal.internal_user_id IS NOT NULL", cohort_sql)
        self.assertNotIn("facts.is_excluded", cohort_sql)
        self.assertNotIn("excluded.external_user_id IS NULL", cohort_sql)

    async def test_amounts_are_returned_by_currency(self) -> None:
        connection = _FakeConnection(
            [
                [
                    {"currency": "CNY", "payment_total_minor": 3500, "refund_total_minor": 500},
                    {"currency": "USD", "payment_total_minor": 200, "refund_total_minor": 0},
                ]
            ]
        )

        result = await load_amounts(
            connection,
            TrafficAnalyticsFilters(segment="all", source_kind="direct"),
            self.window,
        )

        self.assertEqual([item["currency"] for item in result], ["CNY", "USD"])
        self.assertIn("GROUP BY facts.currency", connection.calls[0][0])
        self.assertIn("attribution.source_kind = :source_kind", connection.calls[0][0])
        self.assertIn("COALESCE(facts.is_excluded, FALSE) = FALSE", connection.calls[0][0])
        self.assertEqual(connection.calls[0][1]["source_kind"], "direct")

    async def test_link_performance_is_capped_and_uses_promotion_metadata_filters(self) -> None:
        connection = _FakeConnection([[]])
        filters = TrafficAnalyticsFilters(
            channel_id=UUID("11111111-1111-1111-1111-111111111111"),
            campaign_id=UUID("22222222-2222-2222-2222-222222222222"),
        )

        await load_link_performance(connection, filters, self.window)

        statement, params = connection.calls[0]
        self.assertIn("LIMIT 50", statement)
        self.assertIn("campaign.channel_id = :channel_id", statement)
        self.assertIn("link.campaign_id = :campaign_id", statement)
        link_traffic_sql = statement.split(", link_cohort AS", maxsplit=1)[0]
        self.assertIn("JOIN growth.tracking_links AS traffic_link", link_traffic_sql)
        self.assertIn("JOIN growth.campaigns AS traffic_campaign", link_traffic_sql)
        self.assertIn("traffic_link.campaign_id = :campaign_id", link_traffic_sql)
        self.assertIn("traffic_campaign.channel_id = :channel_id", link_traffic_sql)
        self.assertEqual(params["source_kind"], "promotion")

    async def test_trends_bucket_in_selected_site_timezone_or_utc(self) -> None:
        connection = _FakeConnection([[]])

        await load_traffic_trends(
            connection,
            TrafficAnalyticsFilters(site_id="aiwelink"),
            self.window,
        )

        statement = connection.calls[0][0]
        normalized_statement = " ".join(statement.split()).replace("( ", "(")
        self.assertIn("SELECT site.timezone", statement)
        self.assertIn("FROM growth.sites AS site", statement)
        self.assertIn("site.site_id = :site_id", statement)
        self.assertIn("'UTC'", statement)
        self.assertIn(
            "date_trunc(:bucket, homepage.visited_at,",
            normalized_statement,
        )
        self.assertIn(
            "date_trunc(:bucket, visit.visited_at,",
            normalized_statement,
        )
        self.assertIn(
            "date_trunc(:bucket, registered_at,",
            normalized_statement,
        )

    async def test_user_list_applies_milestone_and_pagination(self) -> None:
        connection = _FakeConnection(
            [
                {"total": 1},
                [
                    {
                        "site_id": "aiwelink",
                        "external_user_id": "42",
                        "account_label": "用户 42",
                        "is_internal": False,
                        "source_kind": "promotion",
                        "registered_at": datetime(2026, 7, 24, tzinfo=UTC),
                    }
                ],
            ]
        )
        query = TrafficUsersQuery(milestone="paid", limit=25, offset=50)

        items, total = await list_milestone_users(connection, query, self.window)

        self.assertEqual(total, 1)
        self.assertEqual(items[0]["external_user_id"], "42")
        list_statement, list_params = connection.calls[1]
        self.assertIn("facts.first_payment_at IS NOT NULL", list_statement)
        self.assertEqual(list_params["limit"], 25)
        self.assertEqual(list_params["offset"], 50)


class TrafficAnalyticsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_assembles_rates_on_one_growth_connection(self) -> None:
        from app.modules.growth import analytics_service

        connection = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = connection
        context.__aexit__.return_value = None
        summary = {
            "homepage_pv": 20,
            "homepage_uv": 10,
            "link_pv": 8,
            "link_uv": 4,
            "registered_accounts": 2,
            "promotion_registered_accounts": 1,
            "called_accounts": 1,
            "paid_accounts": 1,
            "second_paid_accounts": 0,
            "continued_accounts": 1,
            "refunded_accounts": 0,
        }

        with (
            patch.object(analytics_service, "growth_connection", return_value=context) as connection_mock,
            patch.object(analytics_service.repository, "load_traffic_summary", new=AsyncMock(return_value=summary)),
            patch.object(analytics_service.repository, "load_traffic_trends", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_source_breakdown", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_link_performance", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_amounts", new=AsyncMock(return_value=[])),
        ):
            result = await analytics_service.get_traffic_analytics_overview(
                object(),
                TrafficAnalyticsFilters(),
                now=datetime(2026, 7, 25, 8, 30, tzinfo=UTC),
            )

        connection_mock.assert_called_once()
        self.assertEqual(connection.execute.await_count, 2)
        readonly_statement = connection.execute.await_args_list[0].args[0]
        self.assertEqual(str(readonly_statement).strip(), "SET TRANSACTION READ ONLY")
        timeout_statement, timeout_params = connection.execute.await_args_list[1].args
        self.assertIn("set_config('statement_timeout'", str(timeout_statement))
        self.assertEqual(timeout_params["statement_timeout"], "5s")
        self.assertEqual(result["window"]["range"], "7d")
        self.assertEqual(result["window"]["timezone"], "UTC")
        self.assertEqual(result["rates"]["homepage_registration_rate"], 0.2)
        self.assertEqual(result["rates"]["link_registration_rate"], 0.25)
        self.assertEqual(result["rates"]["call_rate"], 0.5)
        self.assertEqual(result["rates"]["second_payment_rate"], 0.0)

    async def test_users_response_returns_stable_unique_public_ids_without_leaking_raw_ids(self) -> None:
        from app.modules.growth import analytics_service

        connection = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = connection
        context.__aexit__.return_value = None
        query = TrafficUsersQuery(limit=25, offset=50)
        secret_key = "analytics-test-secret"
        with (
            patch.object(analytics_service, "growth_connection", return_value=context),
            patch.object(
                analytics_service,
                "get_settings",
                return_value=SimpleNamespace(app_secret_key=secret_key),
                create=True,
            ),
            patch.object(
                analytics_service.repository,
                "list_milestone_users",
                new=AsyncMock(
                    return_value=(
                        [
                            {
                                "site_id": "aiwelink",
                                "external_user_id": "staff@example.com",
                                "account_label": "staff@example.com",
                            },
                            {
                                "site_id": "aiwelink",
                                "external_user_id": "susan@example.org",
                                "account_label": "susan@example.org",
                            },
                            {
                                "site_id": "aiwelink",
                                "external_user_id": "staff@example.com",
                                "account_label": "staff@example.com",
                            },
                        ],
                        76,
                    )
                ),
            ),
        ):
            result = await analytics_service.get_traffic_analytics_users(object(), query)

        items = result["items"]
        self.assertEqual(
            [item["external_user_id"] for item in items],
            ["s***@e***", "s***@e***", "s***@e***"],
        )
        self.assertEqual(
            [item["account_label"] for item in items],
            ["s***@e***", "s***@e***", "s***@e***"],
        )
        expected_staff_id = "usr_" + hmac.new(
            secret_key.encode("utf-8"),
            b"aiwelink\0staff@example.com",
            hashlib.sha256,
        ).hexdigest()[:32]
        public_user_ids = [item.get("public_user_id") for item in items]
        self.assertEqual(public_user_ids[0], expected_staff_id)
        self.assertNotEqual(public_user_ids[0], public_user_ids[1])
        self.assertEqual(public_user_ids[0], public_user_ids[2])
        self.assertNotIn("staff@example.com", str(result))
        self.assertNotIn("susan@example.org", str(result))
        self.assertEqual(connection.execute.await_count, 2)
        readonly_statement = connection.execute.await_args_list[0].args[0]
        self.assertEqual(str(readonly_statement).strip(), "SET TRANSACTION READ ONLY")
        timeout_statement, timeout_params = connection.execute.await_args_list[1].args
        self.assertIn("set_config('statement_timeout'", str(timeout_statement))
        self.assertEqual(timeout_params["statement_timeout"], "5s")
        self.assertEqual(result["total"], 76)
        self.assertEqual(result["limit"], 25)
        self.assertEqual(result["offset"], 50)


class _FakeMappings:
    def __init__(self, value):
        self.value = value

    def one(self):
        if isinstance(self.value, list):
            return self.value[0]
        return self.value

    def one_or_none(self):
        if isinstance(self.value, list):
            return self.value[0] if self.value else None
        return self.value

    def all(self):
        if self.value is None:
            return []
        return self.value if isinstance(self.value, list) else [self.value]


class _FakeResult:
    def __init__(self, value):
        self.value = value

    def mappings(self):
        return _FakeMappings(self.value)


class _FakeConnection:
    def __init__(self, values):
        self.values = list(values)
        self.calls: list[tuple[str, dict]] = []
        self.statements = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, statement, parameters=None):
        self.statements.append(statement)
        self.calls.append((str(statement), dict(parameters or {})))
        return _FakeResult(self.values.pop(0))


if __name__ == "__main__":
    unittest.main()
