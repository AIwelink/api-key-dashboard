import secrets

from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import PasswordResetRequest, UserCreate, UserUpdate
from app.security import hash_password
from app.modules.system.audit import write_audit_log
from app.modules.system.permissions import require_view_permission, role_exists
from app.modules.system.user_projection import public_user
from app.utils import now_utc


router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
async def list_users(
    _: dict = Depends(require_view_permission("users")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    users = [public_user(user) async for user in db.users.find({}).sort("created_at", -1)]
    users.sort(key=lambda user: user.get("authorization_status") != "pending")
    return {"items": users, "total": len(users)}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreate,
    actor: dict = Depends(require_view_permission("users")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    email = payload.email.lower()
    _require_owner_for_roles(actor, payload.role)
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
        await db.users.delete_one({"_id": email, "role": payload.role})
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User role does not exist")
    await write_audit_log(db, actor=actor, action="user.create", resource_type="user", resource_id=email)
    result = public_user(document)
    if not payload.password:
        result["temporary_password"] = password
    return result


@router.get("/{user_id}")
async def get_user(
    user_id: str,
    _: dict = Depends(require_view_permission("users")),
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
    actor: dict = Depends(require_view_permission("users")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    user = await db.users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    update = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    requested_role = update.pop("role", None)
    activating_authorization = user.get("authorization_status") == "pending" and requested_role is not None
    _require_owner_for_roles(actor, user.get("role"), requested_role)
    if requested_role is not None:
        await _require_existing_role(db, requested_role)
        role_update = {
            "role": requested_role,
            "updated_by": actor["_id"],
            "updated_at": now_utc(),
        }
        if activating_authorization:
            role_update["authorization_status"] = "active"
        result = await db.users.update_one(_user_write_filter(user_id, actor), {"$set": role_update})
        _require_matched_user_write(result, actor)
        if not await role_exists(db, requested_role):
            rollback_set = {"role": user.get("role") or "viewer"}
            if activating_authorization:
                rollback_set["authorization_status"] = "pending"
            rollback_unset: dict[str, str] = {}
            for key in ("updated_by", "updated_at"):
                if key in user:
                    rollback_set[key] = user[key]
                else:
                    rollback_unset[key] = ""
            rollback: dict[str, dict[str, object]] = {"$set": rollback_set}
            if rollback_unset:
                rollback["$unset"] = rollback_unset
            rollback_filter = {"_id": user_id, "role": requested_role}
            if activating_authorization:
                rollback_filter["authorization_status"] = "active"
            await db.users.update_one(rollback_filter, rollback)
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User role does not exist")
    if update:
        update["updated_by"] = actor["_id"]
        update["updated_at"] = now_utc()
        result = await db.users.update_one(_user_write_filter(user_id, actor), {"$set": update})
        _require_matched_user_write(result, actor)
    audit_action = "user.authorization_activated" if activating_authorization else "user.update"
    await write_audit_log(db, actor=actor, action=audit_action, resource_type="user", resource_id=user_id)
    return public_user(await db.users.find_one({"_id": user_id}))


async def _require_existing_role(db: AsyncIOMotorDatabase, role_id: str) -> None:
    if not await role_exists(db, role_id):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User role does not exist")


def _require_owner_for_roles(actor: dict, *roles: object) -> None:
    if "owner" in roles and actor.get("role") != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owners can manage owner accounts")


def _user_write_filter(user_id: str, actor: dict) -> dict:
    query: dict = {"_id": user_id}
    if actor.get("role") != "owner":
        query["role"] = {"$ne": "owner"}
    return query


def _require_matched_user_write(result: object, actor: dict) -> None:
    if getattr(result, "matched_count", 1) != 0:
        return
    if actor.get("role") != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Only owners can manage owner accounts")
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")


@router.post("/{user_id}/reset-password")
async def reset_password(
    user_id: str,
    payload: PasswordResetRequest,
    actor: dict = Depends(require_view_permission("users")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    user = await db.users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.get("email_is_placeholder"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="飞书用户没有本地密码，请使用飞书登录",
        )
    _require_owner_for_roles(actor, user.get("role"))
    result = await db.users.update_one(
        _user_write_filter(user_id, actor),
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
    _require_matched_user_write(result, actor)
    await write_audit_log(db, actor=actor, action="user.reset_password", resource_type="user", resource_id=user_id)
    return {"ok": True}


@router.post("/{user_id}/disable")
async def disable_user(
    user_id: str,
    actor: dict = Depends(require_view_permission("users")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    user = await db.users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _require_owner_for_roles(actor, user.get("role"))
    result = await db.users.update_one(
        _user_write_filter(user_id, actor),
        {"$set": {"status": "disabled", "updated_at": now_utc()}},
    )
    _require_matched_user_write(result, actor)
    await write_audit_log(db, actor=actor, action="user.disable", resource_type="user", resource_id=user_id)
    return {"ok": True}


@router.post("/{user_id}/enable")
async def enable_user(
    user_id: str,
    actor: dict = Depends(require_view_permission("users")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    user = await db.users.find_one({"_id": user_id})
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    _require_owner_for_roles(actor, user.get("role"))
    result = await db.users.update_one(
        _user_write_filter(user_id, actor),
        {"$set": {"status": "active", "updated_at": now_utc()}},
    )
    _require_matched_user_write(result, actor)
    await write_audit_log(db, actor=actor, action="user.enable", resource_type="user", resource_id=user_id)
    return {"ok": True}
