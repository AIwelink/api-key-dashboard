import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch


from app.modules.sub2api.account_test_dispatcher import (
    dispatch_test_event,
    handle_plan_correction,
    handle_scheduling,
)


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=UTC)


def _event(
    outcome: str,
    *,
    recovery_required: bool = False,
    recover_state_status: str = "pending",
) -> dict:
    return {
        "_id": "event-1",
        "state_id": "US06-5001:4072",
        "site_id": "US06-5001",
        "remote_account_id": 4072,
        "model": "gpt-5.5",
        "outcome": outcome,
        "tested_at": NOW,
        "recovery": {
            "required": recovery_required,
            "snapshot_http_403": recovery_required,
            "snapshot_fetched_at": NOW,
        },
        "dispatch": {
            "scheduling": {
                "status": "pending",
                "attempts": 0,
                "recover_state_status": (
                    recover_state_status if recovery_required else "not_required"
                ),
                "recover_state_attempts": 0,
                "enable_schedulable_status": (
                    "pending" if recovery_required else "not_required"
                ),
                "enable_schedulable_attempts": 0,
            },
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
            recover_account_state=AsyncMock(),
            update_account=AsyncMock(),
        )

        await handle_scheduling(
            db,
            _event("passed"),
            site={"_id": "US06-5001"},
            client=client,
        )

        client.set_account_schedulable.assert_awaited_once_with(4072, True)
        client.recover_account_state.assert_not_awaited()
        client.update_account.assert_not_awaited()
        update = db.sub2api_accounts_cache.update_one.await_args.args[1]["$set"]
        self.assertEqual(update, {"schedulable": True, "account.schedulable": True})

    async def test_snapshot_403_success_recovers_before_enabling(self) -> None:
        order: list[str] = []
        db = _db(
            {
                "fetched_at": NOW,
                "schedulable": False,
                "account": {
                    "schedulable": False,
                    "error_message": "API returned 403",
                },
            }
        )

        async def recover(_account_id: int) -> dict:
            order.append("recover")
            return {
                "status": "active",
                "error_message": "",
                "schedulable": False,
            }

        async def enable(_account_id: int, _desired: bool) -> dict:
            order.append("enable")
            return {"schedulable": True}

        client = SimpleNamespace(
            recover_account_state=AsyncMock(side_effect=recover),
            set_account_schedulable=AsyncMock(side_effect=enable),
        )

        await handle_scheduling(
            db,
            _event("passed", recovery_required=True),
            site={"_id": "US06-5001"},
            client=client,
        )

        self.assertEqual(order, ["recover", "enable"])
        state_updates = [
            call.args[1]["$set"]
            for call in db.sub2api_account_test_states.update_one.await_args_list
            if "$set" in call.args[1]
        ]
        self.assertTrue(
            any(
                update.get("next_test_at") == NOW + timedelta(hours=24)
                for update in state_updates
            )
        )
        cache_update = db.sub2api_accounts_cache.update_one.await_args.args[1]["$set"]
        self.assertEqual(cache_update["status"], "active")
        self.assertEqual(cache_update["account.error_message"], "")
        self.assertTrue(cache_update["schedulable"])

    async def test_recover_failure_does_not_enable_scheduling(self) -> None:
        db = _db({"schedulable": False})
        client = SimpleNamespace(
            recover_account_state=AsyncMock(
                side_effect=RuntimeError("access_token=recover-secret")
            ),
            set_account_schedulable=AsyncMock(),
        )

        with (
            patch(
                "app.modules.sub2api.account_test_dispatcher.logger.warning"
            ) as warning,
            self.assertRaises(RuntimeError),
        ):
            await handle_scheduling(
                db,
                _event("passed", recovery_required=True),
                client=client,
            )

        client.set_account_schedulable.assert_not_awaited()
        phase_updates = [
            call.args[1]["$set"]
            for call in db.sub2api_account_test_events.update_one.await_args_list
            if "$set" in call.args[1]
        ]
        self.assertTrue(
            any(
                update.get("dispatch.scheduling.recover_state_status") == "failed"
                for update in phase_updates
            )
        )
        self.assertNotIn("recover-secret", repr(phase_updates))
        warning.assert_called_once()
        self.assertNotIn("recover-secret", repr(warning.call_args))

    async def test_recover_cache_failure_does_not_repeat_remote_recovery(self) -> None:
        db = _db({"schedulable": False})
        db.sub2api_accounts_cache.update_one.side_effect = RuntimeError(
            "access_token=cache-secret"
        )
        client = SimpleNamespace(
            recover_account_state=AsyncMock(
                return_value={"status": "active", "schedulable": False}
            ),
            set_account_schedulable=AsyncMock(),
        )

        with self.assertRaises(RuntimeError):
            await handle_scheduling(
                db,
                _event("passed", recovery_required=True),
                client=client,
            )

        phase_updates = [
            call.args[1]["$set"]
            for call in db.sub2api_account_test_events.update_one.await_args_list
            if "$set" in call.args[1]
        ]
        self.assertTrue(
            any(
                update.get("dispatch.scheduling.recover_state_status") == "completed"
                for update in phase_updates
            )
        )
        self.assertFalse(
            any(
                update.get("dispatch.scheduling.recover_state_status") == "failed"
                for update in phase_updates
            )
        )
        self.assertNotIn("cache-secret", repr(phase_updates))
        client.set_account_schedulable.assert_not_awaited()

        db.sub2api_accounts_cache.update_one.side_effect = None
        replay_client = SimpleNamespace(
            recover_account_state=AsyncMock(),
            set_account_schedulable=AsyncMock(return_value={"schedulable": True}),
        )
        await handle_scheduling(
            db,
            _event(
                "passed",
                recovery_required=True,
                recover_state_status="completed",
            ),
            client=replay_client,
        )
        replay_client.recover_account_state.assert_not_awaited()
        replay_client.set_account_schedulable.assert_awaited_once_with(4072, True)

    async def test_enable_failure_preserves_completed_recovery_phase(self) -> None:
        db = _db({"schedulable": False})
        client = SimpleNamespace(
            recover_account_state=AsyncMock(
                return_value={"status": "active", "schedulable": False}
            ),
            set_account_schedulable=AsyncMock(
                side_effect=RuntimeError("access_token=enable-secret")
            ),
        )

        with self.assertRaises(RuntimeError):
            await handle_scheduling(
                db,
                _event("passed", recovery_required=True),
                client=client,
            )

        phase_updates = [
            call.args[1]["$set"]
            for call in db.sub2api_account_test_events.update_one.await_args_list
            if "$set" in call.args[1]
        ]
        self.assertTrue(
            any(
                update.get("dispatch.scheduling.recover_state_status") == "completed"
                for update in phase_updates
            )
        )
        self.assertTrue(
            any(
                update.get("dispatch.scheduling.enable_schedulable_status") == "failed"
                for update in phase_updates
            )
        )
        self.assertNotIn("enable-secret", repr(phase_updates))

    async def test_replay_skips_completed_recover_phase(self) -> None:
        db = _db({"schedulable": False})
        client = SimpleNamespace(
            recover_account_state=AsyncMock(),
            set_account_schedulable=AsyncMock(return_value={}),
        )

        await handle_scheduling(
            db,
            _event(
                "passed",
                recovery_required=True,
                recover_state_status="completed",
            ),
            client=client,
        )

        client.recover_account_state.assert_not_awaited()
        client.set_account_schedulable.assert_awaited_once_with(4072, True)

    async def test_newer_event_after_recover_prevents_scheduling_enable(self) -> None:
        db = _db({"schedulable": False})
        db.sub2api_account_test_states.find_one.side_effect = [
            {"last_event_id": "event-1"},
            {"last_event_id": "event-1"},
            {"last_event_id": "newer-event"},
        ]
        client = SimpleNamespace(
            recover_account_state=AsyncMock(return_value={"status": "active"}),
            set_account_schedulable=AsyncMock(),
        )

        await handle_scheduling(
            db,
            _event("passed", recovery_required=True),
            client=client,
        )

        client.recover_account_state.assert_awaited_once_with(4072)
        client.set_account_schedulable.assert_not_awaited()

    async def test_newer_event_after_phase_start_prevents_remote_recovery(self) -> None:
        db = _db({"schedulable": False})
        db.sub2api_account_test_states.find_one.side_effect = [
            {"last_event_id": "event-1"},
            {"last_event_id": "newer-event"},
        ]
        client = SimpleNamespace(
            recover_account_state=AsyncMock(),
            set_account_schedulable=AsyncMock(),
        )

        await handle_scheduling(
            db,
            _event("passed", recovery_required=True),
            client=client,
        )

        client.recover_account_state.assert_not_awaited()
        client.set_account_schedulable.assert_not_awaited()

    async def test_newer_event_after_enable_phase_start_prevents_remote_enable(self) -> None:
        db = _db({"schedulable": False})
        db.sub2api_account_test_states.find_one.side_effect = [
            {"last_event_id": "event-1"},
            {"last_event_id": "event-1"},
            {"last_event_id": "event-1"},
            {"last_event_id": "newer-event"},
        ]
        client = SimpleNamespace(
            recover_account_state=AsyncMock(
                return_value={"status": "active", "schedulable": False}
            ),
            set_account_schedulable=AsyncMock(),
        )

        await handle_scheduling(
            db,
            _event("passed", recovery_required=True),
            client=client,
        )

        client.recover_account_state.assert_awaited_once_with(4072)
        client.set_account_schedulable.assert_not_awaited()

    async def test_recover_response_is_cached_before_stale_return(self) -> None:
        db = _db({"schedulable": False})
        db.sub2api_account_test_states.find_one.side_effect = [
            {"last_event_id": "event-1"},
            {"last_event_id": "event-1"},
            {"last_event_id": "newer-event"},
        ]
        client = SimpleNamespace(
            recover_account_state=AsyncMock(
                return_value={
                    "status": "active",
                    "error_message": "",
                    "schedulable": False,
                }
            ),
            set_account_schedulable=AsyncMock(),
        )

        await handle_scheduling(
            db,
            _event("passed", recovery_required=True),
            client=client,
        )

        db.sub2api_accounts_cache.update_one.assert_awaited_once()
        cache_update = db.sub2api_accounts_cache.update_one.await_args.args[1]["$set"]
        self.assertEqual(
            cache_update,
            {
                "status": "active",
                "account.status": "active",
                "error_message": "",
                "account.error_message": "",
                "schedulable": False,
                "account.schedulable": False,
            },
        )
        client.set_account_schedulable.assert_not_awaited()

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
                self.assertEqual(update["$set"]["verified_plan_type_source"], "gpt-5.5")

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

    async def test_recovery_handler_failure_retries_after_three_minutes(self) -> None:
        db = _db()
        db.sub2api_account_test_events.find_one.return_value = _event(
            "passed",
            recovery_required=True,
        )
        handler = AsyncMock(side_effect=RuntimeError("temporary recovery failure"))
        with (
            patch(
                "app.modules.sub2api.account_test_dispatcher.HANDLERS",
                {"scheduling": handler},
            ),
            patch(
                "app.modules.sub2api.account_test_dispatcher._claim_handler",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.modules.sub2api.account_test_dispatcher.now_utc",
                return_value=NOW,
            ),
        ):
            await dispatch_test_event(db, "event-1")

        update = db.sub2api_account_test_events.update_one.await_args.args[1]["$set"]
        self.assertEqual(
            update["dispatch.scheduling.next_retry_at"],
            NOW + timedelta(minutes=3),
        )

    async def test_empty_handler_error_still_schedules_retry(self) -> None:
        db = _db()
        db.sub2api_account_test_events.find_one.return_value = _event("passed")
        handler = AsyncMock(side_effect=RuntimeError())
        with (
            patch(
                "app.modules.sub2api.account_test_dispatcher.HANDLERS",
                {"scheduling": handler},
            ),
            patch(
                "app.modules.sub2api.account_test_dispatcher._claim_handler",
                new=AsyncMock(return_value=True),
            ),
            patch(
                "app.modules.sub2api.account_test_dispatcher.now_utc",
                return_value=NOW,
            ),
        ):
            await dispatch_test_event(db, "event-1")

        update = db.sub2api_account_test_events.update_one.await_args.args[1]["$set"]
        self.assertIn("dispatch.scheduling.next_retry_at", update)
        self.assertEqual(
            update["dispatch.scheduling.next_retry_at"],
            NOW + timedelta(minutes=5),
        )


if __name__ == "__main__":
    unittest.main()
