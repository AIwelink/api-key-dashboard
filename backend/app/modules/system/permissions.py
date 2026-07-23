from __future__ import annotations

from typing import Any

from fastapi import Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import RolePermissionsUpdate, ViewName
from app.security import get_current_user
from app.utils import now_utc


SETTINGS_ID = "role_permissions"
ROLE_ORDER = ("owner", "admin", "maintainer", "operator", "viewer")
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


def default_role_permissions() -> dict[str, dict[str, Any]]:
    return {
        role: {
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
    if not stored or stored.get("roles") != public["roles"]:
        await db.app_settings.update_one(
            {"_id": SETTINGS_ID},
            {
                "$set": {
                    "roles": public["roles"],
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
    for role, entry in payload.roles.items():
        roles[role] = _normalize_entry(entry.model_dump(), fallback=roles.get(role))
    updates = {
        "roles": roles,
        "updated_at": now_utc(),
        "updated_by": _actor_id(actor),
    }
    await db.app_settings.update_one(
        {"_id": SETTINGS_ID},
        {"$set": updates},
        upsert=True,
    )
    return _public_settings({**current, **updates})


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
        for role in ROLE_ORDER:
            value = stored_roles.get(role)
            if isinstance(value, dict):
                roles[role] = _normalize_entry(value, fallback=roles[role])
    response: dict[str, Any] = {
        "available_views": list(AVAILABLE_VIEWS),
        "roles": roles,
    }
    if settings and settings.get("updated_at") is not None:
        response["updated_at"] = settings.get("updated_at")
    if settings and settings.get("updated_by") is not None:
        response["updated_by"] = settings.get("updated_by")
    return response


def _normalize_entry(value: dict[str, Any], *, fallback: dict[str, Any] | None = None) -> dict[str, Any]:
    fallback = fallback or {"allowed_views": [], "default_view": None}
    raw_allowed = value.get("allowed_views")
    if isinstance(raw_allowed, list):
        allowed_views = _dedupe_valid_views(raw_allowed)
    else:
        allowed_views = _dedupe_valid_views(fallback.get("allowed_views", []))
    preferred_default = value.get("default_view") or fallback.get("default_view")
    default_view = preferred_default if preferred_default in allowed_views else (allowed_views[0] if allowed_views else None)
    return {
        "allowed_views": allowed_views,
        "default_view": default_view,
    }


def _dedupe_valid_views(values: list[Any]) -> list[ViewName]:
    result: list[ViewName] = []
    for value in values:
        if value in AVAILABLE_VIEW_SET and value not in result:
            result.append(value)
    return result


def _actor_id(actor: dict[str, Any]) -> str:
    return str(actor.get("_id") or actor.get("email") or actor.get("id") or "")
