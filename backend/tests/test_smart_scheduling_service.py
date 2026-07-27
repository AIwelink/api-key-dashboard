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
        used: float = 20,
    ) -> dict[str, object]:
        return {
            "remote_account_id": remote_id,
            "account_type": account_type,
            "priority": priority,
            "concurrency": concurrency,
            "group_ids": group_ids or [3],
            "usage_snapshot": {
                "codex_7d_used_percent": used,
                "codex_7d_reset_at": (self.now + timedelta(days=3)).isoformat(),
                "codex_usage_synced_at": self.now.isoformat(),
            },
        }

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
            update_account_runtime=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 30}
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
        client.update_account_runtime.assert_awaited_once_with(
            7,
            {"priority": 250, "concurrency": 30},
        )
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["changed"], 1)
        self.assertEqual(result["failed"], 0)
        self.assertEqual(
            db.sub2api_smart_scheduling_outcomes.update_one.await_count,
            1,
        )
        release_filter = db.operation_locks.delete_one.await_args.args[0]
        self.assertEqual(release_filter["_id"], "smart-scheduling:api-5001")
        self.assertTrue(release_filter["owner"])

    async def test_multi_group_flags_are_aggregated_by_enabled_strategy(self) -> None:
        account = self.account(7, group_ids=[3], used=90)
        account_from_other_group = dict(account)
        account_from_other_group["group_ids"] = [4]
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 20}
            ),
            update_account_runtime=AsyncMock(),
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

        client.update_account_runtime.assert_awaited_once_with(
            7,
            {"priority": 10, "concurrency": 100},
        )
        self.assertEqual(result["scanned"], 1)
        self.assertEqual(result["changed"], 1)

    async def test_latest_manual_priority_is_revalidated_before_update(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 220, "concurrency": 30}
            ),
            update_account_runtime=AsyncMock(),
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
        client.update_account_runtime.assert_not_awaited()
        self.assertEqual(result["unchanged"], 1)

    async def test_default_off_does_not_acquire_lease_or_call_remote_api(self) -> None:
        db = self.db()
        client = SimpleNamespace(
            get_account=AsyncMock(),
            update_account_runtime=AsyncMock(),
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
        client.update_account_runtime.assert_not_awaited()

    async def test_client_is_built_lazily_for_first_candidate_change(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 20}
            ),
            update_account_runtime=AsyncMock(),
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
        client.update_account_runtime.assert_awaited_once_with(
            7,
            {"priority": 250, "concurrency": 30},
        )
        self.assertEqual(result["changed"], 1)

    async def test_one_account_failure_does_not_stop_the_next_account(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                side_effect=[
                    {"id": 7, "priority": 250, "concurrency": 20},
                    {"id": 8, "priority": 250, "concurrency": 20},
                ]
            ),
            update_account_runtime=AsyncMock(
                side_effect=[
                    RuntimeError("remote body must not be persisted"),
                    {"id": 8, "priority": 250, "concurrency": 30},
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
        self.assertEqual(client.update_account_runtime.await_count, 2)
        first_outcome = (
            db.sub2api_smart_scheduling_outcomes.update_one.await_args_list[0]
            .args[1]["$set"]
        )
        self.assertEqual(first_outcome["status"], "failed")
        self.assertEqual(first_outcome["error_code"], "remote_update_failed")
        self.assertEqual(first_outcome["error_type"], "RuntimeError")
        self.assertEqual(
            {key for key in first_outcome if key.startswith("error")},
            {"error_code", "error_type"},
        )
        self.assertNotIn("remote body", str(first_outcome))

    async def test_admin_auth_failure_stops_later_remote_writes(self) -> None:
        client = SimpleNamespace(
            get_account=AsyncMock(
                side_effect=[
                    InvalidAdminApiKeyError("rejected admin-secret"),
                    {"id": 8, "priority": 250, "concurrency": 20},
                ]
            ),
            update_account_runtime=AsyncMock(),
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
        client.update_account_runtime.assert_not_awaited()
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
            update_account_runtime=AsyncMock(),
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
            update_account_runtime=AsyncMock(),
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
        client.update_account_runtime.assert_not_awaited()

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
                update_account_runtime=AsyncMock(),
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
                "seven_day_reset_at": 1,
            },
        )

    async def test_successful_extreme_update_persists_scheduler_state(self) -> None:
        db = self.db()
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 30}
            ),
            update_account_runtime=AsyncMock(
                return_value={"id": 7, "priority": 10, "concurrency": 100}
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
            {"priority": 10, "concurrency": 100},
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
            outcome_call.args[1]["$set"]["expires_at"],
            self.now + timedelta(days=30),
        )

    async def test_active_lease_conflict_skips_the_runner(self) -> None:
        db = self.db(lease_document={"owner": "other-worker"})
        client = SimpleNamespace(
            get_account=AsyncMock(),
            update_account_runtime=AsyncMock(),
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
        client.update_account_runtime.assert_not_awaited()

    async def test_lease_loss_stops_before_the_next_remote_write(self) -> None:
        db = self.db()
        client = SimpleNamespace(
            get_account=AsyncMock(
                return_value={"id": 7, "priority": 250, "concurrency": 20}
            ),
            update_account_runtime=AsyncMock(),
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
        client.update_account_runtime.assert_not_awaited()
        self.assertEqual(result["failed"], 1)
        outcome = db.sub2api_smart_scheduling_outcomes.update_one.await_args.args[1]["$set"]
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
