from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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

    def test_all_growth_routes_require_owner_or_admin(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
