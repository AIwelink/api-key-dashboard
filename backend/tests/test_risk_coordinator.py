from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
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

    async def test_source_failure_does_not_advance_other_stream_cursor(self) -> None:
        from app.modules.risk import coordinator
        from app.modules.risk.adapters.sub2api import SourcePage

        growth = _TransactionConnection()
        source = _SourceEngine()
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
            patch.object(coordinator, "reconcile_risk_inputs", AsyncMock(return_value=[candidate])),
            patch.object(coordinator.repository, "create_action", create_action),
            patch.object(coordinator.repository, "complete_action", AsyncMock()) as complete,
            patch.object(coordinator.repository, "upsert_risk_account", AsyncMock(return_value={
                "risk_account_id": candidate.risk_account_id,
            })) as account_upsert,
            patch.object(coordinator.repository, "set_stats_exclusion", AsyncMock()) as exclude,
            patch.object(coordinator.repository, "append_event", AsyncMock()),
            patch.object(coordinator.operations_repository, "replace_affected_aggregates", AsyncMock()) as aggregate,
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
                recovered_at=NOW,
            )

        self.assertEqual(result, {"succeeded": 1, "conflicted": 0, "failed": 0})
        adapter.disable_account.assert_awaited_once()
        self.assertEqual(complete.await_args.kwargs["status"], "succeeded")

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


if __name__ == "__main__":
    unittest.main()
