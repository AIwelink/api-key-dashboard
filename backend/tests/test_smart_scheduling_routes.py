from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.modules.sub2api.smart_scheduling import default_smart_scheduling_rules
from app.modules.sub2api.smart_scheduling_service import (
    get_smart_scheduling_settings,
    smart_scheduling_setting_id,
    update_smart_scheduling_settings,
)
from app.modules.system import bootstrap
from app.routers import api_pools
from app.schemas import SmartSchedulingSettingsUpdate


class SmartSchedulingSchemaTests(unittest.TestCase):
    def test_schema_accepts_the_confirmed_defaults(self) -> None:
        rules = default_smart_scheduling_rules()
        rules["account_types"]["pro"]["extreme_load_factor"] = 23456
        payload = SmartSchedulingSettingsUpdate(
            rules=rules
        )

        self.assertEqual(
            payload.rules.account_types.plus.automatic_priority,
            191,
        )
        self.assertEqual(payload.rules.extreme.priority, 10)
        self.assertEqual(
            payload.rules.account_types.pro.extreme_load_factor,
            23456,
        )

    def test_schema_rejects_extreme_load_factor_below_one(self) -> None:
        rules = default_smart_scheduling_rules()
        rules["account_types"]["plus"]["extreme_load_factor"] = 0

        with self.assertRaises(ValidationError):
            SmartSchedulingSettingsUpdate(rules=rules)

    def test_schema_rejects_overlapping_priority_bands(self) -> None:
        rules = default_smart_scheduling_rules()
        rules["account_types"]["plus"]["system_priority_max"] = 205

        with self.assertRaises(ValidationError):
            SmartSchedulingSettingsUpdate(rules=rules)

    def test_schema_requires_all_four_account_types(self) -> None:
        rules = default_smart_scheduling_rules()
        del rules["account_types"]["pro"]

        with self.assertRaises(ValidationError):
            SmartSchedulingSettingsUpdate(rules=rules)


class SmartSchedulingSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_settings_return_defaults_and_latest_run(self) -> None:
        last_run = {
            "_id": "run-1",
            "site_id": "api-5001",
            "scanned": 5,
            "changed": 2,
        }
        db = SimpleNamespace(
            app_settings=SimpleNamespace(find_one=AsyncMock(return_value=None)),
            sub2api_smart_scheduling_runs=SimpleNamespace(
                find_one=AsyncMock(return_value=last_run)
            ),
        )

        result = await get_smart_scheduling_settings(db, "api-5001")

        self.assertEqual(result["site_id"], "api-5001")
        self.assertEqual(
            result["rules"]["account_types"]["plus"]["automatic_priority"],
            191,
        )
        self.assertEqual(result["default_rules"], default_smart_scheduling_rules())
        self.assertEqual(result["last_run"]["changed"], 2)
        db.sub2api_smart_scheduling_runs.find_one.assert_awaited_once_with(
            {"site_id": "api-5001"},
            sort=[("started_at", -1)],
        )

    async def test_legacy_settings_get_fills_extreme_load_factor_defaults(self) -> None:
        legacy_rules = default_smart_scheduling_rules()
        for rule in legacy_rules["account_types"].values():
            rule.pop("extreme_load_factor", None)
        stored = {
            "_id": smart_scheduling_setting_id("api-5001"),
            "site_id": "api-5001",
            "rules": legacy_rules,
        }
        db = SimpleNamespace(
            app_settings=SimpleNamespace(find_one=AsyncMock(return_value=stored)),
            sub2api_smart_scheduling_runs=SimpleNamespace(
                find_one=AsyncMock(return_value=None)
            ),
        )

        result = await get_smart_scheduling_settings(db, "api-5001")

        for account_type in ("pro", "plus", "k12", "team"):
            self.assertEqual(
                result["rules"]["account_types"][account_type]["extreme_load_factor"],
                10000,
            )

    async def test_update_persists_normalized_rules_and_actor(self) -> None:
        now = datetime(2026, 7, 27, 7, 0, tzinfo=UTC)
        rules = default_smart_scheduling_rules()
        rules["account_types"]["plus"]["normal_concurrency"] = 40
        stored = {
            "_id": smart_scheduling_setting_id("api-5001"),
            "site_id": "api-5001",
            "rules": rules,
            "updated_at": now,
            "updated_by_user_id": "admin@example.com",
            "updated_by_name": "Admin",
        }
        db = SimpleNamespace(
            app_settings=SimpleNamespace(
                update_one=AsyncMock(),
                find_one=AsyncMock(return_value=stored),
            ),
            sub2api_smart_scheduling_runs=SimpleNamespace(
                find_one=AsyncMock(return_value=None)
            ),
        )

        with patch(
            "app.modules.sub2api.smart_scheduling_service.now_utc",
            return_value=now,
        ):
            result = await update_smart_scheduling_settings(
                db,
                site_id="api-5001",
                rules=rules,
                actor={"_id": "admin@example.com", "name": "Admin"},
            )

        update = db.app_settings.update_one.await_args.args[1]
        self.assertEqual(
            db.app_settings.update_one.await_args.args[0],
            {"_id": "smart_scheduling:api-5001"},
        )
        self.assertEqual(
            update["$set"]["rules"]["account_types"]["plus"]["normal_concurrency"],
            40,
        )
        self.assertEqual(update["$set"]["updated_by_user_id"], "admin@example.com")
        self.assertEqual(update["$set"]["updated_by_name"], "Admin")
        self.assertEqual(result["rules"]["account_types"]["plus"]["normal_concurrency"], 40)


class SmartSchedulingRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_get_missing_site_returns_404(self) -> None:
        with patch.object(api_pools, "get_site", AsyncMock(return_value=None)):
            with self.assertRaises(HTTPException) as raised:
                await api_pools.get_smart_scheduling_settings_route(
                    site_id="missing",
                    _={},
                    db=object(),
                )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_patch_updates_settings_and_writes_audit(self) -> None:
        rules = default_smart_scheduling_rules()
        custom_values = {"pro": 11000, "plus": 12000, "k12": 13000, "team": 14000}
        for account_type, value in custom_values.items():
            rules["account_types"][account_type]["extreme_load_factor"] = value
        payload = SmartSchedulingSettingsUpdate(rules=rules)
        updated = {"site_id": "api-5001", "rules": payload.rules.model_dump()}
        actor = {"_id": "admin@example.com"}
        with (
            patch.object(
                api_pools,
                "get_site",
                AsyncMock(return_value={"id": "api-5001"}),
            ),
            patch.object(
                api_pools,
                "save_smart_scheduling_settings",
                AsyncMock(return_value=updated),
            ) as service,
            patch.object(api_pools, "write_audit_log", AsyncMock()) as audit,
        ):
            result = await api_pools.patch_smart_scheduling_settings(
                payload=payload,
                site_id="api-5001",
                actor=actor,
                db=object(),
            )

        self.assertEqual(result, updated)
        for account_type, value in custom_values.items():
            self.assertEqual(
                result["rules"]["account_types"][account_type]["extreme_load_factor"],
                value,
            )
        service.assert_awaited_once_with(
            ANY,
            site_id="api-5001",
            rules=rules,
            actor=actor,
        )
        audit.assert_awaited_once()
        self.assertEqual(
            audit.await_args.kwargs["action"],
            "api_pool.smart_scheduling.update",
        )
        self.assertEqual(
            audit.await_args.kwargs["resource_id"],
            "smart_scheduling:api-5001",
        )


class SmartSchedulingIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_indexes_cover_state_runs_outcomes_and_retention(self) -> None:
        outcomes = SimpleNamespace(
            index_information=AsyncMock(
                return_value={
                    "_id_": {"key": [("_id", 1)]},
                    "site_id_1_run_id_1_remote_account_id_1": {
                        "key": [
                            ("site_id", 1),
                            ("run_id", 1),
                            ("remote_account_id", 1),
                        ]
                    },
                }
            ),
            drop_index=AsyncMock(),
            create_index=AsyncMock(),
        )
        db = SimpleNamespace(
            sub2api_smart_scheduling_states=SimpleNamespace(
                create_index=AsyncMock()
            ),
            sub2api_smart_scheduling_runs=SimpleNamespace(
                create_index=AsyncMock()
            ),
            sub2api_smart_scheduling_outcomes=outcomes,
        )

        await bootstrap.ensure_smart_scheduling_indexes(db)

        db.sub2api_smart_scheduling_states.create_index.assert_awaited_once_with(
            [("site_id", 1), ("remote_account_id", 1)],
            unique=True,
        )
        db.sub2api_smart_scheduling_runs.create_index.assert_any_await(
            [("site_id", 1), ("started_at", -1)]
        )
        db.sub2api_smart_scheduling_runs.create_index.assert_any_await(
            "expires_at",
            expireAfterSeconds=0,
        )
        outcomes.index_information.assert_awaited_once_with()
        outcomes.drop_index.assert_awaited_once_with(
            "site_id_1_run_id_1_remote_account_id_1"
        )
        outcomes.create_index.assert_awaited_once_with(
            "expires_at",
            expireAfterSeconds=0,
        )

    async def test_indexes_do_not_drop_missing_legacy_outcome_index(self) -> None:
        outcomes = SimpleNamespace(
            index_information=AsyncMock(
                return_value={"_id_": {"key": [("_id", 1)]}}
            ),
            drop_index=AsyncMock(),
            create_index=AsyncMock(),
        )
        db = SimpleNamespace(
            sub2api_smart_scheduling_states=SimpleNamespace(
                create_index=AsyncMock()
            ),
            sub2api_smart_scheduling_runs=SimpleNamespace(
                create_index=AsyncMock()
            ),
            sub2api_smart_scheduling_outcomes=outcomes,
        )

        await bootstrap.ensure_smart_scheduling_indexes(db)

        outcomes.drop_index.assert_not_awaited()
        outcomes.create_index.assert_awaited_once_with(
            "expires_at",
            expireAfterSeconds=0,
        )


if __name__ == "__main__":
    unittest.main()
