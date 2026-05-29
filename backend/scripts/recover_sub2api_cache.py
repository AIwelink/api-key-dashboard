from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from typing import Any

from pymongo import ReturnDocument

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.services.accounts import apply_metadata_to_account_json
from app.services.pool_lifecycle import operation_actor_updates
from app.services.sub2api_return import remote_usage_snapshot
from app.utils import credentials_email, now_utc, serialize_doc


SYSTEM_ACTOR = {
    "_id": "system:sub2api-cache-recovery",
    "name": "system:sub2api-cache-recovery",
    "email": "system:sub2api-cache-recovery",
    "role": "system",
}


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="write recovered accounts into local accounts collection")
    parser.add_argument("--site-id", default=None)
    parser.add_argument("--remote-id", type=int, action="append", default=[])
    parser.add_argument("--name", action="append", default=[])
    parser.add_argument("--limit", type=int, default=500)
    args = parser.parse_args()

    await connect_to_mongo()
    try:
        db = get_db()
        query: dict[str, Any] = {}
        if args.site_id:
            query["site_id"] = args.site_id
        if args.remote_id:
            query["sub2api_account_id"] = {"$in": args.remote_id}
        if args.name:
            query["account.name"] = {"$in": args.name}

        scanned = 0
        candidates: list[dict[str, Any]] = []
        async for doc in db.sub2api_accounts_cache.find(query).sort("fetched_at", -1).limit(args.limit):
            scanned += 1
            remote = doc.get("account") if isinstance(doc.get("account"), dict) else {}
            if not remote:
                continue
            account_json = remote_to_account_json(remote)
            existing = await find_existing_account(
                db,
                site_id=str(doc.get("site_id") or "default"),
                remote_account_id=doc.get("sub2api_account_id"),
                account_json=account_json,
            )
            if existing is not None:
                continue
            candidates.append({"cache_doc": doc, "account_json": account_json})
        candidates = dedupe_candidates(candidates)

        print(
            json.dumps(
                {
                    "mode": "apply" if args.apply else "dry_run",
                    "scanned_cache_docs": scanned,
                    "candidate_count": len(candidates),
                    "candidates": [candidate_summary(item["cache_doc"]) for item in candidates[:50]],
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )

        if not args.apply:
            return

        recovered = []
        skipped_existing = []
        for item in candidates:
            doc = item["cache_doc"]
            account_json = item["account_json"]
            existing = await find_existing_account(
                db,
                site_id=str(doc.get("site_id") or "default"),
                remote_account_id=doc.get("sub2api_account_id"),
                account_json=account_json,
            )
            if existing is not None:
                skipped_existing.append({"candidate": candidate_summary(doc), "existing_id": str(existing.get("_id"))})
                continue
            recovered_doc = await recover_one(db, cache_doc=doc, account_json=account_json)
            recovered.append(recovered_doc)

        print(
            json.dumps(
                {
                    "recovered_count": len(recovered),
                    "recovered": [
                        {
                            "id": item.get("id"),
                            "email": item.get("metadata", {}).get("email"),
                            "remote_id": item.get("metadata", {}).get("sub2api_account_id"),
                            "site_id": item.get("metadata", {}).get("sub2api_site_id"),
                            "pool_status": item.get("metadata", {}).get("pool_status"),
                        }
                        for item in recovered
                    ],
                    "skipped_existing_count": len(skipped_existing),
                    "skipped_existing": skipped_existing[:20],
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )
    finally:
        await close_mongo_connection()


async def recover_one(db: Any, *, cache_doc: dict[str, Any], account_json: dict[str, Any]) -> dict[str, Any]:
    remote = cache_doc.get("account") if isinstance(cache_doc.get("account"), dict) else {}
    site_id = str(cache_doc.get("site_id") or "default")
    remote_id = cache_doc.get("sub2api_account_id")
    now = now_utc()
    usage_snapshot = remote_usage_snapshot(remote)
    metadata = {
        "source": "sub2api_cache_recovery",
        "pool_status": "problem",
        "pool_id": None,
        "sub2api_site_id": site_id,
        "sub2api_account_id": remote_id,
        "sub2api_group_id": first_group_id(remote),
        "sub2api_group_ids": group_ids(remote),
        "sub2api_group_name": first_group_name(remote),
        "sub2api_cache_recovered": True,
        "sub2api_cache_recovered_at": now,
        "sub2api_cache_recovered_from_fetched_at": cache_doc.get("fetched_at"),
        "sub2api_delete_status": "external_deleted_recovered_from_cache",
        "sub2api_delete_remote_snapshot": remote,
        "sub2api_delete_usage_snapshot": usage_snapshot,
        "sub2api_delete_remote_last_used_at": remote.get("last_used_at"),
        "sub2api_delete_remote_status": remote.get("status"),
        "sub2api_delete_remote_error_message": remote.get("error_message"),
        "remote_status_at_return": remote.get("status"),
        "remote_schedulable_at_return": remote.get("schedulable"),
        "remote_error_at_return": remote.get("error_message"),
        "remote_last_used_at_return": remote.get("last_used_at"),
        "remote_usage_snapshot_at_return": usage_snapshot,
        "last_error": "external sub2api deletion recovered from cache",
        "created_at": now,
        "updated_at": now,
        "uploaded_by_user_id": SYSTEM_ACTOR["_id"],
        "uploader_name": SYSTEM_ACTOR["name"],
        "updated_by_user_id": SYSTEM_ACTOR["_id"],
        "updated_by_name": SYSTEM_ACTOR["name"],
        "email": credentials_email(account_json),
        "priority": 0,
        "analysis": {},
        "tags": [],
    }
    metadata.update(
        {
            key.removeprefix("metadata."): value
            for key, value in operation_actor_updates(SYSTEM_ACTOR, "从 sub2api 缓存恢复外部删除账号", at=now).items()
        }
    )
    account_json = serialize_doc(apply_metadata_to_account_json(account_json, metadata))
    metadata["sha256"] = hashlib.sha256(json.dumps(account_json, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    result = await db.accounts.find_one_and_update(
        {
            "metadata.deleted_at": {"$exists": False},
            "metadata.sub2api_site_id": site_id,
            "metadata.sub2api_account_id": remote_id,
        },
        {"$setOnInsert": {"account_json": account_json, "metadata": metadata}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return serialize_doc(result)


async def find_existing_account(db: Any, *, site_id: str, remote_account_id: Any, account_json: dict[str, Any]) -> dict[str, Any] | None:
    existing = await db.accounts.find_one(
        {
            "metadata.deleted_at": {"$exists": False},
            "metadata.sub2api_site_id": site_id,
            "metadata.sub2api_account_id": remote_account_id,
        }
    )
    if existing:
        return existing
    email = credentials_email(account_json)
    if email:
        return await db.accounts.find_one(
            {
                "metadata.deleted_at": {"$exists": False},
                "account_json.credentials.email": email,
            }
        )
    return None


def candidate_summary(doc: dict[str, Any]) -> dict[str, Any]:
    remote = doc.get("account") if isinstance(doc.get("account"), dict) else {}
    credentials = remote.get("credentials") if isinstance(remote.get("credentials"), dict) else {}
    extra = remote.get("extra") if isinstance(remote.get("extra"), dict) else {}
    return {
        "site_id": doc.get("site_id"),
        "remote_id": doc.get("sub2api_account_id"),
        "name": remote.get("name"),
        "email": credentials.get("email"),
        "status": remote.get("status"),
        "last_used_at": remote.get("last_used_at"),
        "fetched_at": doc.get("fetched_at"),
        "group_ids": doc.get("group_ids"),
    }


def dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key: dict[str, dict[str, Any]] = {}
    for item in candidates:
        key = identity_key(item["cache_doc"], item["account_json"])
        current = by_key.get(key)
        if current is None or fetched_sort_value(item["cache_doc"]) > fetched_sort_value(current["cache_doc"]):
            by_key[key] = item
    return sorted(by_key.values(), key=lambda item: fetched_sort_value(item["cache_doc"]), reverse=True)


def identity_key(cache_doc: dict[str, Any], account_json: dict[str, Any]) -> str:
    email = credentials_email(account_json)
    if email:
        return f"email:{email.lower()}"
    return f"remote:{cache_doc.get('site_id')}:{cache_doc.get('sub2api_account_id')}"


def fetched_sort_value(cache_doc: dict[str, Any]) -> str:
    value = cache_doc.get("fetched_at")
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "")


def remote_to_account_json(remote: dict[str, Any]) -> dict[str, Any]:
    account_json = {key: value for key, value in remote.items() if key not in {"id", "created_at", "updated_at", "last_used_at", "groups", "group_ids", "account_groups"}}
    account_json.setdefault("platform", "openai")
    account_json.setdefault("type", "oauth")
    account_json.setdefault("extra", {})
    account_json.setdefault("credentials", {})
    return account_json


def group_ids(remote: dict[str, Any]) -> list[int]:
    ids: set[int] = set()
    for value in remote.get("group_ids") or []:
        if isinstance(value, int):
            ids.add(value)
    for group in remote.get("groups") or []:
        if isinstance(group, dict) and isinstance(group.get("id"), int):
            ids.add(group["id"])
    for item in remote.get("account_groups") or []:
        if isinstance(item, dict) and isinstance(item.get("group_id"), int):
            ids.add(item["group_id"])
    return sorted(ids)


def first_group_id(remote: dict[str, Any]) -> int | None:
    ids = group_ids(remote)
    return ids[0] if ids else None


def first_group_name(remote: dict[str, Any]) -> str | None:
    groups = remote.get("groups") if isinstance(remote.get("groups"), list) else []
    for group in groups:
        if isinstance(group, dict) and group.get("name"):
            return str(group["name"])
    return None


if __name__ == "__main__":
    asyncio.run(main())
