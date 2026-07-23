import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import PasswordResetRequest, UserCreate, UserUpdate
from app.security import hash_password, require_roles
from app.modules.system.audit import write_audit_log
from app.modules.system.permissions import role_exists
from app.utils import now_utc, serialize_doc


router = APIRouter(prefix="/users", tags=["users"])


def public_user(user: dict) -> dict:
    data = serialize_doc(user)
    data.pop("password_hash", None)
    return data


@router.get("")
async def list_users(
    _: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    users = [public_user(user) async for user in db.users.find({}).sort("created_at", -1)]
    return {"items": users, "total": len(users)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    email = payload.email.lower()
    await _require_existing_role(db, payload.role)
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="User already exists")
    now = now_utc()
    password = payload.password or secrets.token_urlsafe(18)
    document = {
        "_id": email,
        "email": email,
        "name": payload.name,
        "role": payload.role,
        "password_hash": hash_password(password),
        "status": "active" if payload.password else "pending_password_reset",
        "must_change_password": True,
        "created_by": actor["_id"],
        "updated_by": actor["_id"],
        "created_at": now,
        "updated_at": now,
    }
    await db.users.insert_one(document)
    if not await role_exists(db, payload.role):
        await db.users.delete_one({"_id": email})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User role does not exist")
    await write_audit_log(db, actor=actor, action="user.create", resource_type="user", resource_id=email)
    result = public_user(document)
    if not payload.password:
        result["temporary_password"] = password
    return result


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    _: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    user = await db.users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return public_user(user)


@router.patch("/{user_id}")
async def update_user(
    user_id: str,
    payload: UserUpdate,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    user = await db.users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    update = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    requested_role = update.get("role")
    if requested_role is not None:
        await _require_existing_role(db, requested_role)
    original_values = {key: user[key] for key in update if key in user}
    missing_original_keys = [key for key in update if key not in user]
    original_metadata = {key: user[key] for key in ("updated_by", "updated_at") if key in user}
    missing_metadata_keys = [key for key in ("updated_by", "updated_at") if key not in user]
    update["updated_by"] = actor["_id"]
    update["updated_at"] = now_utc()
    await db.users.update_one({"_id": user_id}, {"$set": update})
    if requested_role is not None and not await role_exists(db, requested_role):
        rollback_set = {**original_values, **original_metadata}
        rollback_unset = {key: "" for key in (*missing_original_keys, *missing_metadata_keys)}
        rollback: dict[str, dict[str, object]] = {}
        if rollback_set:
            rollback["$set"] = rollback_set
        if rollback_unset:
            rollback["$unset"] = rollback_unset
        await db.users.update_one({"_id": user_id}, rollback)
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User role does not exist")
    await write_audit_log(db, actor=actor, action="user.update", resource_type="user", resource_id=user_id)
    return public_user(await db.users.find_one({"_id": user_id}))


async def _require_existing_role(db: AsyncIOMotorDatabase, role_id: str) -> None:
    if not await role_exists(db, role_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User role does not exist")


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    payload: PasswordResetRequest,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    user = await db.users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    await db.users.update_one(
        {"_id": user_id},
        {
            "$set": {
                "password_hash": hash_password(payload.password),
                "must_change_password": True,
                "status": "active",
                "updated_by": actor["_id"],
                "updated_at": now_utc(),
            }
        },
    )
    await write_audit_log(db, actor=actor, action="user.reset_password", resource_type="user", resource_id=user_id)
    return {"ok": True}


@router.post("/{user_id}/disable")
async def disable_user(
    user_id: str,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    await db.users.update_one({"_id": user_id}, {"$set": {"status": "disabled", "updated_at": now_utc()}})
    await write_audit_log(db, actor=actor, action="user.disable", resource_type="user", resource_id=user_id)
    return {"ok": True}


@router.post("/{user_id}/enable")
async def enable_user(
    user_id: str,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    await db.users.update_one({"_id": user_id}, {"$set": {"status": "active", "updated_at": now_utc()}})
    await write_audit_log(db, actor=actor, action="user.enable", resource_type="user", resource_id=user_id)
    return {"ok": True}
