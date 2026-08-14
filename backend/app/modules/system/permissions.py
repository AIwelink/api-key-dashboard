from __future__ import annotations

import re
from typing import Any

from fastapi import Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.database import db_dependency
from app.schemas import RolePermissionsUpdate, ViewName
from app.security import get_current_user
from app.utils import now_utc


SETTINGS_ID = "role_permissions"
ROLE_ORDER = ("owner", "admin", "maintainer", "operator", "viewer")
ROLE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
ROLE_LABELS = {
    "owner": "owner",
    "admin": "admin",
    "maintainer": "maintainer",
    "operator": "运营",
    "viewer": "viewer",
}
OWNER_REQUIRED_VIEWS = {"system-management", "api-tokens", "users"}
SYSTEM_MANAGEMENT_ROLES = {"owner", "admin"}
AVAILABLE_VIEWS: tuple[ViewName, ...] = (
    "work-plans",
    "upload",
    "todos",
    "push-error-todos",
    "accounts",
    "available-pool",
    "reserve-pool",
    "api-pools",
    "plus-self-produced",
    "traffic-analysis",
    "operations-management",
    "event-records",
    "alert-center",
    "pool-lifecycle",
    "auto-replenishment",
    "client-sites",
    "traffic-analysis-config",
    "agent-analysis",
    "agent-workbench",
    "system-management",
    "api-tokens",
    "presence",
    "users",
    "logs",
)
AVAILABLE_VIEW_SET = set(AVAILABLE_VIEWS)
MANDATORY_ROLE_VIEWS: set[ViewName] = {"work-plans"}

DEFAULT_ROLE_VIEWS: dict[str, list[ViewName]] = {
    "owner": list(AVAILABLE_VIEWS),
    "admin": [view for view in AVAILABLE_VIEWS if view not in {"presence", "api-tokens"}],
    "maintainer": [
        view
        for view in AVAILABLE_VIEWS
        if view not in {"presence", "traffic-analysis", "traffic-analysis-config", "system-management", "api-tokens", "users"}
    ],
    "operator": ["work-plans", "traffic-analysis", "operations-management"],
    "viewer": [
        view
        for view in AVAILABLE_VIEWS
        if view not in {"presence", "traffic-analysis", "traffic-analysis-config", "system-management", "api-tokens", "users"}
    ],
}
DEFAULT_ROLE_DEFAULTS: dict[str, ViewName] = {
    "owner": "api-pools",
    "admin": "api-pools",
    "maintainer": "api-pools",
    "operator": "traffic-analysis",
    "viewer": "api-pools",
}


class RoleAlreadyExistsError(ValueError):
    pass


class RoleNotFoundError(ValueError):
    pass


class BuiltinRoleDeleteError(ValueError):
    pass


class RoleInUseError(ValueError):
    pass


def default_role_permissions() -> dict[str, dict[str, Any]]:
    return {
        role: {
            "label": ROLE_LABELS[role],
            "builtin": True,
            "allowed_views": list(DEFAULT_ROLE_VIEWS[role]),
            "default_view": DEFAULT_ROLE_DEFAULTS[role],
        }
        for role in ROLE_ORDER
    }


async def get_role_permissions_settings(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    return _public_settings(await db.app_settings.find_one({"_id": SETTINGS_ID}))


async def get_user_role_catalog(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    settings = await get_role_permissions_settings(db)
    role_order = [
        role
        for role in settings["role_order"]
        if role in settings["roles"] and not settings["roles"][role].get("deleting")
    ]
    return {
        "role_order": role_order,
        "roles": {
            role: {
                "label": settings["roles"][role]["label"],
                "builtin": settings["roles"][role]["builtin"],
            }
            for role in role_order
        },
    }


async def ensure_role_permissions_settings(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    stored = await db.app_settings.find_one({"_id": SETTINGS_ID})
    public = _public_settings(stored)
    if not stored:
        try:
            await db.app_settings.update_one(
                {"_id": SETTINGS_ID},
                {
                    "$setOnInsert": {
                        "roles": public["roles"],
                        "role_order": public["role_order"],
                        "updated_at": now_utc(),
                        "updated_by": "system",
                    }
                },
                upsert=True,
            )
        except DuplicateKeyError:
            pass
        return await get_role_permissions_settings(db)

    if stored.get("roles") != public["roles"] or stored.get("role_order") != public["role_order"]:
        update_filter: dict[str, Any] = {"_id": SETTINGS_ID}
        for field in ("roles", "role_order"):
            update_filter[field] = stored[field] if field in stored else {"$exists": False}
        result = await db.app_settings.update_one(
            update_filter,
            {
                "$set": {
                    "roles": public["roles"],
                    "role_order": public["role_order"],
                    "updated_at": now_utc(),
                    "updated_by": "system",
                }
            },
        )
        if not result.matched_count:
            return await get_role_permissions_settings(db)
    return public


async def update_role_permissions_settings(
    db: AsyncIOMotorDatabase,
    *,
    payload: RolePermissionsUpdate,
    actor: dict[str, Any],
) -> dict[str, Any]:
    current = await get_role_permissions_settings(db)
    roles = {role: dict(entry) for role, entry in current["roles"].items()}
    field_updates: dict[str, Any] = {
        "updated_at": now_utc(),
        "updated_by": _actor_id(actor),
    }
    update_filter: dict[str, Any] = {"_id": SETTINGS_ID}
    for role, entry in payload.roles.items():
        if role not in roles:
            raise RoleNotFoundError(f"User role '{role}' does not exist")
        normalized = _normalize_entry(role, entry.model_dump(), fallback=roles[role])
        roles[role] = normalized
        field_updates[f"roles.{role}"] = normalized
        update_filter[f"roles.{role}"] = {"$exists": True}
        update_filter[f"roles.{role}.deleting"] = {"$ne": True}
    result = await db.app_settings.update_one(
        update_filter,
        {"$set": field_updates},
    )
    if not result.matched_count:
        raise RoleNotFoundError("One or more user roles no longer exist")
    return await get_role_permissions_settings(db)


async def create_user_role(
    db: AsyncIOMotorDatabase,
    *,
    role_id: str,
    label: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    current = await get_role_permissions_settings(db)
    if role_id in current["roles"]:
        raise RoleAlreadyExistsError(f"User role '{role_id}' already exists")

    normalized_label = label.strip()
    if not normalized_label:
        raise ValueError("User role label must not be empty")
    entry = _normalize_entry(
        role_id,
        {
            "label": normalized_label,
            "builtin": False,
            "allowed_views": [],
            "default_view": None,
        },
    )
    now = now_utc()

    # Create the settings document when bootstrap has not run yet without touching an existing role map.
    await db.app_settings.update_one(
        {"_id": SETTINGS_ID},
        {
            "$setOnInsert": {
                "roles": current["roles"],
                "role_order": current["role_order"],
                "created_at": now,
            },
        },
        upsert=True,
    )
    try:
        result = await db.app_settings.update_one(
            {"_id": SETTINGS_ID, f"roles.{role_id}": {"$exists": False}},
            {
                "$set": {
                    f"roles.{role_id}": entry,
                    "updated_at": now,
                    "updated_by": _actor_id(actor),
                },
                "$push": {"role_order": role_id},
            },
        )
    except DuplicateKeyError as exc:
        raise RoleAlreadyExistsError(f"User role '{role_id}' already exists") from exc
    if not result.modified_count:
        raise RoleAlreadyExistsError(f"User role '{role_id}' already exists")

    return await get_role_permissions_settings(db)


async def delete_user_role(
    db: AsyncIOMotorDatabase,
    *,
    role_id: str,
    actor: dict[str, Any],
) -> dict[str, Any]:
    current = await get_role_permissions_settings(db)
    entry = current["roles"].get(role_id)
    if entry is None:
        raise RoleNotFoundError(f"User role '{role_id}' does not exist")
    if entry["builtin"]:
        raise BuiltinRoleDeleteError(f"Built-in user role '{role_id}' cannot be deleted")

    now = now_utc()
    mark_result = await db.app_settings.update_one(
        {
            "_id": SETTINGS_ID,
            f"roles.{role_id}": {"$exists": True},
            f"roles.{role_id}.deleting": {"$ne": True},
        },
        {
            "$set": {
                f"roles.{role_id}.deleting": True,
                "updated_at": now,
                "updated_by": _actor_id(actor),
            }
        },
    )
    if not mark_result.modified_count:
        if not entry.get("deleting"):
            raise RoleInUseError(f"User role '{role_id}' deletion is already in progress")

    try:
        assigned_user = await db.users.find_one({"role": role_id}, {"_id": 1})
    except Exception:
        await _clear_role_deleting(db, role_id=role_id, actor=actor)
        raise
    if assigned_user:
        await _clear_role_deleting(db, role_id=role_id, actor=actor)
        raise RoleInUseError(f"User role '{role_id}' is assigned to users")

    try:
        result = await db.app_settings.update_one(
            {"_id": SETTINGS_ID, f"roles.{role_id}.deleting": True},
            {
                "$unset": {f"roles.{role_id}": ""},
                "$pull": {"role_order": role_id},
                "$set": {
                    "updated_at": now,
                    "updated_by": _actor_id(actor),
                },
            },
        )
    except Exception:
        await _clear_role_deleting(db, role_id=role_id, actor=actor)
        raise
    if not result.modified_count:
        await _clear_role_deleting(db, role_id=role_id, actor=actor)
        raise RoleNotFoundError(f"User role '{role_id}' does not exist")

    try:
        late_user = await db.users.find_one({"role": role_id}, {"_id": 1})
    except Exception:
        await _restore_deleted_role(db, role_id=role_id, entry=entry, actor=actor)
        raise
    if late_user:
        await _restore_deleted_role(db, role_id=role_id, entry=entry, actor=actor)
        raise RoleInUseError(f"User role '{role_id}' was assigned during deletion")

    return await get_role_permissions_settings(db)


async def role_exists(db: AsyncIOMotorDatabase, role_id: str) -> bool:
    settings = await get_role_permissions_settings(db)
    entry = settings["roles"].get(role_id)
    return bool(entry and not entry.get("deleting"))


async def permissions_for_user(db: AsyncIOMotorDatabase, user: dict[str, Any]) -> dict[str, Any]:
    settings = await get_role_permissions_settings(db)
    role = str(user.get("role") or "viewer")
    entry = settings["roles"].get(role)
    if not entry or entry.get("deleting"):
        entry = settings["roles"]["viewer"]
    return {
        "allowed_views": list(entry["allowed_views"]),
        "default_view": entry["default_view"],
    }


async def user_can_access_view(db: AsyncIOMotorDatabase, user: dict[str, Any], view: ViewName) -> bool:
    entry = await permissions_for_user(db, user)
    return view in entry["allowed_views"]


def require_any_view_permission(*views: ViewName):
    async def dependency(
        user: dict[str, Any] = Depends(get_current_user),
        db: AsyncIOMotorDatabase = Depends(db_dependency),
    ) -> dict[str, Any]:
        entry = await permissions_for_user(db, user)
        if not any(view in entry["allowed_views"] for view in views):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
        return user

    return dependency


def require_view_permission(view: ViewName):
    async def dependency(
        user: dict[str, Any] = Depends(get_current_user),
        db: AsyncIOMotorDatabase = Depends(db_dependency),
    ) -> dict[str, Any]:
        if not await user_can_access_view(db, user, view):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Permission denied")
        return user

    return dependency


def _public_settings(settings: dict[str, Any] | None) -> dict[str, Any]:
    stored_roles = (settings or {}).get("roles")
    roles = default_role_permissions()
    if isinstance(stored_roles, dict):
        for raw_role, value in stored_roles.items():
            role = str(raw_role)
            if not ROLE_ID_PATTERN.fullmatch(role):
                continue
            if isinstance(value, dict):
                roles[role] = _normalize_entry(role, value, fallback=roles.get(role))
    role_order = _normalize_role_order(settings, roles)
    response: dict[str, Any] = {
        "available_views": list(AVAILABLE_VIEWS),
        "role_order": role_order,
        "roles": roles,
    }
    if settings and settings.get("updated_at") is not None:
        response["updated_at"] = settings.get("updated_at")
    if settings and settings.get("updated_by") is not None:
        response["updated_by"] = settings.get("updated_by")
    return response


def _normalize_entry(role: str, value: dict[str, Any], *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = fallback or {
        "label": ROLE_LABELS.get(role, role),
        "builtin": role in ROLE_ORDER,
        "allowed_views": [],
        "default_view": None,
    }
    raw_allowed = value.get("allowed_views")
    if isinstance(raw_allowed, list):
        allowed_views = _dedupe_valid_views(raw_allowed)
    else:
        allowed_views = _dedupe_valid_views(fallback.get("allowed_views", []))
    if "pool-lifecycle" in allowed_views and "auto-replenishment" not in allowed_views:
        allowed_views.append("auto-replenishment")
    allowed_set = set(allowed_views) | MANDATORY_ROLE_VIEWS
    allowed_views = [view for view in AVAILABLE_VIEWS if view in allowed_set]
    if role == "owner":
        allowed_views = [view for view in AVAILABLE_VIEWS if view in set(allowed_views) | OWNER_REQUIRED_VIEWS]
    else:
        allowed_views = [view for view in allowed_views if view != "api-tokens"]
    if role in SYSTEM_MANAGEMENT_ROLES:
        allowed_views = [view for view in AVAILABLE_VIEWS if view in set(allowed_views) | {"system-management"}]
    else:
        allowed_views = [view for view in allowed_views if view != "system-management"]
    preferred_default = value.get("default_view") or fallback.get("default_view")
    if preferred_default == "api-tokens":
        preferred_default = "system-management"
    default_view = preferred_default if preferred_default in allowed_views else (allowed_views[0] if allowed_views else None)
    raw_label = value.get("label")
    label = str(raw_label).strip() if raw_label is not None else str(fallback.get("label") or role).strip()
    return {
        "label": label or role,
        "builtin": role in ROLE_ORDER,
        "deleting": bool(value.get("deleting", fallback.get("deleting", False))),
        "allowed_views": allowed_views,
        "default_view": default_view,
    }


def _normalize_role_order(settings: dict[str, Any] | None, roles: dict[str, dict[str, Any]]) -> list[str]:
    stored_order = (settings or {}).get("role_order")
    order = [str(role) for role in stored_order if str(role) in roles] if isinstance(stored_order, list) else []
    for role in (*ROLE_ORDER, *roles):
        if role in roles and role not in order:
            order.append(role)
    return order


def _dedupe_valid_views(values: list[Any]) -> list[ViewName]:
    result: list[ViewName] = []
    for value in values:
        if value in AVAILABLE_VIEW_SET and value not in result:
            result.append(value)
    return result


def _actor_id(actor: dict[str, Any]) -> str:
    return str(actor.get("_id") or actor.get("email") or actor.get("id") or "")


async def _clear_role_deleting(
    db: AsyncIOMotorDatabase,
    *,
    role_id: str,
    actor: dict[str, Any],
) -> None:
    await db.app_settings.update_one(
        {"_id": SETTINGS_ID, f"roles.{role_id}.deleting": True},
        {
            "$unset": {f"roles.{role_id}.deleting": ""},
            "$set": {
                "updated_at": now_utc(),
                "updated_by": _actor_id(actor),
            },
        },
    )


async def _restore_deleted_role(
    db: AsyncIOMotorDatabase,
    *,
    role_id: str,
    entry: dict[str, Any],
    actor: dict[str, Any],
) -> None:
    restored_entry = {**entry, "deleting": False}
    await db.app_settings.update_one(
        {"_id": SETTINGS_ID, f"roles.{role_id}": {"$exists": False}},
        {
            "$set": {
                f"roles.{role_id}": restored_entry,
                "updated_at": now_utc(),
                "updated_by": _actor_id(actor),
            },
            "$addToSet": {"role_order": role_id},
        },
    )
