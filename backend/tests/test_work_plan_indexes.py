from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from app.modules.system import bootstrap


class WorkPlanIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_indexes_cover_idempotency_schedule_history_and_cancellation(self) -> None:
        collection = SimpleNamespace(create_index=AsyncMock())
        db = SimpleNamespace(work_plans=collection)
        ensure_work_plan_indexes = getattr(bootstrap, "ensure_work_plan_indexes", None)

        self.assertIsNotNone(ensure_work_plan_indexes)
        await ensure_work_plan_indexes(db)

        collection.create_index.assert_has_awaits(
            [
                call(
                    [("member_id", 1), ("idempotency_key", 1), ("plan_date", 1)],
                    unique=True,
                ),
                call([("plan_date", 1), ("member_id", 1), ("created_at", -1)]),
                call([("member_id", 1), ("plan_date", -1), ("created_at", -1)]),
                call([("is_cancelled", 1), ("plan_date", 1)]),
            ]
        )


if __name__ == "__main__":
    unittest.main()
