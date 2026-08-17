from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.growth.database import create_growth_engine
from app.modules.operations import repository as operations_repository
from app.modules.operations.cache import operations_response_cache
from app.modules.operations.sync import OPERATIONS_AGGREGATE_HISTORY_START
from app.modules.risk import repository
from app.modules.risk.adapters.sub2api import (
    EnforcementResult,
    SourceAccountState,
    Sub2ApiRiskAdapter,
)
from app.modules.risk.service import (
    PreparedBanCandidate,
    action_idempotency_key,
    collect_stream_pages,
    reconcile_risk_inputs,
    source_window_start,
)
from app.modules.system.client_sites import get_client_site
from app.modules.system.growth_database_settings import get_growth_database_settings_private
from app.modules.system.sql_dsn import parse_sql_dsn


SITE_ID = "aiwelink"
SOURCE_CONNECT_TIMEOUT_SECONDS = 30
RISK_STREAMS = ("audit_logs", "usage_logs")


@asynccontextmanager
async def risk_growth_session(
    mongo_db: Any,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
):
    settings = await get_growth_database_settings_private(mongo_db)
    sql_dsn = str(settings.get("sql_dsn") or "").strip()
    if not sql_dsn:
        raise ValueError("PostgreSQL SQL_DSN is not configured")
    engine = create_growth_engine(sql_dsn, engine_factory=engine_factory)
    try:
        async with engine.connect() as connection:
            yield connection
    finally:
        await engine.dispose()


def create_source_engine(
    site: dict[str, Any],
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> Any:
    parsed = parse_sql_dsn(str(site.get("sql_dsn") or ""), "postgresql")
    return engine_factory(
        parsed.driver_url(),
        poolclass=NullPool,
        connect_args=parsed.connect_args(SOURCE_CONNECT_TIMEOUT_SECONDS),
    )


async def run_risk_cycle(
    mongo_db: Any,
    *,
    now: datetime | None = None,
    site_loader: Callable[..., Any] = get_client_site,
    growth_session_factory: Callable[..., Any] = risk_growth_session,
    source_engine_factory: Callable[[dict[str, Any]], Any] = create_source_engine,
    adapter_factory: Callable[[], Any] = Sub2ApiRiskAdapter,
) -> dict[str, Any]:
    detected_at = now or datetime.now(UTC)
    async with growth_session_factory(mongo_db) as growth:
        acquired = await repository.acquire_cycle_lock(growth, site_id=SITE_ID)
        await growth.commit()
        if not acquired:
            return {"site_id": SITE_ID, "status": "skipped", "reason": "already_running"}
        try:
            async with growth.begin():
                settings = await repository.get_settings(growth, site_id=SITE_ID)
            if not settings.get("detector_enabled"):
                return {"site_id": SITE_ID, "status": "paused", "sources": {}}

            site_result = site_loader(mongo_db, SITE_ID, include_api_key=True)
            site = await site_result if hasattr(site_result, "__await__") else site_result
            if site is None:
                raise LookupError("AIWeLink site is not configured")
            if str(site.get("client_type") or "").lower() != "sub2api":
                raise ValueError("AIWeLink risk control requires a Sub2API site")
            if not str(site.get("sql_dsn") or "").strip():
                raise ValueError("AIWeLink SQL_DSN is not configured")

            source_engine = source_engine_factory(site)
            adapter = adapter_factory()
            try:
                return await _run_enabled_cycle(
                    growth,
                    source_engine=source_engine,
                    adapter=adapter,
                    settings=settings,
                    detected_at=detected_at,
                )
            finally:
                await source_engine.dispose()
        finally:
            await repository.release_cycle_lock(growth, site_id=SITE_ID)
            await growth.commit()


async def _run_enabled_cycle(
    growth: Any,
    *,
    source_engine: Any,
    adapter: Any,
    settings: dict[str, Any],
    detected_at: datetime,
) -> dict[str, Any]:
    async with growth.begin():
        cursors = {
            stream: await repository.get_cursor(
                growth,
                site_id=SITE_ID,
                source_stream=stream,
            )
            for stream in RISK_STREAMS
        }

    pages: dict[str, Any] = {}
    source_results: dict[str, dict[str, Any]] = {}
    source_errors: dict[str, Exception] = {}
    candidates: list[PreparedBanCandidate] = []
    async with source_engine.connect() as source_connection:
        for stream in RISK_STREAMS:
            try:
                page = await collect_stream_pages(
                    adapter,
                    source_connection,
                    stream=stream,  # type: ignore[arg-type]
                    after_id=int(cursors[stream].get("last_source_id") or 0),
                    since=source_window_start(now=detected_at),
                )
                pages[stream] = page
                source_results[stream] = {
                    "status": "succeeded",
                    "rows_read": page.rows_read,
                    "observations": len(page.observations),
                }
            except Exception as exc:  # noqa: BLE001 - streams advance independently.
                source_errors[stream] = exc
                source_results[stream] = {
                    "status": "failed",
                    "error_code": type(exc).__name__,
                    "error_message": str(exc)[:500],
                }

        async with growth.begin():
            for stream, page in pages.items():
                await repository.upsert_observations(
                    growth,
                    site_id=SITE_ID,
                    observations=page.observations,
                )
                await repository.save_cursor_success(
                    growth,
                    site_id=SITE_ID,
                    source_stream=stream,
                    last_source_id=page.last_source_id,
                    last_source_created_at=page.latest_created_at,
                    latest_observed_at=page.latest_created_at,
                    rows_read=page.rows_read,
                    succeeded_at=detected_at,
                )
            for stream, exc in source_errors.items():
                await repository.save_cursor_error(
                    growth,
                    site_id=SITE_ID,
                    source_stream=stream,
                    error_code=type(exc).__name__,
                    error_message=str(exc),
                    failed_at=detected_at,
                )
            await repository.cleanup_observations(
                growth,
                site_id=SITE_ID,
                cutoff=detected_at - timedelta(days=30),
            )
            inputs = await repository.list_account_risk_inputs(
                growth,
                site_id=SITE_ID,
                cutoff=detected_at - timedelta(days=int(settings.get("ip_window_days") or 7)),
                minimum_accounts=int(settings.get("shared_ip_min_accounts") or 3),
            )

        async def source_payment_checker(external_user_id: str) -> bool:
            return await adapter.has_completed_payment(source_connection, external_user_id)

        async with growth.begin():
            candidates = await reconcile_risk_inputs(
                growth,
                rows=inputs,
                auto_ban_enabled=bool(settings.get("auto_ban_enabled")),
                detected_at=detected_at,
                source_payment_checker=source_payment_checker,
            )

        prepared = []
        for candidate in candidates:
            before = await adapter.capture_account_state(
                source_connection,
                candidate.evaluation.external_user_id,
            )
            async with growth.begin():
                action = await repository.create_action(
                    growth,
                    risk_action_id=uuid4(),
                    idempotency_key=action_idempotency_key(SITE_ID, candidate.evaluation),
                    risk_account_id=UUID(candidate.risk_account_id),
                    site_id=SITE_ID,
                    external_user_id=candidate.evaluation.external_user_id,
                    email=candidate.evaluation.email,
                    action_type="auto_ban",
                    decision_reason="email_and_shared_ip",
                    matched_email_rules=list(candidate.evaluation.email_rules),
                    shared_ip_evidence=_evidence_payload(candidate),
                    source_user_status_before=before.user_status,
                    source_user_updated_at_before=before.user_updated_at,
                    source_api_key_states_before=_key_state_payload(before),
                    requested_by="system:risk-detector",
                    requested_at=detected_at,
                )
            if action.get("action_status") in {"pending", "failed"}:
                prepared.append((candidate, before, action))

    actions_succeeded = 0
    actions_failed = 0
    for candidate, before, action in prepared:
        action_id = UUID(str(action["risk_action_id"]))
        try:
            async with source_engine.begin() as source_write:
                if await adapter.has_completed_payment(
                    source_write,
                    candidate.evaluation.external_user_id,
                ):
                    await _finalize_paid_protection(
                        growth,
                        candidate=candidate,
                        action_id=action_id,
                        completed_at=detected_at,
                    )
                    continue
                enforced = await adapter.disable_account(
                    source_write,
                    before=before,
                    changed_at=detected_at,
                )
            await _finalize_success(
                growth,
                candidate=candidate,
                action_id=action_id,
                enforced=enforced,
                completed_at=detected_at,
            )
            actions_succeeded += 1
        except Exception as exc:  # noqa: BLE001 - action remains visible and retryable.
            await _finalize_failure(
                growth,
                candidate=candidate,
                action_id=action_id,
                error=exc,
                completed_at=detected_at,
            )
            actions_failed += 1

    return {
        "site_id": SITE_ID,
        "status": "succeeded" if not source_errors else "partial",
        "sources": source_results,
        "candidates": len(candidates),
        "actions_succeeded": actions_succeeded,
        "actions_failed": actions_failed,
    }


async def _finalize_success(
    growth: Any,
    *,
    candidate: PreparedBanCandidate,
    action_id: UUID,
    enforced: EnforcementResult,
    completed_at: datetime,
) -> None:
    result_details = {
        "user_status": enforced.user_status,
        "user_updated_at": enforced.user_updated_at,
        "api_keys": [
            {"id": key.id, "status": key.status, "updated_at": key.updated_at}
            for key in enforced.api_keys
        ],
    }
    async with growth.begin():
        await repository.complete_action(
            growth,
            risk_action_id=action_id,
            status="succeeded",
            completed_at=completed_at,
            result_details=result_details,
        )
        await repository.upsert_risk_account(
            growth,
            site_id=SITE_ID,
            external_user_id=candidate.evaluation.external_user_id,
            email=candidate.evaluation.email,
            risk_status="banned",
            risk_reasons=_risk_reasons(candidate),
            detected_at=completed_at,
            risk_account_id=UUID(candidate.risk_account_id),
        )
        await repository.set_stats_exclusion(
            growth,
            site_id=SITE_ID,
            external_user_id=candidate.evaluation.external_user_id,
            risk_account_id=UUID(candidate.risk_account_id),
            excluded=True,
            actor_id="system:risk-detector",
        )
        await repository.append_event(
            growth,
            risk_event_id=uuid4(),
            idempotency_key=f"auto-ban-succeeded:{action_id}",
            risk_account_id=UUID(candidate.risk_account_id),
            site_id=SITE_ID,
            external_user_id=candidate.evaluation.external_user_id,
            email=candidate.evaluation.email,
            event_type="auto_ban_succeeded",
            decision_reason="email_and_shared_ip",
            matched_email_rules=list(candidate.evaluation.email_rules),
            shared_ip_evidence=_evidence_payload(candidate),
            risk_action_id=action_id,
            event_result=result_details,
            actor_id="system:risk-detector",
            actor_name="AIWeLink risk detector",
            created_at=completed_at,
        )
        await operations_repository.replace_affected_aggregates(
            growth,
            site_id=SITE_ID,
            start_at=OPERATIONS_AGGREGATE_HISTORY_START,
            end_at=completed_at,
        )
    operations_response_cache.invalidate(site_id=SITE_ID)


async def _finalize_paid_protection(
    growth: Any,
    *,
    candidate: PreparedBanCandidate,
    action_id: UUID,
    completed_at: datetime,
) -> None:
    async with growth.begin():
        await repository.complete_action(
            growth,
            risk_action_id=action_id,
            status="conflicted",
            completed_at=completed_at,
            result_details={"protected_reason": "verified_payment_history"},
        )
        await repository.upsert_risk_account(
            growth,
            site_id=SITE_ID,
            external_user_id=candidate.evaluation.external_user_id,
            email=candidate.evaluation.email,
            risk_status="high_risk",
            risk_reasons={
                **_risk_reasons(candidate),
                "protection_reasons": ["verified_payment_history"],
            },
            detected_at=completed_at,
            risk_account_id=UUID(candidate.risk_account_id),
        )


async def _finalize_failure(
    growth: Any,
    *,
    candidate: PreparedBanCandidate,
    action_id: UUID,
    error: Exception,
    completed_at: datetime,
) -> None:
    async with growth.begin():
        await repository.complete_action(
            growth,
            risk_action_id=action_id,
            status="failed",
            completed_at=completed_at,
            error_code=type(error).__name__,
            error_message=str(error),
        )
        await repository.upsert_risk_account(
            growth,
            site_id=SITE_ID,
            external_user_id=candidate.evaluation.external_user_id,
            email=candidate.evaluation.email,
            risk_status="ban_failed",
            risk_reasons=_risk_reasons(candidate),
            detected_at=completed_at,
            risk_account_id=UUID(candidate.risk_account_id),
        )
        await repository.append_event(
            growth,
            risk_event_id=uuid4(),
            idempotency_key=f"auto-ban-failed:{action_id}",
            risk_account_id=UUID(candidate.risk_account_id),
            site_id=SITE_ID,
            external_user_id=candidate.evaluation.external_user_id,
            email=candidate.evaluation.email,
            event_type="auto_ban_failed",
            decision_reason="email_and_shared_ip",
            risk_action_id=action_id,
            error_code=type(error).__name__,
            error_message=str(error),
            actor_id="system:risk-detector",
            actor_name="AIWeLink risk detector",
            created_at=completed_at,
        )


def _key_state_payload(before: SourceAccountState) -> list[dict[str, Any]]:
    return [
        {"id": key.id, "status": key.status, "updated_at": key.updated_at}
        for key in before.api_keys
    ]


def _evidence_payload(candidate: PreparedBanCandidate) -> list[dict[str, Any]]:
    return [
        {
            "ip_address": item.ip_address,
            "distinct_account_count": item.distinct_account_count,
            "external_user_ids": list(item.external_user_ids),
            "sources": list(item.sources),
            "first_seen_at": item.first_seen_at,
            "last_seen_at": item.last_seen_at,
        }
        for item in candidate.evaluation.shared_ips
    ]


def _risk_reasons(candidate: PreparedBanCandidate) -> dict[str, Any]:
    return {
        "email_rules": list(candidate.evaluation.email_rules),
        "shared_ips": _evidence_payload(candidate),
        "protection_reasons": [],
    }
