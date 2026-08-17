from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError


RISK_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000042")


class RiskRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_operator_with_aiwelink_access_can_read_overview(self) -> None:
        from app.routers import risk

        overview = {"banned_count": 2, "high_risk_count": 3, "source_health": []}
        with patch.object(risk.service, "get_risk_overview", AsyncMock(return_value=overview)) as read:
            result = await risk.get_risk_overview(
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(result, overview)
        read.assert_awaited_once()

    async def test_aigclink_only_operator_cannot_read_aiwelink_risk_data(self) -> None:
        from app.routers import risk

        with self.assertRaises(HTTPException) as error:
            await risk.get_risk_overview(
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aigclink"]},
                db=object(),
            )

        self.assertEqual(error.exception.status_code, 403)

    async def test_operator_cannot_change_risk_settings(self) -> None:
        from app.modules.risk.schemas import RiskSettingsUpdate
        from app.routers import risk

        with self.assertRaises(HTTPException) as error:
            await risk.patch_risk_settings(
                payload=RiskSettingsUpdate(detector_enabled=True),
                actor={"_id": "operator-1", "role": "operator", "operations_site_ids": ["aiwelink"]},
                db=object(),
            )

        self.assertEqual(error.exception.status_code, 403)

    async def test_manual_action_requires_non_blank_reason(self) -> None:
        from app.modules.risk.schemas import RiskActionRequest

        with self.assertRaises(ValidationError):
            RiskActionRequest(reason="   ")

    async def test_admin_release_passes_actor_and_writes_safe_audit(self) -> None:
        from app.modules.risk.schemas import RiskActionRequest
        from app.routers import risk

        released = {"risk_account_id": str(RISK_ACCOUNT_ID), "status": "released", "partial": True}
        actor = {"_id": "admin-1", "name": "Admin", "role": "admin", "operations_site_ids": ["aiwelink"]}
        with (
            patch.object(risk.service, "manual_release", AsyncMock(return_value=released)) as release,
            patch.object(risk, "write_audit_log", AsyncMock()) as audit,
        ):
            result = await risk.post_manual_release(
                risk_account_id=RISK_ACCOUNT_ID,
                payload=RiskActionRequest(reason="校园网误报"),
                actor=actor,
                db=object(),
            )

        self.assertTrue(result["partial"])
        self.assertEqual(release.await_args.kwargs["actor_id"], "admin-1")
        self.assertEqual(release.await_args.kwargs["reason"], "校园网误报")
        self.assertNotIn("api_key", str(audit.await_args.kwargs).lower())
        self.assertEqual(audit.await_args.kwargs["action"], "operations.risk.release")

    async def test_admin_can_mark_false_positive_and_remove_override(self) -> None:
        from app.modules.risk.schemas import RiskActionRequest
        from app.routers import risk

        actor = {"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]}
        with (
            patch.object(risk.service, "set_false_positive", AsyncMock(return_value={"status": "cleared"})) as set_override,
            patch.object(risk.service, "remove_manual_override", AsyncMock(return_value={"status": "high_risk"})) as remove,
            patch.object(risk, "write_audit_log", AsyncMock()),
        ):
            cleared = await risk.post_false_positive(
                risk_account_id=RISK_ACCOUNT_ID,
                payload=RiskActionRequest(reason="已核验本人使用"),
                actor=actor,
                db=object(),
            )
            restored = await risk.post_remove_override(
                risk_account_id=RISK_ACCOUNT_ID,
                payload=RiskActionRequest(reason="重新纳入检测"),
                actor=actor,
                db=object(),
            )

        self.assertEqual(cleared["status"], "cleared")
        self.assertEqual(restored["status"], "high_risk")
        set_override.assert_awaited_once()
        remove.assert_awaited_once()

    async def test_manual_action_maps_busy_conflict_and_does_not_audit(self) -> None:
        from app.modules.risk.schemas import RiskActionRequest
        from app.routers import risk

        actor = {"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]}
        with (
            patch.object(
                risk.service,
                "manual_ban",
                AsyncMock(side_effect=RuntimeError("risk control is busy; retry the ban")),
            ),
            patch.object(risk, "write_audit_log", AsyncMock()) as audit,
        ):
            with self.assertRaises(HTTPException) as error:
                await risk.post_manual_ban(
                    risk_account_id=RISK_ACCOUNT_ID,
                    payload=RiskActionRequest(reason="人工确认盗刷"),
                    actor=actor,
                    db=object(),
                )

        self.assertEqual(error.exception.status_code, 409)
        audit.assert_not_awaited()

    async def test_settings_write_maps_growth_database_failure(self) -> None:
        from app.modules.risk.schemas import RiskSettingsUpdate
        from app.routers import risk

        actor = {"_id": "admin-1", "role": "admin", "operations_site_ids": ["aiwelink"]}
        with patch.object(
            risk.service,
            "update_risk_settings",
            AsyncMock(side_effect=SQLAlchemyError("offline")),
        ):
            with self.assertRaises(HTTPException) as error:
                await risk.patch_risk_settings(
                    payload=RiskSettingsUpdate(detector_enabled=True),
                    actor=actor,
                    db=object(),
                )

        self.assertEqual(error.exception.status_code, 503)

    async def test_ip_cluster_read_maps_growth_database_failure(self) -> None:
        from app.routers import risk

        with patch.object(
            risk.service,
            "list_risk_ip_clusters",
            AsyncMock(side_effect=SQLAlchemyError("offline")),
        ):
            with self.assertRaises(HTTPException) as error:
                await risk.get_risk_ip_clusters(
                    search=None,
                    limit=100,
                    offset=0,
                    actor={
                        "_id": "operator-1",
                        "role": "operator",
                        "operations_site_ids": ["aiwelink"],
                    },
                    db=object(),
                )

        self.assertEqual(error.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
