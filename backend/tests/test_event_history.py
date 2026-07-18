from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.events import records


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def sort(self, *_args, **_kwargs):
        return self

    def limit(self, value: int):
        self.items = self.items[:value]
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class EventAccountHistoryTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_detail_returns_legacy_samples_and_new_changes(self) -> None:
        identity = {"_id": "api-5001:user@example.com", "site_id": "api-5001", "normalized_email": "user@example.com"}
        db = SimpleNamespace(
            remote_account_identities=SimpleNamespace(find_one=AsyncMock(return_value=identity)),
            remote_account_sessions=SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([])),
            remote_account_status_events=SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([])),
            remote_account_probe_samples=SimpleNamespace(find=lambda *_args, **_kwargs: AsyncCursor([{"_id": "legacy"}])),
        )
        changes = [{"event_id": "event-1", "changes": {"usage.value": 2}}]

        with (
            patch.object(records, "load_identity_changes", AsyncMock(return_value=changes)) as load_changes,
            patch.object(records, "_event_context", AsyncMock(return_value={})),
            patch.object(records, "_identity_context", AsyncMock(return_value={})),
        ):
            result = await records.get_event_account_detail(db, identity["_id"])

        load_changes.assert_awaited_once_with(
            db,
            site_id="api-5001",
            identity_id="api-5001:user@example.com",
        )
        self.assertEqual(result["samples"], [{"id": "legacy"}])
        self.assertEqual(result["changes"], changes)
        self.assertEqual(result["raw"]["changes"], changes)


if __name__ == "__main__":
    unittest.main()
