from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import recover_feishu_pending_identity as recovery


def source_user() -> dict:
    return {
        "_id": "feishu-pending",
        "email": "feishu-pending@identity.invalid",
        "email_is_placeholder": True,
        "name": "Feishu Member",
        "role": "viewer",
        "status": "active",
        "authorization_status": "pending",
        "created_by": "feishu",
        "feishu_identity": {
            "identity_key": "tenant-a:union:union-1",
            "tenant_key": "tenant-a",
            "union_id": "union-1",
            "open_id": "open-1",
            "user_id": "user-1",
            "name": "Feishu Member",
            "email": None,
            "avatar_url": "https://example.com/avatar.png",
        },
    }


class RecoverySummaryTests(unittest.TestCase):
    def test_safe_summary_excludes_external_identity_and_credentials(self) -> None:
        user = source_user()
        user["password_hash"] = "secret"

        result = recovery.safe_summary(user)

        self.assertEqual(result["id"], "feishu-pending")
        self.assertTrue(result["feishu_bound"])
        serialized = repr(result)
        for secret in (
            "tenant-a:union:union-1",
            "open-1",
            "union-1",
            "user-1",
            "password_hash",
            "secret",
        ):
            self.assertNotIn(secret, serialized)


class RecoveryCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_recover_uses_exact_users_and_existing_service_path(self) -> None:
        source = source_user()
        target = {
            "_id": "owner@example.com",
            "name": "Owner",
            "role": "owner",
            "status": "active",
            "authorization_status": "active",
        }
        users = SimpleNamespace(find_one=AsyncMock(side_effect=[source, target]))
        db = SimpleNamespace(users=users)

        with patch.object(
            recovery,
            "resolve_feishu_user",
            AsyncMock(return_value={**target, "feishu_identity": {"source_user_id": source["_id"]}}),
        ) as resolve_mock:
            result = await recovery.recover(
                db,
                source_user_id=source["_id"],
                target_user_id=target["_id"],
            )

        self.assertEqual(result["_id"], target["_id"])
        self.assertEqual(
            users.find_one.await_args_list[0].args[0],
            {"_id": source["_id"]},
        )
        self.assertEqual(
            users.find_one.await_args_list[1].args[0],
            {"_id": target["_id"]},
        )
        identity = resolve_mock.await_args.kwargs["identity"]
        self.assertEqual(identity.identity_key, "tenant-a:union:union-1")
        self.assertEqual(resolve_mock.await_args.kwargs["purpose"], "bind")
        self.assertEqual(resolve_mock.await_args.kwargs["target_user_id"], target["_id"])

    async def test_recover_rejects_same_source_and_target(self) -> None:
        db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock()))

        with self.assertRaisesRegex(ValueError, "different"):
            await recovery.recover(
                db,
                source_user_id="same-user",
                target_user_id="same-user",
            )

        db.users.find_one.assert_not_awaited()

    async def test_recover_rejects_stored_identity_mismatch(self) -> None:
        source = source_user()
        source["feishu_identity"]["identity_key"] = "tenant-a:union:other"
        target = {"_id": "owner@example.com", "status": "active"}
        db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(side_effect=[source, target])))

        with self.assertRaisesRegex(ValueError, "identity"):
            await recovery.recover(
                db,
                source_user_id=source["_id"],
                target_user_id=target["_id"],
            )


if __name__ == "__main__":
    unittest.main()
