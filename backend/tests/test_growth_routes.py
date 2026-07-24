from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

from app.modules.growth import schemas
from app.routers import growth as growth_router


class GrowthConfigurationRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_channel_writes_audit_with_public_result(self) -> None:
        result = {
            "channel_id": "11111111-1111-1111-1111-111111111111",
            "code": "xiaohongshu",
            "name": "小红书",
            "description": "",
            "status": "active",
        }
        create_mock = AsyncMock(return_value=result)
        audit_mock = AsyncMock()

        with (
            patch.object(growth_router, "create_channel_config", create_mock, create=True),
            patch.object(growth_router, "write_audit_log", audit_mock),
        ):
            response = await growth_router.post_growth_channel(
                schemas.ChannelCreate(code="xiaohongshu", name="小红书"),
                actor={"_id": "admin@example.com", "role": "admin"},
                db=MagicMock(),
            )

        self.assertEqual(response, result)
        self.assertEqual(audit_mock.await_args.kwargs["resource_id"], result["channel_id"])
        self.assertEqual(audit_mock.await_args.kwargs["after"], result)

    async def test_duplicate_campaign_code_returns_specific_conflict(self) -> None:
        from app.modules.growth.repository import GrowthConflictError

        message = "当前站点下已存在相同活动编码"
        create_mock = AsyncMock(side_effect=GrowthConflictError(message))

        with patch.object(growth_router, "create_campaign_config", create_mock, create=True):
            with self.assertRaises(Exception) as caught:
                await growth_router.post_growth_campaign(
                    schemas.CampaignCreate(
                        site_id="aiwelink",
                        channel_id="11111111-1111-1111-1111-111111111111",
                        code="summer-2026",
                        name="重复活动",
                    ),
                    actor={"_id": "admin@example.com", "role": "admin"},
                    db=MagicMock(),
                )

        self.assertEqual(getattr(caught.exception, "status_code", None), 409)
        self.assertEqual(getattr(caught.exception, "detail", None), message)

    async def test_update_channel_writes_audit_with_public_result(self) -> None:
        channel_id = UUID("11111111-1111-1111-1111-111111111111")
        result = {
            "channel_id": str(channel_id),
            "code": "xiaohongshu",
            "name": "小红书运营",
            "description": "内容平台",
            "status": "active",
        }
        update_mock = AsyncMock(return_value=result)
        audit_mock = AsyncMock()

        with (
            patch.object(growth_router, "update_channel_config", update_mock, create=True),
            patch.object(growth_router, "write_audit_log", audit_mock),
        ):
            response = await growth_router.patch_growth_channel(
                channel_id,
                schemas.ChannelUpdate(name="小红书运营", description="内容平台"),
                actor={"_id": "admin@example.com", "role": "admin"},
                db=MagicMock(),
            )

        self.assertEqual(response, result)
        self.assertEqual(audit_mock.await_args.kwargs["action"], "growth.channel.update")
        self.assertEqual(audit_mock.await_args.kwargs["resource_id"], str(channel_id))
        self.assertEqual(audit_mock.await_args.kwargs["after"], result)

    async def test_update_campaign_writes_audit_with_public_result(self) -> None:
        campaign_id = UUID("22222222-2222-2222-2222-222222222222")
        result = {
            "campaign_id": str(campaign_id),
            "site_id": "aiwelink",
            "channel_id": "11111111-1111-1111-1111-111111111111",
            "code": "summer-2026",
            "name": "夏季推广调整",
            "description": "",
            "status": "active",
        }
        update_mock = AsyncMock(return_value=result)
        audit_mock = AsyncMock()

        with (
            patch.object(growth_router, "update_campaign_config", update_mock, create=True),
            patch.object(growth_router, "write_audit_log", audit_mock),
        ):
            response = await growth_router.patch_growth_campaign(
                campaign_id,
                schemas.CampaignUpdate(name="夏季推广调整"),
                actor={"_id": "admin@example.com", "role": "admin"},
                db=MagicMock(),
            )

        self.assertEqual(response, result)
        self.assertEqual(audit_mock.await_args.kwargs["action"], "growth.campaign.update")
        self.assertEqual(audit_mock.await_args.kwargs["resource_id"], str(campaign_id))
        self.assertEqual(audit_mock.await_args.kwargs["after"], result)

    async def test_update_tracking_link_writes_audit_with_public_result(self) -> None:
        tracking_link_id = UUID("33333333-3333-3333-3333-333333333333")
        result = {
            "tracking_link_id": str(tracking_link_id),
            "code": "7km4q2xd",
            "source_name": "更新后的来源",
            "status": "active",
        }
        update_mock = AsyncMock(return_value=result)
        audit_mock = AsyncMock()

        with (
            patch.object(growth_router, "update_tracking_link_config", update_mock, create=True),
            patch.object(growth_router, "write_audit_log", audit_mock),
        ):
            response = await growth_router.patch_growth_tracking_link(
                tracking_link_id,
                schemas.TrackingLinkUpdate(source_name="更新后的来源"),
                actor={"_id": "admin@example.com", "role": "admin"},
                db=MagicMock(),
            )

        self.assertEqual(response, result)
        self.assertEqual(audit_mock.await_args.kwargs["action"], "growth.tracking_link.update")
        self.assertEqual(audit_mock.await_args.kwargs["resource_id"], str(tracking_link_id))
        self.assertEqual(audit_mock.await_args.kwargs["after"], result)

    async def test_update_site_requires_existing_client_site(self) -> None:
        update_mock = AsyncMock(side_effect=LookupError("client site not found"))

        with patch.object(growth_router, "update_growth_site_config", update_mock, create=True):
            with self.assertRaisesRegex(Exception, "client site not found") as caught:
                await growth_router.put_growth_site(
                    "missing",
                    schemas.GrowthSiteUpdate(public_origin="https://api.example.com"),
                    actor={"_id": "owner@example.com", "role": "owner"},
                    db=MagicMock(),
                )

        self.assertEqual(getattr(caught.exception, "status_code", None), 404)

    def test_all_growth_routes_require_traffic_analysis_permission(self) -> None:
        protected_paths = {
            "/growth/sites",
            "/growth/sites/{site_id}",
            "/growth/channels",
            "/growth/campaigns",
            "/growth/tracking-links",
        }
        paths = {route.path for route in growth_router.router.routes}

        self.assertTrue(protected_paths <= paths)
        for route in growth_router.router.routes:
            if route.path not in protected_paths:
                continue
            dependencies = [dependency.call for dependency in route.dependant.dependencies]
            self.assertTrue(dependencies, route.path)
            self.assertEqual(_dependency_permission(dependencies[0]), "traffic-analysis", route.path)


def _dependency_permission(dependency: object) -> str:
    closure = getattr(dependency, "__closure__", None) or ()
    for cell in closure:
        value = cell.cell_contents
        if value == "traffic-analysis":
            return value
    return ""


if __name__ == "__main__":
    unittest.main()
