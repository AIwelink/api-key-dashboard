from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


class RiskCoordinatorTests(unittest.IsolatedAsyncioTestCase):
    async def test_paused_detector_never_opens_aiwelink_source_database(self) -> None:
        from app.modules.risk import coordinator

        growth = _TransactionConnection()
        source_factory = Mock(side_effect=AssertionError("source must remain closed"))
        with (
            patch.object(coordinator.repository, "acquire_cycle_lock", AsyncMock(return_value=True)),
            patch.object(
                coordinator.repository,
                "get_settings",
                AsyncMock(return_value={"detector_enabled": False, "auto_ban_enabled": False}),
            ),
            patch.object(
                coordinator.repository,
                "list_pending_manual_actions",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                coordinator,
                "_refresh_dirty_operations_aggregates",
                AsyncMock(return_value=False),
            ),
            patch.object(coordinator.repository, "release_cycle_lock", AsyncMock()) as release,
        ):
            result = await coordinator.run_risk_cycle(
                object(),
                now=NOW,
                growth_session_factory=lambda db: _async_context(growth),
                source_engine_factory=source_factory,
            )

        self.assertEqual(result["status"], "paused")
        source_factory.assert_not_called()
        release.assert_awaited_once()

    async def test_paused_detector_recovers_pending_manual_actions(self) -> None:
        from app.modules.risk import coordinator

        growth = _TransactionConnection()
        source = _SourceEngine()
        recovery_result = {"succeeded": 1, "conflicted": 0, "failed": 0}
        with (
            patch.object(coordinator.repository, "acquire_cycle_lock", AsyncMock(return_value=True)),
            patch.object(
                coordinator.repository,
                "get_settings",
                AsyncMock(return_value={"detector_enabled": False, "auto_ban_enabled": False}),
            ),
            patch.object(
                coordinator.repository,
                "list_pending_manual_actions",
                AsyncMock(return_value=[_pending_action()]),
            ),
            patch.object(
                coordinator,
                "recover_pending_manual_actions",
                AsyncMock(return_value=recovery_result),
            ) as recover,
            patch.object(
                coordinator,
                "_refresh_dirty_operations_aggregates",
                AsyncMock(return_value=True),
            ) as refresh,
            patch.object(coordinator.repository, "release_cycle_lock", AsyncMock()),
        ):
            result = await coordinator.run_risk_cycle(
                object(),
                now=NOW,
                site_loader=AsyncMock(return_value={
                    "id": "aiwelink",
                    "client_type": "sub2api",
                    "sql_dsn": "postgresql://reader:secret@db/aiwelink",
                }),
                growth_session_factory=lambda db: _async_context(growth),
                source_engine_factory=Mock(return_value=source),
                adapter_factory=Mock(return_value=AsyncMock()),
            )

        self.assertEqual(result["status"], "paused")
        self.assertEqual(result["manual_recovery"], recovery_result)
        recover.assert_awaited_once()
        refresh.assert_awaited_once()
        source.dispose.assert_awaited_once()

    async def test_source_failure_does_not_advance_other_stream_cursor(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import SourcePage

        growth = _TransactionConnection()
        source = _SourceEngine()
        source.read_connection = Mock()
        source.read_connection.rollback = AsyncMock()
        adapter = AsyncMock()
        adapter.read_audit_observations.return_value = SourcePage((), 0, 10, None)
        adapter.read_usage_observations.side_effect = TimeoutError("usage timed out")
        cursors = {
            "audit_logs": {"source_stream": "audit_logs", "last_source_id": 10},
            "usage_logs": {"source_stream": "usage_logs", "last_source_id": 20},
        }
        with (
            patch.object(coordinator.repository, "acquire_cycle_lock", AsyncMock(return_value=True)),
            patch.object(coordinator.repository, "release_cycle_lock", AsyncMock()),
            patch.object(
                coordinator.repository,
                "get_settings",
                AsyncMock(return_value={
                    "detector_enabled": True,
                    "auto_ban_enabled": False,
                    "ip_window_days": 7,
                    "shared_ip_min_accounts": 3,
                }),
            ),
            patch.object(
                coordinator.repository,
                "get_cursor",
                AsyncMock(side_effect=lambda connection, *, site_id, source_stream: cursors[source_stream]),
            ),
            patch.object(coordinator.repository, "upsert_observations", AsyncMock(return_value=0)),
            patch.object(coordinator.repository, "save_cursor_success", AsyncMock()) as success,
            patch.object(coordinator.repository, "save_cursor_error", AsyncMock()) as failed,
            patch.object(coordinator.repository, "cleanup_observations", AsyncMock(return_value=0)),
            patch.object(coordinator.repository, "list_account_risk_inputs", AsyncMock(return_value=[])),
            patch.object(
                coordinator.repository,
                "list_pending_auto_ban_actions",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                coordinator,
                "recover_pending_manual_actions",
                AsyncMock(return_value={"succeeded": 0, "conflicted": 0, "failed": 0}),
            ) as manual_recovery,
            patch.object(
                coordinator,
                "_refresh_dirty_operations_aggregates",
                AsyncMock(return_value=False),
            ),
        ):
            result = await coordinator.run_risk_cycle(
                object(),
                now=NOW,
                site_loader=AsyncMock(return_value={
                    "id": "aiwelink",
                    "client_type": "sub2api",
                    "sql_dsn": "postgresql://reader:secret@db/aiwelink",
                }),
                growth_session_factory=lambda db: _async_context(growth),
                source_engine_factory=Mock(return_value=source),
                adapter_factory=Mock(return_value=adapter),
            )

        self.assertEqual(result["sources"]["audit_logs"]["status"], "succeeded")
        self.assertEqual(result["sources"]["usage_logs"]["status"], "failed")
        success.assert_awaited_once()
        self.assertEqual(success.await_args.kwargs["source_stream"], "audit_logs")
        failed.assert_awaited_once()
        self.assertEqual(failed.await_args.kwargs["source_stream"], "usage_logs")
        manual_recovery.assert_awaited_once()
        source.read_connection.rollback.assert_awaited_once()

    async def test_dirty_operations_aggregates_are_retried_without_new_actions(self) -> None:
        from app.modules.risk import coordinator

        growth = _TransactionConnection()
        with (
            patch.object(
                coordinator.repository,
                "risk_aggregates_are_dirty",
                AsyncMock(return_value=True),
            ),
            patch.object(
                coordinator.operations_repository,
                "replace_affected_aggregates",
                AsyncMock(),
            ) as replace,
            patch.object(
                coordinator.repository,
                "clear_risk_aggregates_dirty",
                AsyncMock(),
            ) as clear,
        ):
            refreshed = await coordinator._refresh_dirty_operations_aggregates(
                growth,
                completed_at=NOW,
            )

        self.assertTrue(refreshed)
        replace.assert_awaited_once()
        clear.assert_awaited_once()

    async def test_failed_aggregate_refresh_keeps_dirty_marker(self) -> None:
        from app.modules.risk import coordinator

        growth = _TransactionConnection()
        with (
            patch.object(
                coordinator.repository,
                "risk_aggregates_are_dirty",
                AsyncMock(return_value=True),
            ),
            patch.object(
                coordinator.operations_repository,
                "replace_affected_aggregates",
                AsyncMock(side_effect=RuntimeError("aggregate failed")),
            ),
            patch.object(
                coordinator.repository,
                "clear_risk_aggregates_dirty",
                AsyncMock(),
            ) as clear,
        ):
            with self.assertRaisesRegex(RuntimeError, "aggregate failed"):
                await coordinator._refresh_dirty_operations_aggregates(
                    growth,
                    completed_at=NOW,
                )

        clear.assert_not_awaited()

    async def test_enabled_cycle_rebuilds_operations_once_for_all_recovered_actions(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import SourcePage

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.read_audit_observations.return_value = SourcePage((), 0, 0, None)
        adapter.read_usage_observations.return_value = SourcePage((), 0, 0, None)
        with (
            patch.object(coordinator, "recover_pending_manual_actions", AsyncMock(return_value={
                "succeeded": 2,
                "conflicted": 0,
                "failed": 0,
            })),
            patch.object(coordinator, "recover_pending_auto_bans", AsyncMock(return_value={
                "succeeded": 3,
                "conflicted": 0,
                "failed": 0,
            })),
            patch.object(coordinator.repository, "get_cursor", AsyncMock(return_value={"last_source_id": 0})),
            patch.object(coordinator.repository, "upsert_observations", AsyncMock(return_value=0)),
            patch.object(coordinator.repository, "save_cursor_success", AsyncMock()),
            patch.object(coordinator.repository, "cleanup_observations", AsyncMock(return_value=0)),
            patch.object(coordinator.repository, "list_account_risk_inputs", AsyncMock(return_value=[])),
            patch.object(coordinator, "reconcile_risk_inputs", AsyncMock(return_value=[])),
            patch.object(
                coordinator,
                "_refresh_dirty_operations_aggregates",
                AsyncMock(return_value=True),
            ) as aggregate,
        ):
            result = await coordinator._run_enabled_cycle(
                growth,
                source_engine=source,
                adapter=adapter,
                settings={
                    "auto_ban_enabled": True,
                    "ip_window_days": 7,
                    "shared_ip_min_accounts": 3,
                },
                detected_at=NOW,
            )

        self.assertEqual(result["manual_recovery"]["succeeded"], 2)
        self.assertEqual(result["recovery"]["succeeded"], 3)
        aggregate.assert_awaited_once()

    async def test_source_connection_failure_marks_both_stream_cursors_failed(self) -> None:
        from app.modules.risk import coordinator

        growth = _TransactionConnection()
        source = _FailingSourceEngine(ConnectionError("source unavailable"))
        adapter = AsyncMock()
        with (
            patch.object(coordinator, "recover_pending_manual_actions", AsyncMock(return_value={
                "succeeded": 0,
                "conflicted": 0,
                "failed": 0,
            })),
            patch.object(coordinator, "recover_pending_auto_bans", AsyncMock(return_value={
                "succeeded": 0,
                "conflicted": 0,
                "failed": 0,
            })),
            patch.object(coordinator.repository, "get_cursor", AsyncMock(return_value={"last_source_id": 0})),
            patch.object(coordinator.repository, "save_cursor_error", AsyncMock()) as save_error,
        ):
            result = await coordinator._run_enabled_cycle(
                growth,
                source_engine=source,
                adapter=adapter,
                settings={
                    "auto_ban_enabled": True,
                    "ip_window_days": 7,
                    "shared_ip_min_accounts": 3,
                },
                detected_at=NOW,
            )

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["actions_succeeded"], 0)
        self.assertEqual(save_error.await_count, 2)
        self.assertEqual(
            {call.kwargs["source_stream"] for call in save_error.await_args_list},
            {"audit_logs", "usage_logs"},
        )
        self.assertTrue(all(
            call.kwargs["error_code"] == "ConnectionError"
            for call in save_error.await_args_list
        ))

    async def test_auto_ban_commits_action_before_source_mutation_and_then_excludes_stats(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import (
            ApiKeyState,
            EnforcementResult,
            SourceAccountState,
            SourcePage,
        )
        from app.modules.risk.service import PreparedBanCandidate, evaluate_account_input

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.read_audit_observations.return_value = SourcePage((), 0, 0, None)
        adapter.read_usage_observations.return_value = SourcePage((), 0, 0, None)
        adapter.has_completed_payment.return_value = False
        before = SourceAccountState(
            "42",
            "a.b@example.com",
            "active",
            NOW,
            (ApiKeyState("key-1", "active", NOW),),
        )
        enforced = EnforcementResult(
            "disabled",
            NOW,
            (ApiKeyState("key-1", "inactive", NOW),),
        )
        adapter.capture_account_state.return_value = before
        adapter.disable_account.return_value = enforced
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
        candidate = PreparedBanCandidate(
            "00000000-0000-0000-0000-000000000042",
            evaluation,
        )
        action_id = "00000000-0000-0000-0000-000000000043"
        call_order = []

        async def create_action(*args, **kwargs):
            call_order.append("action_committed")
            return {"risk_action_id": action_id, "action_status": "pending"}

        async def disable(*args, **kwargs):
            call_order.append("source_disabled")
            return enforced

        adapter.disable_account.side_effect = disable
        with (
            patch.object(coordinator.repository, "acquire_cycle_lock", AsyncMock(return_value=True)),
            patch.object(coordinator.repository, "release_cycle_lock", AsyncMock()),
            patch.object(coordinator.repository, "get_settings", AsyncMock(return_value={
                "detector_enabled": True,
                "auto_ban_enabled": True,
                "ip_window_days": 7,
                "shared_ip_min_accounts": 3,
            })),
            patch.object(coordinator.repository, "get_cursor", AsyncMock(return_value={"last_source_id": 0})),
            patch.object(coordinator.repository, "upsert_observations", AsyncMock(return_value=0)),
            patch.object(coordinator.repository, "save_cursor_success", AsyncMock()),
            patch.object(coordinator.repository, "cleanup_observations", AsyncMock(return_value=0)),
            patch.object(coordinator.repository, "list_account_risk_inputs", AsyncMock(return_value=[{}])),
            patch.object(
                coordinator.repository,
                "list_pending_auto_ban_actions",
                AsyncMock(return_value=[]),
            ),
            patch.object(
                coordinator,
                "recover_pending_manual_actions",
                AsyncMock(return_value={"succeeded": 0, "conflicted": 0, "failed": 0}),
            ),
            patch.object(coordinator, "reconcile_risk_inputs", AsyncMock(return_value=[candidate])),
            patch.object(coordinator.repository, "create_action", create_action),
            patch.object(coordinator.repository, "complete_action", AsyncMock()) as complete,
            patch.object(coordinator.repository, "upsert_risk_account", AsyncMock(return_value={
                "risk_account_id": candidate.risk_account_id,
            })) as account_upsert,
            patch.object(coordinator.repository, "set_stats_exclusion", AsyncMock()) as exclude,
            patch.object(coordinator.repository, "append_event", AsyncMock()),
            patch.object(
                coordinator,
                "_refresh_dirty_operations_aggregates",
                AsyncMock(return_value=True),
            ) as aggregate,
        ):
            result = await coordinator.run_risk_cycle(
                object(),
                now=NOW,
                site_loader=AsyncMock(return_value={
                    "id": "aiwelink",
                    "client_type": "sub2api",
                    "sql_dsn": "postgresql://reader:secret@db/aiwelink",
                }),
                growth_session_factory=lambda db: _async_context(growth),
                source_engine_factory=Mock(return_value=source),
                adapter_factory=Mock(return_value=adapter),
            )

        self.assertEqual(call_order, ["action_committed", "source_disabled"])
        self.assertEqual(result["actions_succeeded"], 1)
        self.assertEqual(complete.await_args.kwargs["status"], "succeeded")
        self.assertEqual(account_upsert.await_args.kwargs["risk_status"], "banned")
        exclude.assert_awaited_once()
        aggregate.assert_awaited_once()
        source.dispose.assert_awaited_once()

    async def test_growth_finalize_failure_keeps_source_committed_action_pending(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import (
            EnforcementResult,
            SourceAccountState,
            SourcePage,
        )
        from app.modules.risk.service import PreparedBanCandidate, evaluate_account_input

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.read_audit_observations.return_value = SourcePage((), 0, 0, None)
        adapter.read_usage_observations.return_value = SourcePage((), 0, 0, None)
        adapter.has_completed_payment.return_value = False
        before = SourceAccountState("42", "a.b@example.com", "active", NOW, ())
        enforced = EnforcementResult("disabled", NOW, ())
        adapter.capture_account_state.return_value = before
        adapter.disable_account.return_value = enforced
        candidate = PreparedBanCandidate(
            "00000000-0000-0000-0000-000000000042",
            evaluate_account_input({
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
            }),
        )
        with (
            patch.object(coordinator, "recover_pending_manual_actions", AsyncMock(return_value={
                "succeeded": 0,
                "conflicted": 0,
                "failed": 0,
            })),
            patch.object(coordinator, "recover_pending_auto_bans", AsyncMock(return_value={
                "succeeded": 0,
                "conflicted": 0,
                "failed": 0,
            })),
            patch.object(coordinator.repository, "get_cursor", AsyncMock(return_value={"last_source_id": 0})),
            patch.object(coordinator.repository, "upsert_observations", AsyncMock(return_value=0)),
            patch.object(coordinator.repository, "save_cursor_success", AsyncMock()),
            patch.object(coordinator.repository, "cleanup_observations", AsyncMock(return_value=0)),
            patch.object(coordinator.repository, "list_account_risk_inputs", AsyncMock(return_value=[])),
            patch.object(coordinator, "reconcile_risk_inputs", AsyncMock(return_value=[candidate])),
            patch.object(coordinator.repository, "create_action", AsyncMock(return_value={
                "risk_action_id": "00000000-0000-0000-0000-000000000043",
                "action_status": "pending",
            })),
            patch.object(coordinator, "_finalize_success", AsyncMock(side_effect=RuntimeError("growth write failed"))),
            patch.object(coordinator, "_finalize_failure", AsyncMock()) as finalize_failure,
        ):
            with self.assertRaisesRegex(RuntimeError, "growth write failed"):
                await coordinator._run_enabled_cycle(
                    growth,
                    source_engine=source,
                    adapter=adapter,
                    settings={
                        "auto_ban_enabled": True,
                        "ip_window_days": 7,
                        "shared_ip_min_accounts": 3,
                    },
                    detected_at=NOW,
                )

        adapter.disable_account.assert_awaited_once()
        finalize_failure.assert_not_awaited()

    async def test_pending_auto_ban_retries_when_source_is_unchanged(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import (
            ApiKeyState,
            EnforcementResult,
            SourceAccountState,
        )

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        before = SourceAccountState(
            "42",
            "a.b@example.com",
            "active",
            NOW,
            (ApiKeyState("key-1", "active", NOW),),
        )
        enforced = EnforcementResult(
            "disabled",
            NOW,
            (ApiKeyState("key-1", "inactive", NOW),),
        )
        adapter.capture_account_state.return_value = before
        adapter.has_completed_payment.return_value = False
        adapter.disable_account.return_value = enforced
        action = _pending_action()

        with (
            patch.object(
                coordinator.repository,
                "list_pending_auto_ban_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator.repository, "complete_action", AsyncMock()) as complete,
            patch.object(coordinator.repository, "upsert_risk_account", AsyncMock(return_value={})),
            patch.object(coordinator.repository, "set_stats_exclusion", AsyncMock()),
            patch.object(coordinator.repository, "append_event", AsyncMock()),
            patch.object(coordinator.operations_repository, "replace_affected_aggregates", AsyncMock()),
        ):
            result = await coordinator.recover_pending_auto_bans(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW + timedelta(minutes=2),
            )

        self.assertEqual(result, {"succeeded": 1, "conflicted": 0, "failed": 0})
        adapter.disable_account.assert_awaited_once()
        self.assertEqual(adapter.disable_account.await_args.kwargs["changed_at"], NOW)
        self.assertEqual(complete.await_args.kwargs["status"], "succeeded")

    async def test_pending_auto_ban_does_not_run_while_auto_ban_is_paused(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.capture_account_state.return_value = coordinator._source_state_from_action(
            _pending_action()
        )
        with patch.object(
            coordinator.repository,
            "list_pending_auto_ban_actions",
            AsyncMock(return_value=[_pending_action()]),
        ) as pending:
            result = await coordinator.recover_pending_auto_bans(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW,
                auto_ban_enabled=False,
            )

        self.assertEqual(result, {"succeeded": 0, "conflicted": 0, "failed": 0})
        pending.assert_awaited_once()
        adapter.capture_account_state.assert_awaited_once()
        adapter.disable_account.assert_not_awaited()

    async def test_paused_auto_ban_finalizes_an_already_committed_source_mutation(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.capture_account_state.return_value = SourceAccountState(
            "42",
            "a.b@example.com",
            "disabled",
            NOW,
            (ApiKeyState("key-1", "inactive", NOW),),
        )
        with (
            patch.object(
                coordinator.repository,
                "list_pending_auto_ban_actions",
                AsyncMock(return_value=[_pending_action()]),
            ),
            patch.object(coordinator, "_finalize_success", AsyncMock()) as finalize,
        ):
            result = await coordinator.recover_pending_auto_bans(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW + timedelta(minutes=2),
                auto_ban_enabled=False,
            )

        self.assertEqual(result["succeeded"], 1)
        adapter.disable_account.assert_not_awaited()
        finalize.assert_awaited_once()

    async def test_pending_auto_ban_respects_a_current_manual_override(self) -> None:
        from app.modules.risk import coordinator

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        action = {**_pending_action(), "manual_override_active": True}
        adapter.capture_account_state.return_value = coordinator._source_state_from_action(action)
        with (
            patch.object(
                coordinator.repository,
                "list_pending_auto_ban_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator, "_finalize_recovery_conflict", AsyncMock()),
            patch.object(coordinator, "_finalize_failure", AsyncMock()),
        ):
            result = await coordinator.recover_pending_auto_bans(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW,
                auto_ban_enabled=True,
            )

        self.assertEqual(result, {"succeeded": 0, "conflicted": 0, "failed": 0})
        adapter.capture_account_state.assert_awaited_once()
        adapter.disable_account.assert_not_awaited()

    async def test_pending_auto_ban_does_not_use_expired_ip_evidence(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import EnforcementResult, SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        before = coordinator._source_state_from_action(_pending_action())
        adapter.capture_account_state.return_value = before
        adapter.has_completed_payment.return_value = False
        adapter.disable_account.return_value = EnforcementResult("disabled", NOW, ())
        evidence = {
            **_pending_action()["shared_ip_evidence"][0],
            "first_seen_at": NOW - timedelta(days=9),
            "last_seen_at": NOW - timedelta(days=8),
        }
        action = {**_pending_action(), "shared_ip_evidence": [evidence]}
        with (
            patch.object(
                coordinator.repository,
                "list_pending_auto_ban_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator, "_finalize_recovery_conflict", AsyncMock()),
            patch.object(coordinator, "_finalize_success", AsyncMock()),
            patch.object(coordinator, "_finalize_failure", AsyncMock()),
        ):
            result = await coordinator.recover_pending_auto_bans(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW,
                auto_ban_enabled=True,
            )

        self.assertEqual(result, {"succeeded": 0, "conflicted": 0, "failed": 0})
        adapter.capture_account_state.assert_awaited_once()
        adapter.disable_account.assert_not_awaited()

    async def test_pending_auto_ban_finalizes_when_source_mutation_already_committed(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        applied = SourceAccountState(
            "42",
            "a.b@example.com",
            "disabled",
            NOW,
            (ApiKeyState("key-1", "inactive", NOW),),
        )
        adapter.capture_account_state.return_value = applied
        action = _pending_action()

        with (
            patch.object(
                coordinator.repository,
                "list_pending_auto_ban_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator.repository, "complete_action", AsyncMock()) as complete,
            patch.object(coordinator.repository, "upsert_risk_account", AsyncMock(return_value={})),
            patch.object(coordinator.repository, "set_stats_exclusion", AsyncMock()),
            patch.object(coordinator.repository, "append_event", AsyncMock()),
            patch.object(coordinator.operations_repository, "replace_affected_aggregates", AsyncMock()),
        ):
            result = await coordinator.recover_pending_auto_bans(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW,
            )

        self.assertEqual(result["succeeded"], 1)
        adapter.disable_account.assert_not_awaited()
        self.assertEqual(complete.await_args.kwargs["status"], "succeeded")

    async def test_pending_recovery_keeps_action_pending_when_growth_finalize_fails(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.capture_account_state.return_value = SourceAccountState(
            "42",
            "a.b@example.com",
            "disabled",
            NOW,
            (ApiKeyState("key-1", "inactive", NOW),),
        )
        with (
            patch.object(
                coordinator.repository,
                "list_pending_auto_ban_actions",
                AsyncMock(return_value=[_pending_action()]),
            ),
            patch.object(coordinator, "_finalize_success", AsyncMock(side_effect=RuntimeError("growth write failed"))),
            patch.object(coordinator, "_finalize_failure", AsyncMock()) as finalize_failure,
        ):
            with self.assertRaisesRegex(RuntimeError, "growth write failed"):
                await coordinator.recover_pending_auto_bans(
                    growth,
                    source_engine=source,
                    adapter=adapter,
                    recovered_at=NOW,
                    auto_ban_enabled=True,
                )

        finalize_failure.assert_not_awaited()

    async def test_pending_manual_ban_recovers_after_source_commit(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.capture_account_state.return_value = SourceAccountState(
            "42",
            "a.b@example.com",
            "disabled",
            NOW,
            (ApiKeyState("key-1", "inactive", NOW),),
        )
        action = {
            **_pending_action(),
            "action_type": "manual_ban",
            "decision_reason": "人工核验",
            "requested_by": "admin-1",
        }
        with (
            patch.object(
                coordinator.repository,
                "list_pending_manual_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator, "_finalize_manual_ban_success", AsyncMock()) as finalize,
            patch.object(coordinator, "_finalize_manual_action_failure", AsyncMock()) as failure,
        ):
            result = await coordinator.recover_pending_manual_actions(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW,
            )

        self.assertEqual(result, {"succeeded": 1, "conflicted": 0, "failed": 0})
        adapter.disable_account.assert_not_awaited()
        finalize.assert_awaited_once()
        failure.assert_not_awaited()

    async def test_pending_manual_release_recovers_partial_result_after_source_commit(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.capture_account_state.return_value = SourceAccountState(
            "42",
            "a.b@example.com",
            "active",
            NOW,
            (
                ApiKeyState("key-1", "active", NOW),
                ApiKeyState("key-2", "revoked", NOW),
            ),
        )
        action = {
            **_pending_action(),
            "action_type": "manual_release",
            "decision_reason": "校园网误报",
            "requested_by": "admin-1",
            "ban_result_details": {
                "user_status": "disabled",
                "user_updated_at": NOW,
                "api_keys": [
                    {"id": "key-1", "status": "inactive", "updated_at": NOW},
                    {"id": "key-2", "status": "inactive", "updated_at": NOW},
                ],
            },
        }
        with (
            patch.object(
                coordinator.repository,
                "list_pending_manual_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator, "_finalize_manual_release_success", AsyncMock()) as finalize,
            patch.object(coordinator, "_finalize_manual_action_failure", AsyncMock()) as failure,
        ):
            result = await coordinator.recover_pending_manual_actions(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW,
            )

        self.assertEqual(result, {"succeeded": 1, "conflicted": 0, "failed": 0})
        adapter.release_account.assert_not_awaited()
        release_result = finalize.await_args.kwargs["release_result"]
        self.assertTrue(release_result.user_restored)
        self.assertEqual(release_result.restored_key_ids, ("key-1",))
        self.assertEqual(release_result.conflicted_key_ids, ("key-2",))
        self.assertTrue(release_result.partial)
        failure.assert_not_awaited()

    async def test_pending_manual_action_keeps_pending_when_growth_finalize_fails(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.capture_account_state.return_value = SourceAccountState(
            "42",
            "a.b@example.com",
            "disabled",
            NOW,
            (ApiKeyState("key-1", "inactive", NOW),),
        )
        action = {
            **_pending_action(),
            "action_type": "manual_ban",
            "decision_reason": "人工核验",
            "requested_by": "admin-1",
        }
        with (
            patch.object(
                coordinator.repository,
                "list_pending_manual_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(
                coordinator,
                "_finalize_manual_ban_success",
                AsyncMock(side_effect=RuntimeError("growth write failed")),
            ),
            patch.object(coordinator, "_finalize_manual_action_failure", AsyncMock()) as failure,
        ):
            with self.assertRaisesRegex(RuntimeError, "growth write failed"):
                await coordinator.recover_pending_manual_actions(
                    growth,
                    source_engine=source,
                    adapter=adapter,
                    recovered_at=NOW,
                )

        adapter.disable_account.assert_not_awaited()
        failure.assert_not_awaited()

    async def test_pending_manual_release_retries_when_source_is_unchanged(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import (
            ApiKeyState,
            ReleaseResult,
            SourceAccountState,
        )

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.capture_account_state.return_value = SourceAccountState(
            "42",
            "a.b@example.com",
            "disabled",
            NOW,
            (ApiKeyState("key-1", "inactive", NOW),),
        )
        adapter.release_account.return_value = ReleaseResult(
            user_restored=True,
            restored_key_ids=("key-1",),
            conflicted_key_ids=(),
            partial=False,
        )
        action = {
            **_pending_action(),
            "action_type": "manual_release",
            "decision_reason": "校园网误报",
            "requested_by": "admin-1",
            "ban_result_details": {
                "user_status": "disabled",
                "user_updated_at": NOW,
                "api_keys": [
                    {"id": "key-1", "status": "inactive", "updated_at": NOW},
                ],
            },
        }
        with (
            patch.object(
                coordinator.repository,
                "list_pending_manual_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator, "_finalize_manual_release_success", AsyncMock()) as finalize,
            patch.object(coordinator, "_finalize_manual_action_failure", AsyncMock()),
        ):
            result = await coordinator.recover_pending_manual_actions(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW + timedelta(minutes=2),
            )

        self.assertEqual(result, {"succeeded": 1, "conflicted": 0, "failed": 0})
        adapter.release_account.assert_awaited_once()
        self.assertEqual(adapter.release_account.await_args.kwargs["changed_at"], NOW)
        finalize.assert_awaited_once()

    async def test_pending_manual_ban_marks_changed_source_state_conflicted(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.capture_account_state.return_value = SourceAccountState(
            "42",
            "other@example.com",
            "active",
            NOW,
            (ApiKeyState("key-1", "active", NOW),),
        )
        action = {
            **_pending_action(),
            "action_type": "manual_ban",
            "decision_reason": "人工核验",
            "requested_by": "admin-1",
        }
        with (
            patch.object(
                coordinator.repository,
                "list_pending_manual_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator, "_finalize_manual_action_conflict", AsyncMock()) as conflict,
            patch.object(coordinator, "_finalize_manual_action_failure", AsyncMock()) as failure,
        ):
            result = await coordinator.recover_pending_manual_actions(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW,
            )

        self.assertEqual(result, {"succeeded": 0, "conflicted": 1, "failed": 0})
        adapter.disable_account.assert_not_awaited()
        conflict.assert_awaited_once()
        failure.assert_not_awaited()

    async def test_pending_manual_release_without_ban_snapshot_fails_closed(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.capture_account_state.return_value = SourceAccountState(
            "42",
            "a.b@example.com",
            "disabled",
            NOW,
            (ApiKeyState("key-1", "inactive", NOW),),
        )
        action = {
            **_pending_action(),
            "action_type": "manual_release",
            "decision_reason": "校园网误报",
            "requested_by": "admin-1",
            "ban_result_details": None,
        }
        with (
            patch.object(
                coordinator.repository,
                "list_pending_manual_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator, "_finalize_manual_release_success", AsyncMock()) as success,
            patch.object(coordinator, "_finalize_manual_action_failure", AsyncMock()) as failure,
        ):
            result = await coordinator.recover_pending_manual_actions(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW,
            )

        self.assertEqual(result, {"succeeded": 0, "conflicted": 0, "failed": 1})
        adapter.release_account.assert_not_awaited()
        success.assert_not_awaited()
        self.assertIsInstance(failure.await_args.kwargs["error"], ValueError)

    async def test_pending_auto_ban_marks_unexpected_source_state_conflicted(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, SourceAccountState

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.capture_account_state.return_value = SourceAccountState(
            "42",
            "a.b@example.com",
            "active",
            NOW,
            (ApiKeyState("key-1", "revoked", NOW),),
        )
        action = _pending_action()

        with (
            patch.object(
                coordinator.repository,
                "list_pending_auto_ban_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator.repository, "complete_action", AsyncMock()) as complete,
            patch.object(coordinator.repository, "upsert_risk_account", AsyncMock()) as upsert,
            patch.object(coordinator.repository, "append_event", AsyncMock()) as event,
        ):
            result = await coordinator.recover_pending_auto_bans(
                growth,
                source_engine=source,
                adapter=adapter,
                recovered_at=NOW,
            )

        self.assertEqual(result["conflicted"], 1)
        adapter.disable_account.assert_not_awaited()
        self.assertEqual(complete.await_args.kwargs["status"], "conflicted")
        self.assertEqual(upsert.await_args.kwargs["risk_status"], "high_risk")
        self.assertEqual(event.await_args.kwargs["event_type"], "auto_ban_conflicted")

    def test_stale_suspicious_email_cannot_ban_a_current_normal_email(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import SourceAccountState
        from app.modules.risk.service import PreparedBanCandidate, evaluate_account_input

        candidate = PreparedBanCandidate(
            risk_account_id="00000000-0000-0000-0000-000000000042",
            evaluation=evaluate_account_input(_pending_action()),
        )
        current = SourceAccountState("42", "normal@example.com", "active", NOW, ())

        self.assertIsNone(coordinator._candidate_for_current_source(candidate, current))

    def test_recovery_rejects_source_write_after_request_as_external_change(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, SourceAccountState

        before = SourceAccountState(
            "42", "a.b@example.com", "active", NOW,
            (ApiKeyState("key-1", "active", NOW),),
        )
        applied_at = NOW + timedelta(minutes=1)
        current = SourceAccountState(
            "42", "a.b@example.com", "disabled", applied_at,
            (ApiKeyState("key-1", "inactive", applied_at),),
        )

        state, enforced = coordinator._classify_recovery_state(
            before=before,
            current=current,
            requested_at=NOW,
        )

        self.assertEqual(state, "conflicted")
        self.assertIsNone(enforced)

    def test_release_recovery_rejects_a_complete_write_after_request_as_external_change(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import ApiKeyState, EnforcementResult, SourceAccountState

        before = SourceAccountState(
            "42", "a.b@example.com", "active", NOW,
            (ApiKeyState("key-1", "active", NOW),),
        )
        enforced = EnforcementResult(
            "disabled", NOW,
            (ApiKeyState("key-1", "inactive", NOW),),
        )
        restored_at = NOW + timedelta(minutes=1)
        current = SourceAccountState(
            "42", "a.b@example.com", "active", restored_at,
            (ApiKeyState("key-1", "active", restored_at),),
        )

        state, result = coordinator._classify_release_recovery_state(
            before=before,
            enforced=enforced,
            current=current,
            requested_at=NOW,
        )

        self.assertEqual(state, "conflicted")
        self.assertIsNone(result)

    async def test_auto_recovery_source_error_leaves_action_pending(self) -> None:
        from app.modules.risk import coordinator

        growth = _TransactionConnection()
        adapter = AsyncMock()
        adapter.capture_account_state.side_effect = ConnectionError("source unavailable")
        with (
            patch.object(
                coordinator.repository,
                "list_pending_auto_ban_actions",
                AsyncMock(return_value=[_pending_action()]),
            ),
            patch.object(coordinator, "_finalize_failure", AsyncMock()) as finalize,
        ):
            result = await coordinator.recover_pending_auto_bans(
                growth,
                source_engine=_SourceEngine(),
                adapter=adapter,
                recovered_at=NOW,
            )

        self.assertEqual(result["failed"], 1)
        finalize.assert_not_awaited()

    async def test_manual_recovery_source_error_leaves_action_pending(self) -> None:
        from app.modules.risk import coordinator

        growth = _TransactionConnection()
        adapter = AsyncMock()
        adapter.capture_account_state.side_effect = ConnectionError("source unavailable")
        action = {
            **_pending_action(),
            "action_type": "manual_ban",
            "decision_reason": "人工核验",
            "requested_by": "admin-1",
        }
        with (
            patch.object(
                coordinator.repository,
                "list_pending_manual_actions",
                AsyncMock(return_value=[action]),
            ),
            patch.object(coordinator, "_finalize_manual_action_failure", AsyncMock()) as finalize,
        ):
            result = await coordinator.recover_pending_manual_actions(
                growth,
                source_engine=_SourceEngine(),
                adapter=adapter,
                recovered_at=NOW,
            )

        self.assertEqual(result["failed"], 1)
        finalize.assert_not_awaited()


class _TransactionConnection:
    def __init__(self):
        self.commit = AsyncMock()

    def begin(self):
        return _async_context(self)


class _SourceEngine:
    def __init__(self):
        self.read_connection = object()
        self.write_connection = object()
        self.dispose = AsyncMock()

    def connect(self):
        return _async_context(self.read_connection)

    def begin(self):
        return _async_context(self.write_connection)


class _FailingSourceEngine:
    def __init__(self, error: Exception):
        self.error = error

    def connect(self):
        return _raising_async_context(self.error)


def _pending_action() -> dict:
    return {
        "risk_action_id": "00000000-0000-0000-0000-000000000043",
        "risk_account_id": "00000000-0000-0000-0000-000000000042",
        "external_user_id": "42",
        "email": "a.b@example.com",
        "decision_reason": "email_and_shared_ip",
        "matched_email_rules": ["email_local_part_dot"],
        "shared_ip_evidence": [{
            "ip_address": "14.31.212.25",
            "distinct_account_count": 3,
            "external_user_ids": ["42", "43", "44"],
            "sources": ["user_audit"],
            "first_seen_at": NOW,
            "last_seen_at": NOW,
        }],
        "source_user_status_before": "active",
        "source_user_updated_at_before": NOW,
        "source_api_key_states_before": [
            {"id": "key-1", "status": "active", "updated_at": NOW}
        ],
        "requested_at": NOW,
    }


@asynccontextmanager
async def _async_context(value):
    yield value


@asynccontextmanager
async def _raising_async_context(error: Exception):
    raise error
    yield


if __name__ == "__main__":
    unittest.main()
