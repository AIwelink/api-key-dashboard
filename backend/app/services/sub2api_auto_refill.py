import asyncio
import logging
from datetime import UTC, datetime, timedelta
from math import ceil
from typing import Any

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from app.services.pool_lifecycle import write_pool_action
from app.services.sub2api_cache import CAPACITY_ACCOUNT_LIMITS
from app.services.sub2api_push import push_account_to_sub2api
from app.utils import credentials_email, now_utc, serialize_doc


logger = logging.getLogger("app.sub2api_auto_refill")

AUTO_REFILL_INTERVAL = timedelta(minutes=30)
AUTO_REFILL_SLEEP_SECONDS = 30 * 60
AUTO_REFILL_BATCH_CONCURRENCY = 5
AUTO_REFILL_MODEL_ID = "gpt-5.4-mini"
AUTO_REFILL_REMOTE_CONCURRENCY = 10
AUTO_REFILL_REMOTE_LOAD_FACTOR = 10
AUTO_REFILL_REMOTE_PRIORITY = 100
AUTO_REFILL_RECENT_DAY_PEAK_MULTIPLE = 1.75
AUTO_REFILL_CURRENT_SPEED_DAYS = 3.5
AUTO_REFILL_ACTOR = {
    "_id": "system:auto-refill",
    "name": "system:auto-refill",
    "email": "system:auto-refill",
    "role": "system",
}


async def auto_refill_scheduler_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        try:
            await run_auto_refill_for_due_groups(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - scheduler must keep running.
            logger.exception("sub2api_auto_refill_scheduler_failed")
        await asyncio.sleep(AUTO_REFILL_SLEEP_SECONDS)


async def run_auto_refill_for_due_groups(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    cursor = db.sub2api_groups_cache.find(
        {
            "capacity_summary.auto_refill_required": True,
            "group.status": {"$ne": "disabled"},
        }
    )
    summaries: list[dict[str, Any]] = []
    async for group_doc in cursor:
        site_id = str(group_doc.get("site_id") or "default")
        group_id = int(group_doc.get("group_id") or 0)
        if group_id <= 0 or not await _auto_refill_due(db, site_id, group_id):
            continue
        summary = await auto_refill_group(db, group_doc=group_doc)
        summaries.append(summary)
    return {
        "groups": len(summaries),
        "selected": sum(item.get("selected", 0) for item in summaries),
        "succeeded": sum(item.get("succeeded", 0) for item in summaries),
        "failed": sum(item.get("failed", 0) for item in summaries),
        "items": summaries,
    }


async def auto_refill_group(db: AsyncIOMotorDatabase, *, group_doc: dict[str, Any]) -> dict[str, Any]:
    site_id = str(group_doc.get("site_id") or "default")
    group_id = int(group_doc.get("group_id") or 0)
    group = group_doc.get("group") if isinstance(group_doc.get("group"), dict) else {}
    summary = group_doc.get("capacity_summary") if isinstance(group_doc.get("capacity_summary"), dict) else {}
    need_count = _needed_refill_count(summary)
    result: dict[str, Any] = {
        "site_id": site_id,
        "group_id": group_id,
        "group_name": group.get("name"),
        "need_count": need_count,
        "selected": 0,
        "succeeded": 0,
        "failed": 0,
        "skipped": False,
        "errors": [],
    }
    if need_count <= 0:
        result["skipped"] = True
        result["reason"] = "auto refill threshold already satisfied"
        await _mark_auto_refill_finished(db, result)
        return result

    candidates = await _select_refill_candidates(db, site_id=site_id, group_id=group_id, account_type=str(summary.get("account_type") or ""), limit=need_count)
    result["selected"] = len(candidates)
    if not candidates:
        result["skipped"] = True
        result["reason"] = "no eligible reserve accounts"
        await _mark_auto_refill_finished(db, result)
        return result

    async def push_one(account: dict[str, Any]) -> dict[str, Any]:
        account_id = str(account["_id"])
        metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
        try:
            response = await push_account_to_sub2api(
                db,
                account_id=account_id,
                site_id=site_id,
                group_id=group_id,
                run_verification=True,
                model_id=AUTO_REFILL_MODEL_ID,
                prompt="",
                concurrency=AUTO_REFILL_REMOTE_CONCURRENCY,
                load_factor=AUTO_REFILL_REMOTE_LOAD_FACTOR,
                priority=AUTO_REFILL_REMOTE_PRIORITY,
                reason="auto refill from reserve pool",
                actor=AUTO_REFILL_ACTOR,
            )
            updated_account = response.get("account") or {}
            updated_metadata = updated_account.get("metadata") or {}
            verification = response.get("verification") or {}
            return {
                "account_id": account_id,
                "email": credentials_email(account.get("account_json") or {}),
                "succeeded": updated_metadata.get("pool_status") == "active",
                "pool_status": updated_metadata.get("pool_status"),
                "remote_id": updated_metadata.get("sub2api_account_id"),
                "verification_status": updated_metadata.get("verification_status"),
                "verification_error": updated_metadata.get("verification_error") or verification.get("error"),
                "updated_at": updated_metadata.get("updated_at"),
            }
        except HTTPException as exc:
            return {
                "account_id": account_id,
                "email": credentials_email(account.get("account_json") or {}),
                "succeeded": False,
                "pool_status": metadata.get("pool_status"),
                "error": str(exc.detail),
            }
        except Exception as exc:  # noqa: BLE001 - keep batch running.
            logger.exception("sub2api_auto_refill_push_failed site_id=%s group_id=%s account_id=%s", site_id, group_id, account_id)
            return {
                "account_id": account_id,
                "email": credentials_email(account.get("account_json") or {}),
                "succeeded": False,
                "pool_status": metadata.get("pool_status"),
                "error": str(exc),
            }

    push_results = await _run_limited(candidates, AUTO_REFILL_BATCH_CONCURRENCY, push_one)
    result["succeeded"] = sum(1 for item in push_results if item.get("succeeded") is True)
    result["failed"] = len(push_results) - result["succeeded"]
    result["accounts"] = push_results
    result["errors"] = [item for item in push_results if item.get("succeeded") is not True][:20]
    await _mark_auto_refill_finished(db, result)
    await write_pool_action(
        db,
        action_type="auto_refill_batch",
        actor=AUTO_REFILL_ACTOR,
        pool_id=str(group_id),
        status_value="succeeded" if result["failed"] == 0 else "failed",
        reason="auto refill reserve accounts into sub2api group",
        after=result,
    )
    logger.info(
        "sub2api_auto_refill_finished site_id=%s group_id=%s need=%s selected=%s succeeded=%s failed=%s",
        site_id,
        group_id,
        need_count,
        result["selected"],
        result["succeeded"],
        result["failed"],
    )
    return result


def _needed_refill_count(summary: dict[str, Any]) -> int:
    account_type = str(summary.get("account_type") or "")
    limits = CAPACITY_ACCOUNT_LIMITS.get(account_type)
    if not limits:
        return 0
    active_5h_capacity = _float_or_zero(summary.get("active_five_hour_capacity_usd"))
    active_7d_capacity = _float_or_zero(summary.get("active_seven_day_capacity_usd"))
    recent_day_5h_peak = _float_or_zero(summary.get("recent_day_five_hour_peak_cost"))
    recent_24h_cost = _float_or_zero(summary.get("recent_24h_cost"))
    needed_5h_capacity = max(0.0, recent_day_5h_peak * AUTO_REFILL_RECENT_DAY_PEAK_MULTIPLE - active_5h_capacity)
    needed_7d_capacity = max(0.0, recent_24h_cost * AUTO_REFILL_CURRENT_SPEED_DAYS - active_7d_capacity)
    needed_5h_accounts = ceil(needed_5h_capacity / limits["five_hour_usd"]) if needed_5h_capacity > 0 else 0
    needed_7d_accounts = ceil(needed_7d_capacity / limits["seven_day_usd"]) if needed_7d_capacity > 0 else 0
    return max(needed_5h_accounts, needed_7d_accounts)


async def list_auto_refill_logs(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None = None,
    group_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    query: dict[str, Any] = {"action_type": "auto_refill_batch"}
    if group_id is not None:
        query["pool_id"] = str(group_id)
    cursor = db.pool_actions.find(query).sort("created_at", -1).limit(max(1, min(limit, 100)))
    raw_items = [doc async for doc in cursor]
    if site_id:
        raw_items = [doc for doc in raw_items if (doc.get("after") or {}).get("site_id") == site_id]
    account_ids = {
        item.get("account_id")
        for doc in raw_items
        for item in _auto_refill_account_items(doc)
        if isinstance(item.get("account_id"), str) and ObjectId.is_valid(item["account_id"])
    }
    account_map = {}
    if account_ids:
        async for account in db.accounts.find({"_id": {"$in": [ObjectId(account_id) for account_id in account_ids]}}):
            account_map[str(account["_id"])] = account

    items = [_format_auto_refill_log(doc, account_map) for doc in raw_items]
    return {"items": serialize_doc(items), "total": len(items)}


async def _select_refill_candidates(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    account_type: str,
    limit: int,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {
        "metadata.deleted_at": {"$exists": False},
        "metadata.pool_status": "reserve",
        "metadata.sub2api_site_id": site_id,
        "metadata.push_lock": {"$exists": False},
        "metadata.sub2api_push_status": {"$ne": "pushing"},
        "$or": [
            {"metadata.sub2api_group_id": group_id},
            {"metadata.pool_id": str(group_id)},
        ],
        "$and": [
            {"$or": [{"metadata.last_error": {"$exists": False}}, {"metadata.last_error": None}, {"metadata.last_error": ""}]},
            {"$or": [{"metadata.sub2api_account_id": {"$exists": False}}, {"metadata.sub2api_account_id": None}, {"metadata.sub2api_account_id": ""}]},
        ],
    }
    if account_type in CAPACITY_ACCOUNT_LIMITS:
        query["metadata.account_type"] = account_type
    cursor = (
        db.accounts.find(query)
        .sort([("metadata.reserve_pinned_at", -1), ("metadata.updated_at", 1), ("metadata.created_at", 1)])
        .limit(max(0, limit))
    )
    return [account async for account in cursor]


def _auto_refill_account_items(doc: dict[str, Any]) -> list[dict[str, Any]]:
    after = doc.get("after") if isinstance(doc.get("after"), dict) else {}
    accounts = after.get("accounts") if isinstance(after.get("accounts"), list) else []
    if accounts:
        return [item for item in accounts if isinstance(item, dict)]
    errors = after.get("errors") if isinstance(after.get("errors"), list) else []
    return [item for item in errors if isinstance(item, dict)]


def _format_auto_refill_log(doc: dict[str, Any], account_map: dict[str, dict[str, Any]]) -> dict[str, Any]:
    after = doc.get("after") if isinstance(doc.get("after"), dict) else {}
    account_items = []
    for item in _auto_refill_account_items(doc):
        account_id = str(item.get("account_id") or "")
        account = account_map.get(account_id) or {}
        metadata = account.get("metadata") if isinstance(account.get("metadata"), dict) else {}
        account_json = account.get("account_json") if isinstance(account.get("account_json"), dict) else {}
        current_status = metadata.get("pool_status") or item.get("pool_status")
        account_items.append(
            {
                "account_id": account_id,
                "email": credentials_email(account_json) or item.get("email"),
                "succeeded": item.get("succeeded") is True,
                "result": "成功" if item.get("succeeded") is True else "失败",
                "current_status": current_status,
                "current_status_label": _pool_status_label(str(current_status or "")),
                "remote_id": metadata.get("sub2api_account_id") or item.get("remote_id"),
                "verification_status": metadata.get("verification_status") or item.get("verification_status"),
                "error": item.get("error") or item.get("verification_error") or metadata.get("last_error"),
                "updated_at": metadata.get("updated_at") or item.get("updated_at"),
            }
        )
    return {
        "id": doc.get("_id"),
        "created_at": doc.get("created_at"),
        "finished_at": doc.get("finished_at"),
        "status": doc.get("status"),
        "site_id": after.get("site_id"),
        "group_id": after.get("group_id"),
        "group_name": after.get("group_name"),
        "need_count": after.get("need_count", 0),
        "selected": after.get("selected", 0),
        "succeeded": after.get("succeeded", 0),
        "failed": after.get("failed", 0),
        "skipped": after.get("skipped", False),
        "reason": after.get("reason"),
        "accounts": account_items,
    }


def _pool_status_label(value: str) -> str:
    return {
        "library": "总库",
        "available": "可用池",
        "reserve": "使用备选池",
        "active": "实际使用池",
        "problem": "问题账号",
        "discarded": "弃用",
    }.get(value, value or "-")


async def _auto_refill_due(db: AsyncIOMotorDatabase, site_id: str, group_id: int) -> bool:
    meta_id = f"{site_id}:{group_id}"
    meta = await db.sub2api_auto_refill_meta.find_one({"_id": meta_id})
    last_finished_at = meta.get("last_finished_at") if meta else None
    if last_finished_at is None:
        return True
    if isinstance(last_finished_at, str):
        try:
            last_finished_at = datetime.fromisoformat(last_finished_at)
        except ValueError:
            return True
    if not isinstance(last_finished_at, datetime):
        return True
    if isinstance(last_finished_at, datetime) and last_finished_at.tzinfo is None:
        last_finished_at = last_finished_at.replace(tzinfo=UTC)
    return now_utc() - last_finished_at >= AUTO_REFILL_INTERVAL


async def _mark_auto_refill_finished(db: AsyncIOMotorDatabase, result: dict[str, Any]) -> None:
    now = now_utc()
    meta_id = f"{result['site_id']}:{result['group_id']}"
    await db.sub2api_auto_refill_meta.update_one(
        {"_id": meta_id},
        {
            "$set": {
                "site_id": result["site_id"],
                "group_id": result["group_id"],
                "last_finished_at": now,
                "last_result": result,
            }
        },
        upsert=True,
    )


async def _run_limited(items: list[dict[str, Any]], concurrency: int, worker) -> list[dict[str, Any]]:
    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def run_one(item: dict[str, Any]) -> dict[str, Any]:
        async with semaphore:
            return await worker(item)

    return await asyncio.gather(*(run_one(item) for item in items))


def _float_or_zero(value: Any) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0
