from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from pydantic import ValidationError


class GrowthSchemaValidationTests(unittest.TestCase):
    def test_site_requires_https_origin_and_normalizes_currency(self) -> None:
        from app.modules.growth.schemas import GrowthSiteUpdate

        with self.assertRaises(ValidationError):
            GrowthSiteUpdate(public_origin="http://api.example.com")

        payload = GrowthSiteUpdate(
            public_origin="https://api.example.com/",
            currency="cny",
            default_landing_path="/register",
        )

        self.assertEqual(payload.public_origin, "https://api.example.com")
        self.assertEqual(payload.currency, "CNY")

    def test_tracking_link_rejects_external_landing_path(self) -> None:
        from app.modules.growth.schemas import TrackingLinkCreate

        with self.assertRaises(ValidationError):
            TrackingLinkCreate(
                site_id="aiwelink",
                campaign_id=uuid4(),
                source_type="post",
                source_name="小红书帖子",
                landing_path="https://evil.example/register",
            )

    def test_tracking_link_accepts_at_most_three_flat_dimensions(self) -> None:
        from app.modules.growth.schemas import TrackingLinkCreate

        with self.assertRaises(ValidationError):
            TrackingLinkCreate(
                site_id="aiwelink",
                campaign_id=uuid4(),
                source_type="group",
                source_name="测试群",
                extra_dimensions={"a": "1", "b": "2", "c": "3", "d": "4"},
            )

        payload = TrackingLinkCreate(
            site_id="aiwelink",
            campaign_id=uuid4(),
            source_type="group",
            source_name="测试群",
            extra_dimensions={" audience ": " developer ", "region": "cn"},
        )

        self.assertEqual(payload.extra_dimensions, {"audience": "developer", "region": "cn"})

    def test_campaign_rejects_invalid_active_window(self) -> None:
        from app.modules.growth.schemas import CampaignCreate

        with self.assertRaises(ValidationError):
            CampaignCreate(
                site_id="aiwelink",
                channel_id=uuid4(),
                code="summer-2026",
                name="夏季推广",
                starts_at="2026-08-01T00:00:00Z",
                ends_at="2026-07-01T00:00:00Z",
            )


class GrowthRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def test_tracking_code_uses_eight_unambiguous_characters(self) -> None:
        from app.modules.growth.repository import TRACKING_CODE_ALPHABET, generate_tracking_code

        for _ in range(50):
            code = generate_tracking_code()
            self.assertEqual(len(code), 8)
            self.assertTrue(set(code) <= set(TRACKING_CODE_ALPHABET))
            self.assertFalse(set(code) & set("0o1il"))

    async def test_create_channel_uses_bound_parameters_and_returns_public_row(self) -> None:
        from app.modules.growth.repository import create_channel
        from app.modules.growth.schemas import ChannelCreate

        channel_id = uuid4()
        row = {
            "channel_id": channel_id,
            "code": "xiaohongshu",
            "name": "小红书",
            "description": "",
            "status": "active",
        }
        connection = _FakeConnection([row])

        result = await create_channel(
            connection,
            ChannelCreate(code="XiaoHongShu", name="小红书"),
            actor_id="admin@example.com",
            channel_id=channel_id,
        )

        self.assertEqual(result["channel_id"], str(channel_id))
        statement, parameters = connection.calls[0]
        self.assertIn(":code", statement)
        self.assertNotIn("xiaohongshu'", statement)
        self.assertEqual(parameters["code"], "xiaohongshu")

    async def test_create_campaign_reports_duplicate_code_within_site(self) -> None:
        from app.modules.growth.repository import GrowthConflictError, create_campaign
        from app.modules.growth.schemas import CampaignCreate

        connection = _FakeConnection([{"site_id": "aiwelink"}, None])
        payload = CampaignCreate(
            site_id="aiwelink",
            channel_id=uuid4(),
            code="summer-2026",
            name="重复活动",
        )

        with self.assertRaises(GrowthConflictError) as caught:
            await create_campaign(connection, payload, actor_id="admin@example.com")

        self.assertEqual(str(caught.exception), "当前站点下已存在相同活动编码")
        statement, _ = connection.calls[0]
        self.assertIn("growth.sites", statement)
        insert_statement, _ = connection.calls[1]
        self.assertIn("ON CONFLICT (site_id, code) DO NOTHING", insert_statement)

    async def test_create_campaign_requires_connected_growth_site(self) -> None:
        from app.modules.growth.repository import GrowthNotFoundError, create_campaign
        from app.modules.growth.schemas import CampaignCreate

        connection = _FakeConnection([None])
        payload = CampaignCreate(
            site_id="aiwelink",
            channel_id=uuid4(),
            code="launch-2026",
            name="上线活动",
        )

        with self.assertRaisesRegex(
            GrowthNotFoundError,
            "当前站点尚未接入流量分析，请先在站点接入页保存站点配置",
        ):
            await create_campaign(connection, payload, actor_id="admin@example.com")

        self.assertEqual(len(connection.calls), 1)
        self.assertIn("growth.sites", connection.calls[0][0])

    async def test_create_tracking_link_rejects_campaign_from_another_site(self) -> None:
        from app.modules.growth.repository import GrowthNotFoundError, create_tracking_link
        from app.modules.growth.schemas import TrackingLinkCreate

        connection = _FakeConnection([None])
        payload = TrackingLinkCreate(
            site_id="site-a",
            campaign_id=uuid4(),
            source_type="post",
            source_name="帖子 A",
        )

        with self.assertRaisesRegex(GrowthNotFoundError, "campaign"):
            await create_tracking_link(connection, payload, actor_id="admin@example.com")

        self.assertEqual(len(connection.calls), 1)

    async def test_create_tracking_link_returns_public_url(self) -> None:
        from app.modules.growth.repository import create_tracking_link
        from app.modules.growth.schemas import TrackingLinkCreate

        campaign_id = uuid4()
        tracking_link_id = uuid4()
        row = {
            "tracking_link_id": tracking_link_id,
            "site_id": "aiwelink",
            "campaign_id": campaign_id,
            "code": "7km4q2xd",
            "source_type": "post",
            "source_name": "帖子 A",
            "status": "active",
            "extra_dimensions": {},
        }
        connection = _FakeConnection([{"campaign_id": campaign_id}, row])

        result = await create_tracking_link(
            connection,
            TrackingLinkCreate(
                site_id="aiwelink",
                campaign_id=campaign_id,
                source_type="post",
                source_name="帖子 A",
            ),
            actor_id="admin@example.com",
            tracking_link_id=tracking_link_id,
            code="7km4q2xd",
        )

        self.assertEqual(result["tracking_link_id"], str(tracking_link_id))
        self.assertEqual(result["public_url"], "https://aiwelink.cc/r/7km4q2xd")


class _FakeMappings:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row

    def all(self):
        return [] if self.row is None else [self.row]


class _FakeResult:
    def __init__(self, row):
        self.row = row

    def mappings(self):
        return _FakeMappings(self.row)


class _FakeConnection:
    def __init__(self, rows: list[dict | None]):
        self.rows = list(rows)
        self.calls: list[tuple[str, dict]] = []
        self.execute = AsyncMock(side_effect=self._execute)

    async def _execute(self, statement, parameters=None):
        self.calls.append((str(statement), dict(parameters or {})))
        return _FakeResult(self.rows.pop(0) if self.rows else None)


if __name__ == "__main__":
    unittest.main()
