from __future__ import annotations

import asyncio
import unittest
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import UUID

from bson import ObjectId

from app.modules.work_plans.domain import WorkPlanConflictError, WorkPlanRuleError, deterministic_plan_id
from app.modules.work_plans.schemas import (
    WorkPlanCreate,
    WorkPlanOperationCreate,
    WorkPlanOperationUpdate,
    WorkPlanUpdate,
)
from app.modules.work_plans import service as work_plan_service
from app.modules.work_plans.service import (
    WorkPlanAccessError,
    WorkPlanNotFoundError,
    WorkPlanPermissionError,
    cancel_work_plan,
    create_work_plans,
    list_my_work_plans,
    list_work_plan_schedule,
    set_member_priority,
    update_work_plan,
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


def operation_payload(**overrides: object) -> WorkPlanOperationCreate:
    values = {
        "operation_type": "activate",
        "anchor_dates": [date(2026, 8, 18)],
        "start_offset_minute": 9 * 60,
        "end_offset_minute": 18 * 60,
        "note": "original note",
        "idempotency_key": IDEMPOTENCY_KEY,
    }
    values.update(overrides)
    return WorkPlanOperationCreate.model_validate(values)


class FakeUpdateResult:
    def __init__(
        self,
        *,
        upserted_id: object | None = None,
        matched_count: int = 0,
    ) -> None:
        self.upserted_id = upserted_id
        self.matched_count = matched_count


class FakeInsertResult:
    def __init__(self, inserted_id: object) -> None:
        self.inserted_id = inserted_id


class FakeCursor:
    def __init__(
        self,
        documents: list[dict],
        *,
        iteration_error_after: int | None = None,
    ) -> None:
        self._documents = [deepcopy(document) for document in documents]
        self.sort_spec: list[tuple[str, int]] | None = None
        self.limit_value: int | None = None
        self._index = 0
        self._iteration_error_after = iteration_error_after

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
        if self._iteration_error_after == self._index:
            raise RuntimeError("cursor disconnected")
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
        self.find_one_and_update_calls: list[tuple[dict, dict]] = []
        self.find_calls: list[dict] = []
        self.insert_calls: list[dict] = []
        self.last_cursor: FakeCursor | None = None
        self.find_cursors: list[FakeCursor] = []
        self.fail_before_write: dict[object, int] = {}
        self.fail_after_write: dict[object, int] = {}
        self.fail_before_write_exceptions: dict[object, Exception] = {}
        self.find_one_errors: dict[object, int] = {}
        self.find_one_exceptions: dict[object, Exception] = {}
        self.find_one_calls: list[dict] = []
        self.find_iteration_error_after: int | None = None
        self.find_error: Exception | None = None
        self.insert_error: Exception | None = None
        self._update_lock = asyncio.Lock()

    async def update_one(self, query: dict, update: dict, *, upsert: bool = False) -> FakeUpdateResult:
        self.update_calls.append((deepcopy(query), deepcopy(update), upsert))
        query_key = self._query_key(query)
        await asyncio.sleep(0)
        async with self._update_lock:
            if query_key in self.fail_before_write_exceptions:
                raise self.fail_before_write_exceptions.pop(query_key)
            if self.fail_before_write.get(query_key, 0) > 0:
                self.fail_before_write[query_key] -= 1
                raise RuntimeError("database unavailable")

            existing = next(
                (
                    document
                    for document in self.documents.values()
                    if self._matches(document, query)
                ),
                None,
            )
            if existing is not None:
                self._apply_update(existing, update)
                return FakeUpdateResult(matched_count=1)
            if not upsert:
                return FakeUpdateResult()

            draft = deepcopy(update["$setOnInsert"])
            document_id = draft.setdefault("_id", ObjectId())
            self.documents[document_id] = draft
            if self.fail_after_write.get(query_key, 0) > 0:
                self.fail_after_write[query_key] -= 1
                raise RuntimeError("write acknowledgement lost")
            return FakeUpdateResult(upserted_id=document_id)

    async def find_one_and_update(
        self,
        query: dict,
        update: dict,
        *,
        return_document: object | None = None,
        upsert: bool = False,
    ) -> dict | None:
        del return_document
        self.find_one_and_update_calls.append((deepcopy(query), deepcopy(update)))
        await asyncio.sleep(0)
        async with self._update_lock:
            existing = next(
                (
                    document
                    for document in self.documents.values()
                    if self._matches(document, query)
                ),
                None,
            )
            if existing is None:
                if not upsert:
                    return None
                document = {
                    field: deepcopy(value)
                    for field, value in query.items()
                    if not field.startswith("$") and not isinstance(value, dict)
                }
                document.update(deepcopy(update.get("$setOnInsert", {})))
                self._apply_update(document, update)
                document_id = document.setdefault("_id", ObjectId())
                self.documents[document_id] = document
                return deepcopy(document)
            self._apply_update(existing, update)
            return deepcopy(existing)

    def find(self, query: dict, projection: dict | None = None) -> FakeCursor:
        self.find_calls.append(deepcopy(query))
        if self.find_error is not None:
            raise self.find_error
        documents = [
            document
            for document in self.documents.values()
            if self._matches(document, query)
        ]
        self.last_cursor = FakeCursor(
            documents,
            iteration_error_after=self.find_iteration_error_after,
        )
        self.find_cursors.append(self.last_cursor)
        return self.last_cursor

    async def count_documents(self, query: dict) -> int:
        return sum(1 for document in self.documents.values() if self._matches(document, query))

    async def find_one(self, query: dict) -> dict | None:
        self.find_one_calls.append(deepcopy(query))
        query_key = self._query_key(query)
        if query_key in self.find_one_exceptions:
            raise self.find_one_exceptions.pop(query_key)
        if self.find_one_errors.get(query_key, 0) > 0:
            self.find_one_errors[query_key] -= 1
            raise RuntimeError("find one disconnected")
        return next(
            (
                deepcopy(document)
                for document in self.documents.values()
                if self._matches(document, query)
            ),
            None,
        )

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
            if field == "$or":
                if not any(FakeCollection._matches(document, branch) for branch in expected):
                    return False
                continue
            actual: object = document
            field_exists = True
            for part in field.split("."):
                if isinstance(actual, dict) and part in actual:
                    actual = actual[part]
                elif isinstance(actual, list) and part.isdigit() and int(part) < len(actual):
                    actual = actual[int(part)]
                else:
                    field_exists = False
                    actual = None
                    break
            if not isinstance(expected, dict):
                if actual != expected:
                    return False
                continue
            if "$exists" in expected and field_exists != expected["$exists"]:
                return False
            if "$in" in expected and actual not in expected["$in"]:
                return False
            if "$gte" in expected and (actual is None or actual < expected["$gte"]):
                return False
            if "$lte" in expected and (actual is None or actual > expected["$lte"]):
                return False
            if "$gt" in expected and (actual is None or actual <= expected["$gt"]):
                return False
            if "$lt" in expected and (actual is None or actual >= expected["$lt"]):
                return False
            if "$ne" in expected and actual == expected["$ne"]:
                return False
        return True

    @staticmethod
    def _apply_update(document: dict, update: dict) -> None:
        for field, value in update.get("$setOnInsert", {}).items():
            document.setdefault(field, deepcopy(value))
        document.update(deepcopy(update.get("$set", {})))
        for field, value in update.get("$max", {}).items():
            if field not in document or document[field] < value:
                document[field] = deepcopy(value)
        for field in update.get("$unset", {}):
            document.pop(field, None)
        for field, value in update.get("$push", {}).items():
            document.setdefault(field, []).append(deepcopy(value))
        for field, criteria in update.get("$pull", {}).items():
            values = document.get(field)
            if not isinstance(values, list):
                continue
            document[field] = [
                item
                for item in values
                if not (
                    isinstance(item, dict)
                    and isinstance(criteria, dict)
                    and all(item.get(key) == value for key, value in criteria.items())
                )
            ]

    @staticmethod
    def _query_key(query: dict) -> object:
        if "_id" in query:
            return query["_id"]
        if "dedupe_key" in query:
            return query["dedupe_key"]
        return tuple(sorted(query.items()))


def fake_db(
    *,
    plans: list[dict] | None = None,
    users: list[dict] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        work_plans=FakeCollection(plans),
        work_plan_member_heads=FakeCollection(),
        users=FakeCollection(users),
        audit_logs=FakeCollection(),
    )


class WorkPlanCreateServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_created_operation_is_visible_after_naive_mongodb_round_trip(self) -> None:
        db = fake_db(users=[{"_id": ACTOR["_id"], "name": ACTOR["name"]}])

        created = await create_work_plans(
            db,
            actor=ACTOR,
            payload=operation_payload(),
            observed_at=OBSERVED_AT,
        )
        for document in db.work_plans.documents.values():
            for field in (
                "requested_start_at",
                "requested_end_at",
                "effective_start_at",
                "effective_end_at",
                "created_at",
            ):
                document[field] = document[field].replace(tzinfo=None)

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            schedule = await list_work_plan_schedule(
                db,
                range_name="all",
                member_ids=None,
                include_cancelled=False,
                observed_at=OBSERVED_AT,
            )
        history = await list_my_work_plans(db, actor=ACTOR, limit=20)

        self.assertEqual(created["results"][0]["outcome"], "created")
        self.assertEqual(len(schedule["segments"]), 1)
        self.assertEqual(schedule["segments"][0]["start_at"], "2026-08-18T01:00:00+00:00")
        self.assertEqual(history["items"][0]["id"], schedule["segments"][0]["winning_operation_id"])
        self.assertEqual(history["items"][0]["created_at"], "2026-08-15T00:00:00+00:00")

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
        create_calls = [call for call in db.work_plans.update_calls if call[2]]
        self.assertEqual(len(create_calls), 2)
        for query, update, upsert in create_calls:
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

    async def test_audit_is_idempotently_reconciled_for_created_records_and_replay(self) -> None:
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
        self.assertEqual(len(db.audit_logs.documents), 2)
        self.assertEqual(len(db.audit_logs.update_calls), 8)
        for audit in db.audit_logs.documents.values():
            self.assertEqual(audit["action"], "work_plan.create")
            self.assertEqual(audit["resource_type"], "work_plan")
            self.assertEqual(audit["resource_id"], audit["after"]["_id"])
            self.assertEqual(audit["actor_id"], ACTOR["_id"])
            self.assertEqual(
                audit["dedupe_key"],
                f"work_plan.create:{audit['resource_id']}",
            )

    async def test_audit_failure_does_not_change_result_and_replay_repairs_it(self) -> None:
        db = fake_db()
        plan_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, date(2026, 8, 18))
        dedupe_key = f"work_plan.create:{plan_id}"
        db.audit_logs.fail_before_write[dedupe_key] = 1

        with patch("app.modules.work_plans.service.logger.error") as logged:
            response = await create_work_plans(
                db,
                actor=ACTOR,
                payload=create_payload(),
                observed_at=OBSERVED_AT,
            )
            replay = await create_work_plans(
                db,
                actor=ACTOR,
                payload=create_payload(),
                observed_at=OBSERVED_AT,
            )

        self.assertEqual(response["results"][0]["outcome"], "created")
        self.assertEqual(replay["results"][0]["outcome"], "duplicate")
        self.assertIn("plan", response["results"][0])
        self.assertEqual(len(db.work_plans.documents), 1)
        self.assertEqual(len(db.audit_logs.documents), 1)
        self.assertEqual(len(db.audit_logs.update_calls), 3)
        logged.assert_called_once()
        logged_text = str(logged.call_args)
        self.assertIn(plan_id, logged_text)
        self.assertIn("RuntimeError", logged_text)
        self.assertNotIn("original note", logged_text)
        self.assertNotIn(ACTOR["name"], logged_text)

    async def test_create_audit_outage_persists_intent_until_explicit_repair(self) -> None:
        db = fake_db()
        plan_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, date(2026, 8, 18))
        dedupe_key = f"work_plan.create:{plan_id}"
        db.audit_logs.fail_before_write[dedupe_key] = 1

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(),
            observed_at=OBSERVED_AT,
        )

        stored = db.work_plans.documents[plan_id]
        self.assertEqual(response["results"][0]["outcome"], "created")
        self.assertNotIn("_audit_intents", response["results"][0]["plan"])
        self.assertIn("_audit_intents", stored)
        self.assertEqual(len(stored["_audit_intents"]), 1)
        self.assertEqual(stored["_audit_intents"][0]["dedupe_key"], dedupe_key)
        self.assertEqual(db.audit_logs.documents, {})
        self.assertTrue(hasattr(work_plan_service, "reconcile_work_plan_audit_intents"))

        repaired = await work_plan_service.reconcile_work_plan_audit_intents(db)

        self.assertEqual(repaired, 1)
        self.assertEqual(stored["_audit_intents"], [])
        self.assertEqual(len(db.audit_logs.documents), 1)
        audit = next(iter(db.audit_logs.documents.values()))
        self.assertIsNone(audit["before"])
        self.assertEqual(audit["after"]["_id"], plan_id)
        self.assertNotIn("_audit_intents", audit["after"])

    async def test_audit_reconciliation_loop_retries_after_transient_failure(self) -> None:
        reconcile = AsyncMock(side_effect=[RuntimeError("audit unavailable"), asyncio.CancelledError()])
        sleep = AsyncMock()

        with (
            patch.object(work_plan_service, "reconcile_work_plan_audit_intents", reconcile),
            patch.object(work_plan_service.asyncio, "sleep", sleep),
            self.assertLogs("app.modules.work_plans.service", level="ERROR") as captured,
        ):
            with self.assertRaises(asyncio.CancelledError):
                await work_plan_service.work_plan_audit_reconciliation_loop(
                    object(),
                    interval_seconds=0,
                )

        self.assertEqual(reconcile.await_count, 2)
        sleep.assert_awaited_once_with(0)
        self.assertIn("exception_type=RuntimeError", captured.output[0])
        self.assertNotIn("audit unavailable", captured.output[0])

    async def test_repair_deduplicates_audit_after_lost_write_acknowledgement(self) -> None:
        db = fake_db()
        plan_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, date(2026, 8, 18))
        dedupe_key = f"work_plan.create:{plan_id}"
        db.audit_logs.fail_after_write[dedupe_key] = 1

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(response["results"][0]["outcome"], "created")
        self.assertEqual(len(db.audit_logs.documents), 1)
        self.assertIn("_audit_intents", db.work_plans.documents[plan_id])
        self.assertEqual(len(db.work_plans.documents[plan_id]["_audit_intents"]), 1)

        repaired = await work_plan_service.reconcile_work_plan_audit_intents(db)

        self.assertEqual(repaired, 1)
        self.assertEqual(len(db.audit_logs.documents), 1)
        self.assertEqual(db.work_plans.documents[plan_id]["_audit_intents"], [])

    async def test_bulk_query_error_falls_back_to_per_id_readback(self) -> None:
        db = fake_db()
        plan_date = date(2026, 8, 18)
        plan_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, plan_date)
        db.work_plans.find_error = RuntimeError("query disconnected")

        with patch("app.modules.work_plans.service.logger.error") as logged:
            response = await create_work_plans(
                db,
                actor=ACTOR,
                payload=create_payload(dates=[plan_date]),
                observed_at=OBSERVED_AT,
            )

        self.assertEqual(response["results"][0]["outcome"], "created")
        self.assertEqual(db.work_plans.find_one_calls, [{"_id": plan_id}])
        self.assertIn("RuntimeError", str(logged.call_args))

    async def test_cursor_error_preserves_partial_results_and_reads_missing_ids(self) -> None:
        dates = [date(2026, 8, 18), date(2026, 8, 19)]
        ids = [deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, value) for value in dates]
        db = fake_db()
        db.work_plans.find_iteration_error_after = 1

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(dates=dates),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual([item["outcome"] for item in response["results"]], ["created", "created"])
        self.assertEqual(db.work_plans.find_one_calls, [{"_id": ids[1]}])

    async def test_acknowledged_create_uses_draft_when_all_readbacks_fail(self) -> None:
        plan_date = date(2026, 8, 18)
        plan_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, plan_date)
        db = fake_db()
        db.work_plans.find_iteration_error_after = 0
        db.work_plans.find_one_errors[plan_id] = 1

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(dates=[plan_date]),
            observed_at=OBSERVED_AT,
        )

        result = response["results"][0]
        self.assertEqual(result["outcome"], "created")
        self.assertEqual(result["plan"]["id"], plan_id)
        self.assertEqual(len(db.audit_logs.documents), 1)
        audit = next(iter(db.audit_logs.documents.values()))
        self.assertEqual(audit["after"]["_id"], plan_id)

    async def test_successful_replay_repairs_a_null_duplicate_audit_snapshot(self) -> None:
        plan_date = date(2026, 8, 18)
        plan_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, plan_date)
        durable_plan = {
            "_id": plan_id,
            "member_id": ACTOR["_id"],
            "member_name": ACTOR["name"],
            "plan_date": plan_date.isoformat(),
            "note": "durable original",
            "created_at": OBSERVED_AT,
        }
        db = fake_db(plans=[durable_plan])
        db.work_plans.find_error = RuntimeError("query disconnected")
        db.work_plans.find_one_errors[plan_id] = 1

        first = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(note="replayed content"),
            observed_at=OBSERVED_AT,
        )
        dedupe_key = f"work_plan.create:{plan_id}"
        audit = next(
            document
            for document in db.audit_logs.documents.values()
            if document["dedupe_key"] == dedupe_key
        )
        self.assertEqual(first["results"][0]["outcome"], "duplicate")
        self.assertIsNone(audit["after"])

        db.work_plans.find_error = None
        replay = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(note="replayed content"),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(replay["results"][0]["outcome"], "duplicate")
        self.assertEqual(audit["after"]["note"], "durable original")

    async def test_failure_logs_never_attach_secret_exception_details(self) -> None:
        secret = "SENTINEL_DATABASE_DOCUMENT_SECRET"
        plan_date = date(2026, 8, 18)
        plan_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, plan_date)
        dedupe_key = f"work_plan.create:{plan_id}"
        read_db = fake_db()
        read_db.work_plans.find_error = RuntimeError(secret)
        read_db.work_plans.find_one_exceptions[plan_id] = RuntimeError(secret)
        read_db.audit_logs.fail_before_write_exceptions[dedupe_key] = RuntimeError(secret)
        write_db = fake_db()
        write_db.work_plans.fail_before_write_exceptions[plan_id] = RuntimeError(secret)

        with self.assertLogs("app.modules.work_plans.service", level="ERROR") as captured:
            await create_work_plans(
                read_db,
                actor=ACTOR,
                payload=create_payload(dates=[plan_date]),
                observed_at=OBSERVED_AT,
            )
            await create_work_plans(
                write_db,
                actor=ACTOR,
                payload=create_payload(dates=[plan_date]),
                observed_at=OBSERVED_AT,
            )

        self.assertEqual(len(captured.records), 4)
        for record in captured.records:
            message = record.getMessage()
            self.assertNotIn(secret, message)
            self.assertIsNone(record.exc_info)
            self.assertTrue(message.startswith("work_plan_create_failure code="))
            self.assertIn("exception_type=RuntimeError", message)

    async def test_readback_fallback_is_bounded_to_five_ids(self) -> None:
        dates = [date(2026, 8, 18 + offset) for offset in range(5)]
        ids = [deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, value) for value in dates]
        db = fake_db()
        db.work_plans.find_error = RuntimeError("query disconnected")
        db.work_plans.find_one_errors = {plan_id: 1 for plan_id in ids}

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(dates=dates),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(len(db.work_plans.find_one_calls), 5)
        self.assertEqual([item["outcome"] for item in response["results"]], ["created"] * 5)

    async def test_uncertain_write_without_durable_record_is_failed(self) -> None:
        plan_date = date(2026, 8, 18)
        plan_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, plan_date)
        db = fake_db()
        db.work_plans.fail_before_write[plan_id] = 1

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(dates=[plan_date]),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(response["results"][0]["outcome"], "failed")
        self.assertEqual(db.audit_logs.documents, {})

    async def test_lost_write_ack_with_unavailable_readbacks_is_uncertain(self) -> None:
        plan_date = date(2026, 8, 18)
        plan_id = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, plan_date)
        db = fake_db()
        db.work_plans.fail_after_write[plan_id] = 1
        db.work_plans.find_error = RuntimeError("query disconnected")
        db.work_plans.find_one_errors[plan_id] = 1

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(dates=[plan_date]),
            observed_at=OBSERVED_AT,
        )

        result = response["results"][0]
        self.assertEqual(result["outcome"], "uncertain")
        self.assertRegex(result["error"], "无法确认|相同.*重试")
        self.assertFalse(response["duplicate_submission"])
        self.assertIn(plan_id, db.work_plans.documents)

        db.work_plans.find_error = None
        retry = await create_work_plans(
            db,
            actor=ACTOR,
            payload=create_payload(dates=[plan_date]),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(retry["results"][0]["outcome"], "duplicate")
        self.assertEqual(len(db.work_plans.documents), 1)

    async def test_concurrent_identical_submissions_create_once_without_overwrite(self) -> None:
        db = fake_db()

        first, second = await asyncio.gather(
            create_work_plans(
                db,
                actor=ACTOR,
                payload=create_payload(note="first content"),
                observed_at=OBSERVED_AT,
            ),
            create_work_plans(
                db,
                actor=ACTOR,
                payload=create_payload(
                    start_time="10:00",
                    end_time="17:00",
                    note="second content",
                ),
                observed_at=OBSERVED_AT.replace(hour=1),
            ),
        )

        self.assertEqual(len(db.work_plans.documents), 1)
        self.assertCountEqual(
            [first["results"][0]["outcome"], second["results"][0]["outcome"]],
            ["created", "duplicate"],
        )
        stored = next(iter(db.work_plans.documents.values()))
        self.assertIn(stored["note"], {"first content", "second content"})
        if stored["note"] == "first content":
            self.assertEqual(stored["start_minute"], 9 * 60)
            self.assertEqual(stored["end_minute"], 18 * 60)
            self.assertEqual(stored["updated_at"], OBSERVED_AT)
        else:
            self.assertEqual(stored["start_minute"], 10 * 60)
            self.assertEqual(stored["end_minute"], 17 * 60)
            self.assertEqual(stored["updated_at"], OBSERVED_AT.replace(hour=1))
        self.assertEqual(len(db.audit_logs.documents), 1)

    async def test_creation_rejects_synthetic_or_missing_browser_identity(self) -> None:
        invalid_actors = [
            {"_id": "service:1", "actor_type": "service"},
            {"_id": "api_token:1", "actor_type": "api_token"},
            {"_id": "member@example.com", "actor_type": None},
            {"_id": "", "actor_type": "user"},
            {"email": "fallback@example.com", "actor_type": "user"},
        ]
        for actor in invalid_actors:
            with self.subTest(actor=actor):
                db = fake_db()
                with self.assertRaisesRegex(WorkPlanAccessError, "浏览器"):
                    await create_work_plans(
                        db,
                        actor=actor,
                        payload=create_payload(),
                        observed_at=OBSERVED_AT,
                    )
                self.assertEqual(db.work_plans.update_calls, [])


class WorkPlanOperationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_member_commands_receive_monotonic_sequences(self) -> None:
        db = fake_db()

        first = await create_work_plans(
            db,
            actor=ACTOR,
            payload=operation_payload(),
            observed_at=OBSERVED_AT,
        )
        second = await create_work_plans(
            db,
            actor=ACTOR,
            payload=operation_payload(idempotency_key=UUID(int=2)),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(first["results"][0]["operation"]["member_sequence"], 1)
        self.assertEqual(second["results"][0]["operation"]["member_sequence"], 2)
        self.assertEqual(
            db.work_plan_member_heads.documents[ACTOR["_id"]]["last_sequence"],
            2,
        )

    async def test_cancel_persists_only_current_green_overlap(self) -> None:
        db = fake_db()
        await create_work_plans(
            db,
            actor=ACTOR,
            payload=operation_payload(),
            observed_at=OBSERVED_AT,
        )

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=operation_payload(
                operation_type="cancel",
                start_offset_minute=8 * 60,
                end_offset_minute=12 * 60,
                idempotency_key=UUID(int=2),
            ),
            observed_at=OBSERVED_AT,
        )

        operation = response["results"][0]["operation"]
        self.assertEqual(operation["requested_start_offset_minute"], 8 * 60)
        self.assertEqual(operation["requested_end_offset_minute"], 12 * 60)
        self.assertEqual(operation["effective_start_offset_minute"], 9 * 60)
        self.assertEqual(operation["effective_end_offset_minute"], 12 * 60)
        self.assertEqual(operation["member_sequence"], 2)

    async def test_cancel_accepts_naive_mongodb_datetimes_for_exact_request(self) -> None:
        db = fake_db()
        await create_work_plans(
            db,
            actor=ACTOR,
            payload=operation_payload(
                anchor_dates=[date(2026, 8, 17)],
                start_offset_minute=9 * 60,
                end_offset_minute=24 * 60,
            ),
            observed_at=OBSERVED_AT,
        )
        for document in db.work_plans.documents.values():
            for field in (
                "requested_start_at",
                "requested_end_at",
                "effective_start_at",
                "effective_end_at",
                "created_at",
            ):
                document[field] = document[field].replace(tzinfo=None)

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=operation_payload(
                operation_type="cancel",
                anchor_dates=[date(2026, 8, 17)],
                start_offset_minute=540,
                end_offset_minute=1440,
                note=None,
                idempotency_key=UUID("341b0035-391c-4926-90a4-4f0ff36c9752"),
            ),
            observed_at=OBSERVED_AT,
        )

        operation = response["results"][0]["operation"]
        self.assertEqual(operation["operation_type"], "cancel")
        self.assertEqual(operation["effective_start_offset_minute"], 540)
        self.assertEqual(operation["effective_end_offset_minute"], 1440)

    async def test_cancel_without_green_overlap_writes_nothing(self) -> None:
        db = fake_db()

        with self.assertRaisesRegex(WorkPlanRuleError, "没有可取消的工作计划"):
            await create_work_plans(
                db,
                actor=ACTOR,
                payload=operation_payload(
                    operation_type="cancel",
                    idempotency_key=UUID(int=2),
                ),
                observed_at=OBSERVED_AT,
            )

        self.assertEqual(db.work_plans.documents, {})

    async def test_idempotent_replay_returns_original_without_advancing_sequence(self) -> None:
        db = fake_db()
        payload = operation_payload()

        first = await create_work_plans(
            db,
            actor=ACTOR,
            payload=payload,
            observed_at=OBSERVED_AT,
        )
        replay = await create_work_plans(
            db,
            actor=ACTOR,
            payload=payload,
            observed_at=OBSERVED_AT + timedelta(minutes=30),
        )

        self.assertEqual(first["results"][0]["outcome"], "created")
        self.assertEqual(replay["results"][0]["outcome"], "duplicate")
        self.assertTrue(replay["duplicate_submission"])
        self.assertEqual(len(db.work_plans.documents), 1)
        self.assertEqual(
            db.work_plan_member_heads.documents[ACTOR["_id"]]["last_sequence"],
            1,
        )


class WorkPlanPriorityServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_priority_requires_manager_and_accepts_large_positive_integer(self) -> None:
        db = fake_db(users=[{"_id": "member", "name": "Member", "role": "viewer"}])

        with self.assertRaises(WorkPlanPermissionError):
            await set_member_priority(
                db,
                actor={**ACTOR, "role": "viewer", "actor_type": "user"},
                member_id="member",
                priority=10,
                observed_at=OBSERVED_AT,
            )

        result = await set_member_priority(
            db,
            actor={**ACTOR, "role": "admin", "actor_type": "user"},
            member_id="member",
            priority=10_000_000,
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(result["work_plan_priority"], 10_000_000)
        self.assertEqual(db.users.documents["member"]["work_plan_priority"], 10_000_000)

    async def test_priority_clear_is_persisted_as_explicit_null(self) -> None:
        db = fake_db(
            users=[
                {
                    "_id": "member",
                    "name": "Member",
                    "role": "viewer",
                    "work_plan_priority": 3,
                }
            ]
        )

        result = await set_member_priority(
            db,
            actor={**ACTOR, "role": "owner", "actor_type": "user"},
            member_id="member",
            priority=None,
            observed_at=OBSERVED_AT,
        )

        self.assertIsNone(result["work_plan_priority"])
        self.assertIn("work_plan_priority", db.users.documents["member"])
        self.assertIsNone(db.users.documents["member"]["work_plan_priority"])


class WorkPlanOperationLeaseServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_expired_lease_is_recovered_without_reusing_sequence(self) -> None:
        db = fake_db()
        db.work_plan_member_heads.documents[ACTOR["_id"]] = {
            "_id": ACTOR["_id"],
            "last_sequence": 4,
            "lease_owner": "abandoned",
            "lease_until": OBSERVED_AT - timedelta(seconds=1),
        }

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=operation_payload(),
            observed_at=OBSERVED_AT,
        )

        self.assertEqual(response["results"][0]["operation"]["member_sequence"], 5)
        head = db.work_plan_member_heads.documents[ACTOR["_id"]]
        self.assertEqual(head["last_sequence"], 5)
        self.assertNotIn("lease_owner", head)
        self.assertNotIn("lease_until", head)

    async def test_lost_acknowledgement_is_reconciled_from_readback(self) -> None:
        db = fake_db()
        operation_id = deterministic_plan_id(
            ACTOR["_id"],
            IDEMPOTENCY_KEY,
            date(2026, 8, 18),
        )
        db.work_plans.fail_after_write[operation_id] = 1

        response = await create_work_plans(
            db,
            actor=ACTOR,
            payload=operation_payload(),
            observed_at=OBSERVED_AT,
        )

        self.assertIn(response["results"][0]["outcome"], {"created", "duplicate"})
        self.assertEqual(response["results"][0]["operation"]["member_sequence"], 1)
        self.assertEqual(
            db.work_plan_member_heads.documents[ACTOR["_id"]]["last_sequence"],
            1,
        )


class WorkPlanScheduleServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_schedule_segments_include_sanitized_source_record(self) -> None:
        db = fake_db(
            users=[{"_id": "member-1", "name": "Member One"}],
            plans=[
                {
                    "_id": "activate-source",
                    "schema_version": 2,
                    "record_kind": "operation",
                    "member_id": "member-1",
                    "member_name": "Member One",
                    "operation_type": "activate",
                    "anchor_date": "2026-08-16",
                    "plan_date": "2026-08-16",
                    "requested_start_at": datetime(2026, 8, 16, 1, tzinfo=UTC),
                    "requested_end_at": datetime(2026, 8, 16, 9, tzinfo=UTC),
                    "effective_start_at": datetime(2026, 8, 16, 1, tzinfo=UTC),
                    "effective_end_at": datetime(2026, 8, 16, 9, tzinfo=UTC),
                    "requested_start_offset_minute": 9 * 60,
                    "requested_end_offset_minute": 17 * 60,
                    "effective_start_offset_minute": 9 * 60,
                    "effective_end_offset_minute": 17 * 60,
                    "member_sequence": 4,
                    "note": "可编辑操作",
                    "idempotency_key": "secret-key",
                    "created_at": datetime(2026, 8, 15, 10, tzinfo=UTC),
                    "_audit_intents": [{"action": "work_plan.create"}],
                }
            ],
        )

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="7d",
                member_ids=None,
                include_cancelled=False,
                observed_at=datetime(2026, 8, 15, 16, tzinfo=UTC),
            )

        record = response["segments"][0]["record"]
        self.assertEqual(record["id"], "activate-source")
        self.assertEqual(record["operation_type"], "activate")
        self.assertEqual(record["requested_start_offset_minute"], 9 * 60)
        self.assertEqual(record["member_sequence"], 4)
        self.assertEqual(record["note"], "可编辑操作")
        self.assertNotIn("_audit_intents", record)

    async def test_schedule_accepts_naive_utc_datetimes_returned_by_mongodb(self) -> None:
        db = fake_db(
            users=[{"_id": "member-1", "name": "Member One"}],
            plans=[
                {
                    "_id": "activate-naive-utc",
                    "schema_version": 2,
                    "record_kind": "operation",
                    "member_id": "member-1",
                    "member_name": "Member One",
                    "operation_type": "activate",
                    "anchor_date": "2026-08-16",
                    "plan_date": "2026-08-16",
                    "effective_start_at": datetime(2026, 8, 16, 1),
                    "effective_end_at": datetime(2026, 8, 16, 9),
                    "member_sequence": 1,
                    "created_at": datetime(2026, 8, 15, 10),
                }
            ],
        )

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="all",
                member_ids=None,
                include_cancelled=False,
                observed_at=OBSERVED_AT,
            )

        self.assertEqual(response["timezone"], "Asia/Shanghai")
        self.assertEqual(response["segments"][0]["start_at"], "2026-08-16T01:00:00+00:00")
        self.assertEqual(response["segments"][0]["end_at"], "2026-08-16T09:00:00+00:00")

    async def test_schedule_projects_cross_day_operations_into_continuous_segments(self) -> None:
        observed_at = datetime(2026, 8, 15, 16, 0, tzinfo=UTC)
        db = fake_db(
            users=[
                {
                    "_id": "member-1",
                    "name": "Member One",
                    "work_plan_priority": 3,
                }
            ],
            plans=[
                {
                    "_id": "activate-1",
                    "schema_version": 2,
                    "record_kind": "operation",
                    "member_id": "member-1",
                    "member_name": "Member One",
                    "operation_type": "activate",
                    "anchor_date": "2026-08-16",
                    "effective_start_at": datetime(2026, 8, 16, 14, tzinfo=UTC),
                    "effective_end_at": datetime(2026, 8, 17, 2, tzinfo=UTC),
                    "member_sequence": 1,
                    "created_at": datetime(2026, 8, 15, 10, tzinfo=UTC),
                },
                {
                    "_id": "cancel-1",
                    "schema_version": 2,
                    "record_kind": "operation",
                    "member_id": "member-1",
                    "member_name": "Member One",
                    "operation_type": "cancel",
                    "anchor_date": "2026-08-16",
                    "effective_start_at": datetime(2026, 8, 16, 18, tzinfo=UTC),
                    "effective_end_at": datetime(2026, 8, 16, 20, tzinfo=UTC),
                    "member_sequence": 2,
                    "created_at": datetime(2026, 8, 15, 11, tzinfo=UTC),
                },
            ],
        )

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="7d",
                member_ids=None,
                include_cancelled=False,
                observed_at=observed_at,
            )

        self.assertEqual(response["start_at"], "2026-08-15T16:00:00+00:00")
        self.assertEqual(response["end_at"], "2026-08-22T16:00:00+00:00")
        self.assertEqual(
            [
                (segment["state"], segment["start_at"], segment["end_at"])
                for segment in response["segments"]
            ],
            [
                ("active", "2026-08-16T14:00:00+00:00", "2026-08-16T18:00:00+00:00"),
                ("cancelled", "2026-08-16T18:00:00+00:00", "2026-08-16T20:00:00+00:00"),
                ("active", "2026-08-16T20:00:00+00:00", "2026-08-17T02:00:00+00:00"),
            ],
        )
        self.assertEqual(response["members"][0]["work_plan_priority"], 3)
        self.assertEqual(
            response["members"][0]["next_green_start"],
            "2026-08-16T14:00:00+00:00",
        )

    async def test_cancelled_legacy_work_is_retained_as_grey_projection(self) -> None:
        db = fake_db(
            users=[{"_id": "legacy", "name": "Legacy"}],
            plans=[
                {
                    "_id": "legacy-cancelled",
                    "member_id": "legacy",
                    "member_name": "Legacy",
                    "plan_date": "2026-08-16",
                    "plan_type": "work",
                    "start_minute": 9 * 60,
                    "end_minute": 12 * 60,
                    "status": "cancelled",
                    "is_cancelled": True,
                    "created_at": datetime(2026, 8, 15, 8, tzinfo=UTC),
                    "cancelled_at": datetime(2026, 8, 15, 9, tzinfo=UTC),
                }
            ],
        )

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="7d",
                member_ids=None,
                include_cancelled=False,
                observed_at=datetime(2026, 8, 15, 16, tzinfo=UTC),
            )

        self.assertEqual(len(response["segments"]), 1)
        self.assertEqual(response["segments"][0]["state"], "cancelled")
        self.assertEqual(response["segments"][0]["member_id"], "legacy")

    async def test_seven_day_schedule_includes_every_profile_and_current_collaboration_state(self) -> None:
        observed_at = datetime(2026, 8, 15, 16, 30, tzinfo=UTC)
        retained_seen_at = datetime(2026, 8, 15, 14, 0, tzinfo=UTC)
        db = fake_db(
            users=[
                {
                    "_id": "member-1",
                    "name": "Current Member Name",
                    "email": "member-1@example.com",
                    "role": "operator",
                    "status": "active",
                },
                {"_id": "quiet-member", "name": "Quiet Member", "status": "active"},
            ],
            plans=[
                {
                    "_id": "plan-current",
                    "member_id": "member-1",
                    "member_name": "Stale Embedded Name",
                    "plan_date": "2026-08-16",
                    "plan_type": "work",
                    "start_minute": 0,
                    "end_minute": 60,
                    "status": "active",
                    "is_cancelled": False,
                },
                {
                    "_id": "plan-last-day",
                    "member_id": "quiet-member",
                    "member_name": "Quiet Member",
                    "plan_date": "2026-08-22",
                    "plan_type": "work",
                    "start_minute": 9 * 60,
                    "end_minute": 18 * 60,
                    "status": "active",
                    "is_cancelled": False,
                },
                {
                    "_id": "plan-outside",
                    "member_id": "member-1",
                    "member_name": "Current Member Name",
                    "plan_date": "2026-08-23",
                    "plan_type": "work",
                    "start_minute": 9 * 60,
                    "end_minute": 18 * 60,
                    "status": "active",
                    "is_cancelled": False,
                },
            ],
        )

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(
                return_value={
                    "member-1": {
                        "is_online": False,
                        "active_clients": 0,
                        "last_seen_at": retained_seen_at,
                    }
                }
            ),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="7d",
                member_ids=None,
                include_cancelled=False,
                observed_at=observed_at,
            )

        self.assertEqual(response["start_date"], "2026-08-16")
        self.assertEqual(response["end_date"], "2026-08-22")
        self.assertEqual(response["timezone"], "Asia/Shanghai")
        self.assertEqual(response["observed_at"], observed_at.isoformat())
        self.assertEqual(
            [plan["id"] for plan in response["plans"]],
            ["plan-current", "plan-last-day"],
        )
        self.assertEqual(db.work_plans.last_cursor.limit_value, 4_000)
        self.assertEqual(len(response["members"]), 2)
        members = {member["member_id"]: member for member in response["members"]}
        self.assertEqual(members["member-1"]["member_name"], "Current Member Name")
        self.assertEqual(members["member-1"]["collaboration_status"], "planned_offline")
        self.assertEqual(members["member-1"]["last_seen_at"], retained_seen_at.isoformat())
        self.assertEqual(members["member-1"]["active_plan"]["id"], "plan-current")
        self.assertEqual(members["quiet-member"]["collaboration_status"], "offline")

    async def test_thirty_day_schedule_applies_member_filter_and_local_boundaries(self) -> None:
        observed_at = datetime(2026, 8, 15, 16, 0, tzinfo=UTC)
        db = fake_db(
            users=[
                {"_id": "selected", "name": "Selected"},
                {"_id": "other", "name": "Other"},
            ],
            plans=[
                {
                    "_id": "selected-last-day",
                    "member_id": "selected",
                    "member_name": "Selected",
                    "plan_date": "2026-09-14",
                    "plan_type": "work",
                    "start_minute": 540,
                    "end_minute": 1080,
                },
                {
                    "_id": "selected-outside",
                    "member_id": "selected",
                    "member_name": "Selected",
                    "plan_date": "2026-09-15",
                    "plan_type": "work",
                    "start_minute": 540,
                    "end_minute": 1080,
                },
                {
                    "_id": "other-plan",
                    "member_id": "other",
                    "member_name": "Other",
                    "plan_date": "2026-08-20",
                    "plan_type": "work",
                    "start_minute": 540,
                    "end_minute": 1080,
                },
            ],
        )

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="30d",
                member_ids=[" selected ", "selected"],
                include_cancelled=False,
                observed_at=observed_at,
            )

        self.assertEqual(response["start_date"], "2026-08-16")
        self.assertEqual(response["end_date"], "2026-09-14")
        self.assertEqual([member["member_id"] for member in response["members"]], ["selected"])
        self.assertEqual([plan["id"] for plan in response["plans"]], ["selected-last-day"])

    async def test_all_range_uses_matching_plan_bounds_and_preserves_deleted_member_name(self) -> None:
        observed_at = datetime(2026, 8, 15, tzinfo=UTC)
        plans = [
            {
                "_id": "older",
                "member_id": "known",
                "member_name": "Old Profile Name",
                "plan_date": "2026-08-01",
                "plan_type": "work",
                "start_minute": 540,
                "end_minute": 1080,
            },
            {
                "_id": "deleted-member-plan",
                "member_id": "deleted",
                "member_name": "Deleted Member Name",
                "plan_date": "2026-09-30",
                "plan_type": "work",
                "start_minute": 540,
                "end_minute": 1080,
            },
            {
                "_id": "cancelled-latest",
                "member_id": "known",
                "member_name": "Known Profile Name",
                "plan_date": "2026-10-01",
                "plan_type": "work",
                "start_minute": 540,
                "end_minute": 1080,
                "status": "cancelled",
            },
        ]
        db = fake_db(
            users=[{"_id": "known", "name": "Known Profile Name"}],
            plans=plans,
        )

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="all",
                member_ids=None,
                include_cancelled=False,
                observed_at=observed_at,
            )

        self.assertEqual(response["start_date"], "2026-08-01")
        self.assertEqual(response["end_date"], "2026-09-30")
        self.assertEqual([plan["id"] for plan in response["plans"]], ["older", "deleted-member-plan"])
        members = {member["member_id"]: member for member in response["members"]}
        self.assertEqual(members["known"]["member_name"], "Known Profile Name")
        self.assertEqual(members["deleted"]["member_name"], "Deleted Member Name")

        include_db = fake_db(
            users=[{"_id": "known", "name": "Known Profile Name"}],
            plans=plans,
        )
        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            included = await list_work_plan_schedule(
                include_db,
                range_name="all",
                member_ids=None,
                include_cancelled=True,
                observed_at=observed_at,
            )

        self.assertEqual(included["end_date"], "2026-10-01")
        self.assertEqual(
            [plan["id"] for plan in included["plans"]],
            ["older", "deleted-member-plan", "cancelled-latest"],
        )

    async def test_empty_all_range_uses_local_observed_date_and_rejects_unknown_ranges(self) -> None:
        observed_at = datetime(2026, 8, 15, 16, 0, tzinfo=UTC)
        db = fake_db(users=[{"_id": "quiet", "name": "Quiet"}])

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="all",
                member_ids=None,
                include_cancelled=False,
                observed_at=observed_at,
            )

        self.assertEqual(response["start_date"], "2026-08-16")
        self.assertEqual(response["end_date"], "2026-08-16")
        with self.assertRaisesRegex(ValueError, "7d.*30d.*all"):
            await list_work_plan_schedule(
                db,
                range_name="14d",
                member_ids=None,
                include_cancelled=False,
                observed_at=observed_at,
            )

    async def test_all_range_bounds_cover_records_beyond_the_return_limit(self) -> None:
        first_date = date(2020, 1, 1)
        plans = [
            {
                "_id": f"plan-{index:04d}",
                "member_id": "member",
                "member_name": "Member",
                "plan_date": (first_date + timedelta(days=index)).isoformat(),
                "plan_type": "work",
                "start_minute": 540,
                "end_minute": 1080,
            }
            for index in range(4_001)
        ]
        db = fake_db(users=[{"_id": "member", "name": "Member"}], plans=plans)

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="all",
                member_ids=None,
                include_cancelled=False,
                observed_at=OBSERVED_AT,
            )

        self.assertEqual(len(response["plans"]), 4_000)
        self.assertEqual(response["start_date"], plans[1]["plan_date"])
        self.assertEqual(response["end_date"], plans[-1]["plan_date"])
        self.assertEqual(response["total"], 4_001)
        self.assertTrue(response["has_more"])
        self.assertIsInstance(response["next_cursor"], str)
        self.assertEqual(response["plans"][0]["id"], "plan-0001")
        self.assertEqual(response["plans"][-1]["id"], "plan-4000")

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            older = await list_work_plan_schedule(
                db,
                range_name="all",
                member_ids=None,
                include_cancelled=False,
                cursor=response["next_cursor"],
                observed_at=OBSERVED_AT,
            )

        self.assertEqual([plan["id"] for plan in older["plans"]], ["plan-0000"])
        self.assertFalse(older["has_more"])
        self.assertIsNone(older["next_cursor"])

    async def test_schedule_metadata_includes_active_deleted_member_beyond_plan_limit(self) -> None:
        first_date = date(2010, 1, 1)
        plans = [
            {
                "_id": f"old-plan-{index:04d}",
                "member_id": "known",
                "member_name": "Known",
                "plan_date": (first_date + timedelta(days=index)).isoformat(),
                "plan_type": "work",
                "start_minute": 540,
                "end_minute": 1080,
            }
            for index in range(4_000)
        ]
        plans.append(
            {
                "_id": "active-after-cap",
                "member_id": "deleted-active",
                "member_name": "Deleted Active Member",
                "plan_date": "2026-08-15",
                "plan_type": "work",
                "start_minute": 0,
                "end_minute": 1_440,
            }
        )
        db = fake_db(users=[{"_id": "known", "name": "Known"}], plans=plans)

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="all",
                member_ids=None,
                include_cancelled=False,
                observed_at=OBSERVED_AT,
            )

        self.assertEqual(len(response["plans"]), 4_000)
        members = {member["member_id"]: member for member in response["members"]}
        self.assertEqual(members["deleted-active"]["member_name"], "Deleted Active Member")
        self.assertEqual(members["deleted-active"]["active_plan"]["id"], "active-after-cap")
        self.assertEqual(members["deleted-active"]["collaboration_status"], "planned_offline")
        self.assertLessEqual(
            max((cursor.limit_value or 0) for cursor in db.work_plans.find_cursors),
            4_001,
        )

    async def test_member_filter_does_not_synthesize_unknown_members_without_plans(self) -> None:
        db = fake_db(users=[{"_id": "known", "name": "Known"}])

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="7d",
                member_ids=["missing"],
                include_cancelled=False,
                observed_at=OBSERVED_AT,
            )

        self.assertEqual(response["members"], [])
        self.assertEqual(response["plans"], [])

    async def test_schedule_hides_users_merged_into_another_account(self) -> None:
        db = fake_db(
            users=[
                {"_id": "target", "name": "张可真", "status": "active"},
                {
                    "_id": "feishu-source",
                    "name": "张可真",
                    "status": "disabled",
                    "merged_into_user_id": "target",
                },
            ]
        )

        with patch(
            "app.modules.work_plans.service.list_member_presence_summaries",
            new=AsyncMock(return_value={}),
        ):
            response = await list_work_plan_schedule(
                db,
                range_name="7d",
                member_ids=None,
                include_cancelled=False,
                observed_at=OBSERVED_AT,
            )

        self.assertEqual([member["member_id"] for member in response["members"]], ["target"])


class WorkPlanHistoryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_history_serializes_naive_mongodb_datetimes_with_utc_offset(self) -> None:
        db = fake_db(
            plans=[
                {
                    "_id": "operation-naive-utc",
                    "schema_version": 2,
                    "record_kind": "operation",
                    "member_id": ACTOR["_id"],
                    "member_name": ACTOR["name"],
                    "operation_type": "activate",
                    "anchor_date": "2026-08-18",
                    "requested_start_at": datetime(2026, 8, 18, 1),
                    "requested_end_at": datetime(2026, 8, 18, 9),
                    "effective_start_at": datetime(2026, 8, 18, 1),
                    "effective_end_at": datetime(2026, 8, 18, 9),
                    "member_sequence": 1,
                    "created_at": datetime(2026, 8, 15, 9),
                }
            ]
        )

        response = await list_my_work_plans(db, actor=ACTOR, limit=20)

        item = response["items"][0]
        self.assertEqual(item["created_at"], "2026-08-15T09:00:00+00:00")
        self.assertEqual(item["effective_start_at"], "2026-08-18T01:00:00+00:00")

    async def test_v2_history_uses_anchor_date_and_keeps_operation_details(self) -> None:
        db = fake_db(
            plans=[
                {
                    "_id": "operation-2",
                    "schema_version": 2,
                    "record_kind": "operation",
                    "member_id": ACTOR["_id"],
                    "member_name": ACTOR["name"],
                    "operation_type": "cancel",
                    "anchor_date": "2026-08-18",
                    "requested_start_at": datetime(2026, 8, 18, tzinfo=UTC),
                    "requested_end_at": datetime(2026, 8, 18, 4, tzinfo=UTC),
                    "effective_start_at": datetime(2026, 8, 18, 1, tzinfo=UTC),
                    "effective_end_at": datetime(2026, 8, 18, 4, tzinfo=UTC),
                    "member_sequence": 2,
                    "created_at": datetime(2026, 8, 15, 9, tzinfo=UTC),
                }
            ]
        )

        response = await list_my_work_plans(db, actor=ACTOR, limit=20)

        self.assertEqual(response["items"][0]["plan_date"], "2026-08-18")
        self.assertEqual(response["items"][0]["operation_type"], "cancel")
        self.assertTrue(response["items"][0]["is_clipped"])
        self.assertEqual(response["items"][0]["history_state"], "cancelled")

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

        response = await list_my_work_plans(db, actor=ACTOR, limit=200)

        self.assertEqual(db.work_plans.find_calls, [{"member_id": ACTOR["_id"]}])
        self.assertEqual(
            db.work_plans.last_cursor.sort_spec,
            [("plan_date", -1), ("created_at", -1), ("_id", -1)],
        )
        self.assertEqual(db.work_plans.last_cursor.limit_value, 201)
        self.assertEqual(response["total"], 3)
        self.assertFalse(response["has_more"])
        self.assertIsNone(response["next_cursor"])
        self.assertEqual(
            [item["id"] for item in response["items"]],
            [str(newer_id), str(cancelled_id), "66bb00000000000000000004"],
        )
        self.assertTrue(response["items"][1]["is_cancelled"])
        self.assertEqual(response["items"][0]["created_at"], "2026-08-15T09:00:00+00:00")

    async def test_history_cursor_pages_through_every_record_without_duplicates(self) -> None:
        plans = [
            {
                "_id": f"plan-{index}",
                "member_id": ACTOR["_id"],
                "plan_date": f"2026-08-{20 - index:02d}",
                "created_at": datetime(2026, 8, 15, 9 - index, tzinfo=UTC),
                "status": "active",
                "is_cancelled": False,
            }
            for index in range(3)
        ]
        db = fake_db(plans=plans)

        first = await list_my_work_plans(db, actor=ACTOR, limit=2)
        second = await list_my_work_plans(
            db,
            actor=ACTOR,
            limit=2,
            cursor=first["next_cursor"],
        )

        self.assertEqual([item["id"] for item in first["items"]], ["plan-0", "plan-1"])
        self.assertEqual([item["id"] for item in second["items"]], ["plan-2"])
        self.assertEqual(first["total"], 3)
        self.assertTrue(first["has_more"])
        self.assertFalse(second["has_more"])
        self.assertEqual(
            {item["id"] for item in [*first["items"], *second["items"]]},
            {"plan-0", "plan-1", "plan-2"},
        )

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

    async def test_history_rejects_other_synthetic_and_missing_id_actors(self) -> None:
        invalid_actors = [
            {"_id": "service:1", "actor_type": "service"},
            {"_id": "member@example.com", "actor_type": None},
            {"_id": "", "actor_type": "user"},
            {"email": "fallback@example.com"},
        ]
        for actor in invalid_actors:
            with self.subTest(actor=actor):
                db = fake_db()
                with self.assertRaisesRegex(WorkPlanAccessError, "浏览器"):
                    await list_my_work_plans(db, actor=actor)
                self.assertEqual(db.work_plans.find_calls, [])


class WorkPlanOperationEditServiceTests(unittest.IsolatedAsyncioTestCase):
    async def _create_target(self, db: SimpleNamespace, actor: dict | None = None) -> dict:
        result = await create_work_plans(
            db,
            actor={**(actor or ACTOR), "actor_type": "user"},
            payload=operation_payload(),
            observed_at=OBSERVED_AT,
        )
        return result["results"][0]["operation"]

    async def test_edit_appends_compensation_and_replacement_without_mutating_target(self) -> None:
        db = fake_db()
        target = await self._create_target(db)
        target_before = deepcopy(db.work_plans.documents[target["id"]])
        payload = WorkPlanOperationUpdate(
            operation_type="activate",
            anchor_date=date(2026, 8, 18),
            start_offset_minute=10 * 60,
            end_offset_minute=19 * 60,
            note="replacement",
            idempotency_key=UUID("6d64155e-f997-49e9-80f0-132874447b72"),
            expected_member_sequence=1,
        )

        result = await update_work_plan(
            db,
            plan_id=target["id"],
            actor={**ACTOR, "actor_type": "user"},
            payload=payload,
            observed_at=OBSERVED_AT,
        )

        operations = [
            operation
            for item in result["results"]
            for operation in item["operations"]
        ]
        self.assertEqual([item["operation_type"] for item in operations], ["cancel", "activate"])
        self.assertEqual([item["member_sequence"] for item in operations], [2, 3])
        self.assertEqual(
            {item["compensates_operation_id"] for item in operations},
            {target["id"]},
        )
        self.assertEqual(
            {item["compensation_group_id"] for item in operations},
            {str(payload.idempotency_key)},
        )
        self.assertEqual(db.work_plans.documents[target["id"]], target_before)

    async def test_edit_replay_is_idempotent_and_stale_revision_writes_nothing(self) -> None:
        db = fake_db()
        target = await self._create_target(db)
        payload = WorkPlanOperationUpdate(
            operation_type="activate",
            anchor_date=date(2026, 8, 18),
            start_offset_minute=10 * 60,
            end_offset_minute=19 * 60,
            idempotency_key=UUID("a5749dd5-c332-4c5e-9b03-d3c83eab77af"),
            expected_member_sequence=1,
        )
        first = await update_work_plan(
            db,
            plan_id=target["id"],
            actor={**ACTOR, "actor_type": "user"},
            payload=payload,
            observed_at=OBSERVED_AT,
        )
        replay = await update_work_plan(
            db,
            plan_id=target["id"],
            actor={**ACTOR, "actor_type": "user"},
            payload=payload,
            observed_at=OBSERVED_AT,
        )

        self.assertFalse(first["duplicate_submission"])
        self.assertTrue(replay["duplicate_submission"])
        self.assertEqual(len(db.work_plans.documents), 3)

        stale = WorkPlanOperationUpdate(
            operation_type="activate",
            anchor_date=date(2026, 8, 18),
            start_offset_minute=11 * 60,
            end_offset_minute=20 * 60,
            idempotency_key=UUID("204d482d-f4e6-4767-a394-12854384e08c"),
            expected_member_sequence=1,
        )
        with self.assertRaisesRegex(WorkPlanConflictError, "刷新后重试"):
            await update_work_plan(
                db,
                plan_id=target["id"],
                actor={**ACTOR, "actor_type": "user"},
                payload=stale,
                observed_at=OBSERVED_AT,
            )
        self.assertEqual(len(db.work_plans.documents), 3)

    async def test_edit_cancellation_restores_old_interval_before_new_cancellation(self) -> None:
        db = fake_db()
        await self._create_target(db)
        cancelled = await create_work_plans(
            db,
            actor={**ACTOR, "actor_type": "user"},
            payload=operation_payload(
                operation_type="cancel",
                start_offset_minute=12 * 60,
                end_offset_minute=14 * 60,
                idempotency_key=UUID("99366685-edcb-41fc-a370-06a170249d4e"),
            ),
            observed_at=OBSERVED_AT,
        )
        target = cancelled["results"][0]["operation"]

        result = await update_work_plan(
            db,
            plan_id=target["id"],
            actor={**ACTOR, "actor_type": "user"},
            payload=WorkPlanOperationUpdate(
                operation_type="cancel",
                anchor_date=date(2026, 8, 18),
                start_offset_minute=13 * 60,
                end_offset_minute=15 * 60,
                idempotency_key=UUID("46ee88d9-5c73-46ec-aeee-58356a5a42ac"),
                expected_member_sequence=2,
            ),
            observed_at=OBSERVED_AT,
        )

        operations = [operation for item in result["results"] for operation in item["operations"]]
        self.assertEqual([item["operation_type"] for item in operations], ["activate", "cancel"])
        self.assertEqual(
            (operations[1]["effective_start_offset_minute"], operations[1]["effective_end_offset_minute"]),
            (13 * 60, 15 * 60),
        )


class WorkPlanMutationServiceTests(unittest.IsolatedAsyncioTestCase):
    def existing_plan(self, **overrides: object) -> dict:
        value = {
            "_id": "plan-1",
            "member_id": ACTOR["_id"],
            "member_name": ACTOR["name"],
            "plan_date": "2026-08-18",
            "plan_type": "work",
            "start_minute": 540,
            "end_minute": 1080,
            "note": None,
            "status": "active",
            "is_cancelled": False,
            "created_at": datetime(2026, 8, 1, tzinfo=UTC),
            "updated_at": datetime(2026, 8, 10, tzinfo=UTC),
        }
        value.update(overrides)
        return value

    async def test_member_cannot_cancel_another_members_plan(self) -> None:
        collection = SimpleNamespace(
            find_one=AsyncMock(return_value=self.existing_plan(member_id="other@example.com")),
            find_one_and_update=AsyncMock(),
        )
        db = SimpleNamespace(work_plans=collection)

        with self.assertRaisesRegex(WorkPlanPermissionError, "不能修改其他成员"):
            await cancel_work_plan(
                db,
                plan_id="plan-1",
                actor={**ACTOR, "actor_type": "user"},
                observed_at=OBSERVED_AT,
            )

        collection.find_one_and_update.assert_not_awaited()

    async def test_cancel_retry_returns_the_stored_cancelled_plan(self) -> None:
        cancelled_at = OBSERVED_AT - timedelta(minutes=5)
        cancelled = self.existing_plan(
            status="cancelled",
            is_cancelled=True,
            cancelled_at=cancelled_at,
            cancelled_by=ACTOR["_id"],
            updated_at=cancelled_at,
            updated_by=ACTOR["_id"],
        )
        collection = SimpleNamespace(
            find_one=AsyncMock(return_value=cancelled),
            find_one_and_update=AsyncMock(),
        )
        db = SimpleNamespace(work_plans=collection)

        result = await cancel_work_plan(
            db,
            plan_id="plan-1",
            actor={**ACTOR, "actor_type": "user"},
            observed_at=OBSERVED_AT,
        )

        self.assertTrue(result["is_cancelled"])
        self.assertEqual(result["cancelled_at"], cancelled_at.isoformat())
        collection.find_one_and_update.assert_not_awaited()

    async def test_cancel_race_returns_the_concurrently_cancelled_plan(self) -> None:
        existing = self.existing_plan()
        cancelled = self.existing_plan(
            status="cancelled",
            is_cancelled=True,
            cancelled_at=OBSERVED_AT,
            cancelled_by=ACTOR["_id"],
            updated_at=OBSERVED_AT,
            updated_by=ACTOR["_id"],
        )
        collection = SimpleNamespace(
            find_one=AsyncMock(side_effect=[existing, cancelled]),
            find_one_and_update=AsyncMock(return_value=None),
        )
        db = SimpleNamespace(work_plans=collection)

        result = await cancel_work_plan(
            db,
            plan_id="plan-1",
            actor={**ACTOR, "actor_type": "user"},
            observed_at=OBSERVED_AT,
        )

        self.assertTrue(result["is_cancelled"])
        self.assertEqual(collection.find_one.await_count, 2)

    async def test_cancel_retry_still_distinguishes_forbidden_and_missing_plans(self) -> None:
        cancelled = self.existing_plan(
            member_id="other@example.com",
            status="cancelled",
            is_cancelled=True,
        )
        forbidden_db = SimpleNamespace(
            work_plans=SimpleNamespace(
                find_one=AsyncMock(return_value=cancelled),
                find_one_and_update=AsyncMock(),
            )
        )
        missing_db = SimpleNamespace(
            work_plans=SimpleNamespace(
                find_one=AsyncMock(return_value=None),
                find_one_and_update=AsyncMock(),
            )
        )

        with self.assertRaises(WorkPlanPermissionError):
            await cancel_work_plan(
                forbidden_db,
                plan_id="plan-1",
                actor={**ACTOR, "actor_type": "user"},
                observed_at=OBSERVED_AT,
            )
        with self.assertRaises(WorkPlanNotFoundError):
            await cancel_work_plan(
                missing_db,
                plan_id="missing",
                actor={**ACTOR, "actor_type": "user"},
                observed_at=OBSERVED_AT,
            )

    async def test_admin_can_update_another_members_plan_and_audits_snapshots(self) -> None:
        existing = self.existing_plan(member_id="other@example.com", member_name="Other")
        updated = {**existing, "note": "Updated", "updated_by": "admin@example.com", "updated_at": OBSERVED_AT}
        collection = SimpleNamespace(
            find_one=AsyncMock(return_value=existing),
            find_one_and_update=AsyncMock(return_value=updated),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(work_plans=collection)
        payload = WorkPlanUpdate(note=" Updated ", expected_updated_at=existing["updated_at"])

        with patch("app.modules.work_plans.service.write_audit_log", new=AsyncMock()) as audit:
            result = await update_work_plan(
                db,
                plan_id="plan-1",
                actor={"_id": "admin@example.com", "name": "Admin", "role": "admin", "actor_type": "user"},
                payload=payload,
                observed_at=OBSERVED_AT,
            )

        self.assertEqual(result["updated_by"], "admin@example.com")
        query, update = collection.find_one_and_update.await_args.args[:2]
        self.assertNotIn("member_id", query)
        self.assertEqual(query["updated_at"], existing["updated_at"])
        self.assertEqual(update["$set"]["note"], "Updated")
        audit.assert_awaited_once()
        self.assertEqual(audit.await_args.kwargs["before"], existing)
        self.assertEqual(audit.await_args.kwargs["after"], updated)

    async def test_update_audit_outage_returns_update_and_replay_repairs_snapshots(self) -> None:
        existing = self.existing_plan()
        db = fake_db(plans=[existing])
        dedupe_key = f"work_plan.update:plan-1:{OBSERVED_AT.isoformat()}"
        db.audit_logs.insert_error = RuntimeError("audit unavailable")
        db.audit_logs.fail_before_write[dedupe_key] = 1

        try:
            result = await update_work_plan(
                db,
                plan_id="plan-1",
                actor={**ACTOR, "actor_type": "user"},
                payload=WorkPlanUpdate(
                    note="durable update",
                    expected_updated_at=existing["updated_at"],
                ),
                observed_at=OBSERVED_AT,
            )
        except RuntimeError as exc:
            self.fail(f"audit outage escaped after durable update: {type(exc).__name__}")

        stored = db.work_plans.documents["plan-1"]
        self.assertEqual(result["note"], "durable update")
        self.assertNotIn("_audit_intents", result)
        self.assertIn("_audit_intents", stored)
        self.assertEqual(len(stored["_audit_intents"]), 1)
        self.assertEqual(stored["_audit_intents"][0]["dedupe_key"], dedupe_key)
        self.assertTrue(hasattr(work_plan_service, "reconcile_work_plan_audit_intents"))

        repaired = await work_plan_service.reconcile_work_plan_audit_intents(db)

        self.assertEqual(repaired, 1)
        self.assertEqual(stored["_audit_intents"], [])
        audit = next(iter(db.audit_logs.documents.values()))
        self.assertIsNone(audit["before"]["note"])
        self.assertEqual(audit["after"]["note"], "durable update")
        self.assertNotIn("_audit_intents", audit["before"])
        self.assertNotIn("_audit_intents", audit["after"])

    async def test_member_update_uses_owner_filter_and_rejects_stale_timestamp(self) -> None:
        existing = self.existing_plan()
        collection = SimpleNamespace(
            find_one=AsyncMock(return_value=existing),
            find_one_and_update=AsyncMock(),
        )
        db = SimpleNamespace(work_plans=collection)
        payload = WorkPlanUpdate(
            note="new",
            expected_updated_at=datetime(2026, 8, 9, tzinfo=UTC),
        )

        with self.assertRaisesRegex(WorkPlanConflictError, "刷新后重试"):
            await update_work_plan(
                db,
                plan_id="plan-1",
                actor={**ACTOR, "actor_type": "user"},
                payload=payload,
                observed_at=OBSERVED_AT,
            )

        collection.find_one_and_update.assert_not_awaited()

    async def test_cancel_is_a_soft_cancel_with_actor_and_timestamps(self) -> None:
        existing = self.existing_plan()
        cancelled = {
            **existing,
            "status": "cancelled",
            "is_cancelled": True,
            "cancelled_at": OBSERVED_AT,
            "cancelled_by": ACTOR["_id"],
            "updated_at": OBSERVED_AT,
            "updated_by": ACTOR["_id"],
        }
        collection = SimpleNamespace(
            find_one=AsyncMock(return_value=existing),
            find_one_and_update=AsyncMock(return_value=cancelled),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(work_plans=collection)

        with patch("app.modules.work_plans.service.write_audit_log", new=AsyncMock()) as audit:
            result = await cancel_work_plan(
                db,
                plan_id="plan-1",
                actor={**ACTOR, "actor_type": "user"},
                observed_at=OBSERVED_AT,
            )

        self.assertTrue(result["is_cancelled"])
        query, update = collection.find_one_and_update.await_args.args[:2]
        self.assertEqual(query["member_id"], ACTOR["_id"])
        self.assertFalse(query["is_cancelled"])
        self.assertEqual(update["$set"]["cancelled_by"], ACTOR["_id"])
        self.assertEqual(update["$set"]["cancelled_at"], OBSERVED_AT)
        audit.assert_awaited_once()

    async def test_cancel_audit_outage_returns_cancelled_and_replay_repairs_snapshots(self) -> None:
        existing = self.existing_plan()
        db = fake_db(plans=[existing])
        dedupe_key = f"work_plan.cancel:plan-1:{OBSERVED_AT.isoformat()}"
        db.audit_logs.insert_error = RuntimeError("audit unavailable")
        db.audit_logs.fail_before_write[dedupe_key] = 1

        try:
            result = await cancel_work_plan(
                db,
                plan_id="plan-1",
                actor={**ACTOR, "actor_type": "user"},
                observed_at=OBSERVED_AT,
            )
        except RuntimeError as exc:
            self.fail(f"audit outage escaped after durable cancel: {type(exc).__name__}")

        stored = db.work_plans.documents["plan-1"]
        self.assertTrue(result["is_cancelled"])
        self.assertNotIn("_audit_intents", result)
        self.assertIn("_audit_intents", stored)
        self.assertEqual(len(stored["_audit_intents"]), 1)
        self.assertEqual(stored["_audit_intents"][0]["dedupe_key"], dedupe_key)

        repaired = await work_plan_service.reconcile_work_plan_audit_intents(db)

        self.assertEqual(repaired, 1)
        self.assertEqual(stored["_audit_intents"], [])
        audit = next(iter(db.audit_logs.documents.values()))
        self.assertFalse(audit["before"]["is_cancelled"])
        self.assertTrue(audit["after"]["is_cancelled"])
        self.assertEqual(audit["after"]["cancelled_by"], ACTOR["_id"])

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
