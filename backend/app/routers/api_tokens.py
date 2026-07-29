from fastapi import APIRouter, Depends, HTTPException, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.modules.system.permissions import require_view_permission
from app.schemas import ApiTokenCreate
from app.modules.system.api_tokens import create_api_token, list_api_tokens, revoke_api_token
from app.modules.system.audit import write_audit_log


router = APIRouter(prefix="/api-tokens", tags=["api-tokens"])


@router.get("")
async def get_api_tokens(
    _: dict = Depends(require_view_permission("api-tokens")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_api_tokens(db)


@router.post("", status_code=status.HTTP_201_CREATED)
async def post_api_token(
    payload: ApiTokenCreate,
    actor: dict = Depends(require_view_permission("api-tokens")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    created = await create_api_token(db, payload=payload, actor=actor)
    await write_audit_log(
        db,
        actor=actor,
        action="api_token.create",
        resource_type="api_token",
        resource_id=created["id"],
        after={key: value for key, value in created.items() if key != "token"},
    )
    return created


@router.post("/{token_id}/revoke")
async def post_revoke_api_token(
    token_id: str,
    actor: dict = Depends(require_view_permission("api-tokens")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict[str, bool]:
    revoked = await revoke_api_token(db, token_id=token_id, actor=actor)
    if not revoked:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="API token not found")
    await write_audit_log(
        db,
        actor=actor,
        action="api_token.revoke",
        resource_type="api_token",
        resource_id=token_id,
    )
    return {"ok": True}
