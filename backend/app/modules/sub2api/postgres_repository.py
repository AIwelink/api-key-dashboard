from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Callable

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.system.sql_dsn import parse_sql_dsn


DATABASE_READ_TIMEOUT_SECONDS = 20
ADMIN_API_KEY_QUERY = "SELECT value FROM settings WHERE key = 'admin_api_key' LIMIT 1"

GROUP_COLUMNS = (
    "id",
    "name",
    "platform",
    "status",
    "sort_order",
    "description",
    "subscription_type",
    "rpm_limit",
    "daily_limit_usd",
    "weekly_limit_usd",
    "monthly_limit_usd",
    "rate_multiplier",
    "peak_rate_enabled",
    "peak_rate_multiplier",
    "peak_start",
    "peak_end",
    "model_routing_enabled",
    "model_routing",
    "models_list_config",
    "supported_model_scopes",
    "default_mapped_model",
    "fallback_group_id",
    "fallback_group_id_on_invalid_request",
    "require_oauth_only",
    "require_privacy_set",
    "is_exclusive",
    "claude_code_only",
    "allow_messages_dispatch",
    "messages_dispatch_model_config",
    "mcp_xml_inject",
    "allow_image_generation",
    "allow_batch_image_generation",
    "image_rate_independent",
    "image_rate_multiplier",
    "image_price_1k",
    "image_price_2k",
    "image_price_4k",
    "batch_image_hold_multiplier",
    "batch_image_discount_multiplier",
    "video_rate_independent",
    "video_rate_multiplier",
    "video_price_480p",
    "video_price_720p",
    "video_price_1080p",
    "web_search_price_per_call",
    "default_validity_days",
    "created_at",
    "updated_at",
)

ACCOUNT_COLUMNS = (
    "id",
    "name",
    "platform",
    "type",
    "status",
    "schedulable",
    "priority",
    "concurrency",
    "load_factor",
    "quota_dimension",
    "rate_multiplier",
    "auto_pause_on_expired",
    "notes",
    "last_used_at",
    "rate_limited_at",
    "rate_limit_reset_at",
    "overload_until",
    "temp_unschedulable_reason",
    "temp_unschedulable_until",
    "session_window_start",
    "session_window_end",
    "session_window_status",
    "expires_at",
    "error_message",
    "proxy_id",
    "proxy_fallback_origin_id",
    "parent_account_id",
    "created_at",
    "updated_at",
)

CACHED_CREDENTIAL_FIELDS = frozenset(
    {
        "_token_version",
        "account_id",
        "account_uuid",
        "auth_mode",
        "base_url",
        "chatgpt_account_id",
        "chatgpt_account_is_fedramp",
        "chatgpt_user_id",
        "client_id",
        "custom_error_codes",
        "custom_error_codes_enabled",
        "disabled",
        "email",
        "email_address",
        "expires_at",
        "expires_in",
        "intercept_warmup_requests",
        "k12_reverify_enabled",
        "last_refresh",
        "model_mapping",
        "openai_auth_mode",
        "org_uuid",
        "organization_id",
        "plan_type",
        "pool_mode",
        "pool_mode_retry_count",
        "project_id",
        "scope",
        "subscription_expires_at",
        "temp_unschedulable_enabled",
        "temp_unschedulable_rules",
        "token_type",
        "type",
        "user_agent",
        "workspace_id",
    }
)

CACHED_EXTRA_FIELDS = frozenset(
    {
        "2FA",
        "2fa",
        "account_claims_email",
        "account_id",
        "account_status",
        "account_type",
        "account_uuid",
        "auth_provider",
        "auto_pause_5h_disabled",
        "auto_pause_7d_disabled",
        "auto_pause_on_expired",
        "base_rpm",
        "batch_code",
        "batch_index",
        "chatgpt_account_id",
        "chatgpt_subscription_active_until",
        "chatgpt_user_id",
        "client_id",
        "codex_5h_actual_cost",
        "codex_5h_request_count",
        "codex_5h_reset_after_seconds",
        "codex_5h_reset_at",
        "codex_5h_token_count",
        "codex_5h_total_cost",
        "codex_5h_usage_updated_at",
        "codex_5h_used_percent",
        "codex_5h_user_cost",
        "codex_5h_window_minutes",
        "codex_7d_actual_cost",
        "codex_7d_request_count",
        "codex_7d_reset_after_seconds",
        "codex_7d_reset_at",
        "codex_7d_token_count",
        "codex_7d_total_cost",
        "codex_7d_used_percent",
        "codex_7d_user_cost",
        "codex_7d_window_minutes",
        "codex_plan_type_source",
        "codex_primary_over_secondary_percent",
        "codex_primary_reset_after_seconds",
        "codex_primary_used_percent",
        "codex_primary_window_minutes",
        "codex_secondary_reset_after_seconds",
        "codex_secondary_used_percent",
        "codex_secondary_window_minutes",
        "codex_total_actual_cost",
        "codex_total_cost",
        "codex_total_request_count",
        "codex_total_token_count",
        "codex_usage_updated_at",
        "concurrency",
        "converted_from",
        "created_at",
        "credential_expires_at",
        "credentials_status",
        "current_concurrency",
        "db_id",
        "email",
        "email_address",
        "email_key",
        "email_session",
        "enable_tls_fingerprint",
        "error",
        "error_message",
        "expired",
        "expires_at",
        "import_template",
        "is_schedulable",
        "k12_reverify_enabled",
        "last_error",
        "last_refresh",
        "last_used",
        "last_used_at",
        "load_factor",
        "login_identity",
        "mailbox",
        "mailbox_connection",
        "mailbox_url",
        "manual_status_label",
        "max_concurrency",
        "max_sessions",
        "message",
        "model_rate_limits",
        "name",
        "no_rt",
        "notes",
        "openai_apikey_responses_websockets_v2_enabled",
        "openai_apikey_responses_websockets_v2_mode",
        "openai_long_context_billing_enabled",
        "openai_oauth_responses_websockets_v2_enabled",
        "openai_oauth_responses_websockets_v2_mode",
        "openai_responses_supported",
        "org_uuid",
        "organization_id",
        "overload_until",
        "passive_usage_7d_reset",
        "passive_usage_7d_utilization",
        "passive_usage_sampled_at",
        "payment_type",
        "phone",
        "phone_bound",
        "phone_number",
        "plan_type",
        "platform",
        "priority",
        "privacy_mode",
        "project_id",
        "proxy_id",
        "public_ref",
        "purchase_source",
        "rate_limit_reset_at",
        "rate_limited_at",
        "rate_multiplier",
        "remark",
        "rpm_sticky_buffer",
        "rpm_strategy",
        "schedulable",
        "self_produced",
        "session_id_masking_enabled",
        "session_idle_timeout_minutes",
        "session_window_end",
        "session_window_start",
        "session_window_status",
        "session_window_utilization",
        "source",
        "source_file",
        "source_order_no",
        "source_target_id",
        "source_template",
        "source_workspace_id",
        "state",
        "status",
        "sub2api_schedulable",
        "sub2api_status",
        "subscription_expires_at",
        "temp_unschedulable_reason",
        "temp_unschedulable_until",
        "tls_fingerprint_profile_id",
        "totp_secret",
        "two_fa",
        "type",
        "used_concurrency",
        "user_msg_queue_mode",
        "version",
        "window_cost_limit",
        "window_cost_sticky_reserve",
        "workspace_id",
    }
)


def _jsonb_allowlist_expression(column: str, fields: frozenset[str]) -> str:
    sorted_fields = sorted(fields)
    chunks = []
    for start in range(0, len(sorted_fields), 40):
        pairs = ", ".join(
            f"'{field}', {column} -> '{field}'"
            for field in sorted_fields[start : start + 40]
        )
        chunks.append(f"jsonb_build_object({pairs})")
    return f"jsonb_strip_nulls({' || '.join(chunks)}) AS {column}"

GROUPS_QUERY = f"""
SELECT {", ".join(GROUP_COLUMNS)}
FROM groups
WHERE deleted_at IS NULL
ORDER BY sort_order ASC, id ASC
"""

ACCOUNTS_QUERY = f"""
SELECT {", ".join(ACCOUNT_COLUMNS)},
       {_jsonb_allowlist_expression("credentials", CACHED_CREDENTIAL_FIELDS)},
       {_jsonb_allowlist_expression("extra", CACHED_EXTRA_FIELDS)}
FROM accounts
WHERE deleted_at IS NULL
ORDER BY id ASC
"""

ACCOUNT_GROUPS_QUERY = """
SELECT ag.account_id, ag.group_id, ag.priority, ag.created_at
FROM account_groups AS ag
JOIN accounts AS a ON a.id = ag.account_id AND a.deleted_at IS NULL
JOIN groups AS g ON g.id = ag.group_id AND g.deleted_at IS NULL
ORDER BY ag.account_id ASC, ag.group_id ASC
"""


async def fetch_admin_api_key(
    sql_dsn: str,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> str:
    parsed = parse_sql_dsn(sql_dsn, "postgresql")
    engine = None
    try:
        engine = engine_factory(
            parsed.driver_url(),
            poolclass=NullPool,
            connect_args=parsed.connect_args(DATABASE_READ_TIMEOUT_SECONDS),
        )
        async with asyncio.timeout(DATABASE_READ_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                result = await connection.execute(text(ADMIN_API_KEY_QUERY))
                value = result.scalar_one_or_none()
        admin_api_key = str(value or "").strip()
        if not admin_api_key:
            raise ValueError("Sub2API PostgreSQL settings.admin_api_key is not configured")
        return admin_api_key
    finally:
        if engine is not None:
            await engine.dispose()


async def fetch_groups(
    sql_dsn: str,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> list[dict[str, Any]]:
    parsed = parse_sql_dsn(sql_dsn, "postgresql")
    engine = None
    try:
        engine = engine_factory(
            parsed.driver_url(),
            poolclass=NullPool,
            connect_args=parsed.connect_args(DATABASE_READ_TIMEOUT_SECONDS),
        )
        async with asyncio.timeout(DATABASE_READ_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                result = await connection.execute(text(GROUPS_QUERY))
                return [_normalize_row(row) for row in result.mappings().all()]
    finally:
        if engine is not None:
            await engine.dispose()


async def fetch_pool_snapshot(
    sql_dsn: str,
    *,
    engine_factory: Callable[..., Any] = create_async_engine,
) -> dict[str, list[dict[str, Any]]]:
    parsed = parse_sql_dsn(sql_dsn, "postgresql")
    engine = None
    try:
        engine = engine_factory(
            parsed.driver_url(),
            poolclass=NullPool,
            connect_args=parsed.connect_args(DATABASE_READ_TIMEOUT_SECONDS),
            isolation_level="REPEATABLE READ",
        )
        async with asyncio.timeout(DATABASE_READ_TIMEOUT_SECONDS):
            async with engine.connect() as connection:
                groups_result = await connection.execute(text(GROUPS_QUERY))
                accounts_result = await connection.execute(text(ACCOUNTS_QUERY))
                relations_result = await connection.execute(text(ACCOUNT_GROUPS_QUERY))
                groups = [_normalize_row(row) for row in groups_result.mappings().all()]
                accounts = [_sanitize_account_row(_normalize_row(row)) for row in accounts_result.mappings().all()]
                relations = [_normalize_row(row) for row in relations_result.mappings().all()]
        return _merge_pool_snapshot(groups, accounts, relations)
    finally:
        if engine is not None:
            await engine.dispose()


def _merge_pool_snapshot(
    groups: list[dict[str, Any]],
    accounts: list[dict[str, Any]],
    relations: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    groups_by_id = {group.get("id"): group for group in groups if group.get("id") is not None}
    accounts_by_id = {account.get("id"): account for account in accounts if account.get("id") is not None}
    relations_by_account: dict[Any, list[dict[str, Any]]] = defaultdict(list)

    for relation in relations:
        account_id = relation.get("account_id")
        group_id = relation.get("group_id")
        if account_id not in accounts_by_id or group_id not in groups_by_id:
            continue
        relations_by_account[account_id].append(relation)

    for account in accounts:
        account_relations = relations_by_account.get(account.get("id"), [])
        account["account_groups"] = account_relations
        account["group_ids"] = [relation["group_id"] for relation in account_relations]
        account["groups"] = [
            _account_group_snapshot(groups_by_id[relation["group_id"]])
            for relation in account_relations
        ]

    accounts_by_group: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for account in accounts:
        for group_id in account.get("group_ids", []):
            accounts_by_group[group_id].append(account)

    for group in groups:
        group_accounts = accounts_by_group.get(group.get("id"), [])
        account_count = len(group_accounts)
        active_account_count = sum(1 for account in group_accounts if _is_active_schedulable(account))
        rate_limited_account_count = sum(1 for account in group_accounts if _is_rate_limited(account))
        if account_count:
            group["account_count"] = account_count
        if active_account_count:
            group["active_account_count"] = active_account_count
        if rate_limited_account_count:
            group["rate_limited_account_count"] = rate_limited_account_count

    return {"groups": groups, "accounts": accounts}


def _account_group_snapshot(group: dict[str, Any]) -> dict[str, Any]:
    return {
        key: group.get(key)
        for key in ("id", "name", "platform", "status")
        if group.get(key) is not None
    }


def _is_active_schedulable(account: dict[str, Any]) -> bool:
    return _is_schedulable_candidate(account) and not _has_active_rate_limit(account)


def _is_rate_limited(account: dict[str, Any]) -> bool:
    return _is_schedulable_candidate(account) and _has_active_rate_limit(account)


def _is_schedulable_candidate(account: dict[str, Any]) -> bool:
    return str(account.get("status") or "").strip().lower() == "active" and account.get("schedulable") is True


def _has_active_rate_limit(account: dict[str, Any]) -> bool:
    value = account.get("rate_limit_reset_at")
    if isinstance(value, datetime):
        reset_at = value
    elif isinstance(value, str):
        try:
            reset_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
    else:
        return False
    if reset_at.tzinfo is None:
        reset_at = reset_at.replace(tzinfo=UTC)
    return reset_at > datetime.now(UTC)


def _normalize_row(row: Any) -> dict[str, Any]:
    return {str(key): _normalize_value(value) for key, value in dict(row).items()}


def _normalize_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {str(key): _normalize_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value


def _sanitize_account_row(account: dict[str, Any]) -> dict[str, Any]:
    account["credentials"] = _allowed_json_fields(account.get("credentials"), CACHED_CREDENTIAL_FIELDS)
    account["extra"] = _allowed_json_fields(account.get("extra"), CACHED_EXTRA_FIELDS)
    return account


def _allowed_json_fields(value: Any, allowed: frozenset[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if str(key) in allowed}
