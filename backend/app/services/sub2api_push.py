from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReturnDocument

from app.services.account_records import write_account_operation, write_account_problem
from app.services.pool_lifecycle import actor_name, operation_actor_updates, write_pool_action
from app.services.sub2api import Sub2ApiClient, account_in_group
from app.services.sub2api_cache import get_site, upsert_cached_account_snapshot
from app.services.sub2api_return import remote_cumulative_usage_snapshot, remote_usage_snapshot
from app.utils import credentials_email, now_utc, object_id, serialize_doc


logger = logging.getLogger("app.sub2api_push")

ALLOWED_PUSH_STATUSES = {"available", "reserve", "problem"}
BLOCKED_PUSH_STATUSES = {"library", "active", "discarded"}
PUSH_PROBLEM_GROUP_NAME = "推送问题账户池"
PUSH_PROBLEM_GROUP_FALLBACK_ID = 4
VERIFICATION_RETRY_ATTEMPTS = 3
PUSH_ERROR_TASK_TYPE = "push_use_pool_error"
PROBLEM_CLASS_PUSH_TOKEN_EXPIRED = "push_token_expired"
BEIJING_TZ = timezone(timedelta(hours=8))
REMOTE_STRIP_FIELDS = {
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
}


async def push_account_to_sub2api(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    site_id: str,
    group_id: int | None,
    run_verification: bool,
    model_id: str,
    prompt: str,
    concurrency: int,
    load_factor: int,
    priority: int,
    reason: str | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    site = await get_site(db, site_id, include_token=True)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")

    account_oid = _account_oid(account_id)
    account = await db.accounts.find_one({"_id": account_oid, "metadata.deleted_at": {"$exists": False}})
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")

    metadata = dict(account.get("metadata", {}))
    target_group_id = resolve_target_group_id(metadata, requested_group_id=group_id)
    _ensure_push_can_start(metadata, target_group_id=target_group_id)
    group_doc = await db.sub2api_groups_cache.find_one({"site_id": site_id, "group_id": target_group_id})
    if group_doc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Target sub2api group not found")
    group = group_doc.get("group", {}) if isinstance(group_doc.get("group"), dict) else {}
    group_name = group.get("name")

    current_status = metadata.get("pool_status", "library")
    if current_status in BLOCKED_PUSH_STATUSES or current_status not in ALLOWED_PUSH_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Only available, reserve, or problem accounts can be pushed. Current status: {current_status}",
        )

    account_json = account.get("account_json") if isinstance(account.get("account_json"), dict) else {}
    credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else None
    if not credentials:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account JSON is missing credentials")

    push_action = await write_pool_action(
        db,
        action_type="push_to_sub2api_group",
        actor=actor,
        account_id=account_id,
        pool_id=str(target_group_id),
        status_value="running",
        reason=reason,
        before={
            "pool_status": current_status,
            "sub2api_account_id": metadata.get("sub2api_account_id"),
            "sub2api_group_ids": metadata.get("sub2api_group_ids"),
            "sha256": metadata.get("sha256"),
            "email": credentials_email(account_json),
        },
    )
    action_id = push_action["id"]

    push_name = _push_account_name(account_json, metadata)
    locked = await _acquire_push_lock(db, account_oid, action_id, target_group_id, actor, push_name=push_name)
    if locked is None:
        await _finish_pool_action(db, action_id=action_id, status_value="failed", error="Account is locked or status changed")
        await write_pool_action(
            db,
            action_type="push_to_sub2api_group_failed",
            actor=actor,
            account_id=account_id,
            pool_id=str(target_group_id),
            status_value="failed",
            reason=reason,
            error="Account is locked or status changed",
        )
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account is locked, already pushing, or status changed")

    client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
    remote_account: dict[str, Any] | None = None
    verification: dict[str, Any] | None = None
    try:
        bound_remote_id = _bound_remote_id(metadata)
        if bound_remote_id is not None:
            payload = build_sub2api_account_payload(
                {**account_json, "name": push_name},
                metadata=metadata,
                group_id=target_group_id,
                concurrency=concurrency,
                load_factor=load_factor,
                priority=priority,
            )
            remote_account = await _refresh_bound_remote_account(
                client=client,
                remote_id=bound_remote_id,
                payload=payload,
            )
            await write_pool_action(
                db,
                action_type="refresh_bound_sub2api_account",
                actor=actor,
                account_id=account_id,
                pool_id=str(target_group_id),
                reason="refresh existing bound sub2api account",
                after={"sub2api_account_id": remote_account.get("id"), "group_id": target_group_id, "name": remote_account.get("name")},
            )
        else:
            duplicate = await find_remote_duplicate(db, site_id=site_id, group_id=target_group_id, account=locked)
            if duplicate and account_in_group(duplicate, target_group_id):
                payload = build_sub2api_account_payload(
                    {**account_json, "name": push_name},
                    metadata=metadata,
                    group_id=target_group_id,
                    concurrency=concurrency,
                    load_factor=load_factor,
                    priority=priority,
                )
                duplicate_id = duplicate.get("id")
                if duplicate_id is None:
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Remote duplicate is missing account id")
                remote_account = await _refresh_bound_remote_account(
                    client=client,
                    remote_id=duplicate_id,
                    payload=payload,
                )
                await write_pool_action(
                    db,
                    action_type="remote_duplicate_refreshed",
                    actor=actor,
                    account_id=account_id,
                    pool_id=str(target_group_id),
                    reason="matched existing remote account in target group and refreshed",
                    remote_snapshot=duplicate,
                    after={"sub2api_account_id": remote_account.get("id"), "group_id": target_group_id, "name": remote_account.get("name")},
                )
            elif duplicate:
                payload = build_sub2api_account_payload(
                    {**account_json, "name": push_name},
                    metadata=metadata,
                    group_id=target_group_id,
                    concurrency=concurrency,
                    load_factor=load_factor,
                    priority=priority,
                )
                duplicate_id = duplicate.get("id")
                if duplicate_id is None:
                    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Remote duplicate is missing account id")
                remote_account = await _refresh_bound_remote_account(
                    client=client,
                    remote_id=duplicate_id,
                    payload=payload,
                )
                await write_pool_action(
                    db,
                    action_type="remote_duplicate_refreshed",
                    actor=actor,
                    account_id=account_id,
                    pool_id=str(target_group_id),
                    reason="matched existing remote account outside target group and refreshed into target group",
                    remote_snapshot=duplicate,
                    after={"sub2api_account_id": remote_account.get("id"), "group_id": target_group_id, "name": remote_account.get("name")},
                )
            else:
                payload = build_sub2api_account_payload(
                    {**account_json, "name": push_name},
                    metadata=metadata,
                    group_id=target_group_id,
                    concurrency=concurrency,
                    load_factor=load_factor,
                    priority=priority,
                )
                remote_account = await client.create_account(payload)

        remote_id = remote_account.get("id")
        if remote_id is None:
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="sub2api did not return remote account id")

        _ensure_remote_group(remote_account, target_group_id)
        remote_snapshot = await upsert_cached_account_snapshot(db, site_id, remote_account)

        if run_verification:
            try:
                verification = await _test_pushed_account_with_retries(client, remote_id, model_id=model_id, prompt=prompt)
            except HTTPException as exc:
                verification = {
                    "success": False,
                    "model": model_id,
                    "prompt": prompt,
                    "latency_ms": None,
                    "response_preview": "",
                    "error": str(exc.detail),
                    "status": "failed",
                }
        else:
            verification = {
                "success": None,
                "model": model_id,
                "prompt": prompt,
                "latency_ms": None,
                "response_preview": "",
                "status": "skipped",
        }

        succeeded = verification.get("success") is True if run_verification else True
        verification_error = None if succeeded else _verification_error_detail(verification)
        failed_task_updates: dict[str, Any] | None = None
        updated = await _mark_push_completed(
            db,
            account_oid=account_oid,
            site_id=site_id,
            group_id=target_group_id,
            group_name=group_name,
            remote_account=remote_snapshot,
            verification=verification,
            verification_passed=True,
            verification_error=verification_error,
            failed_task_updates=failed_task_updates,
            actor=actor,
        )
        await _finish_pool_action(
            db,
            action_id=action_id,
            status_value="succeeded",
            after={
                "pool_status": updated.get("metadata", {}).get("pool_status"),
                "sub2api_account_id": remote_id,
                "verification_status": updated.get("metadata", {}).get("verification_status"),
                "target_group_id": target_group_id,
                "kept_in_target_group": True,
            },
            error=None,
        )

        await write_pool_action(
            db,
            action_type="verify_sub2api_account" if run_verification else "push_to_sub2api_group",
            actor=actor,
            account_id=account_id,
            pool_id=str(target_group_id),
            status_value="succeeded",
            reason=reason,
            remote_snapshot=remote_snapshot,
            after={
                "pool_status": updated.get("metadata", {}).get("pool_status"),
                "sub2api_account_id": remote_id,
                "sub2api_group_ids": updated.get("metadata", {}).get("sub2api_group_ids"),
                "sub2api_push_status": updated.get("metadata", {}).get("sub2api_push_status"),
                "verification_status": updated.get("metadata", {}).get("verification_status"),
                "kept_in_target_group": True,
            },
            error=verification_error,
        )
        logger.info(
            "push_to_sub2api_finished account_id=%s remote_id=%s group_id=%s verification=%s actor=%s",
            account_id,
            remote_id,
            target_group_id,
            updated.get("metadata", {}).get("verification_status"),
            actor.get("_id"),
        )
        return {
            "account": serialize_doc(updated),
            "remote_account": serialize_doc(remote_snapshot),
            "push_action": push_action,
            "verification": serialize_doc(verification),
        }
    except HTTPException as exc:
        await _mark_push_failed(
            db,
            account_oid=account_oid,
            original_status=current_status,
            error=str(exc.detail),
            unset_lock=True,
            actor=actor,
        )
        await _finish_pool_action(db, action_id=action_id, status_value="failed", error=str(exc.detail))
        await write_pool_action(
            db,
            action_type="push_to_sub2api_group_failed",
            actor=actor,
            account_id=account_id,
            pool_id=str(target_group_id),
            status_value="failed",
            reason=reason,
            remote_snapshot=remote_account or {},
            error=str(exc.detail),
        )
        raise
    except Exception as exc:
        logger.exception("push_to_sub2api_uncertain account_id=%s group_id=%s", account_id, target_group_id)
        await _mark_push_uncertain(db, account_oid=account_oid, original_status=current_status, error=str(exc), actor=actor)
        await _finish_pool_action(db, action_id=action_id, status_value="failed", error=str(exc))
        await write_pool_action(
            db,
            action_type="remote_state_uncertain",
            actor=actor,
            account_id=account_id,
            pool_id=str(target_group_id),
            status_value="failed",
            reason=reason,
            remote_snapshot=remote_account or {},
            error=str(exc),
        )
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"sub2api push state uncertain: {str(exc)}") from exc


def build_sub2api_account_payload(
    account_json: dict[str, Any],
    *,
    metadata: dict[str, Any] | None = None,
    group_id: int,
    concurrency: int,
    load_factor: int,
    priority: int,
) -> dict[str, Any]:
    metadata = metadata or {}
    payload = {key: value for key, value in account_json.items() if key not in REMOTE_STRIP_FIELDS}
    push_name = _push_account_name(account_json, metadata)
    payload["name"] = push_name
    payload["group_id"] = group_id
    payload["group_ids"] = [group_id]
    payload["concurrency"] = concurrency
    payload["load_factor"] = load_factor
    payload["priority"] = priority
    payload["status"] = payload.get("status") or "active"
    payload["schedulable"] = True
    payload["confirm_mixed_channel_risk"] = True
    return payload


def _push_account_name(account_json: dict[str, Any], metadata: dict[str, Any]) -> str:
    existing_name = str(account_json.get("name") or "").strip()
    if _should_preserve_existing_name(metadata) and existing_name:
        return existing_name
    return build_sub2api_account_name(account_json, metadata)


def _should_preserve_existing_name(metadata: dict[str, Any]) -> bool:
    upload_intent = str(metadata.get("upload_intent") or "").strip().lower()
    if upload_intent == "renew":
        return True
    if metadata.get("credentials_refreshed_at"):
        return True
    return _metadata_was_content_updated(metadata)


def _metadata_was_content_updated(metadata: dict[str, Any]) -> bool:
    created_at = _parse_datetime(metadata.get("created_at"))
    updated_at = _parse_datetime(metadata.get("updated_at"))
    if created_at is None or updated_at is None:
        return False
    return updated_at > created_at


def _ensure_remote_group(remote_account: dict[str, Any], group_id: int) -> None:
    group_ids = remote_account.get("group_ids")
    if not isinstance(group_ids, list):
        remote_account["group_ids"] = [group_id]
    elif group_id not in group_ids:
        remote_account["group_ids"] = [*group_ids, group_id]


async def _test_pushed_account_with_retries(
    client: Sub2ApiClient,
    remote_id: Any,
    *,
    model_id: str,
    prompt: str,
) -> dict[str, Any]:
    last_verification: dict[str, Any] | None = None
    for attempt in range(VERIFICATION_RETRY_ATTEMPTS):
        try:
            verification = await client.test_account(remote_id, model_id=model_id, prompt=prompt)
        except HTTPException as exc:
            detail = str(exc.detail)
            if _is_server_error_detail(detail):
                raise
            verification = {
                "success": False,
                "model": model_id,
                "prompt": prompt,
                "latency_ms": None,
                "response_preview": "",
                "error": detail,
            }
        verification["attempt"] = attempt + 1
        last_verification = verification
        if verification.get("success") is True:
            return verification
        if not _is_401_verification_failure(verification):
            return verification
    if last_verification is not None:
        last_verification["error"] = last_verification.get("error") or "remote account test failed with 401 after retries"
        last_verification["retry_exhausted"] = True
        return last_verification
    return {
        "success": False,
        "model": model_id,
        "prompt": prompt,
        "latency_ms": None,
        "response_preview": "",
        "error": "remote account test failed",
        "retry_exhausted": True,
    }


def _is_server_error_detail(detail: str) -> bool:
    normalized = detail.lower()
    return (
        any(str(code) in normalized for code in (500, 502, 503, 504))
        or "server error" in normalized
        or "failed after retries" in normalized
        or "did not return a response" in normalized
        or "non-json response" in normalized
    )


def _is_401_verification_failure(verification: dict[str, Any]) -> bool:
    haystack = " ".join(
        str(value)
        for value in (
            verification.get("error"),
            verification.get("response_preview"),
            verification.get("events"),
        )
        if value is not None
    ).lower()
    return any(
        marker in haystack
        for marker in (
            "401",
            "unauthorized",
            "token_expired",
            "authentication token is expired",
            "provided authentication token is expired",
        )
    )


def _verification_error_detail(verification: dict[str, Any]) -> str:
    parts: list[str] = []
    error = verification.get("error")
    if error:
        parts.append(f"error: {error}")
    preview = verification.get("response_preview")
    if preview:
        parts.append(f"response_preview: {preview}")
    complete_event = verification.get("complete_event")
    if isinstance(complete_event, dict) and complete_event:
        parts.append(f"complete_event: {serialize_doc(complete_event)}")
    events = verification.get("events")
    if events:
        parts.append(f"events: {serialize_doc(events)}")
    if not parts:
        parts.append("sub2api account verification failed")
    return " | ".join(parts)


def _verification_requires_problem_group(verification: dict[str, Any]) -> bool:
    return bool(verification.get("retry_exhausted") and _is_401_verification_failure(verification))


async def _build_push_error_task_updates(
    db: AsyncIOMotorDatabase,
    *,
    client: Sub2ApiClient,
    site_id: str,
    account_id: str,
    account_json: dict[str, Any],
    metadata: dict[str, Any],
    remote_account: dict[str, Any],
    verification: dict[str, Any],
    problem_group_id: int,
    problem_group_name: str | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    now = now_utc()
    account_type = _account_type(account_json, metadata)
    remote_id = remote_account.get("id")
    error = str(verification.get("error") or "推送测试返回 401，凭证过期")
    details = {
        "site_id": site_id,
        "remote_account_id": remote_id,
        "problem_group_id": problem_group_id,
        "problem_group_name": problem_group_name,
        "verification": verification,
        "account_type": account_type,
    }
    await write_account_problem(
        db,
        problem_class=PROBLEM_CLASS_PUSH_TOKEN_EXPIRED,
        problem_name="推送测试凭证过期",
        remark_zh="推送到使用池后测试返回 401/token_expired，账号凭证已过期。",
        account_id=account_id,
        severity="error",
        status_value="open",
        site_id=site_id,
        remote_account_id=remote_id,
        details=details,
        actor=actor,
    )
    await write_account_operation(
        db,
        operation_class="push_error_detected",
        operation_name="推送错误识别",
        remark_zh="推送测试识别到 401 凭证过期错误。",
        actor=actor,
        account_id=account_id,
        status_value="failed",
        details=details,
    )
    cumulative_usage = await remote_cumulative_usage_snapshot(db, site_id=site_id, remote_account=remote_account)
    usage_snapshot = remote_usage_snapshot(remote_account, cumulative_usage=cumulative_usage)

    base_updates: dict[str, Any] = {
        "metadata.problem_task_type": PUSH_ERROR_TASK_TYPE,
        "metadata.problem_class": PROBLEM_CLASS_PUSH_TOKEN_EXPIRED,
        "metadata.problem_name": "推送测试凭证过期",
        "metadata.problem_status": "open",
        "metadata.problem_remark_zh": "推送到使用池后测试返回 401/token_expired，账号凭证已过期。",
        "metadata.problem_detected_at": now,
        "metadata.problem_source": "push_verification",
        "metadata.problem_error": error,
        "metadata.problem_remote_account_id": remote_id,
        "metadata.problem_site_id": site_id,
        "metadata.problem_group_id": problem_group_id,
        "metadata.problem_group_name": problem_group_name,
        "metadata.problem_last_test_status": "failed",
        "metadata.problem_last_test_at": now,
        "metadata.problem_last_test_error": error,
        "metadata.problem_last_test_result": verification,
        "metadata.sub2api_delete_remote_snapshot": remote_account,
        "metadata.sub2api_delete_usage_snapshot": usage_snapshot,
        "metadata.sub2api_delete_remote_last_used_at": remote_account.get("last_used_at"),
        "metadata.sub2api_delete_remote_status": remote_account.get("status"),
        "metadata.sub2api_delete_remote_error_message": remote_account.get("error_message"),
    }
    if account_type == "free":
        delete_result = await client.delete_account(remote_id) if remote_id is not None else {"ok": False, "error": "missing remote id"}
        if remote_id is not None:
            await db.sub2api_accounts_cache.delete_one({"site_id": site_id, "sub2api_account_id": remote_id})
        await write_account_operation(
            db,
            operation_class="free_push_error_auto_archive",
            operation_name="free 推送错误自动归档",
            remark_zh="free 账号推送测试 401，无需人工判断，已归档到错误库并从远端错误池删除。",
            actor=actor,
            account_id=account_id,
            details={**details, "delete_result": delete_result},
        )
        base_updates.update(
            {
                "metadata.problem_task_status": "archived",
                "metadata.problem_status": "closed",
                "metadata.problem_resolution": "free_auto_archived",
                "metadata.problem_resolved_at": now,
                "metadata.problem_resolved_by_user_id": actor.get("_id"),
                "metadata.problem_resolved_by_name": actor_name(actor),
                "metadata.problem_remote_delete_status": "succeeded",
                "metadata.problem_remote_deleted_at": now,
                "metadata.sub2api_manual_deleted": True,
                "metadata.sub2api_delete_status": "succeeded",
                "metadata.sub2api_delete_error": None,
            }
        )
    else:
        base_updates.update(
            {
                "metadata.problem_task_status": "pending",
                "metadata.problem_status": "open",
                "metadata.problem_resolution": None,
                "metadata.problem_remote_delete_status": None,
            }
        )
    return base_updates


async def _resolve_push_problem_group(db: AsyncIOMotorDatabase, site_id: str) -> tuple[int, str | None]:
    doc = await db.sub2api_groups_cache.find_one({"site_id": site_id, "group.name": PUSH_PROBLEM_GROUP_NAME})
    if doc and isinstance(doc.get("group_id"), int):
        group = doc.get("group") if isinstance(doc.get("group"), dict) else {}
        return doc["group_id"], group.get("name")
    doc = await db.sub2api_groups_cache.find_one({"site_id": site_id, "group_id": PUSH_PROBLEM_GROUP_FALLBACK_ID})
    if doc and isinstance(doc.get("group_id"), int):
        group = doc.get("group") if isinstance(doc.get("group"), dict) else {}
        return doc["group_id"], group.get("name") or PUSH_PROBLEM_GROUP_NAME
    return PUSH_PROBLEM_GROUP_FALLBACK_ID, PUSH_PROBLEM_GROUP_NAME


async def _move_remote_to_problem_group(
    db: AsyncIOMotorDatabase,
    *,
    client: Sub2ApiClient,
    site_id: str,
    remote_account: dict[str, Any],
    problem_group_id: int,
) -> dict[str, Any]:
    remote_id = remote_account.get("id")
    if remote_id is None:
        return remote_account
    try:
        updated_remote = await client.update_account(
            remote_id,
            {
                "group_id": problem_group_id,
                "group_ids": [problem_group_id],
                "status": remote_account.get("status") or "active",
                "schedulable": remote_account.get("schedulable", True),
            },
        )
    except HTTPException as exc:
        if not _is_sub2api_update_not_found(exc):
            raise
        logger.warning(
            "sub2api_update_group_not_found_fallback site_id=%s remote_id=%s problem_group_id=%s error=%s",
            site_id,
            remote_id,
            problem_group_id,
            exc.detail,
        )
        updated_remote = await _recreate_remote_in_problem_group(
            client=client,
            remote_account=remote_account,
            problem_group_id=problem_group_id,
        )
    if not isinstance(updated_remote, dict) or updated_remote.get("id") is None:
        updated_remote = {**remote_account, "group_id": problem_group_id, "group_ids": [problem_group_id]}
    _ensure_single_remote_group(updated_remote, problem_group_id)
    return await upsert_cached_account_snapshot(db, site_id, updated_remote)


async def _refresh_bound_remote_account(
    *,
    client: Sub2ApiClient,
    remote_id: int | str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        updated_remote = await client.update_account(remote_id, payload)
    except HTTPException as exc:
        if not _is_sub2api_update_not_found(exc):
            raise
        logger.warning(
            "sub2api_bound_update_not_found_fallback remote_id=%s error=%s",
            remote_id,
            exc.detail,
        )
        try:
            await client.delete_account(remote_id)
        except HTTPException as delete_exc:
            if "404" not in str(delete_exc.detail or "").lower():
                raise
            logger.warning("sub2api_bound_delete_before_recreate_not_found remote_id=%s error=%s", remote_id, delete_exc.detail)
        updated_remote = await client.create_account(payload)
        if isinstance(updated_remote, dict):
            updated_remote["recreated_from_remote_id"] = remote_id
    if not isinstance(updated_remote, dict):
        updated_remote = {"id": remote_id, **payload}
    updated_remote.setdefault("id", remote_id)
    return updated_remote


def _is_sub2api_update_not_found(exc: HTTPException) -> bool:
    detail = str(exc.detail or "").lower()
    return "sub2api update failed with status 404" in detail or "404 page not found" in detail


async def _recreate_remote_in_problem_group(
    *,
    client: Sub2ApiClient,
    remote_account: dict[str, Any],
    problem_group_id: int,
) -> dict[str, Any]:
    remote_id = remote_account.get("id")
    payload = {key: value for key, value in remote_account.items() if key not in REMOTE_STRIP_FIELDS}
    payload["group_id"] = problem_group_id
    payload["group_ids"] = [problem_group_id]
    payload["status"] = payload.get("status") or "active"
    payload["schedulable"] = payload.get("schedulable", True)
    payload["confirm_mixed_channel_risk"] = True
    await client.delete_account(remote_id)
    recreated = await client.create_account(payload)
    if isinstance(recreated, dict):
        recreated["recreated_from_remote_id"] = remote_id
    return recreated


def _ensure_single_remote_group(remote_account: dict[str, Any], group_id: int) -> None:
    remote_account["group_id"] = group_id
    remote_account["group_ids"] = [group_id]
    if isinstance(remote_account.get("account_groups"), list):
        remote_account["account_groups"] = [
            item for item in remote_account["account_groups"] if isinstance(item, dict) and item.get("group_id") == group_id
        ]


def _account_type(account_json: dict[str, Any], metadata: dict[str, Any]) -> str:
    credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else {}
    value = metadata.get("account_type") or credentials.get("plan_type") or ""
    return _normalize_account_type(value)


def build_sub2api_account_name(account_json: dict[str, Any], metadata: dict[str, Any]) -> str:
    date_part = _name_date(metadata)
    source_part = "自产" if metadata.get("self_produced") is True else "购买"
    account_type = _name_account_type(account_json, metadata)
    payment_part = _name_payment_type(metadata.get("payment_type"))
    purchase_source_part = _name_purchase_source(metadata)
    name_parts = [date_part, f"{source_part}{account_type}", payment_part]
    if purchase_source_part:
        name_parts.append(purchase_source_part)
    return "-".join(name_parts)


def _name_date(metadata: dict[str, Any]) -> str:
    value = (
        metadata.get("upgrade_completed_at")
        or metadata.get("purchased_at")
        or metadata.get("purchase_at")
        or metadata.get("created_at")
    )
    parsed = _parse_datetime(value)
    if parsed is None:
        return now_utc().astimezone(BEIJING_TZ).strftime("%m%d")
    return parsed.astimezone(BEIJING_TZ).strftime("%m%d")


def _name_account_type(account_json: dict[str, Any], metadata: dict[str, Any]) -> str:
    credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else {}
    value = metadata.get("account_type") or credentials.get("plan_type") or "unknown"
    normalized = _normalize_account_type(value)
    if normalized == "team":
        return "team子号"
    if normalized in {"plus", "k12", "free", "pro"}:
        return normalized
    return str(value).strip() or "unknown"


def _normalize_account_type(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"team", "team_sub", "team-sub", "team_child", "team_child_account", "team子号", "team 子号"}:
        return "team"
    return normalized


def _name_payment_type(value: Any) -> str:
    mapping = {
        "paypal_multi": "PayPal一卡多号",
        "paypal_single": "PayPal一卡一号",
        "no_card": "无卡",
        "gopay": "gopay",
        "other": "其他",
    }
    normalized = str(value or "").strip()
    return mapping.get(normalized, normalized or "未知绑卡")


def _name_purchase_source(metadata: dict[str, Any]) -> str:
    value = str(metadata.get("purchase_source") or "").strip()
    return value


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=BEIJING_TZ)
    return parsed.astimezone(UTC)


def resolve_target_group_id(metadata: dict[str, Any], *, requested_group_id: int | None) -> int:
    stored_group_id = _int_or_none(metadata.get("sub2api_group_id"))
    if stored_group_id is None:
        stored_group_id = _int_or_none(metadata.get("pool_id"))
    if stored_group_id is not None and requested_group_id is not None and stored_group_id != requested_group_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Selected group #{requested_group_id} does not match account target group #{stored_group_id}",
        )
    target_group_id = stored_group_id if stored_group_id is not None else requested_group_id
    if target_group_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Account has no target sub2api group")
    return target_group_id


def _ensure_push_can_start(metadata: dict[str, Any], *, target_group_id: int) -> None:
    if metadata.get("push_lock"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account is already being pushed")
    if metadata.get("sub2api_push_status") == "pushing":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Account is already being pushed")


def _bound_remote_id(metadata: dict[str, Any]) -> int | str | None:
    remote_id = metadata.get("sub2api_account_id")
    if remote_id is None or metadata.get("sub2api_delete_status") == "succeeded":
        return None
    return remote_id


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


async def _finish_pool_action(
    db: AsyncIOMotorDatabase,
    *,
    action_id: str,
    status_value: str,
    after: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    try:
        action_oid = object_id(action_id)
    except ValueError:
        return
    updates: dict[str, Any] = {"status": status_value, "finished_at": now_utc()}
    if after is not None:
        updates["after"] = after
    if error is not None:
        updates["error"] = error
    await db.pool_actions.update_one({"_id": action_oid}, {"$set": updates})


async def find_remote_duplicate(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    account: dict[str, Any],
) -> dict[str, Any] | None:
    metadata = dict(account.get("metadata", {}))
    account_json = account.get("account_json") if isinstance(account.get("account_json"), dict) else {}

    remote_id = metadata.get("sub2api_account_id")
    if remote_id is not None:
        doc = await db.sub2api_accounts_cache.find_one({"site_id": site_id, "sub2api_account_id": remote_id})
        if doc:
            return doc.get("account", {})

    values = {
        "email": credentials_email(account_json),
    }
    candidates: list[dict[str, Any]] = []
    async for doc in db.sub2api_accounts_cache.find({"site_id": site_id}):
        remote = doc.get("account", {}) if isinstance(doc.get("account"), dict) else {}
        if _remote_matches(remote, values):
            candidates.append(remote)

    in_group = [remote for remote in candidates if account_in_group(remote, group_id)]
    if in_group:
        return in_group[0]
    return candidates[0] if candidates else None


def _remote_matches(remote: dict[str, Any], values: dict[str, Any]) -> bool:
    credentials = remote.get("credentials") if isinstance(remote.get("credentials"), dict) else {}
    email = values.get("email")
    if email and credentials.get("email") == email:
        return True
    return False


async def _acquire_push_lock(
    db: AsyncIOMotorDatabase,
    account_oid: Any,
    action_id: str,
    group_id: int,
    actor: dict[str, Any],
    *,
    push_name: str,
) -> dict[str, Any] | None:
    now = now_utc()
    return await db.accounts.find_one_and_update(
        {
            "_id": account_oid,
            "metadata.deleted_at": {"$exists": False},
            "metadata.pool_status": {"$in": list(ALLOWED_PUSH_STATUSES)},
            "metadata.push_lock": {"$exists": False},
            "metadata.sub2api_push_status": {"$ne": "pushing"},
        },
        {
            "$set": {
                "metadata.push_lock": {
                    "action_id": action_id,
                    "locked_at": now,
                    "locked_by_user_id": actor.get("_id"),
                    "locked_by_name": actor_name(actor),
                    "target_group_id": group_id,
                },
                "metadata.sub2api_push_status": "pushing",
                "metadata.sub2api_account_name": push_name,
                "account_json.name": push_name,
                **operation_actor_updates(actor, "开始推送 sub2api", at=now),
            }
        },
        return_document=ReturnDocument.AFTER,
    )


async def _mark_push_completed(
    db: AsyncIOMotorDatabase,
    *,
    account_oid: Any,
    site_id: str,
    group_id: int,
    group_name: str | None,
    remote_account: dict[str, Any],
    verification: dict[str, Any],
    verification_passed: bool,
    failed_task_updates: dict[str, Any] | None,
    actor: dict[str, Any],
    verification_error: str | None = None,
) -> dict[str, Any]:
    now = now_utc()
    remote_id = remote_account.get("id")
    remote_name = remote_account.get("name") or build_sub2api_account_name(remote_account, {})
    remote_account["name"] = remote_name
    verification_status = "skipped" if verification.get("status") == "skipped" else ("passed" if verification_passed and not verification_error else "failed")
    pool_status = "active" if verification_passed else "problem"
    error = verification_error if verification_error else (None if verification_passed else str(verification.get("error") or "sub2api account test failed"))
    updates: dict[str, Any] = {
        "metadata.pool_status": pool_status,
        "metadata.pool_id": str(group_id),
        "metadata.pool_ref_type": "sub2api_group",
        "metadata.sub2api_site_id": site_id,
        "metadata.sub2api_account_id": remote_id,
        "metadata.sub2api_group_id": group_id,
        "metadata.sub2api_group_ids": _remote_group_ids(remote_account, fallback_group_id=group_id),
        "metadata.sub2api_group_name": group_name,
        "metadata.sub2api_account_name": remote_name,
        "metadata.sub2api_push_status": "succeeded",
        "metadata.sub2api_pushed_at": now,
        "metadata.sub2api_last_sync_at": now,
        "metadata.sub2api_last_error": None,
        "metadata.sub2api_manual_deleted": False,
        "metadata.sub2api_delete_status": None,
        "metadata.sub2api_delete_error": None,
        "metadata.verification_status": verification_status,
        "metadata.verification_model": verification.get("model"),
        "metadata.verification_prompt": verification.get("prompt"),
        "metadata.verification_response_preview": verification.get("response_preview"),
        "metadata.verification_latency_ms": verification.get("latency_ms"),
        "metadata.verification_checked_at": now,
        "metadata.verification_error": error,
        "metadata.last_error": error,
        **operation_actor_updates(actor, "推送 sub2api 完成", at=now),
        "account_json.name": remote_name,
    }
    if not verification_passed:
        updates["metadata.problem_snapshot"] = remote_account
    if failed_task_updates:
        updates.update(failed_task_updates)
    result = await db.accounts.find_one_and_update(
        {"_id": account_oid},
        {
            "$set": updates,
            "$unset": {
                "metadata.push_lock": "",
                "metadata.analysis.remote_uncertain": "",
                "metadata.reserve_pinned_at": "",
                "metadata.reserve_pinned_by_user_id": "",
                "metadata.reserve_pinned_by_name": "",
            },
        },
        return_document=ReturnDocument.AFTER,
    )
    return result


async def _mark_push_failed(
    db: AsyncIOMotorDatabase,
    *,
    account_oid: Any,
    original_status: str,
    error: str,
    unset_lock: bool,
    actor: dict[str, Any],
) -> None:
    now = now_utc()
    update_doc: dict[str, Any] = {
        "$set": {
            "metadata.pool_status": original_status,
            "metadata.sub2api_push_status": "failed",
            "metadata.sub2api_last_error": error,
            "metadata.last_error": error,
            **operation_actor_updates(actor, "推送 sub2api 失败", at=now),
        }
    }
    if unset_lock:
        update_doc["$unset"] = {"metadata.push_lock": ""}
    await db.accounts.update_one({"_id": account_oid}, update_doc)


async def _mark_push_uncertain(
    db: AsyncIOMotorDatabase,
    *,
    account_oid: Any,
    original_status: str,
    error: str,
    actor: dict[str, Any],
) -> None:
    now = now_utc()
    await db.accounts.update_one(
        {"_id": account_oid},
        {
            "$set": {
                "metadata.pool_status": original_status,
                "metadata.sub2api_push_status": "uncertain",
                "metadata.sub2api_last_error": error,
                "metadata.last_error": "push remote state unknown",
                "metadata.analysis.remote_uncertain": True,
                **operation_actor_updates(actor, "推送 sub2api 状态不确定", at=now),
            },
            "$unset": {"metadata.push_lock": ""},
        },
    )


async def _load_remote_snapshot(db: AsyncIOMotorDatabase, *, site_id: str, remote_id: Any) -> dict[str, Any] | None:
    doc = await db.sub2api_accounts_cache.find_one({"site_id": site_id, "sub2api_account_id": remote_id})
    if doc and isinstance(doc.get("account"), dict):
        return doc["account"]
    return None


def _remote_group_ids(remote_account: dict[str, Any], *, fallback_group_id: int) -> list[int]:
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
    ids.add(fallback_group_id)
    return sorted(ids)


def _account_oid(account_id: str) -> Any:
    try:
        return object_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found") from exc
