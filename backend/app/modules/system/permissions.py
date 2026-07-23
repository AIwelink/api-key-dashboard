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
OWNER_REQUIRED_VIEWS = {"api-tokens", "users"}
AVAILABLE_VIEWS: tuple[ViewName, ...] = (
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
    "client-sites",
    "traffic-analysis-config",
    "agent-analysis",
    "agent-workbench",
    "api-tokens",
    "presence",
    "users",
    "logs",
)
AVAILABLE_VIEW_SET = set(AVAILABLE_VIEWS)

DEFAULT_ROLE_VIEWS: dict[str, list[ViewName]] = {
    "owner": list(AVAILABLE_VIEWS),
    "admin": [view for view in AVAILABLE_VIEWS if view != "presence"],
    "maintainer": [
        view for view in AVAILABLE_VIEWS if view not in {"presence", "traffic-analysis", "traffic-analysis-config"}
    ],
    "operator": ["traffic-analysis", "operations-management"],
    "viewer": [
        view for view in AVAILABLE_VIEWS if view not in {"presence", "traffic-analysis", "traffic-analysis-config"}
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


async def ensure_role_permissions_settings(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    stored = await db.app_settings.find_one({"_id": SETTINGS_ID})
    public = _public_settings(stored)
    if not stored or stored.get("roles") != public["roles"] or stored.get("role_order") != public["role_order"]:
        await db.app_settings.update_one(
            {"_id": SETTINGS_ID},
            {
                "$set": {
                    "roles": public["roles"],
                    "role_order": public["role_order"],
                    "updated_at": now_utc(),
                    "updated_by": "system",
                }
            },
            upsert=True,
        )
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
    for role, entry in payload.roles.items():
        if role not in roles:
            raise RoleNotFoundError(f"User role '{role}' does not exist")
        normalized = _normalize_entry(role, entry.model_dump(), fallback=roles[role])
        roles[role] = normalized
        field_updates[f"roles.{role}"] = normalized
    await db.app_settings.update_one(
        {"_id": SETTINGS_ID},
        {"$set": field_updates},
        upsert=True,
    )
    return _public_settings(
        {
            **current,
            "roles": roles,
            "updated_at": field_updates["updated_at"],
            "updated_by": field_updates["updated_by"],
        }
    )


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

    roles = {role: dict(value) for role, value in current["roles"].items()}
    roles[role_id] = entry
    return _public_settings(
        {
            **current,
            "roles": roles,
            "role_order": [*current["role_order"], role_id],
            "updated_at": now,
            "updated_by": _actor_id(actor),
        }
    )


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
    if await db.users.find_one({"role": role_id}, {"_id": 1}):
        raise RoleInUseError(f"User role '{role_id}' is assigned to users")

    now = now_utc()
    result = await db.app_settings.update_one(
        {"_id": SETTINGS_ID, f"roles.{role_id}": {"$exists": True}},
        {
            "$unset": {f"roles.{role_id}": ""},
            "$pull": {"role_order": role_id},
            "$set": {
                "updated_at": now,
                "updated_by": _actor_id(actor),
            },
        },
    )
    if not result.modified_count:
        raise RoleNotFoundError(f"User role '{role_id}' does not exist")

    roles = {role: dict(value) for role, value in current["roles"].items() if role != role_id}
    return _public_settings(
        {
            **current,
            "roles": roles,
            "role_order": [role for role in current["role_order"] if role != role_id],
            "updated_at": now,
            "updated_by": _actor_id(actor),
        }
    )


async def role_exists(db: AsyncIOMotorDatabase, role_id: str) -> bool:
    settings = await get_role_permissions_settings(db)
    return role_id in settings["roles"]


async def permissions_for_user(db: AsyncIOMotorDatabase, user: dict[str, Any]) -> dict[str, Any]:
    settings = await get_role_permissions_settings(db)
    role = str(user.get("role") or "viewer")
    entry = settings["roles"].get(role) or settings["roles"]["viewer"]
    return {
        "allowed_views": list(entry["allowed_views"]),
        "default_view": entry["default_view"],
    }


async def user_can_access_view(db: AsyncIOMotorDatabase, user: dict[str, Any], view: ViewName) -> bool:
    entry = await permissions_for_user(db, user)
    return view in entry["allowed_views"]


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
    if role == "owner":
        allowed_views = [view for view in AVAILABLE_VIEWS if view in set(allowed_views) | OWNER_REQUIRED_VIEWS]
    else:
        allowed_views = [view for view in allowed_views if view != "api-tokens"]
    preferred_default = value.get("default_view") or fallback.get("default_view")
    default_view = preferred_default if preferred_default in allowed_views else (allowed_views[0] if allowed_views else None)
    raw_label = value.get("label")
    label = str(raw_label).strip() if raw_label is not None else str(fallback.get("label") or role).strip()
    return {
        "label": label or role,
        "builtin": role in ROLE_ORDER,
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
