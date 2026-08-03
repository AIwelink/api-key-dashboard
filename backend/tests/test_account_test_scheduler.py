import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch


from app.modules.sub2api.account_test_scheduler import (
    load_due_account,
    run_account_test_cycle,
    select_due_account,
)
from app.modules.sub2api.client import InvalidAdminApiKeyError


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


class AsyncCursor:
    def __init__(self, documents: list[dict]) -> None:
        self.documents = documents

    def sort(self, *_args, **_kwargs):
        return self

    def __aiter__(self):
        self.iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self.iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class DueAccountSelectionTests(unittest.TestCase):
    def test_due_snapshot_403_precedes_never_tested_and_normal_due(self) -> None:
        accounts = [
            {"site_id": "site-a", "sub2api_account_id": 10},
            {
                "site_id": "site-a",
                "sub2api_account_id": 11,
                "fetched_at": NOW,
                "account": {"error_message": "API returned 403"},
            },
            {"site_id": "site-a", "sub2api_account_id": 12},
        ]
        states = {
            "site-a:11": {
                "last_tested_at": NOW - timedelta(minutes=4),
                "next_test_at": NOW + timedelta(hours=20),
            },
            "site-a:12": {"next_test_at": NOW - timedelta(hours=1)},
        }

        selected = select_due_account(
            [{"_id": "site-a"}],
            accounts,
            states,
            now=NOW,
        )

        self.assertEqual(selected["account"]["sub2api_account_id"], 11)

    def test_latest_model_403_uses_three_minute_deadline(self) -> None:
        selected = select_due_account(
            [{"_id": "site-a"}],
            [{"site_id": "site-a", "sub2api_account_id": 11}],
            {
                "site-a:11": {
                    "last_http_status": 403,
                    "last_tested_at": NOW - timedelta(minutes=4),
                    "next_test_at": NOW + timedelta(hours=20),
                }
            },
            now=NOW,
        )

        self.assertIsNotNone(selected)
        self.assertEqual(selected["account"]["sub2api_account_id"], 11)

    def test_recovered_account_ignores_older_cached_403(self) -> None:
        selected = select_due_account(
            [{"_id": "site-a"}],
            [
                {
                    "site_id": "site-a",
                    "sub2api_account_id": 11,
                    "fetched_at": NOW - timedelta(seconds=30),
                    "account": {"error_message": "API returned 403"},
                }
            ],
            {
                "site-a:11": {
                    "last_tested_at": NOW - timedelta(minutes=1),
                    "next_test_at": NOW + timedelta(hours=23),
                    "recovery_completed_at": NOW,
                }
            },
            now=NOW,
        )

        self.assertIsNone(selected)

    def test_never_tested_accounts_precede_oldest_due_and_include_disabled(self) -> None:
        sites = [
            {"_id": "site-b", "status": "active"},
            {"_id": "site-a", "status": "active"},
        ]
        accounts = [
            {"site_id": "site-a", "sub2api_account_id": 10, "schedulable": True},
            {"site_id": "site-a", "sub2api_account_id": 11, "schedulable": True},
            {"site_id": "site-b", "sub2api_account_id": 12, "schedulable": False},
        ]
        states = {
            "site-a:10": {"next_test_at": NOW - timedelta(hours=2)},
            "site-a:11": {"next_test_at": NOW + timedelta(hours=1)},
        }

        selected = select_due_account(sites, accounts, states, now=NOW)

        self.assertIsNotNone(selected)
        self.assertEqual(selected["account"]["sub2api_account_id"], 12)
        self.assertFalse(selected["account"]["schedulable"])

        selected = select_due_account(
            sites,
            [account for account in accounts if account["sub2api_account_id"] != 12],
            states,
            now=NOW,
        )
        self.assertEqual(selected["account"]["sub2api_account_id"], 10)

    def test_recent_accounts_are_not_due(self) -> None:
        selected = select_due_account(
            [{"_id": "site-a"}],
            [{"site_id": "site-a", "sub2api_account_id": 10}],
            {"site-a:10": {"next_test_at": NOW + timedelta(minutes=1)}},
            now=NOW,
        )
        self.assertIsNone(selected)


class SchedulerCycleTests(unittest.IsolatedAsyncioTestCase):
    def _db(self) -> SimpleNamespace:
        return SimpleNamespace(
            sub2api_account_test_site_meta=SimpleNamespace(update_one=AsyncMock()),
            sub2api_account_test_events=SimpleNamespace(insert_one=AsyncMock()),
        )

    async def test_load_due_account_reads_403_fields_from_account_cache(self) -> None:
        account_find = MagicMock(return_value=AsyncCursor([]))
        db = SimpleNamespace(
            sub2api_sites=SimpleNamespace(
                find=MagicMock(return_value=AsyncCursor([{"_id": "site-a"}]))
            ),
            sub2api_accounts_cache=SimpleNamespace(find=account_find),
            sub2api_account_test_states=SimpleNamespace(
                find=MagicMock(return_value=AsyncCursor([]))
            ),
            sub2api_account_test_site_meta=SimpleNamespace(
                find=MagicMock(return_value=AsyncCursor([]))
            ),
        )

        result = await load_due_account(db, now=NOW)

        self.assertIsNone(result)
        projection = account_find.call_args.args[1]
        self.assertEqual(projection["status"], 1)
        self.assertEqual(projection["account.error_message"], 1)
        self.assertEqual(projection["account.schedulable"], 1)
        self.assertEqual(projection["fetched_at"], 1)

    async def test_cycle_executes_only_one_remote_test(self) -> None:
        db = self._db()
        due = {
            "site": {"_id": "site-a", "base_url": "http://example.test"},
            "account": {"site_id": "site-a", "sub2api_account_id": 10},
        }
        execute = AsyncMock(return_value={"outcome": "passed"})
        with (
            patch(
                "app.modules.sub2api.account_test_scheduler.acquire_scheduler_lease",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.release_scheduler_lease",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.repair_latest_states_from_events",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.replay_pending_dispatches",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.load_due_account",
                new=AsyncMock(return_value=due),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.build_site_client",
                new=AsyncMock(return_value=SimpleNamespace()),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.execute_account_test",
                new=execute,
            ),
        ):
            result = await run_account_test_cycle(db, now=NOW, owner="worker-1")

        self.assertTrue(result["tested"])
        self.assertEqual(result["remote_account_id"], 10)
        execute.assert_awaited_once()

    async def test_lease_denial_skips_all_work(self) -> None:
        db = self._db()
        with (
            patch(
                "app.modules.sub2api.account_test_scheduler.acquire_scheduler_lease",
                new=AsyncMock(return_value=False),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.load_due_account",
                new=AsyncMock(),
            ) as load_due,
        ):
            result = await run_account_test_cycle(db, now=NOW, owner="worker-2")

        self.assertTrue(result["skipped"])
        self.assertEqual(result["reason"], "lease_unavailable")
        load_due.assert_not_awaited()

    async def test_admin_key_failure_sets_site_backoff_without_account_event(self) -> None:
        db = self._db()
        due = {
            "site": {"_id": "site-a", "base_url": "http://example.test"},
            "account": {"site_id": "site-a", "sub2api_account_id": 10},
        }
        with (
            patch(
                "app.modules.sub2api.account_test_scheduler.acquire_scheduler_lease",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.release_scheduler_lease",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.repair_latest_states_from_events",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.replay_pending_dispatches",
                new=AsyncMock(return_value=0),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.load_due_account",
                new=AsyncMock(return_value=due),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.build_site_client",
                new=AsyncMock(return_value=SimpleNamespace()),
            ),
            patch(
                "app.modules.sub2api.account_test_scheduler.execute_account_test",
                new=AsyncMock(side_effect=InvalidAdminApiKeyError("bad admin key")),
            ),
        ):
            result = await run_account_test_cycle(db, now=NOW, owner="worker-1")

        self.assertFalse(result["tested"])
        self.assertEqual(result["reason"], "admin_auth_error")
        db.sub2api_account_test_events.insert_one.assert_not_awaited()
        update = db.sub2api_account_test_site_meta.update_one.await_args.args[1]["$set"]
        self.assertEqual(update["status"], "admin_auth_error")
        self.assertGreater(update["backoff_until"], NOW)


if __name__ == "__main__":
    unittest.main()
