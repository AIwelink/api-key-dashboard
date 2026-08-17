from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any, Callable
from uuid import UUID, uuid4

from app.modules.operations import repository as operations_repository
from app.modules.operations.cache import operations_response_cache
from app.modules.operations.sync import OPERATIONS_AGGREGATE_HISTORY_START
from app.modules.risk import repository
from app.modules.risk.adapters.sub2api import (
    ApiKeyState,
    EnforcementResult,
    SourceAccountState,
    Sub2ApiRiskAdapter,
)
from app.modules.risk.coordinator import SITE_ID, create_source_engine, risk_growth_session
from app.modules.system.client_sites import get_client_site


async def set_false_positive(
    mongo_db: Any,
    *,
    risk_account_id: UUID,
    actor_id: str,
    actor_name: str,
    reason: str,
    growth_session_factory: Callable[..., Any] = risk_growth_session,
) -> dict[str, Any]:
    normalized_reason = _reason(reason)
    async with growth_session_factory(mongo_db) as growth:
        async with growth.begin():
            account = await repository.get_account(growth, risk_account_id=risk_account_id)
            if account.get("risk_status") == "banned":
                raise ValueError("release the banned account before marking it as a false positive")
            updated = await repository.set_manual_override(
                growth,
                risk_account_id=risk_account_id,
                active=True,
                actor_id=actor_id,
                reason=normalized_reason,
                risk_status="cleared",
            )
            await repository.append_event(
                growth,
                risk_event_id=uuid4(),
                idempotency_key=f"manual-override-set:{risk_account_id}:{uuid4()}",
                risk_account_id=risk_account_id,
                site_id=SITE_ID,
                external_user_id=str(account.get("external_user_id") or ""),
                email=str(account.get("email") or ""),
                event_type="manual_override_set",
                decision_reason=normalized_reason,
                actor_id=actor_id,
                actor_name=actor_name,
                created_at=datetime.now(UTC),
            )
    return updated


async def remove_manual_override(
    mongo_db: Any,
    *,
    risk_account_id: UUID,
    actor_id: str,
    actor_name: str,
    reason: str,
    growth_session_factory: Callable[..., Any] = risk_growth_session,
) -> dict[str, Any]:
    normalized_reason = _reason(reason)
    async with growth_session_factory(mongo_db) as growth:
        async with growth.begin():
            account = await repository.get_account(growth, risk_account_id=risk_account_id)
            target_status = "banned" if account.get("risk_status") == "banned" else "high_risk"
            updated = await repository.set_manual_override(
                growth,
                risk_account_id=risk_account_id,
                active=False,
                actor_id=actor_id,
                reason=normalized_reason,
                risk_status=target_status,
            )
            await repository.append_event(
                growth,
                risk_event_id=uuid4(),
                idempotency_key=f"manual-override-removed:{risk_account_id}:{uuid4()}",
                risk_account_id=risk_account_id,
                site_id=SITE_ID,
                external_user_id=str(account.get("external_user_id") or ""),
                email=str(account.get("email") or ""),
                event_type="manual_override_removed",
                decision_reason=normalized_reason,
                actor_id=actor_id,
                actor_name=actor_name,
                created_at=datetime.now(UTC),
            )
    return updated


async def manual_release(
    mongo_db: Any,
    *,
    risk_account_id: UUID,
    actor_id: str,
    actor_name: str,
    reason: str,
    now: datetime | None = None,
    site_loader: Callable[..., Any] = get_client_site,
    growth_session_factory: Callable[..., Any] = risk_growth_session,
    source_engine_factory: Callable[[dict[str, Any]], Any] = create_source_engine,
    adapter_factory: Callable[[], Any] = Sub2ApiRiskAdapter,
) -> dict[str, Any]:
    changed_at = now or datetime.now(UTC)
    normalized_reason = _reason(reason)
    async with growth_session_factory(mongo_db) as growth:
        acquired = await repository.acquire_cycle_lock(growth, site_id=SITE_ID)
        await growth.commit()
        if not acquired:
            raise RuntimeError("risk control is busy; retry the release")
        try:
            async with growth.begin():
                account = await repository.get_account(growth, risk_account_id=risk_account_id)
                if account.get("risk_status") != "banned":
                    raise ValueError("only a banned account can be released")
                ban_action = await repository.get_latest_succeeded_ban_action(
                    growth,
                    risk_account_id=risk_account_id,
                )
            before = _source_state_from_action(account, ban_action)
            enforced = _enforced_state_from_action(ban_action)
            site = await _load_site(mongo_db, site_loader)
            source_engine = source_engine_factory(site)
            adapter = adapter_factory()
            release_action_id = uuid4()
            try:
                async with growth.begin():
                    await repository.create_action(
                        growth,
                        risk_action_id=release_action_id,
                        idempotency_key=f"manual-release:{risk_account_id}:{release_action_id}",
                        risk_account_id=risk_account_id,
                        site_id=SITE_ID,
                        external_user_id=before.external_user_id,
                        email=before.email,
                        action_type="manual_release",
                        decision_reason=normalized_reason,
                        matched_email_rules=[],
                        shared_ip_evidence=[],
                        source_user_status_before=before.user_status,
                        source_user_updated_at_before=before.user_updated_at,
                        source_api_key_states_before=_key_payload(before.api_keys),
                        requested_by=actor_id,
                        requested_at=changed_at,
                    )
                try:
                    async with source_engine.begin() as source_write:
                        release_result = await adapter.release_account(
                            source_write,
                            before=before,
                            enforced=enforced,
                            changed_at=changed_at,
                        )
                except Exception as exc:
                    async with growth.begin():
                        await repository.complete_action(
                            growth,
                            risk_action_id=release_action_id,
                            status="failed",
                            completed_at=changed_at,
                            error_code=type(exc).__name__,
                            error_message=str(exc),
                        )
                    raise

                details = {
                    "user_restored": release_result.user_restored,
                    "restored_key_ids": list(release_result.restored_key_ids),
                    "conflicted_key_ids": list(release_result.conflicted_key_ids),
                    "partial": release_result.partial,
                }
                async with growth.begin():
                    await repository.complete_action(
                        growth,
                        risk_action_id=release_action_id,
                        status="succeeded",
                        completed_at=changed_at,
                        result_details=details,
                    )
                    updated = await repository.set_manual_override(
                        growth,
                        risk_account_id=risk_account_id,
                        active=True,
                        actor_id=actor_id,
                        reason=normalized_reason,
                        risk_status="released",
                    )
                    await repository.set_stats_exclusion(
                        growth,
                        site_id=SITE_ID,
                        external_user_id=before.external_user_id,
                        risk_account_id=risk_account_id,
                        excluded=False,
                        actor_id=actor_id,
                    )
                    await repository.append_event(
                        growth,
                        risk_event_id=uuid4(),
                        idempotency_key=f"manual-release-result:{release_action_id}",
                        risk_account_id=risk_account_id,
                        site_id=SITE_ID,
                        external_user_id=before.external_user_id,
                        email=before.email,
                        event_type=(
                            "manual_release_partial"
                            if release_result.partial
                            else "manual_release_succeeded"
                        ),
                        decision_reason=normalized_reason,
                        risk_action_id=release_action_id,
                        event_result=details,
                        actor_id=actor_id,
                        actor_name=actor_name,
                        created_at=changed_at,
                    )
                    await operations_repository.replace_affected_aggregates(
                        growth,
                        site_id=SITE_ID,
                        start_at=OPERATIONS_AGGREGATE_HISTORY_START,
                        end_at=changed_at,
                    )
                operations_response_cache.invalidate(site_id=SITE_ID)
                return {**updated, **details, "status": "released"}
            finally:
                await source_engine.dispose()
        finally:
            await repository.release_cycle_lock(growth, site_id=SITE_ID)
            await growth.commit()


async def manual_ban(
    mongo_db: Any,
    *,
    risk_account_id: UUID,
    actor_id: str,
    actor_name: str,
    reason: str,
    now: datetime | None = None,
    site_loader: Callable[..., Any] = get_client_site,
    growth_session_factory: Callable[..., Any] = risk_growth_session,
    source_engine_factory: Callable[[dict[str, Any]], Any] = create_source_engine,
    adapter_factory: Callable[[], Any] = Sub2ApiRiskAdapter,
) -> dict[str, Any]:
    changed_at = now or datetime.now(UTC)
    normalized_reason = _reason(reason)
    async with growth_session_factory(mongo_db) as growth:
        acquired = await repository.acquire_cycle_lock(growth, site_id=SITE_ID)
        await growth.commit()
        if not acquired:
            raise RuntimeError("risk control is busy; retry the ban")
        try:
            async with growth.begin():
                account = await repository.get_account(growth, risk_account_id=risk_account_id)
                if account.get("risk_status") == "banned":
                    raise ValueError("account is already banned")
            site = await _load_site(mongo_db, site_loader)
            source_engine = source_engine_factory(site)
            adapter = adapter_factory()
            action_id = uuid4()
            try:
                async with source_engine.connect() as source_read:
                    before = await adapter.capture_account_state(
                        source_read,
                        str(account["external_user_id"]),
                    )
                async with growth.begin():
                    await repository.create_action(
                        growth,
                        risk_action_id=action_id,
                        idempotency_key=f"manual-ban:{risk_account_id}:{action_id}",
                        risk_account_id=risk_account_id,
                        site_id=SITE_ID,
                        external_user_id=before.external_user_id,
                        email=before.email,
                        action_type="manual_ban",
                        decision_reason=normalized_reason,
                        matched_email_rules=[],
                        shared_ip_evidence=[],
                        source_user_status_before=before.user_status,
                        source_user_updated_at_before=before.user_updated_at,
                        source_api_key_states_before=_key_payload(before.api_keys),
                        requested_by=actor_id,
                        requested_at=changed_at,
                    )
                async with source_engine.begin() as source_write:
                    enforced = await adapter.disable_account(
                        source_write,
                        before=before,
                        changed_at=changed_at,
                    )
                details = _enforced_payload(enforced)
                async with growth.begin():
                    await repository.complete_action(
                        growth,
                        risk_action_id=action_id,
                        status="succeeded",
                        completed_at=changed_at,
                        result_details=details,
                    )
                    updated = await repository.set_manual_override(
                        growth,
                        risk_account_id=risk_account_id,
                        active=False,
                        actor_id=actor_id,
                        reason=normalized_reason,
                        risk_status="banned",
                    )
                    await repository.set_stats_exclusion(
                        growth,
                        site_id=SITE_ID,
                        external_user_id=before.external_user_id,
                        risk_account_id=risk_account_id,
                        excluded=True,
                        actor_id=actor_id,
                    )
                    await repository.append_event(
                        growth,
                        risk_event_id=uuid4(),
                        idempotency_key=f"manual-ban-succeeded:{action_id}",
                        risk_account_id=risk_account_id,
                        site_id=SITE_ID,
                        external_user_id=before.external_user_id,
                        email=before.email,
                        event_type="manual_ban_succeeded",
                        decision_reason=normalized_reason,
                        risk_action_id=action_id,
                        event_result=details,
                        actor_id=actor_id,
                        actor_name=actor_name,
                        created_at=changed_at,
                    )
                    await operations_repository.replace_affected_aggregates(
                        growth,
                        site_id=SITE_ID,
                        start_at=OPERATIONS_AGGREGATE_HISTORY_START,
                        end_at=changed_at,
                    )
                operations_response_cache.invalidate(site_id=SITE_ID)
                return {**updated, "status": "banned"}
            finally:
                await source_engine.dispose()
        finally:
            await repository.release_cycle_lock(growth, site_id=SITE_ID)
            await growth.commit()


async def _load_site(mongo_db: Any, site_loader: Callable[..., Any]) -> dict[str, Any]:
    result = site_loader(mongo_db, SITE_ID, include_api_key=True)
    site = await result if inspect.isawaitable(result) else result
    if site is None or str(site.get("client_type") or "").lower() != "sub2api":
        raise LookupError("AIWeLink Sub2API site is not configured")
    if not str(site.get("sql_dsn") or "").strip():
        raise ValueError("AIWeLink SQL_DSN is not configured")
    return site


def _source_state_from_action(
    account: dict[str, Any],
    action: dict[str, Any],
) -> SourceAccountState:
    return SourceAccountState(
        external_user_id=str(account["external_user_id"]),
        email=str(account.get("email") or ""),
        user_status=str(action.get("source_user_status_before") or ""),
        user_updated_at=_datetime(action.get("source_user_updated_at_before")),
        api_keys=tuple(
            ApiKeyState(
                str(item["id"]),
                str(item.get("status") or ""),
                _datetime(item.get("updated_at")),
            )
            for item in (action.get("source_api_key_states_before") or [])
        ),
    )


def _enforced_state_from_action(action: dict[str, Any]) -> EnforcementResult:
    details = action.get("result_details") or {}
    return EnforcementResult(
        user_status=str(details.get("user_status") or "disabled"),
        user_updated_at=_datetime(details.get("user_updated_at")),
        api_keys=tuple(
            ApiKeyState(
                str(item["id"]),
                str(item.get("status") or "inactive"),
                _datetime(item.get("updated_at")),
            )
            for item in (details.get("api_keys") or [])
        ),
    )


def _datetime(value: Any) -> datetime | None:
    if value is None or isinstance(value, datetime):
        return value
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _key_payload(keys: tuple[ApiKeyState, ...]) -> list[dict[str, Any]]:
    return [
        {"id": key.id, "status": key.status, "updated_at": key.updated_at}
        for key in keys
    ]


def _enforced_payload(enforced: EnforcementResult) -> dict[str, Any]:
    return {
        "user_status": enforced.user_status,
        "user_updated_at": enforced.user_updated_at,
        "api_keys": _key_payload(enforced.api_keys),
    }


def _reason(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("reason is required")
    return normalized
