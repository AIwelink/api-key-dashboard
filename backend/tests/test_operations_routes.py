from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from bson import BSON
from fastapi import HTTPException, Response


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)


class OperationsRoutePermissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_operator_can_list_masked_redemption_codes(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import RedemptionCodeListQuery

        listed = {"items": [{"id": 101, "code_mask": "rede...cret"}], "total": 1}
        with patch.object(
            operations.service,
            "list_redemption_codes",
            AsyncMock(return_value=listed),
            create=True,
        ) as read:
            result = await operations.get_redemption_codes(
                query=RedemptionCodeListQuery(site_id="aiwelink"),
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(result, listed)
        self.assertEqual(read.await_args.kwargs["actor_id"], "operator-1")

    async def test_operator_cannot_reveal_or_delete_redemption_codes(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import RedemptionCodeBatchDelete

        actor = {"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]}
        with self.assertRaises(HTTPException) as reveal_error:
            await operations.get_redemption_code_reveal(
                site_id="aiwelink",
                code_id=101,
                response=Response(),
                actor=actor,
                db=object(),
            )
        with self.assertRaises(HTTPException) as delete_error:
            await operations.delete_redemption_code_route(
                site_id="aiwelink",
                code_id=101,
                actor=actor,
                db=object(),
            )
        with self.assertRaises(HTTPException) as batch_error:
            await operations.post_redemption_code_batch_delete(
                payload=RedemptionCodeBatchDelete(site_id="aiwelink", code_ids=[101]),
                actor=actor,
                db=object(),
            )

        self.assertEqual(reveal_error.exception.status_code, 403)
        self.assertEqual(delete_error.exception.status_code, 403)
        self.assertEqual(batch_error.exception.status_code, 403)

    async def test_admin_reveal_is_no_store_and_audit_excludes_plaintext(self) -> None:
        from app.routers import operations

        response = Response()
        revealed = {
            "code_id": 101,
            "code": "redeem-secret",
            "code_mask": "rede...cret",
            "fetched_at": NOW.isoformat(),
        }
        with (
            patch.object(
                operations.service,
                "reveal_redemption_code",
                AsyncMock(return_value=revealed),
                create=True,
            ),
            patch.object(operations, "write_audit_log", AsyncMock()) as audit,
        ):
            result = await operations.get_redemption_code_reveal(
                site_id="aiwelink",
                code_id=101,
                response=response,
                actor={"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(result["code"], "redeem-secret")
        self.assertEqual(response.headers["Cache-Control"], "no-store")
        self.assertEqual(audit.await_args.kwargs["after"]["code_mask"], "rede...cret")
        self.assertNotIn("code", audit.await_args.kwargs["after"])
        self.assertNotIn("redeem-secret", str(audit.await_args.kwargs))

    async def test_admin_delete_and_batch_delete_are_unavailable_without_mutation_or_audit(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import RedemptionCodeBatchDelete

        actor = {"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]}
        with (
            patch.object(operations.service, "delete_redemption_code", AsyncMock()) as delete_one,
            patch.object(operations.service, "batch_delete_redemption_codes", AsyncMock()) as delete_many,
            patch.object(operations, "write_audit_log", AsyncMock()) as audit,
        ):
            with self.assertRaises(HTTPException) as single_error:
                await operations.delete_redemption_code_route(
                    site_id="aiwelink",
                    code_id=101,
                    actor=actor,
                    db=object(),
                )
            with self.assertRaises(HTTPException) as batch_error:
                await operations.post_redemption_code_batch_delete(
                    payload=RedemptionCodeBatchDelete(site_id="aiwelink", code_ids=[102, 103]),
                    actor=actor,
                    db=object(),
                )

        self.assertEqual(single_error.exception.status_code, 409)
        self.assertEqual(single_error.exception.detail["code"], "capability_unavailable")
        self.assertEqual(batch_error.exception.status_code, 409)
        self.assertEqual(batch_error.exception.detail["code"], "capability_unavailable")
        delete_one.assert_not_awaited()
        delete_many.assert_not_awaited()
        audit.assert_not_awaited()

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
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(result["summary"]["registered_user_count"], 1)
        self.assertEqual(read.await_args.kwargs["allowed_site_ids"], ("aiwelink",))

    async def test_operator_can_read_lifecycle_with_authorized_site_scope(self) -> None:
        from app.routers import operations

        query = operations.OperationsQuery(site_id="aiwelink", range="30d")
        lifecycle = {
            "summary": {"churned_user_count": 2},
            "retention": [],
            "site_breakdown": [],
            "model_breakdown": [],
            "customer_breakdown": [],
        }
        with patch.object(
            operations.service,
            "get_operations_lifecycle_data",
            AsyncMock(return_value=lifecycle),
            create=True,
        ) as read:
            result = await operations.get_operations_lifecycle(
                query=query,
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(result["summary"]["churned_user_count"], 2)
        self.assertEqual(read.await_args.kwargs["allowed_site_ids"], ("aiwelink",))
        self.assertEqual(read.await_args.args[1].range.value, "30d")

    async def test_lifecycle_rejects_an_unauthorized_site(self) -> None:
        from app.routers import operations

        with self.assertRaises(HTTPException) as raised:
            await operations.get_operations_lifecycle(
                query=operations.OperationsQuery(site_id="aigclink"),
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_missing_site_permissions_deny_operations_reads(self) -> None:
        from app.routers import operations

        with self.assertRaises(HTTPException) as raised:
            await operations.get_operations_summary(
                query=operations.OperationsQuery(),
                actor={"_id": "operator-1", "role": "operator"},
                db=object(),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_explicit_unauthorized_site_is_denied(self) -> None:
        from app.routers import operations

        with self.assertRaises(HTTPException) as raised:
            await operations.get_operations_summary(
                query=operations.OperationsQuery(site_id="aigclink"),
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_all_sites_query_passes_only_actor_scope(self) -> None:
        from app.routers import operations

        query = operations.OperationsQuery()
        with patch.object(
            operations.service,
            "get_operations_trend_data",
            AsyncMock(return_value={"items": []}),
        ) as read:
            await operations.get_operations_trends(
                query=query,
                actor={
                    "_id": "operator-1",
                    "role": "operator",
                    "operations_site_ids": ["aigclink", "unknown", "aiwelink"],
                },
                db=object(),
            )

        self.assertEqual(read.await_args.kwargs["allowed_site_ids"], ("aiwelink", "aigclink"))

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
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(result["items"][0]["site_id"], "aiwelink")
        self.assertEqual(refresh.await_args.kwargs["allowed_site_ids"], ("aiwelink",))
        audit.assert_awaited_once()

    async def test_refresh_rejects_any_unauthorized_requested_site(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import RefreshRequest

        with self.assertRaises(HTTPException) as raised:
            await operations.post_operations_refresh(
                payload=RefreshRequest(site_ids=["aiwelink", "aigclink"]),
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_operator_cannot_create_internal_user(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import InternalUserCreate

        with self.assertRaises(HTTPException) as raised:
            await operations.post_internal_user(
                payload=InternalUserCreate(site_id="aiwelink", email="staff@example.com"),
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_operator_cannot_delete_internal_user(self) -> None:
        from app.routers import operations

        with self.assertRaises(HTTPException) as raised:
            await operations.delete_internal_user(
                internal_user_id=uuid4(),
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_admin_internal_user_write_is_audited(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import InternalUserCreate

        created = {
            "internal_user_id": "internal-1",
            "site_id": "aiwelink",
            "email": "staff@example.com",
            "external_user_id": "42",
            "recognized_at": NOW.isoformat(),
            "recognition_status": "recognized",
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
                payload=InternalUserCreate(site_id="aiwelink", email="staff@example.com"),
                actor={"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(result, created)
        self.assertEqual(result["email"], "staff@example.com")
        self.assertEqual(result["recognition_status"], "recognized")
        self.assertEqual(audit.await_args.kwargs["action"], "operations.internal_user.create")

    async def test_admin_internal_user_delete_is_audited(self) -> None:
        from app.routers import operations

        internal_user_id = uuid4()
        deleted = {
            "internal_user_id": str(internal_user_id),
            "site_id": "aiwelink",
            "email": "staff@example.com",
            "external_user_id": "49",
        }
        with (
            patch.object(
                operations.service,
                "delete_internal_user_config",
                AsyncMock(return_value=deleted),
                create=True,
            ),
            patch.object(operations, "write_audit_log", AsyncMock()) as audit,
        ):
            result = await operations.delete_internal_user(
                internal_user_id=internal_user_id,
                actor={"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(result, deleted)
        self.assertEqual(audit.await_args.kwargs["action"], "operations.internal_user.delete")
        self.assertEqual(audit.await_args.kwargs["before"], deleted)

    async def test_admin_cannot_write_an_unauthorized_site(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import InternalUserCreate

        with self.assertRaises(HTTPException) as raised:
            await operations.post_internal_user(
                payload=InternalUserCreate(site_id="aigclink", email="staff@example.com"),
                actor={"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_missing_internal_user_uuid_returns_not_found(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import InternalUserUpdate

        with (
            patch.object(
                operations.service,
                "growth_connection",
                lambda db, write=True: _async_context(object()),
            ),
            patch.object(
                operations.service.repository,
                "get_internal_user_site_id",
                AsyncMock(return_value=None),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await operations.patch_internal_user(
                    internal_user_id=uuid4(),
                    payload=InternalUserUpdate(reason="updated"),
                    actor={"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]},
                    db=object(),
                )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_missing_internal_user_uuid_delete_returns_not_found(self) -> None:
        from app.routers import operations

        with (
            patch.object(
                operations.service,
                "growth_connection",
                lambda db, write=True: _async_context(object()),
            ),
            patch.object(
                operations.service.repository,
                "get_internal_user_site_id",
                AsyncMock(return_value=None),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await operations.delete_internal_user(
                    internal_user_id=uuid4(),
                    actor={"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]},
                    db=object(),
                )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_internal_user_at_unauthorized_site_returns_forbidden(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import InternalUserUpdate

        with (
            patch.object(
                operations.service,
                "growth_connection",
                lambda db, write=True: _async_context(object()),
            ),
            patch.object(
                operations.service.repository,
                "get_internal_user_site_id",
                AsyncMock(return_value="aigclink"),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await operations.patch_internal_user(
                    internal_user_id=uuid4(),
                    payload=InternalUserUpdate(reason="updated"),
                    actor={"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]},
                    db=object(),
                )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_internal_user_delete_at_unauthorized_site_returns_forbidden(self) -> None:
        from app.routers import operations

        with (
            patch.object(
                operations.service,
                "growth_connection",
                lambda db, write=True: _async_context(object()),
            ),
            patch.object(
                operations.service.repository,
                "get_internal_user_site_id",
                AsyncMock(return_value="aigclink"),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await operations.delete_internal_user(
                    internal_user_id=uuid4(),
                    actor={"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]},
                    db=object(),
                )

        self.assertEqual(raised.exception.status_code, 403)

    async def test_missing_classification_task_uuid_returns_not_found(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import ClassificationUpdate

        with (
            patch.object(
                operations.service,
                "growth_connection",
                lambda db, write=True: _async_context(object()),
            ),
            patch.object(
                operations.service.repository,
                "get_classification_task_site_id",
                AsyncMock(return_value=None),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await operations.patch_classification_task(
                    classification_task_id=uuid4(),
                    payload=ClassificationUpdate(status="ignored"),
                    actor={"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]},
                    db=object(),
                )

        self.assertEqual(raised.exception.status_code, 404)

    async def test_classification_task_at_unauthorized_site_returns_forbidden(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import ClassificationUpdate

        with (
            patch.object(
                operations.service,
                "growth_connection",
                lambda db, write=True: _async_context(object()),
            ),
            patch.object(
                operations.service.repository,
                "get_classification_task_site_id",
                AsyncMock(return_value="aigclink"),
            ),
        ):
            with self.assertRaises(HTTPException) as raised:
                await operations.patch_classification_task(
                    classification_task_id=uuid4(),
                    payload=ClassificationUpdate(status="ignored"),
                    actor={"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]},
                    db=object(),
                )

        self.assertEqual(raised.exception.status_code, 403)

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
                actor={"_id": "owner-1", "role": "owner", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(result, created)
        self.assertEqual(audit.await_args.kwargs["action"], "operations.conversion_rate.create")


class OperationsCreditBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_redemption_batch_audit_accepts_postgres_decimal_values(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import RedemptionBatchCreate

        created = {
            "redemption_batch_id": str(uuid4()),
            "site_id": "aiwelink",
            "balance_units_per_code": Decimal("100"),
            "cash_amount_cny": Decimal("0"),
            "command_status": "succeeded",
            "codes": ["redeem-alpha"],
            "codes_available": True,
        }

        async def bson_insert(document):
            BSON.encode(document)
            return SimpleNamespace(inserted_id="audit-1")

        db = SimpleNamespace(
            audit_logs=SimpleNamespace(insert_one=AsyncMock(side_effect=bson_insert))
        )
        with patch.object(
            operations.service,
            "create_redemption_batch",
            AsyncMock(return_value=created),
        ):
            result = await operations.post_redemption_batch(
                payload=RedemptionBatchCreate(
                    site_id="aiwelink",
                    purpose="internal",
                    code_count=1,
                    balance_units_per_code=Decimal("100"),
                    idempotency_key="batch-decimal",
                ),
                actor={"_id": "owner-1", "role": "owner", "operations_site_ids": ["aiwelink"]},
                db=db,
            )

        self.assertEqual(result["codes"], ["redeem-alpha"])
        audited = db.audit_logs.insert_one.await_args.args[0]["after"]
        self.assertEqual(audited["balance_units_per_code"], "100")
        self.assertEqual(audited["cash_amount_cny"], "0")
        self.assertNotIn("codes", audited)

    async def test_redemption_batch_audit_excludes_plaintext_codes(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import RedemptionBatchCreate

        created = {
            "redemption_batch_id": "batch-id",
            "site_id": "aiwelink",
            "command_status": "succeeded",
            "code_masks": ["rede...lpha"],
            "codes": ["redeem-alpha"],
            "codes_available": True,
        }
        with (
            patch.object(
                operations.service,
                "create_redemption_batch",
                AsyncMock(return_value=created),
            ),
            patch.object(operations, "write_audit_log", AsyncMock()) as audit,
        ):
            result = await operations.post_redemption_batch(
                payload=RedemptionBatchCreate(
                    site_id="aiwelink",
                    purpose="internal",
                    code_count=1,
                    balance_units_per_code=Decimal("100"),
                    idempotency_key="batch-1",
                ),
                actor={"_id": "owner-1", "role": "owner", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(result["codes"], ["redeem-alpha"])
        self.assertNotIn("codes", audit.await_args.kwargs["after"])
        self.assertNotIn("redeem-alpha", str(audit.await_args.kwargs))

    async def test_unsupported_credit_adapter_returns_capability_unavailable(self) -> None:
        from app.routers import operations
        from app.modules.operations.schemas import RedemptionBatchCreate

        with patch.object(
            operations.service,
            "get_client_site",
            AsyncMock(return_value={"id": "aiwelink", "client_type": "sub2api"}),
        ):
            with (
                patch.object(
                    operations.service,
                    "growth_connection",
                    lambda db, write=True: _async_context(object()),
                ),
                patch.object(
                    operations.service.repository,
                    "get_redemption_batch_by_idempotency",
                    AsyncMock(return_value=None),
                    create=True,
                ),
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
                        actor={"_id": "owner-1", "role": "owner", "operations_site_ids": ["aiwelink"]},
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

    async def test_sync_status_marks_expired_running_row_as_delayed(self) -> None:
        from app.modules.operations import service

        with (
            patch.object(service, "growth_connection", lambda db: _async_context(object())),
            patch.object(
                service.repository,
                "get_sync_status",
                AsyncMock(
                    return_value=[
                        {
                            "site_id": "aiwelink",
                            "status": "running",
                            "started_at": NOW - timedelta(minutes=31),
                            "last_success_at": NOW - timedelta(minutes=5),
                        }
                    ]
                ),
            ),
            patch.object(service, "datetime") as clock,
        ):
            clock.now.return_value = NOW
            result = await service.get_operations_sync_status(
                object(), allowed_site_ids=("aiwelink",)
            )

        self.assertEqual(result["items"][0]["health"], "delayed")

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
            patch.object(
                service.repository,
                "get_operations_site_breakdown",
                AsyncMock(return_value=[{"site_id": "aiwelink"}]),
                create=True,
            ) as breakdown,
        ):
            first = await service.get_operations_overview(object(), query, allowed_site_ids=("aiwelink",))
            second = await service.get_operations_overview(object(), query, allowed_site_ids=("aiwelink",))

        self.assertEqual(first, second)
        self.assertEqual(summary.await_count, 2)
        self.assertEqual(breakdown.await_count, 1)
        self.assertEqual(first["site_breakdown"], [{"site_id": "aiwelink"}])
        self.assertEqual(summary.await_args_list[0].kwargs["allowed_site_ids"], ("aiwelink",))

    async def test_different_site_scopes_do_not_share_summary_cache(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import OperationsQuery

        summary = AsyncMock(side_effect=[
            {"registered_user_count": 2},
            {"registered_user_count": 1},
            {"registered_user_count": 4},
            {"registered_user_count": 3},
        ])
        query = OperationsQuery(
            range="custom",
            start_at="2026-07-18T00:00:00Z",
            end_at="2026-07-25T00:00:00Z",
        )
        with (
            patch.object(service, "growth_connection", lambda db: _async_context(object())),
            patch.object(service.repository, "get_operations_summary", summary),
            patch.object(
                service.repository,
                "get_operations_site_breakdown",
                AsyncMock(return_value=[]),
                create=True,
            ) as breakdown,
        ):
            await service.get_operations_overview(object(), query, allowed_site_ids=("aiwelink",))
            await service.get_operations_overview(object(), query, allowed_site_ids=("aigclink",))

        self.assertEqual(summary.await_count, 4)
        self.assertEqual(breakdown.await_count, 2)

    async def test_lifecycle_cache_isolated_by_site_segment_and_window(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import OperationsQuery

        summary = AsyncMock(return_value=[{"scope": "all", "site_id": None}])
        retention = AsyncMock(return_value=[])
        models = AsyncMock(return_value=[])
        customers = AsyncMock(return_value=[])
        query = OperationsQuery(
            range="custom",
            start_at="2026-07-01T00:00:00Z",
            end_at="2026-07-08T00:00:00Z",
            segment="ordinary",
        )
        with (
            patch.object(service, "growth_connection", lambda db: _async_context(object())),
            patch.object(service.repository, "get_operations_lifecycle_summary", summary, create=True),
            patch.object(service.repository, "get_operations_retention", retention, create=True),
            patch.object(service.repository, "get_operations_model_breakdown", models, create=True),
            patch.object(service.repository, "get_operations_customer_breakdown", customers, create=True),
        ):
            first = await service.get_operations_lifecycle_data(
                object(), query, allowed_site_ids=("aiwelink",)
            )
            second = await service.get_operations_lifecycle_data(
                object(), query, allowed_site_ids=("aiwelink",)
            )
            await service.get_operations_lifecycle_data(
                object(), query, allowed_site_ids=("aigclink",)
            )

        self.assertEqual(first, second)
        self.assertEqual(summary.await_count, 2)
        self.assertEqual(retention.await_count, 2)
        self.assertEqual(first["summary"]["scope"], "all")
        self.assertEqual(first["site_breakdown"], [])
        self.assertIn("window", first)
        self.assertIn("generated_at", first)

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
            patch.object(
                service.repository,
                "get_operations_site_breakdown",
                AsyncMock(return_value=[{"site_id": "aiwelink"}]),
                create=True,
            ),
        ):
            result = await service.get_operations_overview(object(), query, allowed_site_ids=("aiwelink",))

        self.assertEqual(result["summary"]["registered_user_count"], 1)
        self.assertEqual(result["site_breakdown"][0]["site_id"], "aiwelink")

    def test_operations_routes_are_mounted(self) -> None:
        from app.main import app

        paths = {route.path for route in app.routes}

        self.assertIn("/api/operations/summary", paths)
        self.assertIn("/api/operations/lifecycle", paths)
        self.assertIn("/api/operations/internal-users", paths)
        self.assertIn("/api/operations/classification-tasks/{classification_task_id}", paths)

    def test_redemption_code_search_uses_a_body_based_route(self) -> None:
        from app.main import app

        matching_routes = [
            route
            for route in app.routes
            if route.path == "/api/operations/redemption-codes/query"
        ]

        self.assertEqual(len(matching_routes), 1)
        self.assertEqual(matching_routes[0].methods, {"POST"})


class OperationsInternalUserServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_recognized_internal_user_rebuilds_historical_aggregates_under_site_lock(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import InternalUserCreate

        calls = []

        async def acquire_lock(connection, *, site_id):
            calls.append(("lock", site_id))

        async def create_user(connection, payload, *, actor_id):
            calls.append(("create", payload.site_id))
            return {
                "site_id": payload.site_id,
                "external_user_id": "49",
                "recognition_status": "recognized",
            }

        async def rebuild(connection, *, site_id, start_at, end_at):
            calls.append(("rebuild", site_id, start_at, end_at))

        before = datetime.now(UTC)
        with (
            patch.object(service, "growth_connection", lambda db, write=True: _async_context(object())),
            patch.object(service.repository, "acquire_operations_sync_lock", acquire_lock),
            patch.object(service.repository, "create_internal_user", create_user),
            patch.object(service.repository, "replace_affected_aggregates", rebuild),
        ):
            result = await service.create_internal_user_config(
                object(),
                InternalUserCreate(site_id="aiwelink", email="staff@example.com"),
                actor_id="owner",
            )
        after = datetime.now(UTC)

        self.assertEqual(result["recognition_status"], "recognized")
        self.assertEqual(calls[:2], [("lock", "aiwelink"), ("create", "aiwelink")])
        self.assertEqual(
            calls[2][:3],
            ("rebuild", "aiwelink", service.HISTORICAL_CONVERSION_RATE_START),
        )
        self.assertGreaterEqual(calls[2][3], before)
        self.assertLessEqual(calls[2][3], after)

    async def test_pending_internal_user_does_not_rebuild_historical_aggregates(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import InternalUserCreate

        acquire_lock = AsyncMock()
        rebuild = AsyncMock()
        with (
            patch.object(service, "growth_connection", lambda db, write=True: _async_context(object())),
            patch.object(service.repository, "acquire_operations_sync_lock", acquire_lock),
            patch.object(
                service.repository,
                "create_internal_user",
                AsyncMock(
                    return_value={
                        "site_id": "aiwelink",
                        "external_user_id": None,
                        "recognition_status": "pending",
                    }
                ),
            ),
            patch.object(service.repository, "replace_affected_aggregates", rebuild),
        ):
            result = await service.create_internal_user_config(
                object(),
                InternalUserCreate(site_id="aiwelink", email="unknown@example.com"),
                actor_id="owner",
            )

        self.assertEqual(result["recognition_status"], "pending")
        acquire_lock.assert_awaited_once()
        rebuild.assert_not_awaited()

    async def test_internal_user_update_rebuilds_site_even_when_identity_becomes_pending(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import InternalUserUpdate

        internal_user_id = uuid4()
        calls = []

        async def acquire_lock(connection, *, site_id):
            calls.append(("lock", site_id))

        async def update_user(connection, selected_id, payload, *, actor_id):
            calls.append(("update", str(selected_id)))
            return {
                "site_id": "aiwelink",
                "external_user_id": None,
                "recognition_status": "pending",
            }

        async def rebuild(connection, *, site_id, start_at, end_at):
            calls.append(("rebuild", site_id, start_at))

        with (
            patch.object(service, "growth_connection", lambda db, write=True: _async_context(object())),
            patch.object(
                service.repository,
                "get_internal_user_site_id",
                AsyncMock(return_value="aiwelink"),
            ),
            patch.object(service.repository, "acquire_operations_sync_lock", acquire_lock),
            patch.object(service.repository, "update_internal_user", update_user),
            patch.object(service.repository, "replace_affected_aggregates", rebuild),
        ):
            result = await service.update_internal_user_config(
                object(),
                internal_user_id,
                InternalUserUpdate(email="unknown@example.com"),
                actor_id="owner",
                allowed_site_ids=("aiwelink",),
            )

        self.assertEqual(result["recognition_status"], "pending")
        self.assertEqual(
            calls,
            [
                ("lock", "aiwelink"),
                ("update", str(internal_user_id)),
                ("rebuild", "aiwelink", service.HISTORICAL_CONVERSION_RATE_START),
            ],
        )

    async def test_internal_user_delete_rebuilds_history_under_site_lock(self) -> None:
        from app.modules.operations import service

        internal_user_id = uuid4()
        calls = []

        async def acquire_lock(connection, *, site_id):
            calls.append(("lock", site_id))

        async def delete_user(connection, selected_id):
            calls.append(("delete", str(selected_id)))
            return {
                "internal_user_id": str(selected_id),
                "site_id": "aiwelink",
                "email": "staff@example.com",
            }

        async def rebuild(connection, *, site_id, start_at, end_at):
            calls.append(("rebuild", site_id, start_at))

        with (
            patch.object(service, "growth_connection", lambda db, write=True: _async_context(object())),
            patch.object(
                service.repository,
                "get_internal_user_site_id",
                AsyncMock(return_value="aiwelink"),
            ),
            patch.object(service.repository, "acquire_operations_sync_lock", acquire_lock),
            patch.object(service.repository, "delete_internal_user", delete_user, create=True),
            patch.object(service.repository, "replace_affected_aggregates", rebuild),
            patch.object(service.operations_response_cache, "invalidate") as invalidate,
        ):
            result = await service.delete_internal_user_config(
                object(),
                internal_user_id,
                allowed_site_ids=("aiwelink",),
            )

        self.assertEqual(result["email"], "staff@example.com")
        self.assertEqual(
            calls,
            [
                ("lock", "aiwelink"),
                ("delete", str(internal_user_id)),
                ("rebuild", "aiwelink", service.HISTORICAL_CONVERSION_RATE_START),
            ],
        )
        invalidate.assert_called_once_with(site_id="aiwelink")


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
