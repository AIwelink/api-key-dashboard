import logging
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.utils import now_utc, object_id, serialize_doc


logger = logging.getLogger("app.api_pools")

DEFAULT_POOL_VALUES = {
    "min_active": 20,
    "target_active": 30,
    "max_avg_5h_used": 70,
    "max_avg_7d_used": 80,
    "min_reserve": 10,
    "status": "active",
}


async def list_api_pools(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    await sync_api_pools_from_sub2api_groups(db)
    site_ids = await _active_site_ids(db)
    fallback_site_id = next(iter(site_ids)) if len(site_ids) == 1 else None
    items = []
    async for item in db.api_pools.find({}).sort("created_at", -1):
        pool = dict(item)
        original_site_id = str(pool.get("site_id") or "").strip()
        resolved_site_id = _resolve_pool_site_id(original_site_id, site_ids, fallback_site_id)
        if resolved_site_id != original_site_id:
            pool["site_id"] = resolved_site_id
            pool["site_id_resolved_from"] = original_site_id or None
        items.append(serialize_doc(pool))
    return {"items": items, "total": len(items)}


async def create_api_pool(db: AsyncIOMotorDatabase, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    now = now_utc()
    site_ids = await _active_site_ids(db)
    fallback_site_id = next(iter(site_ids)) if len(site_ids) == 1 else None
    doc = {
        **DEFAULT_POOL_VALUES,
        **payload,
        "created_by": actor.get("_id"),
        "created_by_name": actor.get("name") or actor.get("email"),
        "created_at": now,
        "updated_at": now,
    }
    doc["site_id"] = _resolve_pool_site_id(str(doc.get("site_id") or "").strip(), site_ids, fallback_site_id)
    result = await db.api_pools.insert_one(doc)
    created = await db.api_pools.find_one({"_id": result.inserted_id})
    logger.info(
        "api_pool_created pool_id=%s name=%s account_type=%s active_group_id=%s actor=%s",
        str(result.inserted_id),
        doc.get("name"),
        doc.get("account_type"),
        doc.get("active_group_id"),
        actor.get("_id"),
    )
    return serialize_doc(created)


async def get_api_pool_or_404(db: AsyncIOMotorDatabase, pool_id: str) -> dict[str, Any]:
    try:
        oid = object_id(pool_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API pool not found") from exc
    pool = await db.api_pools.find_one({"_id": oid})
    if pool is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API pool not found")
    return pool


async def update_api_pool(db: AsyncIOMotorDatabase, pool_id: str, payload: dict[str, Any], actor: dict[str, Any]) -> dict[str, Any]:
    pool = await get_api_pool_or_404(db, pool_id)
    updates = {key: value for key, value in payload.items() if value is not None}
    if "site_id" in updates:
        site_ids = await _active_site_ids(db)
        fallback_site_id = next(iter(site_ids)) if len(site_ids) == 1 else None
        updates["site_id"] = _resolve_pool_site_id(str(updates.get("site_id") or "").strip(), site_ids, fallback_site_id)
    updates["updated_at"] = now_utc()
    updates["updated_by"] = actor.get("_id")
    updates["updated_by_name"] = actor.get("name") or actor.get("email")
    await db.api_pools.update_one({"_id": pool["_id"]}, {"$set": updates})
    updated = await db.api_pools.find_one({"_id": pool["_id"]})
    logger.info("api_pool_updated pool_id=%s fields=%s actor=%s", pool_id, sorted(updates.keys()), actor.get("_id"))
    return serialize_doc(updated)


async def sync_api_pools_from_sub2api_groups(db: AsyncIOMotorDatabase, site_id: str | None = None) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if site_id:
        query["site_id"] = site_id

    created = 0
    matched = 0
    skipped = 0
    now = now_utc()

    cursor = db.sub2api_groups_cache.find(query).sort([("site_id", 1), ("group_id", 1)])
    async for group_doc in cursor:
        group_id = _int_or_none(group_doc.get("group_id"))
        group_site_id = str(group_doc.get("site_id") or "").strip() or "default"
        if group_id is None:
            skipped += 1
            continue

        existing = await _find_pool_for_sub2api_group(db, site_id=group_site_id, group_id=group_id)
        if existing is not None:
            matched += 1
            updates = _existing_pool_sync_updates(existing, group_doc)
            if updates:
                updates["updated_at"] = now
                updates.setdefault("updated_by", "system")
                updates.setdefault("updated_by_name", "system")
                await db.api_pools.update_one({"_id": existing["_id"]}, {"$set": updates})
            continue

        group = group_doc.get("group") if isinstance(group_doc.get("group"), dict) else {}
        doc = {
            **DEFAULT_POOL_VALUES,
            "name": _pool_name_from_group(group_doc),
            "account_type": _infer_account_type(group_doc),
            "site_id": group_site_id,
            "active_group_id": group_id,
            "verification_group_id": None,
            "source": "sub2api_group",
            "source_group_key": _source_group_key(group_site_id, group_id),
            "sub2api_group_name": group.get("name"),
            "sub2api_group_status": group.get("status"),
            "created_by": "system",
            "created_by_name": "system",
            "updated_by": "system",
            "updated_by_name": "system",
            "created_at": now,
            "updated_at": now,
        }
        await db.api_pools.insert_one(doc)
        created += 1

    if created:
        logger.info("api_pools_synced_from_sub2api_groups site_id=%s created=%s matched=%s skipped=%s", site_id or "*", created, matched, skipped)
    return {"ok": True, "created": created, "matched": matched, "skipped": skipped}


async def _active_site_ids(db: AsyncIOMotorDatabase) -> set[str]:
    return {
        str(site["_id"])
        async for site in db.sub2api_sites.find({"status": {"$ne": "deleted"}}, {"_id": 1})
        if site.get("_id")
    }


def _resolve_pool_site_id(site_id: str, site_ids: set[str], fallback_site_id: str | None) -> str:
    if site_id and site_id in site_ids:
        return site_id
    if fallback_site_id and (not site_id or site_id == "default" or site_id not in site_ids):
        return fallback_site_id
    return site_id or "default"


async def _find_pool_for_sub2api_group(db: AsyncIOMotorDatabase, *, site_id: str, group_id: int) -> dict[str, Any] | None:
    source_key = _source_group_key(site_id, group_id)
    pool = await db.api_pools.find_one({"source_group_key": source_key})
    if pool is not None:
        return pool
    return await db.api_pools.find_one({"site_id": site_id, "active_group_id": group_id})


def _existing_pool_sync_updates(pool: dict[str, Any], group_doc: dict[str, Any]) -> dict[str, Any]:
    group_id = _int_or_none(group_doc.get("group_id"))
    site_id = str(group_doc.get("site_id") or "").strip() or "default"
    if group_id is None:
        return {}

    group = group_doc.get("group") if isinstance(group_doc.get("group"), dict) else {}
    desired = {
        "source_group_key": _source_group_key(site_id, group_id),
        "sub2api_group_name": group.get("name"),
        "sub2api_group_status": group.get("status"),
    }
    if pool.get("source") in (None, "", "sub2api_group"):
        desired["source"] = "sub2api_group"

    return {key: value for key, value in desired.items() if pool.get(key) != value}


def _source_group_key(site_id: str, group_id: int) -> str:
    return f"{site_id}:{group_id}"


def _pool_name_from_group(group_doc: dict[str, Any]) -> str:
    group_id = _int_or_none(group_doc.get("group_id"))
    group = group_doc.get("group") if isinstance(group_doc.get("group"), dict) else {}
    name = str(group.get("name") or "").strip()
    site_id = str(group_doc.get("site_id") or "").strip() or "default"
    if name:
        return name
    return f"{site_id} group #{group_id}" if group_id is not None else f"{site_id} group"


def _infer_account_type(group_doc: dict[str, Any]) -> str:
    group = group_doc.get("group") if isinstance(group_doc.get("group"), dict) else {}
    capacity = group_doc.get("capacity_summary") if isinstance(group_doc.get("capacity_summary"), dict) else {}
    candidates = [
        capacity.get("account_type"),
        group.get("account_type"),
        group.get("subscription_type"),
        group.get("plan_type"),
        group.get("name"),
    ]
    joined = " ".join(str(item or "").lower() for item in candidates)
    if "pro" in joined:
        return "pro"
    if "team" in joined:
        return "team"
    if "k12" in joined:
        return "k12"
    if "free" in joined:
        return "free"
    if "plus" in joined:
        return "plus"
    return "plus"


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None
