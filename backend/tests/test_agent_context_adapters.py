from __future__ import annotations

import unittest

from app.modules.agent import capacity, context_pack, event_stream


class AgentCapacityAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.raw_capacity = {
            "pool": {
                "id": "sub2api:api-5002:3",
                "site_id": "api-5002",
                "active_group_id": 3,
                "name": "Plus 主池",
                "account_type": "plus",
            },
            "site_id": "api-5002",
            "group_id": 3,
            "active_account_count": 0,
            "reserve_account_count": 2,
            "available_accounts": 5,
            "available_5h_accounts": 4,
            "total_account_count": 9,
            "cache_fresh": True,
            "last_refreshed_at": "2026-07-16T02:00:00+00:00",
            "data_source": "sub2api_groups_cache",
            "refresh_behavior": "read_existing_cache_only",
            "capacity_summary": {
                "account_type": "plus",
                "capacity_limits": {
                    "plus": {"five_hour_usd": 31, "seven_day_usd": 155},
                    "pro": {"five_hour_usd": 360, "seven_day_usd": 2100},
                },
                "active_available_accounts": 5,
                "pool_normal_accounts": 7,
                "pool_active_normal_accounts": 4,
                "pool_five_hour_rate_limited_accounts": 2,
                "pool_seven_day_rate_limited_accounts": 1,
                "pool_abnormal_accounts": 1,
                "pool_excluded_bug_team_accounts": 1,
                "capacity_duplicate_email_accounts": 2,
                "dynamic_five_hour_capacity_usd": 124,
                "dynamic_five_hour_used_estimated_usd": 44,
                "dynamic_five_hour_remaining_estimated_usd": 80,
                "five_hour_actual_used_usd": 50,
                "five_hour_actual_remaining_usd": 74,
                "available_5h_percent": 64.52,
                "active_available_5h_percent": 58.0,
                "actual_available_5h_percent": 59.68,
                "active_actual_available_5h_percent": 52.0,
                "seven_day_capacity_usd": 620,
                "seven_day_used_estimated_usd": 210,
                "seven_day_remaining_estimated_usd": 410,
                "seven_day_actual_used_usd": 225,
                "seven_day_actual_remaining_usd": 395,
                "available_7d_percent": 66.13,
                "actual_available_7d_percent": 63.71,
                "recent_24h_cost": 90,
                "seven_day_24h_peak_cost": 130,
                "recent_day_five_hour_peak_multiple": 0.8,
                "concurrency_actual_in_use": 4,
                "concurrency_actual_available": 13,
                "concurrency_safe_available": 7,
                "concurrency_near_limit_available": 6,
                "concurrency_temporarily_unavailable": 21,
                "concurrency_total_capacity": 38,
                "concurrency_used_percent": 10.53,
                "concurrency_available_percent": 34.21,
                "concurrency_eligible_accounts": 6,
                "concurrency_available_accounts": 3,
                "concurrency_safe_accounts": 1,
                "concurrency_near_limit_accounts": 2,
                "concurrency_temporarily_unavailable_accounts": 3,
                "concurrency_five_hour_limited_accounts": 2,
                "concurrency_short_seven_day_limited_accounts": 1,
                "concurrency_long_seven_day_limited_accounts": 1,
                "concurrency_other_unavailable_accounts": 0,
            },
        }

    def test_capacity_status_uses_site_scoped_account_limits(self) -> None:
        result = capacity.build_agent_capacity_status(self.raw_capacity)

        self.assertEqual(result["account_type"], "plus")
        self.assertEqual(result["account_limits_usd"]["five_hour"], 31)
        self.assertEqual(result["account_limits_usd"]["seven_day"], 155)
        self.assertEqual(result["accounts"]["active"], 0)
        self.assertEqual(result["pool_conditions"]["abnormal_accounts"], 1)
        self.assertEqual(result["five_hour"]["actual_available_percent"], 59.68)

    def test_concurrency_status_is_compact_and_keeps_limit_breakdown(self) -> None:
        result = capacity.build_agent_concurrency_status(self.raw_capacity)

        self.assertTrue(result["available"])
        self.assertEqual(result["safe_available"], 7)
        self.assertEqual(result["near_limit_available"], 6)
        self.assertEqual(result["temporarily_unavailable"], 21)
        self.assertEqual(result["accounts"]["five_hour_limited"], 2)
        self.assertEqual(result["accounts"]["short_seven_day_limited"], 1)

    def test_capability_view_does_not_expose_raw_capacity_summary(self) -> None:
        result = capacity.compact_agent_pool_capacity(self.raw_capacity)

        self.assertIn("capacity_status", result)
        self.assertIn("concurrency_status", result)
        self.assertNotIn("capacity_summary", result)
        self.assertNotIn("group", result)
        self.assertNotIn("cache_meta", result)

    def test_pro_limit_is_not_hard_coded_when_site_config_is_missing(self) -> None:
        raw = {"pool": {"account_type": "pro"}, "capacity_summary": {"account_type": "pro"}}

        self.assertIsNone(context_pack._single_account_5h_limit_usd(raw))
        self.assertIsNone(context_pack._single_account_7d_limit_usd(raw))

    def test_empty_capacity_summary_uses_pool_type_for_site_limits(self) -> None:
        raw = {
            "pool": {"account_type": "plus"},
            "capacity_summary": {
                "account_type": "total",
                "capacity_limits": {"plus": {"five_hour_usd": 28, "seven_day_usd": 140}},
            },
        }

        result = capacity.build_agent_capacity_status(raw)

        self.assertEqual(result["account_type"], "plus")
        self.assertEqual(result["account_limits_usd"]["five_hour"], 28)


class AgentEventContextTests(unittest.TestCase):
    def test_official_refresh_keeps_only_consensus_evidence(self) -> None:
        item = {
            "id": "event-1",
            "event_type": "official_usage_refresh",
            "detected_at": "2026-07-16T02:00:00+00:00",
            "email": "operator@example.com",
            "details": {
                "official_refresh_confirmed": True,
                "candidate_count": 4,
                "eligible_account_count": 5,
                "confirmed_account_types": ["plus"],
                "type_consensus": {
                    "plus": {
                        "confirmed": True,
                        "candidate_count": 4,
                        "eligible_account_count": 5,
                        "candidate_ratio": 0.8,
                        "ignored_raw_field": "do not expose",
                    }
                },
                "previous_used_percent": 73,
                "current_used_percent": 0,
                "unrelated_large_payload": {"raw": "ignored"},
            },
        }

        result = event_stream._event_detail(item, site_id="api-5002", group_id=3, pool_id="sub2api:api-5002:3")

        self.assertTrue(result["evidence"]["official_refresh_confirmed"])
        self.assertEqual(result["evidence"]["type_consensus"]["plus"]["candidate_ratio"], 0.8)
        self.assertNotIn("ignored_raw_field", result["evidence"]["type_consensus"]["plus"])
        self.assertNotIn("unrelated_large_payload", result["evidence"])

    def test_duplicate_resolution_and_401_recovery_are_explicit(self) -> None:
        duplicate = event_stream._event_detail(
            {
                "event_type": "duplicate_email_resolved",
                "details": {"previous_count": 3, "count": 1, "duplicate_state": "resolved"},
            },
            site_id="api-5002",
            group_id=3,
            pool_id="sub2api:api-5002:3",
        )
        recovered = event_stream._event_detail(
            {"event_type": "401_recovered", "details": {}},
            site_id="api-5002",
            group_id=3,
            pool_id="sub2api:api-5002:3",
        )

        self.assertEqual(duplicate["evidence"]["previous_remote_account_count"], 3)
        self.assertEqual(duplicate["evidence"]["current_remote_account_count"], 1)
        self.assertTrue(recovered["evidence"]["recovery_confirmed"])
        self.assertEqual(
            recovered["evidence"]["required_consecutive_healthy_probes"],
            event_stream.CONFIRMED_401_RECOVERY_COUNT,
        )

    def test_window_summary_promotes_new_event_types(self) -> None:
        items = [
            {"event_type": "official_usage_refresh", "detected_at": "2026-07-16T01:00:00+00:00", "identity_id": "a"},
            {"event_type": "duplicate_email_resolved", "detected_at": "2026-07-16T01:01:00+00:00", "identity_id": "b"},
            {"event_type": "401_recovered", "detected_at": "2026-07-16T01:02:00+00:00", "identity_id": "c"},
        ]
        result = event_stream._window_summary(
            range_value="24h",
            response={
                "total": 3,
                "limit": 300,
                "summary": {"official_usage_refreshes": 1, "recovered_401": 1},
            },
            items=items,
            site_id="api-5002",
            group_id=3,
            pool_id="sub2api:api-5002:3",
        )

        self.assertEqual(result["special_events"]["official_usage_refresh"]["confirmed_account_count"], 1)
        self.assertEqual(result["special_events"]["duplicate_email_resolved"]["event_count"], 1)
        self.assertEqual(result["special_events"]["confirmed_401_recovery"]["account_count"], 1)
        self.assertTrue(any("官方额度提前刷新" in item for item in result["interpretation"]))
        self.assertTrue(any("重复邮箱已解决" in item for item in result["interpretation"]))


if __name__ == "__main__":
    unittest.main()
