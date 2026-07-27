from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.modules.sub2api.account_probe import (
    default_group_observability_setting,
    list_group_observability_settings,
    update_group_observability_setting,
)
from app.schemas import GroupObservabilitySettingUpdate


class GroupUptimeKumaSettingsTests(unittest.IsolatedAsyncioTestCase):
    def test_schema_accepts_http_monitor_url_and_rejects_unsafe_scheme(self) -> None:
        payload = GroupObservabilitySettingUpdate(uptime_kuma_monitor_url="https://status.aiwelink.cn/dashboard/4")

        self.assertEqual(
            payload.model_dump(exclude_unset=True),
            {"uptime_kuma_monitor_url": "https://status.aiwelink.cn/dashboard/4"},
        )
        with self.assertRaises(ValidationError):
            GroupObservabilitySettingUpdate(uptime_kuma_monitor_url="javascript:alert(1)")

    async def test_group_monitor_url_is_persisted(self) -> None:
        monitor_url = "https://status.aiwelink.cn/dashboard/4"
        groups = SimpleNamespace(
            find_one=AsyncMock(return_value={"group": {"id": 3, "name": "plus-pool-01"}}),
        )
        settings = SimpleNamespace(
            update_one=AsyncMock(),
            find_one=AsyncMock(
                return_value={
                    "_id": "api-5001:3",
                    "site_id": "api-5001",
                    "group_id": 3,
                    "uptime_kuma_monitor_url": monitor_url,
                }
            ),
        )
        db = SimpleNamespace(
            sub2api_groups_cache=groups,
            group_observability_settings=settings,
        )

        result = await update_group_observability_setting(
            db,
            site_id="api-5001",
            group_id=3,
            payload={"uptime_kuma_monitor_url": monitor_url},
            actor={"_id": "admin@example.com"},
        )

        updates = settings.update_one.await_args.args[1]["$set"]
        self.assertEqual(updates["uptime_kuma_monitor_url"], monitor_url)
        self.assertEqual(result["uptime_kuma_monitor_url"], monitor_url)


class AsyncCursor:
    def __init__(self, documents: list[dict[str, object]]) -> None:
        self.documents = documents

    def sort(self, *_: object, **__: object) -> "AsyncCursor":
        return self

    def __aiter__(self):
        self._iterator = iter(self.documents)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class GroupSmartSchedulingSettingsTests(unittest.IsolatedAsyncioTestCase):
    def test_new_group_defaults_both_strategies_off(self) -> None:
        setting = default_group_observability_setting(
            "api-5001",
            3,
            "plus-pool",
        )

        self.assertFalse(setting["type_priority_enabled"])
        self.assertFalse(setting["quota_acceleration_enabled"])

    async def test_old_group_document_returns_explicit_false_flags(self) -> None:
        db = SimpleNamespace(
            group_observability_settings=SimpleNamespace(
                find=lambda *_args, **_kwargs: AsyncCursor(
                    [
                        {
                            "_id": "api-5001:3",
                            "site_id": "api-5001",
                            "group_id": 3,
                            "enabled": True,
                        }
                    ]
                )
            ),
            sub2api_groups_cache=SimpleNamespace(
                find=lambda *_args, **_kwargs: AsyncCursor(
                    [
                        {
                            "site_id": "api-5001",
                            "group_id": 3,
                            "group": {"id": 3, "name": "plus-pool"},
                        }
                    ]
                )
            ),
            sub2api_capacity_notification_meta=SimpleNamespace(
                find=lambda *_args, **_kwargs: AsyncCursor([])
            ),
        )

        result = await list_group_observability_settings(db, "api-5001")

        self.assertFalse(result["items"][0]["type_priority_enabled"])
        self.assertFalse(result["items"][0]["quota_acceleration_enabled"])

    async def test_group_strategy_flags_are_persisted(self) -> None:
        groups = SimpleNamespace(
            find_one=AsyncMock(
                return_value={"group": {"id": 3, "name": "plus-pool"}}
            ),
        )
        settings = SimpleNamespace(
            update_one=AsyncMock(),
            find_one=AsyncMock(
                return_value={
                    "_id": "api-5001:3",
                    "site_id": "api-5001",
                    "group_id": 3,
                    "type_priority_enabled": True,
                    "quota_acceleration_enabled": True,
                }
            ),
        )
        db = SimpleNamespace(
            sub2api_groups_cache=groups,
            group_observability_settings=settings,
        )

        result = await update_group_observability_setting(
            db,
            site_id="api-5001",
            group_id=3,
            payload={
                "type_priority_enabled": True,
                "quota_acceleration_enabled": True,
            },
            actor={"_id": "admin@example.com"},
        )

        updates = settings.update_one.await_args.args[1]["$set"]
        self.assertTrue(updates["type_priority_enabled"])
        self.assertTrue(updates["quota_acceleration_enabled"])
        self.assertTrue(result["type_priority_enabled"])
        self.assertTrue(result["quota_acceleration_enabled"])


if __name__ == "__main__":
    unittest.main()
