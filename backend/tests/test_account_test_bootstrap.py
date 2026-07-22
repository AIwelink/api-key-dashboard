import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, call


from app.modules.system.bootstrap import ensure_account_test_indexes


class AccountTestBootstrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_creates_durable_test_indexes(self) -> None:
        db = SimpleNamespace(
            sub2api_account_test_states=SimpleNamespace(create_index=AsyncMock()),
            sub2api_account_test_events=SimpleNamespace(create_index=AsyncMock()),
            sub2api_account_test_site_meta=SimpleNamespace(create_index=AsyncMock()),
        )

        await ensure_account_test_indexes(db)

        db.sub2api_account_test_states.create_index.assert_has_awaits(
            [
                call([("site_id", 1), ("remote_account_id", 1)], unique=True),
                call("next_test_at"),
            ]
        )
        db.sub2api_account_test_events.create_index.assert_any_await(
            "expires_at", expireAfterSeconds=0
        )
        db.sub2api_account_test_events.create_index.assert_any_await(
            [("site_id", 1), ("remote_account_id", 1), ("tested_at", -1)]
        )
        db.sub2api_account_test_site_meta.create_index.assert_awaited_once_with(
            "site_id", unique=True
        )

    def test_application_starts_unified_scheduler_instead_of_legacy_long_7d_loop(self) -> None:
        source = (Path(__file__).parents[1] / "app" / "main.py").read_text(encoding="utf-8")
        self.assertIn("account_test_scheduler_loop", source)
        self.assertNotIn("long_7d_probe_scheduler_loop", source)


if __name__ == "__main__":
    unittest.main()
