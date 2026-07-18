from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

from bson import BSON
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ReplaceOne, UpdateOne


CHANGE_RETENTION_DAYS = 30
CHECKPOINT_RETENTION_DAYS = 365
MAX_BATCH_ENTRIES = 500
MAX_BATCH_BSON_BYTES = 8 * 1024 * 1024
CHANGE_SCHEMA_VERSION = 1
CHECKPOINT_SCHEMA_VERSION = 1
SHANGHAI_TZ = timezone(timedelta(hours=8))


def dynamic_snapshot(account: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "usage": dict(account.get("usage_snapshot") if isinstance(account.get("usage_snapshot"), dict) else {}),
        "subscription": dict(
            account.get("subscription_snapshot")
            if isinstance(account.get("subscription_snapshot"), dict)
            else {}
        ),
    }


def snapshot_hash(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=True, sort_keys=True, separators=(",", ":"), default=_json_value)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_history_change(
    *,
    identity_id: str,
    remote_account_id: Any,
    previous: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any] | None:
    previous = _normalized_dynamic_snapshot(previous)
    current = _normalized_dynamic_snapshot(current)
    changes: dict[str, Any] = {}
    unset: list[str] = []
    for section in ("usage", "subscription"):
        previous_values = previous[section]
        current_values = current[section]
        for field in sorted(set(previous_values) | set(current_values)):
            path = f"{section}.{field}"
            if field not in current_values:
                unset.append(path)
            elif field not in previous_values or previous_values[field] != current_values[field]:
                changes[path] = current_values[field]
    if not changes and not unset:
        return None
    previous_hash = snapshot_hash(previous)
    new_hash = snapshot_hash(current)
    event_source = f"{identity_id}:{previous_hash}:{new_hash}"
    return {
        "event_id": hashlib.sha256(event_source.encode("utf-8")).hexdigest(),
        "identity_id": identity_id,
        "remote_account_id": remote_account_id,
        "changes": changes,
        "unset": unset,
        "previous_state_hash": previous_hash,
        "new_state_hash": new_hash,
        "_new_state": current,
    }


def public_change_entry(change: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in change.items() if not key.startswith("_")}


def chunk_history_changes(
    changes: list[dict[str, Any]],
    *,
    site_id: str,
    run_id: str,
    observed_at: datetime,
    max_entries: int = MAX_BATCH_ENTRIES,
    max_bson_bytes: int = MAX_BATCH_BSON_BYTES,
) -> list[dict[str, Any]]:
    if not changes:
        return []
    observed_at = _as_utc(observed_at)
    batches: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []
    for change in changes:
        entry = public_change_entry(change)
        candidate = [*entries, entry]
        candidate_document = _change_batch_document(
            site_id=site_id,
            run_id=run_id,
            chunk_index=len(batches),
            observed_at=observed_at,
            entries=candidate,
        )
        exceeds_count = len(candidate) > max_entries
        exceeds_size = len(BSON.encode(candidate_document)) > max_bson_bytes
        if entries and (exceeds_count or exceeds_size):
            batches.append(
                _change_batch_document(
                    site_id=site_id,
                    run_id=run_id,
                    chunk_index=len(batches),
                    observed_at=observed_at,
                    entries=entries,
                )
            )
            entries = [entry]
        else:
            entries = candidate
    if entries:
        batches.append(
            _change_batch_document(
                site_id=site_id,
                run_id=run_id,
                chunk_index=len(batches),
                observed_at=observed_at,
                entries=entries,
            )
        )
    return batches


def apply_history_entries(base: dict[str, Any], entries: list[dict[str, Any]]) -> dict[str, Any]:
    state = _normalized_dynamic_snapshot(copy.deepcopy(base))
    seen_event_ids: set[str] = set()
    for entry in entries:
        event_id = str(entry.get("event_id") or "")
        if event_id and event_id in seen_event_ids:
            continue
        if event_id:
            seen_event_ids.add(event_id)
        for path in entry.get("unset") or []:
            section, field = _split_path(path)
            state.setdefault(section, {}).pop(field, None)
        changes = entry.get("changes") if isinstance(entry.get("changes"), dict) else {}
        for path, value in changes.items():
            section, field = _split_path(path)
            state.setdefault(section, {})[field] = value
    return state


async def persist_history_changes(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    run_id: str,
    observed_at: datetime,
    changes: list[dict[str, Any]],
) -> dict[str, Any]:
    batches = chunk_history_changes(
        changes,
        site_id=site_id,
        run_id=run_id,
        observed_at=observed_at,
    )
    if not batches:
        return {"changed_accounts": 0, "changed_fields": 0, "batches": 0, "baselines_advanced": 0}
    await db.remote_account_change_batches.bulk_write(
        [ReplaceOne({"_id": batch["_id"]}, batch, upsert=True) for batch in batches],
        ordered=False,
    )
    baseline_ops = [
        UpdateOne(
            {
                "_id": change["identity_id"],
                "history_baseline_hash": change["previous_state_hash"],
            },
            {
                "$set": {
                    "history_baseline_snapshot": change["_new_state"],
                    "history_baseline_hash": change["new_state_hash"],
                    "history_baseline_confirmed_at": _as_utc(observed_at),
                }
            },
        )
        for change in changes
    ]
    baseline_result = await db.remote_account_identities.bulk_write(baseline_ops, ordered=False)
    return {
        "changed_accounts": len(changes),
        "changed_fields": sum(len(change.get("changes") or {}) + len(change.get("unset") or []) for change in changes),
        "batches": len(batches),
        "baselines_advanced": int(getattr(baseline_result, "modified_count", 0) or 0),
    }


def build_daily_checkpoint_documents(
    identities: list[dict[str, Any]],
    *,
    site_id: str,
    checkpoint_at: datetime,
    max_entries: int = MAX_BATCH_ENTRIES,
    max_bson_bytes: int = MAX_BATCH_BSON_BYTES,
) -> list[dict[str, Any]]:
    checkpoint_at = _as_utc(checkpoint_at)
    local_date = checkpoint_at.astimezone(SHANGHAI_TZ).date().isoformat()
    entries = [
        {
            "identity_id": str(identity.get("_id")),
            "usage": dict(
                identity.get("last_usage_snapshot")
                if isinstance(identity.get("last_usage_snapshot"), dict)
                else {}
            ),
            "subscription": dict(
                identity.get("current_subscription_snapshot")
                if isinstance(identity.get("current_subscription_snapshot"), dict)
                else {}
            ),
            "cumulative_usage": dict(
                identity.get("cumulative_usage_snapshot")
                if isinstance(identity.get("cumulative_usage_snapshot"), dict)
                else {}
            ),
        }
        for identity in identities
        if identity.get("_id")
    ]
    documents: list[dict[str, Any]] = []
    chunk: list[dict[str, Any]] = []
    for entry in entries:
        candidate = [*chunk, entry]
        candidate_document = _checkpoint_document(
            site_id=site_id,
            local_date=local_date,
            checkpoint_at=checkpoint_at,
            chunk_index=len(documents),
            entries=candidate,
        )
        if chunk and (len(candidate) > max_entries or len(BSON.encode(candidate_document)) > max_bson_bytes):
            documents.append(
                _checkpoint_document(
                    site_id=site_id,
                    local_date=local_date,
                    checkpoint_at=checkpoint_at,
                    chunk_index=len(documents),
                    entries=chunk,
                )
            )
            chunk = [entry]
        else:
            chunk = candidate
    if chunk:
        documents.append(
            _checkpoint_document(
                site_id=site_id,
                local_date=local_date,
                checkpoint_at=checkpoint_at,
                chunk_index=len(documents),
                entries=chunk,
            )
        )
    return documents


async def ensure_daily_checkpoint(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    checkpoint_at: datetime,
) -> dict[str, Any]:
    checkpoint_at = _as_utc(checkpoint_at)
    local_date = checkpoint_at.astimezone(SHANGHAI_TZ).date().isoformat()
    manifest_id = f"{site_id}:{local_date}:manifest"
    existing = await db.remote_account_daily_checkpoints.find_one({"_id": manifest_id, "complete": True})
    if existing:
        return {"ok": True, "status": "skipped", "site_id": site_id, "local_date": local_date}
    cursor = db.remote_account_identities.find(
        {"site_id": site_id, "current_presence": "present"},
        {
            "last_usage_snapshot": 1,
            "current_subscription_snapshot": 1,
            "cumulative_usage_snapshot": 1,
        },
    )
    identities = [identity async for identity in cursor]
    documents = build_daily_checkpoint_documents(
        identities,
        site_id=site_id,
        checkpoint_at=checkpoint_at,
    )
    expires_at = checkpoint_at + timedelta(days=CHECKPOINT_RETENTION_DAYS)
    manifest = {
        "_id": manifest_id,
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "document_type": "manifest",
        "site_id": site_id,
        "local_date": local_date,
        "checkpoint_at": checkpoint_at,
        "chunk_count": len(documents),
        "entry_count": sum(document["entry_count"] for document in documents),
        "complete": True,
        "expires_at": expires_at,
    }
    await db.remote_account_daily_checkpoints.bulk_write(
        [
            *[ReplaceOne({"_id": document["_id"]}, document, upsert=True) for document in documents],
            ReplaceOne({"_id": manifest_id}, manifest, upsert=True),
        ],
        ordered=True,
    )
    return {
        "ok": True,
        "status": "created",
        "site_id": site_id,
        "local_date": local_date,
        "chunks": len(documents),
        "accounts": manifest["entry_count"],
    }


async def load_identity_changes(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    identity_id: str,
    limit: int = 120,
) -> list[dict[str, Any]]:
    cursor = (
        db.remote_account_change_batches.find(
            {"site_id": site_id, "entries.identity_id": identity_id},
            {"observed_at": 1, "entries": 1},
        )
        .sort("observed_at", -1)
        .limit(max(limit * 4, limit))
    )
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    async for batch in cursor:
        for entry in batch.get("entries") or []:
            if not isinstance(entry, dict) or entry.get("identity_id") != identity_id:
                continue
            event_id = str(entry.get("event_id") or "")
            if event_id and event_id in seen:
                continue
            if event_id:
                seen.add(event_id)
            results.append(
                {
                    **entry,
                    "batch_id": batch.get("_id"),
                    "observed_at": batch.get("observed_at"),
                }
            )
            if len(results) >= limit:
                return results
    return results


def _change_batch_document(
    *,
    site_id: str,
    run_id: str,
    chunk_index: int,
    observed_at: datetime,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "_id": f"{site_id}:{run_id}:{chunk_index}",
        "schema_version": CHANGE_SCHEMA_VERSION,
        "site_id": site_id,
        "probe_run_id": run_id,
        "chunk_index": chunk_index,
        "observed_at": observed_at,
        "entries": entries,
        "entry_count": len(entries),
        "expires_at": observed_at + timedelta(days=CHANGE_RETENTION_DAYS),
    }


def _checkpoint_document(
    *,
    site_id: str,
    local_date: str,
    checkpoint_at: datetime,
    chunk_index: int,
    entries: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "_id": f"{site_id}:{local_date}:{chunk_index}",
        "schema_version": CHECKPOINT_SCHEMA_VERSION,
        "document_type": "checkpoint",
        "site_id": site_id,
        "local_date": local_date,
        "checkpoint_at": checkpoint_at,
        "chunk_index": chunk_index,
        "entries": entries,
        "entry_count": len(entries),
        "expires_at": checkpoint_at + timedelta(days=CHECKPOINT_RETENTION_DAYS),
    }


def _normalized_dynamic_snapshot(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        "usage": dict(snapshot.get("usage") if isinstance(snapshot.get("usage"), dict) else {}),
        "subscription": dict(
            snapshot.get("subscription") if isinstance(snapshot.get("subscription"), dict) else {}
        ),
    }


def _split_path(path: Any) -> tuple[str, str]:
    section, separator, field = str(path or "").partition(".")
    if not separator or section not in {"usage", "subscription"} or not field:
        raise ValueError(f"invalid account history field path: {path}")
    return section, field


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _as_utc(value).isoformat().replace("+00:00", "Z")
    return str(value)
