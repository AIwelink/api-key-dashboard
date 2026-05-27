from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import SyncRunRequest
from app.security import require_roles
from app.services.accounts import get_account_or_404
from app.services.audit import write_audit_log
from app.services.sub2api import refresh_account_observation
from app.utils import now_utc, object_id, serialize_doc


router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/preview")
async def preview_sync(
    payload: SyncRunRequest,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    query = {"metadata.deleted_at": {"$exists": False}}
    if payload.account_ids:
        query["_id"] = {"$in": [object_id(item) for item in payload.account_ids]}
    count = await db.accounts.count_documents(query)
    return {"dry_run": True, "summary": {"will_check": count}, "items": []}


@router.post("/run")
async def run_sync(
    payload: SyncRunRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    query = {"metadata.deleted_at": {"$exists": False}}
    if payload.account_ids:
        query["_id"] = {"$in": [object_id(item) for item in payload.account_ids]}

    job = {
        "type": "manual",
        "scope": "selection" if payload.account_ids else "all",
        "account_ids": payload.account_ids or [],
        "status": "running",
        "dry_run": payload.dry_run,
        "summary": {"checked": 0, "failed": 0},
        "started_at": now_utc(),
        "created_by": actor["_id"],
        "created_at": now_utc(),
    }
    result = await db.sync_jobs.insert_one(job)

    checked = 0
    async for account in db.accounts.find(query):
        if not payload.dry_run:
            await refresh_account_observation(db, account)
        checked += 1

    await db.sync_jobs.update_one(
        {"_id": result.inserted_id},
        {
            "$set": {
                "status": "succeeded",
                "summary": {"checked": checked, "failed": 0},
                "finished_at": now_utc(),
            }
        },
    )
    await write_audit_log(
        db,
        actor=actor,
        action="sync.run",
        resource_type="sync_job",
        resource_id=str(result.inserted_id),
        after={"checked": checked, "dry_run": payload.dry_run},
    )
    job = await db.sync_jobs.find_one({"_id": result.inserted_id})
    return serialize_doc(job)


@router.get("/jobs")
async def list_jobs(
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    items = [serialize_doc(item) async for item in db.sync_jobs.find({}).sort("created_at", -1).limit(100)]
    return {"items": items, "total": len(items)}


@router.get("/jobs/{job_id}")
async def get_job(
    job_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    job = await db.sync_jobs.find_one({"_id": object_id(job_id)})
    return serialize_doc(job) if job else {}


@router.post("/accounts/{account_id}")
async def sync_one_account(
    account_id: str,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    account = await get_account_or_404(db, account_id)
    updated = await refresh_account_observation(db, account)
    await write_audit_log(db, actor=actor, action="sync.account", resource_type="account", resource_id=account_id)
    return serialize_doc(updated)

