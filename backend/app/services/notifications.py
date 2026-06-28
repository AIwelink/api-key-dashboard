import asyncio
import base64
import hashlib
import hmac
import json
import secrets
from typing import Any
from urllib import error as urllib_error
from urllib.parse import quote_plus, urlencode
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


def _redact_token(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 12:
        return f"{value[:4]}..."
    return f"{value[:6]}...{value[-5:]}"


def _public_channel(document: dict[str, Any]) -> dict[str, Any]:
    data = serialize_doc(document)
    config = data.get("config") if isinstance(data.get("config"), dict) else {}
    data["webhook_configured"] = bool(config.get("webhook_url"))
    data["signing_secret_configured"] = bool(config.get("signing_secret"))
    data["webhook_preview"] = _redact_webhook(config.get("webhook_url"))
    data["telegram_bot_token_configured"] = bool(config.get("telegram_bot_token"))
    data["telegram_bot_token_preview"] = _redact_token(config.get("telegram_bot_token"))
    data["telegram_chat_id"] = config.get("telegram_chat_id") or None
    data.pop("config", None)
    return data


def _channel_config_from_create(payload: NotificationChannelCreate) -> dict[str, str]:
    if payload.channel_type == "telegram":
        return {
            "telegram_bot_token": str(payload.telegram_bot_token or "").strip(),
            "telegram_chat_id": str(payload.telegram_chat_id or "").strip(),
        }
    return {
        "webhook_url": str(payload.webhook_url or "").strip(),
        "signing_secret": str(payload.signing_secret or "").strip(),
    }


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
        "config": _channel_config_from_create(payload),
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
    if payload.telegram_bot_token is not None:
        updates["config.telegram_bot_token"] = payload.telegram_bot_token.strip()
    if payload.telegram_chat_id is not None:
        updates["config.telegram_chat_id"] = payload.telegram_chat_id.strip()
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
        elif channel_type == "telegram":
            result = await _send_telegram_test(document, actor=actor)
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


async def send_notification_event(
    db: AsyncIOMotorDatabase,
    *,
    event_type: str,
    title: str,
    text: str,
    markdown_text: str | None = None,
    severity: str = "info",
    source: str = "system",
    resource_type: str | None = None,
    resource_id: str | None = None,
    payload: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    channel_ids: list[str] | None = None,
) -> dict[str, Any]:
    now = now_utc()
    event_id = secrets.token_hex(12)
    event_doc = {
        "_id": event_id,
        "event_type": event_type,
        "severity": severity,
        "source": source,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "dedupe_key": dedupe_key,
        "title": title,
        "text": text,
        "markdown_text": markdown_text,
        "payload": payload or {},
        "status": "pending",
        "channel_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "created_at": now,
        "updated_at": now,
    }
    await db.notification_events.insert_one(event_doc)

    query: dict[str, Any] = {"status": "active"}
    if channel_ids is not None:
        query["_id"] = {"$in": channel_ids}
    channels = [item async for item in db.notification_channels.find(query).sort("created_at", 1)]
    items: list[dict[str, Any]] = []
    success = 0
    failed = 0
    for channel in channels:
        channel_id = str(channel.get("_id"))
        channel_type = str(channel.get("channel_type") or "")
        try:
            if channel_type == "dingtalk":
                result = await _send_dingtalk_message(channel, title=title, markdown_text=markdown_text or text)
            elif channel_type == "telegram":
                result = await _send_telegram_message(channel, text=text)
            else:
                raise ValueError(f"暂不支持的通知类型：{channel_type}")
            success += 1
            status_value = "success"
            message = result.get("message") or "通知已发送"
        except Exception as exc:  # noqa: BLE001 - notification failures must not break probes.
            failed += 1
            status_value = "failed"
            message = str(exc) or exc.__class__.__name__
        item = {
            "channel_id": channel_id,
            "channel_name": channel.get("name"),
            "channel_type": channel_type,
            "status": status_value,
            "message": message,
            "attempted_at": now,
        }
        items.append(item)
        await db.notification_deliveries.insert_one(
            {
                "_id": secrets.token_hex(12),
                **item,
                "notification_event_id": event_id,
                "event_type": event_type,
                "severity": severity,
                "title": title,
                "created_at": now,
            }
        )
        await db.notification_channels.update_one(
            {"_id": channel_id},
            {
                "$set": {
                    "last_delivery_at": now,
                    "last_delivery_status": status_value,
                    "last_delivery_message": message,
                    "updated_at": now,
                }
            },
        )
    final_status = "skipped" if not channels else "success" if failed == 0 else "failed" if success == 0 else "partial"
    finished_at = now_utc()
    await db.notification_events.update_one(
        {"_id": event_id},
        {
            "$set": {
                "status": final_status,
                "channel_count": len(channels),
                "success_count": success,
                "failed_count": failed,
                "finished_at": finished_at,
                "updated_at": finished_at,
            }
        },
    )
    event_doc.update(
        {
            "status": final_status,
            "channel_count": len(channels),
            "success_count": success,
            "failed_count": failed,
            "finished_at": finished_at,
            "updated_at": finished_at,
        }
    )
    return {"event": serialize_doc(event_doc), "total": len(channels), "success": success, "failed": failed, "items": serialize_doc(items)}


async def send_notification_to_active_channels(
    db: AsyncIOMotorDatabase,
    *,
    title: str,
    text: str,
    markdown_text: str | None = None,
    event_type: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return await send_notification_event(
        db,
        event_type=event_type,
        title=title,
        text=text,
        markdown_text=markdown_text,
        payload=payload,
    )


async def _send_dingtalk_test(document: dict[str, Any], *, actor: dict[str, Any]) -> dict[str, Any]:
    config = document.get("config") if isinstance(document.get("config"), dict) else {}
    webhook_url = str(config.get("webhook_url") or "").strip()
    signing_secret = str(config.get("signing_secret") or "").strip()
    if not webhook_url:
        raise ValueError("钉钉 Webhook 地址未配置")
    if not signing_secret:
        raise ValueError("钉钉加签密钥未配置")

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
    return await _send_dingtalk_message(document, title="AIwelink 通知测试", markdown_text=content)


async def _send_telegram_test(document: dict[str, Any], *, actor: dict[str, Any]) -> dict[str, Any]:
    config = document.get("config") if isinstance(document.get("config"), dict) else {}
    bot_token = str(config.get("telegram_bot_token") or "").strip()
    chat_id = str(config.get("telegram_chat_id") or "").strip()
    if not bot_token:
        raise ValueError("Telegram Bot Token 未配置")
    if not chat_id:
        raise ValueError("Telegram Chat ID 未配置")

    settings = get_settings()
    actor_name = actor.get("name") or actor.get("email") or actor.get("_id") or "未知用户"
    text = "\n".join(
        [
            "AIwelink 通知测试",
            f"通知名称：{document.get('name') or document.get('_id')}",
            f"系统：{settings.app_name}",
            f"操作人：{actor_name}",
            f"时间：{now_utc().isoformat()}",
        ]
    )
    return await _send_telegram_message(document, text=text)


async def _send_dingtalk_message(document: dict[str, Any], *, title: str, markdown_text: str) -> dict[str, Any]:
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
    payload = {
        "msgtype": "markdown",
        "markdown": {
            "title": title,
            "text": markdown_text,
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
        return {"message": data.get("errmsg") or "钉钉通知已发送"}
    return {"message": text or "钉钉通知已发送"}


async def _send_telegram_message(document: dict[str, Any], *, text: str) -> dict[str, Any]:
    config = document.get("config") if isinstance(document.get("config"), dict) else {}
    bot_token = str(config.get("telegram_bot_token") or "").strip()
    chat_id = str(config.get("telegram_chat_id") or "").strip()
    if not bot_token:
        raise ValueError("Telegram Bot Token 未配置")
    if not chat_id:
        raise ValueError("Telegram Chat ID 未配置")

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }
    status_code, response_text = await asyncio.to_thread(_post_form_sync, url, payload)
    if status_code >= 400:
        raise RuntimeError(f"Telegram 请求失败：HTTP {status_code} {response_text}")
    try:
        data = json.loads(response_text) if response_text else None
    except ValueError:
        data = None
    if isinstance(data, dict) and data.get("ok") is not True:
        raise RuntimeError(f"Telegram 返回失败：{data}")
    return {"message": "Telegram 通知已发送"}


def _post_json_sync(url: str, payload: dict[str, Any]) -> tuple[int, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib_request.Request(url, data=data, headers={"Content-Type": "application/json; charset=utf-8"}, method="POST")
    try:
        with urllib_request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib_error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _post_form_sync(url: str, payload: dict[str, Any]) -> tuple[int, str]:
    data = urlencode(payload).encode("utf-8")
    request = urllib_request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded; charset=utf-8"}, method="POST")
    try:
        with urllib_request.urlopen(request, timeout=15) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib_error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
