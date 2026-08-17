from __future__ import annotations

import json
import unittest
from datetime import UTC, datetime


NOW = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


class Sub2ApiRiskAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_audit_page_resolves_registration_email_without_persisting_body(self) -> None:
        from app.modules.risk.adapters.sub2api import Sub2ApiRiskAdapter

        connection = _FakeConnection(
            [
                [
                    {
                        "id": 101,
                        "created_at": NOW,
                        "actor_user_id": None,
                        "actor_email": "",
                        "action": "auth.register",
                        "path": "/api/v1/auth/register",
                        "client_ip": "14.31.212.25",
                        "request_body": json.dumps({"email": " E.L.Lame@Example.com "}),
                    },
                    {
                        "id": 102,
                        "created_at": NOW,
                        "actor_user_id": "77",
                        "actor_email": "known@example.com",
                        "action": "keys.create",
                        "path": "/api/v1/keys",
                        "client_ip": "2001:0db8::1",
                        "request_body": None,
                    },
                ],
                [{"id": "42", "email": "e.l.lame@example.com"}],
            ]
        )
        adapter = Sub2ApiRiskAdapter()

        page = await adapter.read_audit_observations(
            connection,
            after_id=100,
            since=NOW,
            limit=500,
        )

        self.assertEqual(page.rows_read, 2)
        self.assertEqual(page.last_source_id, 102)
        self.assertEqual(page.latest_created_at, NOW)
        self.assertEqual(page.observations[0].external_user_id, "42")
        self.assertEqual(page.observations[0].email, "e.l.lame@example.com")
        self.assertEqual(page.observations[0].source_type, "registration_audit")
        self.assertEqual(page.observations[1].ip_address, "2001:db8::1")
        self.assertEqual(page.observations[1].source_type, "user_audit")
        self.assertFalse(hasattr(page.observations[0], "request_body"))
        audit_sql, audit_parameters = connection.calls[0]
        self.assertIn("FROM audit_logs", audit_sql)
        self.assertIn("id > :after_id", audit_sql)
        self.assertIn("created_at >= :since", audit_sql)
        self.assertIn("LIMIT :limit", audit_sql)
        self.assertEqual(audit_parameters["limit"], 500)

    async def test_audit_page_advances_cursor_across_invalid_evidence(self) -> None:
        from app.modules.risk.adapters.sub2api import Sub2ApiRiskAdapter

        connection = _FakeConnection(
            [[{
                "id": 103,
                "created_at": NOW,
                "actor_user_id": "42",
                "actor_email": "person@example.com",
                "action": "keys.create",
                "path": "/api/v1/keys",
                "client_ip": "not-an-ip",
                "request_body": None,
            }], []]
        )

        page = await Sub2ApiRiskAdapter().read_audit_observations(
            connection,
            after_id=102,
            since=NOW,
            limit=100,
        )

        self.assertEqual(page.rows_read, 1)
        self.assertEqual(page.last_source_id, 103)
        self.assertEqual(page.observations, ())

    async def test_usage_page_joins_email_and_uses_bounded_id_cursor(self) -> None:
        from app.modules.risk.adapters.sub2api import Sub2ApiRiskAdapter

        connection = _FakeConnection(
            [[{
                "id": 9001,
                "user_id": "42",
                "email": "a.b+tag@example.com",
                "ip_address": "14.31.212.25",
                "created_at": NOW,
            }]]
        )

        page = await Sub2ApiRiskAdapter().read_usage_observations(
            connection,
            after_id=9000,
            since=NOW,
            limit=750,
        )

        self.assertEqual(page.rows_read, 1)
        self.assertEqual(page.observations[0].source_type, "usage_log")
        self.assertEqual(page.observations[0].source_id, 9001)
        sql, parameters = connection.calls[0]
        self.assertIn("FROM usage_logs", sql)
        self.assertIn("JOIN users", sql)
        self.assertIn("usage.id > :after_id", sql)
        self.assertIn("LIMIT :limit", sql)
        self.assertEqual(parameters["limit"], 750)

    async def test_capture_account_state_includes_user_and_all_key_states(self) -> None:
        from app.modules.risk.adapters.sub2api import Sub2ApiRiskAdapter

        connection = _FakeConnection(
            [
                [{"id": "42", "email": "a.b@example.com", "status": "active", "updated_at": NOW}],
                [
                    {"id": "key-1", "status": "active", "updated_at": NOW},
                    {"id": "key-2", "status": "quota_exhausted", "updated_at": NOW},
                ],
            ]
        )

        state = await Sub2ApiRiskAdapter().capture_account_state(connection, "42")

        self.assertEqual(state.user_status, "active")
        self.assertEqual([key.status for key in state.api_keys], ["active", "quota_exhausted"])
        self.assertIn("deleted_at IS NULL", connection.calls[0][0])
        self.assertIn("deleted_at IS NULL", connection.calls[1][0])

    async def test_completed_cash_payment_protects_account_from_auto_ban(self) -> None:
        from app.modules.risk.adapters.sub2api import Sub2ApiRiskAdapter

        connection = _FakeConnection([[{"has_paid_history": True}]])

        paid = await Sub2ApiRiskAdapter().has_completed_payment(connection, "42")

        self.assertTrue(paid)
        sql, parameters = connection.calls[0]
        self.assertIn("FROM payment_orders", sql)
        self.assertIn("status = 'COMPLETED'", sql)
        self.assertIn("pay_amount > 0", sql)
        self.assertEqual(parameters["external_user_id"], "42")

    async def test_disable_account_updates_user_and_only_active_keys(self) -> None:
        from app.modules.risk.adapters.sub2api import (
            ApiKeyState,
            SourceAccountState,
            Sub2ApiRiskAdapter,
        )

        before = SourceAccountState(
            external_user_id="42",
            email="a.b@example.com",
            user_status="active",
            user_updated_at=NOW,
            api_keys=(
                ApiKeyState("key-1", "active", NOW),
                ApiKeyState("key-2", "quota_exhausted", NOW),
            ),
        )
        changed_at = datetime(2026, 8, 17, 12, 1, tzinfo=UTC)
        connection = _FakeConnection(
            [
                [{"id": "42", "status": "active", "updated_at": NOW}],
                [
                    {"id": "key-1", "status": "active", "updated_at": NOW},
                    {"id": "key-2", "status": "quota_exhausted", "updated_at": NOW},
                ],
                [{"updated_at": changed_at}],
                [{"id": "key-1", "updated_at": changed_at}],
            ]
        )

        result = await Sub2ApiRiskAdapter().disable_account(
            connection,
            before=before,
            changed_at=changed_at,
        )

        self.assertEqual(result.user_status, "disabled")
        self.assertEqual([key.id for key in result.api_keys], ["key-1"])
        statements = "\n".join(statement for statement, _ in connection.calls)
        self.assertGreaterEqual(statements.count("FOR UPDATE"), 2)
        self.assertIn("SET status = 'disabled'", statements)
        self.assertIn("SET status = 'inactive'", statements)
        self.assertIn("status = 'active'", statements)

    async def test_disable_account_rejects_a_concurrent_user_change(self) -> None:
        from app.modules.risk.adapters.sub2api import (
            SourceAccountState,
            SourceStateConflict,
            Sub2ApiRiskAdapter,
        )

        before = SourceAccountState("42", "a.b@example.com", "active", NOW, ())
        connection = _FakeConnection(
            [[{"id": "42", "status": "disabled", "updated_at": NOW}]]
        )

        with self.assertRaises(SourceStateConflict):
            await Sub2ApiRiskAdapter().disable_account(
                connection,
                before=before,
                changed_at=NOW,
            )

        self.assertEqual(len(connection.calls), 1)

    async def test_release_restores_only_unchanged_risk_mutations(self) -> None:
        from app.modules.risk.adapters.sub2api import (
            ApiKeyState,
            EnforcementResult,
            SourceAccountState,
            Sub2ApiRiskAdapter,
        )

        banned_at = datetime(2026, 8, 17, 12, 1, tzinfo=UTC)
        released_at = datetime(2026, 8, 17, 12, 2, tzinfo=UTC)
        before = SourceAccountState(
            "42",
            "a.b@example.com",
            "active",
            NOW,
            (ApiKeyState("key-1", "active", NOW), ApiKeyState("key-2", "active", NOW)),
        )
        enforced = EnforcementResult(
            user_status="disabled",
            user_updated_at=banned_at,
            api_keys=(
                ApiKeyState("key-1", "inactive", banned_at),
                ApiKeyState("key-2", "inactive", banned_at),
            ),
        )
        connection = _FakeConnection(
            [
                [{"id": "42", "status": "disabled", "updated_at": banned_at}],
                [{"updated_at": released_at}],
                [
                    {"id": "key-1", "status": "inactive", "updated_at": banned_at},
                    {"id": "key-2", "status": "inactive", "updated_at": released_at},
                ],
                [{"id": "key-1", "updated_at": released_at}],
            ]
        )

        result = await Sub2ApiRiskAdapter().release_account(
            connection,
            before=before,
            enforced=enforced,
            changed_at=released_at,
        )

        self.assertTrue(result.user_restored)
        self.assertEqual(result.restored_key_ids, ("key-1",))
        self.assertEqual(result.conflicted_key_ids, ("key-2",))
        self.assertTrue(result.partial)
        statements = "\n".join(statement for statement, _ in connection.calls)
        self.assertIn("SET status = :status", statements)
        self.assertIn("SET status = 'active'", statements)


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


class _FakeConnection:
    def __init__(self, results: list[list[dict]]):
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []

    async def execute(self, statement, parameters=None) -> _FakeResult:
        self.calls.append((str(statement), dict(parameters or {})))
        rows = self.results.pop(0) if self.results else []
        return _FakeResult(rows)


if __name__ == "__main__":
    unittest.main()
