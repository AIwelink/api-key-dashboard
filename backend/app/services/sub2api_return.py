from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Literal

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.services.accounts import create_account
from app.services.pool_lifecycle import actor_name, write_pool_action
from app.services.sub2api import Sub2ApiClient
from app.services.sub2api_cache import DEFAULT_SITE_ID, refresh_site_cache
from app.utils import extract_email, now_utc, object_id, serialize_doc


logger = logging.getLogger("app.sub2api_return")

ReturnTargetStatus = Literal["available", "library"]
REMOTE_DELETE_LOCK_TTL_SECONDS = 300

REMOTE_ACCOUNT_STRIP_FIELDS = {
    "id",
    "created_at",
    "updated_at",
    "last_used_at",
    "error_message",
    "credentials_status",
    "current_concurrency",
    "rate_limited_at",
    "rate_limit_reset_at",
    "overload_until",
    "temp_unschedulable_until",
    "temp_unschedulable_reason",
    "session_window_start",
    "session_window_end",
    "session_window_status",
    "account_groups",
    "groups",
    "group",
    "group_id",
    "group_ids",
    "schedulable",
    "codex_5h_used_percent",
    "codex_7d_used_percent",
    "codex_5h_reset_after_seconds",
    "codex_7d_reset_after_seconds",
    "codex_5h_reset_at",
    "codex_7d_reset_at",
    "codex_usage_updated_at",
    "codex_usage_synced_at",
    "codex_5h_request_count",
    "codex_7d_request_count",
    "codex_5h_token_count",
    "codex_7d_token_count",
    "codex_5h_actual_cost",
    "codex_7d_actual_cost",
    "codex_5h_total_cost",
    "codex_7d_total_cost",
}


async def manual_delete_sub2api_account(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account_id: int,
    target_status: ReturnTargetStatus,
    reason: str | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    if site_id != DEFAULT_SITE_ID:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")

    lock_id = f"{site_id}:{remote_account_id}:manual_delete"
    remote_lock = await _acquire_remote_delete_lock(db, lock_id=lock_id, remote_account_id=remote_account_id, actor=actor)
    if remote_lock is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Remote account delete is already running")

    cached = await db.sub2api_accounts_cache.find_one({"site_id": site_id, "sub2api_account_id": remote_account_id})
    remote_account = cached.get("account", {}) if cached and isinstance(cached.get("account"), dict) else None
    client = Sub2ApiClient()
    if remote_account is None:
        remote_account = await client.get_account(remote_account_id)
    if not isinstance(remote_account, dict) or not remote_account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remote sub2api account not found")

    group_ids = _remote_group_ids(remote_account)
    primary_group_id = group_ids[0] if group_ids else None
    group_name = _remote_group_names(remote_account)[0] if _remote_group_names(remote_account) else None
    is_abnormal = _is_remote_abnormal(remote_account)
    now = now_utc()
    account_json = _remote_to_account_json(remote_account)
    metadata_updates = {
        "source": "sub2api_manual_return",
        "pool_status": target_status,
        "pool_id": None,
        "sub2api_site_id": site_id,
        "sub2api_account_id": remote_account_id,
        "sub2api_group_id": primary_group_id,
        "sub2api_group_ids": group_ids,
        "sub2api_group_name": group_name,
        "sub2api_manual_deleted": True,
        "sub2api_deleted_at": now,
        "sub2api_deleted_by_user_id": actor.get("_id"),
        "sub2api_deleted_by_name": actor_name(actor),
        "sub2api_delete_mode": "manual",
        "sub2api_delete_target_status": target_status,
        "sub2api_delete_reason": reason,
        "sub2api_delete_status": "pending",
        "sub2api_return_snapshot": remote_account,
        "remote_status_at_return": remote_account.get("status"),
        "remote_schedulable_at_return": remote_account.get("schedulable"),
        "remote_error_at_return": remote_account.get("error_message"),
        "remote_last_used_at_return": remote_account.get("last_used_at"),
        "return_is_abnormal": is_abnormal,
        "return_health_status": "abnormal" if is_abnormal else "normal",
        "return_test_status": "not_tested",
        "return_tested_at": None,
        "return_checked_at": now,
        "verification_status": "not_tested",
        "verification_checked_at": None,
        "verification_error": None,
        "last_error": None if not is_abnormal else str(remote_account.get("error_message") or remote_account.get("status") or "remote abnormal"),
    }

    try:
        account = await _find_local_account(db, remote_account_id=remote_account_id, account_json=account_json)
        if account is None:
            account = await create_account(db, account_json=account_json, metadata=metadata_updates, actor=actor)
            account_id = account["id"]
            before = {}
            locked_account = await _acquire_local_return_lock(db, account_id=account_id, remote_account_id=remote_account_id, actor=actor)
            if locked_account is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account return is already running")
        else:
            account_id = str(account["_id"])
            before = {
                "pool_status": account.get("metadata", {}).get("pool_status"),
                "sub2api_account_id": account.get("metadata", {}).get("sub2api_account_id"),
                "sha256": account.get("metadata", {}).get("sha256"),
            }
            if account.get("metadata", {}).get("sub2api_delete_status") == "succeeded":
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Remote account was already deleted")
            locked_account = await _acquire_local_return_lock(db, account_id=account_id, remote_account_id=remote_account_id, actor=actor)
            if locked_account is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account return is already running")
            update_doc: dict[str, Any] = {
                "$set": _flatten_metadata(metadata_updates)
                | {
                    "metadata.updated_at": now,
                    "metadata.updated_by_user_id": actor.get("_id"),
                    "metadata.updated_by_name": actor_name(actor),
                }
            }
            if target_status in {"available", "library"}:
                update_doc["$unset"] = {"metadata.pool_id": ""}
            account = serialize_doc(
                await db.accounts.find_one_and_update(
                    {"_id": account["_id"]},
                    update_doc,
                    return_document=ReturnDocument.AFTER,
                )
            )

        action = await write_pool_action(
            db,
            action_type="manual_delete_sub2api_account",
            actor=actor,
            account_id=account_id,
            pool_id=str(primary_group_id) if primary_group_id is not None else None,
            status_value="running",
            reason=reason,
            before=before,
            remote_snapshot=remote_account,
        )

        delete_result = await client.delete_account(remote_account_id)
        await _mark_local_delete_result(
            db,
            account_id=account_id,
            status_value="succeeded",
            error=None,
            delete_result=delete_result,
            release_lock=True,
        )
        cache_refresh_error = None
        try:
            await refresh_site_cache(db, site_id)
        except Exception as exc:  # noqa: BLE001 - deletion already succeeded; keep the account return successful.
            cache_refresh_error = str(exc)
            logger.warning(
                "manual_delete_sub2api_refresh_failed remote_id=%s account_id=%s error=%s",
                remote_account_id,
                account_id,
                cache_refresh_error,
            )
            await _mark_cache_refresh_error(db, account_id=account_id, error=cache_refresh_error)
        await _finish_action(
            db,
            action_id=action["id"],
            status_value="succeeded",
            after={
                "pool_status": target_status,
                "sub2api_account_id": remote_account_id,
                "target_status": target_status,
                "return_is_abnormal": is_abnormal,
                "delete_result": delete_result,
                "cache_refresh_error": cache_refresh_error,
            },
        )
        logger.info(
            "manual_delete_sub2api_account_succeeded remote_id=%s account_id=%s target=%s actor=%s",
            remote_account_id,
            account_id,
            target_status,
            actor.get("_id"),
        )
        updated = await _find_account_by_string_id(db, account_id)
        return {
            "account": serialize_doc(updated) if updated else account,
            "remote_account": serialize_doc(remote_account),
            "delete_result": serialize_doc(delete_result),
            "action": action,
        }
    except HTTPException as exc:
        if "account_id" in locals():
            await _mark_local_delete_result(
                db,
                account_id=account_id,
                status_value="failed",
                error=str(exc.detail),
                delete_result=None,
                release_lock=True,
            )
        if "action" in locals():
            await _finish_action(db, action_id=action["id"], status_value="failed", error=str(exc.detail))
        raise
    finally:
        await _release_remote_delete_lock(db, lock_id=lock_id)


async def _find_local_account(
    db: AsyncIOMotorDatabase,
    *,
    remote_account_id: int,
    account_json: dict[str, Any],
) -> dict[str, Any] | None:
    credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else {}
    email = extract_email(account_json)
    chatgpt_account_id = credentials.get("chatgpt_account_id")
    or_clauses: list[dict[str, Any]] = [{"metadata.sub2api_account_id": remote_account_id}]
    if chatgpt_account_id:
        or_clauses.append({"account_json.credentials.chatgpt_account_id": chatgpt_account_id})
        or_clauses.append({"metadata.chatgpt_account_id": chatgpt_account_id})
    if email:
        or_clauses.append({"metadata.email": email})
        or_clauses.append({"account_json.credentials.email": email})
        or_clauses.append({"account_json.extra.email": email})
    if account_json.get("name"):
        or_clauses.append({"account_json.name": account_json["name"]})
    return await db.accounts.find_one({"metadata.deleted_at": {"$exists": False}, "$or": or_clauses})


async def _find_account_by_string_id(db: AsyncIOMotorDatabase, account_id: str) -> dict[str, Any] | None:
    try:
        oid = object_id(account_id)
    except ValueError:
        return None
    return await db.accounts.find_one({"_id": oid})


async def _acquire_remote_delete_lock(
    db: AsyncIOMotorDatabase,
    *,
    lock_id: str,
    remote_account_id: int,
    actor: dict[str, Any],
) -> dict[str, Any] | None:
    now = now_utc()
    return await db.operation_locks.find_one_and_update(
        {
            "_id": lock_id,
            "$or": [
                {"expires_at": {"$lte": now}},
                {"expires_at": {"$exists": False}},
            ],
        },
        {
            "$set": {
                "lock_type": "sub2api_manual_delete",
                "remote_account_id": remote_account_id,
                "locked_at": now,
                "locked_by_user_id": actor.get("_id"),
                "locked_by_name": actor_name(actor),
                "expires_at": now + timedelta(seconds=REMOTE_DELETE_LOCK_TTL_SECONDS),
            }
        },
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


async def _release_remote_delete_lock(db: AsyncIOMotorDatabase, *, lock_id: str) -> None:
    await db.operation_locks.delete_one({"_id": lock_id})


async def _acquire_local_return_lock(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    remote_account_id: int,
    actor: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        oid = object_id(account_id)
    except ValueError:
        return None
    now = now_utc()
    return await db.accounts.find_one_and_update(
        {
            "_id": oid,
            "metadata.deleted_at": {"$exists": False},
            "metadata.sub2api_return_lock": {"$exists": False},
            "metadata.sub2api_delete_status": {"$ne": "pending"},
        },
        {
            "$set": {
                "metadata.sub2api_return_lock": {
                    "remote_account_id": remote_account_id,
                    "locked_at": now,
                    "locked_by_user_id": actor.get("_id"),
                    "locked_by_name": actor_name(actor),
                },
                "metadata.sub2api_delete_status": "pending",
                "metadata.updated_at": now,
                "metadata.updated_by_user_id": actor.get("_id"),
                "metadata.updated_by_name": actor_name(actor),
            }
        },
        return_document=ReturnDocument.AFTER,
    )


def _remote_to_account_json(remote_account: dict[str, Any]) -> dict[str, Any]:
    account_json = {key: value for key, value in remote_account.items() if key not in REMOTE_ACCOUNT_STRIP_FIELDS}
    account_json.setdefault("platform", "openai")
    account_json.setdefault("type", "oauth")
    account_json.setdefault("extra", {})
    if not isinstance(account_json.get("credentials"), dict):
        account_json["credentials"] = {}
    return account_json


def _flatten_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None and key == "pool_id":
            continue
        result[f"metadata.{key}"] = value
    return result


def _remote_group_ids(remote_account: dict[str, Any]) -> list[int]:
    ids: set[int] = set()
    group_ids = remote_account.get("group_ids")
    if isinstance(group_ids, list):
        ids.update(item for item in group_ids if isinstance(item, int))
    groups = remote_account.get("groups")
    if isinstance(groups, list):
        ids.update(group.get("id") for group in groups if isinstance(group, dict) and isinstance(group.get("id"), int))
    account_groups = remote_account.get("account_groups")
    if isinstance(account_groups, list):
        ids.update(item.get("group_id") for item in account_groups if isinstance(item, dict) and isinstance(item.get("group_id"), int))
    return sorted(ids)


def _remote_group_names(remote_account: dict[str, Any]) -> list[str]:
    names: list[str] = []
    groups = remote_account.get("groups")
    if isinstance(groups, list):
        names.extend(str(group.get("name")) for group in groups if isinstance(group, dict) and group.get("name"))
    return names


def _is_remote_abnormal(remote_account: dict[str, Any]) -> bool:
    status_value = str(remote_account.get("status") or "").lower()
    if status_value in {"error", "failed", "banned", "disabled", "invalid"}:
        return True
    if remote_account.get("schedulable") is False and status_value not in {"active", "warning"}:
        return True
    error = str(remote_account.get("error_message") or "").lower()
    if not error:
        return False
    if "429" in error or "529" in error or "rate limit" in error or "限流" in error:
        return False
    return True


async def _mark_local_delete_result(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    status_value: str,
    error: str | None,
    delete_result: dict[str, Any] | None,
    release_lock: bool,
) -> None:
    try:
        oid = object_id(account_id)
    except ValueError:
        return
    updates: dict[str, Any] = {
        "metadata.sub2api_delete_status": status_value,
        "metadata.sub2api_delete_finished_at": now_utc(),
    }
    if error:
        updates["metadata.sub2api_delete_error"] = error
        updates["metadata.last_error"] = error
        updates["metadata.pool_status"] = "active"
    else:
        updates["metadata.sub2api_delete_error"] = None
    if delete_result is not None:
        updates["metadata.sub2api_delete_result"] = delete_result
    update_doc: dict[str, Any] = {"$set": updates}
    if release_lock:
        update_doc["$unset"] = {"metadata.sub2api_return_lock": ""}
    await db.accounts.update_one({"_id": oid}, update_doc)


async def _mark_cache_refresh_error(db: AsyncIOMotorDatabase, *, account_id: str, error: str) -> None:
    try:
        oid = object_id(account_id)
    except ValueError:
        return
    await db.accounts.update_one(
        {"_id": oid},
        {"$set": {"metadata.sub2api_cache_refresh_after_delete_error": error, "metadata.updated_at": now_utc()}},
    )


async def _finish_action(
    db: AsyncIOMotorDatabase,
    *,
    action_id: str,
    status_value: str,
    after: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    try:
        oid = object_id(action_id)
    except ValueError:
        return
    updates: dict[str, Any] = {"status": status_value, "finished_at": now_utc()}
    if after is not None:
        updates["after"] = after
    if error is not None:
        updates["error"] = error
    await db.pool_actions.update_one({"_id": oid}, {"$set": updates})
