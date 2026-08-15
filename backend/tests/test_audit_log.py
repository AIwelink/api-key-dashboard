from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, call

from app.modules.system.audit import write_audit_log
from app.modules.system.bootstrap import ensure_audit_indexes


ACTOR = {
    "_id": "member@example.com",
    "name": "Member Name",
    "actor_type": "user",
}


def fake_db() -> SimpleNamespace:
    return SimpleNamespace(
        audit_logs=SimpleNamespace(
            insert_one=AsyncMock(),
            update_one=AsyncMock(),
            create_index=AsyncMock(),
        )
    )


class AuditLogWriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_legacy_callers_continue_to_insert_without_dedupe(self) -> None:
        db = fake_db()

        await write_audit_log(
            db,
            actor=ACTOR,
            action="account.create",
            resource_type="account",
            resource_id="account-1",
        )

        db.audit_logs.insert_one.assert_awaited_once()
        db.audit_logs.update_one.assert_not_awaited()
        document = db.audit_logs.insert_one.await_args.args[0]
        self.assertNotIn("dedupe_key", document)

    async def test_dedupe_key_uses_set_on_insert_upsert(self) -> None:
        db = fake_db()
        dedupe_key = "work_plan.create:plan-1"

        await write_audit_log(
            db,
            actor=ACTOR,
            action="work_plan.create",
            resource_type="work_plan",
            resource_id="plan-1",
            after={"_id": "plan-1"},
            dedupe_key=dedupe_key,
        )

        db.audit_logs.insert_one.assert_not_awaited()
        db.audit_logs.update_one.assert_awaited_once()
        query, update = db.audit_logs.update_one.await_args.args
        self.assertEqual(query, {"dedupe_key": dedupe_key})
        self.assertEqual(set(update), {"$setOnInsert"})
        self.assertEqual(update["$setOnInsert"]["dedupe_key"], dedupe_key)
        self.assertEqual(update["$setOnInsert"]["resource_id"], "plan-1")
        self.assertTrue(db.audit_logs.update_one.await_args.kwargs["upsert"])


class AuditLogIndexTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_indexes_include_sparse_unique_dedupe_key(self) -> None:
        db = fake_db()

        await ensure_audit_indexes(db)

        db.audit_logs.create_index.assert_has_awaits(
            [
                call("created_at"),
                call("dedupe_key", unique=True, sparse=True),
            ]
        )
        self.assertEqual(db.audit_logs.create_index.await_count, 2)


if __name__ == "__main__":
    unittest.main()
