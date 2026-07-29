from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from app.modules.client_metrics.models import AdapterSample


class ClientMetricAdapter(Protocol):
    async def sample(
        self,
        *,
        site: dict[str, Any],
        bucket_at: datetime,
        cursor: dict[str, Any],
    ) -> AdapterSample: ...
