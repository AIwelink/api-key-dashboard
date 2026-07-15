from __future__ import annotations

import unittest

from app.modules.sub2api import cache


class Sub2ApiSiteRefreshIntervalTests(unittest.TestCase):
    def test_public_site_supplies_refresh_interval_for_legacy_site(self) -> None:
        site = cache.public_site({"_id": "api-5001", "token": "secret"})

        self.assertEqual(site["refresh_interval_minutes"], 30)
        self.assertTrue(site["token_configured"])
        self.assertNotIn("token", site)

    def test_site_refresh_interval_uses_site_value_and_clamps_range(self) -> None:
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": 90}), 90)
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": 10}), 10)
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": 0}), 30)
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": -5}), 1)
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": 3000}), 1440)
        self.assertEqual(cache._site_refresh_interval_minutes({"refresh_interval_minutes": "invalid"}), 30)


if __name__ == "__main__":
    unittest.main()
