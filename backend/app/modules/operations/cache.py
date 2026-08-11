from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any


def _cache_key_contains(value: object, target: str) -> bool:
    if value == target:
        return True
    if isinstance(value, (tuple, list, set, frozenset)):
        return any(_cache_key_contains(item, target) for item in value)
    return False


@dataclass(frozen=True)
class _CacheEntry:
    value: Any
    expires_at: float


class OperationsResponseCache:
    def __init__(
        self,
        ttl_seconds: int = 60,
        max_entries: int = 256,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be greater than zero")
        if max_entries <= 0:
            raise ValueError("max_entries must be greater than zero")
        self._ttl_seconds = ttl_seconds
        self._max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[tuple[object, ...], _CacheEntry] = OrderedDict()
        self._inflight: dict[tuple[object, ...], asyncio.Task[Any]] = {}
        self._lock = asyncio.Lock()

    @property
    def size(self) -> int:
        return len(self._entries)

    async def get_or_load(
        self,
        key: tuple[object, ...],
        loader: Callable[[], Awaitable[Any]],
    ) -> Any:
        async with self._lock:
            now = self._clock()
            cached = self._entries.get(key)
            if cached is not None and cached.expires_at > now:
                self._entries.move_to_end(key)
                return cached.value
            if cached is not None:
                self._entries.pop(key, None)
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(loader())
                self._inflight[key] = task

        try:
            value = await task
        except BaseException:
            async with self._lock:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)
            raise

        async with self._lock:
            if self._inflight.get(key) is task:
                self._inflight.pop(key, None)
                self._entries[key] = _CacheEntry(
                    value=value,
                    expires_at=self._clock() + self._ttl_seconds,
                )
                self._entries.move_to_end(key)
                while len(self._entries) > self._max_entries:
                    self._entries.popitem(last=False)
        return value

    def invalidate(self, *, site_id: str | None = None) -> None:
        if site_id is None:
            self._entries.clear()
            return
        for key in tuple(self._entries):
            if _cache_key_contains(key, site_id):
                self._entries.pop(key, None)


operations_response_cache = OperationsResponseCache()
