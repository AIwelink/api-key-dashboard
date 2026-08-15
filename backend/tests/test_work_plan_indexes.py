from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from app.modules.system import bootstrap


class WorkPlanIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_indexes_cover_idempotency_schedule_history_and_cancellation(self) -> None:
        collection = SimpleNamespace(
            create_index=AsyncMock(),
            index_information=AsyncMock(return_value={}),
            drop_index=AsyncMock(),
        )
        head_collection = SimpleNamespace(create_index=AsyncMock())
        db = SimpleNamespace(
            work_plans=collection,
            work_plan_member_heads=head_collection,
        )
        ensure_work_plan_indexes = getattr(bootstrap, "ensure_work_plan_indexes", None)

        self.assertIsNotNone(ensure_work_plan_indexes)
        await ensure_work_plan_indexes(db)

        collection.create_index.assert_has_awaits(
            [
                call(
                    [("member_id", 1), ("idempotency_key", 1), ("plan_date", 1)],
                    unique=True,
                    partialFilterExpression={"schema_version": {"$exists": False}},
                ),
                call([("plan_date", 1), ("member_id", 1), ("created_at", -1)]),
                call([("member_id", 1), ("plan_date", -1), ("created_at", -1)]),
                call([("is_cancelled", 1), ("plan_date", 1)]),
                call(
                    [
                        ("member_id", 1),
                        ("idempotency_key", 1),
                        ("anchor_date", 1),
                        ("operation_type", 1),
                        ("effective_start_at", 1),
                    ],
                    unique=True,
                    partialFilterExpression={"schema_version": 2},
                ),
                call(
                    [("member_id", 1), ("member_sequence", 1)],
                    unique=True,
                    partialFilterExpression={"schema_version": 2},
                ),
                call(
                    [
                        ("member_id", 1),
                        ("effective_start_at", 1),
                        ("effective_end_at", 1),
                    ],
                    partialFilterExpression={"schema_version": 2},
                ),
                call(
                    [("member_id", 1), ("member_sequence", -1)],
                    partialFilterExpression={"schema_version": 2},
                ),
                call("compensates_operation_id", sparse=True),
            ]
        )
        head_collection.create_index.assert_awaited_once_with("lease_until")

    async def test_bootstrap_sets_unique_zhang_owner_only_when_priority_is_missing(self) -> None:
        owner = {
            "_id": "owner",
            "name": " 张城玮 ",
            "role": "owner",
            "status": "active",
        }

        class Cursor:
            def limit(self, value: int) -> "Cursor":
                self.value = value
                return self

            def __aiter__(self):
                self._done = False
                return self

            async def __anext__(self):
                if self._done:
                    raise StopAsyncIteration
                self._done = True
                return owner

        users = SimpleNamespace(
            find=lambda query: Cursor(),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(users=users)

        await bootstrap.ensure_work_plan_priority_defaults(db)

        users.update_one.assert_awaited_once_with(
            {"_id": "owner", "work_plan_priority": {"$exists": False}},
            {"$set": {"work_plan_priority": 1}},
        )


if __name__ == "__main__":
    unittest.main()
