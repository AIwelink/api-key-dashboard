from __future__ import annotations

import unittest
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.modules.risk.domain import IpObservation


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


class RiskRepositoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_cursor_success_clears_error_and_advances_source_identity(self) -> None:
        from app.modules.risk.repository import save_cursor_success

        connection = _FakeConnection([[]])
        await save_cursor_success(
            connection,
            site_id="aiwelink",
            source_stream="audit_logs",
            last_source_id=101,
            last_source_created_at=NOW,
            latest_observed_at=NOW,
            rows_read=12,
            succeeded_at=NOW,
        )

        sql, parameters = connection.calls[0]
        self.assertIn("ON CONFLICT (site_id, source_stream) DO UPDATE", sql)
        self.assertIn("last_error_code = ''", sql)
        self.assertEqual(parameters["last_source_id"], 101)
        self.assertEqual(parameters["rows_read"], 12)

    async def test_cursor_failure_keeps_existing_source_id(self) -> None:
        from app.modules.risk.repository import save_cursor_error

        connection = _FakeConnection([[]])
        await save_cursor_error(
            connection,
            site_id="aiwelink",
            source_stream="usage_logs",
            error_code="TimeoutError",
            error_message="source timed out",
            failed_at=NOW,
        )

        sql, parameters = connection.calls[0]
        self.assertIn("last_source_id", sql)
        self.assertIn("DO UPDATE SET", sql)
        update_sql = sql.split("DO UPDATE SET", 1)[1]
        self.assertNotIn("last_source_id = EXCLUDED", update_sql)
        self.assertEqual(parameters["error_code"], "TimeoutError")

    async def test_action_completion_persists_enforced_post_state(self) -> None:
        from app.modules.risk.repository import complete_action

        action_id = uuid4()
        connection = _FakeConnection([[{"risk_action_id": action_id, "action_status": "succeeded"}]])

        row = await complete_action(
            connection,
            risk_action_id=action_id,
            status="succeeded",
            completed_at=NOW,
            result_details={"user_updated_at": NOW.isoformat(), "api_keys": []},
        )

        self.assertEqual(row["action_status"], "succeeded")
        sql, _ = connection.calls[0]
        self.assertIn("attempt_count = attempt_count + 1", sql)
        self.assertIn("result_details = CAST(:result_details AS JSONB)", sql)

    async def test_latest_successful_ban_action_returns_private_restore_state(self) -> None:
        from app.modules.risk.repository import get_latest_succeeded_ban_action

        action_id = uuid4()
        connection = _FakeConnection([[{
            "risk_action_id": action_id,
            "action_type": "auto_ban",
            "action_status": "succeeded",
            "source_api_key_states_before": [],
            "result_details": {},
        }]])

        action = await get_latest_succeeded_ban_action(
            connection,
            risk_account_id=uuid4(),
        )

        self.assertEqual(action["risk_action_id"], str(action_id))
        sql, _ = connection.calls[0]
        self.assertIn("action_type IN ('auto_ban', 'manual_ban')", sql)
        self.assertIn("action_status = 'succeeded'", sql)
        self.assertIn("source_api_key_states_before", sql)

    async def test_pending_auto_ban_actions_are_bounded_and_include_recovery_state(self) -> None:
        from app.modules.risk.repository import list_pending_auto_ban_actions

        action_id = uuid4()
        connection = _FakeConnection([[
            {
                "risk_action_id": action_id,
                "action_status": "pending",
                "source_api_key_states_before": [],
            }
        ]])

        rows = await list_pending_auto_ban_actions(
            connection,
            site_id="aiwelink",
            limit=5000,
        )

        self.assertEqual(rows[0]["risk_action_id"], str(action_id))
        sql, parameters = connection.calls[0]
        self.assertIn("action_type = 'auto_ban'", sql)
        self.assertIn("action_status = 'pending'", sql)
        self.assertIn("source_api_key_states_before", sql)
        self.assertIn("LIMIT :limit", sql)
        self.assertEqual(parameters["limit"], 200)

    async def test_settings_default_to_paused_fixed_thresholds(self) -> None:
        from app.modules.risk.repository import get_settings

        row = await get_settings(_FakeConnection([[]]), site_id="aiwelink")

        self.assertEqual(row["site_id"], "aiwelink")
        self.assertFalse(row["detector_enabled"])
        self.assertFalse(row["auto_ban_enabled"])
        self.assertEqual(row["poll_interval_seconds"], 60)
        self.assertEqual(row["ip_window_days"], 7)
        self.assertEqual(row["shared_ip_min_accounts"], 3)

    async def test_cycle_lock_is_non_blocking_and_site_scoped(self) -> None:
        from app.modules.risk.repository import acquire_cycle_lock

        connection = _FakeConnection([[{"acquired": True}]])

        acquired = await acquire_cycle_lock(connection, site_id="aiwelink")

        self.assertTrue(acquired)
        sql, parameters = connection.calls[0]
        self.assertIn("pg_try_advisory_lock", sql)
        self.assertIn(":site_id", sql)
        self.assertEqual(parameters["site_id"], "aiwelink")

    async def test_cycle_lock_has_explicit_session_release(self) -> None:
        from app.modules.risk.repository import release_cycle_lock

        connection = _FakeConnection([[{"released": True}]])

        await release_cycle_lock(connection, site_id="aiwelink")

        sql, parameters = connection.calls[0]
        self.assertIn("pg_advisory_unlock", sql)
        self.assertEqual(parameters["site_id"], "aiwelink")

    async def test_observations_are_upserted_as_compressed_relationships(self) -> None:
        from app.modules.risk.repository import upsert_observations

        connection = _FakeConnection([[]])
        observations = (
            IpObservation("42", "a.b@example.com", "14.31.212.25", "user_audit", NOW, 101),
            IpObservation("42", "a.b@example.com", "14.31.212.25", "user_audit", NOW, 102),
        )

        count = await upsert_observations(connection, site_id="aiwelink", observations=observations)

        self.assertEqual(count, 2)
        sql, parameters = connection.calls[0]
        self.assertIn("ON CONFLICT (site_id, external_user_id, ip_address, source_type)", sql)
        self.assertIn("event_count = growth.risk_ip_accounts.event_count + 1", sql)
        self.assertEqual(len(parameters), 2)
        self.assertNotIn("request_body", sql)

    async def test_risk_inputs_require_three_distinct_accounts_in_window(self) -> None:
        from app.modules.risk.repository import list_account_risk_inputs

        connection = _FakeConnection([[{
            "external_user_id": "42",
            "email": "a.b@example.com",
            "manual_override_active": False,
            "shared_ip_evidence": [],
        }]])

        rows = await list_account_risk_inputs(
            connection,
            site_id="aiwelink",
            cutoff=NOW - timedelta(days=7),
            minimum_accounts=3,
        )

        self.assertEqual(rows[0]["external_user_id"], "42")
        sql, parameters = connection.calls[0]
        self.assertIn("COUNT(DISTINCT external_user_id) >= :minimum_accounts", sql)
        self.assertIn("last_seen_at >= :cutoff", sql)
        self.assertIn("jsonb_agg", sql)
        self.assertIn("AS has_verified_payment", sql)
        self.assertIn("event.cash_amount_cny > 0", sql)
        self.assertEqual(parameters["minimum_accounts"], 3)

    async def test_risk_account_upsert_preserves_manual_override(self) -> None:
        from app.modules.risk.repository import upsert_risk_account

        risk_account_id = uuid4()
        connection = _FakeConnection([[{
            "risk_account_id": risk_account_id,
            "risk_status": "high_risk",
            "manual_override_active": True,
        }]])

        row = await upsert_risk_account(
            connection,
            site_id="aiwelink",
            external_user_id="42",
            email="a.b@example.com",
            risk_status="high_risk",
            risk_reasons={"email_rules": ["email_local_part_dot"]},
            detected_at=NOW,
            risk_account_id=risk_account_id,
        )

        self.assertEqual(row["risk_account_id"], str(risk_account_id))
        sql, _ = connection.calls[0]
        self.assertIn("ON CONFLICT (site_id, external_user_id) DO UPDATE", sql)
        self.assertNotIn("manual_override_active = EXCLUDED", sql)

    async def test_action_creation_uses_deterministic_idempotency_key(self) -> None:
        from app.modules.risk.repository import create_action

        action_id = uuid4()
        connection = _FakeConnection([[{
            "risk_action_id": action_id,
            "action_status": "pending",
        }]])

        row = await create_action(
            connection,
            risk_action_id=action_id,
            idempotency_key="auto-ban:aiwelink:42:cluster-v1",
            risk_account_id=uuid4(),
            site_id="aiwelink",
            external_user_id="42",
            email="a.b@example.com",
            action_type="auto_ban",
            decision_reason="email_and_shared_ip",
            matched_email_rules=["email_local_part_dot"],
            shared_ip_evidence=[{"ip_address": "14.31.212.25", "account_count": 3}],
            source_user_status_before="active",
            source_user_updated_at_before=NOW,
            source_api_key_states_before=[{"id": "key-1", "status": "active"}],
            requested_by="system:risk-detector",
            requested_at=NOW,
        )

        self.assertEqual(row["risk_action_id"], str(action_id))
        sql, parameters = connection.calls[0]
        self.assertIn("ON CONFLICT (idempotency_key) DO UPDATE", sql)
        self.assertEqual(parameters["idempotency_key"], "auto-ban:aiwelink:42:cluster-v1")

    async def test_event_is_append_only_and_idempotent(self) -> None:
        from app.modules.risk.repository import append_event

        connection = _FakeConnection([[{"risk_event_id": uuid4()}]])

        await append_event(
            connection,
            risk_event_id=uuid4(),
            idempotency_key="high-risk:42:v1",
            risk_account_id=uuid4(),
            site_id="aiwelink",
            external_user_id="42",
            email="a.b@example.com",
            event_type="high_risk_detected",
            decision_reason="email_rule",
            created_at=NOW,
        )

        sql, _ = connection.calls[0]
        self.assertIn("INSERT INTO growth.risk_events", sql)
        self.assertIn("ON CONFLICT (idempotency_key) DO NOTHING", sql)
        self.assertNotIn("DO UPDATE", sql)

    async def test_stats_exclusion_updates_operations_and_traffic_views(self) -> None:
        from app.modules.risk.repository import set_stats_exclusion

        risk_account_id = uuid4()
        connection = _FakeConnection([[], []])

        await set_stats_exclusion(
            connection,
            site_id="aiwelink",
            external_user_id="42",
            risk_account_id=risk_account_id,
            excluded=True,
            actor_id="system:risk-detector",
        )

        statements = "\n".join(statement for statement, _ in connection.calls)
        self.assertIn("UPDATE growth.ops_user_snapshots", statements)
        self.assertIn("is_risk_excluded = :excluded", statements)
        self.assertIn("INSERT INTO growth.user_exclusions", statements)
        self.assertIn("source = 'rule'", statements)

    async def test_release_only_deactivates_risk_owned_traffic_exclusion(self) -> None:
        from app.modules.risk.repository import set_stats_exclusion

        connection = _FakeConnection([[], []])
        await set_stats_exclusion(
            connection,
            site_id="aiwelink",
            external_user_id="42",
            risk_account_id=uuid4(),
            excluded=False,
            actor_id="admin-1",
        )

        exclusion_sql = connection.calls[1][0]
        self.assertIn("UPDATE growth.user_exclusions", exclusion_sql)
        self.assertIn("source = 'rule'", exclusion_sql)
        self.assertIn("reason LIKE 'risk_control:%'", exclusion_sql)

    async def test_cleanup_removes_only_expired_compressed_ip_rows(self) -> None:
        from app.modules.risk.repository import cleanup_observations

        connection = _FakeConnection([[{"deleted_count": 12}]])

        deleted = await cleanup_observations(
            connection,
            site_id="aiwelink",
            cutoff=NOW - timedelta(days=30),
        )

        self.assertEqual(deleted, 12)
        sql, _ = connection.calls[0]
        self.assertIn("DELETE FROM growth.risk_ip_accounts", sql)
        self.assertIn("last_seen_at < :cutoff", sql)
        self.assertNotIn("risk_events", sql)

    async def test_overview_counts_statuses_clusters_failures_and_source_health(self) -> None:
        from app.modules.risk.repository import get_overview

        connection = _FakeConnection([[{
            "banned_count": 2,
            "high_risk_count": 3,
            "shared_ip_cluster_count": 4,
            "failed_action_count": 1,
        }]])

        overview = await get_overview(
            connection,
            site_id="aiwelink",
            cutoff=NOW - timedelta(days=7),
            minimum_accounts=3,
        )

        self.assertEqual(overview["banned_count"], 2)
        sql, parameters = connection.calls[0]
        self.assertIn("growth.risk_accounts", sql)
        self.assertIn("growth.risk_ip_accounts", sql)
        self.assertIn("growth.risk_actions", sql)
        self.assertIn("COUNT(DISTINCT external_user_id) >= :minimum_accounts", sql)
        self.assertEqual(parameters["minimum_accounts"], 3)

    async def test_account_list_is_bounded_and_returns_shared_ip_summary(self) -> None:
        from app.modules.risk.repository import list_accounts

        connection = _FakeConnection([[{
            "risk_account_id": uuid4(),
            "email": "a.b@example.com",
            "risk_status": "high_risk",
            "total_count": 1,
        }]])

        result = await list_accounts(
            connection,
            site_id="aiwelink",
            status="high_risk",
            query="a.b",
            limit=5000,
            offset=0,
        )

        self.assertEqual(result["total"], 1)
        sql, parameters = connection.calls[0]
        self.assertIn("COUNT(*) OVER () AS total_count", sql)
        self.assertIn("shared_ip_count", sql)
        self.assertEqual(parameters["limit"], 200)
        self.assertEqual(parameters["status"], "high_risk")

    async def test_detail_contains_ip_evidence_actions_and_event_timeline(self) -> None:
        from app.modules.risk.repository import get_account_detail

        risk_id = uuid4()
        connection = _FakeConnection([
            [{
                "risk_account_id": risk_id,
                "site_id": "aiwelink",
                "external_user_id": "42",
                "email": "a.b@example.com",
            }],
            [{"ip_address": "14.31.212.25"}],
            [{"risk_action_id": uuid4(), "action_status": "succeeded"}],
            [{"risk_event_id": uuid4(), "event_type": "auto_ban_succeeded"}],
        ])

        detail = await get_account_detail(connection, risk_account_id=risk_id)

        self.assertEqual(detail["email"], "a.b@example.com")
        self.assertEqual(len(detail["ip_evidence"]), 1)
        self.assertEqual(len(detail["actions"]), 1)
        self.assertEqual(len(detail["events"]), 1)
        statements = "\n".join(statement for statement, _ in connection.calls)
        self.assertNotIn("source_api_key_states_before", statements)
        self.assertIn("growth.risk_events", statements)


class _FakeMappings:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def all(self) -> list[dict]:
        return self.rows


class _FakeResult:
    def __init__(self, rows: list[dict]):
        self.rows = rows

    def mappings(self) -> _FakeMappings:
        return _FakeMappings(self.rows)

    def scalar_one_or_none(self):
        if not self.rows:
            return None
        row = self.rows[0]
        return next(iter(row.values())) if isinstance(row, dict) else row


class _FakeConnection:
    def __init__(self, results: list[list[dict]]):
        self.results = list(results)
        self.calls: list[tuple[str, dict | list[dict]]] = []

    async def execute(self, statement, parameters=None) -> _FakeResult:
        self.calls.append((str(statement), parameters or {}))
        rows = self.results.pop(0) if self.results else []
        return _FakeResult(rows)


if __name__ == "__main__":
    unittest.main()
