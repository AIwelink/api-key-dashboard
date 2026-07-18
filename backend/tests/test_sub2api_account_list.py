from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.modules.sub2api import cache


class AccountListCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items
        self.sort_fields: list[tuple[str, int]] | None = None
        self.skip_count = 0
        self.limit_count = 0

    def sort(self, fields: list[tuple[str, int]]):
        self.sort_fields = fields
        return self

    def skip(self, count: int):
        self.skip_count = count
        return self

    def limit(self, count: int):
        self.limit_count = count
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class CachedGroupAccountListTests(unittest.IsolatedAsyncioTestCase):
    async def test_sorts_new_cache_documents_by_remote_creation_time_descending(self) -> None:
        cursor = AccountListCursor([])
        accounts_cache = SimpleNamespace(
            count_documents=AsyncMock(return_value=0),
            find=lambda _query: cursor,
        )
        db = SimpleNamespace(sub2api_accounts_cache=accounts_cache)

        with (
            patch.object(cache, "_attach_local_account_metadata", new=AsyncMock()),
            patch.object(cache, "get_cache_meta", new=AsyncMock(return_value={})),
            patch.object(cache, "_get_or_update_group_capacity_summary", new=AsyncMock(return_value={})),
        ):
            await cache.list_cached_group_accounts(db, "api-5001", 3, page=2, page_size=50)

        self.assertEqual(cursor.sort_fields, [("created_at", -1), ("sub2api_account_id", -1)])
        self.assertEqual(cursor.skip_count, 50)
        self.assertEqual(cursor.limit_count, 50)


if __name__ == "__main__":
    unittest.main()
