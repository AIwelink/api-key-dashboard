from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.modules.system import client_sites


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class ClientSiteTests(unittest.IsolatedAsyncioTestCase):
    async def test_newapi_client_is_stored_outside_sub2api_sites(self) -> None:
        stored = {
            "_id": "customer-newapi-us01",
            "name": "Customer NewAPI US01",
            "client_type": "newapi",
            "base_url": "https://client.example.com",
            "api_key": "client-secret",
            "admin_user_id": "42",
            "status": "active",
        }
        collection = SimpleNamespace(replace_one=AsyncMock(), find_one=AsyncMock(return_value=stored))
        db = SimpleNamespace(client_sites=collection)

        result = await client_sites.create_client_site(
            db,
            payload=stored | {"id": stored["_id"]},
            actor={"_id": "admin@example.com", "name": "Admin"},
        )

        saved = collection.replace_one.await_args.args[1]
        self.assertEqual(saved["client_type"], "newapi")
        self.assertEqual(saved["admin_user_id"], "42")
        self.assertEqual(saved["api_key"], "client-secret")
        self.assertTrue(result["api_key_configured"])
        self.assertNotIn("api_key", result)

    async def test_newapi_client_requires_admin_user_id(self) -> None:
        db = SimpleNamespace(client_sites=SimpleNamespace(replace_one=AsyncMock()))

        with self.assertRaisesRegex(ValueError, "admin_user_id"):
            await client_sites.create_client_site(
                db,
                payload={
                    "id": "customer-newapi-us01",
                    "name": "Customer NewAPI US01",
                    "client_type": "newapi",
                    "base_url": "https://client.example.com",
                    "api_key": "client-secret",
                },
                actor={"_id": "admin@example.com"},
            )

    async def test_sub2api_client_does_not_require_admin_user_id(self) -> None:
        stored = {
            "_id": "customer-sub2api-us01",
            "name": "Customer Sub2API US01",
            "client_type": "sub2api",
            "base_url": "https://sub2-client.example.com",
            "api_key": "client-secret",
            "admin_user_id": "",
            "status": "active",
        }
        collection = SimpleNamespace(replace_one=AsyncMock(), find_one=AsyncMock(return_value=stored))
        db = SimpleNamespace(client_sites=collection)

        result = await client_sites.create_client_site(
            db,
            payload=stored | {"id": stored["_id"]},
            actor={"_id": "admin@example.com"},
        )

        self.assertEqual(result["client_type"], "sub2api")

    async def test_newapi_accepts_mysql_database_dsn_and_masks_credentials(self) -> None:
        stored = {
            "_id": "customer-newapi-us01",
            "name": "Customer NewAPI US01",
            "client_type": "newapi",
            "base_url": "https://client.example.com",
            "api_key": "client-secret",
            "admin_user_id": "42",
            "database_dsn": "mysql://report:secret%40word@mysql.internal:3307/newapi",
            "status": "active",
        }
        collection = SimpleNamespace(replace_one=AsyncMock(), find_one=AsyncMock(return_value=stored))
        db = SimpleNamespace(client_sites=collection)

        result = await client_sites.create_client_site(
            db,
            payload=stored | {"id": stored["_id"]},
            actor={"_id": "admin@example.com"},
        )

        saved = collection.replace_one.await_args.args[1]
        self.assertEqual(saved["database_dsn"], stored["database_dsn"])
        self.assertEqual(saved["data_retention_days"], 90)
        self.assertTrue(result["database_dsn_configured"])
        self.assertEqual(result["database_type"], "mysql")
        self.assertEqual(result["database_endpoint"], "mysql.internal:3307/newapi")
        self.assertNotIn("database_dsn", result)
        self.assertNotIn("report", str(result))
        self.assertNotIn("secret", str(result))

    async def test_sub2api_accepts_postgresql_database_dsn(self) -> None:
        stored = {
            "_id": "customer-sub2api-us01",
            "name": "Customer Sub2API US01",
            "client_type": "sub2api",
            "base_url": "https://sub2-client.example.com",
            "api_key": "client-secret",
            "admin_user_id": "",
            "database_dsn": "postgresql://reader:secret@postgres.internal:5433/sub2api",
            "data_retention_days": 120,
            "status": "active",
        }
        collection = SimpleNamespace(replace_one=AsyncMock(), find_one=AsyncMock(return_value=stored))
        db = SimpleNamespace(client_sites=collection)

        result = await client_sites.create_client_site(
            db,
            payload=stored | {"id": stored["_id"]},
            actor={"_id": "admin@example.com"},
        )

        self.assertEqual(collection.replace_one.await_args.args[1]["data_retention_days"], 120)
        self.assertEqual(result["database_type"], "postgresql")
        self.assertEqual(result["database_endpoint"], "postgres.internal:5433/sub2api")

    async def test_database_dsn_protocol_must_match_client_type(self) -> None:
        db = SimpleNamespace(
            client_sites=SimpleNamespace(
                replace_one=AsyncMock(),
                find_one=AsyncMock(return_value=None),
            )
        )

        with self.assertRaisesRegex(ValueError, "mysql"):
            await client_sites.create_client_site(
                db,
                payload={
                    "id": "customer-newapi-us01",
                    "client_type": "newapi",
                    "base_url": "https://client.example.com",
                    "admin_user_id": "42",
                    "database_dsn": "postgresql://reader:secret@postgres.internal/newapi",
                },
                actor={"_id": "admin@example.com"},
            )

    async def test_blank_database_dsn_update_preserves_secret(self) -> None:
        current = {
            "_id": "customer-newapi-us01",
            "client_type": "newapi",
            "base_url": "https://client.example.com",
            "admin_user_id": "42",
            "database_dsn": "mysql://reader:secret@mysql.internal/newapi",
            "data_retention_days": 90,
            "status": "active",
        }
        collection = SimpleNamespace(
            find_one=AsyncMock(side_effect=[current, current]),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(client_sites=collection)

        result = await client_sites.update_client_site(
            db,
            site_id="customer-newapi-us01",
            payload={"database_dsn": "", "data_retention_days": 180},
            actor={"_id": "admin@example.com"},
        )

        updates = collection.update_one.await_args.args[1]["$set"]
        self.assertNotIn("database_dsn", updates)
        self.assertEqual(updates["data_retention_days"], 180)
        self.assertTrue(result["database_dsn_configured"])

    async def test_legacy_newapi_sites_are_migrated_and_removed_from_account_pool_sites(self) -> None:
        legacy = {
            "_id": "newapi-us01",
            "name": "NewAPI US01",
            "site_type": "newapi",
            "base_url": "https://newapi.example.com",
            "token": "legacy-secret",
            "admin_user_id": "42",
            "status": "active",
        }
        source = SimpleNamespace(
            find=lambda *_args, **_kwargs: AsyncCursor([legacy]),
            update_one=AsyncMock(),
        )
        destination = SimpleNamespace(update_one=AsyncMock())
        db = SimpleNamespace(sub2api_sites=source, client_sites=destination)

        result = await client_sites.migrate_legacy_client_sites(db)

        self.assertEqual(result["migrated"], 1)
        migrated = destination.update_one.await_args.args[1]["$setOnInsert"]
        self.assertEqual(migrated["client_type"], "newapi")
        self.assertEqual(migrated["api_key"], "legacy-secret")
        source_updates = source.update_one.await_args.args[1]["$set"]
        self.assertEqual(source_updates["status"], "deleted")
        self.assertEqual(source_updates["migrated_to_client_site_id"], "newapi-us01")


if __name__ == "__main__":
    unittest.main()
