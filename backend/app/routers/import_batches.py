from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import ImportBatchCreate
from app.security import require_roles
from app.services.audit import write_audit_log
from app.services.import_batches import create_import_batch
from app.utils import object_id, serialize_doc


router = APIRouter(prefix="/import-batches", tags=["import-batches"])


@router.post("")
async def post_import_batch(
    payload: ImportBatchCreate,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await create_import_batch(
        db,
        payload=payload.payload,
        name=payload.name,
        upload_intent=payload.upload_intent,
        source_template=payload.source_template,
        remark=payload.remark,
        metadata_defaults=payload.metadata_defaults,
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="import_batch.create",
        resource_type="import_batch",
        resource_id=result["batch"]["id"],
        after={
            "created": len(result["created"]),
            "updated": len(result["updated"]),
            "blocked": len(result["blocked"]),
            "errors": len(result["errors"]),
        },
    )
    return result


@router.get("")
async def list_import_batches(
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    items = [serialize_doc(item) async for item in db.import_batches.find({}).sort("created_at", -1).limit(200)]
    return {"items": items, "total": len(items)}


@router.get("/{batch_id}")
async def get_import_batch(
    batch_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    try:
        batch_oid = object_id(batch_id)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found") from exc
    batch = await db.import_batches.find_one({"_id": batch_oid})
    if not batch:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Import batch not found")
    accounts = [
        serialize_doc(item)
        async for item in db.accounts.find({"metadata.batch_id": batch_id, "metadata.deleted_at": {"$exists": False}}).sort("metadata.created_at", 1)
    ]
    return {"batch": serialize_doc(batch), "accounts": accounts, "total_accounts": len(accounts)}
