from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.auto_replenishment import settings
from app.modules.auto_replenishment.secrets import decrypt_secret


TEST_SECRET_KEY = "test-only-application-secret"


def fake_db(
    current: dict | None = None,
    *,
    site: dict | None = None,
    group: dict | None = None,
) -> tuple[SimpleNamespace, SimpleNamespace]:
    collection = SimpleNamespace(
        find_one=AsyncMock(return_value=current),
        replace_one=AsyncMock(),
    )
    return (
        SimpleNamespace(
            auto_replenishment_settings=collection,
            sub2api_sites=SimpleNamespace(find_one=AsyncMock(return_value=site)),
            sub2api_groups_cache=SimpleNamespace(find_one=AsyncMock(return_value=group)),
        ),
        collection,
    )


class AutoReplenishmentSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_settings_return_safe_defaults(self) -> None:
        db, _ = fake_db()

        result = await settings.get_auto_replenishment_settings(db)

        self.assertEqual(result["provider"], "sogouedu")
        self.assertEqual(result["base_url"], "https://sogouedu.cc")
        self.assertFalse(result["enabled"])
        self.assertEqual(result["minimum_account_count"], 2)
        self.assertEqual(result["minimum_runway_minutes"], 5)
        self.assertEqual(result["product"], "oauth_7d")
        self.assertEqual(result["local_account_type"], "team")
        self.assertEqual(result["target_site_id"], "us06-5001")
        self.assertEqual(result["target_group_name"], "plus账号池01")
        self.assertFalse(result["password_configured"])
        self.assertNotIn("password_ciphertext", result)

    async def test_initial_save_encrypts_password_and_returns_only_public_fields(self) -> None:
        db, collection = fake_db(
            site={"_id": "us06-5001", "status": "active", "site_type": "sub2api"},
            group={"site_id": "us06-5001", "group_id": 3, "group": {"id": 3, "name": "plus账号池01"}},
        )

        result = await settings.save_auto_replenishment_settings(
            db,
            payload={
                "username": "buyer",
                "password": "supplier-password",
                "enabled": True,
                "minimum_account_count": 2,
                "minimum_runway_minutes": 5,
            },
            actor={"_id": "owner@example.com", "name": "Owner"},
            secret_key=TEST_SECRET_KEY,
        )

        saved = collection.replace_one.await_args.args[1]
        self.assertNotEqual(saved["password_ciphertext"], "supplier-password")
        self.assertEqual(decrypt_secret(saved["password_ciphertext"], TEST_SECRET_KEY), "supplier-password")
        self.assertEqual(saved["target_group_id"], 3)
        self.assertEqual(saved["updated_by"], "owner@example.com")
        self.assertTrue(result["password_configured"])
        self.assertNotIn("password_ciphertext", result)
        self.assertNotIn("supplier-password", str(result))

    async def test_blank_password_update_preserves_existing_ciphertext(self) -> None:
        ciphertext = settings.encrypt_secret("old-password", TEST_SECRET_KEY)
        current = {
            **settings.DEFAULT_SETTINGS,
            "_id": settings.SETTINGS_ID,
            "username": "buyer",
            "password_ciphertext": ciphertext,
            "target_group_id": 3,
        }
        db, collection = fake_db(
            current,
            site={"_id": "us06-5001", "status": "active", "site_type": "sub2api"},
            group={"site_id": "us06-5001", "group_id": 3, "group": {"id": 3, "name": "plus账号池01"}},
        )

        result = await settings.save_auto_replenishment_settings(
            db,
            payload={
                "username": "buyer-updated",
                "password": "",
                "enabled": False,
                "minimum_account_count": 4,
                "minimum_runway_minutes": 8,
            },
            actor={"_id": "owner@example.com"},
            secret_key=TEST_SECRET_KEY,
        )

        saved = collection.replace_one.await_args.args[1]
        self.assertEqual(saved["password_ciphertext"], ciphertext)
        self.assertEqual(saved["username"], "buyer-updated")
        self.assertEqual(result["minimum_account_count"], 4)
        self.assertTrue(result["password_configured"])

    async def test_initial_save_requires_username_and_password(self) -> None:
        db, _ = fake_db()

        with self.assertRaisesRegex(ValueError, "username"):
            await settings.save_auto_replenishment_settings(
                db,
                payload={"username": "", "password": "secret"},
                actor={"_id": "owner@example.com"},
                secret_key=TEST_SECRET_KEY,
            )
        with self.assertRaisesRegex(ValueError, "password"):
            await settings.save_auto_replenishment_settings(
                db,
                payload={"username": "buyer", "password": ""},
                actor={"_id": "owner@example.com"},
                secret_key=TEST_SECRET_KEY,
            )

    async def test_thresholds_are_bounded(self) -> None:
        db, _ = fake_db()

        with self.assertRaisesRegex(ValueError, "minimum_account_count"):
            await settings.save_auto_replenishment_settings(
                db,
                payload={"username": "buyer", "password": "secret", "minimum_account_count": 0},
                actor={"_id": "owner@example.com"},
                secret_key=TEST_SECRET_KEY,
            )
        with self.assertRaisesRegex(ValueError, "minimum_runway_minutes"):
            await settings.save_auto_replenishment_settings(
                db,
                payload={"username": "buyer", "password": "secret", "minimum_runway_minutes": 1441},
                actor={"_id": "owner@example.com"},
                secret_key=TEST_SECRET_KEY,
            )

    async def test_save_rejects_missing_target_site_or_group(self) -> None:
        db, _ = fake_db(site=None)

        with self.assertRaisesRegex(LookupError, "target sub2api site"):
            await settings.save_auto_replenishment_settings(
                db,
                payload={"username": "buyer", "password": "secret"},
                actor={"_id": "owner@example.com"},
                secret_key=TEST_SECRET_KEY,
            )

        db, _ = fake_db(
            site={"_id": "us06-5001", "status": "active", "site_type": "sub2api"},
            group=None,
        )
        with self.assertRaisesRegex(LookupError, "target sub2api group"):
            await settings.save_auto_replenishment_settings(
                db,
                payload={"username": "buyer", "password": "secret"},
                actor={"_id": "owner@example.com"},
                secret_key=TEST_SECRET_KEY,
            )

    async def test_target_group_lookup_accepts_the_spaced_remote_name(self) -> None:
        db, _ = fake_db(
            site={"_id": "us06-5001", "status": "active", "site_type": "sub2api"},
            group={"site_id": "us06-5001", "group_id": 3, "group": {"id": 3, "name": "plus 账号池 01"}},
        )

        result = await settings.save_auto_replenishment_settings(
            db,
            payload={"username": "buyer", "password": "secret"},
            actor={"_id": "owner@example.com"},
            secret_key=TEST_SECRET_KEY,
        )

        query = db.sub2api_groups_cache.find_one.await_args.args[0]
        patterns = [item.get("group.name") or item.get("name") for item in query["$or"]]
        self.assertTrue(any(hasattr(pattern, "search") and pattern.search("plus 账号池 01") for pattern in patterns))
        self.assertEqual(result["target_group_name"], "plus 账号池 01")


if __name__ == "__main__":
    unittest.main()
