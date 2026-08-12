from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text

from app.modules.operations.schemas import (
    BalanceAdjustmentCreate,
    ClassificationUpdate,
    ConversionRateCreate,
    InternalUserCreate,
    InternalUserUpdate,
    RedemptionBatchCreate,
)


class OperationsNotFoundError(LookupError):
    pass


def _public_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _public_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_public_value(item) for item in value]
    return value


def _public_row(row: Any) -> dict[str, Any]:
    return _public_value(dict(row))


def _one(result: Any) -> dict[str, Any] | None:
    row = result.mappings().one_or_none()
    return _public_row(row) if row is not None else None


def _all(result: Any) -> list[dict[str, Any]]:
    return [_public_row(row) for row in result.mappings().all()]


def _record_dict(record: Any) -> dict[str, Any]:
    if hasattr(record, "model_dump"):
        result = record.model_dump()
    elif is_dataclass(record):
        result = asdict(record)
    else:
        result = dict(record)
    return {
        key: (value.value if isinstance(value, Enum) else value)
        for key, value in result.items()
    }


async def list_internal_users(
    connection: Any,
    *,
    allowed_site_ids: tuple[str, ...],
    query: str | None = None,
) -> list[dict[str, Any]]:
    result = await connection.execute(
        text(
            """
            SELECT internal_user.*,
                   CASE
                       WHEN internal_user.external_user_id IS NULL THEN 'pending'
                       ELSE 'recognized'
                   END AS recognition_status
            FROM growth.internal_users AS internal_user
            WHERE internal_user.site_id = ANY(CAST(:allowed_site_ids AS TEXT[]))
              AND (
                  CAST(:query AS TEXT) IS NULL
                  OR internal_user.external_user_id ILIKE '%' || :query || '%'
                  OR internal_user.email ILIKE '%' || :query || '%'
                  OR internal_user.account_label ILIKE '%' || :query || '%'
              )
            ORDER BY internal_user.created_at DESC, internal_user.internal_user_id
            """
        ),
        {"allowed_site_ids": allowed_site_ids, "query": query.strip() if query else None},
    )
    return _all(result)


async def create_internal_user(
    connection: Any,
    payload: InternalUserCreate,
    *,
    actor_id: str,
    internal_user_id: UUID | None = None,
) -> dict[str, Any]:
    selected_id = internal_user_id or uuid4()
    values = payload.model_dump()
    result = await connection.execute(
        text(
            """
            WITH email_matches AS (
                SELECT snapshot.external_user_id,
                       NOT EXISTS (
                           SELECT 1
                           FROM growth.internal_users AS existing
                           WHERE existing.site_id = snapshot.site_id
                             AND existing.external_user_id = snapshot.external_user_id
                       ) AS available
                FROM growth.ops_user_snapshots AS snapshot
                WHERE snapshot.site_id = :site_id
                  AND lower(trim(snapshot.account_label)) = lower(trim(:email))
            ), candidate AS (
                SELECT CASE
                           WHEN COUNT(*) = 1 AND BOOL_AND(email_matches.available)
                           THEN MIN(email_matches.external_user_id)
                           ELSE NULL
                       END AS external_user_id
                FROM email_matches
            ), inserted AS (
                INSERT INTO growth.internal_users (
                    internal_user_id, site_id, external_user_id, email, account_label,
                    reason, active_from, active_until, recognized_at, created_by, updated_by
                )
                SELECT
                    :internal_user_id, :site_id, candidate.external_user_id, :email, :email,
                    :reason, :active_from, :active_until,
                    CASE WHEN candidate.external_user_id IS NULL THEN NULL ELSE NOW() END,
                    :actor_id, :actor_id
                FROM candidate
                RETURNING *
            ), attached AS (
                UPDATE growth.ops_user_snapshots AS snapshot
                SET is_internal = TRUE,
                    internal_user_id = inserted.internal_user_id,
                    synced_at = NOW()
                FROM inserted
                WHERE inserted.external_user_id IS NOT NULL
                  AND inserted.active_from <= NOW()
                  AND (inserted.active_until IS NULL OR inserted.active_until > NOW())
                  AND snapshot.site_id = inserted.site_id
                  AND snapshot.external_user_id = inserted.external_user_id
            )
            SELECT inserted.*,
                   CASE
                       WHEN inserted.external_user_id IS NULL THEN 'pending'
                       ELSE 'recognized'
                   END AS recognition_status
            FROM inserted
            """
        ),
        {"internal_user_id": selected_id, "actor_id": actor_id, **values},
    )
    row = _one(result)
    return row or {}


async def get_internal_user_site_id(
    connection: Any,
    internal_user_id: UUID,
) -> str | None:
    result = await connection.execute(
        text("SELECT site_id FROM growth.internal_users WHERE internal_user_id = :internal_user_id"),
        {"internal_user_id": internal_user_id},
    )
    value = result.scalar_one_or_none()
    return str(value) if value is not None else None


async def update_internal_user(
    connection: Any,
    internal_user_id: UUID,
    payload: InternalUserUpdate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    updates = payload.model_dump(exclude_unset=True)
    if not updates:
        result = await connection.execute(
            text(
                """
                SELECT internal_user.*,
                       CASE
                           WHEN internal_user.external_user_id IS NULL THEN 'pending'
                           ELSE 'recognized'
                       END AS recognition_status
                FROM growth.internal_users AS internal_user
                WHERE internal_user.internal_user_id = :internal_user_id
                """
            ),
            {"internal_user_id": internal_user_id},
        )
    elif "email" in updates:
        additional_updates = {key: value for key, value in updates.items() if key != "email"}
        additional_assignments = "".join(
            f", {field} = :{field}" for field in additional_updates
        )
        result = await connection.execute(
            text(
                f"""
                WITH cleared_identity AS (
                    UPDATE growth.internal_users
                    SET email = :email,
                        account_label = :email,
                        external_user_id = NULL,
                        recognized_at = NULL
                        {additional_assignments},
                        updated_by = :actor_id,
                        updated_at = NOW()
                    WHERE internal_user_id = :internal_user_id
                    RETURNING *
                ), cleared_snapshot AS (
                    UPDATE growth.ops_user_snapshots AS snapshot
                    SET is_internal = FALSE,
                        internal_user_id = NULL,
                        synced_at = NOW()
                    FROM cleared_identity
                    WHERE snapshot.internal_user_id = cleared_identity.internal_user_id
                ), email_matches AS (
                    SELECT snapshot.external_user_id,
                           NOT EXISTS (
                               SELECT 1
                               FROM growth.internal_users AS existing
                               WHERE existing.site_id = snapshot.site_id
                                 AND existing.external_user_id = snapshot.external_user_id
                                 AND existing.internal_user_id <> :internal_user_id
                           ) AS available
                    FROM growth.ops_user_snapshots AS snapshot
                    JOIN cleared_identity
                      ON cleared_identity.site_id = snapshot.site_id
                    WHERE lower(trim(snapshot.account_label)) = lower(trim(:email))
                ), candidate AS (
                    SELECT CASE
                               WHEN COUNT(*) = 1 AND BOOL_AND(email_matches.available)
                               THEN MIN(email_matches.external_user_id)
                               ELSE NULL
                           END AS external_user_id
                    FROM email_matches
                ), recognized AS (
                    UPDATE growth.internal_users AS internal_user
                    SET external_user_id = candidate.external_user_id,
                        recognized_at = CASE
                            WHEN candidate.external_user_id IS NULL THEN NULL
                            ELSE NOW()
                        END
                    FROM candidate
                    WHERE internal_user.internal_user_id = :internal_user_id
                    RETURNING internal_user.*
                ), attached AS (
                    UPDATE growth.ops_user_snapshots AS snapshot
                    SET is_internal = TRUE,
                        internal_user_id = recognized.internal_user_id,
                        synced_at = NOW()
                    FROM recognized
                    WHERE recognized.external_user_id IS NOT NULL
                      AND recognized.active_from <= NOW()
                      AND (recognized.active_until IS NULL OR recognized.active_until > NOW())
                      AND snapshot.site_id = recognized.site_id
                      AND snapshot.external_user_id = recognized.external_user_id
                )
                SELECT recognized.*,
                       CASE
                           WHEN recognized.external_user_id IS NULL THEN 'pending'
                           ELSE 'recognized'
                       END AS recognition_status
                FROM recognized
                """
            ),
            {
                "internal_user_id": internal_user_id,
                "actor_id": actor_id,
                "email": updates["email"],
                **additional_updates,
            },
        )
    else:
        assignments = ", ".join(f"{field} = :{field}" for field in updates)
        result = await connection.execute(
            text(
                f"""
                WITH updated AS (
                    UPDATE growth.internal_users
                    SET {assignments}, updated_by = :actor_id, updated_at = NOW()
                    WHERE internal_user_id = :internal_user_id
                    RETURNING *
                ), reclassified AS (
                    UPDATE growth.ops_user_snapshots AS snapshot
                    SET is_internal = (
                            updated.external_user_id IS NOT NULL
                            AND updated.active_from <= NOW()
                            AND (updated.active_until IS NULL OR updated.active_until > NOW())
                        ),
                        internal_user_id = CASE
                            WHEN updated.external_user_id IS NOT NULL
                             AND updated.active_from <= NOW()
                             AND (updated.active_until IS NULL OR updated.active_until > NOW())
                            THEN updated.internal_user_id
                            ELSE NULL
                        END,
                        synced_at = NOW()
                    FROM updated
                    WHERE updated.external_user_id IS NOT NULL
                      AND snapshot.site_id = updated.site_id
                      AND snapshot.external_user_id = updated.external_user_id
                )
                SELECT updated.*,
                       CASE
                           WHEN updated.external_user_id IS NULL THEN 'pending'
                           ELSE 'recognized'
                       END AS recognition_status
                FROM updated
                """
            ),
            {"internal_user_id": internal_user_id, "actor_id": actor_id, **updates},
        )
    row = _one(result)
    if row is None:
        raise OperationsNotFoundError("internal user not found")
    return row


async def delete_internal_user(
    connection: Any,
    internal_user_id: UUID,
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            WITH target AS (
                SELECT *
                FROM growth.internal_users
                WHERE internal_user_id = :internal_user_id
                FOR UPDATE
            ), cleared AS (
                UPDATE growth.ops_user_snapshots AS snapshot
                SET is_internal = FALSE,
                    internal_user_id = NULL,
                    synced_at = NOW()
                FROM target
                WHERE snapshot.internal_user_id = target.internal_user_id
                RETURNING snapshot.external_user_id
            ), deletion_gate AS (
                SELECT target.*
                FROM target
                WHERE (SELECT COUNT(*) FROM cleared) >= 0
            ), deleted AS (
                DELETE FROM growth.internal_users AS internal_user
                USING deletion_gate AS target
                WHERE internal_user.internal_user_id = target.internal_user_id
                RETURNING target.*
            )
            SELECT deleted.*,
                   CASE
                       WHEN deleted.external_user_id IS NULL THEN 'pending'
                       ELSE 'recognized'
                   END AS recognition_status
            FROM deleted
            """
        ),
        {"internal_user_id": internal_user_id},
    )
    row = _one(result)
    if row is None:
        raise OperationsNotFoundError("internal user not found")
    return row


async def deactivate_internal_user(
    connection: Any,
    internal_user_id: UUID,
    *,
    actor_id: str,
    active_until: datetime,
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            WITH deactivated AS (
                UPDATE growth.internal_users
                SET active_until = :active_until, updated_by = :actor_id, updated_at = NOW()
                WHERE internal_user_id = :internal_user_id
                RETURNING *
            ), cleared AS (
                UPDATE growth.ops_user_snapshots AS snapshot
                SET is_internal = FALSE, internal_user_id = NULL, synced_at = NOW()
                FROM deactivated
                WHERE snapshot.site_id = deactivated.site_id
                  AND snapshot.external_user_id = deactivated.external_user_id
            )
            SELECT * FROM deactivated
            """
        ),
        {
            "internal_user_id": internal_user_id,
            "actor_id": actor_id,
            "active_until": active_until,
        },
    )
    row = _one(result)
    if row is None:
        raise OperationsNotFoundError("internal user not found")
    return row


async def list_conversion_rates(
    connection: Any,
    *,
    allowed_site_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    result = await connection.execute(
        text(
            """
            SELECT * FROM growth.balance_conversion_rates
            WHERE site_id = ANY(CAST(:allowed_site_ids AS TEXT[]))
            ORDER BY site_id, effective_from DESC
            """
        ),
        {"allowed_site_ids": allowed_site_ids},
    )
    return _all(result)


async def create_conversion_rate(
    connection: Any,
    payload: ConversionRateCreate,
    *,
    actor_id: str,
    conversion_rate_id: UUID | None = None,
) -> dict[str, Any]:
    selected_id = conversion_rate_id or uuid4()
    result = await connection.execute(
        text(
            """
            WITH closed_rate AS (
                UPDATE growth.balance_conversion_rates
                SET effective_until = :effective_from
                WHERE site_id = :site_id
                  AND effective_from < :effective_from
                  AND (effective_until IS NULL OR effective_until > :effective_from)
            )
            INSERT INTO growth.balance_conversion_rates (
                conversion_rate_id, site_id, balance_units_per_cny,
                effective_from, effective_until, note, created_by
            ) VALUES (
                :conversion_rate_id, :site_id, :balance_units_per_cny,
                :effective_from, :effective_until, :note, :actor_id
            )
            RETURNING *
            """
        ),
        {
            "conversion_rate_id": selected_id,
            "actor_id": actor_id,
            **payload.model_dump(),
        },
    )
    return _one(result) or {}


async def create_redemption_batch_request(
    connection: Any,
    payload: RedemptionBatchCreate,
    *,
    actor_id: str,
    redemption_batch_id: UUID | None = None,
) -> dict[str, Any]:
    values = _record_dict(payload)
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.redemption_batches (
                redemption_batch_id, site_id, idempotency_key, purpose, code_count,
                balance_units_per_code, cash_amount_cny, note, command_status,
                requested_by
            ) VALUES (
                :redemption_batch_id, :site_id, :idempotency_key, :purpose, :code_count,
                :balance_units_per_code, :cash_amount_cny, :note, 'pending',
                :actor_id
            )
            RETURNING *
            """
        ),
        {
            "redemption_batch_id": redemption_batch_id or uuid4(),
            "actor_id": actor_id,
            **values,
        },
    )
    return _one(result) or {}


async def list_redemption_batch_attributions(
    connection: Any,
    *,
    site_id: str,
) -> list[dict[str, Any]]:
    result = await connection.execute(
        text(
            """
            SELECT redemption_batch_id,
                   site_id,
                   source_batch_id,
                   code_masks,
                   requested_by,
                   created_at
            FROM growth.redemption_batches
            WHERE site_id = :site_id
              AND command_status = 'succeeded'
              AND source_batch_id IS NOT NULL
              AND source_batch_id <> ''
            ORDER BY created_at DESC, redemption_batch_id DESC
            """
        ),
        {"site_id": site_id},
    )
    return _all(result)


async def get_redemption_batch_by_idempotency(
    connection: Any,
    *,
    site_id: str,
    idempotency_key: str,
) -> dict[str, Any] | None:
    result = await connection.execute(
        text(
            """
            SELECT *
            FROM growth.redemption_batches
            WHERE site_id = :site_id
              AND idempotency_key = :idempotency_key
            LIMIT 1
            """
        ),
        {"site_id": site_id, "idempotency_key": idempotency_key},
    )
    return _one(result)


async def complete_redemption_batch(
    connection: Any,
    *,
    redemption_batch_id: UUID,
    source_batch_id: str,
    code_hashes: list[str],
    code_masks: list[str],
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            UPDATE growth.redemption_batches
            SET command_status = 'succeeded',
                source_batch_id = :source_batch_id,
                code_hashes = CAST(:code_hashes AS JSONB),
                code_masks = CAST(:code_masks AS JSONB),
                completed_at = NOW(),
                error_code = '',
                error_message = ''
            WHERE redemption_batch_id = :redemption_batch_id
            RETURNING *
            """
        ),
        {
            "redemption_batch_id": redemption_batch_id,
            "source_batch_id": source_batch_id,
            "code_hashes": json.dumps(code_hashes),
            "code_masks": json.dumps(code_masks),
        },
    )
    return _one(result) or {}


async def fail_redemption_batch(
    connection: Any,
    *,
    redemption_batch_id: UUID,
    error_code: str,
    error_message: str,
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            UPDATE growth.redemption_batches
            SET command_status = 'failed',
                completed_at = NOW(),
                error_code = :error_code,
                error_message = :error_message
            WHERE redemption_batch_id = :redemption_batch_id
              AND command_status <> 'succeeded'
            RETURNING *
            """
        ),
        {
            "redemption_batch_id": redemption_batch_id,
            "error_code": error_code[:120],
            "error_message": error_message[:500],
        },
    )
    return _one(result) or {}


async def create_balance_adjustment_request(
    connection: Any,
    payload: BalanceAdjustmentCreate,
    *,
    actor_id: str,
    adjustment_request_id: UUID | None = None,
) -> dict[str, Any]:
    values = _record_dict(payload)
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.balance_adjustment_requests (
                adjustment_request_id, site_id, external_user_id, idempotency_key,
                purpose, balance_units, cash_amount_cny, note, command_status,
                requested_by
            ) VALUES (
                :adjustment_request_id, :site_id, :external_user_id, :idempotency_key,
                :purpose, :balance_units, :cash_amount_cny, :note, 'pending',
                :actor_id
            )
            RETURNING *
            """
        ),
        {
            "adjustment_request_id": adjustment_request_id or uuid4(),
            "actor_id": actor_id,
            **values,
        },
    )
    return _one(result) or {}


async def upsert_user_snapshots(connection: Any, records: list[Any]) -> tuple[int, int]:
    if not records:
        return 0, 0
    parameters = [_record_dict(record) for record in records]
    await connection.execute(
        text(
            """
            INSERT INTO growth.ops_user_snapshots (
                site_id, external_user_id, account_label, registered_at,
                account_status, balance_units, is_internal, internal_user_id,
                source_created_at, source_updated_at, synced_at
            )
            SELECT
                :site_id, :external_user_id, :account_label, :registered_at,
                :account_status, :balance_units,
                internal.internal_user_id IS NOT NULL, internal.internal_user_id,
                :source_created_at, :source_updated_at, NOW()
            FROM (VALUES (1)) AS seed(value)
            LEFT JOIN LATERAL (
                SELECT configured.internal_user_id
                FROM growth.internal_users AS configured
                WHERE configured.site_id = :site_id
                  AND configured.external_user_id = :external_user_id
                  AND configured.active_from <= NOW()
                  AND (configured.active_until IS NULL OR configured.active_until > NOW())
                LIMIT 1
            ) AS internal ON TRUE
            ON CONFLICT (site_id, external_user_id) DO UPDATE SET
                account_label = EXCLUDED.account_label,
                registered_at = EXCLUDED.registered_at,
                account_status = EXCLUDED.account_status,
                balance_units = EXCLUDED.balance_units,
                is_internal = EXCLUDED.is_internal,
                internal_user_id = EXCLUDED.internal_user_id,
                source_created_at = EXCLUDED.source_created_at,
                source_updated_at = EXCLUDED.source_updated_at,
                synced_at = NOW()
            """
        ),
        parameters,
    )
    recognition_result = await connection.execute(
        text(
            """
            WITH matches AS (
                SELECT configured.internal_user_id,
                       snapshot.external_user_id,
                       NOT EXISTS (
                           SELECT 1
                           FROM growth.internal_users AS existing
                           WHERE existing.site_id = snapshot.site_id
                             AND existing.external_user_id = snapshot.external_user_id
                             AND existing.internal_user_id <> configured.internal_user_id
                       ) AS available
                FROM growth.internal_users AS configured
                JOIN growth.ops_user_snapshots AS snapshot
                  ON snapshot.site_id = configured.site_id
                 AND lower(trim(configured.email)) = lower(trim(snapshot.account_label))
                WHERE configured.external_user_id IS NULL
                  AND configured.email IS NOT NULL
                  AND configured.active_from <= NOW()
                  AND (configured.active_until IS NULL OR configured.active_until > NOW())
            ), unique_matches AS (
                SELECT matches.internal_user_id,
                       MIN(matches.external_user_id) AS external_user_id
                FROM matches
                GROUP BY matches.internal_user_id
                HAVING COUNT(*) = 1 AND BOOL_AND(matches.available)
            ), recognized AS (
                UPDATE growth.internal_users AS configured
                SET external_user_id = unique_matches.external_user_id,
                    recognized_at = NOW(),
                    updated_at = NOW()
                FROM unique_matches
                WHERE configured.internal_user_id = unique_matches.internal_user_id
                  AND (
                      configured.external_user_id = unique_matches.external_user_id
                      OR configured.external_user_id IS NULL
                  )
                RETURNING configured.internal_user_id,
                          configured.site_id,
                          configured.external_user_id
            ), attached AS (
                UPDATE growth.ops_user_snapshots AS snapshot
                SET is_internal = TRUE,
                    internal_user_id = recognized.internal_user_id,
                    synced_at = NOW()
                FROM recognized
                WHERE snapshot.site_id = recognized.site_id
                  AND snapshot.external_user_id = recognized.external_user_id
                RETURNING snapshot.external_user_id
            )
            SELECT COUNT(*) FROM attached
            """
        )
    )
    recognized_count = int(recognition_result.scalar_one_or_none() or 0)
    return len(parameters), recognized_count


async def reconcile_internal_user_snapshots(connection: Any, *, site_id: str) -> int:
    result = await connection.execute(
        text(
            """
            WITH desired AS (
                SELECT snapshot.site_id,
                       snapshot.external_user_id,
                       configured.internal_user_id
                FROM growth.ops_user_snapshots AS snapshot
                LEFT JOIN growth.internal_users AS configured
                  ON configured.site_id = snapshot.site_id
                 AND configured.external_user_id = snapshot.external_user_id
                 AND configured.active_from <= NOW()
                 AND (configured.active_until IS NULL OR configured.active_until > NOW())
                WHERE snapshot.site_id = :site_id
            ), reclassified AS (
                UPDATE growth.ops_user_snapshots AS snapshot
                SET is_internal = desired.internal_user_id IS NOT NULL,
                    internal_user_id = desired.internal_user_id,
                    synced_at = NOW()
                FROM desired
                WHERE snapshot.site_id = desired.site_id
                  AND snapshot.external_user_id = desired.external_user_id
                  AND (
                      snapshot.is_internal IS DISTINCT FROM
                          (desired.internal_user_id IS NOT NULL)
                      OR snapshot.internal_user_id IS DISTINCT FROM desired.internal_user_id
                  )
                RETURNING snapshot.external_user_id
            )
            SELECT COUNT(*) FROM reclassified
            """
        ),
        {"site_id": site_id},
    )
    return int(result.scalar_one_or_none() or 0)


async def upsert_usage_facts(connection: Any, records: list[Any]) -> int:
    if not records:
        return 0
    parameters = []
    for record in records:
        values = _record_dict(record)
        values.setdefault("usage_fact_id", uuid4())
        parameters.append(values)
    await connection.execute(
        text(
            """
            INSERT INTO growth.usage_facts (
                usage_fact_id, site_id, external_user_id, source_type, source_record_id,
                successful_call_count, consumed_balance_units, cost_cny,
                conversion_rate_id, occurred_at, source_updated_at, synced_at
            ) VALUES (
                :usage_fact_id, :site_id, :external_user_id, :source_type, :source_record_id,
                :successful_call_count, :consumed_balance_units, :cost_cny,
                :conversion_rate_id, :occurred_at, :source_updated_at, NOW()
            )
            ON CONFLICT (site_id, source_type, source_record_id) DO UPDATE SET
                external_user_id = EXCLUDED.external_user_id,
                successful_call_count = EXCLUDED.successful_call_count,
                consumed_balance_units = EXCLUDED.consumed_balance_units,
                cost_cny = EXCLUDED.cost_cny,
                conversion_rate_id = EXCLUDED.conversion_rate_id,
                occurred_at = EXCLUDED.occurred_at,
                source_updated_at = EXCLUDED.source_updated_at,
                synced_at = NOW()
            """
        ),
        parameters,
    )
    return len(parameters)


async def upsert_credit_events(connection: Any, records: list[Any]) -> int:
    if not records:
        return 0
    parameters = []
    for record in records:
        values = _record_dict(record)
        values.setdefault("credit_event_id", uuid4())
        values["source_metadata"] = json.dumps(
            values.get("source_metadata") or {}, ensure_ascii=True, default=str
        )
        parameters.append(values)
    await connection.execute(
        text(
            """
            INSERT INTO growth.credit_events AS existing (
                credit_event_id, site_id, external_user_id, source_type, source_record_id,
                direction, purpose, classification_status, balance_units, cash_amount_cny,
                conversion_rate_id, occurred_at, source_updated_at, source_metadata,
                synced_at
            ) VALUES (
                :credit_event_id, :site_id, :external_user_id, :source_type, :source_record_id,
                :direction, :purpose, :classification_status, :balance_units, :cash_amount_cny,
                :conversion_rate_id, :occurred_at, :source_updated_at,
                CAST(:source_metadata AS JSONB), NOW()
            )
            ON CONFLICT (site_id, source_type, source_record_id) DO UPDATE SET
                external_user_id = EXCLUDED.external_user_id,
                direction = EXCLUDED.direction,
                purpose = CASE
                    WHEN existing.classification_status = 'classified'
                     AND EXCLUDED.classification_status = 'pending'
                    THEN existing.purpose
                    ELSE EXCLUDED.purpose
                END,
                classification_status = CASE
                    WHEN existing.classification_status = 'classified'
                     AND EXCLUDED.classification_status = 'pending'
                    THEN existing.classification_status
                    ELSE EXCLUDED.classification_status
                END,
                balance_units = EXCLUDED.balance_units,
                cash_amount_cny = CASE
                    WHEN existing.classification_status = 'classified'
                     AND EXCLUDED.classification_status = 'pending'
                    THEN existing.cash_amount_cny
                    ELSE EXCLUDED.cash_amount_cny
                END,
                conversion_rate_id = EXCLUDED.conversion_rate_id,
                occurred_at = EXCLUDED.occurred_at,
                source_updated_at = EXCLUDED.source_updated_at,
                source_metadata = EXCLUDED.source_metadata,
                synced_at = NOW(),
                updated_at = NOW()
            """
        ),
        parameters,
    )
    return len(parameters)


async def delete_source_credit_events(
    connection: Any,
    *,
    site_id: str,
    source_types: tuple[str, ...],
) -> None:
    if not source_types:
        return
    await connection.execute(
        text(
            """
            DELETE FROM growth.credit_events
            WHERE site_id = :site_id
              AND source_type = ANY(CAST(:source_types AS TEXT[]))
            """
        ),
        {"site_id": site_id, "source_types": source_types},
    )


async def create_pending_classification_tasks(connection: Any, *, site_id: str) -> int:
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.classification_tasks (
                classification_task_id, site_id, credit_event_id
            )
            SELECT gen_random_uuid(), event.site_id, event.credit_event_id
            FROM growth.credit_events AS event
            LEFT JOIN growth.classification_tasks AS task
              ON task.credit_event_id = event.credit_event_id
            WHERE event.site_id = :site_id
              AND event.classification_status = 'pending'
              AND task.credit_event_id IS NULL
            ON CONFLICT (credit_event_id) DO NOTHING
            RETURNING classification_task_id
            """
        ),
        {"site_id": site_id},
    )
    return len(result.mappings().all())


async def list_classification_tasks(
    connection: Any,
    *,
    allowed_site_ids: tuple[str, ...],
    status: str = "pending",
) -> list[dict[str, Any]]:
    result = await connection.execute(
        text(
            """
            SELECT task.*, event.external_user_id, event.source_type,
                   event.source_record_id, event.balance_units, event.occurred_at,
                   snapshot.account_label
            FROM growth.classification_tasks AS task
            JOIN growth.credit_events AS event ON event.credit_event_id = task.credit_event_id
            LEFT JOIN growth.ops_user_snapshots AS snapshot
              ON snapshot.site_id = event.site_id
             AND snapshot.external_user_id = event.external_user_id
            WHERE task.site_id = ANY(CAST(:allowed_site_ids AS TEXT[]))
              AND task.status = :status
            ORDER BY event.occurred_at DESC,
                     task.created_at DESC,
                     task.classification_task_id DESC
            """
        ),
        {"allowed_site_ids": allowed_site_ids, "status": status},
    )
    return _all(result)


async def get_classification_task_site_id(
    connection: Any,
    classification_task_id: UUID,
) -> str | None:
    result = await connection.execute(
        text(
            "SELECT site_id FROM growth.classification_tasks "
            "WHERE classification_task_id = :classification_task_id"
        ),
        {"classification_task_id": classification_task_id},
    )
    value = result.scalar_one_or_none()
    return str(value) if value is not None else None


async def resolve_classification_task(
    connection: Any,
    classification_task_id: UUID,
    payload: ClassificationUpdate,
    *,
    actor_id: str,
) -> dict[str, Any]:
    values = payload.model_dump()
    purpose = values["purpose"].value if values["purpose"] is not None else None
    result = await connection.execute(
        text(
            """
            WITH updated_task AS (
                UPDATE growth.classification_tasks
                SET status = :status,
                    resolved_purpose = :purpose,
                    resolved_cash_amount_cny = :cash_amount_cny,
                    note = :note,
                    resolved_by = :actor_id,
                    resolved_at = NOW()
                WHERE classification_task_id = :classification_task_id
                  AND status = 'pending'
                RETURNING *
            ), updated_event AS (
                UPDATE growth.credit_events AS event
                SET purpose = :purpose,
                    cash_amount_cny = :cash_amount_cny,
                    classification_status = 'classified',
                    updated_at = NOW()
                FROM updated_task AS task
                WHERE event.credit_event_id = task.credit_event_id
                  AND :status = 'resolved'
                RETURNING event.credit_event_id
            )
            SELECT * FROM updated_task
            """
        ),
        {
            "classification_task_id": classification_task_id,
            "actor_id": actor_id,
            "purpose": purpose,
            "cash_amount_cny": values["cash_amount_cny"],
            "status": values["status"],
            "note": values["note"],
        },
    )
    row = _one(result)
    if row is None:
        raise OperationsNotFoundError("pending classification task not found")
    return row


def _segment_filter(alias: str) -> str:
    return (
        f"(:segment = 'all' OR (:segment = 'internal' AND {alias}.is_internal) "
        f"OR (:segment = 'ordinary' AND NOT {alias}.is_internal))"
    )


async def get_operations_summary(
    connection: Any,
    *,
    allowed_site_ids: tuple[str, ...],
    segment: str,
    start_at: datetime,
    end_at: datetime,
) -> dict[str, Any]:
    segment_filter = _segment_filter("snapshot")
    result = await connection.execute(
        text(
            f"""
            WITH scoped_users AS (
                SELECT snapshot.*
                FROM growth.ops_user_snapshots AS snapshot
                WHERE snapshot.site_id = ANY(CAST(:allowed_site_ids AS TEXT[]))
                  AND {segment_filter}
            ), user_metrics AS (
                SELECT COUNT(*) FILTER (
                    WHERE registered_at >= :start_at AND registered_at < :end_at
                ) AS registered_user_count
                FROM scoped_users
            ), usage_metrics AS (
                SELECT COUNT(DISTINCT usage.external_user_id) AS active_user_count,
                       COALESCE(SUM(usage.successful_call_count), 0) AS successful_call_count,
                       COALESCE(SUM(usage.consumed_balance_units), 0) AS consumed_balance_units,
                       COALESCE(SUM(usage.cost_cny), 0) AS cost_cny,
                       COALESCE(SUM(usage.cost_cny) FILTER (
                           WHERE usage.site_id = 'aigclink'
                             AND NOT snapshot.is_internal
                       ), 0) AS aigclink_income_cny
                FROM growth.usage_facts AS usage
                JOIN scoped_users AS snapshot
                  ON snapshot.site_id = usage.site_id
                 AND snapshot.external_user_id = usage.external_user_id
                WHERE usage.occurred_at >= :start_at AND usage.occurred_at < :end_at
            ), credit_metrics AS (
                SELECT COUNT(DISTINCT event.external_user_id) FILTER (
                           WHERE event.direction = 'credit' AND event.purpose = 'sale'
                       ) AS payer_count,
                       COUNT(*) FILTER (
                           WHERE event.direction = 'credit' AND event.purpose = 'sale'
                       ) AS sale_event_count,
                       COALESCE(SUM(event.cash_amount_cny) FILTER (
                           WHERE event.direction = 'credit'
                             AND event.purpose = 'sale'
                             AND event.site_id <> 'aigclink'
                       ), 0) AS aiwelink_income_cny,
                       COALESCE(SUM(event.cash_amount_cny) FILTER (
                           WHERE event.direction = 'debit' AND event.purpose = 'sale'
                       ), 0) AS refund_cny
                FROM growth.credit_events AS event
                JOIN scoped_users AS snapshot
                  ON snapshot.site_id = event.site_id
                 AND snapshot.external_user_id = event.external_user_id
                WHERE event.classification_status = 'classified'
                  AND event.occurred_at >= :start_at AND event.occurred_at < :end_at
            )
            SELECT user_metrics.registered_user_count,
                   usage_metrics.active_user_count,
                   usage_metrics.successful_call_count,
                   usage_metrics.consumed_balance_units,
                   usage_metrics.cost_cny,
                   credit_metrics.payer_count,
                   credit_metrics.sale_event_count,
                   usage_metrics.aigclink_income_cny
                       + credit_metrics.aiwelink_income_cny AS gross_income_cny,
                   credit_metrics.refund_cny,
                   usage_metrics.aigclink_income_cny
                       + credit_metrics.aiwelink_income_cny
                       - credit_metrics.refund_cny AS net_income_cny
            FROM user_metrics, usage_metrics, credit_metrics
            """
        ),
        {
            "allowed_site_ids": allowed_site_ids,
            "segment": segment,
            "start_at": start_at,
            "end_at": end_at,
        },
    )
    return _one(result) or {}


async def get_operations_site_breakdown(
    connection: Any,
    *,
    allowed_site_ids: tuple[str, ...],
    segment: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    segment_filter = _segment_filter("snapshot")
    result = await connection.execute(
        text(
            f"""
            WITH scoped_sites AS (
                SELECT unnest(CAST(:allowed_site_ids AS TEXT[])) AS site_id
            ), scoped_users AS (
                SELECT snapshot.*
                FROM growth.ops_user_snapshots AS snapshot
                WHERE snapshot.site_id = ANY(CAST(:allowed_site_ids AS TEXT[]))
                  AND {segment_filter}
            ), user_metrics AS (
                SELECT snapshot.site_id,
                       COUNT(*) FILTER (
                           WHERE snapshot.registered_at >= :start_at
                             AND snapshot.registered_at < :end_at
                       ) AS registered_user_count
                FROM scoped_users AS snapshot
                GROUP BY snapshot.site_id
            ), usage_metrics AS (
                SELECT usage.site_id,
                       COUNT(DISTINCT usage.external_user_id) AS active_user_count,
                       COALESCE(SUM(usage.successful_call_count), 0) AS successful_call_count,
                       COALESCE(SUM(usage.consumed_balance_units), 0) AS consumed_balance_units,
                       COALESCE(SUM(usage.cost_cny), 0) AS cost_cny,
                       COALESCE(SUM(usage.cost_cny) FILTER (
                           WHERE usage.site_id = 'aigclink'
                             AND NOT snapshot.is_internal
                       ), 0) AS aigclink_income_cny
                FROM growth.usage_facts AS usage
                JOIN scoped_users AS snapshot
                  ON snapshot.site_id = usage.site_id
                 AND snapshot.external_user_id = usage.external_user_id
                WHERE usage.occurred_at >= :start_at
                  AND usage.occurred_at < :end_at
                GROUP BY usage.site_id
            ), credit_metrics AS (
                SELECT event.site_id,
                       COUNT(DISTINCT event.external_user_id) FILTER (
                           WHERE event.direction = 'credit' AND event.purpose = 'sale'
                       ) AS payer_count,
                       COUNT(*) FILTER (
                           WHERE event.direction = 'credit' AND event.purpose = 'sale'
                       ) AS sale_event_count,
                       COALESCE(SUM(event.cash_amount_cny) FILTER (
                           WHERE event.direction = 'credit'
                             AND event.purpose = 'sale'
                             AND event.site_id <> 'aigclink'
                       ), 0) AS aiwelink_income_cny,
                       COALESCE(SUM(event.cash_amount_cny) FILTER (
                           WHERE event.direction = 'debit' AND event.purpose = 'sale'
                       ), 0) AS refund_cny
                FROM growth.credit_events AS event
                JOIN scoped_users AS snapshot
                  ON snapshot.site_id = event.site_id
                 AND snapshot.external_user_id = event.external_user_id
                WHERE event.classification_status = 'classified'
                  AND event.occurred_at >= :start_at
                  AND event.occurred_at < :end_at
                GROUP BY event.site_id
            )
            SELECT site.site_id,
                   COALESCE(users.registered_user_count, 0) AS registered_user_count,
                   COALESCE(usage.active_user_count, 0) AS active_user_count,
                   COALESCE(usage.successful_call_count, 0) AS successful_call_count,
                   COALESCE(usage.consumed_balance_units, 0) AS consumed_balance_units,
                   COALESCE(usage.cost_cny, 0) AS cost_cny,
                   COALESCE(credit.payer_count, 0) AS payer_count,
                   COALESCE(credit.sale_event_count, 0) AS sale_event_count,
                   COALESCE(usage.aigclink_income_cny, 0)
                       + COALESCE(credit.aiwelink_income_cny, 0) AS gross_income_cny,
                   COALESCE(credit.refund_cny, 0) AS refund_cny,
                   COALESCE(usage.aigclink_income_cny, 0)
                       + COALESCE(credit.aiwelink_income_cny, 0)
                       - COALESCE(credit.refund_cny, 0) AS net_income_cny
            FROM scoped_sites AS site
            LEFT JOIN user_metrics AS users ON users.site_id = site.site_id
            LEFT JOIN usage_metrics AS usage ON usage.site_id = site.site_id
            LEFT JOIN credit_metrics AS credit ON credit.site_id = site.site_id
            ORDER BY site.site_id
            """
        ),
        {
            "allowed_site_ids": allowed_site_ids,
            "segment": segment,
            "start_at": start_at,
            "end_at": end_at,
        },
    )
    return _all(result)


async def get_operations_trends(
    connection: Any,
    *,
    allowed_site_ids: tuple[str, ...],
    segment: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    hourly = end_at - start_at <= timedelta(hours=48)
    table_name = "ops_hourly_stats" if hourly else "ops_daily_stats"
    bucket_name = "bucket_start" if hourly else "bucket_date"
    timezone_join = "AND ordinary.timezone = stats.timezone" if not hourly else ""
    income_expression = """
        COALESCE(
            CASE
                WHEN stats.site_id = 'aigclink' AND stats.user_segment = 'internal' THEN 0
                WHEN stats.site_id = 'aigclink' THEN ordinary.cost_cny
                ELSE stats.gross_income_cny
            END,
            0
        )
    """.strip()
    result = await connection.execute(
        text(
            f"""
            SELECT stats.{bucket_name} AS bucket, stats.site_id, stats.user_segment,
                   stats.registered_user_count, stats.active_user_count,
                   stats.successful_call_count, stats.consumed_balance_units,
                   stats.cost_cny, stats.payer_count, stats.sale_event_count,
                   {income_expression} AS gross_income_cny,
                   stats.refund_cny,
                   {income_expression} - stats.refund_cny AS net_income_cny,
                   stats.computed_at
            FROM growth.{table_name} AS stats
            LEFT JOIN growth.{table_name} AS ordinary
              ON ordinary.site_id = stats.site_id
             AND ordinary.{bucket_name} = stats.{bucket_name}
             AND ordinary.user_segment = 'ordinary'
             {timezone_join}
            WHERE stats.site_id = ANY(CAST(:allowed_site_ids AS TEXT[]))
              AND stats.user_segment = :segment
              AND stats.{bucket_name} >= :start_at
              AND stats.{bucket_name} < :end_at
            ORDER BY stats.{bucket_name}, stats.site_id
            """
        ),
        {
            "allowed_site_ids": allowed_site_ids,
            "segment": segment,
            "start_at": start_at,
            "end_at": end_at,
        },
    )
    return _all(result)


async def list_operations_users(
    connection: Any,
    *,
    allowed_site_ids: tuple[str, ...],
    segment: str,
    start_at: datetime,
    end_at: datetime,
    query: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    segment_filter = _segment_filter("snapshot")
    result = await connection.execute(
        text(
            f"""
            SELECT snapshot.*,
                   COUNT(DISTINCT usage.usage_fact_id) AS usage_event_count,
                   COALESCE(SUM(usage.successful_call_count), 0) AS successful_call_count,
                   COALESCE(SUM(usage.cost_cny), 0) AS cost_cny,
                   MAX(usage.occurred_at) AS last_used_at
            FROM growth.ops_user_snapshots AS snapshot
            LEFT JOIN growth.usage_facts AS usage
              ON usage.site_id = snapshot.site_id
             AND usage.external_user_id = snapshot.external_user_id
             AND usage.occurred_at >= :start_at AND usage.occurred_at < :end_at
            WHERE snapshot.site_id = ANY(CAST(:allowed_site_ids AS TEXT[]))
              AND {segment_filter}
              AND (
                  CAST(:query AS TEXT) IS NULL
                  OR snapshot.external_user_id ILIKE '%' || :query || '%'
                  OR snapshot.account_label ILIKE '%' || :query || '%'
              )
            GROUP BY snapshot.site_id, snapshot.external_user_id
            ORDER BY last_used_at DESC NULLS LAST, snapshot.registered_at DESC NULLS LAST
            LIMIT :limit OFFSET :offset
            """
        ),
        {
            "allowed_site_ids": allowed_site_ids,
            "segment": segment,
            "start_at": start_at,
            "end_at": end_at,
            "query": query.strip() if query else None,
            "limit": min(max(limit, 1), 500),
            "offset": max(offset, 0),
        },
    )
    return _all(result)


async def get_sync_status(connection: Any, *, allowed_site_ids: tuple[str, ...]) -> list[dict[str, Any]]:
    result = await connection.execute(
        text(
            """
            WITH latest AS (
                SELECT DISTINCT ON (site_id)
                       site_id, run_id, adapter_name, status, started_at, finished_at,
                       rows_scanned, rows_upserted, rows_rejected, error_code, error_message
                FROM growth.sync_runs
                WHERE stream_name = 'operations'
                  AND site_id = ANY(CAST(:allowed_site_ids AS TEXT[]))
                ORDER BY site_id, started_at DESC
            )
            SELECT latest.*, cursor.last_success_at, cursor.watermark_at
            FROM latest
            LEFT JOIN growth.sync_cursors AS cursor
              ON cursor.site_id = latest.site_id
             AND cursor.adapter_name = latest.adapter_name
             AND cursor.stream_name = 'operations'
            ORDER BY latest.site_id
            """
        ),
        {"allowed_site_ids": allowed_site_ids},
    )
    return _all(result)


async def get_operations_sync_cursor(
    connection: Any,
    *,
    site_id: str,
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            SELECT cursor_value, watermark_at, last_success_at, last_run_id, updated_at
            FROM growth.sync_cursors
            WHERE site_id = :site_id AND stream_name = 'operations'
            ORDER BY updated_at DESC
            LIMIT 1
            """
        ),
        {"site_id": site_id},
    )
    return _one(result) or {}


async def start_operations_sync_run(
    connection: Any,
    *,
    site_id: str,
    adapter_name: str,
    trigger_type: str,
    started_at: datetime,
    run_id: UUID | None = None,
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            INSERT INTO growth.sync_runs (
                run_id, site_id, adapter_name, stream_name, trigger_type,
                status, started_at
            ) VALUES (
                :run_id, :site_id, :adapter_name, 'operations', :trigger_type,
                'running', :started_at
            )
            RETURNING *
            """
        ),
        {
            "run_id": run_id or uuid4(),
            "site_id": site_id,
            "adapter_name": adapter_name,
            "trigger_type": trigger_type,
            "started_at": started_at,
        },
    )
    return _one(result) or {}


async def finish_operations_sync_run(
    connection: Any,
    *,
    run_id: UUID,
    site_id: str,
    adapter_name: str,
    status: str,
    finished_at: datetime,
    rows_scanned: int,
    rows_upserted: int,
    aggregate_version: int,
    rows_rejected: int = 0,
    error_code: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    result = await connection.execute(
        text(
            """
            WITH finished AS (
                UPDATE growth.sync_runs
                SET status = :status,
                    rows_scanned = :rows_scanned,
                    rows_upserted = :rows_upserted,
                    rows_rejected = :rows_rejected,
                    finished_at = :finished_at,
                    error_code = :error_code,
                    error_message = :error_message
                WHERE run_id = :run_id
                RETURNING *
            ), cursor_update AS (
                INSERT INTO growth.sync_cursors (
                    site_id, adapter_name, stream_name, cursor_value,
                    watermark_at, last_success_at, last_run_id, updated_at
                )
                SELECT :site_id, :adapter_name, 'operations',
                       jsonb_build_object('aggregate_version', :aggregate_version),
                       :finished_at, :finished_at, :run_id, NOW()
                FROM finished
                WHERE finished.status = 'succeeded'
                ON CONFLICT (site_id, adapter_name, stream_name) DO UPDATE SET
                    cursor_value = COALESCE(growth.sync_cursors.cursor_value, '{}'::JSONB)
                        || EXCLUDED.cursor_value,
                    watermark_at = EXCLUDED.watermark_at,
                    last_success_at = EXCLUDED.last_success_at,
                    last_run_id = EXCLUDED.last_run_id,
                    updated_at = NOW()
            )
            SELECT * FROM finished
            """
        ),
        {
            "run_id": run_id,
            "site_id": site_id,
            "adapter_name": adapter_name,
            "status": status,
            "finished_at": finished_at,
            "rows_scanned": rows_scanned,
            "rows_upserted": rows_upserted,
            "aggregate_version": aggregate_version,
            "rows_rejected": rows_rejected,
            "error_code": error_code,
            "error_message": error_message[:500],
        },
    )
    return _one(result) or {}


async def replace_affected_aggregates(
    connection: Any,
    *,
    site_id: str,
    start_at: datetime,
    end_at: datetime,
    timezone: str = "Asia/Shanghai",
) -> None:
    await _replace_aggregate_table(
        connection,
        table_name="ops_hourly_stats",
        bucket_expression="date_trunc('hour', event_at)",
        bucket_column="bucket_start",
        bucket_start_expression="date_trunc('hour', CAST(:start_at AS TIMESTAMPTZ))",
        bucket_end_expression="date_trunc('hour', CAST(:end_at AS TIMESTAMPTZ)) + INTERVAL '1 hour'",
        event_start_expression="date_trunc('hour', CAST(:start_at AS TIMESTAMPTZ))",
        event_end_expression="date_trunc('hour', CAST(:end_at AS TIMESTAMPTZ)) + INTERVAL '1 hour'",
        site_id=site_id,
        start_at=start_at,
        end_at=end_at,
        timezone=timezone,
    )
    await _replace_aggregate_table(
        connection,
        table_name="ops_daily_stats",
        bucket_expression="(event_at AT TIME ZONE :timezone)::date",
        bucket_column="bucket_date",
        bucket_start_expression="(CAST(:start_at AS TIMESTAMPTZ) AT TIME ZONE :timezone)::date",
        bucket_end_expression="(CAST(:end_at AS TIMESTAMPTZ) AT TIME ZONE :timezone)::date + 1",
        event_start_expression="((CAST(:start_at AS TIMESTAMPTZ) AT TIME ZONE :timezone)::date AT TIME ZONE :timezone)",
        event_end_expression="(((CAST(:end_at AS TIMESTAMPTZ) AT TIME ZONE :timezone)::date + 1) AT TIME ZONE :timezone)",
        site_id=site_id,
        start_at=start_at,
        end_at=end_at,
        timezone=timezone,
    )


async def _replace_aggregate_table(
    connection: Any,
    *,
    table_name: str,
    bucket_expression: str,
    bucket_column: str,
    bucket_start_expression: str,
    bucket_end_expression: str,
    event_start_expression: str,
    event_end_expression: str,
    site_id: str,
    start_at: datetime,
    end_at: datetime,
    timezone: str,
) -> None:
    timezone_column = ", timezone" if table_name == "ops_daily_stats" else ""
    timezone_value = ", :timezone" if table_name == "ops_daily_stats" else ""
    await connection.execute(
        text(
            f"""
            DELETE FROM growth.{table_name}
            WHERE site_id = :site_id
              AND {bucket_column} >= {bucket_start_expression}
              AND {bucket_column} < {bucket_end_expression}
              {"AND timezone = :timezone" if table_name == "ops_daily_stats" else ""}
            """
        ),
        {"site_id": site_id, "start_at": start_at, "end_at": end_at, "timezone": timezone},
    )
    await connection.execute(
        text(
            f"""
            WITH raw_events AS (
                SELECT snapshot.site_id, snapshot.external_user_id,
                       snapshot.registered_at AS event_at,
                       snapshot.is_internal,
                       1::BIGINT AS registered_count, 0::BIGINT AS call_count,
                       0::NUMERIC AS consumed_units, 0::NUMERIC AS cost_cny,
                       0::BIGINT AS sale_count, 0::NUMERIC AS income_cny,
                       0::NUMERIC AS refund_cny
                FROM growth.ops_user_snapshots AS snapshot
                WHERE snapshot.site_id = :site_id
                  AND snapshot.registered_at >= {event_start_expression}
                  AND snapshot.registered_at < {event_end_expression}
                UNION ALL
                SELECT usage.site_id, usage.external_user_id, usage.occurred_at,
                       snapshot.is_internal, 0, usage.successful_call_count,
                       usage.consumed_balance_units, usage.cost_cny, 0,
                       CASE
                           WHEN usage.site_id = 'aigclink' AND NOT snapshot.is_internal
                           THEN usage.cost_cny
                           ELSE 0
                       END,
                       0
                FROM growth.usage_facts AS usage
                JOIN growth.ops_user_snapshots AS snapshot
                  ON snapshot.site_id = usage.site_id
                 AND snapshot.external_user_id = usage.external_user_id
                WHERE usage.site_id = :site_id
                  AND usage.occurred_at >= {event_start_expression}
                  AND usage.occurred_at < {event_end_expression}
                UNION ALL
                SELECT event.site_id, event.external_user_id, event.occurred_at,
                       snapshot.is_internal, 0, 0, 0, 0,
                       CASE WHEN event.direction = 'credit' AND event.purpose = 'sale' THEN 1 ELSE 0 END,
                       CASE
                           WHEN event.site_id <> 'aigclink'
                            AND event.direction = 'credit'
                            AND event.purpose = 'sale'
                           THEN event.cash_amount_cny
                           ELSE 0
                       END,
                       CASE WHEN event.direction = 'debit' AND event.purpose = 'sale' THEN event.cash_amount_cny ELSE 0 END
                FROM growth.credit_events AS event
                JOIN growth.ops_user_snapshots AS snapshot
                  ON snapshot.site_id = event.site_id
                 AND snapshot.external_user_id = event.external_user_id
                WHERE event.classification_status = 'classified'
                  AND event.site_id = :site_id
                  AND event.occurred_at >= {event_start_expression}
                  AND event.occurred_at < {event_end_expression}
            ), segmented AS (
                SELECT raw.*, segment.user_segment
                FROM raw_events AS raw
                CROSS JOIN LATERAL (
                    VALUES (CASE WHEN raw.is_internal THEN 'internal' ELSE 'ordinary' END), ('all')
                ) AS segment(user_segment)
            )
            INSERT INTO growth.{table_name} (
                site_id, {bucket_column}{timezone_column}, user_segment,
                registered_user_count, active_user_count, successful_call_count,
                consumed_balance_units, cost_cny, payer_count, sale_event_count,
                gross_income_cny, refund_cny, computed_at
            )
            SELECT site_id, {bucket_expression}{timezone_value}, user_segment,
                   SUM(registered_count),
                   COUNT(DISTINCT external_user_id) FILTER (WHERE call_count > 0),
                   SUM(call_count), SUM(consumed_units), SUM(cost_cny),
                   COUNT(DISTINCT external_user_id) FILTER (WHERE sale_count > 0),
                   SUM(sale_count), SUM(income_cny), SUM(refund_cny), NOW()
            FROM segmented
            GROUP BY site_id, {bucket_expression}, user_segment
            """
        ),
        {"site_id": site_id, "start_at": start_at, "end_at": end_at, "timezone": timezone},
    )


async def acquire_operations_sync_lock(connection: Any, *, site_id: str) -> None:
    await connection.execute(
        text(
            """
            SELECT pg_advisory_xact_lock(
                hashtext('growth-operations-sync:' || CAST(:site_id AS TEXT))
            )
            """
        ),
        {"site_id": site_id},
    )
