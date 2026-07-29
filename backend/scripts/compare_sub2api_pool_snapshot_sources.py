from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.sub2api.account_usage_postgres_repository import fetch_account_usage_snapshots
from app.modules.sub2api.cache import _extract_group_ids, _fetch_all_accounts, get_site
from app.modules.sub2api.client import Sub2ApiClient
from app.modules.sub2api.dashboard import dashboard_snapshot_ranges
from app.modules.sub2api.dashboard_postgres_repository import (
    fetch_group_dashboard_snapshot,
    fetch_group_hour_counters,
    fetch_model_statistics,
    fetch_site_dashboard_snapshot,
)
from app.modules.sub2api.postgres_repository import fetch_pool_snapshot


COMPARE_ACCOUNT_FIELDS = ("status", "schedulable", "priority", "concurrency", "rate_limit_reset_at")
COMPARE_GROUP_FIELDS = ("status", "account_count", "active_account_count", "rate_limited_account_count")
MAX_DIFFERENCE_EXAMPLES = 20
DASHBOARD_COMPARE_FIELDS = (
    "requests",
    "input_tokens",
    "output_tokens",
    "cache_creation_tokens",
    "cache_read_tokens",
    "total_tokens",
    "cost",
    "actual_cost",
)
ACCOUNT_USAGE_COMPARE_FIELDS = ("utilization", "resets_at", "remaining_seconds")
ACCOUNT_WINDOW_STATS_COMPARE_FIELDS = ("requests", "tokens", "cost", "standard_cost", "user_cost")


async def _fetch_http_pool_snapshot(client: Sub2ApiClient) -> dict[str, list[dict[str, Any]]]:
    groups_data, accounts = await asyncio.gather(
        client.list_groups(page=1, page_size=500),
        _fetch_all_accounts(client),
    )
    return {
        "groups": [item for item in groups_data.get("items", []) if isinstance(item, dict)],
        "accounts": accounts,
    }


def compare_snapshots(database: dict[str, Any], http: dict[str, Any]) -> dict[str, Any]:
    database_accounts = _items_by_id(database.get("accounts"))
    http_accounts = _items_by_id(http.get("accounts"))
    database_groups = _items_by_id(database.get("groups"))
    http_groups = _items_by_id(http.get("groups"))

    database_account_ids = set(database_accounts)
    http_account_ids = set(http_accounts)
    database_group_ids = set(database_groups)
    http_group_ids = set(http_groups)

    account_differences = []
    for account_id in sorted(database_account_ids & http_account_ids):
        database_account = database_accounts[account_id]
        http_account = http_accounts[account_id]
        fields = {
            field: {"database": database_account.get(field), "http": http_account.get(field)}
            for field in COMPARE_ACCOUNT_FIELDS
            if not _values_equal(database_account.get(field), http_account.get(field))
        }
        database_group_membership = _extract_group_ids(database_account)
        http_group_membership = _extract_group_ids(http_account)
        if database_group_membership != http_group_membership:
            fields["group_ids"] = {"database": database_group_membership, "http": http_group_membership}
        if fields:
            account_differences.append({"id": account_id, "fields": fields})

    group_differences = []
    for group_id in sorted(database_group_ids & http_group_ids):
        database_group = database_groups[group_id]
        http_group = http_groups[group_id]
        fields = {
            field: {"database": database_group.get(field), "http": http_group.get(field)}
            for field in COMPARE_GROUP_FIELDS
            if database_group.get(field) != http_group.get(field)
        }
        if fields:
            group_differences.append({"id": group_id, "fields": fields})

    return {
        "database": {"groups": len(database_groups), "accounts": len(database_accounts)},
        "http": {"groups": len(http_groups), "accounts": len(http_accounts)},
        "group_ids_only_in_database": sorted(database_group_ids - http_group_ids)[:MAX_DIFFERENCE_EXAMPLES],
        "group_ids_only_in_http": sorted(http_group_ids - database_group_ids)[:MAX_DIFFERENCE_EXAMPLES],
        "account_ids_only_in_database": sorted(database_account_ids - http_account_ids)[:MAX_DIFFERENCE_EXAMPLES],
        "account_ids_only_in_http": sorted(http_account_ids - database_account_ids)[:MAX_DIFFERENCE_EXAMPLES],
        "group_difference_count": len(group_differences),
        "group_difference_examples": group_differences[:MAX_DIFFERENCE_EXAMPLES],
        "account_difference_count": len(account_differences),
        "account_difference_examples": account_differences[:MAX_DIFFERENCE_EXAMPLES],
        "database_group_account_distributions": _group_account_distributions(database),
        "field_contract": {
            "top_level": _field_key_comparison(database_accounts.values(), http_accounts.values()),
            "credentials": _nested_field_key_comparison(database_accounts.values(), http_accounts.values(), "credentials"),
            "extra": _nested_field_key_comparison(database_accounts.values(), http_accounts.values(), "extra"),
        },
    }


def compare_dashboard_snapshots(database: dict[str, Any], http: dict[str, Any]) -> dict[str, Any]:
    database_points = _trend_by_bucket(database.get("trend"))
    http_points = _trend_by_bucket(http.get("trend"))
    database_buckets = set(database_points)
    http_buckets = set(http_points)
    differences = []
    for bucket in sorted(database_buckets & http_buckets):
        database_point = database_points[bucket]
        http_point = http_points[bucket]
        metrics = {
            field: {"database": database_point.get(field), "http": http_point.get(field)}
            for field in DASHBOARD_COMPARE_FIELDS
            if not _numeric_values_equal(database_point.get(field), http_point.get(field))
        }
        if metrics:
            differences.append({"bucket": bucket, "metrics": metrics})
    return {
        "database_points": len(database_points),
        "http_points": len(http_points),
        "buckets_only_in_database": sorted(database_buckets - http_buckets)[:MAX_DIFFERENCE_EXAMPLES],
        "buckets_only_in_http": sorted(http_buckets - database_buckets)[:MAX_DIFFERENCE_EXAMPLES],
        "difference_count": len(differences),
        "difference_examples": differences[:MAX_DIFFERENCE_EXAMPLES],
    }


def compare_account_usage_snapshots(
    database: dict[int, dict[str, Any]],
    http: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    database_ids = set(database)
    http_ids = set(http)
    differences = []
    for account_id in sorted(database_ids & http_ids):
        window_differences = {}
        for window in ("five_hour", "seven_day"):
            database_window = database[account_id].get(window)
            http_window = http[account_id].get(window)
            metrics = _account_usage_window_differences(database_window, http_window)
            if metrics:
                window_differences[window] = metrics
        if window_differences:
            differences.append({"id": account_id, "windows": window_differences})
    return {
        "database_accounts": len(database_ids),
        "http_accounts": len(http_ids),
        "account_ids_only_in_database": sorted(database_ids - http_ids)[:MAX_DIFFERENCE_EXAMPLES],
        "account_ids_only_in_http": sorted(http_ids - database_ids)[:MAX_DIFFERENCE_EXAMPLES],
        "difference_count": len(differences),
        "difference_examples": differences[:MAX_DIFFERENCE_EXAMPLES],
    }


def _account_usage_window_differences(database: Any, http: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(database, dict) or not isinstance(http, dict):
        return {} if database == http else {"window": {"database": database, "http": http}}
    differences = {}
    for field in ACCOUNT_USAGE_COMPARE_FIELDS:
        database_value = database.get(field)
        http_value = http.get(field)
        if field == "resets_at":
            equal = _values_equal(database_value, http_value)
        elif field == "remaining_seconds":
            equal = _seconds_values_close(database_value, http_value)
        else:
            equal = _numeric_values_equal(database_value, http_value)
        if not equal:
            differences[field] = {"database": database_value, "http": http_value}
    database_stats = database.get("window_stats") if isinstance(database.get("window_stats"), dict) else {}
    http_stats = http.get("window_stats") if isinstance(http.get("window_stats"), dict) else {}
    for field in ACCOUNT_WINDOW_STATS_COMPARE_FIELDS:
        database_value = database_stats.get(field)
        http_value = http_stats.get(field)
        if not _numeric_values_equal(database_value, http_value):
            differences[field] = {"database": database_value, "http": http_value}
    return differences


def _seconds_values_close(left: Any, right: Any) -> bool:
    try:
        return abs(int(left or 0) - int(right or 0)) <= 5
    except (TypeError, ValueError):
        return left == right


def _trend_by_bucket(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        return {}
    return {
        str(item["date"]): item
        for item in value
        if isinstance(item, dict) and item.get("date") is not None
    }


def _numeric_values_equal(left: Any, right: Any) -> bool:
    try:
        return abs(float(left or 0) - float(right or 0)) < 0.000001
    except (TypeError, ValueError):
        return left == right


def _items_by_id(value: Any) -> dict[int, dict[str, Any]]:
    result: dict[int, dict[str, Any]] = {}
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, dict):
            continue
        try:
            item_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        result[item_id] = item
    return result


def _group_account_distributions(snapshot: dict[str, Any]) -> dict[int, dict[str, Any]]:
    distributions: dict[int, dict[str, Any]] = {}
    accounts_by_group: dict[int, list[dict[str, Any]]] = {}
    for account in snapshot.get("accounts", []):
        if not isinstance(account, dict):
            continue
        for group_id in _extract_group_ids(account):
            accounts_by_group.setdefault(group_id, []).append(account)
    now = datetime.now(UTC)
    for group_id, accounts in accounts_by_group.items():
        distributions[group_id] = {
            "accounts": len(accounts),
            "status": dict(Counter(str(account.get("status")) for account in accounts)),
            "schedulable": dict(Counter(str(account.get("schedulable")) for account in accounts)),
            "session_window_status": dict(Counter(str(account.get("session_window_status")) for account in accounts)),
            "future_rate_limit_reset": sum(_is_future(account.get("rate_limit_reset_at"), now) for account in accounts),
            "future_temp_unschedulable": sum(_is_future(account.get("temp_unschedulable_until"), now) for account in accounts),
            "future_overload": sum(_is_future(account.get("overload_until"), now) for account in accounts),
        }
    return distributions


def _field_key_comparison(database_items: Any, http_items: Any) -> dict[str, list[str]]:
    database_keys = {str(key) for item in database_items for key in item}
    http_keys = {str(key) for item in http_items for key in item}
    return {
        "only_in_database": sorted(database_keys - http_keys),
        "only_in_http": sorted(http_keys - database_keys),
    }


def _nested_field_key_comparison(database_items: Any, http_items: Any, field: str) -> dict[str, list[str]]:
    database_keys = {
        str(key)
        for item in database_items
        if isinstance(item.get(field), dict)
        for key in item[field]
    }
    http_keys = {
        str(key)
        for item in http_items
        if isinstance(item.get(field), dict)
        for key in item[field]
    }
    return {
        "only_in_database": sorted(database_keys - http_keys),
        "only_in_http": sorted(http_keys - database_keys),
    }


def _is_future(value: Any, now: datetime) -> bool:
    parsed = _as_datetime(value)
    if parsed is None:
        return False
    return parsed > now


def _values_equal(left: Any, right: Any) -> bool:
    if left == right:
        return True
    left_datetime = _as_datetime(left)
    right_datetime = _as_datetime(right)
    return left_datetime is not None and right_datetime is not None and left_datetime == right_datetime


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


async def main(
    site_id: str,
    *,
    http_contract_only: bool = False,
    dashboard_only: bool = False,
    account_usage_only: bool = False,
    database_group_id: int | None = None,
    sample_size: int = 50,
) -> None:
    await connect_to_mongo()
    try:
        db = get_db()
        site = await get_site(db, site_id, include_token=True)
        if site is None:
            raise LookupError("sub2api site not found")
        sql_dsn = str(site.get("sql_dsn") or "").strip()
        if not sql_dsn:
            raise ValueError("SQL_DSN is not configured")
        client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
        if database_group_id is not None:
            ranges = dashboard_snapshot_ranges()
            snapshots = await asyncio.gather(
                *(
                    fetch_group_dashboard_snapshot(
                        sql_dsn,
                        group_id=database_group_id,
                        start_date=str(config["params"]["start_date"]),
                        end_date=str(config["params"]["end_date"]),
                        granularity=str(config["params"]["granularity"]),
                    )
                    for config in ranges
                )
            )
            models, counters = await asyncio.gather(
                fetch_model_statistics(
                    sql_dsn,
                    group_id=database_group_id,
                    start_date=str(ranges[-1]["params"]["start_date"]),
                    end_date=str(ranges[-1]["params"]["end_date"]),
                ),
                fetch_group_hour_counters(
                    sql_dsn,
                    group_ids=[database_group_id],
                    sampled_at=datetime.now(UTC),
                ),
            )
            print(
                json.dumps(
                    {
                        "site_id": site_id,
                        "group_id": database_group_id,
                        "data_source": "postgresql",
                        "ranges": [
                            {
                                "range_type": config["range_type"],
                                "granularity": snapshot["granularity"],
                                "trend_points": len(snapshot["trend"]),
                            }
                            for config, snapshot in zip(ranges, snapshots, strict=True)
                        ],
                        "models": len(models),
                        "current_hour_counters": counters.get(database_group_id),
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return
        if account_usage_only:
            all_accounts = [
                doc["account"]
                async for doc in db.sub2api_accounts_cache.find(
                    {"site_id": site_id, "account.id": {"$ne": None}},
                    {"account": 1},
                )
                if isinstance(doc.get("account"), dict)
            ]
            if not all_accounts:
                pool_snapshot = await fetch_pool_snapshot(sql_dsn)
                all_accounts = [
                    account
                    for account in pool_snapshot.get("accounts", [])
                    if isinstance(account, dict) and account.get("id") is not None
                ]
            accounts = _account_usage_sample(all_accounts, sample_size)
            observed_at = datetime.now(UTC)
            database_usage, http_result = await asyncio.gather(
                fetch_account_usage_snapshots(sql_dsn, accounts=accounts, observed_at=observed_at),
                _fetch_http_account_usage_sample(client, accounts),
            )
            http_usage, http_errors = http_result
            print(
                json.dumps(
                    {
                        "site_id": site_id,
                        "sample_size": len(accounts),
                        "http_errors": http_errors,
                        **compare_account_usage_snapshots(database_usage, http_usage),
                    },
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
            return
        if dashboard_only:
            comparisons = []
            for range_config in dashboard_snapshot_ranges():
                params = range_config["params"]
                database_snapshot, http_snapshot = await asyncio.gather(
                    fetch_site_dashboard_snapshot(
                        sql_dsn,
                        start_date=str(params["start_date"]),
                        end_date=str(params["end_date"]),
                        granularity=str(params["granularity"]),
                    ),
                    client.get_dashboard_snapshot(**params),
                )
                comparisons.append(
                    {
                        "range_type": range_config["range_type"],
                        "granularity": params["granularity"],
                        **compare_dashboard_snapshots(database_snapshot, http_snapshot),
                    }
                )
            print(json.dumps({"site_id": site_id, "ranges": comparisons}, ensure_ascii=False, indent=2, default=str))
            return
        if http_contract_only:
            http_snapshot = await _fetch_http_pool_snapshot(client)
            http_accounts = _items_by_id(http_snapshot.get("accounts"))
            empty_accounts: list[dict[str, Any]] = []
            print(
                json.dumps(
                    {
                        "top_level_keys": sorted({str(key) for item in http_accounts.values() for key in item}),
                        "credentials_keys": _nested_field_key_comparison(empty_accounts, http_accounts.values(), "credentials")["only_in_http"],
                        "extra_keys": _nested_field_key_comparison(empty_accounts, http_accounts.values(), "extra")["only_in_http"],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        database_snapshot, http_snapshot = await asyncio.gather(
            fetch_pool_snapshot(sql_dsn),
            _fetch_http_pool_snapshot(client),
        )
        print(json.dumps(compare_snapshots(database_snapshot, http_snapshot), ensure_ascii=False, indent=2, default=str))
    finally:
        await close_mongo_connection()


async def _fetch_http_account_usage_sample(
    client: Sub2ApiClient,
    accounts: list[dict[str, Any]],
) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    limits = httpx.Limits(max_connections=20, max_keepalive_connections=20)
    async with httpx.AsyncClient(timeout=20, limits=limits) as http_client:
        results = await asyncio.gather(
            *(
                client.get_account_usage(account["id"], timezone="Asia/Shanghai", http_client=http_client)
                for account in accounts
            ),
            return_exceptions=True,
        )
    snapshots: dict[int, dict[str, Any]] = {}
    errors = []
    for account, result in zip(accounts, results, strict=True):
        account_id = int(account["id"])
        if isinstance(result, BaseException):
            errors.append({"id": account_id, "error": str(result)})
        elif isinstance(result, dict):
            snapshots[account_id] = result
    return snapshots, errors


def _account_usage_sample(accounts: list[dict[str, Any]], sample_size: int) -> list[dict[str, Any]]:
    def usage_score(account: dict[str, Any]) -> int:
        extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
        has_five_hour = account.get("codex_5h_used_percent") is not None or extra.get("codex_5h_used_percent") is not None
        has_seven_day = account.get("codex_7d_used_percent") is not None or extra.get("codex_7d_used_percent") is not None
        return int(has_five_hour) + int(has_seven_day)

    prioritized = sorted(accounts, key=usage_score, reverse=True)
    return prioritized[: max(1, sample_size)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare non-sensitive Sub2API pool snapshot fields across PostgreSQL and HTTP")
    parser.add_argument("site_id", help="Configured account-pool site ID")
    parser.add_argument("--http-contract-only", action="store_true", help="Print only HTTP response field names")
    parser.add_argument("--dashboard", action="store_true", help="Compare site-wide PostgreSQL and HTTP dashboard trends")
    parser.add_argument("--account-usage", action="store_true", help="Compare a sample of account usage windows")
    parser.add_argument("--database-group", type=int, help="Validate PostgreSQL group trends, models and current-hour counters")
    parser.add_argument("--sample-size", type=int, default=50, help="Account usage comparison sample size")
    arguments = parser.parse_args()
    asyncio.run(
        main(
            arguments.site_id,
            http_contract_only=arguments.http_contract_only,
            dashboard_only=arguments.dashboard,
            account_usage_only=arguments.account_usage,
            database_group_id=arguments.database_group,
            sample_size=arguments.sample_size,
        )
    )
