from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from app.modules.sub2api import cache


class Sub2ApiSiteRefreshIntervalTests(unittest.TestCase):
    def test_public_site_supplies_refresh_interval_for_legacy_site(self) -> None:
        site = cache.public_site({"_id": "api-5001", "token": "secret"})

        self.assertEqual(site["refresh_interval_minutes"], 1)
        self.assertEqual(site["site_type"], "sub2api")
        self.assertTrue(site["token_configured"])
        self.assertNotIn("token", site)

    def test_site_refresh_interval_uses_site_value_and_clamps_range(self) -> None:
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": 90}), 90)
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": 10}), 10)
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": 0}), 1)
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": -5}), 1)
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": 3000}), 1440)
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": "invalid"}), 1)

    def test_public_site_supplies_long_seven_day_probe_model_for_legacy_site(self) -> None:
        site = cache.public_site({"_id": "api-5001", "token": "secret"})

        self.assertEqual(site["long_7d_probe_model"], "gpt-5.5")


class Sub2ApiAdminTokenValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_rejects_non_ascii_or_whitespace_admin_token(self) -> None:
        sites = SimpleNamespace(replace_one=AsyncMock(), find_one=AsyncMock(return_value=None))
        db = SimpleNamespace(sub2api_sites=sites)

        with self.assertRaisesRegex(ValueError, "ASCII"):
            await cache.create_site_config(
                db,
                {
                    "id": "api-5002",
                    "base_url": "https://sub2api.example.com",
                    "site_type": "sub2api",
                    "token": "key-中文 value",
                },
            )

        sites.replace_one.assert_not_awaited()


class LongSevenDayProbeModelSiteSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def test_create_persists_trimmed_probe_model(self) -> None:
        stored = {
            "_id": "api-5001",
            "name": "Sub2API US06",
            "base_url": "https://sub2api.example.com",
            "site_type": "sub2api",
            "long_7d_probe_model": "gpt-5.6",
            "status": "active",
        }
        sites = SimpleNamespace(replace_one=AsyncMock(), find_one=AsyncMock(return_value=stored))
        db = SimpleNamespace(sub2api_sites=sites)

        result = await cache.create_site_config(
            db,
            stored | {"id": stored["_id"], "long_7d_probe_model": "  gpt-5.6  "},
        )

        self.assertEqual(sites.replace_one.await_args.args[1]["long_7d_probe_model"], "gpt-5.6")
        self.assertEqual(result["long_7d_probe_model"], "gpt-5.6")

    async def test_blank_update_restores_default_probe_model(self) -> None:
        current = {
            "_id": "api-5001",
            "site_type": "sub2api",
            "base_url": "https://sub2api.example.com",
            "long_7d_probe_model": "gpt-5.4",
            "status": "active",
        }
        updated = current | {"long_7d_probe_model": "gpt-5.5"}
        sites = SimpleNamespace(
            find_one=AsyncMock(side_effect=[current, updated]),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(sub2api_sites=sites)

        result = await cache.update_site_config(db, "api-5001", {"long_7d_probe_model": ""})

        self.assertEqual(sites.update_one.await_args.args[1]["$set"]["long_7d_probe_model"], "gpt-5.5")
        self.assertEqual(result["long_7d_probe_model"], "gpt-5.5")


class UptimeKumaSiteSettingsTests(unittest.IsolatedAsyncioTestCase):
    def test_public_site_masks_uptime_kuma_api_key(self) -> None:
        site = cache.public_site(
            {
                "_id": "api-5001",
                "token": "sub2-secret",
                "uptime_kuma_url": "https://status.aiwelink.cn",
                "uptime_kuma_api_key": "uptime-secret",
            }
        )

        self.assertEqual(site["uptime_kuma_url"], "https://status.aiwelink.cn")
        self.assertTrue(site["uptime_kuma_api_key_configured"])
        self.assertNotIn("uptime_kuma_api_key", site)

    async def test_create_persists_uptime_kuma_connection(self) -> None:
        stored = {
            "_id": "api-5001",
            "name": "Sub2API US06",
            "base_url": "https://sub2api.example.com",
            "site_type": "sub2api",
            "token": "sub2-secret",
            "uptime_kuma_url": "https://status.aiwelink.cn",
            "uptime_kuma_api_key": "uptime-secret",
            "status": "active",
        }
        sites = SimpleNamespace(replace_one=AsyncMock(), find_one=AsyncMock(return_value=stored))
        db = SimpleNamespace(sub2api_sites=sites)

        result = await cache.create_site_config(db, stored | {"id": stored["_id"]})

        saved = sites.replace_one.await_args.args[1]
        self.assertEqual(saved["uptime_kuma_url"], "https://status.aiwelink.cn")
        self.assertEqual(saved["uptime_kuma_api_key"], "uptime-secret")
        self.assertTrue(result["uptime_kuma_api_key_configured"])
        self.assertNotIn("uptime_kuma_api_key", result)

    async def test_blank_update_does_not_clear_existing_uptime_kuma_api_key(self) -> None:
        current = {
            "_id": "api-5001",
            "site_type": "sub2api",
            "base_url": "https://sub2api.example.com",
            "token": "sub2-secret",
            "uptime_kuma_url": "https://status.aiwelink.cn",
            "uptime_kuma_api_key": "uptime-secret",
            "status": "active",
        }
        sites = SimpleNamespace(
            find_one=AsyncMock(side_effect=[current, current]),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(sub2api_sites=sites)

        result = await cache.update_site_config(
            db,
            "api-5001",
            {"uptime_kuma_url": "https://status.aiwelink.cn/", "uptime_kuma_api_key": ""},
        )

        updates = sites.update_one.await_args.args[1]["$set"]
        self.assertEqual(updates["uptime_kuma_url"], "https://status.aiwelink.cn")
        self.assertNotIn("uptime_kuma_api_key", updates)
        self.assertTrue(result["uptime_kuma_api_key_configured"])


class Sub2ApiSqlDsnTests(unittest.IsolatedAsyncioTestCase):
    def test_public_site_masks_postgresql_sql_dsn(self) -> None:
        site = cache.public_site(
            {
                "_id": "api-5001",
                "site_type": "sub2api",
                "sql_dsn": "host=postgres.internal port=5433 user=reader password=secret dbname=sub2api sslmode=disable",
            }
        )

        self.assertTrue(site["sql_dsn_configured"])
        self.assertEqual(site["database_type"], "postgresql")
        self.assertEqual(site["database_endpoint"], "postgres.internal:5433/sub2api")
        self.assertNotIn("sql_dsn", site)
        self.assertNotIn("reader", str(site))
        self.assertNotIn("secret", str(site))

    async def test_create_persists_postgresql_sql_dsn(self) -> None:
        stored = {
            "_id": "api-5001",
            "name": "Sub2API US06",
            "base_url": "https://sub2api.example.com",
            "site_type": "sub2api",
            "sql_dsn": "host=postgres.internal port=5432 user=reader password=secret dbname=sub2api sslmode=disable",
            "status": "active",
        }
        sites = SimpleNamespace(replace_one=AsyncMock(), find_one=AsyncMock(return_value=stored))
        db = SimpleNamespace(sub2api_sites=sites)

        result = await cache.create_site_config(db, stored | {"id": stored["_id"]})

        self.assertEqual(sites.replace_one.await_args.args[1]["sql_dsn"], stored["sql_dsn"])
        self.assertTrue(result["sql_dsn_configured"])

    async def test_create_rejects_mysql_sql_dsn(self) -> None:
        sites = SimpleNamespace(replace_one=AsyncMock(), find_one=AsyncMock(return_value=None))
        db = SimpleNamespace(sub2api_sites=sites)

        with self.assertRaisesRegex(ValueError, "PostgreSQL"):
            await cache.create_site_config(
                db,
                {
                    "id": "api-5001",
                    "base_url": "https://sub2api.example.com",
                    "sql_dsn": "reader:secret@tcp(mysql.internal:3306)/sub2api",
                },
            )

        sites.replace_one.assert_not_awaited()

    async def test_blank_sql_dsn_update_preserves_secret(self) -> None:
        current = {
            "_id": "api-5001",
            "site_type": "sub2api",
            "base_url": "https://sub2api.example.com",
            "sql_dsn": "host=postgres.internal user=reader password=secret dbname=sub2api sslmode=disable",
            "status": "active",
        }
        sites = SimpleNamespace(
            find_one=AsyncMock(side_effect=[current, current]),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(sub2api_sites=sites)

        result = await cache.update_site_config(db, "api-5001", {"sql_dsn": ""})

        self.assertNotIn("sql_dsn", sites.update_one.await_args.args[1]["$set"])
        self.assertTrue(result["sql_dsn_configured"])


class AsyncCursor:
    def __init__(self, items: list[dict[str, object]]) -> None:
        self.items = items

    def sort(self, *_args):
        return self

    def __aiter__(self):
        return self._iterate()

    async def _iterate(self):
        for item in self.items:
            yield item


class AccountPoolSiteSeparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_account_pool_site_rejects_newapi_type(self) -> None:
        sites = SimpleNamespace(replace_one=AsyncMock(), find_one=AsyncMock())
        db = SimpleNamespace(sub2api_sites=sites)

        with self.assertRaisesRegex(ValueError, "client-sites"):
            await cache.create_site_config(
                db,
                {
                    "id": "newapi-us01",
                    "name": "NewAPI US01",
                    "base_url": "https://newapi.example.com",
                    "site_type": "newapi",
                    "token": "secret",
                },
            )

        sites.replace_one.assert_not_awaited()

    async def test_list_sites_can_filter_sub2api_and_include_legacy_records(self) -> None:
        cursor = AsyncCursor([])
        sites = SimpleNamespace(find=MagicMock(return_value=cursor))
        db = SimpleNamespace(sub2api_sites=sites)

        await cache.list_sites(db, site_type="sub2api")

        self.assertEqual(sites.find.call_args.args[0], cache.sub2api_site_query(status={"$ne": "deleted"}))

    async def test_unfiltered_account_pool_list_excludes_client_site_records(self) -> None:
        cursor = AsyncCursor([])
        sites = SimpleNamespace(find=MagicMock(return_value=cursor))
        db = SimpleNamespace(sub2api_sites=sites)

        await cache.list_sites(db)

        self.assertEqual(sites.find.call_args.args[0], cache.sub2api_site_query(status={"$ne": "deleted"}))

    async def test_account_pool_list_rejects_newapi_filter(self) -> None:
        sites = SimpleNamespace(find=MagicMock())
        db = SimpleNamespace(sub2api_sites=sites)

        with self.assertRaisesRegex(ValueError, "client-sites"):
            await cache.list_sites(db, site_type="newapi")

        sites.find.assert_not_called()

    async def test_get_account_pool_site_excludes_client_site_records(self) -> None:
        sites = SimpleNamespace(find_one=AsyncMock(return_value=None))
        db = SimpleNamespace(sub2api_sites=sites)

        self.assertIsNone(await cache.get_site(db, "customer-newapi-us01"))

        expected = cache.sub2api_site_query(status={"$ne": "deleted"}) | {"_id": "customer-newapi-us01"}
        self.assertEqual(sites.find_one.await_args.args[0], expected)

    def test_sub2api_query_accepts_explicit_and_legacy_site_types(self) -> None:
        self.assertEqual(
            cache.sub2api_site_query(status="active"),
            {
                "status": "active",
                "$or": [
                    {"site_type": "sub2api"},
                    {"site_type": {"$exists": False}},
                    {"site_type": None},
                    {"site_type": ""},
                ],
            },
        )


if __name__ == "__main__":
    unittest.main()
