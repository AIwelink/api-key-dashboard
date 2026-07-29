from __future__ import annotations

import unittest
from unittest.mock import ANY, AsyncMock, patch

from fastapi import HTTPException
from pydantic import ValidationError

from app.routers import plus_self_produced as router
from app.schemas import PlusSelfProducedSettingsUpdate


class PlusSelfProducedSchemaTests(unittest.TestCase):
    def test_interval_minutes_is_bounded(self) -> None:
        self.assertEqual(
            PlusSelfProducedSettingsUpdate(
                enabled=True,
                interval_minutes=15,
                source_group_id=14,
                plus_group_id=16,
                banned_group_id=17,
                plus_error_group_id=19,
            ).model_dump(exclude_unset=True),
            {
                "enabled": True,
                "interval_minutes": 15,
                "source_group_id": 14,
                "plus_group_id": 16,
                "banned_group_id": 17,
                "plus_error_group_id": 19,
            },
        )
        with self.assertRaises(ValidationError):
            PlusSelfProducedSettingsUpdate(interval_minutes=0)
        with self.assertRaises(ValidationError):
            PlusSelfProducedSettingsUpdate(interval_minutes=1_441)
        with self.assertRaises(ValidationError):
            PlusSelfProducedSettingsUpdate(source_group_id=0)


class PlusSelfProducedRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_groups_delegate_to_postgresql_service(self) -> None:
        endpoint = getattr(router, "get_plus_self_produced_groups", None)
        self.assertTrue(callable(endpoint), "groups endpoint is not implemented")
        if not callable(endpoint):
            return
        groups = [{"id": 4, "name": "plus自产", "status": "active"}]
        with patch.object(router, "list_groups", AsyncMock(return_value=groups)) as list_groups:
            result = await endpoint(_={}, db=object())

        self.assertEqual(result, groups)
        list_groups.assert_awaited_once_with(ANY)

    async def test_status_and_results_delegate_to_services(self) -> None:
        status_payload = {"site_id": "US06-5002", "running": False}
        results_payload = {"items": [], "total": 0, "page": 1, "page_size": 50}
        with (
            patch.object(router, "get_status", AsyncMock(return_value=status_payload)) as get_status,
            patch.object(router, "list_results", AsyncMock(return_value=results_payload)) as list_results,
        ):
            status_result = await router.get_plus_self_produced_status(_={}, db=object())
            results_result = await router.get_plus_self_produced_results(
                page=1,
                page_size=50,
                classification="unauthorized_banned",
                _={},
                db=object(),
            )

        self.assertEqual(status_result, status_payload)
        self.assertEqual(results_result, results_payload)
        get_status.assert_awaited_once_with(ANY)
        list_results.assert_awaited_once_with(
            ANY,
            page=1,
            page_size=50,
            classification="unauthorized_banned",
        )

    async def test_settings_update_is_audited(self) -> None:
        payload = PlusSelfProducedSettingsUpdate(enabled=False, interval_minutes=30)
        updated = {"enabled": False, "interval_minutes": 30}
        actor = {"_id": "admin@example.com"}
        with (
            patch.object(router, "update_settings", AsyncMock(return_value=updated)) as update_settings,
            patch.object(router, "write_audit_log", AsyncMock()) as write_audit_log,
        ):
            result = await router.patch_plus_self_produced_settings(payload=payload, actor=actor, db=object())

        self.assertEqual(result, updated)
        update_settings.assert_awaited_once_with(ANY, {"enabled": False, "interval_minutes": 30}, actor)
        write_audit_log.assert_awaited_once()

    async def test_manual_run_conflict_returns_409(self) -> None:
        with patch.object(
            router,
            "run_probe",
            AsyncMock(return_value={"ok": False, "conflict": True, "status": "running"}),
        ):
            with self.assertRaises(HTTPException) as raised:
                await router.post_plus_self_produced_run(actor={"_id": "admin@example.com"}, db=object())

        self.assertEqual(raised.exception.status_code, 409)

    async def test_manual_run_is_audited(self) -> None:
        run = {"ok": True, "run_id": "run-1", "promoted": 2, "banned": 1}
        with (
            patch.object(router, "run_probe", AsyncMock(return_value=run)) as run_probe,
            patch.object(router, "write_audit_log", AsyncMock()) as write_audit_log,
        ):
            result = await router.post_plus_self_produced_run(actor={"_id": "admin@example.com"}, db=object())

        self.assertEqual(result, run)
        run_probe.assert_awaited_once_with(ANY, trigger="manual")
        write_audit_log.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
