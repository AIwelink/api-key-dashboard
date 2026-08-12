from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from unittest.mock import AsyncMock, patch
from uuid import uuid4


@asynccontextmanager
async def _async_context(value):
    yield value


class OperationsCreditCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_redemption_code_list_is_masked_attributed_and_current_actor_first(self) -> None:
        from app.modules.operations import service

        adapter = AsyncMock()
        adapter.list_redemption_codes.return_value = {
            "items": [
                {
                    "id": 303,
                    "code": "external-secret",
                    "status": "unused",
                    "value": 20,
                    "created_at": "2026-08-12T04:00:00Z",
                },
                {
                    "id": 202,
                    "code": "other-secret",
                    "status": "used",
                    "value": 30,
                    "created_at": "2026-08-12T03:00:00Z",
                    "notes": "private reconciliation note",
                    "future_secret": "must-not-pass-through",
                    "user": {
                        "email": "customer@example.com",
                        "balance": 999,
                        "role": "user",
                        "allowed_groups": [1, 2],
                    },
                    "group": {"id": 1, "name": "private group"},
                },
                {
                    "id": 101,
                    "code": "current-secret",
                    "status": "unused",
                    "value": 40,
                    "created_at": "2026-08-12T02:00:00Z",
                },
            ],
            "total": 3,
            "pages": 1,
        }
        attributions = [
            {
                "source_batch_id": "101",
                "code_masks": ["curr...cret"],
                "requested_by": "owner-1",
                "created_at": "2026-08-12T02:00:00+00:00",
            },
            {
                "source_batch_id": "202",
                "code_masks": ["othe...cret"],
                "requested_by": "admin-2",
                "created_at": "2026-08-12T03:00:00+00:00",
            },
        ]

        with (
            patch.object(
                service,
                "get_client_site",
                AsyncMock(return_value={"id": "aiwelink", "client_type": "sub2api"}),
            ),
            patch.object(service, "create_credit_command_adapter", return_value=adapter),
            patch.object(service, "growth_connection", lambda db: _async_context(object())),
            patch.object(
                service.repository,
                "list_redemption_batch_attributions",
                AsyncMock(return_value=attributions),
                create=True,
            ),
            patch.object(
                service,
                "_redemption_creator_labels",
                AsyncMock(return_value={"owner-1": "Owner", "admin-2": "Admin"}),
                create=True,
            ),
        ):
            result = await service.list_redemption_codes(
                object(),
                site_id="aiwelink",
                page=1,
                page_size=20,
                status_filter=None,
                origin=None,
                search=None,
                actor_id="owner-1",
            )

        self.assertEqual([item["id"] for item in result["items"]], [101, 303, 202])
        self.assertEqual(result["items"][0]["origin"], "management_panel")
        self.assertEqual(result["items"][0]["created_by"], "Owner")
        self.assertTrue(result["items"][0]["created_by_current_user"])
        self.assertEqual(result["items"][1]["origin"], "api_site")
        self.assertEqual(result["items"][1]["code_mask"], "exte...cret")
        self.assertNotIn("code", result["items"][0])
        self.assertNotIn("current-secret", repr(result))
        self.assertEqual(result["items"][2]["user"], {"email": "customer@example.com"})
        self.assertNotIn("notes", result["items"][2])
        self.assertNotIn("future_secret", result["items"][2])
        self.assertNotIn("group", result["items"][2])
        self.assertNotIn("999", repr(result))
        self.assertFalse(result["truncated"])

    async def test_redemption_code_list_filters_origin_before_pagination(self) -> None:
        from app.modules.operations import service

        adapter = AsyncMock()
        adapter.list_redemption_codes.return_value = {
            "items": [
                {"id": 3, "code": "external-three", "status": "unused", "created_at": "2026-08-12T03:00:00Z"},
                {"id": 2, "code": "managed-two", "status": "unused", "created_at": "2026-08-12T02:00:00Z"},
                {"id": 1, "code": "external-one", "status": "unused", "created_at": "2026-08-12T01:00:00Z"},
            ],
            "total": 3,
            "pages": 1,
        }
        with (
            patch.object(service, "get_client_site", AsyncMock(return_value={"id": "aiwelink", "client_type": "sub2api"})),
            patch.object(service, "create_credit_command_adapter", return_value=adapter),
            patch.object(service, "growth_connection", lambda db: _async_context(object())),
            patch.object(
                service.repository,
                "list_redemption_batch_attributions",
                AsyncMock(return_value=[{"source_batch_id": "2", "code_masks": ["mana...-two"], "requested_by": "owner-1"}]),
                create=True,
            ),
            patch.object(service, "_redemption_creator_labels", AsyncMock(return_value={}), create=True),
        ):
            result = await service.list_redemption_codes(
                object(),
                site_id="aiwelink",
                page=2,
                page_size=1,
                status_filter=None,
                origin="api_site",
                search=None,
                actor_id="owner-1",
            )

        self.assertEqual(result["total"], 2)
        self.assertEqual(result["pages"], 2)
        self.assertEqual([item["id"] for item in result["items"]], [1])

    async def test_redemption_code_search_stays_out_of_upstream_query_parameters(self) -> None:
        from app.modules.operations import service

        adapter = AsyncMock()
        adapter.list_redemption_codes.return_value = {
            "items": [
                {
                    "id": 2,
                    "code": "private-secret-code",
                    "status": "unused",
                    "created_at": "2026-08-12T02:00:00Z",
                },
                {
                    "id": 1,
                    "code": "unrelated-code",
                    "status": "used",
                    "created_at": "2026-08-12T01:00:00Z",
                    "user": {"email": "customer@example.com"},
                },
            ],
            "total": 2,
            "pages": 1,
        }
        with (
            patch.object(service, "get_client_site", AsyncMock(return_value={"id": "aiwelink", "client_type": "sub2api"})),
            patch.object(service, "create_credit_command_adapter", return_value=adapter),
            patch.object(service, "growth_connection", lambda db: _async_context(object())),
            patch.object(
                service.repository,
                "list_redemption_batch_attributions",
                AsyncMock(return_value=[]),
                create=True,
            ),
            patch.object(service, "_redemption_creator_labels", AsyncMock(return_value={}), create=True),
        ):
            result = await service.list_redemption_codes(
                object(),
                site_id="aiwelink",
                page=1,
                page_size=20,
                status_filter=None,
                origin=None,
                search="private-secret-code",
                actor_id="owner-1",
            )

        self.assertEqual([item["id"] for item in result["items"]], [2])
        self.assertNotIn("private-secret-code", repr(result))
        adapter.list_redemption_codes.assert_awaited_once_with(
            page=1,
            page_size=service.REDEMPTION_REMOTE_PAGE_SIZE,
            status_filter=None,
            search=None,
        )

    async def test_reveal_works_but_delete_requires_atomic_upstream_capability(self) -> None:
        from app.modules.operations import service

        adapter = AsyncMock()
        adapter.get_redemption_code.return_value = {
            "id": 101,
            "code": "redeem-secret",
            "status": "unused",
        }
        with (
            patch.object(service, "get_client_site", AsyncMock(return_value={"id": "aiwelink", "client_type": "sub2api"})),
            patch.object(service, "create_credit_command_adapter", return_value=adapter),
        ):
            revealed = await service.reveal_redemption_code(object(), site_id="aiwelink", code_id=101)
            with self.assertRaises(service.CreditCapabilityUnavailable):
                await service.delete_redemption_code(object(), site_id="aiwelink", code_id=101)

        self.assertEqual(revealed["code"], "redeem-secret")

    async def test_batch_delete_requires_atomic_upstream_capability(self) -> None:
        from app.modules.operations import service

        adapter = AsyncMock()
        with (
            patch.object(service, "get_client_site", AsyncMock(return_value={"id": "aiwelink", "client_type": "sub2api"})),
            patch.object(service, "create_credit_command_adapter", return_value=adapter),
        ):
            with self.assertRaises(service.CreditCapabilityUnavailable):
                await service.batch_delete_redemption_codes(
                    object(), site_id="aiwelink", code_ids=[101, 102]
                )

        adapter.get_redemption_code.assert_not_awaited()

    async def test_sub2api_adapter_manages_redemption_codes(self) -> None:
        from app.modules.operations.credit_commands import Sub2ApiCreditCommandAdapter

        client = AsyncMock()
        client.list_redemption_codes.return_value = {"items": [{"id": 101}], "total": 1}
        client.get_redemption_code.return_value = {"id": 101, "status": "unused"}
        adapter = Sub2ApiCreditCommandAdapter(client=client)

        listed = await adapter.list_redemption_codes(
            page=1,
            page_size=1000,
            status_filter="unused",
            search="alpha",
        )
        item = await adapter.get_redemption_code(code_id=101)

        self.assertEqual(listed["total"], 1)
        self.assertEqual(item["status"], "unused")
        client.list_redemption_codes.assert_awaited_once_with(
            page=1,
            page_size=1000,
            status_filter="unused",
            search="alpha",
        )
        client.get_redemption_code.assert_awaited_once_with(101)

    async def test_sub2api_redemption_batch_is_generated_and_completed(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import RedemptionBatchCreate

        payload = RedemptionBatchCreate(
            site_id="aiwelink",
            purpose="promotion",
            code_count=2,
            balance_units_per_code=Decimal("100"),
            idempotency_key="batch-1",
        )
        batch_id = uuid4()
        pending = {
            "redemption_batch_id": batch_id,
            "site_id": "aiwelink",
            "command_status": "pending",
        }
        completed = pending | {
            "command_status": "succeeded",
            "source_batch_id": "101,102",
            "code_masks": ["rede...lpha", "rede...beta"],
        }
        adapter = AsyncMock()
        adapter.create_redemption_batch.return_value = {
            "codes": ["redeem-alpha", "redeem-beta"],
            "source_batch_id": "101,102",
        }

        with (
            patch.object(
                service,
                "get_client_site",
                AsyncMock(
                    return_value={
                        "id": "aiwelink",
                        "client_type": "sub2api",
                        "base_url": "https://api.aiwelink.cc",
                        "api_key": "admin-key",
                    }
                ),
            ),
            patch.object(
                service,
                "growth_connection",
                lambda db, write=True: _async_context(object()),
            ),
            patch.object(
                service,
                "create_credit_command_adapter",
                return_value=adapter,
                create=True,
            ),
            patch.object(
                service.repository,
                "get_redemption_batch_by_idempotency",
                AsyncMock(return_value=None),
                create=True,
            ),
            patch.object(
                service.repository,
                "create_redemption_batch_request",
                AsyncMock(return_value=pending),
            ) as create_request,
            patch.object(
                service.repository,
                "complete_redemption_batch",
                AsyncMock(return_value=completed),
                create=True,
            ) as complete,
        ):
            result = await service.create_redemption_batch(
                object(),
                payload,
                actor_id="owner-1",
            )

        create_request.assert_awaited_once()
        adapter.create_redemption_batch.assert_awaited_once_with(site=unittest.mock.ANY, payload=payload)
        self.assertEqual(complete.await_args.kwargs["code_hashes"], [
            sha256(code.encode("utf-8")).hexdigest()
            for code in ("redeem-alpha", "redeem-beta")
        ])
        self.assertNotIn("redeem-alpha", str(complete.await_args.kwargs))
        self.assertEqual(result["codes"], ["redeem-alpha", "redeem-beta"])
        self.assertTrue(result["codes_available"])

    async def test_existing_succeeded_batch_does_not_generate_again(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import RedemptionBatchCreate

        payload = RedemptionBatchCreate(
            site_id="aiwelink",
            purpose="internal",
            code_count=1,
            balance_units_per_code=Decimal("10"),
            idempotency_key="same-batch",
        )
        existing = {
            "redemption_batch_id": uuid4(),
            "site_id": "aiwelink",
            "purpose": "internal",
            "code_count": 1,
            "balance_units_per_code": Decimal("10"),
            "cash_amount_cny": Decimal("0"),
            "note": "",
            "command_status": "succeeded",
            "code_masks": ["abcd...wxyz"],
        }
        adapter = AsyncMock()

        with (
            patch.object(
                service,
                "get_client_site",
                AsyncMock(return_value={"id": "aiwelink", "client_type": "sub2api"}),
            ),
            patch.object(
                service,
                "create_credit_command_adapter",
                return_value=adapter,
            ),
            patch.object(
                service,
                "growth_connection",
                lambda db, write=True: _async_context(object()),
            ),
            patch.object(
                service.repository,
                "get_redemption_batch_by_idempotency",
                AsyncMock(return_value=existing),
                create=True,
            ),
        ):
            result = await service.create_redemption_batch(
                object(),
                payload,
                actor_id="owner-1",
            )

        adapter.create_redemption_batch.assert_not_awaited()
        self.assertEqual(result["codes"], [])
        self.assertFalse(result["codes_available"])
        self.assertTrue(result["idempotent_replay"])

    async def test_failed_remote_generation_marks_the_pending_batch_failed(self) -> None:
        from app.modules.operations import service
        from app.modules.operations.schemas import RedemptionBatchCreate

        batch_id = uuid4()
        payload = RedemptionBatchCreate(
            site_id="aiwelink",
            purpose="internal",
            code_count=1,
            balance_units_per_code=Decimal("10"),
            idempotency_key="failed-batch",
        )
        adapter = AsyncMock()
        adapter.create_redemption_batch.side_effect = RuntimeError("upstream unavailable")
        failed = AsyncMock(return_value={"redemption_batch_id": batch_id, "command_status": "failed"})

        with (
            patch.object(
                service,
                "get_client_site",
                AsyncMock(return_value={"id": "aiwelink", "client_type": "sub2api"}),
            ),
            patch.object(
                service,
                "growth_connection",
                lambda db, write=True: _async_context(object()),
            ),
            patch.object(service, "create_credit_command_adapter", return_value=adapter),
            patch.object(
                service.repository,
                "get_redemption_batch_by_idempotency",
                AsyncMock(return_value=None),
            ),
            patch.object(
                service.repository,
                "create_redemption_batch_request",
                AsyncMock(
                    return_value={
                        "redemption_batch_id": batch_id,
                        "site_id": "aiwelink",
                        "command_status": "pending",
                    }
                ),
            ),
            patch.object(service.repository, "fail_redemption_batch", failed),
        ):
            with self.assertRaisesRegex(RuntimeError, "upstream unavailable"):
                await service.create_redemption_batch(object(), payload, actor_id="owner-1")

        failed.assert_awaited_once_with(
            unittest.mock.ANY,
            redemption_batch_id=batch_id,
            error_code="RuntimeError",
            error_message="upstream unavailable",
        )

    async def test_sub2api_adapter_splits_batches_at_one_hundred(self) -> None:
        from app.modules.operations.credit_commands import Sub2ApiCreditCommandAdapter
        from app.modules.operations.schemas import RedemptionBatchCreate

        client = AsyncMock()
        client.generate_redemption_codes.side_effect = [
            [{"id": index, "code": f"code-{index}"} for index in range(1, 101)],
            [{"id": index, "code": f"code-{index}"} for index in range(101, 151)],
        ]
        adapter = Sub2ApiCreditCommandAdapter(client=client)
        payload = RedemptionBatchCreate(
            site_id="aiwelink",
            purpose="promotion",
            code_count=150,
            balance_units_per_code=Decimal("10"),
            idempotency_key="large-batch",
        )

        result = await adapter.create_redemption_batch(site={}, payload=payload)

        self.assertEqual(client.generate_redemption_codes.await_count, 2)
        self.assertEqual(client.generate_redemption_codes.await_args_list[0].kwargs["count"], 100)
        self.assertEqual(
            client.generate_redemption_codes.await_args_list[0].kwargs["idempotency_key"],
            "large-batch:chunk:1",
        )
        self.assertEqual(client.generate_redemption_codes.await_args_list[1].kwargs["count"], 50)
        self.assertEqual(
            client.generate_redemption_codes.await_args_list[1].kwargs["idempotency_key"],
            "large-batch:chunk:2",
        )
        self.assertEqual(len(result["codes"]), 150)


if __name__ == "__main__":
    unittest.main()
