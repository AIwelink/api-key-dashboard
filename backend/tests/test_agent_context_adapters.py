from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.agent import capacity, context_pack, decision_validator, event_stream, long_term_memory


class _AsyncCursor:
    def __init__(self, items):
        self.items = list(items)

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, value):
        self.items = self.items[:value]
        return self

    def __aiter__(self):
        async def iterator():
            for item in self.items:
                yield item

        return iterator()


class _Collection:
    def __init__(self, items):
        self.items = list(items)

    def find(self, *_args, **_kwargs):
        return _AsyncCursor(self.items)


class _Db:
    def __init__(self, *, client_sites=(), sub2api_sites=()):
        self.client_sites = _Collection(client_sites)
        self.sub2api_sites = _Collection(sub2api_sites)


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
                    "k12": {"five_hour_usd": 20, "seven_day_usd": 100},
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
                "realtime_risk_ready": True,
                "sample_count": 60,
                "concurrency_sample_count": 58,
                "latest_sampled_at": "2026-07-16T02:00:00+00:00",
                "pressure_stage": "rising",
                "pressure_stage_label": "Rising",
                "inventory_risk": True,
                "demand_ratio": 1.4,
                "tpm_momentum": 1.3,
                "pressure_tpm": 4200,
                "pressure_rpm": 48,
                "burn_usd_per_hour": 12.5,
                "actual_runway_hours": 1.4,
                "dynamic_runway_hours": 3.6,
                "target_runway_hours": 5,
                "actual_target_hours": 2,
                "estimated_concurrency": 12,
                "concurrency_ema_5": 10,
                "concurrency_p90_1h": 14,
                "concurrency_coverage": 0.9,
                "concurrency_target_coverage": 1.2,
                "replenishment_required": True,
                "quota_refill_accounts": 2,
                "concurrency_refill_accounts": 3,
                "recommended_refill_accounts": 3,
                "recommended_refill_options": {
                    "plus": {
                        "account_type": "plus",
                        "quota_refill_accounts": 2,
                        "concurrency_refill_accounts": 3,
                        "recommended_refill_accounts": 3,
                    },
                    "k12": {
                        "account_type": "k12",
                        "quota_refill_accounts": 8,
                        "concurrency_refill_accounts": 3,
                        "recommended_refill_accounts": 8,
                    },
                },
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
        self.assertEqual(result["schema_version"], "agent_capacity_status.v2")
        self.assertTrue(result["realtime_risk"]["ready"])
        self.assertEqual(result["realtime_risk"]["pressure_tpm"], 4200)
        self.assertEqual(result["realtime_risk"]["sample"]["sample_count"], 60)

    def test_concurrency_status_is_compact_and_keeps_limit_breakdown(self) -> None:
        result = capacity.build_agent_concurrency_status(self.raw_capacity)

        self.assertTrue(result["available"])
        self.assertEqual(result["safe_available"], 7)
        self.assertEqual(result["near_limit_available"], 6)
        self.assertEqual(result["temporarily_unavailable"], 21)
        self.assertEqual(result["accounts"]["five_hour_limited"], 2)
        self.assertEqual(result["accounts"]["short_seven_day_limited"], 1)
        self.assertEqual(result["schema_version"], "agent_concurrency_status.v2")
        self.assertEqual(result["estimated"], 12)
        self.assertEqual(result["coverage"], 0.9)

    def test_system_capacity_assessment_is_advisory_and_keeps_type_options(self) -> None:
        result = capacity.build_system_capacity_assessment(self.raw_capacity)

        self.assertTrue(result["ready"])
        self.assertTrue(result["advisory_only"])
        self.assertEqual(result["recommended_refill_accounts"], 3)
        self.assertEqual(result["account_type_options"]["k12"]["recommended_refill_accounts"], 8)
        self.assertEqual(result["account_type_options"]["k12"]["limits_usd"]["five_hour"], 20)
        self.assertEqual(result["account_type_options"]["k12"]["quota_profile"], "five_hour_and_seven_day")
        self.assertEqual(result["decision_boundary"], "evidence_only_llm_keeps_final_decision")

    def test_capability_view_does_not_expose_raw_capacity_summary(self) -> None:
        result = capacity.compact_agent_pool_capacity(self.raw_capacity)

        self.assertIn("capacity_status", result)
        self.assertIn("concurrency_status", result)
        self.assertIn("system_capacity_assessment", result)
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


class AgentTypedRefillDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.context = {
            "target_pool": {"account_type": "plus"},
            "data_quality": {"capacity_available": True, "probe_available": True},
            "system_capacity_assessment": {
                "primary_account_type": "plus",
                "account_type_options": {
                    "plus": {
                        "recommended_refill_accounts": 10,
                        "quota_refill_accounts": 10,
                        "concurrency_refill_accounts": 2,
                        "quota_profile": "seven_day_only_or_shared_quota",
                        "limits_usd": {"five_hour": 140, "seven_day": 140},
                    },
                    "k12": {
                        "recommended_refill_accounts": 40,
                        "quota_refill_accounts": 40,
                        "concurrency_refill_accounts": 2,
                        "quota_profile": "five_hour_and_seven_day",
                        "limits_usd": {"five_hour": 20, "seven_day": 100},
                    },
                },
            },
        }

    def test_validator_keeps_selected_type_and_alternative_plan(self) -> None:
        result = decision_validator.validate_agent_decision(
            {
                "severity": "warning",
                "suggested_add_count": 40,
                "suggested_account_type": "k12",
                "should_add_accounts": True,
                "suggested_refill_options": [
                    {"account_type": "k12", "suggested_add_count": 40, "selected": True, "reason": "Need 5h and 7d quota."},
                    {"account_type": "plus", "suggested_add_count": 10, "selected": False, "reason": "Alternative."},
                ],
            },
            context_pack=self.context,
        )

        self.assertEqual(result["suggested_account_type"], "k12")
        self.assertEqual(result["suggested_add_count"], 40)
        self.assertTrue(result["suggested_refill_options"][0]["selected"])
        self.assertEqual(result["suggested_refill_options"][0]["quota_profile"], "five_hour_and_seven_day")
        self.assertIn("K12 40", result["refill_plan_summary"])
        self.assertIn("Plus 10", result["refill_plan_summary"])

    def test_validator_rejects_type_outside_current_pool_options(self) -> None:
        result = decision_validator.validate_agent_decision(
            {
                "severity": "warning",
                "suggested_add_count": 10,
                "suggested_account_type": "pro",
                "should_add_accounts": True,
            },
            context_pack=self.context,
        )

        self.assertEqual(result["suggested_account_type"], "plus")
        self.assertTrue(
            any("not available for the current pool" in item for item in result["validator"]["warnings"])
        )


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


class AgentCapacityEvidenceTests(unittest.TestCase):
    def test_capacity_notification_consensus_is_windowed_without_raw_payload(self) -> None:
        now = datetime(2026, 7, 18, 8, tzinfo=UTC)
        event = event_stream._compact_capacity_notification_event(
            {
                "_id": "notification-1",
                "event_type": "sub2api.capacity.recovered",
                "severity": "success",
                "status": "success",
                "created_at": now - timedelta(minutes=10),
                "payload": {
                    "notification_type": "recovery",
                    "health_status": "healthy",
                    "trigger_reason": "recovered",
                    "capacity_summary": {"raw": "must not leak"},
                },
            }
        )
        summary = event_stream._capacity_consensus_for_window(
            {"current_state": "recovered", "active_alert": False, "events_7d": [event]},
            range_value="1h",
            now=now,
        )

        self.assertEqual(summary["recovery_count"], 1)
        self.assertNotIn("capacity_summary", event)

    def test_capacity_and_tpm_samples_are_aggregated_compactly(self) -> None:
        now = datetime(2026, 7, 18, 8, tzinfo=UTC)
        capacity_samples = [
            {
                "sampled_at": now,
                "capacity_summary": {
                    "available_accounts": 10,
                    "health_status": "healthy",
                    "pressure_stage": "steady",
                    "actual_runway_hours": 4,
                    "concurrency_coverage": 1.4,
                    "recommended_refill_accounts": 0,
                },
            },
            {
                "sampled_at": now + timedelta(minutes=5),
                "capacity_summary": {
                    "available_accounts": 7,
                    "health_status": "danger",
                    "pressure_stage": "rising",
                    "actual_runway_hours": 1,
                    "concurrency_coverage": 0.8,
                    "replenishment_required": True,
                    "recommended_refill_accounts": 3,
                    "recommended_refill_options": {"plus": {"recommended_refill_accounts": 3}},
                },
            },
        ]
        tpm_samples = [
            {"sampled_at": now, "tpm": 100, "rpm": 10, "current_concurrency": 2, "source": "reported"},
            {"sampled_at": now + timedelta(minutes=1), "tpm": 300, "rpm": 20, "current_concurrency": 5, "source": "reported"},
        ]

        capacity_result = long_term_memory._aggregate_capacity_samples(capacity_samples)
        pressure_result = long_term_memory._aggregate_tpm_samples(tpm_samples)

        self.assertEqual(capacity_result["available_accounts"]["change"], -3)
        self.assertEqual(capacity_result["actual_runway_hours"]["min"], 1)
        self.assertEqual(capacity_result["recommended_refill_by_account_type_max"]["plus"], 3)
        self.assertEqual(pressure_result["tpm"]["avg"], 200)
        self.assertEqual(pressure_result["concurrency"]["max"], 5)


class AgentClientSiteScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_scope_merges_client_sites_and_sub2api_sites(self) -> None:
        db = _Db(
            client_sites=[
                {"_id": "newapi-1", "name": "NewAPI", "client_type": "newapi", "status": "active"},
                {"_id": "disabled", "name": "Disabled", "client_type": "sub2api", "status": "disabled"},
            ],
            sub2api_sites=[
                {"_id": "sub2api-1", "name": "Sub2API", "site_type": "sub2api", "status": "active"},
            ],
        )

        result = await capacity.list_agent_site_scope(db)

        self.assertEqual([item["site_id"] for item in result["patrol_sites"]], ["sub2api-1"])
        self.assertEqual(
            {item["site_id"] for item in result["usage_attribution_sites"]},
            {"newapi-1", "sub2api-1"},
        )


if __name__ == "__main__":
    unittest.main()
