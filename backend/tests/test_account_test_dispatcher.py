import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


from app.modules.sub2api.account_test_dispatcher import (
    dispatch_test_event,
    handle_plan_correction,
    handle_scheduling,
)


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _event(outcome: str) -> dict:
    return {
        "_id": "event-1",
        "state_id": "US06-5001:4072",
        "site_id": "US06-5001",
        "remote_account_id": 4072,
        "outcome": outcome,
        "tested_at": NOW,
        "dispatch": {
            "scheduling": {"status": "pending"},
            "plan_correction": {"status": "pending"},
        },
    }


def _db(account: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        sub2api_accounts_cache=SimpleNamespace(
            find_one=AsyncMock(return_value=account),
            update_one=AsyncMock(),
        ),
        sub2api_account_test_states=SimpleNamespace(
            find_one=AsyncMock(return_value={"last_event_id": "event-1"}),
            update_one=AsyncMock(),
        ),
        sub2api_account_test_events=SimpleNamespace(
            find_one=AsyncMock(),
            update_one=AsyncMock(return_value=SimpleNamespace(modified_count=1)),
            find=AsyncMock(),
        ),
    )


class SchedulingHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_passed_result_reenables_disabled_account(self) -> None:
        db = _db({"schedulable": False, "account": {"schedulable": False}})
        client = SimpleNamespace(
            set_account_schedulable=AsyncMock(return_value={"id": 4072, "schedulable": True}),
            update_account=AsyncMock(),
        )

        await handle_scheduling(
            db,
            _event("passed"),
            site={"_id": "US06-5001"},
            client=client,
        )

        client.set_account_schedulable.assert_awaited_once_with(4072, True)
        client.update_account.assert_not_awaited()
        update = db.sub2api_accounts_cache.update_one.await_args.args[1]["$set"]
        self.assertEqual(update, {"schedulable": True, "account.schedulable": True})

    async def test_confirmed_account_failures_do_not_auto_disable_while_disabled(self) -> None:
        for outcome in ("unauthorized", "payment_required", "inactive_owner"):
            with self.subTest(outcome=outcome):
                db = _db({"schedulable": True})
                client = SimpleNamespace(set_account_schedulable=AsyncMock(return_value={}))
                await handle_scheduling(db, _event(outcome), site={}, client=client)
                client.set_account_schedulable.assert_not_awaited()

    async def test_rate_limit_and_unconfirmed_failures_do_not_change_scheduling(self) -> None:
        for outcome in (
            "rate_limited",
            "forbidden_other",
            "model_not_supported",
            "failed",
            "transport_error",
        ):
            with self.subTest(outcome=outcome):
                db = _db({"schedulable": True})
                client = SimpleNamespace(set_account_schedulable=AsyncMock())
                await handle_scheduling(db, _event(outcome), site={}, client=client)
                client.set_account_schedulable.assert_not_awaited()

    async def test_stale_event_cannot_override_newer_scheduling_judgment(self) -> None:
        db = _db({"schedulable": False})
        db.sub2api_account_test_states.find_one.return_value = {
            "last_event_id": "newer-event"
        }
        client = SimpleNamespace(set_account_schedulable=AsyncMock())

        await handle_scheduling(db, _event("passed"), site={}, client=client)

        client.set_account_schedulable.assert_not_awaited()

    async def test_removed_account_does_not_retry_remote_scheduling(self) -> None:
        db = _db(None)
        client = SimpleNamespace(set_account_schedulable=AsyncMock())

        with patch(
            "app.modules.sub2api.account_test_dispatcher.AUTO_DISABLE_CONFIRMED_FAILURES",
            True,
        ):
            await handle_scheduling(db, _event("unauthorized"), site={}, client=client)

        client.set_account_schedulable.assert_not_awaited()


class PlanCorrectionHandlerTests(unittest.IsolatedAsyncioTestCase):
    def _candidate(self) -> dict:
        return {
            "credentials": {"plan_type": "free"},
            "extra": {
                "source": "sub_bundle_input",
                "codex_5h_window_minutes": 0,
                "codex_7d_window_minutes": 10080,
            },
            "groups": [{"id": 3, "name": "plus 账号池 01"}],
        }

    async def test_only_supported_results_verify_candidate_as_plus(self) -> None:
        for outcome in ("passed", "rate_limited"):
            with self.subTest(outcome=outcome):
                db = _db(self._candidate())
                await handle_plan_correction(db, _event(outcome))
                update = db.sub2api_account_test_states.update_one.await_args.args[1]
                self.assertEqual(update["$set"]["verified_plan_type"], "plus")
                self.assertEqual(update["$set"]["verified_plan_type_source"], "gpt-5.4")

    async def test_cached_wrapper_uses_nested_remote_account_signature(self) -> None:
        db = _db(
            {
                "site_id": "US06-5001",
                "sub2api_account_id": 4072,
                "account": self._candidate(),
            }
        )

        await handle_plan_correction(db, _event("passed"))

        update = db.sub2api_account_test_states.update_one.await_args.args[1]
        self.assertEqual(update["$set"]["verified_plan_type"], "plus")

    async def test_model_not_supported_clears_existing_correction(self) -> None:
        db = _db(self._candidate())
        await handle_plan_correction(db, _event("model_not_supported"))
        update = db.sub2api_account_test_states.update_one.await_args.args[1]
        self.assertIn("verified_plan_type", update["$unset"])
        self.assertIn("verified_plan_type_source", update["$unset"])

    async def test_other_failures_preserve_existing_correction(self) -> None:
        db = _db(self._candidate())
        await handle_plan_correction(db, _event("unauthorized"))
        db.sub2api_account_test_states.update_one.assert_not_awaited()

    async def test_stale_event_cannot_clear_newer_plan_verification(self) -> None:
        db = _db(self._candidate())
        db.sub2api_account_test_states.find_one.return_value = {
            "last_event_id": "newer-event",
            "verified_plan_type": "plus",
        }

        await handle_plan_correction(db, _event("model_not_supported"))

        db.sub2api_account_test_states.update_one.assert_not_awaited()


class DispatcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatcher_marks_each_claimed_handler_completed(self) -> None:
        db = _db()
        db.sub2api_account_test_events.find_one.return_value = _event("passed")
        scheduling = AsyncMock()
        plan_correction = AsyncMock()
        handlers = {"scheduling": scheduling, "plan_correction": plan_correction}

        with (
            patch(
                "app.modules.sub2api.account_test_dispatcher.HANDLERS",
                handlers,
            ),
            patch(
                "app.modules.sub2api.account_test_dispatcher._claim_handler",
                new=AsyncMock(return_value=True),
            ),
        ):
            result = await dispatch_test_event(db, "event-1")

        scheduling.assert_awaited_once_with(db, db.sub2api_account_test_events.find_one.return_value)
        plan_correction.assert_awaited_once()
        self.assertEqual(result["completed"], 2)
        updates = [call.args[1]["$set"] for call in db.sub2api_account_test_events.update_one.await_args_list]
        self.assertTrue(any(update.get("dispatch.scheduling.status") == "completed" for update in updates))
        self.assertTrue(any(update.get("dispatch.plan_correction.status") == "completed" for update in updates))

    async def test_dispatcher_records_failure_without_raising_or_retesting(self) -> None:
        db = _db()
        db.sub2api_account_test_events.find_one.return_value = _event("passed")
        handler = AsyncMock(side_effect=RuntimeError("temporary failure"))
        with (
            patch(
                "app.modules.sub2api.account_test_dispatcher.HANDLERS",
                {"scheduling": handler},
            ),
            patch(
                "app.modules.sub2api.account_test_dispatcher._claim_handler",
                new=AsyncMock(return_value=True),
            ),
        ):
            result = await dispatch_test_event(db, "event-1")

        self.assertEqual(result["failed"], 1)
        update = db.sub2api_account_test_events.update_one.await_args.args[1]["$set"]
        self.assertEqual(update["dispatch.scheduling.status"], "failed")
        self.assertIn("temporary failure", update["dispatch.scheduling.last_error"])


if __name__ == "__main__":
    unittest.main()
