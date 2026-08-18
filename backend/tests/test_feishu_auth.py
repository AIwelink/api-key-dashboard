from __future__ import annotations

import unittest
from datetime import UTC, datetime

from app import schemas
from app.config import Settings


class FeishuConfigurationTests(unittest.TestCase):
    def test_allowed_tenant_keys_are_normalized(self) -> None:
        settings = Settings(feishu_allowed_tenant_keys=" tenant-b,tenant-a, tenant-b ,, ")

        self.assertTrue(hasattr(settings, "allowed_feishu_tenant_keys"))
        self.assertEqual(settings.allowed_feishu_tenant_keys(), {"tenant-a", "tenant-b"})

    def test_binding_required_response_is_explicit_and_timezone_aware(self) -> None:
        self.assertTrue(hasattr(schemas, "LoginBindingRequiredResponse"))
        response_type = schemas.LoginBindingRequiredResponse
        expires_at = datetime(2026, 8, 18, 12, tzinfo=UTC)

        response = response_type(
            authorization_url="https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example",
            session_id="session-1",
            expires_at=expires_at,
        )

        self.assertEqual(response.status, "binding_required")
        self.assertEqual(response.expires_at, expires_at)

    def test_binding_required_response_rejects_naive_expiration(self) -> None:
        self.assertTrue(hasattr(schemas, "LoginBindingRequiredResponse"))
        response_type = schemas.LoginBindingRequiredResponse

        with self.assertRaises(ValueError):
            response_type(
                authorization_url="https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example",
                session_id="session-1",
                expires_at=datetime(2026, 8, 18, 12),
            )


if __name__ == "__main__":
    unittest.main()
