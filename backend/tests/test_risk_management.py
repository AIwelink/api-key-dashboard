from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)
RISK_ID = UUID("00000000-0000-0000-0000-000000000042")
BAN_ACTION_ID = UUID("00000000-0000-0000-0000-000000000043")
RELEASE_ACTION_ID = UUID("00000000-0000-0000-0000-000000000044")


class RiskManagementTests(unittest.IsolatedAsyncioTestCase):
    async def test_false_positive_sets_override_and_clears_review_state(self) -> None:
        from app.modules.risk import management

        growth = _TransactionConnection()
        account = {
            "risk_account_id": str(RISK_ID),
            "site_id": "aiwelink",
            "external_user_id": "42",
            "email": "a.b@example.com",
            "risk_status": "high_risk",
        }
        with (
            patch.object(management.repository, "acquire_cycle_lock", AsyncMock(return_value=True)) as acquire,
            patch.object(management.repository, "release_cycle_lock", AsyncMock()) as release,
            patch.object(management.repository, "get_account", AsyncMock(return_value=account)),
            patch.object(management.repository, "set_manual_override", AsyncMock(return_value={
                **account,
                "risk_status": "cleared",
                "manual_override_active": True,
            })) as override,
            patch.object(management.repository, "append_event", AsyncMock()) as event,
        ):
            result = await management.set_false_positive(
                object(),
                risk_account_id=RISK_ID,
                actor_id="admin-1",
                actor_name="Admin",
                reason="已核验本人使用",
                growth_session_factory=lambda db: _async_context(growth),
            )

        self.assertEqual(result["risk_status"], "cleared")
        self.assertTrue(override.await_args.kwargs["active"])
        self.assertEqual(override.await_args.kwargs["risk_status"], "cleared")
        self.assertEqual(event.await_args.kwargs["event_type"], "manual_override_set")
        acquire.assert_awaited_once()
        release.assert_awaited_once()

    async def test_false_positive_requires_release_for_banned_account(self) -> None:
        from app.modules.risk import management

        growth = _TransactionConnection()
        with (
            patch.object(management.repository, "acquire_cycle_lock", AsyncMock(return_value=True)),
            patch.object(management.repository, "release_cycle_lock", AsyncMock()),
            patch.object(management.repository, "get_account", AsyncMock(return_value={
                "risk_account_id": str(RISK_ID),
                "risk_status": "banned",
            })),
        ):
            with self.assertRaisesRegex(ValueError, "release"):
                await management.set_false_positive(
                    object(),
                    risk_account_id=RISK_ID,
                    actor_id="admin-1",
                    actor_name="Admin",
                    reason="误报",
                    growth_session_factory=lambda db: _async_context(growth),
                )

    async def test_manual_release_pre_records_action_then_restores_and_sets_override(self) -> None:
        from app.modules.risk import management
        from app.modules.risk.adapters.sub2api import ReleaseResult

        growth = _TransactionConnection()
        source = _SourceEngine()
        adapter = AsyncMock()
        adapter.release_account.return_value = ReleaseResult(
            user_restored=True,
            restored_key_ids=("key-1",),
            conflicted_key_ids=("key-2",),
            partial=True,
        )
        account = {
            "risk_account_id": str(RISK_ID),
            "site_id": "aiwelink",
            "external_user_id": "42",
            "email": "a.b@example.com",
            "risk_status": "banned",
        }
        ban_action = {
            "risk_action_id": str(BAN_ACTION_ID),
            "source_user_status_before": "active",
            "source_user_updated_at_before": NOW,
            "source_api_key_states_before": [
                {"id": "key-1", "status": "active", "updated_at": NOW},
                {"id": "key-2", "status": "active", "updated_at": NOW},
            ],
            "result_details": {
                "user_status": "disabled",
                "user_updated_at": NOW,
                "api_keys": [
                    {"id": "key-1", "status": "inactive", "updated_at": NOW},
                    {"id": "key-2", "status": "inactive", "updated_at": NOW},
                ],
            },
        }
        call_order = []

        async def create_action(*args, **kwargs):
            call_order.append("action_committed")
            return {"risk_action_id": str(RELEASE_ACTION_ID), "action_status": "pending"}

        async def release(*args, **kwargs):
            call_order.append("source_released")
            return adapter.release_account.return_value

        adapter.release_account.side_effect = release
        with (
            patch.object(management.repository, "acquire_cycle_lock", AsyncMock(return_value=True)),
            patch.object(management.repository, "release_cycle_lock", AsyncMock()),
            patch.object(management.repository, "get_account", AsyncMock(return_value=account)),
            patch.object(management.repository, "get_latest_succeeded_ban_action", AsyncMock(return_value=ban_action)),
            patch.object(management.repository, "create_action", create_action),
            patch.object(management.repository, "complete_action", AsyncMock()) as complete,
            patch.object(management.repository, "set_manual_override", AsyncMock(return_value={
                **account,
                "risk_status": "released",
                "manual_override_active": True,
            })) as override,
            patch.object(management.repository, "set_stats_exclusion", AsyncMock()) as exclusion,
            patch.object(management.repository, "append_event", AsyncMock()) as event,
            patch.object(management.operations_repository, "replace_affected_aggregates", AsyncMock()),
        ):
            result = await management.manual_release(
                object(),
                risk_account_id=RISK_ID,
                actor_id="admin-1",
                actor_name="Admin",
                reason="校园网误报",
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

        self.assertEqual(call_order, ["action_committed", "source_released"])
        self.assertTrue(result["partial"])
        self.assertEqual(complete.await_args.kwargs["status"], "succeeded")
        self.assertEqual(override.await_args.kwargs["risk_status"], "released")
        self.assertFalse(exclusion.await_args.kwargs["excluded"])
        self.assertEqual(event.await_args.kwargs["event_type"], "manual_release_partial")
        source.dispose.assert_awaited_once()


class _TransactionConnection:
    def __init__(self):
        self.commit = AsyncMock()

    def begin(self):
        return _async_context(self)


class _SourceEngine:
    def __init__(self):
        self.write_connection = object()
        self.dispose = AsyncMock()

    def begin(self):
        return _async_context(self.write_connection)


@asynccontextmanager
async def _async_context(value):
    yield value


if __name__ == "__main__":
    unittest.main()
