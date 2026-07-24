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
    site_id: str | None = None,
    query: str | None = None,
) -> list[dict[str, Any]]:
    result = await connection.execute(
        text(
            """
            SELECT internal_user.*
            FROM growth.internal_users AS internal_user
            WHERE (CAST(:site_id AS TEXT) IS NULL OR internal_user.site_id = :site_id)
              AND (
                  CAST(:query AS TEXT) IS NULL
                  OR internal_user.external_user_id ILIKE '%' || :query || '%'
                  OR internal_user.account_label ILIKE '%' || :query || '%'
              )
            ORDER BY internal_user.created_at DESC, internal_user.internal_user_id
            """
        ),
        {"site_id": site_id, "query": query.strip() if query else None},
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
            INSERT INTO growth.internal_users (
                internal_user_id, site_id, external_user_id, account_label, reason,
                active_from, active_until, created_by, updated_by
            ) VALUES (
                :internal_user_id, :site_id, :external_user_id, :account_label, :reason,
                :active_from, :active_until, :actor_id, :actor_id
            )
            RETURNING *
            """
        ),
        {"internal_user_id": selected_id, "actor_id": actor_id, **values},
    )
    row = _one(result)
    await connection.execute(
        text(
            """
            UPDATE growth.ops_user_snapshots
            SET is_internal = TRUE, internal_user_id = :internal_user_id, synced_at = NOW()
            WHERE site_id = :site_id AND external_user_id = :external_user_id
            """
        ),
        {
            "internal_user_id": selected_id,
            "site_id": payload.site_id,
            "external_user_id": payload.external_user_id,
        },
    )
    return row or {}


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
                "SELECT * FROM growth.internal_users WHERE internal_user_id = :internal_user_id"
            ),
            {"internal_user_id": internal_user_id},
        )
    else:
        assignments = ", ".join(f"{field} = :{field}" for field in updates)
        result = await connection.execute(
            text(
                f"""
                UPDATE growth.internal_users
                SET {assignments}, updated_by = :actor_id, updated_at = NOW()
                WHERE internal_user_id = :internal_user_id
                RETURNING *
                """
            ),
            {"internal_user_id": internal_user_id, "actor_id": actor_id, **updates},
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
    site_id: str | None = None,
) -> list[dict[str, Any]]:
    result = await connection.execute(
        text(
            """
            SELECT * FROM growth.balance_conversion_rates
            WHERE CAST(:site_id AS TEXT) IS NULL OR site_id = :site_id
            ORDER BY site_id, effective_from DESC
            """
        ),
        {"site_id": site_id},
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


async def upsert_user_snapshots(connection: Any, records: list[Any]) -> int:
    if not records:
        return 0
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
    return len(parameters)


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
            INSERT INTO growth.credit_events (
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
                purpose = EXCLUDED.purpose,
                classification_status = EXCLUDED.classification_status,
                balance_units = EXCLUDED.balance_units,
                cash_amount_cny = EXCLUDED.cash_amount_cny,
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
    site_id: str | None = None,
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
            WHERE (CAST(:site_id AS TEXT) IS NULL OR task.site_id = :site_id)
              AND task.status = :status
            ORDER BY task.created_at DESC
            """
        ),
        {"site_id": site_id, "status": status},
    )
    return _all(result)


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
    site_id: str | None,
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
                WHERE (CAST(:site_id AS TEXT) IS NULL OR snapshot.site_id = :site_id)
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
                       COALESCE(SUM(usage.cost_cny), 0) AS cost_cny
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
                           WHERE event.direction = 'credit' AND event.purpose = 'sale'
                       ), 0) AS gross_income_cny,
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
            SELECT user_metrics.*, usage_metrics.*, credit_metrics.*,
                   credit_metrics.gross_income_cny - credit_metrics.refund_cny AS net_income_cny
            FROM user_metrics, usage_metrics, credit_metrics
            """
        ),
        {
            "site_id": site_id,
            "segment": segment,
            "start_at": start_at,
            "end_at": end_at,
        },
    )
    return _one(result) or {}


async def get_operations_trends(
    connection: Any,
    *,
    site_id: str | None,
    segment: str,
    start_at: datetime,
    end_at: datetime,
) -> list[dict[str, Any]]:
    hourly = end_at - start_at <= timedelta(hours=48)
    table_name = "ops_hourly_stats" if hourly else "ops_daily_stats"
    bucket_name = "bucket_start" if hourly else "bucket_date"
    result = await connection.execute(
        text(
            f"""
            SELECT {bucket_name} AS bucket, site_id, user_segment,
                   registered_user_count, active_user_count, successful_call_count,
                   consumed_balance_units, cost_cny, payer_count, sale_event_count,
                   gross_income_cny, refund_cny,
                   gross_income_cny - refund_cny AS net_income_cny, computed_at
            FROM growth.{table_name}
            WHERE (CAST(:site_id AS TEXT) IS NULL OR site_id = :site_id)
              AND user_segment = :segment
              AND {bucket_name} >= :start_at AND {bucket_name} < :end_at
            ORDER BY {bucket_name}, site_id
            """
        ),
        {
            "site_id": site_id,
            "segment": segment,
            "start_at": start_at,
            "end_at": end_at,
        },
    )
    return _all(result)


async def list_operations_users(
    connection: Any,
    *,
    site_id: str | None,
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
            WHERE (CAST(:site_id AS TEXT) IS NULL OR snapshot.site_id = :site_id)
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
            "site_id": site_id,
            "segment": segment,
            "start_at": start_at,
            "end_at": end_at,
            "query": query.strip() if query else None,
            "limit": min(max(limit, 1), 500),
            "offset": max(offset, 0),
        },
    )
    return _all(result)


async def get_sync_status(connection: Any) -> list[dict[str, Any]]:
    result = await connection.execute(
        text(
            """
            SELECT DISTINCT ON (site_id)
                   site_id, run_id, adapter_name, status, started_at, finished_at,
                   rows_scanned, rows_upserted, rows_rejected, error_code, error_message
            FROM growth.sync_runs
            WHERE stream_name = 'operations'
            ORDER BY site_id, started_at DESC
            """
        )
    )
    return _all(result)


async def replace_affected_aggregates(
    connection: Any,
    *,
    start_at: datetime,
    end_at: datetime,
    timezone: str = "Asia/Shanghai",
) -> None:
    await _replace_aggregate_table(
        connection,
        table_name="ops_hourly_stats",
        bucket_expression="date_trunc('hour', event_at)",
        bucket_column="bucket_start",
        start_at=start_at,
        end_at=end_at,
        timezone=timezone,
    )
    await _replace_aggregate_table(
        connection,
        table_name="ops_daily_stats",
        bucket_expression="(event_at AT TIME ZONE :timezone)::date",
        bucket_column="bucket_date",
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
            WHERE {bucket_column} >= :start_at AND {bucket_column} < :end_at
              {"AND timezone = :timezone" if table_name == "ops_daily_stats" else ""}
            """
        ),
        {"start_at": start_at, "end_at": end_at, "timezone": timezone},
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
                WHERE snapshot.registered_at >= :start_at AND snapshot.registered_at < :end_at
                UNION ALL
                SELECT usage.site_id, usage.external_user_id, usage.occurred_at,
                       snapshot.is_internal, 0, usage.successful_call_count,
                       usage.consumed_balance_units, usage.cost_cny, 0, 0, 0
                FROM growth.usage_facts AS usage
                JOIN growth.ops_user_snapshots AS snapshot
                  ON snapshot.site_id = usage.site_id
                 AND snapshot.external_user_id = usage.external_user_id
                WHERE usage.occurred_at >= :start_at AND usage.occurred_at < :end_at
                UNION ALL
                SELECT event.site_id, event.external_user_id, event.occurred_at,
                       snapshot.is_internal, 0, 0, 0, 0,
                       CASE WHEN event.direction = 'credit' AND event.purpose = 'sale' THEN 1 ELSE 0 END,
                       CASE WHEN event.direction = 'credit' AND event.purpose = 'sale' THEN event.cash_amount_cny ELSE 0 END,
                       CASE WHEN event.direction = 'debit' AND event.purpose = 'sale' THEN event.cash_amount_cny ELSE 0 END
                FROM growth.credit_events AS event
                JOIN growth.ops_user_snapshots AS snapshot
                  ON snapshot.site_id = event.site_id
                 AND snapshot.external_user_id = event.external_user_id
                WHERE event.classification_status = 'classified'
                  AND event.occurred_at >= :start_at AND event.occurred_at < :end_at
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
        {"start_at": start_at, "end_at": end_at, "timezone": timezone},
    )
