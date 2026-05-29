from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.services.pool_lifecycle import actor_name, write_pool_action
from app.services.sub2api import Sub2ApiClient
from app.services.sub2api_cache import get_site, refresh_site_cache
from app.services.sub2api_push import build_sub2api_account_payload
from app.utils import extract_email, now_utc, object_id, serialize_doc


logger = logging.getLogger("app.sub2api_verify")


async def test_remote_sub2api_account(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account_id: int,
    model_id: str,
    prompt: str,
    reason: str | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    site = await get_site(db, site_id, include_token=True)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")

    remote_snapshot = await _load_cached_remote(db, site_id=site_id, remote_account_id=remote_account_id)
    action = await write_pool_action(
        db,
        action_type="test_remote_sub2api_account",
        actor=actor,
        pool_id=_first_group_id_as_str(remote_snapshot),
        status_value="running",
        reason=reason,
        remote_snapshot=remote_snapshot or {},
        before={"sub2api_account_id": remote_account_id, "model_id": model_id},
    )
    client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
    try:
        verification = await client.test_account(remote_account_id, model_id=model_id, prompt=prompt)
        status_value = "succeeded" if verification.get("success") is True else "failed"
        await _write_remote_test_cache(
            db,
            site_id=site_id,
            remote_account_id=remote_account_id,
            verification=verification,
            actor=actor,
        )
        await _write_local_remote_test_if_bound(
            db,
            site_id=site_id,
            remote_account_id=remote_account_id,
            verification=verification,
            actor=actor,
        )
        await _finish_action(
            db,
            action_id=action["id"],
            status_value=status_value,
            after={"verification": verification},
            error=None if status_value == "succeeded" else str(verification.get("error") or "remote account test failed"),
        )
        logger.info(
            "test_remote_sub2api_account_finished remote_id=%s status=%s actor=%s",
            remote_account_id,
            status_value,
            actor.get("_id"),
        )
        return {
            "remote_account": serialize_doc(remote_snapshot or {}),
            "verification": serialize_doc(verification),
            "action": action,
        }
    except HTTPException as exc:
        verification = {
            "success": False,
            "model": model_id,
            "prompt": prompt,
            "response_preview": "",
            "latency_ms": None,
            "error": str(exc.detail),
        }
        await _write_remote_test_cache(
            db,
            site_id=site_id,
            remote_account_id=remote_account_id,
            verification=verification,
            actor=actor,
        )
        await _write_local_remote_test_if_bound(
            db,
            site_id=site_id,
            remote_account_id=remote_account_id,
            verification=verification,
            actor=actor,
        )
        await _finish_action(db, action_id=action["id"], status_value="failed", error=str(exc.detail))
        raise


async def verify_account_via_sub2api_group(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    site_id: str,
    verification_group_id: int,
    model_id: str,
    prompt: str,
    cleanup_remote: bool,
    concurrency: int,
    load_factor: int,
    priority: int,
    reason: str | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    site = await get_site(db, site_id, include_token=True)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    group_doc = await db.sub2api_groups_cache.find_one({"site_id": site_id, "group_id": verification_group_id})
    if group_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Verification group not found")
    group = group_doc.get("group", {}) if isinstance(group_doc.get("group"), dict) else {}
    group_name = group.get("name")

    account_oid = _account_oid(account_id)
    action = await write_pool_action(
        db,
        action_type="verify_account_started",
        actor=actor,
        account_id=account_id,
        pool_id=str(verification_group_id),
        status_value="running",
        reason=reason,
    )
    locked = await _acquire_verification_lock(db, account_oid=account_oid, action_id=action["id"], actor=actor)
    if locked is None:
        await _finish_action(db, action_id=action["id"], status_value="failed", error="Account verification is locked or account not found")
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account verification is locked or account not found")

    account_json = locked.get("account_json") if isinstance(locked.get("account_json"), dict) else {}
    credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else None
    if not credentials:
        await _mark_verification_failed(db, account_oid=account_oid, error="Account JSON is missing credentials", unset_lock=True)
        await _finish_action(db, action_id=action["id"], status_value="failed", error="Account JSON is missing credentials")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account JSON is missing credentials")

    client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
    remote_account: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    cleanup: dict[str, Any] = {"status": "not_needed"}
    try:
        payload = build_sub2api_account_payload(
            account_json,
            metadata=locked.get("metadata", {}) if isinstance(locked.get("metadata"), dict) else {},
            group_id=verification_group_id,
            concurrency=concurrency,
            load_factor=load_factor,
            priority=priority,
        )
        remote_account = await client.create_account(payload)
        remote_id = remote_account.get("id")
        if remote_id is None:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="sub2api did not return remote account id")
        await write_pool_action(
            db,
            action_type="verify_account_pushed_to_verification_group",
            actor=actor,
            account_id=account_id,
            pool_id=str(verification_group_id),
            remote_snapshot=remote_account,
            after={"sub2api_account_id": remote_id, "verification_group_id": verification_group_id},
        )

        try:
            verification = await client.test_account(remote_id, model_id=model_id, prompt=prompt)
        except HTTPException as exc:
            verification = {
                "success": False,
                "model": model_id,
                "prompt": prompt,
                "response_preview": "",
                "latency_ms": None,
                "error": str(exc.detail),
            }
        passed = verification.get("success") is True

        if cleanup_remote:
            cleanup = await _cleanup_verification_remote(client, remote_id)
        await _mark_verification_completed(
            db,
            account_oid=account_oid,
            site_id=site_id,
            verification_group_id=verification_group_id,
            verification_group_name=group_name,
            remote_account=remote_account,
            verification=verification,
            cleanup=cleanup,
            actor=actor,
        )
        await _finish_action(
            db,
            action_id=action["id"],
            status_value="succeeded" if passed else "failed",
            after={
                "verification_status": "passed" if passed else "failed",
                "verification_group_id": verification_group_id,
                "remote_account_id": remote_id,
                "cleanup": cleanup,
            },
            error=None if passed else str(verification.get("error") or "verification failed"),
        )
        try:
            await refresh_site_cache(db, site_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("verify_account_refresh_failed account_id=%s error=%s", account_id, exc)
        updated = await db.accounts.find_one({"_id": account_oid})
        return {
            "account": serialize_doc(updated),
            "remote_account": serialize_doc(remote_account),
            "verification": serialize_doc(verification),
            "cleanup": serialize_doc(cleanup),
            "action": action,
        }
    except HTTPException as exc:
        await _mark_verification_failed(
            db,
            account_oid=account_oid,
            error=str(exc.detail),
            unset_lock=True,
            remote_account=remote_account,
        )
        await _finish_action(db, action_id=action["id"], status_value="failed", error=str(exc.detail))
        raise
    except Exception as exc:
        logger.exception("verify_account_uncertain account_id=%s group_id=%s", account_id, verification_group_id)
        await _mark_verification_failed(
            db,
            account_oid=account_oid,
            error=str(exc),
            unset_lock=True,
            remote_account=remote_account,
        )
        await _finish_action(db, action_id=action["id"], status_value="failed", error=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"verification state uncertain: {str(exc)}") from exc


async def _cleanup_verification_remote(client: Sub2ApiClient, remote_id: Any) -> dict[str, Any]:
    try:
        result = await client.delete_account(remote_id)
        return {"status": "succeeded", "delete_result": result}
    except HTTPException as exc:
        return {"status": "failed", "error": str(exc.detail)}


async def _acquire_verification_lock(
    db: AsyncIOMotorDatabase,
    *,
    account_oid: Any,
    action_id: str,
    actor: dict[str, Any],
) -> dict[str, Any] | None:
    now = now_utc()
    return await db.accounts.find_one_and_update(
        {
            "_id": account_oid,
            "metadata.deleted_at": {"$exists": False},
            "metadata.verification_lock": {"$exists": False},
        },
        {
            "$set": {
                "metadata.verification_status": "testing",
                "metadata.verification_lock": {
                    "action_id": action_id,
                    "locked_at": now,
                    "locked_by_user_id": actor.get("_id"),
                    "locked_by_name": actor_name(actor),
                },
                "metadata.updated_by_user_id": actor.get("_id"),
                "metadata.updated_by_name": actor_name(actor),
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def _mark_verification_completed(
    db: AsyncIOMotorDatabase,
    *,
    account_oid: Any,
    site_id: str,
    verification_group_id: int,
    verification_group_name: str | None,
    remote_account: dict[str, Any],
    verification: dict[str, Any],
    cleanup: dict[str, Any],
    actor: dict[str, Any],
) -> None:
    now = now_utc()
    passed = verification.get("success") is True
    cleanup_status = cleanup.get("status")
    updates: dict[str, Any] = {
        "metadata.verification_status": "passed" if passed else "failed",
        "metadata.verification_checked_at": now,
        "metadata.verification_model": verification.get("model"),
        "metadata.verification_prompt": verification.get("prompt"),
        "metadata.verification_response_preview": verification.get("response_preview"),
        "metadata.verification_latency_ms": verification.get("latency_ms"),
        "metadata.verification_error": None if passed else str(verification.get("error") or "verification failed"),
        "metadata.verification_site_id": site_id,
        "metadata.verification_group_id": verification_group_id,
        "metadata.verification_group_name": verification_group_name,
        "metadata.verification_remote_account_id": remote_account.get("id"),
        "metadata.verification_remote_snapshot": remote_account,
        "metadata.verification_cleanup_status": cleanup_status,
        "metadata.verification_cleanup_error": cleanup.get("error"),
        "metadata.last_error": None if passed else str(verification.get("error") or "verification failed"),
        "metadata.updated_by_user_id": actor.get("_id"),
        "metadata.updated_by_name": actor_name(actor),
    }
    if cleanup_status == "failed":
        updates["metadata.analysis.verification_remote_leftover"] = True
    await db.accounts.update_one({"_id": account_oid}, {"$set": updates, "$unset": {"metadata.verification_lock": ""}})


async def _mark_verification_failed(
    db: AsyncIOMotorDatabase,
    *,
    account_oid: Any,
    error: str,
    unset_lock: bool,
    remote_account: dict[str, Any] | None = None,
) -> None:
    updates: dict[str, Any] = {
        "metadata.verification_status": "failed",
        "metadata.verification_checked_at": now_utc(),
        "metadata.verification_error": error,
        "metadata.last_error": error,
    }
    if remote_account is not None:
        updates["metadata.verification_remote_snapshot"] = remote_account
    update_doc: dict[str, Any] = {"$set": updates}
    if unset_lock:
        update_doc["$unset"] = {"metadata.verification_lock": ""}
    await db.accounts.update_one({"_id": account_oid}, update_doc)


async def _write_remote_test_cache(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account_id: int,
    verification: dict[str, Any],
    actor: dict[str, Any],
) -> None:
    now = now_utc()
    status_value = "passed" if verification.get("success") is True else "failed"
    await db.sub2api_accounts_cache.update_one(
        {"site_id": site_id, "sub2api_account_id": remote_account_id},
        {
            "$set": {
                "remote_test_status": status_value,
                "remote_tested_at": now,
                "remote_test_model": verification.get("model"),
                "remote_test_prompt": verification.get("prompt"),
                "remote_test_response_preview": verification.get("response_preview"),
                "remote_test_latency_ms": verification.get("latency_ms"),
                "remote_test_error": verification.get("error"),
                "remote_tested_by_user_id": actor.get("_id"),
                "remote_tested_by_name": actor_name(actor),
                "account.codex_remote_test_status": status_value,
                "account.codex_remote_tested_at": now,
                "account.codex_remote_test_model": verification.get("model"),
                "account.codex_remote_test_response_preview": verification.get("response_preview"),
                "account.codex_remote_test_latency_ms": verification.get("latency_ms"),
                "account.codex_remote_test_error": verification.get("error"),
            }
        },
    )


async def _write_local_remote_test_if_bound(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account_id: int,
    verification: dict[str, Any],
    actor: dict[str, Any],
) -> None:
    now = now_utc()
    status_value = "passed" if verification.get("success") is True else "failed"
    await db.accounts.update_many(
        {
            "metadata.deleted_at": {"$exists": False},
            "metadata.sub2api_site_id": site_id,
            "metadata.sub2api_account_id": remote_account_id,
        },
        {
            "$set": {
                "metadata.remote_test_status": status_value,
                "metadata.remote_tested_at": now,
                "metadata.remote_test_model": verification.get("model"),
                "metadata.remote_test_response_preview": verification.get("response_preview"),
                "metadata.remote_test_latency_ms": verification.get("latency_ms"),
                "metadata.remote_test_error": verification.get("error"),
                "metadata.updated_by_user_id": actor.get("_id"),
                "metadata.updated_by_name": actor_name(actor),
            }
        },
    )


async def _load_cached_remote(db: AsyncIOMotorDatabase, *, site_id: str, remote_account_id: int) -> dict[str, Any] | None:
    doc = await db.sub2api_accounts_cache.find_one({"site_id": site_id, "sub2api_account_id": remote_account_id})
    if doc and isinstance(doc.get("account"), dict):
        return doc["account"]
    return None


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


def _first_group_id_as_str(remote_account: dict[str, Any] | None) -> str | None:
    if not remote_account:
        return None
    group_ids = remote_account.get("group_ids")
    if isinstance(group_ids, list) and group_ids:
        return str(group_ids[0])
    return None


def _account_oid(account_id: str) -> Any:
    try:
        return object_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found") from exc
