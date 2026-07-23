from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import AgentLlmSettingsUpdate, GrowthDatabaseSettingsUpdate, RolePermissionsUpdate, UserRoleCreate
from app.modules.agent.llm_client import AgentLlmConfigError, test_agent_llm_connection
from app.modules.agent.settings import (
    AgentLlmSettingsValidationError,
    get_agent_llm_settings,
    get_agent_llm_settings_private,
    update_agent_llm_settings,
    update_agent_llm_test_result,
)
from app.modules.system.audit import write_audit_log
from app.modules.system.growth_database_settings import (
    GrowthDatabaseOperationError,
    get_growth_database_settings,
    get_growth_schema_status,
    initialize_growth_database,
    run_growth_database_test,
    update_growth_database_settings,
)
from app.modules.system.permissions import (
    BuiltinRoleDeleteError,
    RoleAlreadyExistsError,
    RoleInUseError,
    RoleNotFoundError,
    create_user_role,
    delete_user_role,
    get_role_permissions_settings,
    get_user_role_catalog,
    require_view_permission,
    update_role_permissions_settings,
)


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/growth-database")
async def get_growth_database_settings_route(
    _: dict = Depends(require_view_permission("traffic-analysis-config")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_growth_database_settings(db)


@router.put("/growth-database")
async def put_growth_database_settings(
    payload: GrowthDatabaseSettingsUpdate,
    actor: dict = Depends(require_view_permission("traffic-analysis-config")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    before = await get_growth_database_settings(db)
    try:
        updated = await update_growth_database_settings(
            db,
            sql_dsn=payload.sql_dsn,
            actor=actor,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="settings.growth_database.update",
        resource_type="setting",
        resource_id="growth_database",
        before=before,
        after=updated,
    )
    return updated


@router.post("/growth-database/test")
async def post_growth_database_test(
    actor: dict = Depends(require_view_permission("traffic-analysis-config")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    try:
        result = await run_growth_database_test(db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="settings.growth_database.test",
        resource_type="setting",
        resource_id="growth_database",
        after={
            "ok": result.get("ok"),
            "database_type": result.get("database_type"),
            "database_endpoint": result.get("database_endpoint"),
            "latency_ms": result.get("latency_ms"),
            "server_version": result.get("server_version"),
            "error": result.get("error"),
            "tested_at": result.get("tested_at"),
        },
    )
    return result


@router.get("/growth-database/schema")
async def get_growth_database_schema(
    actor: dict = Depends(require_view_permission("traffic-analysis-config")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    del actor
    try:
        return await get_growth_schema_status(db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except GrowthDatabaseOperationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc


@router.post("/growth-database/initialize")
async def post_growth_database_initialize(
    actor: dict = Depends(require_view_permission("traffic-analysis-config")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    try:
        result = await initialize_growth_database(db, actor=actor)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except GrowthDatabaseOperationError as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="settings.growth_database.initialize",
        resource_type="setting",
        resource_id="growth_database",
        after=result,
    )
    return result


@router.get("/sync-policy")
async def get_sync_policy(
    _: dict = Depends(require_view_permission("system-management")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    policy = await db.app_settings.find_one({"_id": "sync_policy"})
    if not policy:
        return {"auto_sync": False, "interval_minutes": 30, "auto_pause_on_expired": True}
    policy.pop("_id", None)
    return policy


@router.patch("/sync-policy")
async def update_sync_policy(
    payload: dict,
    actor: dict = Depends(require_view_permission("system-management")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    await db.app_settings.update_one({"_id": "sync_policy"}, {"$set": payload}, upsert=True)
    await write_audit_log(db, actor=actor, action="settings.sync_policy.update", resource_type="setting", resource_id="sync_policy")
    return await get_sync_policy(actor, db)


@router.get("/role-permissions")
async def get_role_permissions_settings_route(
    _: dict = Depends(require_view_permission("system-management")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_role_permissions_settings(db)


@router.get("/user-roles")
async def get_user_roles(
    _: dict = Depends(require_view_permission("users")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_user_role_catalog(db)


@router.put("/role-permissions")
async def put_role_permissions_settings(
    payload: RolePermissionsUpdate,
    actor: dict = Depends(require_view_permission("system-management")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    before = await get_role_permissions_settings(db)
    try:
        updated = await update_role_permissions_settings(db, payload=payload, actor=actor)
    except RoleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="settings.role_permissions.update",
        resource_type="setting",
        resource_id="role_permissions",
        before=before,
        after=updated,
    )
    return updated


@router.post("/role-permissions/roles", status_code=status.HTTP_201_CREATED)
async def post_user_role(
    payload: UserRoleCreate,
    actor: dict = Depends(require_view_permission("system-management")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    before = await get_role_permissions_settings(db)
    try:
        updated = await create_user_role(db, role_id=payload.id, label=payload.label, actor=actor)
    except RoleAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="settings.role.create",
        resource_type="role",
        resource_id=payload.id,
        before=before,
        after=updated,
    )
    return updated


@router.delete("/role-permissions/roles/{role_id}")
async def delete_user_role_route(
    role_id: str,
    actor: dict = Depends(require_view_permission("system-management")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    before = await get_role_permissions_settings(db)
    try:
        updated = await delete_user_role(db, role_id=role_id, actor=actor)
    except RoleInUseError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except BuiltinRoleDeleteError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except RoleNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="settings.role.delete",
        resource_type="role",
        resource_id=role_id,
        before=before,
        after=updated,
    )
    return updated


@router.get("/agent-llm")
async def get_agent_llm_settings_route(
    _: dict = Depends(require_view_permission("system-management")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await get_agent_llm_settings(db)


@router.put("/agent-llm")
async def put_agent_llm_settings(
    payload: AgentLlmSettingsUpdate,
    actor: dict = Depends(require_view_permission("system-management")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    before = await get_agent_llm_settings(db)
    try:
        updated = await update_agent_llm_settings(db, payload=payload, actor=actor)
    except AgentLlmSettingsValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    await write_audit_log(
        db,
        actor=actor,
        action="settings.agent_llm.update",
        resource_type="setting",
        resource_id="agent_llm",
        before=before,
        after=updated,
    )
    return updated


@router.post("/agent-llm/test")
async def post_agent_llm_test(
    actor: dict = Depends(require_view_permission("system-management")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    settings = await get_agent_llm_settings_private(db)
    try:
        result = await test_agent_llm_connection(settings)
        public_settings = await update_agent_llm_test_result(db, ok=True, message=result.get("message") or "Agent LLM connection is ready")
        response = {**result, "settings": public_settings}
        await write_audit_log(
            db,
            actor=actor,
            action="settings.agent_llm.test",
            resource_type="setting",
            resource_id="agent_llm",
            after={"ok": True, "message": response.get("message"), "model": response.get("model")},
        )
        return response
    except AgentLlmConfigError as exc:
        message = str(exc) or exc.__class__.__name__
        await update_agent_llm_test_result(db, ok=False, message=message)
        await write_audit_log(
            db,
            actor=actor,
            action="settings.agent_llm.test",
            resource_type="setting",
            resource_id="agent_llm",
            after={"ok": False, "message": message},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        ) from exc
    except Exception as exc:  # noqa: BLE001 - test failures are returned to the settings UI.
        message = str(exc) or exc.__class__.__name__
        await update_agent_llm_test_result(db, ok=False, message=message)
        await write_audit_log(
            db,
            actor=actor,
            action="settings.agent_llm.test",
            resource_type="setting",
            resource_id="agent_llm",
            after={"ok": False, "message": message},
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=message,
        ) from exc
