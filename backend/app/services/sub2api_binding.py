from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.accounts import get_account_or_404
from app.services.pool_lifecycle import actor_name, write_pool_action
from app.utils import now_utc, serialize_doc


async def manually_unbind_sub2api_account(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    account = await get_account_or_404(db, account_id)
    metadata = dict(account.get("metadata", {}))
    remote_id = metadata.get("sub2api_account_id")
    if remote_id is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account is not bound to a sub2api account")

    now = now_utc()
    history_item = {
        "sub2api_account_id": remote_id,
        "sub2api_site_id": metadata.get("sub2api_site_id"),
        "sub2api_group_id": metadata.get("sub2api_group_id"),
        "sub2api_group_ids": metadata.get("sub2api_group_ids"),
        "unbound_at": now,
        "unbound_by_user_id": actor.get("_id"),
        "unbound_by_name": actor_name(actor),
    }
    await db.accounts.update_one(
        {"_id": account["_id"]},
        {
            "$set": {
                "metadata.sub2api_push_status": "manual_unbound",
                "metadata.sub2api_manual_unbound": True,
                "metadata.sub2api_manual_unbound_at": now,
                "metadata.sub2api_manual_unbound_by_user_id": actor.get("_id"),
                "metadata.sub2api_manual_unbound_by_name": actor_name(actor),
                "metadata.sub2api_last_error": None,
                "metadata.last_error": None,
                "metadata.updated_at": now,
                "metadata.updated_by_user_id": actor.get("_id"),
                "metadata.updated_by_name": actor_name(actor),
            },
            "$unset": {
                "metadata.sub2api_account_id": "",
                "metadata.sub2api_group_ids": "",
                "metadata.sub2api_last_sync_at": "",
                "metadata.sub2api_pushed_at": "",
                "metadata.push_lock": "",
            },
            "$push": {"metadata.sub2api_unbind_history": history_item},
        },
    )
    updated = await db.accounts.find_one({"_id": account["_id"]})
    await write_pool_action(
        db,
        action_type="manual_unbind_sub2api_account",
        actor=actor,
        account_id=account_id,
        pool_id=str(metadata.get("pool_id") or metadata.get("sub2api_group_id") or ""),
        status_value="succeeded",
        reason="manual unbind local sub2api account binding",
        before={
            "sub2api_account_id": remote_id,
            "sub2api_site_id": metadata.get("sub2api_site_id"),
            "sub2api_group_id": metadata.get("sub2api_group_id"),
        },
        after={"sub2api_account_id": None},
    )
    return serialize_doc(updated)
