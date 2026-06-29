from typing import Any

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import ImportCommitRequest, ImportPreviewRequest
from app.security import require_roles
from app.modules.accounts.accounts import create_account
from app.modules.system.audit import write_audit_log
from app.modules.accounts.json_parser import extract_account_objects
from app.utils import credentials_email, now_utc


router = APIRouter(tags=["imports"])


@router.post("/imports/preview")
async def preview_import(
    payload: ImportPreviewRequest,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    accounts = extract_account_objects(payload.payload, source_template=payload.source_template)
    items = []
    create_count = 0
    update_count = 0
    conflict_count = 0
    for account_json in accounts:
        name = account_json.get("name")
        email = credentials_email(account_json)
        existing = None
        if email:
            existing = await db.accounts.find_one(
                {
                    "account_json.credentials.email": email,
                    "metadata.deleted_at": {"$exists": False},
                }
            )
        action = "create"
        if existing:
            action = "update" if existing.get("account_json") == account_json else "conflict"
        if action == "create":
            create_count += 1
        elif action == "update":
            update_count += 1
        else:
            conflict_count += 1
        items.append({"name": name, "email": email, "action": action})

    return {
        "summary": {
            "create": create_count,
            "update": update_count,
            "conflict": conflict_count,
            "invalid": 0,
        },
        "items": items,
    }


@router.post("/imports/commit")
async def commit_import(
    payload: ImportCommitRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    accounts = extract_account_objects(payload.payload, source_template=payload.source_template)
    created = []
    for account_json in accounts:
        metadata = dict(payload.metadata_defaults)
        metadata.setdefault("source", "import")
        account = await create_account(db, account_json=account_json, metadata=metadata, actor=actor)
        created.append(account["id"])
    await write_audit_log(
        db,
        actor=actor,
        action="import.commit",
        resource_type="account",
        after={"created": len(created)},
    )
    return {"created": created, "count": len(created)}


@router.get("/exports/sub2api")
async def export_sub2api(
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    cursor = db.accounts.find({"metadata.deleted_at": {"$exists": False}}).sort("metadata.created_at", 1)
    accounts = [item["account_json"] async for item in cursor]
    return {"exported_at": now_utc().isoformat(), "proxies": [], "accounts": accounts}
