from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.work_plans.projection import (
    EffectiveSegment,
    NormalizedOperation,
    clip_cancellation,
    project_operations,
)


WINDOW_START = datetime(2026, 8, 16, tzinfo=UTC)
WINDOW_END = WINDOW_START + timedelta(days=2)


def at(minute: int) -> datetime:
    return WINDOW_START + timedelta(minutes=minute)


def operation(
    operation_type: str,
    start_minute: int,
    end_minute: int,
    sequence: int,
) -> NormalizedOperation:
    return NormalizedOperation(
        operation_id=f"operation-{sequence}",
        member_id="member-1",
        operation_type=operation_type,
        start_at=at(start_minute),
        end_at=at(end_minute),
        order_key=(1, sequence, f"operation-{sequence}"),
    )


def simplified(segments: list[EffectiveSegment]) -> list[tuple[str, int, int]]:
    return [
        (
            segment.state,
            int((segment.start_at - WINDOW_START).total_seconds() // 60),
            int((segment.end_at - WINDOW_START).total_seconds() // 60),
        )
        for segment in segments
    ]


class WorkPlanProjectionTests(unittest.TestCase):
    def test_overlapping_activation_merges_into_one_linear_segment(self) -> None:
        segments = project_operations(
            [
                operation("activate", 720, 1_440, 1),
                operation("activate", 540, 900, 2),
            ],
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )

        self.assertEqual(simplified(segments), [("active", 540, 1_440)])

    def test_cancel_activation_and_second_cancel_follow_last_operation(self) -> None:
        segments = project_operations(
            [
                operation("activate", 540, 1_440, 1),
                operation("cancel", 720, 900, 2),
                operation("activate", 660, 960, 3),
                operation("cancel", 720, 900, 4),
            ],
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )

        self.assertEqual(
            simplified(segments),
            [
                ("active", 540, 720),
                ("cancelled", 720, 900),
                ("active", 900, 1_440),
            ],
        )

    def test_later_activation_rejoins_cancelled_gap(self) -> None:
        segments = project_operations(
            [
                operation("activate", 540, 1_440, 1),
                operation("cancel", 720, 900, 2),
                operation("activate", 660, 960, 3),
            ],
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )

        self.assertEqual(simplified(segments), [("active", 540, 1_440)])

    def test_cross_midnight_activation_remains_one_segment(self) -> None:
        segments = project_operations(
            [operation("activate", 1_320, 1_800, 1)],
            window_start=WINDOW_START,
            window_end=WINDOW_END,
        )

        self.assertEqual(simplified(segments), [("active", 1_320, 1_800)])

    def test_cancellation_clips_to_each_green_fragment(self) -> None:
        green = [
            EffectiveSegment("active", at(540), at(720), "operation-1"),
            EffectiveSegment("active", at(780), at(1_080), "operation-2"),
        ]

        self.assertEqual(
            clip_cancellation(green, requested_start=at(480), requested_end=at(900)),
            [(at(540), at(720)), (at(780), at(900))],
        )

    def test_projection_rejects_unaligned_or_unknown_operations(self) -> None:
        with self.assertRaisesRegex(ValueError, "30 分钟"):
            project_operations(
                [operation("activate", 541, 600, 1)],
                window_start=WINDOW_START,
                window_end=WINDOW_END,
            )

        with self.assertRaisesRegex(ValueError, "操作类型"):
            project_operations(
                [operation("unknown", 540, 600, 1)],
                window_start=WINDOW_START,
                window_end=WINDOW_END,
            )


if __name__ == "__main__":
    unittest.main()
