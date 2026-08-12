from __future__ import annotations

import unittest
from contextlib import asynccontextmanager
from decimal import Decimal
from hashlib import sha256
from unittest.mock import AsyncMock, patch
from uuid import uuid4


@asynccontextmanager
async def _async_context(value):
    yield value


class OperationsCreditCommandTests(unittest.IsolatedAsyncioTestCase):
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
