from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import ChangePasswordRequest, LoginRequest, LoginResponse
from app.modules.system.permissions import permissions_for_user
from app.modules.operations.site_permissions import normalize_operations_site_ids
from app.security import create_access_token, get_current_user, hash_password, verify_password
from app.modules.system.audit import write_audit_log
from app.utils import now_utc, serialize_doc


router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(payload: LoginRequest, db: AsyncIOMotorDatabase = Depends(db_dependency)) -> LoginResponse:
    user = await db.users.find_one({"email": payload.email.lower()})
    if user is None or user.get("status") == "disabled":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")
    if not verify_password(payload.password, user.get("password_hash", "")):
        await write_audit_log(
            db,
            actor=None,
            action="auth.login_failed",
            resource_type="user",
            resource_id=user.get("_id"),
            after={"email": payload.email.lower()},
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    await db.users.update_one(
        {"_id": user["_id"]},
        {"$set": {"last_login_at": now_utc(), "updated_at": now_utc()}},
    )
    token = create_access_token(subject=user["_id"], role=user["role"])
    user = await db.users.find_one({"_id": user["_id"]})
    safe_user = await user_with_permissions(db, user)
    return LoginResponse(access_token=token, user=safe_user)


@router.post("/logout")
async def logout() -> dict[str, bool]:
    return {"ok": True}


@router.get("/me")
async def me(
    user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await user_with_permissions(db, user)


async def user_with_permissions(db: AsyncIOMotorDatabase, user: dict) -> dict:
    safe_user = serialize_doc(user)
    safe_user.pop("password_hash", None)
    safe_user["operations_site_ids"] = normalize_operations_site_ids(user.get("operations_site_ids"))
    safe_user["permissions"] = await permissions_for_user(db, user)
    return safe_user


@router.post("/change-password")
async def change_password(
    payload: ChangePasswordRequest,
    user: dict = Depends(get_current_user),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    if not verify_password(payload.current_password, user.get("password_hash", "")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Current password is incorrect")
    await db.users.update_one(
        {"_id": user["_id"]},
        {
            "$set": {
                "password_hash": hash_password(payload.new_password),
                "must_change_password": False,
                "updated_at": now_utc(),
            }
        },
    )
    await write_audit_log(
        db,
        actor=user,
        action="auth.change_password",
        resource_type="user",
        resource_id=user["_id"],
    )
    return {"ok": True}
