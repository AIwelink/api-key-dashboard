from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch


class RiskSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scheduler_runs_every_sixty_seconds(self) -> None:
        from app.modules.risk.scheduler import RISK_POLL_INTERVAL_SECONDS, risk_control_loop

        schema_initializer = AsyncMock(return_value={"initialized": True})
        cycle = AsyncMock(return_value={"status": "succeeded"})
        delays = []

        async def stop_after_delay(seconds: float) -> None:
            delays.append(seconds)
            raise asyncio.CancelledError

        with self.assertRaises(asyncio.CancelledError):
            await risk_control_loop(
                object(),
                cycle_func=cycle,
                sleep_func=stop_after_delay,
                schema_initializer=schema_initializer,
            )

        self.assertEqual(RISK_POLL_INTERVAL_SECONDS, 60)
        schema_initializer.assert_awaited_once()
        cycle.assert_awaited_once()
        self.assertEqual(delays, [60])

    async def test_cycle_failure_is_logged_and_retried_after_sixty_seconds(self) -> None:
        from app.modules.risk.scheduler import risk_control_loop

        cycle = AsyncMock(side_effect=RuntimeError("source unavailable"))
        delays = []

        async def stop_after_delay(seconds: float) -> None:
            delays.append(seconds)
            raise asyncio.CancelledError

        with patch("app.modules.risk.scheduler.logger.exception") as logged:
            with self.assertRaises(asyncio.CancelledError):
                await risk_control_loop(
                    object(),
                    cycle_func=cycle,
                    sleep_func=stop_after_delay,
                    schema_initializer=AsyncMock(return_value={"initialized": True}),
                )

        logged.assert_called_once()
        self.assertEqual(delays, [60])

    async def test_schema_failure_prevents_detection(self) -> None:
        from app.modules.risk.scheduler import risk_control_loop

        cycle = AsyncMock()

        async def stop_after_delay(seconds: float) -> None:
            raise asyncio.CancelledError

        with patch("app.modules.risk.scheduler.logger.exception"):
            with self.assertRaises(asyncio.CancelledError):
                await risk_control_loop(
                    object(),
                    cycle_func=cycle,
                    sleep_func=stop_after_delay,
                    schema_initializer=AsyncMock(side_effect=RuntimeError("migration failed")),
                )

        cycle.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
