from __future__ import annotations

import json
import secrets
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.modules.growth.database import growth_connection
from app.modules.growth.schemas import (
    CampaignCreate,
    CampaignUpdate,
    ChannelCreate,
    ChannelUpdate,
    GrowthSiteUpdate,
    TrackingLinkCreate,
    TrackingLinkUpdate,
)
from app.modules.system.client_sites import get_client_site, list_client_sites


TRACKING_CODE_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
PUBLIC_TRACKING_BASE_URL = "https://aiwelink.cc/r"


class GrowthNotFoundError(LookupError):
    pass


class GrowthConflictError(RuntimeError):
    pass


def generate_tracking_code() -> str:
    return "".join(secrets.choice(TRACKING_CODE_ALPHABET) for _ in range(8))


def _public_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _public_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    return value


def _public_row(row: Any) -> dict[str, Any]:
    result = _public_value(dict(row))
    if result.get("code") and result.get("tracking_link_id"):
        result["public_url"] = f"{PUBLIC_TRACKING_BASE_URL}/{result['code']}"
    return result


def _one(result: Any) -> dict[str, Any] | None:
    row = result.mappings().one_or_none()
    return _public_row(row) if row is not None else None


def _all(result: Any) -> list[dict[str, Any]]:
    return [_public_row(row) for row in result.mappings().all()]


async def list_sites(connection: Any) -> list[dict[str, Any]]:
    result = await connection.execute(text("SELECT * FROM growth.sites ORDER BY created_at, site_id"))
    return _all(result)


async def upsert_site(
    connection: Any,
    payload: GrowthSiteUpdate,
    *,
    client_site: dict[str, Any],
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.sites (
                site_id, site_name, system_type, public_origin, default_landing_path,
                timezone, currency, binding_mode, sync_interval_seconds,
                initial_sync_from, status
            ) VALUES (
                :site_id, :site_name, :system_type, :public_origin, :default_landing_path,
                :timezone, :currency, :binding_mode, :sync_interval_seconds,
                :initial_sync_from, :status
            )
            ON CONFLICT (site_id) DO UPDATE SET
                site_name = EXCLUDED.site_name,
                system_type = EXCLUDED.system_type,
                public_origin = EXCLUDED.public_origin,
                default_landing_path = EXCLUDED.default_landing_path,
                timezone = EXCLUDED.timezone,
                currency = EXCLUDED.currency,
                binding_mode = EXCLUDED.binding_mode,
                sync_interval_seconds = EXCLUDED.sync_interval_seconds,
                initial_sync_from = EXCLUDED.initial_sync_from,
                status = EXCLUDED.status,
                updated_at = NOW()
            RETURNING *
            """
        ),
        {
            "site_id": client_site["id"],
            "site_name": client_site.get("name") or client_site["id"],
            "system_type": client_site["client_type"],
            **payload.model_dump(),
        },
    )
    return _one(result) or {}


async def list_channels(connection: Any) -> list[dict[str, Any]]:
    result = await connection.execute(
        text("SELECT * FROM growth.channels ORDER BY created_at, channel_id")
    )
    return _all(result)


async def create_channel(
    connection: Any,
    payload: ChannelCreate,
    *,
    actor_id: str,
    channel_id: UUID | None = None,
) -> dict[str, Any]:
    selected_id = channel_id or uuid4()
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.channels (
                channel_id, code, name, description, status, created_by, updated_by
            ) VALUES (
                :channel_id, :code, :name, :description, :status, :actor_id, :actor_id
            )
            RETURNING *
            """
        ),
        {"channel_id": selected_id, "actor_id": actor_id, **payload.model_dump()},
    )
    return _one(result) or {}


async def update_channel(
    connection: Any,
    channel_id: UUID,
    payload: ChannelUpdate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        result = await connection.execute(
            text("SELECT * FROM growth.channels WHERE channel_id = :channel_id"),
            {"channel_id": channel_id},
        )
        row = _one(result)
    else:
        assignments = ", ".join(f"{field} = :{field}" for field in updates)
        result = await connection.execute(
            text(
                f"""
                UPDATE growth.channels
                SET {assignments}, updated_by = :actor_id, updated_at = NOW()
                WHERE channel_id = :channel_id
                RETURNING *
                """
            ),
            {"channel_id": channel_id, "actor_id": actor_id, **updates},
        )
        row = _one(result)
    if row is None:
        raise GrowthNotFoundError("channel not found")
    return row


async def list_campaigns(
    connection: Any,
    *,
    site_id: str | None = None,
    channel_id: UUID | None = None,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    parameters: dict[str, Any] = {}
    if site_id:
        conditions.append("campaign.site_id = :site_id")
        parameters["site_id"] = site_id
    if channel_id:
        conditions.append("campaign.channel_id = :channel_id")
        parameters["channel_id"] = channel_id
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    result = await connection.execute(
        text(
            f"""
            SELECT campaign.*, channel.name AS channel_name, site.site_name
            FROM growth.campaigns AS campaign
            JOIN growth.channels AS channel ON channel.channel_id = campaign.channel_id
            JOIN growth.sites AS site ON site.site_id = campaign.site_id
            {where}
            ORDER BY campaign.created_at DESC, campaign.campaign_id
            """
        ),
        parameters,
    )
    return _all(result)


async def create_campaign(
    connection: Any,
    payload: CampaignCreate,
    *,
    actor_id: str,
    campaign_id: UUID | None = None,
) -> dict[str, Any]:
    site_result = await connection.execute(
        text("SELECT site_id FROM growth.sites WHERE site_id = :site_id"),
        {"site_id": payload.site_id},
    )
    if _one(site_result) is None:
        raise GrowthNotFoundError(
            "当前站点尚未接入流量分析，请先在站点接入页保存站点配置"
        )
    selected_id = campaign_id or uuid4()
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.campaigns (
                campaign_id, site_id, channel_id, code, name, description,
                starts_at, ends_at, status, created_by, updated_by
            ) VALUES (
                :campaign_id, :site_id, :channel_id, :code, :name, :description,
                :starts_at, :ends_at, :status, :actor_id, :actor_id
            )
            ON CONFLICT (site_id, code) DO NOTHING
            RETURNING *
            """
        ),
        {"campaign_id": selected_id, "actor_id": actor_id, **payload.model_dump()},
    )
    row = _one(result)
    if row is None:
        raise GrowthConflictError("当前站点下已存在相同活动编码")
    return row


async def update_campaign(
    connection: Any,
    campaign_id: UUID,
    payload: CampaignUpdate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        result = await connection.execute(
            text("SELECT * FROM growth.campaigns WHERE campaign_id = :campaign_id"),
            {"campaign_id": campaign_id},
        )
    else:
        assignments = ", ".join(f"{field} = :{field}" for field in updates)
        result = await connection.execute(
            text(
                f"""
                UPDATE growth.campaigns
                SET {assignments}, updated_by = :actor_id, updated_at = NOW()
                WHERE campaign_id = :campaign_id
                RETURNING *
                """
            ),
            {"campaign_id": campaign_id, "actor_id": actor_id, **updates},
        )
    row = _one(result)
    if row is None:
        raise GrowthNotFoundError("campaign not found")
    return row


async def list_tracking_links(
    connection: Any,
    *,
    site_id: str | None = None,
    campaign_id: UUID | None = None,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    parameters: dict[str, Any] = {}
    if site_id:
        conditions.append("link.site_id = :site_id")
        parameters["site_id"] = site_id
    if campaign_id:
        conditions.append("link.campaign_id = :campaign_id")
        parameters["campaign_id"] = campaign_id
    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    result = await connection.execute(
        text(
            f"""
            SELECT link.*, campaign.name AS campaign_name, campaign.channel_id,
                   channel.name AS channel_name, site.site_name
            FROM growth.tracking_links AS link
            JOIN growth.campaigns AS campaign ON campaign.campaign_id = link.campaign_id
            JOIN growth.channels AS channel ON channel.channel_id = campaign.channel_id
            JOIN growth.sites AS site ON site.site_id = link.site_id
            {where}
            ORDER BY link.created_at DESC, link.tracking_link_id
            """
        ),
        parameters,
    )
    return _all(result)


async def create_tracking_link(
    connection: Any,
    payload: TrackingLinkCreate,
    *,
    actor_id: str,
    tracking_link_id: UUID | None = None,
    code: str | None = None,
) -> dict[str, Any]:
    campaign_result = await connection.execute(
        text(
            """
            SELECT campaign_id
            FROM growth.campaigns
            WHERE campaign_id = :campaign_id AND site_id = :site_id
            """
        ),
        {"campaign_id": payload.campaign_id, "site_id": payload.site_id},
    )
    if _one(campaign_result) is None:
        raise GrowthNotFoundError("campaign does not belong to the selected site")

    selected_id = tracking_link_id or uuid4()
    selected_code = code or generate_tracking_code()
    values = payload.model_dump()
    values["extra_dimensions"] = json.dumps(values["extra_dimensions"], ensure_ascii=False)
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.tracking_links (
                tracking_link_id, site_id, campaign_id, code, source_type, source_name,
                source_url, audience_group, promoter, landing_path, extra_dimensions,
                valid_from, valid_until, status, created_by, updated_by
            ) VALUES (
                :tracking_link_id, :site_id, :campaign_id, :code, :source_type, :source_name,
                :source_url, :audience_group, :promoter, :landing_path,
                CAST(:extra_dimensions AS JSONB), :valid_from, :valid_until, :status,
                :actor_id, :actor_id
            )
            RETURNING *
            """
        ),
        {
            "tracking_link_id": selected_id,
            "code": selected_code,
            "actor_id": actor_id,
            **values,
        },
    )
    return _one(result) or {}


async def update_tracking_link(
    connection: Any,
    tracking_link_id: UUID,
    payload: TrackingLinkUpdate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if "extra_dimensions" in updates:
        updates["extra_dimensions"] = json.dumps(updates["extra_dimensions"] or {}, ensure_ascii=False)
    if not updates:
        result = await connection.execute(
            text("SELECT * FROM growth.tracking_links WHERE tracking_link_id = :tracking_link_id"),
            {"tracking_link_id": tracking_link_id},
        )
    else:
        assignments = ", ".join(
            "extra_dimensions = CAST(:extra_dimensions AS JSONB)"
            if field == "extra_dimensions"
            else f"{field} = :{field}"
            for field in updates
        )
        result = await connection.execute(
            text(
                f"""
                UPDATE growth.tracking_links
                SET {assignments}, updated_by = :actor_id, updated_at = NOW()
                WHERE tracking_link_id = :tracking_link_id
                RETURNING *
                """
            ),
            {"tracking_link_id": tracking_link_id, "actor_id": actor_id, **updates},
        )
    row = _one(result)
    if row is None:
        raise GrowthNotFoundError("tracking link not found")
    return row


async def list_growth_site_configs(mongo_db: Any) -> dict[str, Any]:
    client_result = await list_client_sites(mongo_db)
    async with growth_connection(mongo_db) as connection:
        configured_sites = {site["site_id"]: site for site in await list_sites(connection)}
    items = []
    for client in client_result["items"]:
        configured = configured_sites.get(client["id"])
        items.append(
            {
                "site_id": client["id"],
                "site_name": client.get("name") or client["id"],
                "system_type": client["client_type"],
                "base_url": client.get("base_url") or "",
                "database_configured": bool(client.get("sql_dsn_configured")),
                "configured": configured is not None,
                **(configured or {}),
            }
        )
    return {"items": items, "total": len(items)}


async def update_growth_site_config(
    mongo_db: Any,
    site_id: str,
    payload: GrowthSiteUpdate,
) -> dict[str, Any]:
    client = await get_client_site(mongo_db, site_id)
    if client is None:
        raise LookupError("client site not found")
    async with growth_connection(mongo_db, write=True) as connection:
        return await upsert_site(connection, payload, client_site=client)


async def list_channel_configs(mongo_db: Any) -> dict[str, Any]:
    async with growth_connection(mongo_db) as connection:
        items = await list_channels(connection)
    return {"items": items, "total": len(items)}


async def create_channel_config(mongo_db: Any, payload: ChannelCreate, *, actor_id: str) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        return await create_channel(connection, payload, actor_id=actor_id)


async def update_channel_config(
    mongo_db: Any,
    channel_id: UUID,
    payload: ChannelUpdate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        return await update_channel(connection, channel_id, payload, actor_id=actor_id)


async def list_campaign_configs(
    mongo_db: Any,
    *,
    site_id: str | None = None,
    channel_id: UUID | None = None,
) -> dict[str, Any]:
    async with growth_connection(mongo_db) as connection:
        items = await list_campaigns(connection, site_id=site_id, channel_id=channel_id)
    return {"items": items, "total": len(items)}


async def create_campaign_config(mongo_db: Any, payload: CampaignCreate, *, actor_id: str) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        return await create_campaign(connection, payload, actor_id=actor_id)


async def update_campaign_config(
    mongo_db: Any,
    campaign_id: UUID,
    payload: CampaignUpdate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        return await update_campaign(connection, campaign_id, payload, actor_id=actor_id)


async def list_tracking_link_configs(
    mongo_db: Any,
    *,
    site_id: str | None = None,
    campaign_id: UUID | None = None,
) -> dict[str, Any]:
    async with growth_connection(mongo_db) as connection:
        items = await list_tracking_links(connection, site_id=site_id, campaign_id=campaign_id)
    return {"items": items, "total": len(items)}


async def create_tracking_link_config(
    mongo_db: Any,
    payload: TrackingLinkCreate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    for attempt in range(5):
        try:
            async with growth_connection(mongo_db, write=True) as connection:
                return await create_tracking_link(connection, payload, actor_id=actor_id)
        except IntegrityError:
            if attempt == 4:
                raise
    raise RuntimeError("tracking code generation exhausted")


async def update_tracking_link_config(
    mongo_db: Any,
    tracking_link_id: UUID,
    payload: TrackingLinkUpdate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    async with growth_connection(mongo_db, write=True) as connection:
        return await update_tracking_link(connection, tracking_link_id, payload, actor_id=actor_id)
