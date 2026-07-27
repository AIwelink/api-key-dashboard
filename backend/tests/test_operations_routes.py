from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class OperationsRoutePermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_operator_can_read_summary(self) -> None:
        from app.routers import operations

        query = operations.OperationsQuery()
        with patch.object(
            operations.service,
            "get_operations_overview",
            AsyncMock(return_value={"summary": {"registered_user_count": 1}}),
        ) as read:
            result = await operations.get_operations_summary(
                query=query,
                actor={"_id": "operator-1", "role": "operator"},
                db=object(),
            )

        self.assertEqual(result["summary"]["registered_user_count"], 1)
        read.assert_awaited_once()

    async def test_operator_can_trigger_refresh(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import RefreshRequest

        refresh = AsyncMock(return_value={"items": [{"site_id": "aiwelink"}]})
        with (
            patch.object(operations.service, "refresh_operations", refresh),
            patch.object(operations, "write_audit_log", AsyncMock()) as audit,
        ):
            result = await operations.post_operations_refresh(
                payload=RefreshRequest(site_ids=["aiwelink"]),
                actor={"_id": "operator-1", "role": "operator"},
                db=object(),
            )

        self.assertEqual(result["items"][0]["site_id"], "aiwelink")
        audit.assert_awaited_once()

    async def test_operator_cannot_create_internal_user(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import InternalUserCreate

        with self.assertRaises(HTTPException) as raised:
            await operations.post_internal_user(
                payload=InternalUserCreate(site_id="aiwelink", external_user_id="42"),
                actor={"_id": "operator-1", "role": "operator"},
                db=object(),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_admin_internal_user_write_is_audited(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import InternalUserCreate

        created = {
            "internal_user_id": "internal-1",
            "site_id": "aiwelink",
            "external_user_id": "42",
        }
        with (
            patch.object(
                operations.service,
                "create_internal_user_config",
                AsyncMock(return_value=created),
            ),
            patch.object(operations, "write_audit_log", AsyncMock()) as audit,
        ):
            result = await operations.post_internal_user(
                payload=InternalUserCreate(site_id="aiwelink", external_user_id="42"),
                actor={"_id": "admin-1", "role": "admin"},
                db=object(),
            )

        self.assertEqual(result, created)
        self.assertEqual(audit.await_args.kwargs["action"], "operations.internal_user.create")

    async def test_owner_conversion_rate_write_is_audited(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import ConversionRateCreate

        created = {"conversion_rate_id": "rate-1", "site_id": "aiwelink"}
        with (
            patch.object(
                operations.service,
                "create_conversion_rate_config",
                AsyncMock(return_value=created),
            ),
            patch.object(operations, "write_audit_log", AsyncMock()) as audit,
        ):
            result = await operations.post_conversion_rate(
                payload=ConversionRateCreate(
                    site_id="aiwelink",
                    balance_units_per_cny=Decimal("10"),
                    effective_from=NOW,
                ),
                actor={"_id": "owner-1", "role": "owner"},
                db=object(),
            )

        self.assertEqual(result, created)
        self.assertEqual(audit.await_args.kwargs["action"], "operations.conversion_rate.create")


class OperationsCreditBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_credit_adapter_returns_capability_unavailable(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import RedemptionBatchCreate

        with patch.object(
            operations.service,
            "get_client_site",
            AsyncMock(return_value={"id": "aiwelink", "client_type": "sub2api"}),
        ):
            with self.assertRaises(HTTPException) as raised:
                await operations.post_redemption_batch(
                    payload=RedemptionBatchCreate(
                        site_id="aiwelink",
                        purpose="internal",
                        code_count=1,
                        balance_units_per_code=Decimal("100"),
                        idempotency_key="batch-1",
                    ),
                    actor={"_id": "owner-1", "role": "owner"},
                    db=object(),
                )

        self.assertEqual(raised.exception.status_code, 409)
        self.assertEqual(raised.exception.detail["code"], "capability_unavailable")


class OperationsServiceCacheTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        from app.modules.operations.cache import operations_response_cache

        operations_response_cache.invalidate()

    async def asyncTearDown(self) -> None:
        from app.modules.operations.cache import operations_response_cache

        operations_response_cache.invalidate()

    async def test_same_summary_query_reuses_60_second_cache(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import OperationsQuery

        summary = AsyncMock(side_effect=[{"registered_user_count": 2}, {"registered_user_count": 1}])
        query = OperationsQuery(
            range="custom",
            start_at="2026-07-18T00:00:00Z",
            end_at="2026-07-25T00:00:00Z",
        )
        with (
            patch.object(service, "growth_connection", lambda db: _async_context(object())),
            patch.object(service.repository, "get_operations_summary", summary),
        ):
            first = await service.get_operations_overview(object(), query)
            second = await service.get_operations_overview(object(), query)

        self.assertEqual(first, second)
        self.assertEqual(summary.await_count, 2)

    async def test_summary_does_not_run_concurrent_queries_on_one_connection(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import OperationsQuery

        active_queries = 0

        async def guarded_summary(*args, **kwargs):
            nonlocal active_queries
            if active_queries:
                raise RuntimeError("concurrent use of one async connection")
            active_queries += 1
            await asyncio.sleep(0)
            active_queries -= 1
            return {"registered_user_count": 1}

        query = OperationsQuery(
            range="custom",
            start_at="2026-07-01T00:00:00Z",
            end_at="2026-07-08T00:00:00Z",
        )
        with (
            patch.object(service, "growth_connection", lambda db: _async_context(object())),
            patch.object(service.repository, "get_operations_summary", guarded_summary),
        ):
            result = await service.get_operations_overview(object(), query)

        self.assertEqual(result["summary"]["registered_user_count"], 1)

    def test_operations_routes_are_mounted(self) -> None:
        from app.main import app

        paths = {route.path for route in app.routes}

        self.assertIn("/api/operations/summary", paths)
        self.assertIn("/api/operations/internal-users", paths)
        self.assertIn("/api/operations/classification-tasks/{classification_task_id}", paths)


class OperationsConversionRateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_first_implicit_rate_covers_all_historical_data(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import ConversionRateCreate

        payload = ConversionRateCreate(
            site_id="aiwelink",
            balance_units_per_cny=Decimal("10"),
        )
        create_rate = AsyncMock(return_value={"conversion_rate_id": "rate-1"})

        with (
            patch.object(service, "growth_connection", lambda db, write=True: _async_context(object())),
            patch.object(service.repository, "list_conversion_rates", AsyncMock(return_value=[])),
            patch.object(service.repository, "create_conversion_rate", create_rate),
        ):
            await service.create_conversion_rate_config(object(), payload, actor_id="owner")

        saved_payload = create_rate.await_args.args[1]
        self.assertEqual(
            saved_payload.effective_from,
            datetime(1970, 1, 1, tzinfo=UTC),
        )


@asynccontextmanager
async def _async_context(value):
    yield value


if __name__ == "__main__":
    unittest.main()
