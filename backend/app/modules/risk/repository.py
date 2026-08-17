from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any, Iterable
from uuid import UUID, uuid4

from sqlalchemy import text

from app.modules.risk.domain import IpObservation


DEFAULT_SETTINGS = {
    "detector_enabled": False,
    "auto_ban_enabled": False,
    "poll_interval_seconds": 60,
    "ip_window_days": 7,
    "shared_ip_min_accounts": 3,
}


def _public_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    if isinstance(value, tuple):
        return [_public_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _public_value(item) for key, item in value.items()}
    return value


def _rows(result: Any) -> list[dict[str, Any]]:
    return [
        {key: _public_value(value) for key, value in dict(row).items()}
        for row in result.mappings().all()
    ]


def _one(result: Any) -> dict[str, Any] | None:
    rows = _rows(result)
    return rows[0] if rows else None


async def get_settings(connection: Any, *, site_id: str) -> dict[str, Any]:
    result = await connection.execute(
        text("SELECT * FROM growth.risk_settings WHERE site_id = :site_id"),
        {"site_id": site_id},
    )
    return {"site_id": site_id, **DEFAULT_SETTINGS, **(_one(result) or {})}


async def get_cursor(
    connection: Any,
    *,
    site_id: str,
    source_stream: str,
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            SELECT * FROM growth.risk_sync_cursors
            WHERE site_id = :site_id AND source_stream = :source_stream
            """
        ),
        {"site_id": site_id, "source_stream": source_stream},
    )
    return {
        "site_id": site_id,
        "source_stream": source_stream,
        "last_source_id": 0,
        **(_one(result) or {}),
    }


async def save_cursor_success(
    connection: Any,
    *,
    site_id: str,
    source_stream: str,
    last_source_id: int,
    last_source_created_at: datetime | None,
    latest_observed_at: datetime | None,
    rows_read: int,
    succeeded_at: datetime,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO growth.risk_sync_cursors (
                site_id, source_stream, last_source_id, last_source_created_at,
                last_success_at, latest_observed_at, last_rows_read,
                last_error_code, last_error_message, updated_at
            ) VALUES (
                :site_id, :source_stream, :last_source_id, :last_source_created_at,
                :succeeded_at, :latest_observed_at, :rows_read, '', '', :succeeded_at
            )
            ON CONFLICT (site_id, source_stream) DO UPDATE SET
                last_source_id = EXCLUDED.last_source_id,
                last_source_created_at = EXCLUDED.last_source_created_at,
                last_success_at = EXCLUDED.last_success_at,
                latest_observed_at = COALESCE(
                    EXCLUDED.latest_observed_at,
                    growth.risk_sync_cursors.latest_observed_at
                ),
                last_rows_read = EXCLUDED.last_rows_read,
                last_error_code = '',
                last_error_message = '',
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "site_id": site_id,
            "source_stream": source_stream,
            "last_source_id": max(int(last_source_id), 0),
            "last_source_created_at": last_source_created_at,
            "latest_observed_at": latest_observed_at,
            "rows_read": max(int(rows_read), 0),
            "succeeded_at": succeeded_at,
        },
    )


async def save_cursor_error(
    connection: Any,
    *,
    site_id: str,
    source_stream: str,
    error_code: str,
    error_message: str,
    failed_at: datetime,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO growth.risk_sync_cursors (
                site_id, source_stream, last_source_id, last_rows_read,
                last_error_code, last_error_message, updated_at
            ) VALUES (
                :site_id, :source_stream, 0, 0,
                :error_code, :error_message, :failed_at
            )
            ON CONFLICT (site_id, source_stream) DO UPDATE SET
                last_rows_read = 0,
                last_error_code = EXCLUDED.last_error_code,
                last_error_message = EXCLUDED.last_error_message,
                updated_at = EXCLUDED.updated_at
            """
        ),
        {
            "site_id": site_id,
            "source_stream": source_stream,
            "error_code": error_code,
            "error_message": error_message[:1000],
            "failed_at": failed_at,
        },
    )


async def acquire_cycle_lock(connection: Any, *, site_id: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT pg_try_advisory_lock(
                hashtext('growth-risk-cycle:' || CAST(:site_id AS TEXT))
            ) AS acquired
            """
        ),
        {"site_id": site_id},
    )
    row = _one(result)
    return bool(row and row.get("acquired"))


async def release_cycle_lock(connection: Any, *, site_id: str) -> None:
    await connection.execute(
        text(
            """
            SELECT pg_advisory_unlock(
                hashtext('growth-risk-cycle:' || CAST(:site_id AS TEXT))
            ) AS released
            """
        ),
        {"site_id": site_id},
    )


async def upsert_observations(
    connection: Any,
    *,
    site_id: str,
    observations: Iterable[IpObservation],
) -> int:
    parameters = [
        {
            "site_id": site_id,
            "external_user_id": item.external_user_id,
            "email": item.email,
            "ip_address": item.ip_address,
            "source_type": item.source_type,
            "observed_at": item.observed_at,
            "source_id": item.source_id,
        }
        for item in observations
    ]
    if not parameters:
        return 0
    await connection.execute(
        text(
            """
            INSERT INTO growth.risk_ip_accounts (
                site_id, external_user_id, email, ip_address, source_type,
                first_seen_at, last_seen_at, event_count, latest_source_id,
                created_at, updated_at
            ) VALUES (
                :site_id, :external_user_id, :email, CAST(:ip_address AS INET), :source_type,
                :observed_at, :observed_at, 1, :source_id, NOW(), NOW()
            )
            ON CONFLICT (site_id, external_user_id, ip_address, source_type)
            DO UPDATE SET
                email = EXCLUDED.email,
                first_seen_at = LEAST(growth.risk_ip_accounts.first_seen_at, EXCLUDED.first_seen_at),
                last_seen_at = GREATEST(growth.risk_ip_accounts.last_seen_at, EXCLUDED.last_seen_at),
                event_count = growth.risk_ip_accounts.event_count + 1,
                latest_source_id = GREATEST(
                    growth.risk_ip_accounts.latest_source_id,
                    EXCLUDED.latest_source_id
                ),
                updated_at = NOW()
            """
        ),
        parameters,
    )
    return len(parameters)


async def list_account_risk_inputs(
    connection: Any,
    *,
    site_id: str,
    cutoff: datetime,
    minimum_accounts: int,
) -> list[dict[str, Any]]:
    result = await connection.execute(
        text(
            """
            WITH recent AS (
                SELECT site_id, external_user_id, email, host(ip_address) AS ip_address,
                       source_type, first_seen_at, last_seen_at
                FROM growth.risk_ip_accounts
                WHERE site_id = :site_id AND last_seen_at >= :cutoff
            ), shared AS (
                SELECT ip_address,
                       COUNT(DISTINCT external_user_id) AS distinct_account_count,
                       ARRAY_AGG(DISTINCT external_user_id ORDER BY external_user_id)
                           AS external_user_ids,
                       ARRAY_AGG(DISTINCT source_type ORDER BY source_type) AS sources,
                       MIN(first_seen_at) AS first_seen_at,
                       MAX(last_seen_at) AS last_seen_at
                FROM recent
                GROUP BY ip_address
                HAVING COUNT(DISTINCT external_user_id) >= :minimum_accounts
            ), account_pool AS (
                SELECT DISTINCT site_id, external_user_id, email FROM recent
                UNION
                SELECT site_id, external_user_id, email
                FROM growth.risk_accounts
                WHERE site_id = :site_id
            ), evidence AS (
                SELECT recent.external_user_id,
                       jsonb_agg(DISTINCT jsonb_build_object(
                           'ip_address', shared.ip_address,
                           'distinct_account_count', shared.distinct_account_count,
                           'external_user_ids', shared.external_user_ids,
                           'sources', shared.sources,
                           'first_seen_at', shared.first_seen_at,
                           'last_seen_at', shared.last_seen_at
                       )) AS shared_ip_evidence
                FROM recent
                JOIN shared ON shared.ip_address = recent.ip_address
                GROUP BY recent.external_user_id
            )
            SELECT pool.external_user_id, pool.email,
                   COALESCE(account.risk_account_id, NULL) AS risk_account_id,
                   account.risk_status,
                   COALESCE(account.manual_override_active, FALSE) AS manual_override_active,
                   EXISTS (
                       SELECT 1
                       FROM growth.credit_events AS event
                       WHERE event.site_id = pool.site_id
                         AND event.external_user_id = pool.external_user_id
                         AND event.direction = 'credit'
                         AND event.purpose = 'sale'
                         AND event.classification_status = 'classified'
                         AND event.cash_amount_cny > 0
                   ) AS has_verified_payment,
                   COALESCE(evidence.shared_ip_evidence, '[]'::JSONB) AS shared_ip_evidence
            FROM account_pool AS pool
            LEFT JOIN growth.risk_accounts AS account
              ON account.site_id = pool.site_id
             AND account.external_user_id = pool.external_user_id
            LEFT JOIN evidence ON evidence.external_user_id = pool.external_user_id
            ORDER BY pool.external_user_id
            """
        ),
        {"site_id": site_id, "cutoff": cutoff, "minimum_accounts": minimum_accounts},
    )
    return _rows(result)


async def upsert_risk_account(
    connection: Any,
    *,
    site_id: str,
    external_user_id: str,
    email: str,
    risk_status: str,
    risk_reasons: dict[str, Any],
    detected_at: datetime,
    risk_account_id: UUID | None = None,
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.risk_accounts (
                risk_account_id, site_id, external_user_id, email, normalized_email,
                risk_status, risk_reasons, first_detected_at, last_detected_at,
                banned_at, released_at, is_stats_excluded, created_at, updated_at
            ) VALUES (
                :risk_account_id, :site_id, :external_user_id, :email, lower(trim(:email)),
                :risk_status, CAST(:risk_reasons AS JSONB), :detected_at, :detected_at,
                CASE WHEN :risk_status = 'banned' THEN :detected_at ELSE NULL END,
                CASE WHEN :risk_status = 'released' THEN :detected_at ELSE NULL END,
                :risk_status = 'banned', NOW(), NOW()
            )
            ON CONFLICT (site_id, external_user_id) DO UPDATE SET
                email = EXCLUDED.email,
                normalized_email = EXCLUDED.normalized_email,
                risk_status = EXCLUDED.risk_status,
                risk_reasons = EXCLUDED.risk_reasons,
                last_detected_at = EXCLUDED.last_detected_at,
                banned_at = CASE
                    WHEN EXCLUDED.risk_status = 'banned'
                    THEN COALESCE(growth.risk_accounts.banned_at, EXCLUDED.last_detected_at)
                    ELSE growth.risk_accounts.banned_at
                END,
                released_at = CASE
                    WHEN EXCLUDED.risk_status = 'released' THEN EXCLUDED.last_detected_at
                    ELSE growth.risk_accounts.released_at
                END,
                is_stats_excluded = EXCLUDED.risk_status = 'banned',
                updated_at = NOW()
            RETURNING *
            """
        ),
        {
            "risk_account_id": risk_account_id or uuid4(),
            "site_id": site_id,
            "external_user_id": external_user_id,
            "email": email,
            "risk_status": risk_status,
            "risk_reasons": json.dumps(risk_reasons, default=str),
            "detected_at": detected_at,
        },
    )
    return _one(result) or {}


async def create_action(
    connection: Any,
    *,
    risk_action_id: UUID,
    idempotency_key: str,
    risk_account_id: UUID,
    site_id: str,
    external_user_id: str,
    email: str,
    action_type: str,
    decision_reason: str,
    matched_email_rules: list[str],
    shared_ip_evidence: list[dict[str, Any]],
    source_user_status_before: str,
    source_user_updated_at_before: datetime | None,
    source_api_key_states_before: list[dict[str, Any]],
    requested_by: str,
    requested_at: datetime,
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.risk_actions (
                risk_action_id, idempotency_key, risk_account_id, site_id,
                external_user_id, email, action_type, action_status,
                decision_reason, matched_email_rules, shared_ip_evidence,
                source_user_status_before, source_user_updated_at_before,
                source_api_key_states_before, requested_by, requested_at
            ) VALUES (
                :risk_action_id, :idempotency_key, :risk_account_id, :site_id,
                :external_user_id, :email, :action_type, 'pending',
                :decision_reason, CAST(:matched_email_rules AS JSONB),
                CAST(:shared_ip_evidence AS JSONB), :source_user_status_before,
                :source_user_updated_at_before, CAST(:source_api_key_states_before AS JSONB),
                :requested_by, :requested_at
            )
            ON CONFLICT (idempotency_key) DO UPDATE SET
                idempotency_key = growth.risk_actions.idempotency_key
            RETURNING *
            """
        ),
        {
            "risk_action_id": risk_action_id,
            "idempotency_key": idempotency_key,
            "risk_account_id": risk_account_id,
            "site_id": site_id,
            "external_user_id": external_user_id,
            "email": email,
            "action_type": action_type,
            "decision_reason": decision_reason,
            "matched_email_rules": json.dumps(matched_email_rules),
            "shared_ip_evidence": json.dumps(shared_ip_evidence, default=str),
            "source_user_status_before": source_user_status_before,
            "source_user_updated_at_before": source_user_updated_at_before,
            "source_api_key_states_before": json.dumps(source_api_key_states_before, default=str),
            "requested_by": requested_by,
            "requested_at": requested_at,
        },
    )
    return _one(result) or {}


async def complete_action(
    connection: Any,
    *,
    risk_action_id: UUID,
    status: str,
    completed_at: datetime,
    result_details: dict[str, Any] | None = None,
    error_code: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            UPDATE growth.risk_actions
            SET action_status = :status,
                result_details = CAST(:result_details AS JSONB),
                attempt_count = attempt_count + 1,
                error_code = :error_code,
                error_message = :error_message,
                started_at = COALESCE(started_at, :completed_at),
                completed_at = :completed_at
            WHERE risk_action_id = :risk_action_id
            RETURNING *
            """
        ),
        {
            "risk_action_id": risk_action_id,
            "status": status,
            "result_details": json.dumps(result_details or {}, default=str),
            "error_code": error_code,
            "error_message": error_message[:1000],
            "completed_at": completed_at,
        },
    )
    return _one(result) or {}


async def append_event(
    connection: Any,
    *,
    risk_event_id: UUID,
    idempotency_key: str,
    risk_account_id: UUID | None,
    site_id: str,
    external_user_id: str,
    email: str,
    event_type: str,
    decision_reason: str,
    created_at: datetime,
    matched_email_rules: list[str] | None = None,
    shared_ip_evidence: list[dict[str, Any]] | None = None,
    risk_action_id: UUID | None = None,
    event_result: dict[str, Any] | None = None,
    error_code: str = "",
    error_message: str = "",
    actor_id: str = "",
    actor_name: str = "",
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.risk_events (
                risk_event_id, idempotency_key, risk_account_id, site_id,
                external_user_id, email, event_type, decision_reason,
                matched_email_rules, shared_ip_evidence, risk_action_id,
                event_result, error_code, error_message, actor_id, actor_name, created_at
            ) VALUES (
                :risk_event_id, :idempotency_key, :risk_account_id, :site_id,
                :external_user_id, :email, :event_type, :decision_reason,
                CAST(:matched_email_rules AS JSONB), CAST(:shared_ip_evidence AS JSONB),
                :risk_action_id, CAST(:event_result AS JSONB), :error_code,
                :error_message, :actor_id, :actor_name, :created_at
            )
            ON CONFLICT (idempotency_key) DO NOTHING
            RETURNING *
            """
        ),
        {
            "risk_event_id": risk_event_id,
            "idempotency_key": idempotency_key,
            "risk_account_id": risk_account_id,
            "site_id": site_id,
            "external_user_id": external_user_id,
            "email": email,
            "event_type": event_type,
            "decision_reason": decision_reason,
            "matched_email_rules": json.dumps(matched_email_rules or []),
            "shared_ip_evidence": json.dumps(shared_ip_evidence or [], default=str),
            "risk_action_id": risk_action_id,
            "event_result": json.dumps(event_result or {}, default=str),
            "error_code": error_code,
            "error_message": error_message,
            "actor_id": actor_id,
            "actor_name": actor_name,
            "created_at": created_at,
        },
    )
    return _one(result) or {}


async def set_stats_exclusion(
    connection: Any,
    *,
    site_id: str,
    external_user_id: str,
    risk_account_id: UUID,
    excluded: bool,
    actor_id: str,
) -> None:
    await connection.execute(
        text(
            """
            UPDATE growth.ops_user_snapshots
            SET is_risk_excluded = :excluded,
                risk_account_id = CASE WHEN :excluded THEN :risk_account_id ELSE NULL END,
                synced_at = NOW()
            WHERE site_id = :site_id AND external_user_id = :external_user_id
            """
        ),
        {
            "site_id": site_id,
            "external_user_id": external_user_id,
            "risk_account_id": risk_account_id,
            "excluded": excluded,
        },
    )
    if excluded:
        await connection.execute(
            text(
                """
                INSERT INTO growth.user_exclusions (
                    site_id, external_user_id, reason, source, is_active,
                    created_by, updated_by, created_at, updated_at
                ) VALUES (
                    :site_id, :external_user_id, :reason, 'rule', TRUE,
                    :actor_id, :actor_id, NOW(), NOW()
                )
                ON CONFLICT (site_id, external_user_id) DO UPDATE SET
                    reason = EXCLUDED.reason,
                    source = 'rule',
                    is_active = TRUE,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = NOW()
                """
            ),
            {
                "site_id": site_id,
                "external_user_id": external_user_id,
                "reason": f"risk_control:{risk_account_id}",
                "actor_id": actor_id,
            },
        )
    else:
        await connection.execute(
            text(
                """
                UPDATE growth.user_exclusions
                SET is_active = FALSE, updated_by = :actor_id, updated_at = NOW()
                WHERE site_id = :site_id
                  AND external_user_id = :external_user_id
                  AND source = 'rule'
                  AND reason LIKE 'risk_control:%'
                """
            ),
            {"site_id": site_id, "external_user_id": external_user_id, "actor_id": actor_id},
        )


async def cleanup_observations(
    connection: Any,
    *,
    site_id: str,
    cutoff: datetime,
) -> int:
    result = await connection.execute(
        text(
            """
            WITH deleted AS (
                DELETE FROM growth.risk_ip_accounts
                WHERE site_id = :site_id AND last_seen_at < :cutoff
                RETURNING 1
            )
            SELECT COUNT(*) AS deleted_count FROM deleted
            """
        ),
        {"site_id": site_id, "cutoff": cutoff},
    )
    row = _one(result)
    return int((row or {}).get("deleted_count") or 0)
