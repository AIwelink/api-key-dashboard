import hashlib
import json
import logging
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.accounts import create_account, update_account
from app.services.json_parser import extract_account_objects
from app.services.pool_lifecycle import write_pool_action
from app.utils import credentials_email, now_utc, serialize_doc


logger = logging.getLogger("app.import_batches")


def payload_sha256(payload: Any) -> str:
    if isinstance(payload, str):
        raw = payload
    else:
        raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def create_import_batch(
    db: AsyncIOMotorDatabase,
    *,
    payload: Any,
    name: str | None,
    upload_intent: str,
    source_template: str,
    remark: str | None,
    metadata_defaults: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any]:
    accounts = extract_account_objects(payload, source_template=source_template)
    now = now_utc()
    logger.info(
        "import_batch_start name=%s upload_intent=%s account_count=%s actor=%s",
        name,
        upload_intent,
        len(accounts),
        actor.get("_id"),
    )
    batch_doc = {
        "name": name or f"import-{now.strftime('%Y%m%d-%H%M%S')}",
        "upload_intent": upload_intent,
        "source_template": source_template,
        "uploaded_by_user_id": actor.get("_id"),
        "uploader_name": actor.get("name") or actor.get("email"),
        "created_at": now,
        "raw_sha256": payload_sha256(payload),
        "total_count": len(accounts),
        "created_count": 0,
        "updated_count": 0,
        "blocked_count": 0,
        "error_count": 0,
        "status": "ok",
        "remark": remark,
    }
    result = await db.import_batches.insert_one(batch_doc)
    batch_id = str(result.inserted_id)

    created: list[str] = []
    updated: list[str] = []
    blocked: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for account_json in accounts:
        try:
            existing = await find_existing_account(db, account_json)
            base_metadata = {
                **metadata_defaults,
                "source": "import_batch",
                "batch_id": batch_id,
                "upload_intent": upload_intent,
                "source_template": source_template,
            }
            if existing:
                existing_metadata = existing.get("metadata", {})
                existing_pool_status = existing_metadata.get("pool_status")
                if existing_pool_status == "active":
                    blocked_item = {
                        "account_id": str(existing["_id"]),
                        "name": account_json.get("name"),
                        "email": credentials_email(account_json),
                        "reason": "existing account is active; JSON update was blocked",
                    }
                    blocked.append(blocked_item)
                    await write_pool_action(
                        db,
                        action_type="update_account_blocked",
                        actor=actor,
                        account_id=str(existing["_id"]),
                        status_value="failed",
                        reason=blocked_item["reason"],
                        before={
                            "sha256": existing_metadata.get("sha256"),
                            "email": existing_metadata.get("email"),
                            "pool_status": existing_pool_status,
                        },
                        error=blocked_item["reason"],
                    )
                    logger.warning(
                        "import_batch_update_blocked batch_id=%s account_id=%s email=%s reason=%s",
                        batch_id,
                        str(existing["_id"]),
                        blocked_item["email"],
                        blocked_item["reason"],
                    )
                    continue
                before_sha = existing.get("metadata", {}).get("sha256")
                metadata = dict(base_metadata)
                if not existing_pool_status:
                    metadata["pool_status"] = "library"
                account = await update_account(
                    db,
                    account_id=str(existing["_id"]),
                    account_json=account_json,
                    metadata=metadata,
                    actor=actor,
                )
                updated.append(account["id"])
                await write_pool_action(
                    db,
                    action_type="update_account",
                    actor=actor,
                    account_id=account["id"],
                    reason="import batch matched existing account",
                    before={"sha256": before_sha, "email": existing_metadata.get("email"), "pool_status": existing_pool_status},
                    after={
                        "sha256": account.get("metadata", {}).get("sha256"),
                        "email": account.get("metadata", {}).get("email"),
                        "pool_status": account.get("metadata", {}).get("pool_status"),
                    },
                )
            else:
                metadata = {**base_metadata, "pool_status": "library"}
                account = await create_account(db, account_json=account_json, metadata=metadata, actor=actor)
                created.append(account["id"])
                await write_pool_action(
                    db,
                    action_type="import_account",
                    actor=actor,
                    account_id=account["id"],
                    reason="import batch created account",
                    after={"batch_id": batch_id, "upload_intent": upload_intent, "pool_status": "library"},
                )
        except Exception as exc:  # Keep importing other valid accounts in the batch.
            errors.append({"name": account_json.get("name"), "error": str(exc)})
            logger.exception("import_batch_account_failed batch_id=%s name=%s", batch_id, account_json.get("name"))

    status_value = "ok" if not errors and not blocked else "has_error"
    await db.import_batches.update_one(
        {"_id": result.inserted_id},
        {
            "$set": {
                "created_count": len(created),
                "updated_count": len(updated),
                "blocked_count": len(blocked),
                "error_count": len(errors),
                "status": status_value,
                "blocked": blocked,
                "errors": errors,
            }
        },
    )
    batch = await db.import_batches.find_one({"_id": result.inserted_id})
    logger.info(
        "import_batch_finished batch_id=%s created=%s updated=%s blocked=%s errors=%s status=%s",
        batch_id,
        len(created),
        len(updated),
        len(blocked),
        len(errors),
        status_value,
    )
    return {"batch": serialize_doc(batch), "created": created, "updated": updated, "blocked": blocked, "errors": errors}


async def find_existing_account(db: AsyncIOMotorDatabase, account_json: dict[str, Any]) -> dict[str, Any] | None:
    email = credentials_email(account_json)
    if not email:
        return None
    return await db.accounts.find_one({"account_json.credentials.email": email, "metadata.deleted_at": {"$exists": False}})
