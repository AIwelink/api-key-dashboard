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
    list_registration_attributions,
    load_active_source_breakdown,
    load_classified_source_breakdown,
    load_data_quality,
    load_link_performance,
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
        now = datetime(2026, 7, 27, 8, 30, tzinfo=UTC)

        last_day = resolve_traffic_window("24h", now=now)
        last_week = resolve_traffic_window("7d", now=now)
        last_month = resolve_traffic_window("30d", now=now)
        last_quarter = resolve_traffic_window("90d", now=now)

        self.assertEqual(last_day.start_at, datetime(2026, 7, 26, 8, 30, tzinfo=UTC))
        self.assertEqual(last_day.bucket, "hour")
        self.assertEqual(last_week.start_at, datetime(2026, 7, 20, 8, 30, tzinfo=UTC))
        self.assertEqual(last_month.start_at, datetime(2026, 6, 27, 8, 30, tzinfo=UTC))
        self.assertEqual(last_quarter.start_at, datetime(2026, 4, 28, 8, 30, tzinfo=UTC))
        self.assertEqual(last_week.bucket, "day")

    def test_safe_rate_preserves_missing_denominator(self) -> None:
        self.assertIsNone(safe_rate(3, 0))
        self.assertEqual(safe_rate(9, 12), 0.75)

    def test_filters_trim_site_and_force_promotion_for_link_dimensions(self) -> None:
        filters = TrafficAnalyticsFilters(
            site_id=" aiwelink ",
            source_kind="direct",
            channel_id=UUID("11111111-1111-1111-1111-111111111111"),
        )

        self.assertEqual(filters.site_id, "aiwelink")
        self.assertEqual(filters.source_kind, "promotion")

    def test_registration_query_only_controls_pagination(self) -> None:
        query = TrafficUsersQuery(limit=25, offset=50)

        self.assertEqual(query.limit, 25)
        self.assertEqual(query.offset, 50)
        self.assertFalse(hasattr(query, "milestone"))
        with self.assertRaises(ValueError):
            TrafficUsersQuery(limit=101)


class TrafficAnalyticsRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.window = resolve_traffic_window(
            "7d",
            now=datetime(2026, 7, 27, 8, 30, tzinfo=UTC),
        )

    async def test_summary_separates_recorded_counted_excluded_and_pending(self) -> None:
        connection = _FakeConnection(
            [
                {
                    "homepage_recorded_visits": 12,
                    "homepage_counted_pv": 9,
                    "homepage_session_uv": 5,
                    "homepage_excluded_visits": 3,
                    "homepage_latest_event_at": datetime(2026, 7, 27, tzinfo=UTC),
                    "link_recorded_visits": 8,
                    "link_counted_pv": 6,
                    "link_session_uv": 4,
                    "link_excluded_visits": 2,
                    "link_attribution_updates": 3,
                    "bucket_timezone": "Asia/Shanghai",
                },
                {
                    "attributed_accounts": 5,
                    "excluded_accounts": 1,
                    "facts_pending_accounts": 2,
                },
            ]
        )

        result = await load_traffic_summary(
            connection,
            TrafficAnalyticsFilters(segment="ordinary", site_id="aiwelink"),
            self.window,
        )

        self.assertEqual(result["homepage_counted_pv"], 9)
        self.assertEqual(result["homepage_excluded_visits"], 3)
        self.assertEqual(result["link_attribution_updates"], 3)
        self.assertEqual(result["facts_pending_accounts"], 2)
        traffic_sql, cohort_sql = [statement for statement, _ in connection.calls]
        self.assertIn("COUNT(*) FILTER (WHERE homepage.is_counted)", traffic_sql)
        self.assertIn("COUNT(DISTINCT homepage.anonymous_visitor_key)", traffic_sql)
        self.assertIn("COUNT(*) FILTER (WHERE NOT homepage.is_counted)", traffic_sql)
        self.assertIn("visit.is_attribution_update", traffic_sql)
        self.assertIn("facts.external_user_id IS NULL", cohort_sql)
        self.assertIn("facts.is_excluded", cohort_sql)
        self.assertNotIn("first_successful_call_at", cohort_sql)
        self.assertNotIn("first_payment_at", cohort_sql)

    async def test_active_sources_use_last_counted_event_for_exclusive_session_uv(self) -> None:
        connection = _FakeConnection(
            [[{"source_kind": "promotion", "counted_pv": 5, "session_uv": 2}]]
        )

        result = await load_active_source_breakdown(
            connection,
            TrafficAnalyticsFilters(site_id="aiwelink"),
            self.window,
        )

        self.assertEqual(result[0]["session_uv"], 2)
        statement = " ".join(connection.calls[0][0].split())
        self.assertIn("ROW_NUMBER() OVER", statement)
        self.assertIn("PARTITION BY homepage.anonymous_visitor_key", statement)
        self.assertIn("ORDER BY homepage.visited_at DESC, homepage.page_view_id DESC", statement)
        self.assertIn("homepage.is_counted", statement)
        self.assertIn("WHERE visitor_rank = 1", statement)

    async def test_classified_sources_are_a_separate_natural_entry_query(self) -> None:
        connection = _FakeConnection(
            [[{"source_kind": "direct", "counted_pv": 7, "session_uv": 3}]]
        )

        await load_classified_source_breakdown(
            connection,
            TrafficAnalyticsFilters(source_kind="promotion"),
            self.window,
        )

        statement = connection.calls[0][0]
        self.assertIn("homepage.classified_source_kind", statement)
        self.assertIn("homepage.active_source_kind = :source_kind", statement)
        self.assertNotIn("ROW_NUMBER()", statement)

    async def test_link_performance_includes_quality_status_and_registration_fields(self) -> None:
        connection = _FakeConnection([[]])
        filters = TrafficAnalyticsFilters(
            channel_id=UUID("11111111-1111-1111-1111-111111111111"),
            campaign_id=UUID("22222222-2222-2222-2222-222222222222"),
        )

        await load_link_performance(connection, filters, self.window)

        statement, params = connection.calls[0]
        self.assertIn("recorded_visits", statement)
        self.assertIn("excluded_visits", statement)
        self.assertIn("attribution_updates", statement)
        self.assertIn("link.valid_from", statement)
        self.assertIn("link.valid_until", statement)
        self.assertIn("link.status", statement)
        self.assertIn("fact_state = 'normal'", statement)
        self.assertNotIn("fact_state <> 'excluded'", statement)
        self.assertNotIn("called_accounts", statement)
        self.assertNotIn("paid_accounts", statement)
        self.assertEqual(params["source_kind"], "promotion")

    async def test_quality_query_reports_exclusions_bots_redirects_http_and_freshness(self) -> None:
        connection = _FakeConnection(
            [
                [
                    {"event_scope": "homepage", "reason": "bot", "event_count": 2},
                    {"event_scope": "link", "reason": "unclassified", "event_count": 1},
                ],
                {
                    "homepage_bot_visits": 2,
                    "link_bot_visits": 1,
                    "latest_source_data_fresh_at": None,
                    "latest_computed_at": None,
                    "facts_delay_seconds": None,
                },
                [{"redirect_result": "redirected", "event_count": 4}],
                [{"http_status": 302, "event_count": 4}],
            ]
        )

        result = await load_data_quality(
            connection,
            TrafficAnalyticsFilters(site_id="aiwelink"),
            self.window,
        )

        self.assertEqual(result["homepage_bot_visits"], 2)
        self.assertEqual(result["exclusion_reasons"][1]["reason"], "unclassified")
        sql = "\n".join(statement for statement, _ in connection.calls)
        self.assertIn("exclusion_reason", sql)
        self.assertIn("is_bot", sql)
        self.assertIn("redirect_result", sql)
        self.assertIn("http_status", sql)
        self.assertIn("source_data_fresh_at", sql)
        self.assertIn("computed_at", sql)

    async def test_registration_list_preserves_fact_state_and_omits_downstream_values(self) -> None:
        connection = _FakeConnection(
            [
                {"total": 1},
                [
                    {
                        "site_id": "aiwelink",
                        "external_user_id": "42",
                        "account_label": "用户 42",
                        "source_kind": "promotion",
                        "fact_state": "facts_pending",
                        "registered_at": datetime(2026, 7, 27, tzinfo=UTC),
                    }
                ],
            ]
        )
        query = TrafficUsersQuery(limit=25, offset=50)

        items, total = await list_registration_attributions(connection, query, self.window)

        self.assertEqual(total, 1)
        self.assertEqual(items[0]["fact_state"], "facts_pending")
        count_sql, _ = connection.calls[0]
        list_sql, list_params = connection.calls[1]
        self.assertIn("user_attributions", count_sql)
        self.assertIn("facts_pending", list_sql)
        self.assertIn("source_touch_at", list_sql)
        self.assertNotIn("first_successful_call_at", list_sql)
        self.assertNotIn("first_payment_at", list_sql)
        self.assertNotIn("evidence_hash", list_sql)
        self.assertNotIn("anonymous_visitor_key", list_sql)
        self.assertEqual(list_params["limit"], 25)
        self.assertEqual(list_params["offset"], 50)

    async def test_all_statements_type_nullable_filters(self) -> None:
        connection = _FakeConnection(
            [
                {}, {}, [], [], [], [], [], {}, [], [], {"total": 0}, [],
            ]
        )
        filters = TrafficAnalyticsFilters(site_id="aiwelink-main")
        query = TrafficUsersQuery(site_id="aiwelink-main")

        await load_traffic_summary(connection, filters, self.window)
        await load_traffic_trends(connection, filters, self.window)
        await load_active_source_breakdown(connection, filters, self.window)
        await load_classified_source_breakdown(connection, filters, self.window)
        await load_link_performance(connection, filters, self.window)
        await load_data_quality(connection, filters, self.window)
        await list_registration_attributions(connection, query, self.window)

        for statement in connection.statements:
            bind_names = set(statement._bindparams)
            self.assertTrue(
                {"start_at", "end_at", "site_id", "source_kind", "channel_id", "campaign_id", "tracking_link_id"}
                <= bind_names
            )
            self.assertIsInstance(statement._bindparams["start_at"].type, DateTime)
            self.assertTrue(statement._bindparams["start_at"].type.timezone)
            self.assertIsInstance(statement._bindparams["site_id"].type, Text)
            self.assertIsInstance(statement._bindparams["channel_id"].type, Uuid)
            if "limit" in bind_names:
                self.assertIsInstance(statement._bindparams["limit"].type, Integer)


class TrafficAnalyticsServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_overview_exposes_confirmed_contract_without_misleading_rates(self) -> None:
        from app.modules.growth import analytics_service

        connection = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = connection
        context.__aexit__.return_value = None
        summary = {
            "homepage_recorded_visits": 12,
            "homepage_counted_pv": 9,
            "homepage_session_uv": 5,
            "homepage_excluded_visits": 3,
            "homepage_latest_event_at": "2026-07-27T08:00:00+00:00",
            "link_recorded_visits": 8,
            "link_counted_pv": 6,
            "link_session_uv": 4,
            "link_excluded_visits": 2,
            "link_attribution_updates": 3,
            "attributed_accounts": 5,
            "excluded_accounts": 1,
            "facts_pending_accounts": 2,
            "bucket_timezone": "Asia/Shanghai",
        }
        quality = {
            "exclusion_reasons": [],
            "homepage_bot_visits": 0,
            "link_bot_visits": 0,
            "redirect_results": [],
            "http_statuses": [],
            "latest_source_data_fresh_at": None,
            "latest_computed_at": None,
            "facts_delay_seconds": None,
        }

        with (
            patch.object(analytics_service, "growth_connection", return_value=context),
            patch.object(analytics_service.repository, "load_traffic_summary", new=AsyncMock(return_value=summary)),
            patch.object(analytics_service.repository, "load_traffic_trends", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_active_source_breakdown", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_classified_source_breakdown", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_link_performance", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_data_quality", new=AsyncMock(return_value=quality)),
        ):
            result = await analytics_service.get_traffic_analytics_overview(
                object(),
                TrafficAnalyticsFilters(),
                now=datetime(2026, 7, 27, 8, 30, tzinfo=UTC),
            )

        self.assertEqual(result["homepage_summary"]["counted_pv"], 9)
        self.assertEqual(result["homepage_summary"]["valid_rate"], 0.75)
        self.assertEqual(result["link_summary"]["attribution_updates"], 3)
        self.assertEqual(result["registration_summary"]["facts_pending_accounts"], 2)
        self.assertEqual(result["capabilities"]["downstream_facts"], "unavailable")
        self.assertNotIn("rates", result)
        self.assertNotIn("amounts", result)
        self.assertNotIn("called_accounts", str(result))
        self.assertEqual(connection.execute.await_count, 2)

    async def test_overview_returns_null_valid_rate_when_no_homepage_records(self) -> None:
        from app.modules.growth import analytics_service

        connection = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = connection
        context.__aexit__.return_value = None
        summary = {
            "homepage_recorded_visits": 0,
            "homepage_counted_pv": 0,
            "homepage_session_uv": 0,
            "homepage_excluded_visits": 0,
            "link_recorded_visits": 0,
            "link_counted_pv": 0,
            "link_session_uv": 0,
            "link_excluded_visits": 0,
            "link_attribution_updates": 0,
            "attributed_accounts": 0,
            "excluded_accounts": 0,
            "facts_pending_accounts": 0,
        }
        with (
            patch.object(analytics_service, "growth_connection", return_value=context),
            patch.object(analytics_service.repository, "load_traffic_summary", new=AsyncMock(return_value=summary)),
            patch.object(analytics_service.repository, "load_traffic_trends", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_active_source_breakdown", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_classified_source_breakdown", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_link_performance", new=AsyncMock(return_value=[])),
            patch.object(analytics_service.repository, "load_data_quality", new=AsyncMock(return_value={})),
        ):
            result = await analytics_service.get_traffic_analytics_overview(
                object(), TrafficAnalyticsFilters()
            )

        self.assertIsNone(result["homepage_summary"]["valid_rate"])

    async def test_users_response_masks_identifiers_and_preserves_fact_state(self) -> None:
        from app.modules.growth import analytics_service

        connection = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = connection
        context.__aexit__.return_value = None
        secret_key = "analytics-test-secret"
        rows = [
            {
                "site_id": "aiwelink",
                "external_user_id": "staff@example.com",
                "account_label": "staff@example.com",
                "fact_state": "facts_pending",
            }
        ]
        with (
            patch.object(analytics_service, "growth_connection", return_value=context),
            patch.object(analytics_service, "get_settings", return_value=SimpleNamespace(app_secret_key=secret_key)),
            patch.object(
                analytics_service.repository,
                "list_registration_attributions",
                new=AsyncMock(return_value=(rows, 1)),
            ),
        ):
            result = await analytics_service.get_traffic_analytics_users(
                object(), TrafficUsersQuery(limit=25, offset=50)
            )

        expected_id = "usr_" + hmac.new(
            secret_key.encode(), b"aiwelink\0staff@example.com", hashlib.sha256
        ).hexdigest()[:32]
        self.assertEqual(result["items"][0]["public_user_id"], expected_id)
        self.assertEqual(result["items"][0]["external_user_id"], "s***@e***")
        self.assertEqual(result["items"][0]["fact_state"], "facts_pending")
        self.assertNotIn("staff@example.com", str(result))


class _FakeMappings:
    def __init__(self, value):
        self.value = value

    def one(self):
        if isinstance(self.value, list):
            return self.value[0]
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
