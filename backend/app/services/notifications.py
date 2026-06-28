import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from typing import Any
from urllib import error as urllib_error
from urllib.parse import quote_plus
from urllib import request as urllib_request

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.schemas import NotificationChannelCreate, NotificationChannelUpdate
from app.utils import now_utc, serialize_doc


def _redact_webhook(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 24:
        return f"{value[:8]}..."
    return f"{value[:18]}...{value[-8:]}"


def _public_channel(document: dict[str, Any]) -> dict[str, Any]:
    data = serialize_doc(document)
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    data["webhook_configured"] = bool(config.get("webhook_url"))
    data["signing_secret_configured"] = bool(config.get("signing_secret"))
    data["webhook_preview"] = _redact_webhook(config.get("webhook_url"))
    data.pop("config", None)
    return data


async def list_notification_channels(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    items = [_public_channel(item) async for item in db.notification_channels.find({}).sort("created_at", -1)]
    return {"items": items, "total": len(items)}


async def create_notification_channel(
    db: AsyncIOMotorDatabase,
    *,
    payload: NotificationChannelCreate,
    actor: dict[str, Any],
) -> dict[str, Any]:
    now = now_utc()
    channel_id = secrets.token_hex(12)
    document = {
        "_id": channel_id,
        "name": payload.name.strip(),
        "channel_type": payload.channel_type,
        "status": payload.status,
        "config": {
            "webhook_url": payload.webhook_url.strip(),
            "signing_secret": payload.signing_secret.strip(),
        },
        "note": payload.note.strip() if payload.note else None,
        "last_test_at": None,
        "last_test_status": None,
        "last_test_message": None,
        "created_by": actor.get("_id"),
        "updated_by": actor.get("_id"),
        "created_at": now,
        "updated_at": now,
    }
    await db.notification_channels.insert_one(document)
    return _public_channel(document)


async def update_notification_channel(
    db: AsyncIOMotorDatabase,
    *,
    channel_id: str,
    payload: NotificationChannelUpdate,
    actor: dict[str, Any],
) -> dict[str, Any] | None:
    existing = await db.notification_channels.find_one({"_id": channel_id})
    if not existing:
        return None
    now = now_utc()
    updates: dict[str, Any] = {
        "updated_by": actor.get("_id"),
        "updated_at": now,
    }
    if payload.name is not None:
        updates["name"] = payload.name.strip()
    if payload.status is not None:
        updates["status"] = payload.status
    if payload.note is not None:
        updates["note"] = payload.note.strip() or None
    if payload.webhook_url is not None:
        updates["config.webhook_url"] = payload.webhook_url.strip()
    if payload.signing_secret is not None:
        updates["config.signing_secret"] = payload.signing_secret.strip()
    await db.notification_channels.update_one({"_id": channel_id}, {"$set": updates})
    document = await db.notification_channels.find_one({"_id": channel_id})
    return _public_channel(document) if document else None


async def delete_notification_channel(db: AsyncIOMotorDatabase, *, channel_id: str) -> bool:
    result = await db.notification_channels.delete_one({"_id": channel_id})
    return result.deleted_count > 0


async def test_notification_channel(db: AsyncIOMotorDatabase, *, channel_id: str, actor: dict[str, Any]) -> dict[str, Any] | None:
    document = await db.notification_channels.find_one({"_id": channel_id})
    if not document:
        return None
    now = now_utc()
    try:
        if document.get("status") != "active":
            raise ValueError("通知配置已停用")
        channel_type = document.get("channel_type")
        if channel_type == "dingtalk":
            result = await _send_dingtalk_test(document, actor=actor)
        else:
            raise ValueError(f"暂不支持的通知类型：{channel_type}")
        await db.notification_channels.update_one(
            {"_id": channel_id},
            {
                "$set": {
                    "last_test_at": now,
                    "last_test_status": "success",
                    "last_test_message": result.get("message") or "测试通知已发送",
                    "updated_at": now,
                }
            },
        )
        document = await db.notification_channels.find_one({"_id": channel_id})
        return {"ok": True, "message": result.get("message") or "测试通知已发送", "channel": _public_channel(document or {})}
    except Exception as exc:
        message = str(exc) or exc.__class__.__name__
        await db.notification_channels.update_one(
            {"_id": channel_id},
            {
                "$set": {
                    "last_test_at": now,
                    "last_test_status": "failed",
                    "last_test_message": message,
                    "updated_at": now,
                }
            },
        )
        document = await db.notification_channels.find_one({"_id": channel_id})
        return {"ok": False, "message": message, "channel": _public_channel(document or {})}


async def _send_dingtalk_test(document: dict[str, Any], *, actor: dict[str, Any]) -> dict[str, Any]:
    config = document.get("config") if isinstance(document.get("config"), dict) else {}
    webhook_url = str(config.get("webhook_url") or "").strip()
    signing_secret = str(config.get("signing_secret") or "").strip()
    if not webhook_url:
        raise ValueError("钉钉 Webhook 地址未配置")
    if not signing_secret:
        raise ValueError("钉钉加签密钥未配置")

    timestamp = str(int(now_utc().timestamp() * 1000))
    string_to_sign = f"{timestamp}\n{signing_secret}"
    signature = hmac.new(signing_secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    sign = quote_plus(base64.b64encode(signature).decode("utf-8"))
    separator = "&" if "?" in webhook_url else "?"
    signed_url = f"{webhook_url}{separator}timestamp={timestamp}&sign={sign}"
    settings = get_settings()
    actor_name = actor.get("name") or actor.get("email") or actor.get("_id") or "未知用户"
    content = "\n".join(
        [
            "### AIwelink 通知测试",
            f"- 通知名称：{document.get('name') or document.get('_id')}",
            f"- 系统：{settings.app_name}",
            f"- 操作人：{actor_name}",
            f"- 时间：{now_utc().isoformat()}",
        ]
    )
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": "AIwelink 通知测试",
            "text": content,
        },
    }
    status_code, text = await asyncio.to_thread(_post_json_sync, signed_url, payload)
    if status_code >= 400:
        raise RuntimeError(f"钉钉 Webhook 请求失败：HTTP {status_code} {text}")
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    if isinstance(data, dict):
        errcode = data.get("errcode")
        if errcode not in (0, "0", None):
            raise RuntimeError(f"钉钉 Webhook 返回失败：{data}")
        return {"message": data.get("errmsg") or "钉钉测试通知已发送"}
    return {"message": text or "钉钉测试通知已发送"}


def _post_json_sync(url: str, payload: dict[str, Any]) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib_request.Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with urllib_request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib_error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
