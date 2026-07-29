import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock


from app.modules.sub2api.account_test_service import execute_account_test
from app.modules.sub2api.client import InvalidAdminApiKeyError


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _db() -> SimpleNamespace:
    return SimpleNamespace(
        sub2api_account_test_events=SimpleNamespace(insert_one=AsyncMock()),
        sub2api_account_test_states=SimpleNamespace(update_one=AsyncMock()),
        sub2api_accounts_cache=SimpleNamespace(update_one=AsyncMock()),
    )


class AccountTestServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_persists_sanitized_result_before_dispatch(self) -> None:
        db = _db()
        order: list[str] = []
        stored: dict[str, dict] = {}

        async def insert_event(document: dict) -> None:
            order.append("event_inserted")
            stored["event"] = document

        async def update_state(query: dict, update: dict, *, upsert: bool) -> None:
            order.append("state_updated")
            stored["state"] = update["$set"]

        async def update_cache(query: dict, update: dict) -> None:
            order.append("cache_updated")

        async def dispatch(_db: object, event_id: str) -> None:
            order.append("dispatched")
            self.assertEqual(event_id, stored["event"]["_id"])

        db.sub2api_account_test_events.insert_one.side_effect = insert_event
        db.sub2api_account_test_states.update_one.side_effect = update_state
        db.sub2api_accounts_cache.update_one.side_effect = update_cache
        client = SimpleNamespace(
            test_account=AsyncMock(
                return_value={
                    "success": True,
                    "model": "gpt-5.5",
                    "latency_ms": 123,
                    "response_preview": "refresh_token=preview-secret",
                    "credentials": {"access_token": "secret"},
                    "events": [{"access_token": "secret"}],
                }
            )
        )
        account = {
            "sub2api_account_id": 4072,
            "credentials": {"email": " User@Example.com ", "refresh_token": "secret"},
            "name": "account-name",
        }
        site = {"_id": "US06-5001", "token": "admin-secret"}

        result = await execute_account_test(
            db,
            site=site,
            account=account,
            client=client,
            dispatcher=dispatch,
            now=NOW,
        )

        client.test_account.assert_awaited_once_with(
            4072,
            model_id="gpt-5.5",
            prompt="",
            mode="default",
        )
        self.assertLess(order.index("event_inserted"), order.index("dispatched"))
        self.assertLess(order.index("state_updated"), order.index("dispatched"))
        self.assertEqual(result["outcome"], "passed")
        self.assertEqual(stored["event"]["normalized_email"], "user@example.com")
        self.assertEqual(stored["event"]["next_test_at"], NOW + timedelta(hours=24))
        self.assertEqual(stored["event"]["expires_at"], NOW + timedelta(days=90))
        self.assertNotIn("credentials", stored["event"])
        self.assertNotIn("events", stored["event"])
        self.assertNotIn("preview-secret", repr(stored["event"]))
        self.assertNotIn("admin-secret", repr(stored["event"]))
        self.assertEqual(stored["state"]["last_event_id"], stored["event"]["_id"])

    async def test_snapshot_403_success_records_recovery_context_and_rapid_interval(
        self,
    ) -> None:
        db = _db()
        client = SimpleNamespace(
            test_account=AsyncMock(
                return_value={"success": True, "model": "gpt-5.5"}
            )
        )
        dispatcher = AsyncMock()
        fetched_at = NOW - timedelta(seconds=20)

        result = await execute_account_test(
            db,
            site={"_id": "US06-5001"},
            account={
                "sub2api_account_id": 4072,
                "fetched_at": fetched_at,
                "account": {"error_message": "API returned 403"},
            },
            client=client,
            dispatcher=dispatcher,
            now=NOW,
        )

        self.assertEqual(result["next_test_at"], NOW + timedelta(minutes=3))
        self.assertTrue(result["recovery"]["required"])
        self.assertTrue(result["recovery"]["snapshot_http_403"])
        self.assertEqual(result["recovery"]["snapshot_fetched_at"], fetched_at)
        self.assertEqual(
            result["dispatch"]["scheduling"]["recover_state_status"],
            "pending",
        )
        state = db.sub2api_account_test_states.update_one.await_args.args[1]["$set"]
        self.assertTrue(state["last_snapshot_http_403"])
        self.assertEqual(state["interval_mode"], "rapid_403")

    async def test_model_403_uses_rapid_interval_without_starting_recovery(
        self,
    ) -> None:
        db = _db()
        result = await execute_account_test(
            db,
            site={"_id": "US06-5001"},
            account={"sub2api_account_id": 4072},
            client=SimpleNamespace(
                test_account=AsyncMock(
                    return_value={
                        "success": False,
                        "error": "API returned 403",
                    }
                )
            ),
            dispatcher=AsyncMock(),
            now=NOW,
        )

        self.assertEqual(result["http_status"], 403)
        self.assertEqual(result["next_test_at"], NOW + timedelta(minutes=3))
        self.assertFalse(result["recovery"]["required"])
        self.assertEqual(
            result["dispatch"]["scheduling"]["recover_state_status"],
            "not_required",
        )

    async def test_admin_key_failure_does_not_create_account_event(self) -> None:
        db = _db()
        client = SimpleNamespace(
            test_account=AsyncMock(
                side_effect=InvalidAdminApiKeyError("Admin API Key is invalid")
            )
        )

        with self.assertRaises(InvalidAdminApiKeyError):
            await execute_account_test(
                db,
                site={"_id": "US06-5001"},
                account={"sub2api_account_id": 4072},
                client=client,
                dispatcher=AsyncMock(),
                now=NOW,
            )

        db.sub2api_account_test_events.insert_one.assert_not_awaited()
        db.sub2api_account_test_states.update_one.assert_not_awaited()

    async def test_transport_failure_is_saved_and_dispatched(self) -> None:
        db = _db()
        dispatcher = AsyncMock()
        client = SimpleNamespace(
            test_account=AsyncMock(side_effect=TimeoutError("connection timed out"))
        )

        result = await execute_account_test(
            db,
            site={"_id": "US06-5001"},
            account={"sub2api_account_id": 4072},
            client=client,
            dispatcher=dispatcher,
            now=NOW,
        )

        self.assertEqual(result["outcome"], "transport_error")
        self.assertEqual(result["error"], "connection timed out")
        self.assertEqual(result["next_test_at"], NOW + timedelta(hours=24))
        db.sub2api_account_test_events.insert_one.assert_awaited_once()
        dispatcher.assert_awaited_once_with(db, result["_id"])

    async def test_transport_error_redacts_embedded_credentials(self) -> None:
        db = _db()
        client = SimpleNamespace(
            test_account=AsyncMock(
                side_effect=RuntimeError("request failed access_token=transport-secret")
            )
        )

        result = await execute_account_test(
            db,
            site={"_id": "US06-5001"},
            account={"sub2api_account_id": 4072},
            client=client,
            dispatcher=AsyncMock(),
            now=NOW,
        )

        self.assertNotIn("transport-secret", result["error"])
        self.assertIn("***", result["error"])


if __name__ == "__main__":
    unittest.main()
