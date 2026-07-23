from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import NotificationChannelCreate, NotificationChannelUpdate
from app.security import get_current_user
from app.modules.system.permissions import require_view_permission
from app.modules.system.audit import write_audit_log
from app.modules.notifications.service import (
    create_notification_channel,
    delete_notification_channel,
    list_notification_channels,
    test_notification_channel,
    update_notification_channel,
)


router = APIRouter(
    prefix="/notification-channels",
    tags=["notification-channels"],
    dependencies=[Depends(require_view_permission("system-management"))],
)


@router.get("")
async def get_notification_channels(
    _: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_notification_channels(db)


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_notification_channel(
    payload: NotificationChannelCreate,
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    created = await create_notification_channel(db, payload=payload, actor=actor)
    await write_audit_log(
        db,
        actor=actor,
        action="notification_channel.create",
        resource_type="notification_channel",
        resource_id=created["id"],
        after=created,
    )
    return created


@router.put("/{channel_id}")
async def put_notification_channel(
    channel_id: str,
    payload: NotificationChannelUpdate,
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_notification_channel(db, channel_id=channel_id, payload=payload, actor=actor)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification channel not found")
    await write_audit_log(
        db,
        actor=actor,
        action="notification_channel.update",
        resource_type="notification_channel",
        resource_id=channel_id,
        after=updated,
    )
    return updated


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_notification_channel_route(
    channel_id: str,
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> None:
    deleted = await delete_notification_channel(db, channel_id=channel_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification channel not found")
    await write_audit_log(
        db,
        actor=actor,
        action="notification_channel.delete",
        resource_type="notification_channel",
        resource_id=channel_id,
    )


@router.post("/{channel_id}/test")
async def post_notification_channel_test(
    channel_id: str,
    actor: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await test_notification_channel(db, channel_id=channel_id, actor=actor)
    if not result:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification channel not found")
    await write_audit_log(
        db,
        actor=actor,
        action="notification_channel.test",
        resource_type="notification_channel",
        resource_id=channel_id,
        after={"ok": result.get("ok"), "message": result.get("message")},
    )
    if not result.get("ok"):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=result.get("message") or "Notification test failed")
    return result
