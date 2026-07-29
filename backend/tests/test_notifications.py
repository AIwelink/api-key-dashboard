from __future__ import annotations

import base64
import hashlib
import hmac
import unittest
from datetime import UTC, datetime
from unittest.mock import patch

from pydantic import ValidationError

from app.modules.notifications import service
from app.schemas import NotificationChannelCreate


class FeishuNotificationTests(unittest.IsolatedAsyncioTestCase):
    def test_feishu_create_requires_webhook_but_not_signing_secret(self) -> None:
        channel = NotificationChannelCreate(
            name="飞书预警",
            channel_type="feishu",
            webhook_url="https://open.feishu.cn/open-apis/bot/v2/hook/test",
        )

        self.assertEqual(channel.channel_type, "feishu")
        self.assertIsNone(channel.signing_secret)
        with self.assertRaises(ValidationError):
            NotificationChannelCreate(name="缺少 Webhook", channel_type="feishu")

    async def test_feishu_message_uses_official_signature_payload(self) -> None:
        fixed_now = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)
        document = {
            "config": {
                "webhook_url": "https://open.feishu.cn/open-apis/bot/v2/hook/test",
                "signing_secret": "secret",
            }
        }

        with (
            patch.object(service, "now_utc", return_value=fixed_now),
            patch.object(service, "_post_json_sync", return_value=(200, '{"code":0,"msg":"success"}')) as post,
        ):
            result = await service._send_feishu_message(document, title="容量预警", text="账号池容量紧张")

        url, payload = post.call_args.args
        timestamp = str(int(fixed_now.timestamp()))
        expected_signature = base64.b64encode(
            hmac.new(f"{timestamp}\nsecret".encode("utf-8"), digestmod=hashlib.sha256).digest()
        ).decode("utf-8")
        self.assertEqual(url, document["config"]["webhook_url"])
        self.assertEqual(payload["timestamp"], timestamp)
        self.assertEqual(payload["sign"], expected_signature)
        self.assertEqual(payload["content"]["text"], "容量预警\n账号池容量紧张")
        self.assertEqual(result["message"], "success")


if __name__ == "__main__":
    unittest.main()
