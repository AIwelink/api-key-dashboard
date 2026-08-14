from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from app.modules.sub2api import smart_scheduling_service
from app.modules.sub2api.client import InvalidAdminApiKeyError
from app.modules.sub2api.smart_scheduling import default_smart_scheduling_rules
from app.modules.sub2api.smart_scheduling_service import (
    acquire_smart_scheduling_lease,
    release_smart_scheduling_lease,
    run_smart_scheduling,
)


class AsyncCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class SmartSchedulingServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 7, 27, 7, 0, tzinfo=UTC)
        self.rules = default_smart_scheduling_rules()

    def account(
        self,
        remote_id: int,
        *,
        group_ids: list[int] | None = None,
        account_type: str = "plus",
        priority: int = 250,
        concurrency: int = 20,
        load_factor: int = 10,
        used: float = 20,
        created_at: str | None = None,
        status: str = "active",
        schedulable: bool | None = True,
        error_message: str | None = None,
    ) -> dict[str, object]:
        return {
            "remote_account_id": remote_id,
            "account_type": account_type,
            "priority": priority,
            "concurrency": concurrency,
            "load_factor": load_factor,
            "group_ids": group_ids or [3],
            "created_at": created_at
            or (self.now + timedelta(seconds=remote_id)).isoformat(),
            "status": status,
            "schedulable": schedulable,
            "error_message": error_message,
            "usage_snapshot": {
                "codex_7d_used_percent": used,
                "codex_7d_reset_at": (self.now + timedelta(days=3)).isoformat(),
                "codex_usage_synced_at": self.now.isoformat(),
            },
        }

    @staticmethod
    def bulk_targets(client: SimpleNamespace) -> dict[int, dict[str, object]]:
        targets: dict[int, dict[str, object]] = {}
        for call in client.bulk_update_accounts_runtime.await_args_list:
            account_ids, payload = call.args
            for account_id in account_ids:
                targets[int(account_id)] = payload
        return targets

    def db(
        self,
        *,
        states: list[dict[str, object]] | None = None,
        lease_document: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        async def acquire(
            _query: dict[str, object],
            update: dict[str, dict[str, object]],
            **_kwargs: object,
        ) -> dict[str, object]:
            if lease_document is not None:
                return lease_document
            return {"owner": update["$set"]["owner"]}

        return SimpleNamespace(
            operation_locks=SimpleNamespace(
                find_one_and_update=AsyncMock(side_effect=acquire),
                delete_one=AsyncMock(return_value=SimpleNamespace(deleted_count=1)),
            ),
            sub2api_smart_scheduling_states=SimpleNamespace(
                find=MagicMock(return_value=AsyncCursor(states or [])),
                update_one=AsyncMock(),
            ),
            sub2api_smart_scheduling_runs=SimpleNamespace(
                insert_one=AsyncMock(),
                update_one=AsyncMock(),
            ),
            sub2api_smart_scheduling_outcomes=SimpleNamespace(
                update_one=AsyncMock(),
            ),
        )

    def site(self) -> dict[str, object]:
        return {
            "id": "api-5001",
            "base_url": "https://sub2.example.com",
            "sql_dsn": "postgresql://reader:secret@postgres/sub2api",
        }

    async def test_runner_deduplicates_multi_group_account_and_updates_minimal_fields(self) -> None:
        account = self.account(7, group_ids=[3, 4])
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 20}
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={
                    "success": 1,
                    "failed": 0,
                    "success_ids": [7],
                    "failed_ids": [],
                }
            ),
        )
        db = self.db()

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[account, dict(account)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                },
                4: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                },
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        client.get_account.assert_awaited_once_with(7)
        client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [7],
            {"priority": 200, "concurrency": 30, "group_ids": [3, 4]},
        )
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["failed"], 0)
        db.sub2api_smart_scheduling_outcomes.update_one.assert_not_awaited()
        release_filter = db.operation_locks.delete_one.await_args.args[0]
        self.assertEqual(release_filter["_id"], "smart-scheduling:api-5001")
        self.assertTrue(release_filter["owner"])

    async def test_existing_state_mode_transition_writes_sparse_event(self) -> None:
        db = self.db(
            states=[
                {
                    "remote_account_id": 7,
                    "mode": "normal",
                    "last_target": {"priority": 250, "concurrency": 30},
                }
            ]
        )
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 30}
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [7], "failed_ids": []}
            ),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7, used=90)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(result["changed"], 1)
        outcome = db.sub2api_smart_scheduling_outcomes.update_one.await_args.args[1][
            "$set"
        ]
        self.assertEqual(outcome["event_type"], "state_transition")
        self.assertEqual(
            outcome["previous_state"],
            {"mode": "normal", "target": {"priority": 250, "concurrency": 30}},
        )
        self.assertEqual(
            outcome["applied_state"],
            {
                "mode": "extreme",
                "target": {
                    "priority": 10,
                    "concurrency": 100,
                    "load_factor": 10000,
                },
            },
        )

    async def test_reason_only_state_refresh_does_not_write_event(self) -> None:
        db = self.db(
            states=[
                {
                    "remote_account_id": 7,
                    "mode": "normal",
                    "last_target": {"priority": 200, "concurrency": 30},
                    "last_reason": "previous_reason",
                }
            ]
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7, priority=200, concurrency=30)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=SimpleNamespace(
                get_account=AsyncMock(),
                bulk_update_accounts_runtime=AsyncMock(),
            ),
            now=self.now,
        )

        self.assertEqual(result["unchanged"], 1)
        db.sub2api_smart_scheduling_outcomes.update_one.assert_not_awaited()

    async def test_target_only_transition_writes_sparse_event(self) -> None:
        db = self.db(
            states=[
                {
                    "remote_account_id": 7,
                    "mode": "normal",
                    "last_target": {"priority": 201, "concurrency": 30},
                }
            ]
        )
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 30}
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [7], "failed_ids": []}
            ),
        )

        await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7, concurrency=30)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        outcome = db.sub2api_smart_scheduling_outcomes.update_one.await_args.args[1][
            "$set"
        ]
        self.assertEqual(outcome["event_type"], "state_transition")
        self.assertEqual(outcome["previous_state"]["mode"], "normal")
        self.assertEqual(outcome["applied_state"]["mode"], "normal")
        self.assertEqual(
            outcome["previous_state"]["target"]["priority"],
            201,
        )
        self.assertEqual(outcome["applied_state"]["target"]["priority"], 200)

    async def test_skipped_decision_does_not_write_event(self) -> None:
        result_db = self.db()

        result = await run_smart_scheduling(
            result_db,
            site=self.site(),
            accounts=[self.account(7, account_type="free")],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=SimpleNamespace(
                get_account=AsyncMock(),
                bulk_update_accounts_runtime=AsyncMock(),
            ),
            now=self.now,
        )

        self.assertEqual(result["skipped"], 1)
        result_db.sub2api_smart_scheduling_outcomes.update_one.assert_not_awaited()

    async def test_accounts_with_same_groups_receive_distinct_queue_priorities(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                side_effect=[
                    {"id": 7, "priority": 250, "concurrency": 20, "group_ids": [3]},
                    {"id": 8, "priority": 250, "concurrency": 20, "group_ids": [3]},
                ]
            ),
            bulk_update_accounts_runtime=AsyncMock(
                side_effect=[
                    {"success": 1, "failed": 0, "success_ids": [7], "failed_ids": []},
                    {"success": 1, "failed": 0, "success_ids": [8], "failed_ids": []},
                ]
            ),
        )

        result = await run_smart_scheduling(
            self.db(),
            site=self.site(),
            accounts=[self.account(7), self.account(8)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(
            [call.args for call in client.bulk_update_accounts_runtime.await_args_list],
            [
                ([7], {"priority": 200, "concurrency": 30, "group_ids": [3]}),
                ([8], {"priority": 201, "concurrency": 30, "group_ids": [3]}),
            ],
        )
        self.assertEqual(result["changed"], 2)
        self.assertEqual(result["failed"], 0)

    async def test_accounts_with_different_groups_are_updated_in_separate_batches(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                side_effect=[
                    {"id": 7, "priority": 250, "concurrency": 20, "group_ids": [3]},
                    {"id": 8, "priority": 250, "concurrency": 20, "group_ids": [4]},
                ]
            ),
            bulk_update_accounts_runtime=AsyncMock(
                side_effect=[
                    {"success": 1, "failed": 0, "success_ids": [7], "failed_ids": []},
                    {"success": 1, "failed": 0, "success_ids": [8], "failed_ids": []},
                ]
            ),
        )

        result = await run_smart_scheduling(
            self.db(),
            site=self.site(),
            accounts=[self.account(7), self.account(8, group_ids=[4])],
            group_settings={
                3: {"type_priority_enabled": True, "quota_acceleration_enabled": False},
                4: {"type_priority_enabled": True, "quota_acceleration_enabled": False},
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(client.bulk_update_accounts_runtime.await_count, 2)
        self.assertEqual(
            [call.args for call in client.bulk_update_accounts_runtime.await_args_list],
            [
                ([7], {"priority": 200, "concurrency": 30, "group_ids": [3]}),
                ([8], {"priority": 201, "concurrency": 30, "group_ids": [4]}),
            ],
        )
        self.assertEqual(result["changed"], 2)
        self.assertEqual(result["failed"], 0)

    async def test_site_wide_team_queue_spans_enabled_groups(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                side_effect=[
                    {"id": 7, "priority": 50, "concurrency": 20, "group_ids": [3]},
                    {"id": 8, "priority": 51, "concurrency": 20, "group_ids": [4]},
                ]
            ),
            bulk_update_accounts_runtime=AsyncMock(
                side_effect=[
                    {"success_ids": [7], "failed_ids": []},
                    {"success_ids": [8], "failed_ids": []},
                ]
            ),
        )
        accounts = [
            self.account(
                7,
                account_type="team",
                group_ids=[3],
                created_at="2026-01-02T00:00:00+00:00",
                priority=50,
            ),
            self.account(
                8,
                account_type="team",
                group_ids=[4],
                created_at="2026-01-01T00:00:00+00:00",
                priority=51,
            ),
        ]

        result = await run_smart_scheduling(
            self.db(),
            site=self.site(),
            accounts=accounts,
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                },
                4: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                },
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(
            self.bulk_targets(client),
            {
                8: {"priority": 50, "concurrency": 30, "group_ids": [4]},
                7: {"priority": 51, "concurrency": 30, "group_ids": [3]},
            },
        )
        self.assertEqual(result["changed"], 2)

    async def test_unavailable_oldest_moves_back_and_recovery_returns_it_to_head(self) -> None:
        oldest = self.account(
            1,
            account_type="team",
            created_at="2026-01-01T00:00:00+00:00",
            priority=50,
            error_message="API returned 429",
        )
        newer = self.account(
            2,
            account_type="team",
            created_at="2026-01-02T00:00:00+00:00",
            priority=51,
        )
        first_client = SimpleNamespace(
            get_account=AsyncMock(
                side_effect=[
                    {"id": 1, "priority": 50, "concurrency": 20, "group_ids": [3]},
                    {"id": 2, "priority": 51, "concurrency": 20, "group_ids": [3]},
                ]
            ),
            bulk_update_accounts_runtime=AsyncMock(
                side_effect=[
                    {"success_ids": [1], "failed_ids": []},
                    {"success_ids": [2], "failed_ids": []},
                ]
            ),
        )

        first = await run_smart_scheduling(
            self.db(),
            site=self.site(),
            accounts=[oldest, newer],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=first_client,
            now=self.now,
        )

        self.assertEqual(
            self.bulk_targets(first_client),
            {
                2: {"priority": 50, "concurrency": 30, "group_ids": [3]},
                1: {"priority": 51, "concurrency": 30, "group_ids": [3]},
            },
        )
        self.assertEqual(first["changed"], 2)

        recovered_oldest = dict(oldest)
        recovered_oldest.update(
            {"priority": 51, "concurrency": 30, "error_message": None}
        )
        advanced_newer = dict(newer)
        advanced_newer.update({"priority": 50, "concurrency": 30})
        second_client = SimpleNamespace(
            get_account=AsyncMock(
                side_effect=[
                    {"id": 1, "priority": 51, "concurrency": 30, "group_ids": [3]},
                    {"id": 2, "priority": 50, "concurrency": 30, "group_ids": [3]},
                ]
            ),
            bulk_update_accounts_runtime=AsyncMock(
                side_effect=[
                    {"success_ids": [1], "failed_ids": []},
                    {"success_ids": [2], "failed_ids": []},
                ]
            ),
        )

        second = await run_smart_scheduling(
            self.db(),
            site=self.site(),
            accounts=[recovered_oldest, advanced_newer],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-2",
            rules=self.rules,
            client=second_client,
            now=self.now,
        )

        self.assertEqual(
            self.bulk_targets(second_client),
            {
                1: {"priority": 50, "concurrency": 30, "group_ids": [3]},
                2: {"priority": 51, "concurrency": 30, "group_ids": [3]},
            },
        )
        self.assertEqual(second["changed"], 2)

    async def test_extreme_account_does_not_consume_team_normal_slot(self) -> None:
        extreme = self.account(
            1,
            account_type="team",
            created_at="2026-01-01T00:00:00+00:00",
            priority=10,
            concurrency=100,
            load_factor=10000,
            used=90,
        )
        normal = self.account(
            2,
            account_type="team",
            created_at="2026-01-02T00:00:00+00:00",
            priority=51,
        )
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={
                    "id": 2,
                    "priority": 51,
                    "concurrency": 20,
                    "group_ids": [3],
                }
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [2], "failed_ids": []}
            ),
        )

        result = await run_smart_scheduling(
            self.db(),
            site=self.site(),
            accounts=[extreme, normal],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        client.get_account.assert_awaited_once_with(2)
        client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [2], {"priority": 50, "concurrency": 30, "group_ids": [3]}
        )
        self.assertEqual(result["changed"], 1)

    async def test_queue_metadata_is_persisted_without_baseline_event(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={
                    "id": 1,
                    "priority": 51,
                    "concurrency": 20,
                    "group_ids": [3],
                }
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [1], "failed_ids": []}
            ),
        )
        db = self.db()

        await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[
                self.account(
                    1,
                    account_type="team",
                    created_at="2026-01-01T00:00:00+00:00",
                    priority=51,
                )
            ],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        state_update = (
            db.sub2api_smart_scheduling_states.update_one.await_args.args[1]["$set"]
        )
        self.assertEqual(state_update["queue_partition"], "usable")
        self.assertEqual(state_update["queue_index"], 0)
        self.assertEqual(state_update["queue_priority"], 50)
        db.sub2api_smart_scheduling_outcomes.update_one.assert_not_awaited()

    async def test_multi_group_flags_are_aggregated_by_enabled_strategy(self) -> None:
        account = self.account(7, group_ids=[3], used=90)
        account_from_other_group = dict(account)
        account_from_other_group["group_ids"] = [4]
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 20}
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={
                    "success": 1,
                    "failed": 0,
                    "success_ids": [7],
                    "failed_ids": [],
                }
            ),
        )

        result = await run_smart_scheduling(
            self.db(),
            site=self.site(),
            accounts=[account, account_from_other_group],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                },
                4: {
                    "type_priority_enabled": False,
                    "quota_acceleration_enabled": True,
                },
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [7],
            {
                "priority": 10,
                "concurrency": 100,
                "load_factor": 10000,
                "group_ids": [3, 4],
            },
        )
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["changed"], 1)

    async def test_latest_manual_priority_is_revalidated_before_update(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 220, "concurrency": 30}
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={
                    "success": 1,
                    "failed": 0,
                    "success_ids": [7],
                    "failed_ids": [],
                }
            ),
        )

        result = await run_smart_scheduling(
            self.db(),
            site=self.site(),
            accounts=[self.account(7, priority=300, concurrency=30)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        client.get_account.assert_awaited_once_with(7)
        client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [7],
            {"priority": 200, "concurrency": 30, "group_ids": [3]},
        )
        self.assertEqual(result["changed"], 1)

    async def test_default_off_does_not_acquire_lease_or_call_remote_api(self) -> None:
        db = self.db()
        client = SimpleNamespace(
            get_account=AsyncMock(),
            bulk_update_accounts_runtime=AsyncMock(),
        )

        with (
            patch.object(
                smart_scheduling_service,
                "fetch_admin_api_key",
                AsyncMock(),
            ) as fetch_key,
            patch.object(
                smart_scheduling_service,
                "Sub2ApiClient",
            ) as client_constructor,
        ):
            result = await run_smart_scheduling(
                db,
                site=self.site(),
                accounts=[self.account(7)],
                group_settings={
                    3: {
                        "type_priority_enabled": False,
                        "quota_acceleration_enabled": False,
                    }
                },
                probe_run_id="probe-1",
                rules=self.rules,
                client=client,
                now=self.now,
            )

        self.assertEqual(result["status"], "disabled")
        db.operation_locks.find_one_and_update.assert_not_awaited()
        fetch_key.assert_not_awaited()
        client_constructor.assert_not_called()
        client.get_account.assert_not_awaited()
        client.bulk_update_accounts_runtime.assert_not_awaited()

    async def test_client_is_built_lazily_for_first_candidate_change(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 20}
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={
                    "success": 1,
                    "failed": 0,
                    "success_ids": [7],
                    "failed_ids": [],
                }
            ),
        )
        fetch_key = AsyncMock(return_value="admin-key")

        with (
            patch.object(
                smart_scheduling_service,
                "fetch_admin_api_key",
                fetch_key,
            ),
            patch.object(
                smart_scheduling_service,
                "Sub2ApiClient",
                return_value=client,
            ) as client_constructor,
        ):
            result = await run_smart_scheduling(
                self.db(),
                site=self.site(),
                accounts=[self.account(7)],
                group_settings={
                    3: {
                        "type_priority_enabled": True,
                        "quota_acceleration_enabled": False,
                    }
                },
                probe_run_id="probe-1",
                rules=self.rules,
                now=self.now,
            )

        fetch_key.assert_awaited_once_with(self.site()["sql_dsn"])
        client_constructor.assert_called_once_with(
            base_url=self.site()["base_url"],
            token="admin-key",
        )
        client.get_account.assert_awaited_once_with(7)
        client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [7],
            {"priority": 200, "concurrency": 30, "group_ids": [3]},
        )
        self.assertEqual(result["changed"], 1)

    async def test_partial_bulk_failure_is_recorded_per_account(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                side_effect=[
                    {"id": 7, "priority": 250, "concurrency": 20},
                    {"id": 8, "priority": 250, "concurrency": 20},
                ]
            ),
            bulk_update_accounts_runtime=AsyncMock(
                side_effect=[
                    {
                        "success": 0,
                        "failed": 1,
                        "success_ids": [],
                        "failed_ids": [7],
                        "results": [{"account_id": 7, "success": False}],
                    },
                    {
                        "success": 1,
                        "failed": 0,
                        "success_ids": [8],
                        "failed_ids": [],
                        "results": [{"account_id": 8, "success": True}],
                    },
                ]
            ),
        )
        db = self.db()

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7), self.account(8)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["changed"], 1)
        self.assertEqual(
            [call.args for call in client.bulk_update_accounts_runtime.await_args_list],
            [
                ([7], {"priority": 200, "concurrency": 30, "group_ids": [3]}),
                ([8], {"priority": 201, "concurrency": 30, "group_ids": [3]}),
            ],
        )
        first_outcome = (
            db.sub2api_smart_scheduling_outcomes.update_one.await_args_list[0]
            .args[1]["$set"]
        )
        self.assertEqual(first_outcome["status"], "failed")
        self.assertEqual(first_outcome["event_type"], "remote_update_failed")
        self.assertNotIn("previous_state", first_outcome)
        self.assertNotIn("applied_state", first_outcome)
        self.assertEqual(first_outcome["error_code"], "remote_update_failed")
        self.assertEqual(first_outcome["error_type"], "BulkUpdateAccountFailed")
        self.assertEqual(
            {key for key in first_outcome if key.startswith("error")},
            {"error_code", "error_type"},
        )

    async def test_admin_auth_failure_stops_later_remote_writes(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                side_effect=[
                    InvalidAdminApiKeyError("rejected admin-secret"),
                    {"id": 8, "priority": 250, "concurrency": 20},
                ]
            ),
            bulk_update_accounts_runtime=AsyncMock(),
        )
        db = self.db()

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7), self.account(8)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(client.get_account.await_count, 1)
        client.bulk_update_accounts_runtime.assert_not_awaited()
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["changed"], 0)
        self.assertEqual(
            db.sub2api_smart_scheduling_outcomes.update_one.await_count,
            1,
        )
        failed_outcome = (
            db.sub2api_smart_scheduling_outcomes.update_one.await_args.args[1]["$set"]
        )
        self.assertEqual(failed_outcome["error_code"], "admin_auth_error")
        self.assertEqual(failed_outcome["error_type"], "InvalidAdminApiKeyError")
        self.assertEqual(failed_outcome["event_type"], "remote_update_failed")
        self.assertNotIn("admin-secret", str(failed_outcome))

    async def test_client_configuration_failure_stops_before_later_accounts(self) -> None:
        db = self.db()
        fetch_key = AsyncMock(
            side_effect=ValueError(
                "postgresql://reader:secret@postgres/sub2api is not configured"
            )
        )

        with (
            patch.object(
                smart_scheduling_service,
                "fetch_admin_api_key",
                fetch_key,
            ),
            patch.object(
                smart_scheduling_service,
                "Sub2ApiClient",
            ) as client_constructor,
        ):
            result = await run_smart_scheduling(
                db,
                site=self.site(),
                accounts=[self.account(7), self.account(8)],
                group_settings={
                    3: {
                        "type_priority_enabled": True,
                        "quota_acceleration_enabled": False,
                    }
                },
                probe_run_id="probe-1",
                rules=self.rules,
                now=self.now,
            )

        fetch_key.assert_awaited_once_with(self.site()["sql_dsn"])
        client_constructor.assert_not_called()
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(result["changed"], 0)
        self.assertEqual(
            db.sub2api_smart_scheduling_outcomes.update_one.await_count,
            1,
        )
        failed_outcome = (
            db.sub2api_smart_scheduling_outcomes.update_one.await_args.args[1]["$set"]
        )
        self.assertEqual(
            failed_outcome["error_code"],
            "admin_api_configuration_error",
        )
        self.assertEqual(failed_outcome["error_type"], "ValueError")
        self.assertEqual(failed_outcome["event_type"], "remote_update_failed")
        self.assertNotIn("reader:secret", str(failed_outcome))

    async def test_missing_sql_dsn_does_not_fall_back_to_site_token(self) -> None:
        db = self.db()
        site = {
            "id": "api-5001",
            "base_url": "https://sub2.example.com",
            "token": "legacy-admin-secret",
        }
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 20}
            ),
            bulk_update_accounts_runtime=AsyncMock(),
        )

        with (
            patch.object(
                smart_scheduling_service,
                "fetch_admin_api_key",
                AsyncMock(),
            ) as fetch_key,
            patch.object(
                smart_scheduling_service,
                "Sub2ApiClient",
                return_value=client,
            ) as client_constructor,
        ):
            result = await run_smart_scheduling(
                db,
                site=site,
                accounts=[self.account(7)],
                group_settings={
                    3: {
                        "type_priority_enabled": True,
                        "quota_acceleration_enabled": False,
                    }
                },
                probe_run_id="probe-1",
                rules=self.rules,
                now=self.now,
            )

        fetch_key.assert_not_awaited()
        client_constructor.assert_not_called()
        self.assertEqual(result["failed"], 1)
        failed_outcome = (
            db.sub2api_smart_scheduling_outcomes.update_one.await_args.args[1]["$set"]
        )
        self.assertEqual(
            failed_outcome["error_code"],
            "admin_api_configuration_error",
        )
        self.assertEqual(failed_outcome["event_type"], "remote_update_failed")
        self.assertNotIn("legacy-admin-secret", str(failed_outcome))

    async def test_stale_extreme_state_is_held_without_remote_calls(self) -> None:
        reset_at = self.now + timedelta(days=3)
        db = self.db(
            states=[
                {
                    "site_id": "api-5001",
                    "remote_account_id": 7,
                    "mode": "extreme",
                    "seven_day_reset_at": reset_at.isoformat(),
                }
            ]
        )
        account = self.account(7, priority=10, concurrency=100)
        account["usage_snapshot"]["codex_usage_synced_at"] = (
            self.now - timedelta(minutes=6)
        ).isoformat()
        client = SimpleNamespace(
            get_account=AsyncMock(),
            bulk_update_accounts_runtime=AsyncMock(),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[account],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(result["skipped"], 1)
        client.get_account.assert_not_awaited()
        client.bulk_update_accounts_runtime.assert_not_awaited()
        db.sub2api_smart_scheduling_outcomes.update_one.assert_not_awaited()

    async def test_states_are_preloaded_once_with_compact_projection(self) -> None:
        reset_at = self.now + timedelta(days=3)
        db = self.db(
            states=[
                {
                    "site_id": "api-5001",
                    "remote_account_id": remote_id,
                    "mode": "extreme",
                    "seven_day_reset_at": reset_at.isoformat(),
                }
                for remote_id in (7, 8)
            ]
        )
        accounts = [
            self.account(remote_id, priority=10, concurrency=100)
            for remote_id in (7, 8)
        ]
        for account in accounts:
            account["usage_snapshot"]["codex_usage_synced_at"] = (
                self.now - timedelta(minutes=6)
            ).isoformat()

        await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=accounts,
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=SimpleNamespace(
                get_account=AsyncMock(),
                bulk_update_accounts_runtime=AsyncMock(),
            ),
            now=self.now,
        )

        db.sub2api_smart_scheduling_states.find.assert_called_once_with(
            {
                "site_id": "api-5001",
                "remote_account_id": {"$in": [7, 8]},
            },
            {
                "remote_account_id": 1,
                "mode": 1,
                "adapted_type": 1,
                "last_strategy": 1,
                "last_reason": 1,
                "last_target": 1,
                "seven_day_reset_at": 1,
                "rate_limit_detected_at": 1,
                "original_load_factor": 1,
                "original_load_factor_captured_at": 1,
            },
        )

    async def test_first_extreme_429_persists_pending_without_remote_update(self) -> None:
        account = self.account(
            7,
            priority=10,
            concurrency=100,
            load_factor=10000,
            used=95,
        )
        account["error_message"] = "API returned 429"
        db = self.db(states=[{"remote_account_id": 7, "mode": "extreme"}])
        client = SimpleNamespace(
            get_account=AsyncMock(),
            bulk_update_accounts_runtime=AsyncMock(),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[account],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        client.get_account.assert_not_awaited()
        client.bulk_update_accounts_runtime.assert_not_awaited()
        self.assertEqual(result["unchanged"], 1)
        state = (
            db.sub2api_smart_scheduling_states.update_one.await_args.args[1]["$set"]
        )
        self.assertEqual(state["mode"], "rate_limit_pending")
        self.assertEqual(state["rate_limit_detected_at"], self.now.isoformat())

    async def test_elapsed_429_delay_bulk_restores_normal_values(self) -> None:
        detected_at = self.now - timedelta(minutes=31)
        db = self.db(
            states=[
                {
                    "remote_account_id": 7,
                    "mode": "rate_limit_pending",
                    "rate_limit_detected_at": detected_at,
                    "original_load_factor": 7,
                    "original_load_factor_captured_at": self.now
                    - timedelta(hours=1),
                }
            ]
        )
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={
                    "id": 7,
                    "priority": 10,
                    "concurrency": 100,
                    "group_ids": [3],
                }
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [7], "failed_ids": []}
            ),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7, priority=10, concurrency=100, used=95)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [7],
            {
                "priority": 200,
                "concurrency": 30,
                "load_factor": 7,
                "group_ids": [3],
            },
        )
        self.assertEqual(result["changed"], 1)
        state = (
            db.sub2api_smart_scheduling_states.update_one.await_args.args[1]["$set"]
        )
        self.assertEqual(state["mode"], "rate_limited_cooldown")
        self.assertEqual(
            state["rate_limit_detected_at"], detected_at.isoformat()
        )
        state_update = (
            db.sub2api_smart_scheduling_states.update_one.await_args.args[1]
        )
        self.assertEqual(
            state_update["$unset"],
            {
                "original_load_factor": "",
                "original_load_factor_captured_at": "",
            },
        )

    async def test_disabling_quota_strategy_keeps_cooldown_in_normal_queue(self) -> None:
        detected_at = self.now - timedelta(minutes=31)
        db = self.db(
            states=[
                {
                    "remote_account_id": 7,
                    "mode": "rate_limited_cooldown",
                    "rate_limit_detected_at": detected_at,
                }
            ]
        )
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={
                    "id": 7,
                    "priority": 191,
                    "concurrency": 100,
                    "group_ids": [3],
                }
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [7], "failed_ids": []}
            ),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7, priority=191, concurrency=100, used=95)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [7],
            {
                "priority": 200,
                "concurrency": 30,
                "load_factor": 10,
                "group_ids": [3],
            },
        )
        self.assertEqual(result["changed"], 1)
        state = (
            db.sub2api_smart_scheduling_states.update_one.await_args.args[1]["$set"]
        )
        self.assertEqual(state["mode"], "rate_limited_cooldown")
        self.assertEqual(state["queue_partition"], "temporarily_unusable")
        self.assertEqual(state["queue_priority"], 200)

    async def test_failed_delayed_recovery_does_not_advance_cooldown_state(self) -> None:
        detected_at = self.now - timedelta(minutes=31)
        db = self.db(
            states=[
                {
                    "remote_account_id": 7,
                    "mode": "rate_limit_pending",
                    "rate_limit_detected_at": detected_at,
                }
            ]
        )
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={
                    "id": 7,
                    "priority": 10,
                    "concurrency": 100,
                    "group_ids": [3],
                }
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [], "failed_ids": [7]}
            ),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7, priority=10, concurrency=100, used=95)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(result["failed"], 1)
        db.sub2api_smart_scheduling_states.update_one.assert_not_awaited()

    async def test_extreme_update_captures_latest_load_factor_before_bulk(self) -> None:
        events: list[tuple[str, object]] = []
        db = self.db()

        async def record_state_update(
            query: dict[str, object],
            update: dict[str, object],
            **_kwargs: object,
        ) -> SimpleNamespace:
            events.append(("state", {"query": query, "update": update}))
            return SimpleNamespace(matched_count=1, upserted_id=None)

        async def record_bulk(
            account_ids: list[int],
            payload: dict[str, object],
        ) -> dict[str, object]:
            events.append(("bulk", payload))
            return {"success_ids": account_ids, "failed_ids": []}

        db.sub2api_smart_scheduling_states.update_one.side_effect = (
            record_state_update
        )
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={
                    "id": 7,
                    "priority": 250,
                    "concurrency": 30,
                    "load_factor": 7,
                    "group_ids": [3],
                }
            ),
            bulk_update_accounts_runtime=AsyncMock(side_effect=record_bulk),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7, load_factor=3, used=90)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(result["changed"], 1)
        bulk_index = next(
            index for index, (event, _value) in enumerate(events) if event == "bulk"
        )
        capture_updates = [
            value["update"]
            for event, value in events[:bulk_index]
            if event == "state"
            and (
                "original_load_factor" in value["update"].get("$set", {})
                or "original_load_factor"
                in value["update"].get("$setOnInsert", {})
            )
        ]
        self.assertTrue(capture_updates)
        self.assertTrue(
            any(
                update.get("$set", {}).get("original_load_factor") == 7
                or update.get("$setOnInsert", {}).get("original_load_factor") == 7
                for update in capture_updates
            )
        )
        capture_calls = (
            db.sub2api_smart_scheduling_states.update_one.await_args_list[:2]
        )
        self.assertEqual(capture_calls[0].args[0], {"_id": "api-5001:7"})
        self.assertTrue(capture_calls[0].kwargs["upsert"])
        self.assertFalse(capture_calls[1].kwargs["upsert"])
        client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [7],
            {
                "priority": 10,
                "concurrency": 100,
                "load_factor": 10000,
                "group_ids": [3],
            },
        )
        successful_state_update = events[-1][1]["update"]
        self.assertEqual(
            successful_state_update["$set"]["last_target"]["load_factor"],
            10000,
        )
        self.assertNotIn("$unset", successful_state_update)

    async def test_successful_quota_recovery_restores_and_clears_original_load_factor(self) -> None:
        reset_at = self.now + timedelta(days=3)
        db = self.db(
            states=[
                {
                    "remote_account_id": 7,
                    "mode": "extreme",
                    "seven_day_reset_at": reset_at.isoformat(),
                    "original_load_factor": 7,
                    "original_load_factor_captured_at": self.now
                    - timedelta(hours=1),
                }
            ]
        )
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={
                    "id": 7,
                    "priority": 10,
                    "concurrency": 100,
                    "load_factor": 10000,
                    "group_ids": [3],
                }
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [7], "failed_ids": []}
            ),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[
                self.account(
                    7,
                    priority=10,
                    concurrency=100,
                    load_factor=10000,
                    used=79.9,
                )
            ],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(result["changed"], 1)
        client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [7],
            {
                "priority": 200,
                "concurrency": 30,
                "load_factor": 7,
                "group_ids": [3],
            },
        )
        state_update = (
            db.sub2api_smart_scheduling_states.update_one.await_args.args[1]
        )
        self.assertEqual(
            state_update["$unset"],
            {
                "original_load_factor": "",
                "original_load_factor_captured_at": "",
            },
        )

    async def test_failed_first_extreme_update_recovers_capture_on_next_run(self) -> None:
        first_db = self.db()
        first_client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={
                    "id": 7,
                    "priority": 250,
                    "concurrency": 30,
                    "load_factor": 7,
                    "group_ids": [3],
                }
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [], "failed_ids": [7]}
            ),
        )

        first = await run_smart_scheduling(
            first_db,
            site=self.site(),
            accounts=[self.account(7, load_factor=7, used=90)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=first_client,
            now=self.now,
        )

        self.assertEqual(first["failed"], 1)
        captured_state: dict[str, object] = {}
        for call in (
            first_db.sub2api_smart_scheduling_states.update_one.await_args_list
        ):
            update = call.args[1]
            captured_state.update(update.get("$setOnInsert", {}))
            captured_state.update(update.get("$set", {}))
        self.assertEqual(captured_state["original_load_factor"], 7)
        self.assertEqual(captured_state["mode"], "extreme")
        self.assertEqual(
            captured_state["seven_day_reset_at"],
            (self.now + timedelta(days=3)).isoformat(),
        )

        second_db = self.db(states=[captured_state])
        second_client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={
                    "id": 7,
                    "priority": 250,
                    "concurrency": 30,
                    "load_factor": 7,
                    "group_ids": [3],
                }
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [7], "failed_ids": []}
            ),
        )

        second = await run_smart_scheduling(
            second_db,
            site=self.site(),
            accounts=[
                self.account(
                    7,
                    priority=250,
                    concurrency=30,
                    load_factor=7,
                    used=79.9,
                )
            ],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-2",
            rules=self.rules,
            client=second_client,
            now=self.now + timedelta(minutes=1),
        )

        self.assertEqual(second["changed"], 1)
        second_client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [7],
            {
                "priority": 200,
                "concurrency": 30,
                "load_factor": 7,
                "group_ids": [3],
            },
        )
        recovery_update = (
            second_db.sub2api_smart_scheduling_states.update_one.await_args.args[1]
        )
        self.assertEqual(
            recovery_update["$unset"],
            {
                "original_load_factor": "",
                "original_load_factor_captured_at": "",
            },
        )

    async def test_unchanged_recovery_clears_saved_original_load_factor(self) -> None:
        reset_at = self.now + timedelta(days=3)
        db = self.db(
            states=[
                {
                    "remote_account_id": 7,
                    "mode": "extreme",
                    "seven_day_reset_at": reset_at.isoformat(),
                    "original_load_factor": 7,
                    "original_load_factor_captured_at": self.now,
                }
            ]
        )
        client = SimpleNamespace(
            get_account=AsyncMock(),
            bulk_update_accounts_runtime=AsyncMock(),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[
                self.account(
                    7,
                    priority=200,
                    concurrency=30,
                    load_factor=7,
                    used=79.9,
                )
            ],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(result["unchanged"], 1)
        client.get_account.assert_not_awaited()
        client.bulk_update_accounts_runtime.assert_not_awaited()
        state_update = (
            db.sub2api_smart_scheduling_states.update_one.await_args.args[1]
        )
        self.assertEqual(
            state_update["$unset"],
            {
                "original_load_factor": "",
                "original_load_factor_captured_at": "",
            },
        )

    async def test_partial_recovery_failure_keeps_failed_account_original_load_factor(self) -> None:
        detected_at = self.now - timedelta(minutes=31)
        db = self.db(
            states=[
                {
                    "remote_account_id": remote_id,
                    "mode": "rate_limit_pending",
                    "rate_limit_detected_at": detected_at,
                    "original_load_factor": 7,
                    "original_load_factor_captured_at": self.now,
                }
                for remote_id in (7, 8)
            ]
        )
        client = SimpleNamespace(
            get_account=AsyncMock(
                side_effect=[
                    {
                        "id": remote_id,
                        "priority": 10,
                        "concurrency": 100,
                        "load_factor": 10000,
                        "group_ids": [3],
                    }
                    for remote_id in (7, 8)
                ]
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={"success_ids": [7], "failed_ids": [8]}
            ),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[
                self.account(
                    remote_id,
                    priority=10,
                    concurrency=100,
                    load_factor=10000,
                    used=95,
                )
                for remote_id in (7, 8)
            ],
            group_settings={
                3: {
                    "type_priority_enabled": False,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["failed"], 1)
        client.bulk_update_accounts_runtime.assert_awaited_once_with(
            [7, 8],
            {
                "priority": 191,
                "concurrency": 30,
                "load_factor": 7,
                "group_ids": [3],
            },
        )
        state_calls = db.sub2api_smart_scheduling_states.update_one.await_args_list
        self.assertEqual(len(state_calls), 1)
        self.assertEqual(state_calls[0].args[0]["_id"], "api-5001:7")
        self.assertIn("$unset", state_calls[0].args[1])

    async def test_successful_extreme_update_persists_scheduler_state(self) -> None:
        db = self.db(
            states=[
                {
                    "remote_account_id": 7,
                    "mode": "normal",
                    "last_target": {"priority": 250, "concurrency": 30},
                }
            ]
        )
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 30}
            ),
            bulk_update_accounts_runtime=AsyncMock(
                return_value={
                    "success": 1,
                    "failed": 0,
                    "success_ids": [7],
                    "failed_ids": [],
                }
            ),
        )

        await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7, used=90)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        state_update = (
            db.sub2api_smart_scheduling_states.update_one.await_args.args[1]["$set"]
        )
        self.assertEqual(state_update["mode"], "extreme")
        self.assertEqual(
            state_update["last_target"],
            {"priority": 10, "concurrency": 100, "load_factor": 10000},
        )
        self.assertEqual(state_update["adapted_type"], "plus")
        self.assertEqual(state_update["last_probe_run_id"], "probe-1")
        state_call = db.sub2api_smart_scheduling_states.update_one.await_args
        self.assertTrue(state_call.kwargs["upsert"])
        self.assertEqual(
            state_call.args[1]["$setOnInsert"]["created_at"],
            self.now,
        )

        run_document = db.sub2api_smart_scheduling_runs.insert_one.await_args.args[0]
        self.assertEqual(run_document["expires_at"], self.now + timedelta(days=90))
        outcome_call = db.sub2api_smart_scheduling_outcomes.update_one.await_args
        self.assertTrue(outcome_call.kwargs["upsert"])
        self.assertEqual(
            outcome_call.args[1]["$set"]["event_type"],
            "state_transition",
        )
        self.assertEqual(
            outcome_call.args[1]["$set"]["expires_at"],
            self.now + timedelta(days=7),
        )

    async def test_active_lease_conflict_skips_the_runner(self) -> None:
        db = self.db(lease_document={"owner": "other-worker"})
        client = SimpleNamespace(
            get_account=AsyncMock(),
            bulk_update_accounts_runtime=AsyncMock(),
        )

        result = await run_smart_scheduling(
            db,
            site=self.site(),
            accounts=[self.account(7)],
            group_settings={
                3: {
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": False,
                }
            },
            probe_run_id="probe-1",
            rules=self.rules,
            client=client,
            now=self.now,
        )

        self.assertEqual(result["status"], "locked")
        db.sub2api_smart_scheduling_runs.insert_one.assert_not_awaited()
        db.operation_locks.delete_one.assert_not_awaited()
        client.get_account.assert_not_awaited()
        client.bulk_update_accounts_runtime.assert_not_awaited()

    async def test_lease_loss_stops_before_the_next_remote_write(self) -> None:
        db = self.db()
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 20}
            ),
            bulk_update_accounts_runtime=AsyncMock(),
        )

        with (
            patch.object(
                smart_scheduling_service,
                "monotonic",
                side_effect=[0.0, 151.0],
                create=True,
            ),
            patch.object(
                smart_scheduling_service,
                "renew_smart_scheduling_lease",
                AsyncMock(return_value=False),
                create=True,
            ) as renew,
        ):
            result = await run_smart_scheduling(
                db,
                site=self.site(),
                accounts=[self.account(7)],
                group_settings={
                    3: {
                        "type_priority_enabled": True,
                        "quota_acceleration_enabled": False,
                    }
                },
                probe_run_id="probe-1",
                rules=self.rules,
                client=client,
                now=self.now,
            )

        renew.assert_awaited_once()
        client.get_account.assert_not_awaited()
        client.bulk_update_accounts_runtime.assert_not_awaited()
        self.assertEqual(result["failed"], 1)
        outcome = db.sub2api_smart_scheduling_outcomes.update_one.await_args.args[1]["$set"]
        self.assertEqual(outcome["event_type"], "remote_update_failed")
        self.assertEqual(outcome["error_code"], "scheduling_lease_lost")

    async def test_lease_release_failure_does_not_mask_run_result(self) -> None:
        db = self.db()
        db.operation_locks.delete_one.side_effect = RuntimeError(
            "access_token=release-secret"
        )

        with self.assertLogs(
            "app.sub2api_smart_scheduling",
            level="ERROR",
        ) as captured_logs:
            result = await run_smart_scheduling(
                db,
                site=self.site(),
                accounts=[self.account(7, account_type="free")],
                group_settings={
                    3: {
                        "type_priority_enabled": True,
                        "quota_acceleration_enabled": False,
                    }
                },
                probe_run_id="probe-1",
                rules=self.rules,
                now=self.now,
            )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["skipped"], 1)
        db.sub2api_smart_scheduling_runs.update_one.assert_awaited_once()
        db.operation_locks.delete_one.assert_awaited_once()
        self.assertNotIn("release-secret", "\n".join(captured_logs.output))


class SmartSchedulingLeaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_lease_expires_after_five_minutes(self) -> None:
        now = datetime(2026, 7, 27, 7, 0, tzinfo=UTC)

        async def acquire(
            _query: dict[str, object],
            update: dict[str, dict[str, object]],
            **_kwargs: object,
        ) -> dict[str, object]:
            return {"owner": update["$set"]["owner"]}

        db = SimpleNamespace(
            operation_locks=SimpleNamespace(
                find_one_and_update=AsyncMock(side_effect=acquire)
            )
        )

        self.assertTrue(
            await acquire_smart_scheduling_lease(
                db,
                site_id="api-5001",
                owner="worker-a",
                now=now,
            )
        )

        update = db.operation_locks.find_one_and_update.await_args.args[1]
        self.assertEqual(update["$set"]["expires_at"], now + timedelta(minutes=5))

    async def test_active_owner_rejects_lease(self) -> None:
        db = SimpleNamespace(
            operation_locks=SimpleNamespace(
                find_one_and_update=AsyncMock(return_value={"owner": "worker-b"})
            )
        )
        now = datetime(2026, 7, 27, 7, 0, tzinfo=UTC)

        acquired = await acquire_smart_scheduling_lease(
            db,
            site_id="api-5001",
            owner="worker-a",
            now=now,
        )

        self.assertFalse(acquired)
        query = db.operation_locks.find_one_and_update.await_args.args[0]
        self.assertEqual(query["_id"], "smart-scheduling:api-5001")
        self.assertIn("$or", query)

    async def test_renewal_requires_the_unexpired_current_owner(self) -> None:
        now = datetime(2026, 7, 27, 7, 2, 31, tzinfo=UTC)
        update_one = AsyncMock(
            return_value=SimpleNamespace(matched_count=0)
        )
        db = SimpleNamespace(
            operation_locks=SimpleNamespace(update_one=update_one)
        )

        renewed = await smart_scheduling_service.renew_smart_scheduling_lease(
            db,
            site_id="api-5001",
            owner="worker-a",
            now=now,
        )

        self.assertFalse(renewed)
        query = update_one.await_args.args[0]
        self.assertEqual(
            query,
            {
                "_id": "smart-scheduling:api-5001",
                "owner": "worker-a",
                "expires_at": {"$gt": now},
            },
        )

    async def test_release_only_deletes_the_current_owner(self) -> None:
        db = SimpleNamespace(
            operation_locks=SimpleNamespace(delete_one=AsyncMock())
        )

        await release_smart_scheduling_lease(
            db,
            site_id="api-5001",
            owner="worker-a",
        )

        db.operation_locks.delete_one.assert_awaited_once_with(
            {
                "_id": "smart-scheduling:api-5001",
                "owner": "worker-a",
            }
        )


if __name__ == "__main__":
    unittest.main()
