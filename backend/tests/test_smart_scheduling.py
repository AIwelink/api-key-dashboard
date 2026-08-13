from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.sub2api.smart_scheduling import (
    adapted_scheduling_type,
    build_type_priority_queue,
    default_smart_scheduling_rules,
    evaluate_account,
    normalize_smart_scheduling_rules,
)


class SmartSchedulingDefaultsTests(unittest.TestCase):
    def test_defaults_match_confirmed_priority_and_concurrency_rules(self) -> None:
        rules = default_smart_scheduling_rules()

        self.assertEqual(rules["account_types"]["plus"]["automatic_priority"], 191)
        self.assertEqual(rules["account_types"]["k12"]["automatic_priority"], 91)
        self.assertEqual(rules["account_types"]["team"]["automatic_priority"], 41)
        self.assertEqual(rules["account_types"]["pro"]["automatic_priority"], 991)
        self.assertEqual(rules["account_types"]["plus"]["extreme_entry_percent"], 90.0)
        self.assertEqual(rules["account_types"]["pro"]["extreme_entry_percent"], 95.0)
        self.assertEqual(rules["account_types"]["pro"]["normal_concurrency"], 30)
        self.assertEqual(rules["account_types"]["pro"]["extreme_concurrency"], 100)
        self.assertEqual(
            rules["extreme"],
            {"priority_min": 1, "priority_max": 20, "priority": 10},
        )

    def test_defaults_are_returned_as_an_independent_copy(self) -> None:
        first = default_smart_scheduling_rules()
        first["account_types"]["plus"]["automatic_priority"] = 9999

        second = default_smart_scheduling_rules()

        self.assertEqual(second["account_types"]["plus"]["automatic_priority"], 191)

    def test_normalizer_fills_missing_values_from_defaults(self) -> None:
        rules = normalize_smart_scheduling_rules(
            {"account_types": {"plus": {"normal_concurrency": 40}}}
        )

        self.assertEqual(rules["account_types"]["plus"]["normal_concurrency"], 40)
        self.assertEqual(rules["account_types"]["plus"]["automatic_priority"], 191)
        self.assertEqual(rules["account_types"]["pro"]["automatic_priority"], 991)

    def test_rejects_overlapping_priority_bands(self) -> None:
        rules = default_smart_scheduling_rules()
        rules["account_types"]["plus"]["system_priority_max"] = 205

        with self.assertRaisesRegex(ValueError, "priority bands"):
            normalize_smart_scheduling_rules(rules)

    def test_rejects_fixed_priority_outside_system_band(self) -> None:
        rules = default_smart_scheduling_rules()
        rules["account_types"]["k12"]["automatic_priority"] = 100

        with self.assertRaisesRegex(ValueError, "automatic priority"):
            normalize_smart_scheduling_rules(rules)

    def test_rejects_recovery_at_or_above_entry_threshold(self) -> None:
        rules = default_smart_scheduling_rules()
        rules["account_types"]["plus"]["recovery_percent"] = 90

        with self.assertRaisesRegex(ValueError, "recovery"):
            normalize_smart_scheduling_rules(rules)

    def test_rejects_extreme_band_that_is_not_ahead_of_normal_bands(self) -> None:
        rules = default_smart_scheduling_rules()
        rules["extreme"] = {"priority_min": 40, "priority_max": 60, "priority": 50}

        with self.assertRaisesRegex(ValueError, "extreme"):
            normalize_smart_scheduling_rules(rules)

    def test_rejects_cross_type_band_overlap(self) -> None:
        rules = default_smart_scheduling_rules()
        rules["account_types"]["plus"]["system_priority_min"] = 180

        with self.assertRaisesRegex(ValueError, "overlap"):
            normalize_smart_scheduling_rules(rules)


class SmartSchedulingDecisionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 27, 7, 0, tzinfo=UTC)
        self.rules = default_smart_scheduling_rules()

    def account(
        self,
        account_type: str,
        *,
        priority: int,
        concurrency: int = 30,
        used: float | None = 20,
        sampled_at: datetime | None = None,
        reset_at: datetime | None = None,
        status: str = "active",
        error_message: str | None = None,
    ) -> dict[str, object]:
        usage: dict[str, object] = {}
        if used is not None:
            usage["codex_7d_used_percent"] = used
        if sampled_at is not None or used is not None:
            usage["codex_usage_synced_at"] = (sampled_at or self.now).isoformat()
        if reset_at is not None or used is not None:
            usage["codex_7d_reset_at"] = (
                reset_at or (self.now + timedelta(days=3))
            ).isoformat()
        return {
            "remote_account_id": 7,
            "account_type": account_type,
            "priority": priority,
            "concurrency": concurrency,
            "group_ids": [3],
            "status": status,
            "error_message": error_message,
            "usage_snapshot": usage,
        }

    def evaluate(
        self,
        account: dict[str, object],
        *,
        type_enabled: bool = True,
        quota_enabled: bool = True,
        state: dict[str, object] | None = None,
    ) -> dict[str, object]:
        return evaluate_account(
            account=account,
            rules=self.rules,
            type_priority_enabled=type_enabled,
            quota_acceleration_enabled=quota_enabled,
            state=state,
            now=self.now,
        )

    def queue_entry(
        self,
        remote_id: int,
        *,
        account_type: str = "team",
        created_at: str | None = None,
        priority: int = 50,
        status: str = "active",
        schedulable: bool | None = True,
        error_message: str | None = None,
        mode: str | None = None,
        used: float | None = 20,
        type_enabled: bool = True,
        quota_enabled: bool = False,
    ) -> dict[str, object]:
        account = self.account(
            account_type,
            priority=priority,
            used=used,
            status=status,
            error_message=error_message,
        )
        account["remote_account_id"] = remote_id
        account["created_at"] = created_at
        account["schedulable"] = schedulable
        return {
            "remote_account_id": remote_id,
            "account": account,
            "state": {"mode": mode} if mode else None,
            "type_priority_enabled": type_enabled,
            "quota_acceleration_enabled": quota_enabled,
        }

    def test_oldest_usable_accounts_receive_contiguous_type_priorities(self) -> None:
        plan = build_type_priority_queue(
            [
                self.queue_entry(3, created_at="2026-01-03T00:00:00+00:00"),
                self.queue_entry(1, created_at="2026-01-01T00:00:00+00:00"),
                self.queue_entry(2, created_at="2026-01-02T00:00:00+00:00"),
            ],
            rules=self.rules,
            now=self.now,
        )

        self.assertEqual(
            {account_id: data["priority"] for account_id, data in plan.items()},
            {"1": 50, "2": 51, "3": 52},
        )
        self.assertEqual(plan["1"]["queue_partition"], "usable")
        self.assertEqual(plan["2"]["queue_index"], 1)

    def test_unusable_oldest_moves_after_usable_and_recovery_returns_head(self) -> None:
        entries = [
            self.queue_entry(1, created_at="2026-01-01T00:00:00+00:00"),
            self.queue_entry(2, created_at="2026-01-02T00:00:00+00:00"),
            self.queue_entry(3, created_at="2026-01-03T00:00:00+00:00"),
        ]
        entries[0]["account"]["error_message"] = "API returned 429"

        unavailable = build_type_priority_queue(entries, rules=self.rules, now=self.now)

        self.assertEqual(
            {account_id: data["priority"] for account_id, data in unavailable.items()},
            {"2": 50, "3": 51, "1": 52},
        )
        self.assertEqual(
            unavailable["1"]["queue_partition"], "temporarily_unusable"
        )

        entries[0]["account"]["error_message"] = None
        recovered = build_type_priority_queue(entries, rules=self.rules, now=self.now)

        self.assertEqual(recovered["1"]["priority"], 50)
        self.assertEqual(recovered["1"]["queue_partition"], "usable")

    def test_queue_tie_breaks_by_remote_id_and_missing_created_at_is_last(self) -> None:
        plan = build_type_priority_queue(
            [
                self.queue_entry(8, created_at=None),
                self.queue_entry(2, created_at="2026-01-01T00:00:00+00:00"),
                self.queue_entry(1, created_at="2026-01-01T00:00:00+00:00"),
            ],
            rules=self.rules,
            now=self.now,
        )

        self.assertEqual(plan["1"]["priority"], 50)
        self.assertEqual(plan["2"]["priority"], 51)
        self.assertEqual(plan["8"]["priority"], 52)
        self.assertIsNone(plan["8"]["queue_created_at"])

    def test_each_type_uses_its_configured_manual_priority_minimum(self) -> None:
        plan = build_type_priority_queue(
            [
                self.queue_entry(1, account_type="team"),
                self.queue_entry(2, account_type="k12", priority=100),
                self.queue_entry(3, account_type="plus", priority=200),
                self.queue_entry(4, account_type="pro", priority=1000),
            ],
            rules=self.rules,
            now=self.now,
        )

        self.assertEqual(
            {account_id: data["priority"] for account_id, data in plan.items()},
            {"1": 50, "2": 100, "3": 200, "4": 1000},
        )

    def test_queue_clamps_overflow_to_manual_band_maximum(self) -> None:
        plan = build_type_priority_queue(
            [self.queue_entry(remote_id) for remote_id in range(1, 45)],
            rules=self.rules,
            now=self.now,
        )

        self.assertEqual(plan["1"]["priority"], 50)
        self.assertEqual(plan["41"]["priority"], 90)
        self.assertEqual(plan["44"]["priority"], 90)

    def test_extreme_and_pending_accounts_do_not_consume_normal_slots(self) -> None:
        plan = build_type_priority_queue(
            [
                self.queue_entry(
                    1,
                    created_at="2026-01-01T00:00:00+00:00",
                    mode="extreme",
                    used=90,
                    quota_enabled=True,
                ),
                self.queue_entry(
                    2,
                    created_at="2026-01-02T00:00:00+00:00",
                    mode="rate_limit_pending",
                    used=90,
                    quota_enabled=True,
                ),
                self.queue_entry(3, created_at="2026-01-03T00:00:00+00:00"),
            ],
            rules=self.rules,
            now=self.now,
        )

        self.assertNotIn("1", plan)
        self.assertNotIn("2", plan)
        self.assertEqual(plan["3"]["priority"], 50)

    def test_owned_extreme_state_never_consumes_a_normal_slot(self) -> None:
        plan = build_type_priority_queue(
            [
                self.queue_entry(
                    1,
                    created_at="2026-01-01T00:00:00+00:00",
                    mode="extreme",
                    quota_enabled=False,
                ),
                self.queue_entry(2, created_at="2026-01-02T00:00:00+00:00"),
            ],
            rules=self.rules,
            now=self.now,
        )

        self.assertNotIn("1", plan)
        self.assertEqual(plan["2"]["priority"], 50)

    def test_recovered_extreme_state_rejoins_the_normal_queue(self) -> None:
        entry = self.queue_entry(
            1,
            created_at="2026-01-01T00:00:00+00:00",
            mode="extreme",
            used=79.9,
            quota_enabled=True,
        )

        plan = build_type_priority_queue([entry], rules=self.rules, now=self.now)

        self.assertEqual(plan["1"]["priority"], 50)
        self.assertEqual(plan["1"]["queue_partition"], "usable")

    def test_elapsed_rate_limit_pending_rejoins_the_unusable_tail(self) -> None:
        entry = self.queue_entry(
            1,
            created_at="2026-01-01T00:00:00+00:00",
            mode="rate_limit_pending",
            used=90,
            quota_enabled=True,
        )
        entry["state"]["rate_limit_detected_at"] = (
            self.now - timedelta(minutes=31)
        ).isoformat()

        plan = build_type_priority_queue([entry], rules=self.rules, now=self.now)

        self.assertEqual(plan["1"]["priority"], 50)
        self.assertEqual(
            plan["1"]["queue_partition"], "temporarily_unusable"
        )

    def test_type_aliases_share_team_queue_and_disabled_entries_are_ignored(self) -> None:
        plan = build_type_priority_queue(
            [
                self.queue_entry(
                    1,
                    account_type="bug_team",
                    created_at="2026-01-01T00:00:00+00:00",
                ),
                self.queue_entry(
                    2,
                    account_type="special_team",
                    created_at="2026-01-02T00:00:00+00:00",
                ),
                self.queue_entry(3, type_enabled=False),
            ],
            rules=self.rules,
            now=self.now,
        )

        self.assertEqual(plan["1"]["priority"], 50)
        self.assertEqual(plan["2"]["priority"], 51)
        self.assertNotIn("3", plan)

    def test_unusable_conditions_share_the_tail_partition(self) -> None:
        entries = [
            self.queue_entry(1, schedulable=False),
            self.queue_entry(2, status="error"),
            self.queue_entry(3, error_message="API returned 403"),
            self.queue_entry(4, mode="rate_limited_cooldown"),
            self.queue_entry(5, status="", schedulable=True),
            self.queue_entry(6, status="active", schedulable=None),
        ]

        plan = build_type_priority_queue(entries, rules=self.rules, now=self.now)

        self.assertEqual(
            {data["queue_partition"] for data in plan.values()},
            {"temporarily_unusable"},
        )

    def test_adapted_types_use_the_supported_scheduling_tiers(self) -> None:
        self.assertEqual(adapted_scheduling_type("plus"), "plus")
        self.assertEqual(adapted_scheduling_type("special_plus"), "plus")
        self.assertEqual(adapted_scheduling_type("team"), "team")
        self.assertEqual(adapted_scheduling_type("bug_team"), "team")
        self.assertEqual(adapted_scheduling_type("special_team"), "team")
        self.assertEqual(adapted_scheduling_type("k12"), "k12")
        self.assertEqual(adapted_scheduling_type("pro"), "pro")
        self.assertIsNone(adapted_scheduling_type("free"))
        self.assertIsNone(adapted_scheduling_type("unknown"))

    def test_both_disabled_skips_without_a_target(self) -> None:
        decision = self.evaluate(
            self.account("plus", priority=300),
            type_enabled=False,
            quota_enabled=False,
        )

        self.assertEqual(decision["status"], "skipped")
        self.assertEqual(decision["reason"], "strategies_disabled")
        self.assertIsNone(decision["target"])

    def test_legal_manual_priority_is_preserved_while_concurrency_is_corrected(self) -> None:
        decision = self.evaluate(
            self.account("plus", priority=250, concurrency=20),
            quota_enabled=False,
        )

        self.assertEqual(decision["status"], "change")
        self.assertEqual(decision["strategy"], "type_priority")
        self.assertEqual(decision["mode"], "normal")
        self.assertEqual(decision["target"], {"priority": 250, "concurrency": 30})

    def test_legal_system_priority_is_preserved(self) -> None:
        decision = self.evaluate(
            self.account("k12", priority=95, concurrency=30),
            quota_enabled=False,
        )

        self.assertEqual(decision["status"], "unchanged")
        self.assertEqual(decision["target"], {"priority": 95, "concurrency": 30})

    def test_out_of_band_priority_uses_fixed_automatic_value(self) -> None:
        expected = {"pro": 991, "plus": 191, "k12": 91, "team": 41}

        for account_type, priority in expected.items():
            with self.subTest(account_type=account_type):
                decision = self.evaluate(
                    self.account(account_type, priority=5000),
                    quota_enabled=False,
                )
                self.assertEqual(
                    decision["target"], {"priority": priority, "concurrency": 30}
                )

    def test_extreme_precedes_type_normalization_at_exact_threshold(self) -> None:
        decision = self.evaluate(self.account("plus", priority=250, used=90))

        self.assertEqual(decision["status"], "change")
        self.assertEqual(decision["strategy"], "quota_acceleration")
        self.assertEqual(decision["mode"], "extreme")
        self.assertEqual(decision["target"], {"priority": 10, "concurrency": 100})

    def test_k12_and_team_enter_extreme_at_ninety_percent(self) -> None:
        for account_type in ("k12", "team", "bug_team", "special_team"):
            with self.subTest(account_type=account_type):
                decision = self.evaluate(
                    self.account(account_type, priority=100, used=90)
                )
                self.assertEqual(decision["mode"], "extreme")
                self.assertEqual(
                    decision["target"], {"priority": 10, "concurrency": 100}
                )

    def test_pro_enters_extreme_at_ninety_five_not_ninety(self) -> None:
        normal = self.evaluate(self.account("pro", priority=1000, used=94.9))
        extreme = self.evaluate(self.account("pro", priority=1000, used=95))

        self.assertEqual(normal["mode"], "normal")
        self.assertEqual(normal["target"], {"priority": 1000, "concurrency": 30})
        self.assertEqual(extreme["mode"], "extreme")
        self.assertEqual(extreme["target"], {"priority": 10, "concurrency": 100})

    def test_quota_strategy_alone_does_nothing_below_threshold(self) -> None:
        decision = self.evaluate(
            self.account("plus", priority=300, concurrency=20, used=89.9),
            type_enabled=False,
            quota_enabled=True,
        )

        self.assertEqual(decision["status"], "skipped")
        self.assertEqual(decision["reason"], "quota_below_threshold")
        self.assertIsNone(decision["target"])

    def test_stale_quota_does_not_enter_extreme_but_type_strategy_still_runs(self) -> None:
        decision = self.evaluate(
            self.account(
                "plus",
                priority=300,
                used=99,
                sampled_at=self.now - timedelta(minutes=6),
            )
        )

        self.assertEqual(decision["mode"], "normal")
        self.assertEqual(decision["reason"], "quota_stale_type_normalized")
        self.assertEqual(decision["target"], {"priority": 191, "concurrency": 30})

    def test_stale_quota_holds_a_scheduler_owned_extreme_state(self) -> None:
        reset_at = self.now + timedelta(days=3)
        decision = self.evaluate(
            self.account(
                "plus",
                priority=10,
                concurrency=100,
                used=20,
                sampled_at=self.now - timedelta(minutes=6),
                reset_at=reset_at,
            ),
            state={
                "mode": "extreme",
                "seven_day_reset_at": reset_at.isoformat(),
            },
        )

        self.assertEqual(decision["status"], "held")
        self.assertEqual(decision["reason"], "quota_stale_extreme_held")
        self.assertIsNone(decision["target"])

    def test_disabling_quota_strategy_does_not_roll_back_owned_extreme_state(self) -> None:
        reset_at = self.now + timedelta(days=3)
        decision = self.evaluate(
            self.account(
                "plus",
                priority=10,
                concurrency=100,
                used=20,
                reset_at=reset_at,
            ),
            type_enabled=True,
            quota_enabled=False,
            state={
                "mode": "extreme",
                "seven_day_reset_at": reset_at.isoformat(),
            },
        )

        self.assertEqual(decision["status"], "held")
        self.assertEqual(decision["reason"], "quota_strategy_disabled_extreme_held")
        self.assertIsNone(decision["target"])

    def test_extreme_state_recovers_below_eighty_percent(self) -> None:
        reset_at = self.now + timedelta(days=3)
        decision = self.evaluate(
            self.account(
                "plus",
                priority=10,
                concurrency=100,
                used=79.9,
                reset_at=reset_at,
            ),
            state={
                "mode": "extreme",
                "seven_day_reset_at": reset_at.isoformat(),
            },
        )

        self.assertEqual(decision["strategy"], "quota_recovery")
        self.assertEqual(decision["mode"], "normal")
        self.assertEqual(decision["target"], {"priority": 191, "concurrency": 30})

    def test_extreme_state_does_not_recover_at_exactly_eighty_percent(self) -> None:
        reset_at = self.now + timedelta(days=3)
        decision = self.evaluate(
            self.account(
                "plus",
                priority=10,
                concurrency=100,
                used=80,
                reset_at=reset_at,
            ),
            state={
                "mode": "extreme",
                "seven_day_reset_at": reset_at.isoformat(),
            },
        )

        self.assertEqual(decision["mode"], "extreme")
        self.assertEqual(decision["target"], {"priority": 10, "concurrency": 100})

    def test_extreme_state_recovers_when_reset_identity_changes(self) -> None:
        old_reset = self.now + timedelta(hours=1)
        new_reset = self.now + timedelta(days=7)
        decision = self.evaluate(
            self.account(
                "pro",
                priority=10,
                concurrency=100,
                used=10,
                reset_at=new_reset,
            ),
            state={
                "mode": "extreme",
                "seven_day_reset_at": old_reset.isoformat(),
            },
        )

        self.assertEqual(decision["strategy"], "quota_recovery")
        self.assertEqual(decision["target"], {"priority": 991, "concurrency": 30})
        self.assertEqual(decision["seven_day_reset_at"], new_reset.isoformat())

    def test_extreme_429_starts_pending_delay_without_runtime_change(self) -> None:
        reset_at = self.now + timedelta(days=3)
        decision = self.evaluate(
            self.account(
                "plus",
                priority=10,
                concurrency=100,
                used=95,
                reset_at=reset_at,
                error_message="API returned 429: rate limited",
            ),
            state={"mode": "extreme", "seven_day_reset_at": reset_at.isoformat()},
        )

        self.assertEqual(decision["status"], "unchanged")
        self.assertEqual(decision["mode"], "rate_limit_pending")
        self.assertEqual(decision["rate_limit_detected_at"], self.now.isoformat())
        self.assertEqual(decision["target"], {"priority": 10, "concurrency": 100})

    def test_pending_429_uses_first_detection_time(self) -> None:
        detected_at = self.now - timedelta(minutes=10)
        decision = self.evaluate(
            self.account(
                "plus",
                priority=10,
                concurrency=100,
                used=95,
                error_message="429",
            ),
            state={
                "mode": "rate_limit_pending",
                "rate_limit_detected_at": detected_at.isoformat(),
            },
        )

        self.assertEqual(decision["rate_limit_detected_at"], detected_at.isoformat())

    def test_pending_waits_until_exact_thirty_minute_boundary(self) -> None:
        cases = (
            (timedelta(minutes=29, seconds=59), "rate_limit_pending"),
            (timedelta(minutes=30), "rate_limited_cooldown"),
        )
        for elapsed, expected_mode in cases:
            with self.subTest(elapsed=elapsed):
                decision = self.evaluate(
                    self.account("plus", priority=10, concurrency=100, used=95),
                    state={
                        "mode": "rate_limit_pending",
                        "rate_limit_detected_at": (self.now - elapsed).isoformat(),
                    },
                )
                self.assertEqual(decision["mode"], expected_mode)
                expected_target = (
                    {"priority": 10, "concurrency": 100}
                    if expected_mode == "rate_limit_pending"
                    else {"priority": 191, "concurrency": 30}
                )
                self.assertEqual(decision["target"], expected_target)

    def test_cooldown_blocks_extreme_until_quota_recovers(self) -> None:
        decision = self.evaluate(
            self.account("plus", priority=191, concurrency=30, used=95),
            state={
                "mode": "rate_limited_cooldown",
                "rate_limit_detected_at": self.now.isoformat(),
            },
        )

        self.assertEqual(decision["mode"], "rate_limited_cooldown")
        self.assertEqual(decision["target"], {"priority": 191, "concurrency": 30})

    def test_cooldown_releases_below_recovery_threshold(self) -> None:
        decision = self.evaluate(
            self.account("plus", priority=191, concurrency=30, used=79.9),
            state={
                "mode": "rate_limited_cooldown",
                "rate_limit_detected_at": self.now.isoformat(),
            },
        )

        self.assertEqual(decision["mode"], "normal")
        self.assertIsNone(decision["rate_limit_detected_at"])

    def test_pending_reset_recovers_before_delay_elapses(self) -> None:
        old_reset = self.now + timedelta(hours=1)
        new_reset = self.now + timedelta(days=7)
        decision = self.evaluate(
            self.account(
                "plus",
                priority=10,
                concurrency=100,
                used=10,
                reset_at=new_reset,
            ),
            state={
                "mode": "rate_limit_pending",
                "rate_limit_detected_at": (
                    self.now - timedelta(minutes=5)
                ).isoformat(),
                "seven_day_reset_at": old_reset.isoformat(),
            },
        )

        self.assertEqual(decision["mode"], "normal")
        self.assertEqual(decision["reason"], "seven_day_window_reset")

    def test_cooldown_reset_releases_to_normal(self) -> None:
        old_reset = self.now + timedelta(hours=1)
        new_reset = self.now + timedelta(days=7)
        decision = self.evaluate(
            self.account(
                "plus",
                priority=191,
                concurrency=30,
                used=95,
                reset_at=new_reset,
            ),
            state={
                "mode": "rate_limited_cooldown",
                "rate_limit_detected_at": self.now.isoformat(),
                "seven_day_reset_at": old_reset.isoformat(),
            },
        )

        self.assertEqual(decision["mode"], "normal")
        self.assertEqual(decision["reason"], "seven_day_window_reset")

    def test_stale_quota_holds_cooldown_at_normal_values(self) -> None:
        decision = self.evaluate(
            self.account(
                "plus",
                priority=191,
                concurrency=30,
                used=95,
                sampled_at=self.now - timedelta(minutes=6),
            ),
            state={
                "mode": "rate_limited_cooldown",
                "rate_limit_detected_at": self.now.isoformat(),
            },
        )

        self.assertEqual(decision["mode"], "rate_limited_cooldown")
        self.assertEqual(decision["target"], {"priority": 191, "concurrency": 30})

    def test_missing_quota_holds_cooldown_at_normal_values(self) -> None:
        decision = self.evaluate(
            self.account(
                "plus",
                priority=191,
                concurrency=30,
                used=None,
            ),
            state={
                "mode": "rate_limited_cooldown",
                "rate_limit_detected_at": self.now.isoformat(),
            },
        )

        self.assertEqual(decision["mode"], "rate_limited_cooldown")
        self.assertEqual(decision["target"], {"priority": 191, "concurrency": 30})

    def test_rate_limited_status_starts_pending(self) -> None:
        decision = self.evaluate(
            self.account(
                "plus",
                priority=10,
                concurrency=100,
                used=95,
                status="rate_limited",
            ),
            state={"mode": "extreme"},
        )

        self.assertEqual(decision["mode"], "rate_limit_pending")

    def test_normal_account_ignores_429_recovery_signal(self) -> None:
        decision = self.evaluate(
            self.account(
                "plus",
                priority=250,
                used=20,
                error_message="API returned 429",
            ),
            state={"mode": "normal"},
        )

        self.assertEqual(decision["mode"], "normal")
        self.assertNotEqual(decision["strategy"], "rate_limit_recovery")

    def test_4290_does_not_start_rate_limit_pending(self) -> None:
        decision = self.evaluate(
            self.account(
                "plus",
                priority=10,
                concurrency=100,
                used=95,
                error_message="wait 4290 milliseconds",
            ),
            state={"mode": "extreme"},
        )

        self.assertEqual(decision["mode"], "extreme")

    def test_missing_quota_cannot_trigger_extreme(self) -> None:
        decision = self.evaluate(
            self.account("plus", priority=250, used=None),
            type_enabled=False,
            quota_enabled=True,
        )

        self.assertEqual(decision["status"], "skipped")
        self.assertEqual(decision["reason"], "quota_missing")

    def test_unsupported_type_is_skipped(self) -> None:
        decision = self.evaluate(self.account("free", priority=1))

        self.assertEqual(decision["status"], "skipped")
        self.assertEqual(decision["reason"], "unsupported_account_type")


if __name__ == "__main__":
    unittest.main()
