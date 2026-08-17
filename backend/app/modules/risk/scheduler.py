from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.modules.system.growth_database_settings import initialize_growth_database


RISK_POLL_INTERVAL_SECONDS = 60
RISK_SCHEDULER_ACTOR = {
    "_id": "system:risk-detector",
    "name": "AIWeLink risk detector",
}
logger = logging.getLogger("app.risk.scheduler")


async def risk_control_loop(
    mongo_db: Any,
    *,
    cycle_func: Callable[[Any], Awaitable[dict[str, Any]]] | None = None,
    sleep_func: Callable[[float], Awaitable[None]] = asyncio.sleep,
    schema_initializer: Callable[..., Awaitable[dict[str, Any]]] = initialize_growth_database,
) -> None:
    selected_cycle = cycle_func
    schema_ready = False
    while not schema_ready:
        try:
            result = await schema_initializer(mongo_db, actor=RISK_SCHEDULER_ACTOR)
            schema_ready = bool(result.get("initialized"))
            if not schema_ready:
                logger.error(
                    "risk_growth_schema_not_ready current_version=%s pending_versions=%s",
                    result.get("current_version"),
                    result.get("pending_versions") or [],
                )
        except Exception:  # noqa: BLE001 - a later poll can recover infrastructure.
            logger.exception("risk_growth_schema_initialization_failed")
        if not schema_ready:
            await sleep_func(RISK_POLL_INTERVAL_SECONDS)

    if selected_cycle is None:
        from app.modules.risk.coordinator import run_risk_cycle

        selected_cycle = run_risk_cycle
    while True:
        try:
            await selected_cycle(mongo_db)
        except Exception:  # noqa: BLE001 - keep near-real-time protection alive.
            logger.exception("risk_detection_cycle_failed")
        await sleep_func(RISK_POLL_INTERVAL_SECONDS)
