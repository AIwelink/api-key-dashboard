from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Literal

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.services.accounts import apply_metadata_to_account_json, create_account, normalize_metadata
from app.services.pool_lifecycle import actor_name, write_pool_action
from app.services.sub2api import Sub2ApiClient
from app.services.sub2api_cache import get_site, refresh_site_cache
from app.utils import extract_email, now_utc, object_id, serialize_doc


logger = logging.getLogger("app.sub2api_return")

ReturnTargetStatus = Literal["available", "library", "problem"]
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
    delete_mode: str = "manual",
    refresh_cache: bool = True,
    metadata_extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    site = await get_site(db, site_id, include_token=True)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")

    lock_id = f"{site_id}:{remote_account_id}:manual_delete"
    remote_lock = await _acquire_remote_delete_lock(db, lock_id=lock_id, remote_account_id=remote_account_id, actor=actor)
    if remote_lock is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Remote account delete is already running")

    cached = await db.sub2api_accounts_cache.find_one({"site_id": site_id, "sub2api_account_id": remote_account_id})
    remote_account = cached.get("account", {}) if cached and isinstance(cached.get("account"), dict) else None
    client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
    if remote_account is None:
        remote_account = await client.get_account(remote_account_id)
    if not isinstance(remote_account, dict) or not remote_account:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Remote sub2api account not found")

    group_ids = _remote_group_ids(remote_account)
    primary_group_id = group_ids[0] if group_ids else None
    group_name = _remote_group_names(remote_account)[0] if _remote_group_names(remote_account) else None
    is_abnormal = _is_remote_abnormal(remote_account)
    abnormal_reason = remote_abnormal_reason(remote_account)
    now = now_utc()
    account_json = _remote_to_account_json(remote_account)
    metadata_updates = {
        "source": "sub2api_auto_abnormal_return" if delete_mode == "auto_abnormal" else "sub2api_manual_return",
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
        "sub2api_delete_mode": delete_mode,
        "sub2api_delete_target_status": target_status,
        "sub2api_delete_reason": reason,
        "sub2api_delete_status": "pending",
        "sub2api_return_snapshot": remote_account,
        "remote_status_at_return": remote_account.get("status"),
        "remote_schedulable_at_return": remote_account.get("schedulable"),
        "remote_error_at_return": remote_account.get("error_message"),
        "remote_last_used_at_return": remote_account.get("last_used_at"),
        "return_is_abnormal": is_abnormal,
        "abnormal_detected_at": now if is_abnormal else None,
        "abnormal_status": remote_account.get("status") if is_abnormal else None,
        "abnormal_schedulable": remote_account.get("schedulable") if is_abnormal else None,
        "abnormal_error_message": remote_account.get("error_message") if is_abnormal else None,
        "abnormal_reason": abnormal_reason,
        "abnormal_usage_snapshot": remote_usage_snapshot(remote_account),
        "abnormal_remote_snapshot": remote_account if is_abnormal else None,
        "return_health_status": "abnormal" if is_abnormal else "normal",
        "return_test_status": "not_tested",
        "return_tested_at": None,
        "return_checked_at": now,
        "verification_status": "not_tested",
        "verification_checked_at": None,
        "verification_error": None,
        "last_error": None if not is_abnormal else str(remote_account.get("error_message") or remote_account.get("status") or "remote abnormal"),
    }
    if metadata_extra:
        metadata_updates.update(metadata_extra)

    try:
        account = await _find_local_account(db, site_id=site_id, remote_account_id=remote_account_id, account_json=account_json)
        if account is None:
            account = await create_account(db, account_json=account_json, metadata=metadata_updates, actor=actor)
            account_id = account["id"]
            before = {}
            locked_account = await _acquire_local_return_lock(db, account_id=account_id, remote_account_id=remote_account_id, actor=actor)
            if locked_account is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account return is already running")
        else:
            account_id = str(account["_id"])
            account_metadata = account.get("metadata", {})
            before = {
                "pool_status": account_metadata.get("pool_status"),
                "sub2api_account_id": account_metadata.get("sub2api_account_id"),
                "sha256": account_metadata.get("sha256"),
            }
            if _remote_account_was_already_deleted(account_metadata, site_id=site_id, remote_account_id=remote_account_id):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Remote account was already deleted")
            locked_account = await _acquire_local_return_lock(db, account_id=account_id, remote_account_id=remote_account_id, actor=actor)
            if locked_account is None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account return is already running")
            next_account_json = apply_metadata_to_account_json(account_json, metadata_updates)
            next_metadata = normalize_metadata(
                next_account_json,
                metadata_updates,
                actor=actor,
                existing=account_metadata,
            )
            previous_deleted_remote = _previous_deleted_remote_history_item(
                account_metadata,
                site_id=site_id,
                current_remote_account_id=remote_account_id,
            )
            if previous_deleted_remote:
                _append_deleted_remote_history(next_metadata, previous_deleted_remote)
            if target_status in {"available", "library", "problem"}:
                next_metadata.pop("pool_id", None)
            account = serialize_doc(
                await db.accounts.find_one_and_update(
                    {"_id": account["_id"]},
                    {"$set": {"account_json": next_account_json, "metadata": next_metadata}},
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
            site_id=site_id,
            remote_account_id=remote_account_id,
            status_value="succeeded",
            error=None,
            delete_result=delete_result,
            release_lock=True,
        )
        cache_refresh_error = None
        if refresh_cache:
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
                site_id=site_id,
                remote_account_id=remote_account_id,
                status_value="failed",
                error=str(exc.detail),
                delete_result=None,
                release_lock=True,
            )
        if "action" in locals():
            await _finish_action(db, action_id=action["id"], status_value="failed", error=str(exc.detail))
        raise
    except Exception as exc:
        if "account_id" in locals():
            await _mark_local_delete_result(
                db,
                account_id=account_id,
                site_id=site_id,
                remote_account_id=remote_account_id,
                status_value="failed",
                error=str(exc),
                delete_result=None,
                release_lock=True,
            )
        if "action" in locals():
            await _finish_action(db, action_id=action["id"], status_value="failed", error=str(exc))
        raise
    finally:
        await _release_remote_delete_lock(db, lock_id=lock_id)


async def _find_local_account(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account_id: int,
    account_json: dict[str, Any],
) -> dict[str, Any] | None:
    credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else {}
    email = extract_email(account_json)
    chatgpt_account_id = credentials.get("chatgpt_account_id")
    exact = await db.accounts.find_one(
        {
            "metadata.deleted_at": {"$exists": False},
            "metadata.sub2api_site_id": site_id,
            "metadata.sub2api_account_id": remote_account_id,
        }
    )
    if exact:
        return exact

    or_clauses: list[dict[str, Any]] = []
    if chatgpt_account_id:
        or_clauses.append({"account_json.credentials.chatgpt_account_id": chatgpt_account_id})
        or_clauses.append({"metadata.chatgpt_account_id": chatgpt_account_id})
    if email:
        or_clauses.append({"metadata.email": email})
        or_clauses.append({"account_json.credentials.email": email})
        or_clauses.append({"account_json.extra.email": email})
    if account_json.get("name"):
        or_clauses.append({"account_json.name": account_json["name"]})
    if not or_clauses:
        return None
    return await db.accounts.find_one(
        {
            "metadata.deleted_at": {"$exists": False},
            "$or": [{"metadata.sub2api_site_id": site_id}, {"metadata.sub2api_site_id": {"$exists": False}}, {"metadata.sub2api_site_id": None}],
            "$and": [{"$or": or_clauses}],
        }
    )


async def _find_account_by_string_id(db: AsyncIOMotorDatabase, account_id: str) -> dict[str, Any] | None:
    try:
        oid = object_id(account_id)
    except ValueError:
        return None
    return await db.accounts.find_one({"_id": oid})


def _remote_account_was_already_deleted(
    metadata: dict[str, Any],
    *,
    site_id: str,
    remote_account_id: int,
) -> bool:
    if metadata.get("sub2api_delete_status") != "succeeded":
        return False
    deleted_ids = metadata.get("sub2api_deleted_remote_ids")
    if isinstance(deleted_ids, list):
        for item in deleted_ids:
            if isinstance(item, dict):
                if item.get("site_id") == site_id and _same_remote_id(item.get("remote_account_id"), remote_account_id):
                    return True
            elif _same_remote_id(item, remote_account_id):
                return True
    return metadata.get("sub2api_site_id") == site_id and _same_remote_id(metadata.get("sub2api_account_id"), remote_account_id)


def _previous_deleted_remote_history_item(
    metadata: dict[str, Any],
    *,
    site_id: str,
    current_remote_account_id: int,
) -> dict[str, Any] | None:
    if metadata.get("sub2api_delete_status") != "succeeded":
        return None
    previous_remote_id = metadata.get("sub2api_account_id")
    if previous_remote_id is None or _same_remote_id(previous_remote_id, current_remote_account_id):
        return None
    return {
        "site_id": metadata.get("sub2api_site_id") or site_id,
        "remote_account_id": previous_remote_id,
        "deleted_at": metadata.get("sub2api_delete_finished_at"),
    }


def _append_deleted_remote_history(metadata: dict[str, Any], item: dict[str, Any]) -> None:
    current = metadata.get("sub2api_deleted_remote_ids")
    history = list(current) if isinstance(current, list) else []
    item_site_id = item.get("site_id")
    item_remote_id = item.get("remote_account_id")
    for existing in history:
        if not isinstance(existing, dict):
            continue
        if existing.get("site_id") == item_site_id and _same_remote_id(existing.get("remote_account_id"), item_remote_id):
            metadata["sub2api_deleted_remote_ids"] = history
            return
    history.append(item)
    metadata["sub2api_deleted_remote_ids"] = history


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
    stale_before = now - timedelta(seconds=REMOTE_DELETE_LOCK_TTL_SECONDS)
    return await db.accounts.find_one_and_update(
        {
            "_id": oid,
            "metadata.deleted_at": {"$exists": False},
            "$or": [
                {
                    "metadata.sub2api_return_lock": {"$exists": False},
                    "metadata.sub2api_delete_status": {"$ne": "pending"},
                },
                {"metadata.sub2api_return_lock.locked_at": {"$lte": stale_before}},
            ],
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
    return serialize_doc(account_json)


def _same_remote_id(left: Any, right: Any) -> bool:
    try:
        return int(left) == int(right)
    except (TypeError, ValueError):
        return str(left) == str(right)


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


def remote_abnormal_reason(remote_account: dict[str, Any]) -> str | None:
    status_value = str(remote_account.get("status") or "").lower()
    if status_value in {"error", "failed", "banned", "disabled", "invalid"}:
        return f"status={status_value}"
    if remote_account.get("schedulable") is False and status_value not in {"active", "warning"}:
        return f"schedulable=false,status={status_value or 'unknown'}"
    error = str(remote_account.get("error_message") or "").lower()
    if not error:
        return None
    if "429" in error or "529" in error or "rate limit" in error or "闄愭祦" in error:
        return None
    return f"error={remote_account.get('error_message')}"


def remote_usage_snapshot(remote_account: dict[str, Any]) -> dict[str, Any]:
    extra = remote_account.get("extra") if isinstance(remote_account.get("extra"), dict) else {}
    keys = (
        "codex_5h_used_percent",
        "codex_7d_used_percent",
        "codex_5h_request_count",
        "codex_7d_request_count",
        "codex_5h_token_count",
        "codex_7d_token_count",
        "codex_5h_actual_cost",
        "codex_7d_actual_cost",
        "codex_5h_total_cost",
        "codex_7d_total_cost",
        "codex_usage_synced_at",
        "last_used_at",
        "current_concurrency",
        "concurrency",
        "status",
        "schedulable",
        "error_message",
    )
    snapshot: dict[str, Any] = {}
    for key in keys:
        value = remote_account.get(key)
        if value is None:
            value = extra.get(key)
        if value is not None:
            snapshot[key] = value
    return snapshot


async def _mark_local_delete_result(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    site_id: str,
    remote_account_id: int,
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
    if status_value == "succeeded":
        update_doc["$addToSet"] = {
            "metadata.sub2api_deleted_remote_ids": {
                "site_id": site_id,
                "remote_account_id": remote_account_id,
                "deleted_at": updates["metadata.sub2api_delete_finished_at"],
            }
        }
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
        {"$set": {"metadata.sub2api_cache_refresh_after_delete_error": error}},
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
