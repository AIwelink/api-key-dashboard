from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
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
    ApiKeyState,
    EnforcementResult,
    ReleaseResult,
    SourceAccountState,
    Sub2ApiRiskAdapter,
)
from app.modules.risk.domain import RiskDecision
from app.modules.risk.service import (
    PreparedBanCandidate,
    action_idempotency_key,
    collect_stream_pages,
    evaluate_account_input,
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
                pending_manual_actions = (
                    []
                    if settings.get("detector_enabled")
                    else await repository.list_pending_manual_actions(
                        growth,
                        site_id=SITE_ID,
                        limit=1,
                    )
                )
            if not settings.get("detector_enabled") and not pending_manual_actions:
                await _refresh_dirty_operations_aggregates(
                    growth,
                    completed_at=detected_at,
                )
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
                if not settings.get("detector_enabled"):
                    manual_recovery = await recover_pending_manual_actions(
                        growth,
                        source_engine=source_engine,
                        adapter=adapter,
                        recovered_at=detected_at,
                    )
                    await _refresh_dirty_operations_aggregates(
                        growth,
                        completed_at=detected_at,
                    )
                    return {
                        "site_id": SITE_ID,
                        "status": "paused",
                        "sources": {},
                        "manual_recovery": manual_recovery,
                    }
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
    manual_recovery = await recover_pending_manual_actions(
        growth,
        source_engine=source_engine,
        adapter=adapter,
        recovered_at=detected_at,
    )
    recovery = await recover_pending_auto_bans(
        growth,
        source_engine=source_engine,
        adapter=adapter,
        recovered_at=detected_at,
        auto_ban_enabled=bool(settings.get("auto_ban_enabled")),
    )
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
    async with _source_connection_result(source_engine) as (source_connection, connection_error):
        if connection_error is not None:
            async with growth.begin():
                for stream in RISK_STREAMS:
                    await repository.save_cursor_error(
                        growth,
                        site_id=SITE_ID,
                        source_stream=stream,
                        error_code=type(connection_error).__name__,
                        error_message=str(connection_error),
                        failed_at=detected_at,
                    )
            failed_sources = {
                stream: {
                    "status": "failed",
                    "error_code": type(connection_error).__name__,
                    "error_message": str(connection_error)[:500],
                }
                for stream in RISK_STREAMS
            }
            return {
                "site_id": SITE_ID,
                "status": "failed",
                "sources": failed_sources,
                "candidates": 0,
                "actions_succeeded": 0,
                "actions_failed": 0,
                "recovery": recovery,
                "manual_recovery": manual_recovery,
            }
        if source_connection is None:
            raise RuntimeError("AIWeLink source connection did not initialize")
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
                await _rollback_source_connection(source_connection)
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
            current_candidate = _candidate_for_current_source(candidate, before)
            if current_candidate is None:
                current_evaluation = _current_source_evaluation(candidate, before)
                async with growth.begin():
                    await repository.upsert_risk_account(
                        growth,
                        site_id=SITE_ID,
                        external_user_id=current_evaluation.external_user_id,
                        email=current_evaluation.email,
                        risk_status="high_risk",
                        risk_reasons={
                            "email_rules": list(current_evaluation.email_rules),
                            "shared_ips": _evidence_payload(candidate),
                            "protection_reasons": [],
                        },
                        detected_at=detected_at,
                        risk_account_id=UUID(candidate.risk_account_id),
                    )
                continue
            candidate = current_candidate
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
        paid_protected = False
        enforced: EnforcementResult | None = None
        try:
            async with source_engine.begin() as source_write:
                paid_protected = await adapter.has_completed_payment(
                    source_write,
                    candidate.evaluation.external_user_id,
                )
                if not paid_protected:
                    enforced = await adapter.disable_account(
                        source_write,
                        before=before,
                        changed_at=detected_at,
                    )
        except Exception as exc:  # noqa: BLE001 - action remains visible and retryable.
            await _finalize_failure(
                growth,
                candidate=candidate,
                action_id=action_id,
                error=exc,
                completed_at=detected_at,
            )
            actions_failed += 1
            continue
        if paid_protected:
            await _finalize_paid_protection(
                growth,
                candidate=candidate,
                action_id=action_id,
                completed_at=detected_at,
            )
            continue
        if enforced is None:
            raise RuntimeError("source ban completed without enforcement details")
        await _finalize_success(
            growth,
            candidate=candidate,
            action_id=action_id,
            enforced=enforced,
            completed_at=detected_at,
        )
        actions_succeeded += 1

    await _refresh_dirty_operations_aggregates(growth, completed_at=detected_at)

    return {
        "site_id": SITE_ID,
        "status": "succeeded" if not source_errors else "partial",
        "sources": source_results,
        "candidates": len(candidates),
        "actions_succeeded": actions_succeeded,
        "actions_failed": actions_failed,
        "recovery": recovery,
        "manual_recovery": manual_recovery,
    }


@asynccontextmanager
async def _source_connection_result(source_engine: Any):
    stack = AsyncExitStack()
    try:
        connection = await stack.enter_async_context(source_engine.connect())
    except Exception as exc:  # noqa: BLE001 - caller records source health.
        yield None, exc
        return
    async with stack:
        yield connection, None


async def recover_pending_auto_bans(
    growth: Any,
    *,
    source_engine: Any,
    adapter: Any,
    recovered_at: datetime,
    auto_ban_enabled: bool = True,
) -> dict[str, int]:
    async with growth.begin():
        actions = await repository.list_pending_auto_ban_actions(
            growth,
            site_id=SITE_ID,
            limit=200,
        )

    counts = {"succeeded": 0, "conflicted": 0, "failed": 0}
    for action in actions:
        candidate = _candidate_from_action(action)
        evidence_cutoff = source_window_start(now=recovered_at)
        evidence_is_current = any(
            evidence.last_seen_at >= evidence_cutoff
            for evidence in candidate.evaluation.shared_ips
        )
        action_id = UUID(str(action["risk_action_id"]))
        before = _source_state_from_action(action)
        requested_at = _datetime(action.get("requested_at"))
        applied: EnforcementResult | None = None
        recovery_state = ""
        paid_protected = False
        try:
            async with source_engine.begin() as source_write:
                current = await adapter.capture_account_state(
                    source_write,
                    before.external_user_id,
                )
                recovery_state, applied = _classify_recovery_state(
                    before=before,
                    current=current,
                    requested_at=requested_at,
                )
                if recovery_state == "not_applied":
                    if (
                        not auto_ban_enabled
                        or action.get("manual_override_active")
                        or not evidence_is_current
                    ):
                        continue
                    paid_protected = await adapter.has_completed_payment(
                        source_write,
                        before.external_user_id,
                    )
                    if not paid_protected:
                        applied = await adapter.disable_account(
                            source_write,
                            before=before,
                            changed_at=recovered_at,
                        )
        except Exception:  # Source transaction rolled back; keep the action pending for retry.
            counts["failed"] += 1
            continue
        if recovery_state == "conflicted":
            await _finalize_recovery_conflict(
                growth,
                candidate=candidate,
                action_id=action_id,
                completed_at=recovered_at,
            )
            counts["conflicted"] += 1
            continue
        if paid_protected:
            await _finalize_paid_protection(
                growth,
                candidate=candidate,
                action_id=action_id,
                completed_at=recovered_at,
            )
            counts["conflicted"] += 1
            continue
        if applied is None:
            raise RuntimeError("recovered source state did not include enforcement details")
        await _finalize_success(
            growth,
            candidate=candidate,
            action_id=action_id,
            enforced=applied,
            completed_at=recovered_at,
        )
        counts["succeeded"] += 1
    return counts


async def recover_pending_manual_actions(
    growth: Any,
    *,
    source_engine: Any,
    adapter: Any,
    recovered_at: datetime,
) -> dict[str, int]:
    async with growth.begin():
        actions = await repository.list_pending_manual_actions(
            growth,
            site_id=SITE_ID,
            limit=200,
        )

    counts = {"succeeded": 0, "conflicted": 0, "failed": 0}
    for action in actions:
        action_type = str(action.get("action_type") or "")
        action_id = UUID(str(action["risk_action_id"]))
        before = _source_state_from_action(action)
        requested_at = _datetime(action.get("requested_at"))
        if requested_at is None:
            await _finalize_manual_action_conflict(
                growth,
                action=action,
                error_message="Pending manual action has no requested timestamp",
                completed_at=recovered_at,
            )
            counts["conflicted"] += 1
            continue

        if action_type not in {"manual_ban", "manual_release"}:
            await _finalize_manual_action_conflict(
                growth,
                action=action,
                error_message=f"Unsupported pending manual action: {action_type}",
                completed_at=recovered_at,
            )
            counts["conflicted"] += 1
            continue
        enforced_state: EnforcementResult | None = None
        if action_type == "manual_release":
            try:
                enforced_state = _enforced_state_from_manual_action(action)
            except Exception as exc:  # Corrupt lifecycle evidence cannot become recoverable.
                await _finalize_manual_action_failure(
                    growth,
                    action=action,
                    error=exc,
                    completed_at=recovered_at,
                )
                counts["failed"] += 1
                continue

        try:
            async with source_engine.begin() as source_write:
                current = await adapter.capture_account_state(
                    source_write,
                    before.external_user_id,
                )
                if action_type == "manual_ban":
                    recovery_state, enforced = _classify_recovery_state(
                        before=before,
                        current=current,
                        requested_at=requested_at,
                    )
                    if recovery_state == "not_applied":
                        enforced = await adapter.disable_account(
                            source_write,
                            before=before,
                            changed_at=recovered_at,
                        )
                    release_result = None
                elif action_type == "manual_release":
                    if enforced_state is None:
                        raise RuntimeError("manual release recovery snapshot was not initialized")
                    recovery_state, existing_result = _classify_release_recovery_state(
                        before=before,
                        enforced=enforced_state,
                        current=current,
                        requested_at=requested_at,
                    )
                    release_result = existing_result
                    enforced = None
                    if recovery_state == "retry":
                        retried = await adapter.release_account(
                            source_write,
                            before=before,
                            enforced=enforced_state,
                            changed_at=recovered_at,
                        )
                        release_result = _merge_release_results(
                            existing_result,
                            retried,
                            enforced=enforced_state,
                        )
        except Exception:  # Source transaction rolled back; keep the action pending for retry.
            counts["failed"] += 1
            continue

        if recovery_state == "conflicted":
            await _finalize_manual_action_conflict(
                growth,
                action=action,
                error_message="AIWeLink source state does not match the pending manual action snapshot",
                completed_at=recovered_at,
            )
            counts["conflicted"] += 1
            continue
        if action_type == "manual_ban":
            if enforced is None:
                raise RuntimeError("recovered manual ban has no enforcement details")
            await _finalize_manual_ban_success(
                growth,
                action=action,
                enforced=enforced,
                completed_at=recovered_at,
            )
        else:
            if release_result is None:
                raise RuntimeError("recovered manual release has no result details")
            await _finalize_manual_release_success(
                growth,
                action=action,
                release_result=release_result,
                completed_at=recovered_at,
            )
        counts["succeeded"] += 1
    return counts


def _classify_recovery_state(
    *,
    before: SourceAccountState,
    current: SourceAccountState,
    requested_at: datetime | None,
) -> tuple[str, EnforcementResult | None]:
    if current == before:
        return "not_applied", None
    if (
        requested_at is None
        or current.external_user_id != before.external_user_id
        or current.email != before.email
        or current.user_status != "disabled"
        or not _at_or_after(current.user_updated_at, requested_at)
        or len(current.api_keys) != len(before.api_keys)
    ):
        return "conflicted", None

    current_keys = {key.id: key for key in current.api_keys}
    enforced_keys: list[ApiKeyState] = []
    for prior in before.api_keys:
        observed = current_keys.get(prior.id)
        if observed is None:
            return "conflicted", None
        if prior.status == "active":
            if (
                observed.status != "inactive"
                or observed.updated_at != current.user_updated_at
            ):
                return "conflicted", None
            enforced_keys.append(observed)
        elif observed != prior:
            return "conflicted", None
    return (
        "applied",
        EnforcementResult(
            user_status=current.user_status,
            user_updated_at=current.user_updated_at,
            api_keys=tuple(enforced_keys),
        ),
    )


def _classify_release_recovery_state(
    *,
    before: SourceAccountState,
    enforced: EnforcementResult,
    current: SourceAccountState,
    requested_at: datetime,
) -> tuple[str, ReleaseResult | None]:
    if (
        current.external_user_id != before.external_user_id
        or current.email != before.email
    ):
        return "conflicted", None

    user_restored = (
        current.user_status == before.user_status
        and _at_or_after(current.user_updated_at, requested_at)
    )
    user_pending = (
        current.user_status == enforced.user_status
        and current.user_updated_at == enforced.user_updated_at
    )
    if not user_restored and not user_pending:
        user_conflicted = True
    else:
        user_conflicted = False

    current_keys = {key.id: key for key in current.api_keys}
    restored_key_ids: list[str] = []
    conflicted_key_ids: list[str] = []
    pending_key_ids: list[str] = []
    for prior in enforced.api_keys:
        observed = current_keys.get(prior.id)
        if observed is None:
            conflicted_key_ids.append(prior.id)
        elif observed.status == "active" and _at_or_after(observed.updated_at, requested_at):
            restored_key_ids.append(prior.id)
        elif observed == prior:
            pending_key_ids.append(prior.id)
        else:
            conflicted_key_ids.append(prior.id)

    existing = ReleaseResult(
        user_restored=user_restored,
        restored_key_ids=tuple(restored_key_ids),
        conflicted_key_ids=tuple(conflicted_key_ids),
        partial=(
            user_conflicted
            or not user_restored
            or bool(conflicted_key_ids)
            or bool(pending_key_ids)
        ),
    )
    if user_pending or pending_key_ids:
        return "retry", existing
    return "applied", existing


def _at_or_after(value: datetime | None, minimum: datetime) -> bool:
    return value is not None and value >= minimum


def _current_source_evaluation(
    candidate: PreparedBanCandidate,
    current: SourceAccountState,
):
    return evaluate_account_input(
        {
            "external_user_id": current.external_user_id,
            "email": current.email,
            "shared_ip_evidence": _evidence_payload(candidate),
            "manual_override_active": candidate.evaluation.manual_override_active,
            "has_paid_history": candidate.evaluation.has_paid_history,
        }
    )


def _candidate_for_current_source(
    candidate: PreparedBanCandidate,
    current: SourceAccountState,
) -> PreparedBanCandidate | None:
    evaluation = _current_source_evaluation(candidate, current)
    if evaluation.decision != RiskDecision.BAN:
        return None
    return PreparedBanCandidate(
        risk_account_id=candidate.risk_account_id,
        evaluation=evaluation,
    )


async def _rollback_source_connection(connection: Any) -> None:
    rollback = getattr(connection, "rollback", None)
    if rollback is not None:
        await rollback()


def _merge_release_results(
    existing: ReleaseResult | None,
    retried: ReleaseResult,
    *,
    enforced: EnforcementResult,
) -> ReleaseResult:
    restored_ids = set(retried.restored_key_ids)
    user_restored = retried.user_restored
    if existing is not None:
        restored_ids.update(existing.restored_key_ids)
        user_restored = user_restored or existing.user_restored
    conflicted_ids = tuple(
        key.id for key in enforced.api_keys if key.id not in restored_ids
    )
    return ReleaseResult(
        user_restored=user_restored,
        restored_key_ids=tuple(sorted(restored_ids)),
        conflicted_key_ids=conflicted_ids,
        partial=not user_restored or bool(conflicted_ids),
    )


def _source_state_from_action(action: dict[str, Any]) -> SourceAccountState:
    return SourceAccountState(
        external_user_id=str(action.get("external_user_id") or ""),
        email=str(action.get("email") or ""),
        user_status=str(action.get("source_user_status_before") or ""),
        user_updated_at=_datetime(action.get("source_user_updated_at_before")),
        api_keys=tuple(
            ApiKeyState(
                id=str(item.get("id") or ""),
                status=str(item.get("status") or ""),
                updated_at=_datetime(item.get("updated_at")),
            )
            for item in (action.get("source_api_key_states_before") or [])
        ),
    )


def _enforced_state_from_manual_action(action: dict[str, Any]) -> EnforcementResult:
    details = action.get("ban_result_details")
    if not isinstance(details, dict) or not {
        "user_status",
        "user_updated_at",
        "api_keys",
    }.issubset(details):
        raise ValueError("Pending manual release has no successful ban snapshot")
    return EnforcementResult(
        user_status=str(details.get("user_status") or "disabled"),
        user_updated_at=_datetime(details.get("user_updated_at")),
        api_keys=tuple(
            ApiKeyState(
                id=str(item.get("id") or ""),
                status=str(item.get("status") or "inactive"),
                updated_at=_datetime(item.get("updated_at")),
            )
            for item in (details.get("api_keys") or [])
        ),
    )


def _candidate_from_action(action: dict[str, Any]) -> PreparedBanCandidate:
    evaluation = evaluate_account_input(
        {
            "external_user_id": action.get("external_user_id"),
            "email": action.get("email"),
            "shared_ip_evidence": action.get("shared_ip_evidence") or [],
            "manual_override_active": False,
            "has_paid_history": False,
        }
    )
    return PreparedBanCandidate(
        risk_account_id=str(action.get("risk_account_id") or ""),
        evaluation=evaluation,
    )


async def _finalize_recovery_conflict(
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
            error_code="SourceStateConflict",
            error_message="AIWeLink source state does not match the pending ban snapshot",
        )
        await repository.upsert_risk_account(
            growth,
            site_id=SITE_ID,
            external_user_id=candidate.evaluation.external_user_id,
            email=candidate.evaluation.email,
            risk_status="high_risk",
            risk_reasons={
                **_risk_reasons(candidate),
                "protection_reasons": ["source_state_conflict"],
            },
            detected_at=completed_at,
            risk_account_id=UUID(candidate.risk_account_id),
        )
        await repository.append_event(
            growth,
            risk_event_id=uuid4(),
            idempotency_key=f"auto-ban-conflicted:{action_id}",
            risk_account_id=UUID(candidate.risk_account_id),
            site_id=SITE_ID,
            external_user_id=candidate.evaluation.external_user_id,
            email=candidate.evaluation.email,
            event_type="auto_ban_conflicted",
            decision_reason="source_state_conflict",
            matched_email_rules=list(candidate.evaluation.email_rules),
            shared_ip_evidence=_evidence_payload(candidate),
            risk_action_id=action_id,
            error_code="SourceStateConflict",
            error_message="AIWeLink source state does not match the pending ban snapshot",
            actor_id="system:risk-detector",
            actor_name="AIWeLink risk detector",
            created_at=completed_at,
        )


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


async def _finalize_manual_ban_success(
    growth: Any,
    *,
    action: dict[str, Any],
    enforced: EnforcementResult,
    completed_at: datetime,
) -> None:
    action_id = UUID(str(action["risk_action_id"]))
    risk_account_id = UUID(str(action["risk_account_id"]))
    actor_id = str(action.get("requested_by") or "")
    details = {
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
            result_details=details,
        )
        await repository.set_manual_override(
            growth,
            risk_account_id=risk_account_id,
            active=False,
            actor_id=actor_id,
            reason=str(action.get("decision_reason") or ""),
            risk_status="banned",
        )
        await repository.set_stats_exclusion(
            growth,
            site_id=SITE_ID,
            external_user_id=str(action.get("external_user_id") or ""),
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
            external_user_id=str(action.get("external_user_id") or ""),
            email=str(action.get("email") or ""),
            event_type="manual_ban_succeeded",
            decision_reason=str(action.get("decision_reason") or ""),
            risk_action_id=action_id,
            event_result=details,
            actor_id=actor_id,
            actor_name=actor_id,
            created_at=completed_at,
        )


async def _finalize_manual_release_success(
    growth: Any,
    *,
    action: dict[str, Any],
    release_result: ReleaseResult,
    completed_at: datetime,
) -> None:
    action_id = UUID(str(action["risk_action_id"]))
    risk_account_id = UUID(str(action["risk_account_id"]))
    actor_id = str(action.get("requested_by") or "")
    details = {
        "user_restored": release_result.user_restored,
        "restored_key_ids": list(release_result.restored_key_ids),
        "conflicted_key_ids": list(release_result.conflicted_key_ids),
        "partial": release_result.partial,
    }
    async with growth.begin():
        await repository.complete_action(
            growth,
            risk_action_id=action_id,
            status="succeeded",
            completed_at=completed_at,
            result_details=details,
        )
        await repository.set_manual_override(
            growth,
            risk_account_id=risk_account_id,
            active=True,
            actor_id=actor_id,
            reason=str(action.get("decision_reason") or ""),
            risk_status="released",
        )
        await repository.set_stats_exclusion(
            growth,
            site_id=SITE_ID,
            external_user_id=str(action.get("external_user_id") or ""),
            risk_account_id=risk_account_id,
            excluded=False,
            actor_id=actor_id,
        )
        await repository.append_event(
            growth,
            risk_event_id=uuid4(),
            idempotency_key=f"manual-release-result:{action_id}",
            risk_account_id=risk_account_id,
            site_id=SITE_ID,
            external_user_id=str(action.get("external_user_id") or ""),
            email=str(action.get("email") or ""),
            event_type=(
                "manual_release_partial"
                if release_result.partial
                else "manual_release_succeeded"
            ),
            decision_reason=str(action.get("decision_reason") or ""),
            risk_action_id=action_id,
            event_result=details,
            actor_id=actor_id,
            actor_name=actor_id,
            created_at=completed_at,
        )


async def _finalize_manual_action_failure(
    growth: Any,
    *,
    action: dict[str, Any],
    error: Exception,
    completed_at: datetime,
) -> None:
    action_id = UUID(str(action["risk_action_id"]))
    action_type = str(action.get("action_type") or "")
    actor_id = str(action.get("requested_by") or "")
    async with growth.begin():
        await repository.complete_action(
            growth,
            risk_action_id=action_id,
            status="failed",
            completed_at=completed_at,
            error_code=type(error).__name__,
            error_message=str(error),
        )
        await repository.append_event(
            growth,
            risk_event_id=uuid4(),
            idempotency_key=f"{action_type.replace('_', '-')}-failed:{action_id}",
            risk_account_id=UUID(str(action["risk_account_id"])),
            site_id=SITE_ID,
            external_user_id=str(action.get("external_user_id") or ""),
            email=str(action.get("email") or ""),
            event_type=(
                "manual_ban_failed"
                if action_type == "manual_ban"
                else "manual_release_partial"
            ),
            decision_reason=str(action.get("decision_reason") or ""),
            risk_action_id=action_id,
            error_code=type(error).__name__,
            error_message=str(error),
            actor_id=actor_id,
            actor_name=actor_id,
            created_at=completed_at,
        )


async def _finalize_manual_action_conflict(
    growth: Any,
    *,
    action: dict[str, Any],
    error_message: str,
    completed_at: datetime,
) -> None:
    action_id = UUID(str(action["risk_action_id"]))
    action_type = str(action.get("action_type") or "")
    actor_id = str(action.get("requested_by") or "")
    async with growth.begin():
        await repository.complete_action(
            growth,
            risk_action_id=action_id,
            status="conflicted",
            completed_at=completed_at,
            error_code="SourceStateConflict",
            error_message=error_message,
        )
        await repository.append_event(
            growth,
            risk_event_id=uuid4(),
            idempotency_key=f"{action_type.replace('_', '-')}-conflicted:{action_id}",
            risk_account_id=UUID(str(action["risk_account_id"])),
            site_id=SITE_ID,
            external_user_id=str(action.get("external_user_id") or ""),
            email=str(action.get("email") or ""),
            event_type=(
                "manual_ban_failed"
                if action_type == "manual_ban"
                else "manual_release_partial"
            ),
            decision_reason=str(action.get("decision_reason") or ""),
            risk_action_id=action_id,
            error_code="SourceStateConflict",
            error_message=error_message,
            actor_id=actor_id,
            actor_name=actor_id,
            created_at=completed_at,
        )


async def _refresh_operations_aggregates(
    growth: Any,
    *,
    completed_at: datetime,
) -> None:
    async with growth.begin():
        await operations_repository.replace_affected_aggregates(
            growth,
            site_id=SITE_ID,
            start_at=OPERATIONS_AGGREGATE_HISTORY_START,
            end_at=completed_at,
        )
        await repository.clear_risk_aggregates_dirty(growth, site_id=SITE_ID)
    operations_response_cache.invalidate(site_id=SITE_ID)


async def _refresh_dirty_operations_aggregates(
    growth: Any,
    *,
    completed_at: datetime,
) -> bool:
    async with growth.begin():
        dirty = await repository.risk_aggregates_are_dirty(growth, site_id=SITE_ID)
    if not dirty:
        return False
    await _refresh_operations_aggregates(growth, completed_at=completed_at)
    return True


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


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
