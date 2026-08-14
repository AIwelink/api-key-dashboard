from __future__ import annotations

import unittest
from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID

from bson import ObjectId

from app.modules.work_plans.domain import WorkPlanRuleError, deterministic_plan_id
from app.modules.work_plans.schemas import WorkPlanCreate
from app.modules.work_plans.service import (
    WorkPlanAccessError,
    create_work_plans,
    list_my_work_plans,
)


ACTOR = {
    "_id": "member@example.com",
    "name": "Member Name",
    "role": "operator",
}
OBSERVED_AT = datetime(2026, 8, 15, tzinfo=UTC)
IDEMPOTENCY_KEY = UUID("d4426fd9-a2fd-44c0-b47e-f36ae16c9d19")


def create_payload(**overrides: object) -> WorkPlanCreate:
    values = {
        "plan_type": "work",
        "dates": [date(2026, 8, 18)],
        "start_time": "09:00",
        "end_time": "18:00",
        "note": "original note",
        "idempotency_key": IDEMPOTENCY_KEY,
    }
    values.update(overrides)
    return WorkPlanCreate.model_validate(values)


class FakeUpdateResult:
    def __init__(self, *, upserted_id: object | None = None) -> None:
        self.upserted_id = upserted_id


class FakeInsertResult:
    def __init__(self, inserted_id: object) -> None:
        self.inserted_id = inserted_id


class FakeCursor:
    def __init__(self, documents: list[dict]) -> None:
        self._documents = [deepcopy(document) for document in documents]
        self.sort_spec: list[tuple[str, int]] | None = None
        self.limit_value: int | None = None
        self._index = 0

    def sort(self, spec: list[tuple[str, int]]) -> "FakeCursor":
        self.sort_spec = list(spec)
        for field, direction in reversed(self.sort_spec):
            self._documents.sort(
                key=lambda document: document.get(field),
                reverse=direction < 0,
            )
        return self

    def limit(self, value: int) -> "FakeCursor":
        self.limit_value = value
        self._documents = self._documents[:value]
        return self

    def __aiter__(self) -> "FakeCursor":
        self._index = 0
        return self

    async def __anext__(self) -> dict:
        if self._index >= len(self._documents):
            raise StopAsyncIteration
        document = deepcopy(self._documents[self._index])
        self._index += 1
        return document


class FakeCollection:
    def __init__(self, documents: list[dict] | None = None) -> None:
        self.documents = {
            document["_id"]: deepcopy(document) for document in (documents or [])
        }
        self.update_calls: list[tuple[dict, dict, bool]] = []
        self.find_calls: list[dict] = []
        self.insert_calls: list[dict] = []
        self.last_cursor: FakeCursor | None = None
        self.fail_before_write: dict[object, int] = {}
        self.fail_after_write: dict[object, int] = {}
        self.insert_error: Exception | None = None

    async def update_one(self, query: dict, update: dict, *, upsert: bool = False) -> FakeUpdateResult:
        self.update_calls.append((deepcopy(query), deepcopy(update), upsert))
        document_id = query["_id"]
        if self.fail_before_write.get(document_id, 0) > 0:
            self.fail_before_write[document_id] -= 1
            raise RuntimeError("database unavailable")

        if document_id in self.documents:
            return FakeUpdateResult()

        if not upsert:
            return FakeUpdateResult()
        draft = deepcopy(update["$setOnInsert"])
        self.documents[document_id] = draft
        if self.fail_after_write.get(document_id, 0) > 0:
            self.fail_after_write[document_id] -= 1
            raise RuntimeError("write acknowledgement lost")
        return FakeUpdateResult(upserted_id=document_id)

    def find(self, query: dict) -> FakeCursor:
        self.find_calls.append(deepcopy(query))
        documents = [
            document
            for document in self.documents.values()
            if self._matches(document, query)
        ]
        self.last_cursor = FakeCursor(documents)
        return self.last_cursor

    async def insert_one(self, document: dict) -> FakeInsertResult:
        if self.insert_error is not None:
            raise self.insert_error
        stored = deepcopy(document)
        inserted_id = stored.setdefault("_id", ObjectId())
        self.documents[inserted_id] = stored
        self.insert_calls.append(stored)
        return FakeInsertResult(inserted_id)

    @staticmethod
    def _matches(document: dict, query: dict) -> bool:
        for field, expected in query.items():
            actual = document.get(field)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif actual != expected:
                return False
        return True


def fake_db(*, plans: list[dict] | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        work_plans=FakeCollection(plans),
        audit_logs=FakeCollection(),
    )


class WorkPlanCreateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_actor_identity_is_the_only_identity_written(self) -> None:
        db = fake_db()

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(),
            observed_at=OBSERVED_AT,
        )

        document = next(iter(db.work_plans.documents.values()))
        self.assertEqual(document["member_id"], ACTOR["_id"])
        self.assertEqual(document["member_name"], ACTOR["name"])
        self.assertEqual(response["results"][0]["plan"]["member_id"], ACTOR["_id"])
        self.assertNotIn("member_id", create_payload().model_fields_set)

    async def test_two_dates_create_independent_records_in_chronological_order(self) -> None:
        db = fake_db()
        payload = create_payload(dates=[date(2026, 8, 20), date(2026, 8, 18)])

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=payload,
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(response["total"], 2)
        self.assertEqual(
            [item["plan_date"] for item in response["results"]],
            ["2026-08-18", "2026-08-20"],
        )
        self.assertEqual([item["outcome"] for item in response["results"]], ["created", "created"])
        self.assertEqual(len(db.work_plans.documents), 2)
        self.assertEqual(len({item["plan"]["id"] for item in response["results"]}), 2)
        for query, update, upsert in db.work_plans.update_calls:
            self.assertEqual(query, {"_id": update["$setOnInsert"]["_id"]})
            self.assertEqual(set(update), {"$setOnInsert"})
            self.assertTrue(upsert)

    async def test_every_date_reports_created_duplicate_or_failed(self) -> None:
        existing_date = date(2026, 8, 19)
        failed_date = date(2026, 8, 20)
        existing_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, existing_date)
        failed_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, failed_date)
        db = fake_db(
            plans=[
                {
                    "_id": existing_id,
                    "member_id": ACTOR["_id"],
                    "member_name": ACTOR["name"],
                    "plan_date": existing_date.isoformat(),
                    "created_at": OBSERVED_AT,
                }
            ]
        )
        db.work_plans.fail_before_write[failed_id] = 1

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(
                dates=[date(2026, 8, 18), existing_date, failed_date]
            ),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(
            [item["outcome"] for item in response["results"]],
            ["created", "duplicate", "failed"],
        )
        self.assertIn("plan", response["results"][0])
        self.assertIn("plan", response["results"][1])
        self.assertRegex(response["results"][2]["error"], "失败|重试")
        self.assertFalse(response["duplicate_submission"])

    async def test_duplicate_replay_does_not_mutate_original_content(self) -> None:
        db = fake_db()
        first_payload = create_payload(note="original note")
        replay_payload = create_payload(
            start_time="10:00",
            end_time="17:00",
            note="changed note",
        )

        first = await create_work_plans(
            db,
            actor=ACTOR,
            payload=first_payload,
            observed_at=OBSERVED_AT,
        )
        replay = await create_work_plans(
            db,
            actor=ACTOR,
            payload=replay_payload,
            observed_at=OBSERVED_AT.replace(hour=1),
        )

        stored = next(iter(db.work_plans.documents.values()))
        self.assertEqual(first["results"][0]["outcome"], "created")
        self.assertEqual(replay["results"][0]["outcome"], "duplicate")
        self.assertTrue(replay["duplicate_submission"])
        self.assertEqual(stored["note"], "original note")
        self.assertEqual(stored["start_minute"], 9 * 60)
        self.assertEqual(stored["updated_at"], OBSERVED_AT)

    async def test_retry_creates_only_date_that_failed_before_write(self) -> None:
        failed_date = date(2026, 8, 20)
        failed_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, failed_date)
        db = fake_db()
        db.work_plans.fail_before_write[failed_id] = 1
        payload = create_payload(dates=[date(2026, 8, 18), failed_date])

        first = await create_work_plans(
            db,
            actor=ACTOR,
            payload=payload,
            observed_at=OBSERVED_AT,
        )
        retry = await create_work_plans(
            db,
            actor=ACTOR,
            payload=payload,
            observed_at=OBSERVED_AT,
        )

        self.assertEqual([item["outcome"] for item in first["results"]], ["created", "failed"])
        self.assertEqual([item["outcome"] for item in retry["results"]], ["duplicate", "created"])
        self.assertEqual(len(db.work_plans.documents), 2)

    async def test_uncertain_write_is_reported_from_durable_readback(self) -> None:
        plan_date = date(2026, 8, 18)
        plan_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, plan_date)
        db = fake_db()
        db.work_plans.fail_after_write[plan_id] = 1

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(dates=[plan_date]),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(response["results"][0]["outcome"], "duplicate")
        self.assertIn("plan", response["results"][0])
        self.assertNotIn("error", response["results"][0])
        self.assertTrue(response["duplicate_submission"])
        self.assertIn(plan_id, db.work_plans.documents)

    async def test_domain_validation_happens_before_any_write(self) -> None:
        db = fake_db()
        invalid_dates = [date(2026, 8, 18 + offset) for offset in range(6)]

        with self.assertRaises(WorkPlanRuleError):
            await create_work_plans(
                db,
                actor=ACTOR,
                payload=create_payload(dates=invalid_dates),
                observed_at=OBSERVED_AT,
            )

        self.assertEqual(db.work_plans.update_calls, [])
        self.assertEqual(db.audit_logs.insert_calls, [])

    async def test_audit_is_written_once_per_created_record_and_not_on_replay(self) -> None:
        db = fake_db()
        payload = create_payload(dates=[date(2026, 8, 18), date(2026, 8, 19)])

        created = await create_work_plans(
            db,
            actor=ACTOR,
            payload=payload,
            observed_at=OBSERVED_AT,
        )
        replay = await create_work_plans(
            db,
            actor=ACTOR,
            payload=payload,
            observed_at=OBSERVED_AT,
        )

        self.assertEqual([item["outcome"] for item in created["results"]], ["created", "created"])
        self.assertTrue(replay["duplicate_submission"])
        self.assertEqual(len(db.audit_logs.insert_calls), 2)
        for audit in db.audit_logs.insert_calls:
            self.assertEqual(audit["action"], "work_plan.create")
            self.assertEqual(audit["resource_type"], "work_plan")
            self.assertEqual(audit["resource_id"], audit["after"]["_id"])
            self.assertEqual(audit["actor_id"], ACTOR["_id"])

    async def test_audit_failure_does_not_change_created_result(self) -> None:
        db = fake_db()
        db.audit_logs.insert_error = RuntimeError("audit unavailable")

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(response["results"][0]["outcome"], "created")
        self.assertIn("plan", response["results"][0])
        self.assertEqual(len(db.work_plans.documents), 1)


class WorkPlanHistoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_is_strictly_personal_includes_cancelled_and_serializes(self) -> None:
        newer_id = ObjectId("66bb00000000000000000001")
        cancelled_id = ObjectId("66bb00000000000000000002")
        db = fake_db(
            plans=[
                {
                    "_id": ObjectId("66bb00000000000000000003"),
                    "member_id": "someone-else@example.com",
                    "plan_date": "2026-08-30",
                    "created_at": datetime(2026, 8, 10, tzinfo=UTC),
                    "status": "active",
                },
                {
                    "_id": cancelled_id,
                    "member_id": ACTOR["_id"],
                    "plan_date": "2026-08-18",
                    "created_at": datetime(2026, 8, 15, 8, tzinfo=UTC),
                    "status": "cancelled",
                    "is_cancelled": True,
                },
                {
                    "_id": newer_id,
                    "member_id": ACTOR["_id"],
                    "plan_date": "2026-08-18",
                    "created_at": datetime(2026, 8, 15, 9, tzinfo=UTC),
                    "status": "active",
                    "is_cancelled": False,
                },
                {
                    "_id": ObjectId("66bb00000000000000000004"),
                    "member_id": ACTOR["_id"],
                    "plan_date": "2026-08-17",
                    "created_at": datetime(2026, 8, 15, 10, tzinfo=UTC),
                    "status": "active",
                },
            ]
        )

        response = await list_my_work_plans(db, actor=ACTOR, limit=9_999)

        self.assertEqual(db.work_plans.find_calls, [{"member_id": ACTOR["_id"]}])
        self.assertEqual(
            db.work_plans.last_cursor.sort_spec,
            [("plan_date", -1), ("created_at", -1)],
        )
        self.assertEqual(db.work_plans.last_cursor.limit_value, 4_000)
        self.assertEqual(response["total"], 3)
        self.assertEqual(
            [item["id"] for item in response["items"]],
            [str(newer_id), str(cancelled_id), "66bb00000000000000000004"],
        )
        self.assertTrue(response["items"][1]["is_cancelled"])
        self.assertEqual(response["items"][0]["created_at"], "2026-08-15T09:00:00+00:00")

    async def test_history_rejects_api_token_actor_before_query(self) -> None:
        db = fake_db()
        api_actor = {
            "_id": "api_token:token-1",
            "name": "automation",
            "actor_type": "api_token",
        }

        with self.assertRaisesRegex(WorkPlanAccessError, "API.*令牌|浏览器"):
            await list_my_work_plans(db, actor=api_actor)

        self.assertEqual(db.work_plans.find_calls, [])

    async def test_creation_delegates_to_domain_before_the_first_write(self) -> None:
        db = fake_db()

        with patch(
            "app.modules.work_plans.service.build_plan_drafts",
            side_effect=WorkPlanRuleError("invalid"),
        ) as build_plan_drafts:
            with self.assertRaisesRegex(WorkPlanRuleError, "invalid"):
                await create_work_plans(
                    db,
                    actor=ACTOR,
                    payload=create_payload(),
                    observed_at=OBSERVED_AT,
                )

        build_plan_drafts.assert_called_once_with(ACTOR, create_payload(), OBSERVED_AT)
        self.assertEqual(db.work_plans.update_calls, [])


if __name__ == "__main__":
    unittest.main()
