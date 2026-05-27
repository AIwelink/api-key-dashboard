from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.sub2api_return import manual_delete_sub2api_account, remote_abnormal_reason, remote_usage_snapshot
from app.utils import extract_email, now_utc, serialize_doc


logger = logging.getLogger("app.sub2api_abnormal")
AUTO_REMOVE_DELETE_CONCURRENCY = 5
AUTO_REMOVE_LOCK_RETRY_ATTEMPTS = 4
AUTO_REMOVE_LOCK_RETRY_DELAY_SECONDS = 2.0

SYSTEM_ACTOR = {
    "_id": "system:auto-abnormal-removal",
    "name": "System Auto Removal",
    "email": "system@local",
    "role": "owner",
}


async def auto_remove_abnormal_accounts(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    accounts: list[dict[str, Any]],
) -> dict[str, Any]:
    candidates = [account for account in accounts if account.get("id") is not None and remote_abnormal_reason(account)]
    if not candidates:
        return {"enabled": True, "removed": 0, "failed": 0, "items": []}

    grouped_candidates = _group_candidates_by_identity(candidates)
    semaphore = asyncio.Semaphore(AUTO_REMOVE_DELETE_CONCURRENCY)

    async def process_group(group: list[dict[str, Any]]) -> list[dict[str, Any]]:
        async with semaphore:
            group_items: list[dict[str, Any]] = []
            for account in group:
                group_items.append(await _delete_abnormal_account(db, site_id=site_id, account=account))
            return group_items

    group_results = await asyncio.gather(*(process_group(group) for group in grouped_candidates))
    items = [item for group_items in group_results for item in group_items]
    removed_ids = [item["remote_account_id"] for item in items if item.get("status") == "removed"]

    if removed_ids:
        await db.sub2api_accounts_cache.delete_many({"site_id": site_id, "sub2api_account_id": {"$in": removed_ids}})

    summary = {
        "enabled": True,
        "removed": len(removed_ids),
        "failed": sum(1 for item in items if item.get("status") == "failed"),
        "delete_concurrency": AUTO_REMOVE_DELETE_CONCURRENCY,
        "items": items,
        "updated_at": now_utc(),
    }
    await db.sub2api_cache_meta.update_one(
        {"_id": site_id},
        {
            "$set": {
                "auto_remove_abnormal": serialize_doc({key: value for key, value in summary.items() if key != "items"}),
                "auto_remove_abnormal_last_items": serialize_doc(items[-20:]),
            }
        },
        upsert=True,
    )
    return serialize_doc(summary)


async def _delete_abnormal_account(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    account: dict[str, Any],
) -> dict[str, Any]:
    remote_id = account.get("id")
    reason = remote_abnormal_reason(account) or "remote abnormal"
    usage_snapshot = remote_usage_snapshot(account)
    last_lock_error: HTTPException | None = None
    for attempt in range(AUTO_REMOVE_LOCK_RETRY_ATTEMPTS):
        try:
            result = await manual_delete_sub2api_account(
                db,
                site_id=site_id,
                remote_account_id=int(remote_id),
                target_status="problem",
                reason=f"auto remove abnormal account: {reason}",
                actor=SYSTEM_ACTOR,
                delete_mode="auto_abnormal",
                refresh_cache=False,
                metadata_extra={
                    "abnormal_auto_removed": True,
                    "abnormal_auto_removed_at": now_utc(),
                    "abnormal_auto_remove_reason": reason,
                    "abnormal_usage_snapshot": usage_snapshot,
                },
            )
            return {
                "remote_account_id": remote_id,
                "status": "removed",
                "reason": reason,
                "local_account_id": result.get("account", {}).get("id"),
            }
        except HTTPException as exc:
            if str(exc.detail) == "Account return is already running" and attempt < AUTO_REMOVE_LOCK_RETRY_ATTEMPTS - 1:
                last_lock_error = exc
                await asyncio.sleep(AUTO_REMOVE_LOCK_RETRY_DELAY_SECONDS * (attempt + 1))
                continue
            logger.warning("auto_remove_abnormal_failed site_id=%s remote_id=%s error=%s", site_id, remote_id, exc.detail)
            return {"remote_account_id": remote_id, "status": "failed", "reason": reason, "error": str(exc.detail)}
        except Exception as exc:  # noqa: BLE001
            logger.exception("auto_remove_abnormal_uncertain site_id=%s remote_id=%s", site_id, remote_id)
            return {"remote_account_id": remote_id, "status": "failed", "reason": reason, "error": str(exc)}
    if last_lock_error is not None:
        logger.warning("auto_remove_abnormal_failed site_id=%s remote_id=%s error=%s", site_id, remote_id, last_lock_error.detail)
        return {"remote_account_id": remote_id, "status": "failed", "reason": reason, "error": str(last_lock_error.detail)}
    error = "auto remove retry loop ended unexpectedly"
    logger.error("auto_remove_abnormal_failed site_id=%s remote_id=%s error=%s", site_id, remote_id, error)
    return {"remote_account_id": remote_id, "status": "failed", "reason": reason, "error": error}


def _group_candidates_by_identity(candidates: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for account in candidates:
        key = _candidate_identity_key(account)
        groups.setdefault(key, []).append(account)
    return list(groups.values())


def _candidate_identity_key(account: dict[str, Any]) -> str:
    credentials = account.get("credentials") if isinstance(account.get("credentials"), dict) else {}
    chatgpt_account_id = credentials.get("chatgpt_account_id")
    if chatgpt_account_id:
        return f"chatgpt_account_id:{chatgpt_account_id}"
    email = extract_email(account) or account.get("email") or account.get("account_claims_email")
    if isinstance(email, str) and email.strip():
        return f"email:{email.strip().lower()}"
    name = account.get("name")
    if isinstance(name, str) and name.strip():
        return f"name:{name.strip().lower()}"
    return f"remote_id:{account.get('id')}"
