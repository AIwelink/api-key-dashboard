from __future__ import annotations

import unittest
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

from pydantic import ValidationError

from app.modules.work_plans.domain import (
    WorkPlanConflictError,
    WorkPlanRuleError,
    build_plan_drafts,
    collaboration_status,
    deterministic_plan_id,
    is_plan_manager,
    time_to_minute,
    validate_update,
)
from app.modules.work_plans.schemas import WorkPlanCreate, WorkPlanUpdate


IDEMPOTENCY_KEY = UUID("d4426fd9-a2fd-44c0-b47e-f36ae16c9d19")
OBSERVED_AT = datetime(2026, 8, 15, tzinfo=UTC)
ACTOR = {
    "_id": "member@example.com",
    "name": "Member Name",
    "role": "operator",
}


def create_payload(**overrides: object) -> WorkPlanCreate:
    values = {
        "plan_type": "work",
        "dates": [date(2026, 8, 18)],
        "start_time": "09:00",
        "end_time": "18:00",
        "note": None,
        "idempotency_key": IDEMPOTENCY_KEY,
    }
    values.update(overrides)
    return WorkPlanCreate.model_validate(values)


def existing_plan(**overrides: object) -> dict:
    values = {
        "_id": "plan-1",
        "member_id": ACTOR["_id"],
        "member_name": ACTOR["name"],
        "plan_date": "2026-08-18",
        "plan_type": "work",
        "start_minute": 9 * 60,
        "end_minute": 18 * 60,
        "note": "existing note",
        "status": "active",
        "is_cancelled": False,
        "updated_at": datetime(2026, 8, 14, 12, tzinfo=UTC),
    }
    values.update(overrides)
    return values


class WorkPlanTimeRuleTests(unittest.TestCase):
    def test_schema_parses_valid_half_hour_times_and_converts_to_minutes(self) -> None:
        payload = create_payload(start_time="09:30", end_time="18:00")

        self.assertEqual(payload.start_time, time(9, 30))
        self.assertEqual(time_to_minute(payload.start_time), 570)

    def test_time_conversion_rejects_seconds_microseconds_and_other_minutes(self) -> None:
        invalid_values = [
            time(9, 0, 1),
            time(9, 0, 0, 1),
            time(9, 15),
        ]

        for invalid_value in invalid_values:
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaisesRegex(WorkPlanRuleError, "30"):
                    time_to_minute(invalid_value)

    def test_schema_and_domain_reject_timezone_aware_times(self) -> None:
        for field_name in ("start_time", "end_time"):
            with self.subTest(schema_field=field_name):
                with self.assertRaisesRegex(ValidationError, "时区"):
                    create_payload(**{field_name: "09:00Z"})

        with self.assertRaisesRegex(ValidationError, "时区"):
            WorkPlanUpdate(start_time="09:00+08:00")
        with self.assertRaisesRegex(WorkPlanRuleError, "时区"):
            time_to_minute(time(9, tzinfo=UTC))

    def test_create_requires_end_time_later_than_start_time(self) -> None:
        for end_time in ("09:00", "08:30"):
            with self.subTest(end_time=end_time):
                with self.assertRaisesRegex(WorkPlanRuleError, "结束时间"):
                    build_plan_drafts(
                        ACTOR,
                        create_payload(start_time="09:00", end_time=end_time),
                        OBSERVED_AT,
                    )

    def test_end_time_accepts_midnight_boundary_without_allowing_it_as_a_start(self) -> None:
        draft = build_plan_drafts(
            ACTOR,
            create_payload(start_time="23:30", end_time="24:00"),
            OBSERVED_AT,
        )[0]

        self.assertEqual(draft["start_minute"], 1_410)
        self.assertEqual(draft["end_minute"], 1_440)
        with self.assertRaises(ValidationError):
            create_payload(start_time="24:00", end_time="24:00")


class WorkPlanCollaborationStatusTests(unittest.TestCase):
    def test_online_member_in_current_work_plan_keeps_plan_context(self) -> None:
        self.assertEqual(
            collaboration_status(
                is_online=True,
                active_plan={"plan_type": "work"},
            ),
            "in_plan",
        )

    def test_temporary_unavailable_has_priority_while_offline(self) -> None:
        self.assertEqual(
            collaboration_status(
                is_online=False,
                active_plan={"plan_type": "temporary_unavailable"},
            ),
            "temporary_unavailable",
        )

    def test_offline_member_in_current_work_plan_is_neutrally_planned_offline(self) -> None:
        self.assertEqual(
            collaboration_status(
                is_online=False,
                active_plan={"plan_type": "work"},
            ),
            "planned_offline",
        )

    def test_online_and_unscheduled_offline_states_are_distinct(self) -> None:
        self.assertEqual(collaboration_status(is_online=True, active_plan=None), "online")
        self.assertEqual(collaboration_status(is_online=False, active_plan=None), "offline")


class WorkPlanCreateRuleTests(unittest.TestCase):
    def test_dates_are_normalized_chronologically(self) -> None:
        payload = create_payload(
            dates=[date(2026, 8, 20), date(2026, 8, 18), date(2026, 8, 19)]
        )

        drafts = build_plan_drafts(ACTOR, payload, OBSERVED_AT)

        self.assertEqual(
            [draft["plan_date"] for draft in drafts],
            ["2026-08-18", "2026-08-19", "2026-08-20"],
        )

    def test_duplicate_dates_are_rejected_instead_of_silently_removed(self) -> None:
        payload = create_payload(dates=[date(2026, 8, 18), date(2026, 8, 18)])

        with self.assertRaisesRegex(WorkPlanRuleError, "日期.*重复"):
            build_plan_drafts(ACTOR, payload, OBSERVED_AT)

    def test_one_to_five_dates_are_accepted(self) -> None:
        for count in range(1, 6):
            with self.subTest(count=count):
                dates = [date(2026, 8, 18) + timedelta(days=index) for index in range(count)]
                drafts = build_plan_drafts(ACTOR, create_payload(dates=dates), OBSERVED_AT)
                self.assertEqual(len(drafts), count)

    def test_six_dates_are_rejected_with_range_guidance(self) -> None:
        dates = [date(2026, 8, 18) + timedelta(days=index) for index in range(6)]

        with self.assertRaisesRegex(
            WorkPlanRuleError,
            "一次最多添加 5 天计划，请缩小日期范围",
        ):
            build_plan_drafts(ACTOR, create_payload(dates=dates), OBSERVED_AT)

    def test_temporary_unavailable_requires_exactly_one_date(self) -> None:
        payload = create_payload(
            plan_type="temporary_unavailable",
            dates=[date(2026, 8, 18), date(2026, 8, 19)],
        )

        with self.assertRaisesRegex(WorkPlanRuleError, "只能选择 1 个日期"):
            build_plan_drafts(ACTOR, payload, OBSERVED_AT)

    def test_temporary_unavailable_start_is_checked_in_shanghai_time(self) -> None:
        observed_at = datetime(2026, 8, 15, tzinfo=UTC)
        too_soon = create_payload(
            plan_type="temporary_unavailable",
            dates=[date(2026, 8, 15)],
            start_time="08:30",
            end_time="09:30",
        )

        with self.assertRaisesRegex(WorkPlanRuleError, "至少晚于当前时间 1 小时"):
            build_plan_drafts(ACTOR, too_soon, observed_at)

        exactly_one_hour = create_payload(
            plan_type="temporary_unavailable",
            dates=[date(2026, 8, 15)],
            start_time="09:00",
            end_time="09:30",
        )
        drafts = build_plan_drafts(ACTOR, exactly_one_hour, observed_at)
        self.assertEqual(len(drafts), 1)

    def test_deterministic_ids_are_stable_and_distinct_per_date(self) -> None:
        first = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, date(2026, 8, 18))
        repeated = deterministic_plan_id(ACTOR["_id"], str(IDEMPOTENCY_KEY), date(2026, 8, 18))
        next_date = deterministic_plan_id(ACTOR["_id"], IDEMPOTENCY_KEY, date(2026, 8, 19))

        self.assertEqual(first, repeated)
        self.assertNotEqual(first, next_date)
        self.assertEqual(first, "fff97f7c-f2bd-5e7c-b9e8-b88bbb2c1825")

    def test_only_owner_and_admin_are_plan_managers(self) -> None:
        for role in ("owner", "admin"):
            with self.subTest(role=role):
                self.assertTrue(is_plan_manager({"role": role}))
        for role in ("maintainer", "operator", "viewer", "Owner", None):
            with self.subTest(role=role):
                self.assertFalse(is_plan_manager({"role": role}))

    def test_create_schema_rejects_client_member_identity(self) -> None:
        with self.assertRaises(ValidationError):
            create_payload(member_id="forged-member")

    def test_drafts_use_actor_identity_and_name(self) -> None:
        payload = WorkPlanCreate.model_validate(
            {
                "plan_type": "work",
                "dates": ["2026-08-18"],
                "start_time": "09:00",
                "end_time": "18:00",
                "note": "  client note  ",
                "idempotency_key": str(IDEMPOTENCY_KEY),
            }
        )

        draft = build_plan_drafts(ACTOR, payload, OBSERVED_AT)[0]

        self.assertEqual(draft["member_id"], ACTOR["_id"])
        self.assertEqual(draft["member_name"], ACTOR["name"])
        self.assertEqual(draft["created_by"], ACTOR["_id"])
        self.assertEqual(draft["updated_by"], ACTOR["_id"])
        self.assertEqual(draft["created_at"], OBSERVED_AT)
        self.assertEqual(draft["updated_at"], OBSERVED_AT)
        self.assertEqual(draft["note"], "client note")
        self.assertEqual(draft["status"], "active")
        self.assertFalse(draft["is_cancelled"])
        self.assertEqual(draft["idempotency_key"], str(IDEMPOTENCY_KEY))

    def test_blank_create_note_is_stored_as_none(self) -> None:
        draft = build_plan_drafts(ACTOR, create_payload(note="   "), OBSERVED_AT)[0]

        self.assertIsNone(draft["note"])

    def test_create_note_is_trimmed_before_max_length_validation(self) -> None:
        payload = create_payload(note=f"  {'x' * 500}  ")

        self.assertEqual(payload.note, "x" * 500)
        self.assertEqual(build_plan_drafts(ACTOR, payload, OBSERVED_AT)[0]["note"], "x" * 500)

        with self.assertRaises(ValidationError):
            create_payload(note=f"  {'x' * 501}  ")


class WorkPlanUpdateRuleTests(unittest.TestCase):
    def test_update_merges_omitted_fields_and_excludes_immutable_fields(self) -> None:
        payload = WorkPlanUpdate(start_time="10:00")

        updates = validate_update(existing_plan(), payload, OBSERVED_AT)

        self.assertEqual(
            updates,
            {
                "plan_type": "work",
                "start_minute": 10 * 60,
                "end_minute": 18 * 60,
                "note": "existing note",
                "updated_at": OBSERVED_AT,
            },
        )
        self.assertNotIn("plan_date", updates)
        self.assertNotIn("member_id", updates)

    def test_update_schema_rejects_immutable_and_unknown_fields(self) -> None:
        for field_name, value in (
            ("plan_date", "2026-09-01"),
            ("member_id", "forged-member"),
            ("unknown", True),
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    WorkPlanUpdate.model_validate({"note": "changed", field_name: value})

    def test_update_requires_at_least_one_mutable_field(self) -> None:
        invalid_payloads = (
            {},
            {"expected_updated_at": datetime(2026, 8, 14, 12, tzinfo=UTC)},
        )

        for values in invalid_payloads:
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValidationError, "至少提供一个可更新字段"):
                    WorkPlanUpdate.model_validate(values)

    def test_update_rejects_null_required_mutable_values_but_allows_note_clear(self) -> None:
        for field_name in ("plan_type", "start_time", "end_time"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(ValidationError, "不能为 null"):
                    WorkPlanUpdate.model_validate({field_name: None})

        payload = WorkPlanUpdate(note=None)
        self.assertIn("note", payload.model_fields_set)

    def test_update_note_is_trimmed_before_max_length_validation(self) -> None:
        payload = WorkPlanUpdate(note=f"  {'x' * 500}  ")

        self.assertEqual(payload.note, "x" * 500)
        with self.assertRaises(ValidationError):
            WorkPlanUpdate(note=f"  {'x' * 501}  ")

    def test_update_rejects_invalid_merged_time_window(self) -> None:
        payload = WorkPlanUpdate(end_time="08:30")

        with self.assertRaisesRegex(WorkPlanRuleError, "结束时间"):
            validate_update(existing_plan(), payload, OBSERVED_AT)

    def test_temporary_unavailable_note_and_end_updates_ignore_elapsed_lead_time(self) -> None:
        existing = existing_plan(
            plan_date="2026-08-15",
            plan_type="temporary_unavailable",
            start_minute=8 * 60,
            end_minute=9 * 60,
        )

        note_updates = validate_update(existing, WorkPlanUpdate(note="changed"), OBSERVED_AT)
        end_updates = validate_update(existing, WorkPlanUpdate(end_time="09:30"), OBSERVED_AT)

        self.assertEqual(note_updates["note"], "changed")
        self.assertEqual(end_updates["end_minute"], 9 * 60 + 30)

    def test_unchanged_temporary_full_form_update_ignores_elapsed_lead_time(self) -> None:
        existing = existing_plan(
            plan_date="2026-08-15",
            plan_type="temporary_unavailable",
            start_minute=8 * 60,
            end_minute=9 * 60,
        )
        payload = WorkPlanUpdate(
            plan_type="temporary_unavailable",
            start_time="08:00",
            end_time="09:00",
            note="changed",
            expected_updated_at=existing["updated_at"],
        )

        updates = validate_update(existing, payload, OBSERVED_AT)

        self.assertEqual(updates["plan_type"], "temporary_unavailable")
        self.assertEqual(updates["start_minute"], 8 * 60)
        self.assertEqual(updates["end_minute"], 9 * 60)
        self.assertEqual(updates["note"], "changed")

    def test_temporary_unavailable_start_or_type_change_rechecks_lead_time(self) -> None:
        existing_temporary = existing_plan(
            plan_date="2026-08-15",
            plan_type="temporary_unavailable",
            start_minute=8 * 60,
            end_minute=9 * 60,
        )
        existing_work = existing_plan(
            plan_date="2026-08-15",
            plan_type="work",
            start_minute=8 * 60,
            end_minute=9 * 60,
        )

        with self.assertRaisesRegex(WorkPlanRuleError, "至少晚于当前时间 1 小时"):
            validate_update(existing_temporary, WorkPlanUpdate(start_time="08:30"), OBSERVED_AT)
        with self.assertRaisesRegex(WorkPlanRuleError, "至少晚于当前时间 1 小时"):
            validate_update(
                existing_work,
                WorkPlanUpdate(plan_type="temporary_unavailable"),
                OBSERVED_AT,
            )

    def test_update_normalizes_blank_note_and_preserves_omitted_note(self) -> None:
        cleared = validate_update(existing_plan(), WorkPlanUpdate(note="   "), OBSERVED_AT)
        preserved = validate_update(existing_plan(), WorkPlanUpdate(start_time="09:30"), OBSERVED_AT)

        self.assertIsNone(cleared["note"])
        self.assertEqual(preserved["note"], "existing note")

    def test_update_rejects_cancelled_records(self) -> None:
        for cancelled in (
            existing_plan(is_cancelled=True),
            existing_plan(status="cancelled"),
        ):
            with self.subTest(cancelled=cancelled):
                with self.assertRaisesRegex(WorkPlanConflictError, "已取消"):
                    validate_update(cancelled, WorkPlanUpdate(note="changed"), OBSERVED_AT)

    def test_update_rejects_stale_expected_timestamp(self) -> None:
        payload = WorkPlanUpdate(
            note="changed",
            expected_updated_at=datetime(2026, 8, 14, 11, tzinfo=UTC),
        )

        with self.assertRaisesRegex(WorkPlanConflictError, "已被更新"):
            validate_update(existing_plan(), payload, OBSERVED_AT)

    def test_update_accepts_matching_expected_timestamp_without_persisting_it(self) -> None:
        expected = datetime(2026, 8, 14, 12, tzinfo=UTC)
        payload = WorkPlanUpdate(note=" changed ", expected_updated_at=expected)

        updates = validate_update(existing_plan(), payload, OBSERVED_AT)

        self.assertEqual(updates["note"], "changed")
        self.assertNotIn("expected_updated_at", updates)


if __name__ == "__main__":
    unittest.main()
