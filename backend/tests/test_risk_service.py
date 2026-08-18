from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock
from unittest.mock import patch


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


class RiskServiceTests(unittest.IsolatedAsyncioTestCase):
    def test_event_date_bounds_use_shanghai_calendar_days_as_utc_half_open_range(self) -> None:
        from app.modules.risk.service import event_date_bounds

        start_at, end_at = event_date_bounds(
            start_date=date(2026, 8, 1),
            end_date=date(2026, 8, 18),
        )

        self.assertEqual(start_at, datetime(2026, 7, 31, 16, 0, tzinfo=UTC))
        self.assertEqual(end_at, datetime(2026, 8, 18, 16, 0, tzinfo=UTC))

    def test_event_date_bounds_reject_reversed_range(self) -> None:
        from app.modules.risk.service import event_date_bounds

        with self.assertRaisesRegex(ValueError, "end_date must not be before start_date"):
            event_date_bounds(
                start_date=date(2026, 8, 18),
                end_date=date(2026, 8, 1),
            )

    def test_source_window_is_always_limited_to_the_last_seven_days(self) -> None:
        from app.modules.risk.service import source_window_start

        self.assertEqual(source_window_start(now=NOW), NOW - timedelta(days=7))

    async def test_stream_reader_uses_bounded_pagination_and_cursor_progress(self) -> None:
        from app.modules.risk.adapters.sub2api import SourcePage
        from app.modules.risk.domain import IpObservation
        from app.modules.risk.service import collect_stream_pages

        first = SourcePage(
            observations=(
                IpObservation("1", "a.b@example.com", "10.0.0.1", "usage_log", NOW, 11),
                IpObservation("2", "person@example.com", "10.0.0.1", "usage_log", NOW, 12),
            ),
            rows_read=2,
            last_source_id=12,
            latest_created_at=NOW,
        )
        second = SourcePage(
            observations=(
                IpObservation("3", "other@example.com", "10.0.0.1", "usage_log", NOW, 13),
            ),
            rows_read=1,
            last_source_id=13,
            latest_created_at=NOW,
        )
        adapter = AsyncMock()
        adapter.read_usage_observations.side_effect = [first, second]

        page = await collect_stream_pages(
            adapter,
            object(),
            stream="usage_logs",
            after_id=10,
            since=NOW - timedelta(days=7),
            page_size=2,
            max_pages=3,
        )

        self.assertEqual(page.rows_read, 3)
        self.assertEqual(page.last_source_id, 13)
        self.assertEqual(len(page.observations), 3)
        self.assertEqual(adapter.read_usage_observations.await_count, 2)
        self.assertEqual(
            [call.kwargs["after_id"] for call in adapter.read_usage_observations.await_args_list],
            [10, 12],
        )
        self.assertTrue(all(call.kwargs["limit"] == 2 for call in adapter.read_usage_observations.await_args_list))

    async def test_stream_reader_caps_backlog_work_per_cycle(self) -> None:
        from app.modules.risk.adapters.sub2api import SourcePage
        from app.modules.risk.service import collect_stream_pages

        page = SourcePage((), 100, 100, NOW)
        adapter = AsyncMock()
        adapter.read_audit_observations.side_effect = [page, page]

        result = await collect_stream_pages(
            adapter,
            object(),
            stream="audit_logs",
            after_id=0,
            since=NOW - timedelta(days=7),
            page_size=100,
            max_pages=2,
        )

        self.assertEqual(adapter.read_audit_observations.await_count, 2)
        self.assertEqual(result.rows_read, 200)

    def test_suspicious_email_and_shared_ip_plans_auto_ban(self) -> None:
        from app.modules.risk.domain import RiskDecision
        from app.modules.risk.service import evaluate_account_input

        evaluation = evaluate_account_input({
            "external_user_id": "42",
            "email": "a.b+tag@example.com",
            "manual_override_active": False,
            "shared_ip_evidence": [{
                "ip_address": "14.31.212.25",
                "distinct_account_count": 3,
                "external_user_ids": ["42", "43", "44"],
                "sources": ["user_audit", "usage_log"],
                "first_seen_at": NOW,
                "last_seen_at": NOW,
            }],
        })

        self.assertEqual(evaluation.decision, RiskDecision.BAN)
        self.assertEqual(
            evaluation.email_rules,
            ("email_local_part_dot", "email_plus_tag"),
        )

    def test_normal_email_on_shared_ip_is_review_only(self) -> None:
        from app.modules.risk.domain import RiskDecision
        from app.modules.risk.service import evaluate_account_input

        evaluation = evaluate_account_input({
            "external_user_id": "normal-user",
            "email": "normal@example.com",
            "manual_override_active": False,
            "shared_ip_evidence": [{
                "ip_address": "14.31.212.25",
                "distinct_account_count": 20,
                "external_user_ids": ["normal-user", "42", "43"],
                "sources": ["registration_audit"],
                "first_seen_at": NOW,
                "last_seen_at": NOW,
            }],
        })

        self.assertEqual(evaluation.decision, RiskDecision.HIGH_RISK)
        self.assertEqual(evaluation.email_rules, ())

    def test_risk_ban_candidate_always_requires_manual_review(self) -> None:
        from app.modules.risk.service import desired_risk_status, evaluate_account_input

        evaluation = evaluate_account_input({
            "external_user_id": "42",
            "email": "a.b@example.com",
            "manual_override_active": False,
            "shared_ip_evidence": [{
                "ip_address": "14.31.212.25",
                "distinct_account_count": 3,
                "external_user_ids": ["42", "43", "44"],
                "sources": ["user_audit"],
                "first_seen_at": NOW,
                "last_seen_at": NOW,
            }],
        })

        self.assertEqual(desired_risk_status(evaluation, auto_ban_enabled=False), "high_risk")
        self.assertEqual(desired_risk_status(evaluation, auto_ban_enabled=True), "high_risk")

    def test_paid_account_is_never_planned_for_auto_ban(self) -> None:
        from app.modules.risk.domain import RiskDecision
        from app.modules.risk.service import desired_risk_status, evaluate_account_input

        evaluation = evaluate_account_input({
            "external_user_id": "paid-user",
            "email": "a.b@example.com",
            "has_paid_history": True,
            "manual_override_active": False,
            "shared_ip_evidence": [{
                "ip_address": "14.31.212.25",
                "distinct_account_count": 3,
                "external_user_ids": ["paid-user", "43", "44"],
                "sources": ["user_audit"],
                "first_seen_at": NOW,
                "last_seen_at": NOW,
            }],
        })

        self.assertTrue(evaluation.has_paid_history)
        self.assertEqual(evaluation.decision, RiskDecision.HIGH_RISK)
        self.assertEqual(desired_risk_status(evaluation, auto_ban_enabled=True), "high_risk")

    def test_action_key_is_stable_for_same_evidence_and_changes_with_new_evidence(self) -> None:
        from app.modules.risk.service import action_idempotency_key, evaluate_account_input

        row = {
            "external_user_id": "42",
            "email": "a.b@example.com",
            "manual_override_active": False,
            "shared_ip_evidence": [{
                "ip_address": "14.31.212.25",
                "distinct_account_count": 3,
                "external_user_ids": ["42", "43", "44"],
                "sources": ["usage_log"],
                "first_seen_at": NOW,
                "last_seen_at": NOW,
            }],
        }
        evaluation = evaluate_account_input(row)

        first = action_idempotency_key("aiwelink", evaluation)
        second = action_idempotency_key("aiwelink", evaluation)
        changed = evaluate_account_input({
            **row,
            "shared_ip_evidence": [{
                **row["shared_ip_evidence"][0],
                "last_seen_at": NOW + timedelta(minutes=1),
            }],
        })

        self.assertEqual(first, second)
        self.assertNotEqual(first, action_idempotency_key("aiwelink", changed))

    def test_health_payload_discloses_stale_usage_coverage(self) -> None:
        from app.modules.risk.service import source_health_payload

        payload = source_health_payload(
            {
                "source_stream": "usage_logs",
                "latest_observed_at": NOW - timedelta(days=17),
                "last_success_at": NOW,
                "last_rows_read": 0,
                "last_error_code": "",
                "last_error_message": "",
            },
            now=NOW,
        )

        self.assertEqual(payload["status"], "stale")
        self.assertEqual(payload["source_stream"], "usage_logs")
        self.assertEqual(payload["last_success_at"], NOW)

    async def test_reconciliation_never_prepares_paid_account_for_ban(self) -> None:
        from app.modules.risk import service

        row = {
            "external_user_id": "paid-user",
            "email": "a.b@example.com",
            "risk_status": None,
            "manual_override_active": False,
            "has_verified_payment": False,
            "shared_ip_evidence": [{
                "ip_address": "14.31.212.25",
                "distinct_account_count": 3,
                "external_user_ids": ["paid-user", "43", "44"],
                "sources": ["usage_log"],
                "first_seen_at": NOW,
                "last_seen_at": NOW,
            }],
        }
        account = {
            "risk_account_id": "00000000-0000-0000-0000-000000000042",
            "risk_status": "high_risk",
        }
        with (
            patch.object(service.repository, "upsert_risk_account", AsyncMock(return_value=account)) as upsert,
            patch.object(service.repository, "append_event", AsyncMock()) as append,
        ):
            candidates = await service.reconcile_risk_inputs(
                object(),
                rows=[row],
                auto_ban_enabled=True,
                detected_at=NOW,
                source_payment_checker=AsyncMock(return_value=True),
            )

        self.assertEqual(candidates, [])
        self.assertEqual(upsert.await_args.kwargs["risk_status"], "high_risk")
        self.assertEqual(
            upsert.await_args.kwargs["risk_reasons"]["protection_reasons"],
            ["verified_payment_history"],
        )
        append.assert_awaited_once()

    async def test_reconciliation_records_unpaid_dual_signal_account_for_manual_review(self) -> None:
        from app.modules.risk import service

        row = {
            "external_user_id": "42",
            "email": "a.b@example.com",
            "risk_status": None,
            "manual_override_active": False,
            "has_verified_payment": False,
            "shared_ip_evidence": [{
                "ip_address": "14.31.212.25",
                "distinct_account_count": 3,
                "external_user_ids": ["42", "43", "44"],
                "sources": ["user_audit"],
                "first_seen_at": NOW,
                "last_seen_at": NOW,
            }],
        }
        account = {
            "risk_account_id": "00000000-0000-0000-0000-000000000042",
            "risk_status": "high_risk",
        }
        with (
            patch.object(service.repository, "upsert_risk_account", AsyncMock(return_value=account)) as upsert,
            patch.object(service.repository, "append_event", AsyncMock()),
        ):
            candidates = await service.reconcile_risk_inputs(
                object(),
                rows=[row],
                auto_ban_enabled=True,
                detected_at=NOW,
                source_payment_checker=AsyncMock(return_value=False),
            )

        self.assertEqual(candidates, [])
        self.assertEqual(upsert.await_args.kwargs["risk_status"], "high_risk")

    async def test_reconciliation_keeps_confirmed_ban_as_a_terminal_state(self) -> None:
        from app.modules.risk import service

        row = {
            "external_user_id": "42",
            "email": "a.b@example.com",
            "risk_status": "banned",
            "manual_override_active": False,
            "has_verified_payment": False,
            "shared_ip_evidence": [{
                "ip_address": "14.31.212.25",
                "distinct_account_count": 3,
                "external_user_ids": ["42", "43", "44"],
                "sources": ["user_audit"],
                "first_seen_at": NOW,
                "last_seen_at": NOW,
            }],
        }
        payment_checker = AsyncMock(return_value=False)
        with (
            patch.object(
                service.repository,
                "upsert_risk_account",
                AsyncMock(return_value={
                    "risk_account_id": "00000000-0000-0000-0000-000000000042",
                    "risk_status": "ban_pending",
                }),
            ) as upsert,
            patch.object(service.repository, "append_event", AsyncMock()) as append,
        ):
            candidates = await service.reconcile_risk_inputs(
                object(),
                rows=[row],
                auto_ban_enabled=True,
                detected_at=NOW,
                source_payment_checker=payment_checker,
            )

        self.assertEqual(candidates, [])
        payment_checker.assert_not_awaited()
        upsert.assert_not_awaited()
        append.assert_not_awaited()

    async def test_reconciliation_keeps_released_override_as_a_terminal_state(self) -> None:
        from app.modules.risk import service

        row = {
            "external_user_id": "42",
            "email": "a.b@example.com",
            "risk_status": "released",
            "manual_override_active": True,
            "has_verified_payment": False,
            "shared_ip_evidence": [],
        }
        with (
            patch.object(
                service.repository,
                "upsert_risk_account",
                AsyncMock(return_value={
                    "risk_account_id": "00000000-0000-0000-0000-000000000042",
                }),
            ) as upsert,
            patch.object(service.repository, "append_event", AsyncMock()) as append,
        ):
            candidates = await service.reconcile_risk_inputs(
                object(),
                rows=[row],
                auto_ban_enabled=True,
                detected_at=NOW,
                source_payment_checker=AsyncMock(return_value=False),
            )

        self.assertEqual(candidates, [])
        upsert.assert_not_awaited()
        append.assert_not_awaited()

    async def test_unchanged_high_risk_state_does_not_append_duplicate_event(self) -> None:
        from app.modules.risk import service

        row = {
            "external_user_id": "normal",
            "email": "normal@example.com",
            "risk_status": "high_risk",
            "manual_override_active": False,
            "has_verified_payment": False,
            "shared_ip_evidence": [{
                "ip_address": "14.31.212.25",
                "distinct_account_count": 3,
                "external_user_ids": ["normal", "43", "44"],
                "sources": ["user_audit"],
                "first_seen_at": NOW,
                "last_seen_at": NOW,
            }],
        }
        with (
            patch.object(
                service.repository,
                "upsert_risk_account",
                AsyncMock(return_value={"risk_account_id": "00000000-0000-0000-0000-000000000099"}),
            ),
            patch.object(service.repository, "append_event", AsyncMock()) as append,
        ):
            await service.reconcile_risk_inputs(
                object(),
                rows=[row],
                auto_ban_enabled=True,
                detected_at=NOW,
                source_payment_checker=AsyncMock(return_value=False),
            )

        append.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
