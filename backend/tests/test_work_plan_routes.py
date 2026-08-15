from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app.modules.work_plans.domain import WorkPlanConflictError
from app.modules.work_plans.schemas import WorkPlanUpdate
from app.main import app
from app.routers import work_plans as work_plans_router


def dependency_permission(dependency: object) -> str:
    for cell in getattr(dependency, "__closure__", None) or ():
        if cell.cell_contents == "work-plans":
            return cell.cell_contents
    return ""


class WorkPlanRouterTests(unittest.IsolatedAsyncioTestCase):
    def test_main_application_registers_work_plan_routes(self) -> None:
        paths = {route.path for route in app.routes}
        self.assertIn("/api/work-plans/schedule", paths)
        self.assertIn("/api/work-plans/{plan_id}/cancel", paths)

    def test_router_exposes_complete_contract_with_work_plan_permission(self) -> None:
        routes = {
            (route.path, method): route
            for route in work_plans_router.router.routes
            for method in route.methods
        }
        expected = {
            ("/work-plans/schedule", "GET"),
            ("/work-plans/mine", "GET"),
            ("/work-plans", "POST"),
            ("/work-plans/{plan_id}", "PATCH"),
            ("/work-plans/{plan_id}/cancel", "POST"),
        }
        self.assertTrue(expected <= set(routes))
        for key in expected:
            dependencies = routes[key].dependant.dependencies
            self.assertTrue(dependencies, key)
            self.assertEqual(dependency_permission(dependencies[0].call), "work-plans", key)

    async def test_api_token_actor_is_rejected_with_chinese_403(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await work_plans_router.get_my_work_plans(
                limit=100,
                cursor=None,
                actor={"_id": "api_token:one", "actor_type": "api_token"},
                db=SimpleNamespace(),
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("浏览器", raised.exception.detail)

    async def test_history_route_forwards_cursor_and_maps_invalid_cursor_to_400(self) -> None:
        with patch.object(
            work_plans_router,
            "list_my_work_plans",
            new=AsyncMock(side_effect=ValueError("分页位置已失效，请刷新后重试")),
        ) as history:
            with self.assertRaises(HTTPException) as raised:
                await work_plans_router.get_my_work_plans(
                    limit=100,
                    cursor="invalid",
                    actor={"_id": "member@example.com", "actor_type": "user"},
                    db=SimpleNamespace(),
                )

        history.assert_awaited_once()
        self.assertEqual(history.await_args.kwargs["cursor"], "invalid")
        self.assertEqual(raised.exception.status_code, 400)

    async def test_update_conflict_maps_to_chinese_409(self) -> None:
        with patch.object(
            work_plans_router,
            "update_work_plan",
            new=AsyncMock(side_effect=WorkPlanConflictError("计划已被更新，请刷新后重试")),
        ):
            with self.assertRaises(HTTPException) as raised:
                await work_plans_router.patch_work_plan(
                    plan_id="plan-1",
                    payload=WorkPlanUpdate(note="new"),
                    actor={"_id": "member@example.com", "actor_type": "user"},
                    db=SimpleNamespace(),
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail, "计划已被更新，请刷新后重试")

    async def test_non_manager_cannot_request_cancelled_team_records(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            await work_plans_router.get_work_plan_schedule(
                range_name="all",
                member_ids=None,
                include_cancelled=True,
                actor={"_id": "member@example.com", "role": "viewer", "actor_type": "user"},
                db=SimpleNamespace(),
            )

        self.assertEqual(raised.exception.status_code, 403)
        self.assertIn("管理员", raised.exception.detail)


if __name__ == "__main__":
    unittest.main()
