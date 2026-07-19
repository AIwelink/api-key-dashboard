from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.sub2api.cache import _extract_group_ids, _fetch_http_pool_snapshot, get_site
from app.modules.sub2api.client import Sub2ApiClient
from app.modules.sub2api.postgres_repository import fetch_pool_snapshot


COMPARE_ACCOUNT_FIELDS = ("status", "schedulable", "priority", "concurrency", "rate_limit_reset_at")
COMPARE_GROUP_FIELDS = ("status", "account_count", "active_account_count", "rate_limited_account_count")
MAX_DIFFERENCE_EXAMPLES = 20


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


async def main(site_id: str, *, http_contract_only: bool = False) -> None:
    await connect_to_mongo()
    try:
        site = await get_site(get_db(), site_id, include_token=True)
        if site is None:
            raise LookupError("sub2api site not found")
        sql_dsn = str(site.get("sql_dsn") or "").strip()
        if not sql_dsn:
            raise ValueError("SQL_DSN is not configured")
        client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
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


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Compare non-sensitive Sub2API pool snapshot fields across PostgreSQL and HTTP")
    parser.add_argument("site_id", help="Configured account-pool site ID")
    parser.add_argument("--http-contract-only", action="store_true", help="Print only HTTP response field names")
    arguments = parser.parse_args()
    asyncio.run(main(arguments.site_id, http_contract_only=arguments.http_contract_only))
