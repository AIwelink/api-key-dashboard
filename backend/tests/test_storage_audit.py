from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta

from app.modules.system.storage_audit import (
    simulate_account_change_batches,
    summarize_history_storage_estimate,
    summarize_storage_stats,
)


class StorageAuditSummaryTests(unittest.TestCase):
    def test_summary_orders_collections_and_calculates_share(self) -> None:
        summary = summarize_storage_stats(
            [
                {"name": "small", "count": 2, "size": 100, "storage_size": 80, "index_size": 20},
                {"name": "large", "count": 4, "size": 900, "storage_size": 300, "index_size": 40},
            ]
        )

        self.assertEqual(summary["logical_size"], 1000)
        self.assertEqual(summary["storage_size"], 380)
        self.assertEqual(summary["index_size"], 60)
        self.assertEqual(summary["collections"][0]["name"], "large")
        self.assertEqual(summary["collections"][0]["logical_share_percent"], 90.0)
        self.assertEqual(summary["collections"][0]["average_document_bytes"], 225.0)

    def test_history_estimate_includes_daily_checkpoint_overhead(self) -> None:
        summary = summarize_history_storage_estimate(
            old_document_count=100,
            old_bson_bytes=10_000,
            new_document_count=5,
            new_bson_bytes=1_000,
            elapsed_hours=6,
            checkpoint_document_count=2,
            checkpoint_bson_bytes=200,
        )

        self.assertEqual(summary["observed_document_reduction_percent"], 95.0)
        self.assertEqual(summary["observed_byte_reduction_percent"], 90.0)
        self.assertEqual(summary["projected_old_30d_bytes"], 1_200_000)
        self.assertEqual(summary["projected_change_30d_bytes"], 120_000)
        self.assertEqual(summary["projected_checkpoint_30d_bytes"], 6_000)
        self.assertEqual(summary["projected_new_30d_bytes"], 126_000)
        self.assertEqual(summary["projected_30d_byte_reduction_percent"], 89.5)
        self.assertEqual(summary["projected_old_retained_bytes"], 560_000)
        self.assertEqual(summary["projected_checkpoint_retained_bytes"], 73_000)
        self.assertEqual(summary["projected_new_retained_bytes"], 193_000)
        self.assertEqual(summary["projected_retained_byte_reduction_percent"], 65.54)

    def test_probe_samples_replay_to_changed_new_values_only(self) -> None:
        cutoff = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
        samples = [
            {
                "site_id": "api-5001",
                "probe_run_id": "warmup",
                "identity_id": "api-5001:user@example.com",
                "remote_account_id": 953,
                "sampled_at": cutoff - timedelta(minutes=3),
                "usage_snapshot": {"codex_5h_used_percent": 40},
                "subscription_snapshot": {"plan_type": "plus"},
            },
            {
                "site_id": "api-5001",
                "probe_run_id": "same",
                "identity_id": "api-5001:user@example.com",
                "remote_account_id": 953,
                "sampled_at": cutoff + timedelta(minutes=1),
                "usage_snapshot": {"codex_5h_used_percent": 40},
                "subscription_snapshot": {"plan_type": "plus"},
            },
            {
                "site_id": "api-5001",
                "probe_run_id": "changed",
                "identity_id": "api-5001:user@example.com",
                "remote_account_id": 953,
                "sampled_at": cutoff + timedelta(minutes=4),
                "usage_snapshot": {"codex_5h_used_percent": 42},
                "subscription_snapshot": {"plan_type": "plus"},
            },
        ]

        result = simulate_account_change_batches(samples, cutoff=cutoff)

        self.assertEqual(result["changed_accounts"], 1)
        self.assertEqual(result["changed_fields"], 1)
        self.assertEqual(len(result["documents"]), 1)
        self.assertEqual(
            result["documents"][0]["entries"][0]["changes"],
            {"usage.codex_5h_used_percent": 42},
        )


if __name__ == "__main__":
    unittest.main()
