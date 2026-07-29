from __future__ import annotations

import asyncio
import unittest
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import HTTPException

from app.modules.system import bootstrap
from app.modules.sub2api import client as sub2api_client
from app.modules.sub2api import plus_self_produced
from app.modules.sub2api.plus_self_produced import classify_probe_result, plus_account_name


class PlusProbeDecisionTests(unittest.TestCase):
    def test_classifies_passed_rate_limited_unauthorized_and_failed_results(self) -> None:
        self.assertEqual(classify_probe_result({"success": True}), "passed")
        self.assertEqual(
            classify_probe_result({"success": False, "error": "API returned 429: rate limited"}),
            "rate_limited_but_eligible",
        )
        self.assertEqual(
            classify_probe_result({"success": False, "error": "API returned 401: token invalidated"}),
            "unauthorized_banned",
        )
        self.assertEqual(
            classify_probe_result({"success": False, "error": "API returned 403: forbidden"}),
            "failed",
        )

    def test_direct_http_status_errors_are_classified(self) -> None:
        self.assertEqual(classify_probe_result(error="sub2api account test failed with status 401"), "unauthorized_banned")
        self.assertEqual(classify_probe_result(error="sub2api account test failed with status 429"), "rate_limited_but_eligible")

    def test_chatgpt_model_unsupported_error_has_highest_precedence(self) -> None:
        error = (
            "API returned 400: {\"detail\":\"The 'gpt-5.6-sol' model is not supported "
            "when using Codex with a ChatGPT account.\"}"
        )

        self.assertEqual(classify_probe_result({"success": True, "error": error}), "model_not_supported")

    def test_only_bounded_http_status_codes_match(self) -> None:
        self.assertEqual(classify_probe_result(error="account 4012 failed"), "failed")
        self.assertEqual(classify_probe_result(error="wait 4290 milliseconds"), "failed")

    def test_plus_prefix_is_added_once_with_or_without_existing_space(self) -> None:
        self.assertEqual(plus_account_name("user@example.com"), "plus user@example.com")
        self.assertEqual(plus_account_name("plus user@example.com"), "plus user@example.com")
        self.assertEqual(plus_account_name("plususer@example.com"), "plususer@example.com")
        self.assertEqual(plus_account_name("PLUS user@example.com"), "PLUS user@example.com")

    def test_plus_prefix_is_removed_when_reverting_to_free(self) -> None:
        self.assertEqual(plus_self_produced.free_account_name("plus user@example.com"), "user@example.com")
        self.assertEqual(plus_self_produced.free_account_name("plususer@example.com"), "user@example.com")
        self.assertEqual(plus_self_produced.free_account_name("PLUS user@example.com"), "user@example.com")
        self.assertEqual(plus_self_produced.free_account_name("user@example.com"), "user@example.com")

    def test_move_payload_only_contains_requested_promotion_fields(self) -> None:
        account = {"status": "active", "schedulable": True}

        self.assertEqual(
            plus_self_produced._move_payload(
                account,
                group_id=6,
                name="plus user@example.com",
                plan_type="plus",
            ),
            {
                "name": "plus user@example.com",
                "group_id": 6,
                "group_ids": [6],
                "credentials": {"plan_type": "plus"},
            },
        )
        self.assertEqual(
            plus_self_produced._move_payload(account, group_id=7),
            {"group_id": 7, "group_ids": [7]},
        )

    def test_non_ascii_admin_key_is_rejected_before_building_http_headers(self) -> None:
        client = plus_self_produced.Sub2ApiClient(
            base_url="https://sub2.example.com",
            token="key-中文 value",
        )

        with self.assertRaisesRegex(Exception, "ASCII"):
            client.headers()


class PlusSelfProducedSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_setting_uses_enabled_fifteen_minute_default(self) -> None:
        db = SimpleNamespace(
            plus_self_produced_settings=SimpleNamespace(find_one=AsyncMock(return_value=None)),
        )

        settings = await plus_self_produced.get_settings(db)

        self.assertTrue(settings["enabled"])
        self.assertEqual(settings["interval_minutes"], 15)
        self.assertEqual(settings["site_id"], "US06-5002")
        self.assertEqual(settings["source_group_id"], 4)
        self.assertEqual(settings["plus_group_id"], 6)
        self.assertEqual(settings["banned_group_id"], 7)
        self.assertEqual(settings["plus_error_group_id"], 9)

    async def test_update_persists_enabled_interval_and_group_roles(self) -> None:
        stored = {
            "_id": "plus-self-produced",
            "enabled": False,
            "interval_seconds": 1_200,
            "source_group_id": 14,
            "plus_group_id": 16,
            "banned_group_id": 17,
            "plus_error_group_id": 19,
        }
        settings_collection = SimpleNamespace(
            update_one=AsyncMock(),
            find_one=AsyncMock(return_value=stored),
        )
        db = SimpleNamespace(plus_self_produced_settings=settings_collection)

        with patch.object(
            plus_self_produced,
            "list_groups",
            AsyncMock(return_value=[{"id": group_id} for group_id in (14, 16, 17, 19)]),
        ):
            result = await plus_self_produced.update_settings(
                db,
                {
                    "enabled": False,
                    "interval_minutes": 20,
                    "source_group_id": 14,
                    "plus_group_id": 16,
                    "banned_group_id": 17,
                    "plus_error_group_id": 19,
                },
                {"_id": "admin@example.com"},
            )

        updates = settings_collection.update_one.await_args.args[1]["$set"]
        self.assertFalse(updates["enabled"])
        self.assertEqual(updates["interval_seconds"], 1_200)
        self.assertEqual(updates["updated_by"], "admin@example.com")
        self.assertEqual(updates["source_group_id"], 14)
        self.assertEqual(updates["plus_group_id"], 16)
        self.assertEqual(updates["banned_group_id"], 17)
        self.assertEqual(updates["plus_error_group_id"], 19)
        self.assertFalse(result["enabled"])
        self.assertEqual(result["interval_minutes"], 20)

    async def test_stored_document_configures_group_roles_but_not_site_or_model(self) -> None:
        db = SimpleNamespace(
            plus_self_produced_settings=SimpleNamespace(
                find_one=AsyncMock(
                    return_value={
                        "site_id": "wrong-site",
                        "source_group_id": 99,
                        "plus_group_id": 98,
                        "banned_group_id": 97,
                        "plus_error_group_id": 96,
                        "model": "wrong-model",
                    }
                )
            ),
        )

        settings = await plus_self_produced.get_settings(db)

        self.assertEqual(settings["site_id"], "US06-5002")
        self.assertEqual(settings["source_group_id"], 99)
        self.assertEqual(settings["plus_group_id"], 98)
        self.assertEqual(settings["banned_group_id"], 97)
        self.assertEqual(settings["plus_error_group_id"], 96)
        self.assertEqual(settings["model"], "gpt-5.6-sol")

    async def test_update_rejects_duplicate_effective_group_roles(self) -> None:
        stored = {
            "_id": "plus-self-produced",
            "source_group_id": 4,
            "plus_group_id": 6,
            "banned_group_id": 7,
            "plus_error_group_id": 9,
        }
        settings_collection = SimpleNamespace(
            update_one=AsyncMock(),
            find_one=AsyncMock(return_value=stored),
        )
        db = SimpleNamespace(plus_self_produced_settings=settings_collection)

        with (
            patch.object(
                plus_self_produced,
                "list_groups",
                AsyncMock(return_value=[{"id": group_id} for group_id in (4, 6, 7, 9)]),
            ),
            self.assertRaisesRegex(HTTPException, "distinct"),
        ):
            await plus_self_produced.update_settings(
                db,
                {"source_group_id": 6},
                {"_id": "admin@example.com"},
            )

        settings_collection.update_one.assert_not_awaited()

    async def test_update_rejects_group_missing_from_postgresql(self) -> None:
        stored = {
            "_id": "plus-self-produced",
            "source_group_id": 4,
            "plus_group_id": 6,
            "banned_group_id": 7,
            "plus_error_group_id": 9,
        }
        settings_collection = SimpleNamespace(
            update_one=AsyncMock(),
            find_one=AsyncMock(return_value=stored),
        )
        db = SimpleNamespace(plus_self_produced_settings=settings_collection)

        with (
            patch.object(
                plus_self_produced,
                "list_groups",
                AsyncMock(return_value=[{"id": group_id} for group_id in (4, 6, 7)]),
            ),
            self.assertRaisesRegex(HTTPException, "not found: 9"),
        ):
            await plus_self_produced.update_settings(
                db,
                {"interval_minutes": 30},
                {"_id": "admin@example.com"},
            )

        settings_collection.update_one.assert_not_awaited()

    async def test_partial_update_writes_a_complete_group_role_snapshot(self) -> None:
        stored = {
            "_id": "plus-self-produced",
            "source_group_id": 4,
            "plus_group_id": 6,
            "banned_group_id": 7,
            "plus_error_group_id": 9,
        }
        settings_collection = SimpleNamespace(
            update_one=AsyncMock(),
            find_one=AsyncMock(return_value=stored),
        )
        db = SimpleNamespace(plus_self_produced_settings=settings_collection)

        with patch.object(
            plus_self_produced,
            "list_groups",
            AsyncMock(return_value=[{"id": group_id} for group_id in (6, 7, 9, 14)]),
        ):
            await plus_self_produced.update_settings(
                db,
                {"source_group_id": 14},
                {"_id": "admin@example.com"},
            )

        updates = settings_collection.update_one.await_args.args[1]["$set"]
        self.assertEqual(
            {field: updates[field] for field in plus_self_produced.GROUP_SETTING_DEFAULTS},
            {
                "source_group_id": 14,
                "plus_group_id": 6,
                "banned_group_id": 7,
                "plus_error_group_id": 9,
            },
        )

    def test_due_time_uses_last_finish_and_enabled_state(self) -> None:
        now = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)

        self.assertTrue(plus_self_produced.is_probe_due({"enabled": True, "interval_seconds": 900}, now=now))
        self.assertFalse(
            plus_self_produced.is_probe_due(
                {"enabled": False, "interval_seconds": 900, "last_finished_at": now - timedelta(hours=1)},
                now=now,
            )
        )
        self.assertFalse(
            plus_self_produced.is_probe_due(
                {"enabled": True, "interval_seconds": 900, "last_finished_at": now - timedelta(minutes=14)},
                now=now,
            )
        )
        self.assertTrue(
            plus_self_produced.is_probe_due(
                {"enabled": True, "interval_seconds": 900, "last_finished_at": now - timedelta(minutes=15)},
                now=now,
            )
        )


class PlusSelfProducedIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_indexes_support_latest_runs_and_unique_account_results(self) -> None:
        db = SimpleNamespace(
            plus_self_produced_runs=SimpleNamespace(create_index=AsyncMock()),
            plus_self_produced_account_results=SimpleNamespace(create_index=AsyncMock()),
        )

        await bootstrap.ensure_plus_self_produced_indexes(db)

        db.plus_self_produced_runs.create_index.assert_awaited_once_with([("started_at", -1)])
        db.plus_self_produced_account_results.create_index.assert_any_await(
            [("site_id", 1), ("remote_account_id", 1)],
            unique=True,
        )
        db.plus_self_produced_account_results.create_index.assert_any_await([("tested_at", -1)])


class PlusSelfProducedRunTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.admin_key_patcher = patch.object(
            plus_self_produced,
            "fetch_postgres_admin_api_key",
            AsyncMock(return_value="postgres-admin-key"),
        )
        self.fetch_admin_key = self.admin_key_patcher.start()
        self.addCleanup(self.admin_key_patcher.stop)

    def build_db(self) -> SimpleNamespace:
        async def acquire_lock(_query: object, update: dict[str, object], **_kwargs: object) -> dict[str, object]:
            return {"_id": "plus-self-produced-probe", **update["$set"]}

        return SimpleNamespace(
            plus_self_produced_settings=SimpleNamespace(
                find_one=AsyncMock(return_value=None),
                update_one=AsyncMock(),
            ),
            plus_self_produced_runs=SimpleNamespace(
                insert_one=AsyncMock(),
                update_one=AsyncMock(),
            ),
            plus_self_produced_account_results=SimpleNamespace(update_one=AsyncMock()),
            operation_locks=SimpleNamespace(
                find_one_and_update=AsyncMock(side_effect=acquire_lock),
                update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)),
                delete_one=AsyncMock(return_value=SimpleNamespace(deleted_count=1)),
            ),
        )

    async def test_database_lease_rejects_an_active_owner(self) -> None:
        db = SimpleNamespace(
            operation_locks=SimpleNamespace(
                find_one_and_update=AsyncMock(return_value={"owner": "worker-b"}),
            )
        )

        result = await plus_self_produced.acquire_probe_lease(db, owner="worker-a")

        self.assertFalse(result["acquired"])
        query = db.operation_locks.find_one_and_update.await_args.args[0]
        self.assertEqual(query["_id"], "plus-self-produced-probe")
        self.assertIn("$or", query)

    async def test_lease_release_failure_does_not_replace_a_completed_result(self) -> None:
        db = self.build_db()
        completed = {"ok": True, "status": "completed", "run_id": "run-1"}

        with (
            patch.object(plus_self_produced, "_run_probe_locked", AsyncMock(return_value=completed)),
            patch.object(
                plus_self_produced,
                "release_probe_lease",
                AsyncMock(side_effect=RuntimeError("mongo unavailable")),
            ),
        ):
            result = await plus_self_produced.run_probe(db, trigger="manual")

        self.assertEqual(result, completed)

    async def test_lease_release_failure_does_not_replace_cancellation(self) -> None:
        db = self.build_db()

        with (
            patch.object(
                plus_self_produced,
                "_run_probe_locked",
                AsyncMock(side_effect=asyncio.CancelledError),
            ),
            patch.object(
                plus_self_produced,
                "release_probe_lease",
                AsyncMock(side_effect=RuntimeError("mongo unavailable")),
            ),
        ):
            with self.assertRaises(asyncio.CancelledError):
                await plus_self_produced.run_probe(db, trigger="scheduled")

    async def test_admin_http_401_raises_configuration_error_regardless_of_message(self) -> None:
        client = sub2api_client.Sub2ApiClient(
            base_url="https://sub2.example.com",
            token="ascii-secret",
        )
        response = httpx.Response(
            401,
            json={"detail": "invalid API key"},
            request=httpx.Request("POST", "https://sub2.example.com/api/v1/admin/accounts/42/test"),
        )
        http_client = AsyncMock()
        http_client.request.return_value = response
        http_client_context = AsyncMock()
        http_client_context.__aenter__.return_value = http_client

        with (
            patch.object(sub2api_client.httpx, "AsyncClient", return_value=http_client_context),
            self.assertRaises(sub2api_client.InvalidAdminApiKeyError),
        ):
            await client.test_account(42, model_id="gpt-5.6-sol")

    async def test_invalid_admin_key_response_aborts_the_whole_run(self) -> None:
        client = SimpleNamespace(
            test_account=AsyncMock(
                side_effect=sub2api_client.InvalidAdminApiKeyError(
                    "Sub2API Admin API Key was rejected with status 401"
                )
            )
        )

        with self.assertRaisesRegex(sub2api_client.InvalidAdminApiKeyError, "Admin API Key"):
            await plus_self_produced._test_account(client, 42)

    async def test_admin_auth_failure_during_move_aborts_the_whole_run(self) -> None:
        db = self.build_db()
        accounts = [
            {"id": 50, "name": "first@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
            {"id": 51, "name": "second@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
        ]
        async def update_account(account_id: int, payload: dict[str, object]) -> dict[str, object]:
            if payload == {"credentials": {"model_mapping": {}}}:
                return {"id": account_id, **payload}
            raise sub2api_client.InvalidAdminApiKeyError(
                "Sub2API Admin API Key was rejected with status 401"
            )

        client = SimpleNamespace(
            test_account=AsyncMock(return_value={"success": True, "error": None}),
            update_account=AsyncMock(side_effect=update_account),
        )

        with (
            patch.object(plus_self_produced, "get_site", AsyncMock(return_value={"id": "US06-5002", "base_url": "https://sub2.example.com", "token": "secret", "sql_dsn": "postgresql://reader:secret@postgres/sub2api"})),
            patch.object(plus_self_produced, "Sub2ApiClient", return_value=client),
            patch.object(plus_self_produced, "fetch_postgres_pool_snapshot", AsyncMock(return_value={"groups": [{"id": 4}, {"id": 6}, {"id": 7}, {"id": 9}], "accounts": accounts})),
        ):
            result = await plus_self_produced.run_probe(db, trigger="manual")

        self.assertEqual(result["status"], "failed")
        self.assertIn("Admin API Key", result["error"])
        self.assertEqual(client.test_account.await_count, 1)
        self.assertEqual(client.update_account.await_count, 2)

    async def test_reads_groups_accounts_and_admin_key_from_postgresql(self) -> None:
        db = self.build_db()
        sql_dsn = "postgresql://reader:secret@postgres/sub2api"
        snapshot = {
            "groups": [{"id": 4}, {"id": 6}, {"id": 7}, {"id": 9}],
            "accounts": [
                {"id": 40, "name": "source@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
                {"id": 41, "name": "other@example.com", "status": "active", "schedulable": True, "group_ids": [6]},
            ],
        }
        client = SimpleNamespace(
            test_account=AsyncMock(return_value={"success": True, "error": None}),
            update_account=AsyncMock(return_value={"id": 40, "group_ids": [6]}),
        )
        client_factory = MagicMock(return_value=client)
        fetch_admin_key = AsyncMock(return_value="postgres-admin-key")

        with (
            patch.object(
                plus_self_produced,
                "get_site",
                AsyncMock(
                    return_value={
                        "id": "US06-5002",
                        "base_url": "https://sub2.example.com",
                        "token": "错误 mongo key",
                        "sql_dsn": sql_dsn,
                    }
                ),
            ),
            patch.object(plus_self_produced, "Sub2ApiClient", client_factory),
            patch.object(plus_self_produced, "fetch_postgres_pool_snapshot", AsyncMock(return_value=snapshot), create=True) as fetch_snapshot,
            patch.object(plus_self_produced, "fetch_postgres_admin_api_key", fetch_admin_key, create=True),
            patch.object(plus_self_produced, "upsert_cached_account_snapshot", AsyncMock()),
        ):
            result = await plus_self_produced.run_probe(db, trigger="manual")

        fetch_snapshot.assert_awaited_once_with(sql_dsn)
        fetch_admin_key.assert_awaited_once_with(sql_dsn)
        client_factory.assert_called_once_with(
            base_url="https://sub2.example.com",
            token="postgres-admin-key",
        )
        self.assertEqual(client.test_account.await_count, 2)
        self.assertEqual([call.args[0] for call in client.test_account.await_args_list], [40, 41])
        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["eligible"], 2)
        self.assertEqual(result["promoted"], 1)

    async def test_custom_group_roles_drive_candidate_selection_and_routes(self) -> None:
        db = self.build_db()
        db.plus_self_produced_settings.find_one.return_value = {
            "_id": "plus-self-produced",
            "source_group_id": 14,
            "plus_group_id": 16,
            "banned_group_id": 17,
            "plus_error_group_id": 19,
        }
        accounts = [
            {"id": 140, "name": "source@example.com", "group_ids": [14]},
            {"id": 160, "name": "plus free@example.com", "group_ids": [16]},
            {"id": 170, "name": "not-a-candidate@example.com", "group_ids": [17]},
        ]
        verifications = {
            140: {"success": True, "model": "gpt-5.6-sol", "error": None},
            160: {
                "success": False,
                "model": "gpt-5.6-sol",
                "error": "API returned 400: model is not supported when using Codex with a ChatGPT account",
            },
        }
        client = SimpleNamespace(
            test_account=AsyncMock(side_effect=lambda account_id, **_kwargs: verifications[account_id]),
            update_account=AsyncMock(side_effect=lambda account_id, payload: {"id": account_id, **payload}),
        )

        with (
            patch.object(
                plus_self_produced,
                "get_site",
                AsyncMock(
                    return_value={
                        "id": "US06-5002",
                        "base_url": "https://sub2.example.com",
                        "sql_dsn": "postgresql://reader:secret@postgres/sub2api",
                    }
                ),
            ),
            patch.object(plus_self_produced, "Sub2ApiClient", return_value=client),
            patch.object(
                plus_self_produced,
                "fetch_postgres_pool_snapshot",
                AsyncMock(
                    return_value={
                        "groups": [{"id": group_id} for group_id in (14, 16, 17, 19)],
                        "accounts": accounts,
                    }
                ),
            ),
            patch.object(plus_self_produced, "upsert_cached_account_snapshot", AsyncMock()),
        ):
            result = await plus_self_produced.run_probe(db, trigger="manual")

        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["promoted"], 1)
        self.assertEqual(result["downgraded"], 1)
        self.assertEqual(
            [call.args for call in client.update_account.await_args_list],
            [
                (140, {"credentials": {"model_mapping": {}}}),
                (
                    140,
                    {
                        "name": "plus source@example.com",
                        "group_id": 16,
                        "group_ids": [16],
                        "credentials": {"plan_type": "plus"},
                    },
                ),
                (160, {"credentials": {"model_mapping": {}}}),
                (
                    160,
                    {
                        "name": "free@example.com",
                        "group_id": 14,
                        "group_ids": [14],
                        "credentials": {"plan_type": "free"},
                    },
                ),
            ],
        )
        stored = [call.args[1]["$set"] for call in db.plus_self_produced_account_results.update_one.await_args_list]
        self.assertEqual([item["source_group_id"] for item in stored], [14, 16])
        self.assertEqual([item["destination_group_id"] for item in stored], [16, 14])
        run_document = db.plus_self_produced_runs.insert_one.await_args.args[0]
        self.assertEqual(
            {field: run_document[field] for field in plus_self_produced.GROUP_SETTING_DEFAULTS},
            {
                "source_group_id": 14,
                "plus_group_id": 16,
                "banned_group_id": 17,
                "plus_error_group_id": 19,
            },
        )
        self.assertEqual(
            {field: result[field] for field in plus_self_produced.GROUP_SETTING_DEFAULTS},
            {
                "source_group_id": 14,
                "plus_group_id": 16,
                "banned_group_id": 17,
                "plus_error_group_id": 19,
            },
        )

    async def test_model_reset_failure_skips_probe_and_continues_with_next_account(self) -> None:
        db = self.build_db()
        accounts = [
            {"id": 70, "name": "reset-fails@example.com", "group_ids": [4]},
            {"id": 71, "name": "continues@example.com", "group_ids": [4]},
        ]

        async def update_account(account_id: int, payload: dict[str, object]) -> dict[str, object]:
            if account_id == 70 and payload == {"credentials": {"model_mapping": {}}}:
                raise RuntimeError("model reset failed")
            return {"id": account_id, **payload}

        client = SimpleNamespace(
            test_account=AsyncMock(return_value={"success": True, "model": "gpt-5.6-sol", "error": None}),
            update_account=AsyncMock(side_effect=update_account),
        )

        with (
            patch.object(
                plus_self_produced,
                "get_site",
                AsyncMock(
                    return_value={
                        "id": "US06-5002",
                        "base_url": "https://sub2.example.com",
                        "sql_dsn": "postgresql://reader:secret@postgres/sub2api",
                    }
                ),
            ),
            patch.object(plus_self_produced, "Sub2ApiClient", return_value=client),
            patch.object(
                plus_self_produced,
                "fetch_postgres_pool_snapshot",
                AsyncMock(
                    return_value={
                        "groups": [{"id": group_id} for group_id in (4, 6, 7, 9)],
                        "accounts": accounts,
                    }
                ),
            ),
            patch.object(plus_self_produced, "upsert_cached_account_snapshot", AsyncMock()),
        ):
            result = await plus_self_produced.run_probe(db, trigger="manual")

        self.assertEqual(result["candidates"], 2)
        self.assertEqual(result["tested"], 1)
        self.assertEqual(result["promoted"], 1)
        self.assertEqual(result["failed"], 1)
        client.test_account.assert_awaited_once_with(71, model_id="gpt-5.6-sol", prompt="", mode="default")
        stored = [call.args[1]["$set"] for call in db.plus_self_produced_account_results.update_one.await_args_list]
        self.assertEqual([item["action_status"] for item in stored], ["model_reset_failed", "promoted"])
        self.assertIn("model reset failed", stored[0]["error"])

    async def test_serially_routes_pass_429_and_401_while_leaving_other_failures(self) -> None:
        db = self.build_db()
        accounts = [
            {"id": 10, "name": "user@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
            {"id": 11, "name": "plusready@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
            {"id": 12, "name": "blocked@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
            {"id": 13, "name": "free@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
        ]
        verifications = {
            10: {"success": True, "model": "gpt-5.6-sol", "latency_ms": 10, "error": None},
            11: {"success": False, "model": "gpt-5.6-sol", "latency_ms": 11, "error": "API returned 429"},
            12: {"success": False, "model": "gpt-5.6-sol", "latency_ms": 12, "error": "API returned 401"},
            13: {
                "success": False,
                "model": "gpt-5.6-sol",
                "latency_ms": 13,
                "error": "API returned 400: model is not supported when using Codex with a ChatGPT account",
            },
        }
        active = 0
        max_active = 0

        async def test_account(account_id: int, **_kwargs: object) -> dict[str, object]:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0)
            active -= 1
            return verifications[account_id]

        client = SimpleNamespace(
            test_account=AsyncMock(side_effect=test_account),
            update_account=AsyncMock(
                side_effect=lambda account_id, payload: {"id": account_id, **payload}
            ),
        )

        with (
            patch.object(plus_self_produced, "get_site", AsyncMock(return_value={"id": "US06-5002", "base_url": "https://sub2.example.com", "token": "secret", "sql_dsn": "postgresql://reader:secret@postgres/sub2api"})),
            patch.object(plus_self_produced, "Sub2ApiClient", return_value=client),
            patch.object(plus_self_produced, "fetch_postgres_pool_snapshot", AsyncMock(return_value={"groups": [{"id": 4}, {"id": 6}, {"id": 7}, {"id": 9}], "accounts": accounts})),
            patch.object(plus_self_produced, "upsert_cached_account_snapshot", AsyncMock()) as upsert_cache,
        ):
            result = await plus_self_produced.run_probe(db, trigger="manual")

        self.assertEqual(max_active, 1)
        self.assertEqual(result["candidates"], 4)
        self.assertEqual(result["eligible"], 2)
        self.assertEqual(result["promoted"], 2)
        self.assertEqual(result["banned"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(client.test_account.await_count, 4)
        for call in client.test_account.await_args_list:
            self.assertEqual(call.kwargs, {"model_id": "gpt-5.6-sol", "prompt": "", "mode": "default"})
        self.assertEqual(
            [call.args for call in client.update_account.await_args_list],
            [
                (10, {"credentials": {"model_mapping": {}}}),
                (
                    10,
                    {
                        "name": "plus user@example.com",
                        "group_id": 6,
                        "group_ids": [6],
                        "credentials": {"plan_type": "plus"},
                    },
                ),
                (11, {"credentials": {"model_mapping": {}}}),
                (
                    11,
                    {
                        "name": "plusready@example.com",
                        "group_id": 6,
                        "group_ids": [6],
                        "credentials": {"plan_type": "plus"},
                    },
                ),
                (12, {"credentials": {"model_mapping": {}}}),
                (12, {"group_id": 7, "group_ids": [7]}),
                (13, {"credentials": {"model_mapping": {}}}),
            ],
        )
        self.assertEqual(upsert_cache.await_count, 3)
        stored = [call.args[1]["$set"] for call in db.plus_self_produced_account_results.update_one.await_args_list]
        self.assertEqual(
            [item["classification"] for item in stored],
            ["passed", "rate_limited_but_eligible", "unauthorized_banned", "model_not_supported"],
        )

    async def test_move_failure_is_recorded_and_later_accounts_continue(self) -> None:
        db = self.build_db()
        accounts = [
            {"id": 20, "name": "first@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
            {"id": 21, "name": "second@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
        ]
        async def update_account(account_id: int, payload: dict[str, object]) -> dict[str, object]:
            if payload == {"credentials": {"model_mapping": {}}}:
                return {"id": account_id, **payload}
            if account_id == 20:
                raise RuntimeError("remote update failed")
            return {"id": account_id, **payload}

        client = SimpleNamespace(
            test_account=AsyncMock(
                side_effect=[
                    {"success": True, "error": None},
                    {"success": False, "error": "API returned 401"},
                ]
            ),
            update_account=AsyncMock(side_effect=update_account),
        )

        with (
            patch.object(plus_self_produced, "get_site", AsyncMock(return_value={"id": "US06-5002", "base_url": "https://sub2.example.com", "token": "secret", "sql_dsn": "postgresql://reader:secret@postgres/sub2api"})),
            patch.object(plus_self_produced, "Sub2ApiClient", return_value=client),
            patch.object(plus_self_produced, "fetch_postgres_pool_snapshot", AsyncMock(return_value={"groups": [{"id": 4}, {"id": 6}, {"id": 7}, {"id": 9}], "accounts": accounts})),
            patch.object(plus_self_produced, "upsert_cached_account_snapshot", AsyncMock()),
        ):
            result = await plus_self_produced.run_probe(db, trigger="manual")

        self.assertEqual(client.test_account.await_count, 2)
        self.assertEqual(client.update_account.await_count, 4)
        self.assertEqual(result["promoted"], 0)
        self.assertEqual(result["banned"], 1)
        self.assertEqual(result["failed"], 1)
        stored = [call.args[1]["$set"] for call in db.plus_self_produced_account_results.update_one.await_args_list]
        self.assertEqual(stored[0]["action_status"], "promotion_failed")
        self.assertIn("remote update failed", stored[0]["error"])
        self.assertEqual(stored[1]["action_status"], "banned")

    async def test_revalidates_plus_group_and_corrects_400_or_401_accounts(self) -> None:
        db = self.build_db()
        accounts = [
            {"id": 60, "name": "plus passed@example.com", "group_ids": [6]},
            {"id": 61, "name": "plus limited@example.com", "group_ids": [6]},
            {"id": 62, "name": "plusfree@example.com", "group_ids": [6]},
            {"id": 63, "name": "plus blocked@example.com", "group_ids": [6]},
            {"id": 64, "name": "plus failed@example.com", "group_ids": [6]},
        ]
        verifications = {
            60: {"success": True, "model": "gpt-5.6-sol", "error": None},
            61: {"success": False, "model": "gpt-5.6-sol", "error": "API returned 429"},
            62: {
                "success": False,
                "model": "gpt-5.6-sol",
                "error": (
                    "API returned 400: {\"detail\":\"The 'gpt-5.6-sol' model is not supported "
                    "when using Codex with a ChatGPT account.\"}"
                ),
            },
            63: {"success": False, "model": "gpt-5.6-sol", "error": "API returned 401"},
            64: {"success": False, "model": "gpt-5.6-sol", "error": "API returned 403"},
        }
        client = SimpleNamespace(
            test_account=AsyncMock(side_effect=lambda account_id, **_kwargs: verifications[account_id]),
            update_account=AsyncMock(side_effect=lambda account_id, payload: {"id": account_id, **payload}),
        )

        with (
            patch.object(
                plus_self_produced,
                "get_site",
                AsyncMock(
                    return_value={
                        "id": "US06-5002",
                        "base_url": "https://sub2.example.com",
                        "sql_dsn": "postgresql://reader:secret@postgres/sub2api",
                    }
                ),
            ),
            patch.object(plus_self_produced, "Sub2ApiClient", return_value=client),
            patch.object(
                plus_self_produced,
                "fetch_postgres_pool_snapshot",
                AsyncMock(
                    return_value={
                        "groups": [{"id": 4}, {"id": 6}, {"id": 7}, {"id": 9}],
                        "accounts": accounts,
                    }
                ),
            ),
            patch.object(plus_self_produced, "fetch_postgres_admin_api_key", AsyncMock(return_value="admin-key")),
            patch.object(plus_self_produced, "upsert_cached_account_snapshot", AsyncMock()) as upsert_cache,
        ):
            result = await plus_self_produced.run_probe(db, trigger="manual")

        self.assertEqual(result["candidates"], 5)
        self.assertEqual(result["tested"], 5)
        self.assertEqual(result["eligible"], 2)
        self.assertEqual(result["promoted"], 0)
        self.assertEqual(result["downgraded"], 1)
        self.assertEqual(result["plus_errors"], 1)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(
            [call.args for call in client.update_account.await_args_list],
            [
                (60, {"credentials": {"model_mapping": {}}}),
                (61, {"credentials": {"model_mapping": {}}}),
                (62, {"credentials": {"model_mapping": {}}}),
                (
                    62,
                    {
                        "name": "free@example.com",
                        "group_id": 4,
                        "group_ids": [4],
                        "credentials": {"plan_type": "free"},
                    },
                ),
                (63, {"credentials": {"model_mapping": {}}}),
                (63, {"group_id": 9, "group_ids": [9]}),
                (64, {"credentials": {"model_mapping": {}}}),
            ],
        )
        self.assertEqual(upsert_cache.await_count, 2)
        stored = [call.args[1]["$set"] for call in db.plus_self_produced_account_results.update_one.await_args_list]
        self.assertEqual(
            [item["action_status"] for item in stored],
            ["verified_plus", "verified_plus", "reverted_to_free", "plus_error", "not_moved"],
        )
        self.assertTrue(all(item["source_group_id"] == 6 for item in stored))

    async def test_cache_failure_does_not_reclassify_a_successful_remote_move(self) -> None:
        db = self.build_db()
        accounts = [
            {"id": 30, "name": "user@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
        ]
        client = SimpleNamespace(
            test_account=AsyncMock(return_value={"success": True, "error": None}),
            update_account=AsyncMock(return_value={"id": 30, "group_ids": [6]}),
        )

        with (
            patch.object(plus_self_produced, "get_site", AsyncMock(return_value={"id": "US06-5002", "base_url": "https://sub2.example.com", "token": "secret", "sql_dsn": "postgresql://reader:secret@postgres/sub2api"})),
            patch.object(plus_self_produced, "Sub2ApiClient", return_value=client),
            patch.object(plus_self_produced, "fetch_postgres_pool_snapshot", AsyncMock(return_value={"groups": [{"id": 4}, {"id": 6}, {"id": 7}, {"id": 9}], "accounts": accounts})),
            patch.object(plus_self_produced, "upsert_cached_account_snapshot", AsyncMock(side_effect=RuntimeError("cache unavailable"))),
        ):
            result = await plus_self_produced.run_probe(db, trigger="manual")

        self.assertEqual(result["promoted"], 1)
        self.assertEqual(result["failed"], 0)
        stored = db.plus_self_produced_account_results.update_one.await_args.args[1]["$set"]
        self.assertEqual(stored["action_status"], "promoted")
        self.assertIsNone(stored["error"])

    async def test_lease_loss_after_remote_update_does_not_reclassify_the_move(self) -> None:
        db = self.build_db()
        lease_lost = asyncio.Event()
        accounts = [
            {"id": 31, "name": "user@example.com", "status": "active", "schedulable": True, "group_ids": [4]},
        ]

        async def update_account(account_id: int, payload: dict[str, object]) -> dict[str, object]:
            if payload != {"credentials": {"model_mapping": {}}}:
                lease_lost.set()
            return {"id": account_id, **payload}

        client = SimpleNamespace(
            test_account=AsyncMock(return_value={"success": True, "error": None}),
            update_account=AsyncMock(side_effect=update_account),
        )
        with (
            patch.object(plus_self_produced, "get_site", AsyncMock(return_value={"id": "US06-5002", "base_url": "https://sub2.example.com", "token": "secret", "sql_dsn": "postgresql://reader:secret@postgres/sub2api"})),
            patch.object(plus_self_produced, "Sub2ApiClient", return_value=client),
            patch.object(plus_self_produced, "fetch_postgres_pool_snapshot", AsyncMock(return_value={"groups": [{"id": 4}, {"id": 6}, {"id": 7}, {"id": 9}], "accounts": accounts})),
            patch.object(plus_self_produced, "upsert_cached_account_snapshot", AsyncMock()),
        ):
            result = await plus_self_produced._run_probe_locked(
                db,
                trigger="manual",
                lease_lost=lease_lost,
            )

        self.assertEqual(result["promoted"], 1)
        self.assertEqual(result["failed"], 0)
        stored = db.plus_self_produced_account_results.update_one.await_args.args[1]["$set"]
        self.assertEqual(stored["action_status"], "promoted")

    async def test_cancelled_run_is_finished_before_cancellation_propagates(self) -> None:
        db = self.build_db()
        started = asyncio.Event()
        never = asyncio.Event()

        async def fetch_snapshot(_sql_dsn: str) -> dict[str, object]:
            started.set()
            await never.wait()
            return {"groups": [], "accounts": []}

        client = SimpleNamespace()
        with (
            patch.object(plus_self_produced, "get_site", AsyncMock(return_value={"id": "US06-5002", "base_url": "https://sub2.example.com", "token": "secret", "sql_dsn": "postgresql://reader:secret@postgres/sub2api"})),
            patch.object(plus_self_produced, "Sub2ApiClient", return_value=client),
            patch.object(plus_self_produced, "fetch_postgres_pool_snapshot", AsyncMock(side_effect=fetch_snapshot)),
        ):
            task = asyncio.create_task(plus_self_produced.run_probe(db, trigger="scheduled"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

        run_update = db.plus_self_produced_runs.update_one.await_args.args[1]["$set"]
        self.assertEqual(run_update["status"], "cancelled")
        self.assertFalse(run_update["ok"])


class PlusSelfProducedErrorSafetyTests(unittest.TestCase):
    def test_persisted_error_redacts_tokens_and_api_keys(self) -> None:
        raw = (
            'request failed Authorization: Bearer eyJhbGciOiJIUzI1Ni.secret.signature '
            'x-api-key: admin-secret-value '
            '{"access_token":"access-secret","refresh_token":"refresh-secret","api_key":"sk-secretvalue123"}'
        )

        result = plus_self_produced._short_error(raw)

        self.assertNotIn("eyJhbGciOiJIUzI1Ni.secret.signature", result or "")
        self.assertNotIn("access-secret", result or "")
        self.assertNotIn("refresh-secret", result or "")
        self.assertNotIn("sk-secretvalue123", result or "")
        self.assertNotIn("admin-secret-value", result or "")
        self.assertIn("***", result or "")


if __name__ == "__main__":
    unittest.main()
