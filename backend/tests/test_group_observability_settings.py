from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic import ValidationError

from app.modules.sub2api.account_probe import update_group_observability_setting
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


if __name__ == "__main__":
    unittest.main()
