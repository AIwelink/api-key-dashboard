import hashlib
import json
from typing import Any

from fastapi import HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.pool_lifecycle import operation_actor_updates
from app.utils import extract_email, now_utc, object_id, serialize_doc


EXTRA_METADATA_KEYS = {
    "email_session",
    "account_type",
    "payment_type",
    "2FA",
    "self_produced",
    "purchase_source",
    "purchase_account_type",
    "phone_bound",
    "phone_number",
    "remark",
    "manual_status_label",
    "source_template",
}


LIST_ACCOUNT_PROJECTION = {
    "metadata": 1,
    "account_json.name": 1,
    "account_json.platform": 1,
    "account_json.type": 1,
    "account_json.extra.email": 1,
    "account_json.extra.email_session": 1,
    "account_json.extra.mailbox_connection": 1,
    "account_json.extra.2FA": 1,
    "account_json.extra.password": 1,
    "account_json.credentials.email": 1,
    "account_json.credentials.plan_type": 1,
}


def normalize_phone_bound(value: Any) -> bool | Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "yes", "1"}:
            return True
        if normalized in {"false", "no", "0"}:
            return False
    return value


def normalized_metadata_updates(metadata: dict[str, Any] | None) -> dict[str, Any]:
    updates = dict(metadata or {})
    if "phone_bound" in updates:
        updates["phone_bound"] = normalize_phone_bound(updates["phone_bound"])
    return updates


def apply_metadata_to_account_json(account_json: dict[str, Any], metadata: dict[str, Any] | None) -> dict[str, Any]:
    if not metadata:
        return account_json

    next_json = dict(account_json)
    credentials = dict(next_json.get("credentials") or {})
    extra = dict(next_json.get("extra") or {})
    updates = normalized_metadata_updates(metadata)
    is_edit = updates.get("source") == "edit"

    if updates.get("source") == "fill":
        email_session = updates.get("email_session")
        if email_session and "email" not in credentials:
            credentials["email"] = email_session
        if updates.get("account_type"):
            credentials["plan_type"] = updates["account_type"]

    for key in EXTRA_METADATA_KEYS:
        if key not in updates:
            continue
        value = updates[key]
        if value is None or value == "":
            if is_edit:
                extra.pop(key, None)
            continue
        extra[key] = value

    next_json["credentials"] = credentials
    next_json["extra"] = extra
    return next_json


def normalize_metadata(
    account_json: dict[str, Any],
    metadata: dict[str, Any] | None,
    *,
    actor: dict[str, Any] | None,
    existing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    current = dict(existing or {})
    updates = normalized_metadata_updates(metadata)
    now = now_utc()

    if "created_at" not in current:
        current["created_at"] = now
    current["updated_at"] = now

    if actor is not None and "uploaded_by_user_id" not in current:
        current["uploaded_by_user_id"] = actor.get("_id")
    if actor is not None and "uploader_name" not in current:
        current["uploader_name"] = actor.get("name") or actor.get("email")
    if actor is not None:
        current["updated_by_user_id"] = actor.get("_id")
        current["updated_by_name"] = actor.get("name") or actor.get("email")

    for key, value in updates.items():
        if key in {"created_at", "updated_at", "uploaded_by_user_id"}:
            continue
        current[key] = value

    if not current.get("email"):
        email = extract_email(account_json)
        if email:
            current["email"] = email

    extra = account_json.get("extra") if isinstance(account_json.get("extra"), dict) else {}
    credentials = account_json.get("credentials") if isinstance(account_json.get("credentials"), dict) else {}
    for key in EXTRA_METADATA_KEYS:
        if current.get(key) not in {None, ""} or key not in extra:
            continue
        value = extra[key]
        if value is None or value == "":
            continue
        current[key] = normalize_phone_bound(value) if key == "phone_bound" else value
    if not current.get("account_type") and credentials.get("plan_type"):
        current["account_type"] = credentials["plan_type"]
    if not current.get("phone_number") and extra.get("phone"):
        current["phone_number"] = extra["phone"]

    current.setdefault("tags", [])
    current.setdefault("source", "manual")
    current.setdefault("pool_status", "library")
    current.setdefault("priority", 0)
    current.setdefault("analysis", {})
    current["sha256"] = hashlib.sha256(
        json.dumps(account_json, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return current


async def create_account(
    db: AsyncIOMotorDatabase,
    *,
    account_json: dict[str, Any],
    metadata: dict[str, Any] | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    account_json = apply_metadata_to_account_json(account_json, metadata)
    normalized_metadata = normalize_metadata(account_json, metadata, actor=actor)
    document = {"account_json": account_json, "metadata": normalized_metadata}
    result = await db.accounts.insert_one(document)
    created = await db.accounts.find_one({"_id": result.inserted_id})
    return serialize_doc(created)


async def get_account_or_404(db: AsyncIOMotorDatabase, account_id: str) -> dict[str, Any]:
    try:
        oid = object_id(account_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found") from exc
    account = await db.accounts.find_one({"_id": oid, "metadata.deleted_at": {"$exists": False}})
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    return account


async def update_account(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    account_json: dict[str, Any] | None,
    metadata: dict[str, Any] | None,
    actor: dict[str, Any],
) -> dict[str, Any]:
    account = await get_account_or_404(db, account_id)
    next_account_json = account_json if account_json is not None else account["account_json"]
    next_account_json = apply_metadata_to_account_json(next_account_json, metadata)
    next_metadata = normalize_metadata(
        next_account_json,
        metadata,
        actor=actor,
        existing=account.get("metadata", {}),
    )
    await db.accounts.update_one(
        {"_id": account["_id"]},
        {"$set": {"account_json": next_account_json, "metadata": next_metadata}},
    )
    updated = await db.accounts.find_one({"_id": account["_id"]})
    return serialize_doc(updated)


async def soft_delete_account(
    db: AsyncIOMotorDatabase,
    *,
    account_id: str,
    actor: dict[str, Any],
) -> None:
    account = await get_account_or_404(db, account_id)
    metadata = dict(account.get("metadata", {}))
    now = now_utc()
    metadata["deleted_at"] = now
    metadata["deleted_by"] = actor.get("_id")
    metadata.update(
        {
            key.removeprefix("metadata."): value
            for key, value in operation_actor_updates(actor, "删除本地账号", at=now).items()
        }
    )
    await db.accounts.update_one({"_id": account["_id"]}, {"$set": {"metadata": metadata}})


async def list_accounts(
    db: AsyncIOMotorDatabase,
    *,
    q: str | None = None,
    status_filter: str | None = None,
    payment_type: str | None = None,
    account_type: str | None = None,
    phone_bound: bool | None = None,
    uploader_name: str | None = None,
    manual_status_label: str | None = None,
    account_scope: str | None = None,
    pool_status: str | None = None,
    pool_id: str | None = None,
    site_id: str | None = None,
    sort_by: str = "updated_at",
    sort_dir: str = "desc",
    skip: int = 0,
    limit: int = 50,
) -> dict[str, Any]:
    query: dict[str, Any] = {"metadata.deleted_at": {"$exists": False}}
    if status_filter:
        query["metadata.account_status"] = status_filter
    if payment_type:
        query["metadata.payment_type"] = payment_type
    if account_type:
        query["metadata.account_type"] = account_type
    if phone_bound is not None:
        query["metadata.phone_bound"] = phone_bound
    if uploader_name:
        query["metadata.uploader_name"] = {"$regex": uploader_name, "$options": "i"}
    if manual_status_label:
        query["metadata.manual_status_label"] = {"$regex": manual_status_label, "$options": "i"}
    if account_scope == "problem":
        query["metadata.pool_status"] = "problem"
    elif account_scope == "normal":
        query["metadata.pool_status"] = pool_status if pool_status and pool_status not in {"problem", "discarded"} else {"$nin": ["problem", "discarded"]}
    elif pool_status:
        query["metadata.pool_status"] = pool_status
    if pool_id:
        query["metadata.pool_id"] = pool_id
    if site_id:
        query["metadata.sub2api_site_id"] = site_id
    if q:
        query["$or"] = [
            {"metadata.email": {"$regex": q, "$options": "i"}},
            {"metadata.uploader_name": {"$regex": q, "$options": "i"}},
            {"metadata.updated_by_name": {"$regex": q, "$options": "i"}},
            {"metadata.manual_status_label": {"$regex": q, "$options": "i"}},
            {"metadata.remark": {"$regex": q, "$options": "i"}},
            {"account_json.name": {"$regex": q, "$options": "i"}},
        ]

    sort_fields = {
        "created_at": "metadata.created_at",
        "updated_at": "metadata.updated_at",
        "email": "metadata.email",
        "payment_type": "metadata.payment_type",
        "account_type": "metadata.account_type",
        "account_status": "metadata.account_status",
        "used_quota": "metadata.used_quota",
        "last_request_at": "metadata.last_request_at",
        "pool_status": "metadata.pool_status",
        "priority": "metadata.priority",
    }
    if sort_by == "reserve_order" or pool_status == "reserve":
        sort_spec = [("metadata.reserve_pinned_at", -1), ("metadata.updated_at", 1), ("metadata.created_at", 1)]
    else:
        sort_field = sort_fields.get(sort_by, "metadata.updated_at")
        sort_direction = 1 if sort_dir == "asc" else -1
        sort_spec = [(sort_field, sort_direction)]

    cursor = db.accounts.find(query, LIST_ACCOUNT_PROJECTION).sort(sort_spec).skip(skip).limit(limit)
    items = [serialize_doc(item) async for item in cursor]
    total = await db.accounts.count_documents(query)
    return {"items": items, "total": total, "skip": skip, "limit": limit}
