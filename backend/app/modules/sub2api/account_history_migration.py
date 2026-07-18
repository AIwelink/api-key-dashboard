from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any, Awaitable, Callable, TypeVar

from pymongo import ReplaceOne
from pymongo.errors import AutoReconnect

from app.modules.sub2api.account_history import (
    CHECKPOINT_RETENTION_DAYS,
    CHECKPOINT_SCHEMA_VERSION,
    SHANGHAI_TZ,
    apply_history_entries,
    build_daily_checkpoint_documents,
    build_history_change,
    chunk_history_changes,
    snapshot_hash,
)


def clamp_migration_batch_size(value: int) -> int:
    return max(100, min(10_000, int(value)))


def migration_id_for_boundary(boundary: datetime, *, site_id: str | None = None) -> str:
    source = f"remote_account_probe_samples:{site_id or 'all'}:{_as_utc(boundary).isoformat()}"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:20]
    return f"legacy-account-history-v1-{digest}"


T = TypeVar("T")


async def retry_mongo_operation(
    operation: Callable[[], Awaitable[T]],
    *,
    attempts: int = 5,
    base_delay_seconds: float = 1.0,
) -> T:
    attempts = max(1, int(attempts))
    for attempt in range(attempts):
        try:
            return await operation()
        except AutoReconnect:
            if attempt + 1 >= attempts:
                raise
            await asyncio.sleep(max(0.0, base_delay_seconds) * (2**attempt))
    raise RuntimeError("unreachable MongoDB retry state")


class LegacyReplayState:
    def __init__(self, *, migration_id: str) -> None:
        self.migration_id = migration_id
        self.dynamic_states: dict[str, dict[str, Any]] = {}
        self.cumulative_states: dict[str, dict[str, Any]] = {}
        self.identity_sites: dict[str, str] = {}
        self.source_documents = 0
        self.changed_accounts = 0
        self.changed_fields = 0
        self.change_batches = 0
        self.checkpoint_documents = 0
        self._checkpoint_dates: set[tuple[str, str]] = set()

    @property
    def final_state_hashes(self) -> dict[str, str]:
        return {
            identity_id: snapshot_hash(state)
            for identity_id, state in self.dynamic_states.items()
        }

    def consume_run(
        self,
        *,
        site_id: str,
        run_id: str,
        observed_at: datetime,
        samples: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        observed_at = _as_utc(observed_at)
        self.source_documents += len(samples)
        latest_by_identity = {
            str(sample.get("identity_id")): sample
            for sample in samples
            if sample.get("identity_id")
        }
        changes: list[dict[str, Any]] = []
        for identity_id, sample in sorted(latest_by_identity.items()):
            current = legacy_dynamic_snapshot(sample)
            previous = self.dynamic_states.get(identity_id, {"usage": {}, "subscription": {}})
            change = build_history_change(
                identity_id=identity_id,
                remote_account_id=sample.get("remote_account_id"),
                previous=previous,
                current=current,
                occurrence_id=f"{site_id}:{run_id}",
            )
            self.dynamic_states[identity_id] = current
            self.cumulative_states[identity_id] = dict(
                sample.get("cumulative_usage_snapshot")
                if isinstance(sample.get("cumulative_usage_snapshot"), dict)
                else {}
            )
            self.identity_sites[identity_id] = site_id
            if change is not None:
                changes.append(change)

        batches = chunk_history_changes(
            changes,
            site_id=site_id,
            run_id=run_id,
            observed_at=observed_at,
        )
        for batch in batches:
            batch["migration_id"] = self.migration_id
            batch["source_collection"] = "remote_account_probe_samples"
        self.changed_accounts += len(changes)
        self.changed_fields += sum(
            len(change.get("changes") or {}) + len(change.get("unset") or [])
            for change in changes
        )
        self.change_batches += len(batches)

        checkpoint_documents = self._first_daily_checkpoint(site_id=site_id, checkpoint_at=observed_at)
        self.checkpoint_documents += len(checkpoint_documents)
        return {
            "change_batches": batches,
            "checkpoint_documents": checkpoint_documents,
        }

    def _first_daily_checkpoint(self, *, site_id: str, checkpoint_at: datetime) -> list[dict[str, Any]]:
        local_date = checkpoint_at.astimezone(SHANGHAI_TZ).date().isoformat()
        checkpoint_key = (site_id, local_date)
        if checkpoint_key in self._checkpoint_dates:
            return []
        self._checkpoint_dates.add(checkpoint_key)
        identities = [
            {
                "_id": identity_id,
                "last_usage_snapshot": state.get("usage") or {},
                "current_subscription_snapshot": state.get("subscription") or {},
                "cumulative_usage_snapshot": self.cumulative_states.get(identity_id) or {},
            }
            for identity_id, state in self.dynamic_states.items()
            if self.identity_sites.get(identity_id) == site_id
        ]
        chunks = build_daily_checkpoint_documents(
            identities,
            site_id=site_id,
            checkpoint_at=checkpoint_at,
        )
        for chunk in chunks:
            chunk["document_type"] = "chunk"
            chunk["migration_id"] = self.migration_id
            chunk["source_collection"] = "remote_account_probe_samples"
        manifest = {
            "_id": f"{site_id}:{local_date}:manifest",
            "schema_version": CHECKPOINT_SCHEMA_VERSION,
            "document_type": "manifest",
            "site_id": site_id,
            "local_date": local_date,
            "checkpoint_at": checkpoint_at,
            "chunk_count": len(chunks),
            "entry_count": sum(int(chunk.get("entry_count") or 0) for chunk in chunks),
            "complete": True,
            "migration_id": self.migration_id,
            "source_collection": "remote_account_probe_samples",
            "expires_at": checkpoint_at + timedelta(days=CHECKPOINT_RETENTION_DAYS),
        }
        return [*chunks, manifest]


async def convert_legacy_account_history(
    db: Any,
    *,
    migration_id: str,
    source_max_sampled_at: datetime,
    site_id: str | None = None,
    cursor_batch_size: int = 2_000,
) -> dict[str, Any]:
    boundary = _as_utc(source_max_sampled_at)
    existing = await db.remote_account_history_migrations.find_one({"_id": migration_id})
    if existing and existing.get("stage") in {
        "converted",
        "verification_failed",
        "verified",
        "deleting",
        "completed",
    }:
        return existing
    source_query: dict[str, Any] = {"sampled_at": {"$lte": boundary}}
    if site_id:
        source_query["site_id"] = site_id
    source_documents_expected = await db.remote_account_probe_samples.count_documents(source_query)
    started_at = datetime.now(UTC)
    await db.remote_account_history_migrations.update_one(
        {"_id": migration_id},
        {
            "$setOnInsert": {
                "created_at": started_at,
                "deleted_documents": 0,
            },
            "$set": {
                "stage": "converting",
                "site_id": site_id,
                "source_collection": "remote_account_probe_samples",
                "source_max_sampled_at": boundary,
                "source_documents_expected": source_documents_expected,
                "source_documents_processed": 0,
                "updated_at": started_at,
            },
        },
        upsert=True,
    )
    replay = LegacyReplayState(migration_id=migration_id)
    resume_by_site: dict[str, datetime] = {}
    restored_checkpoints: list[dict[str, Any]] = []
    if existing and existing.get("stage") in {"converting", "conversion_failed"}:
        target_batches = [
            item
            async for item in db.remote_account_change_batches.find(
                {"migration_id": migration_id},
                {"site_id": 1, "observed_at": 1, "chunk_index": 1, "entries": 1},
            ).sort([("observed_at", 1), ("chunk_index", 1)])
        ]
        restored_checkpoints = [
            item
            async for item in db.remote_account_daily_checkpoints.find(
                {"migration_id": migration_id},
                {
                    "site_id": 1,
                    "local_date": 1,
                    "checkpoint_at": 1,
                    "document_type": 1,
                    "entries": 1,
                },
            )
        ]
        resume_by_site = restore_replay_from_targets(
            replay,
            target_batches,
            checkpoint_documents=restored_checkpoints,
        )
    checkpoint_manifest_ids: set[str] = {
        str(document["_id"])
        for document in restored_checkpoints
        if document.get("document_type") == "manifest" and document.get("_id")
    }
    run_count = 0
    try:
        site_ids = [site_id] if site_id else sorted(
            str(value)
            for value in await db.remote_account_probe_samples.distinct("site_id", source_query)
            if value
        )
        projection = {
            "site_id": 1,
            "probe_run_id": 1,
            "identity_id": 1,
            "remote_account_id": 1,
            "sampled_at": 1,
            "created_at": 1,
            "usage_snapshot": 1,
            "subscription_snapshot": 1,
            "cumulative_usage_snapshot": 1,
        }
        for current_site_id in site_ids:
            query = {**source_query, "site_id": current_site_id}
            resume_at = resume_by_site.get(current_site_id)
            if resume_at is not None:
                replay.source_documents += await db.remote_account_probe_samples.count_documents(
                    {
                        "site_id": current_site_id,
                        "sampled_at": {"$lt": resume_at},
                    }
                )
                query["sampled_at"] = {"$gte": resume_at, "$lte": boundary}
            cursor = (
                db.remote_account_probe_samples.find(query, projection)
                .sort([("sampled_at", 1)])
                .batch_size(clamp_migration_batch_size(cursor_batch_size))
            )
            current_observed_at: datetime | None = None
            current_samples: list[dict[str, Any]] = []
            async for sample in cursor:
                observed_at = _sample_datetime(sample)
                if current_observed_at is not None and observed_at != current_observed_at:
                    manifests, persisted_runs = await _persist_sampled_at_group(
                        db,
                        replay=replay,
                        site_id=current_site_id,
                        observed_at=current_observed_at,
                        samples=current_samples,
                        migration_id=migration_id,
                        run_count=run_count,
                    )
                    checkpoint_manifest_ids.update(manifests)
                    run_count += persisted_runs
                    current_samples = []
                current_observed_at = observed_at
                current_samples.append(sample)
            if current_observed_at is not None and current_samples:
                manifests, persisted_runs = await _persist_sampled_at_group(
                    db,
                    replay=replay,
                    site_id=current_site_id,
                    observed_at=current_observed_at,
                    samples=current_samples,
                    migration_id=migration_id,
                    run_count=run_count,
                )
                checkpoint_manifest_ids.update(manifests)
                run_count += persisted_runs
        converted_at = datetime.now(UTC)
        final_state_hashes = [
            {"identity_id": identity_id, "state_hash": state_hash}
            for identity_id, state_hash in sorted(replay.final_state_hashes.items())
        ]
        result = {
            "_id": migration_id,
            "migration_id": migration_id,
            "stage": "converted",
            "site_id": site_id,
            "source_max_sampled_at": boundary,
            "source_documents_expected": source_documents_expected,
            "source_documents_processed": replay.source_documents,
            "probe_runs_processed": run_count,
            "changed_accounts": replay.changed_accounts,
            "changed_fields": replay.changed_fields,
            "change_batches": replay.change_batches,
            "checkpoint_documents": replay.checkpoint_documents,
            "checkpoint_manifest_ids": sorted(checkpoint_manifest_ids),
            "final_state_hashes": final_state_hashes,
            "converted_at": converted_at,
            "updated_at": converted_at,
        }
        await retry_mongo_operation(
            lambda: db.remote_account_history_migrations.update_one(
                {"_id": migration_id},
                {"$set": {key: value for key, value in result.items() if key != "_id"}},
            )
        )
        return result
    except Exception as exc:
        failed_at = datetime.now(UTC)
        await retry_mongo_operation(
            lambda: db.remote_account_history_migrations.update_one(
                {"_id": migration_id},
                {
                    "$set": {
                        "stage": "conversion_failed",
                        "error": str(exc) or exc.__class__.__name__,
                        "source_documents_processed": replay.source_documents,
                        "updated_at": failed_at,
                    }
                },
            )
        )
        raise


async def _persist_replayed_run(
    db: Any,
    *,
    replay: LegacyReplayState,
    site_id: str,
    run_id: str,
    observed_at: datetime,
    samples: list[dict[str, Any]],
) -> list[str]:
    result = replay.consume_run(
        site_id=site_id,
        run_id=run_id,
        observed_at=observed_at,
        samples=samples,
    )
    batches = result["change_batches"]
    if batches:
        await retry_mongo_operation(
            lambda: db.remote_account_change_batches.bulk_write(
                [ReplaceOne({"_id": batch["_id"]}, batch, upsert=True) for batch in batches],
                ordered=False,
            )
        )
    checkpoint_documents = result["checkpoint_documents"]
    if not checkpoint_documents:
        return []
    manifest = next(
        document
        for document in checkpoint_documents
        if document.get("document_type") == "manifest"
    )
    existing = await db.remote_account_daily_checkpoints.find_one(
        {"_id": manifest["_id"], "complete": True},
        {"migration_id": 1},
    )
    if existing and existing.get("migration_id") != replay.migration_id:
        return [str(manifest["_id"])]
    await retry_mongo_operation(
        lambda: db.remote_account_daily_checkpoints.bulk_write(
            [
                ReplaceOne({"_id": document["_id"]}, document, upsert=True)
                for document in checkpoint_documents
            ],
            ordered=True,
        )
    )
    return [str(manifest["_id"])]


async def _persist_sampled_at_group(
    db: Any,
    *,
    replay: LegacyReplayState,
    site_id: str,
    observed_at: datetime,
    samples: list[dict[str, Any]],
    migration_id: str,
    run_count: int,
) -> tuple[list[str], int]:
    by_run: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        run_id = str(sample.get("probe_run_id") or sample.get("_id") or observed_at.isoformat())
        by_run.setdefault(run_id, []).append(sample)
    manifest_ids: list[str] = []
    for offset, (run_id, run_samples) in enumerate(sorted(by_run.items()), start=1):
        manifests = await _persist_replayed_run(
            db,
            replay=replay,
            site_id=site_id,
            run_id=run_id,
            observed_at=observed_at,
            samples=run_samples,
        )
        manifest_ids.extend(manifests)
        await _update_conversion_progress(db, migration_id, replay, run_count + offset)
    return manifest_ids, len(by_run)


async def _update_conversion_progress(
    db: Any,
    migration_id: str,
    replay: LegacyReplayState,
    run_count: int,
) -> None:
    await retry_mongo_operation(
        lambda: db.remote_account_history_migrations.update_one(
            {"_id": migration_id},
            {
                "$set": {
                    "stage": "converting",
                    "source_documents_processed": replay.source_documents,
                    "probe_runs_processed": run_count,
                    "changed_accounts": replay.changed_accounts,
                    "changed_fields": replay.changed_fields,
                    "change_batches": replay.change_batches,
                    "checkpoint_documents": replay.checkpoint_documents,
                    "updated_at": datetime.now(UTC),
                }
            },
        )
    )


def legacy_dynamic_snapshot(sample: dict[str, Any]) -> dict[str, dict[str, Any]]:
    usage = dict(sample.get("usage_snapshot") if isinstance(sample.get("usage_snapshot"), dict) else {})
    if not usage:
        usage = {
            str(key): value
            for key, value in sample.items()
            if str(key).startswith("codex_") and not str(key).endswith("_cumulative")
        }
    subscription = dict(
        sample.get("subscription_snapshot")
        if isinstance(sample.get("subscription_snapshot"), dict)
        else {}
    )
    return {"usage": usage, "subscription": subscription}


def reconstruct_migrated_states(batches: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    seen_event_ids: set[str] = set()
    ordered = sorted(
        batches,
        key=lambda item: (
            _as_utc(item.get("observed_at")) if isinstance(item.get("observed_at"), datetime) else datetime.min.replace(tzinfo=UTC),
            int(item.get("chunk_index") or 0),
            str(item.get("_id") or ""),
        ),
    )
    for batch in ordered:
        for entry in batch.get("entries") or []:
            if not isinstance(entry, dict) or not entry.get("identity_id"):
                continue
            event_id = str(entry.get("event_id") or "")
            if event_id and event_id in seen_event_ids:
                continue
            if event_id:
                seen_event_ids.add(event_id)
            identity_id = str(entry["identity_id"])
            states[identity_id] = apply_history_entries(
                states.get(identity_id, {"usage": {}, "subscription": {}}),
                [entry],
            )
    return states


def compare_reconstructed_states(
    expected_hashes: dict[str, str],
    reconstructed_states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    actual_hashes = {
        identity_id: snapshot_hash(state)
        for identity_id, state in reconstructed_states.items()
    }
    empty_state_hash = snapshot_hash({"usage": {}, "subscription": {}})
    mismatches = [
        {
            "identity_id": identity_id,
            "expected_hash": expected_hashes.get(identity_id),
            "actual_hash": actual_hashes.get(
                identity_id,
                empty_state_hash if identity_id in expected_hashes else None,
            ),
        }
        for identity_id in sorted(set(expected_hashes) | set(actual_hashes))
        if expected_hashes.get(identity_id)
        != actual_hashes.get(
            identity_id,
            empty_state_hash if identity_id in expected_hashes else None,
        )
    ]
    return {
        "ok": not mismatches,
        "expected_identities": len(expected_hashes),
        "actual_identities": len(actual_hashes),
        "mismatch_count": len(mismatches),
        "mismatches": mismatches[:100],
    }


def restore_replay_from_targets(
    replay: LegacyReplayState,
    batches: list[dict[str, Any]],
    *,
    checkpoint_documents: list[dict[str, Any]],
) -> dict[str, datetime]:
    replay.dynamic_states = reconstruct_migrated_states(batches)
    resume_by_site: dict[str, datetime] = {}
    seen_event_ids: set[str] = set()
    changed_fields = 0
    for batch in batches:
        site_id = str(batch.get("site_id") or "")
        observed_at = batch.get("observed_at")
        if site_id and isinstance(observed_at, datetime):
            observed_at = _as_utc(observed_at)
            if observed_at > resume_by_site.get(site_id, datetime.min.replace(tzinfo=UTC)):
                resume_by_site[site_id] = observed_at
        for entry in batch.get("entries") or []:
            if not isinstance(entry, dict) or not entry.get("identity_id"):
                continue
            identity_id = str(entry["identity_id"])
            if site_id:
                replay.identity_sites[identity_id] = site_id
            event_id = str(entry.get("event_id") or "")
            if event_id and event_id in seen_event_ids:
                continue
            if event_id:
                seen_event_ids.add(event_id)
            changed_fields += len(entry.get("changes") or {}) + len(entry.get("unset") or [])
    replay.changed_accounts = len(seen_event_ids)
    replay.changed_fields = changed_fields
    replay.change_batches = len(batches)
    replay.checkpoint_documents = len(checkpoint_documents)
    for document in checkpoint_documents:
        site_id = str(document.get("site_id") or "")
        local_date = str(document.get("local_date") or "")
        checkpoint_at = document.get("checkpoint_at")
        if site_id and local_date and document.get("document_type") == "manifest":
            replay._checkpoint_dates.add((site_id, local_date))
        if site_id and isinstance(checkpoint_at, datetime):
            checkpoint_at = _as_utc(checkpoint_at)
            if checkpoint_at > resume_by_site.get(site_id, datetime.min.replace(tzinfo=UTC)):
                resume_by_site[site_id] = checkpoint_at
        if document.get("document_type") == "chunk":
            for entry in document.get("entries") or []:
                if not isinstance(entry, dict) or not entry.get("identity_id"):
                    continue
                identity_id = str(entry["identity_id"])
                replay.cumulative_states[identity_id] = dict(
                    entry.get("cumulative_usage")
                    if isinstance(entry.get("cumulative_usage"), dict)
                    else {}
                )
                if identity_id not in replay.dynamic_states:
                    replay.dynamic_states[identity_id] = {
                        "usage": dict(entry.get("usage") if isinstance(entry.get("usage"), dict) else {}),
                        "subscription": dict(
                            entry.get("subscription")
                            if isinstance(entry.get("subscription"), dict)
                            else {}
                        ),
                    }
                if site_id:
                    replay.identity_sites[identity_id] = site_id
    return resume_by_site


def evaluate_source_idle(
    latest_sampled_at: datetime | None,
    *,
    now: datetime,
    idle_minutes: int,
) -> dict[str, Any]:
    now = _as_utc(now)
    required_before = now - timedelta(minutes=max(1, idle_minutes))
    latest = _as_utc(latest_sampled_at) if latest_sampled_at is not None else None
    return {
        "idle": latest is None or latest <= required_before,
        "latest_sampled_at": latest,
        "required_before": required_before,
        "idle_minutes": max(1, idle_minutes),
    }


async def assert_legacy_source_idle(
    db: Any,
    *,
    idle_minutes: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    latest = await db.remote_account_probe_samples.find_one(
        {},
        {"sampled_at": 1},
        sort=[("sampled_at", -1)],
    )
    state = evaluate_source_idle(
        (latest or {}).get("sampled_at"),
        now=now or datetime.now(UTC),
        idle_minutes=idle_minutes,
    )
    if not state["idle"]:
        raise RuntimeError(
            "legacy account samples are still being written: "
            f"latest sampled_at={state['latest_sampled_at'].isoformat()}, "
            f"required <= {state['required_before'].isoformat()}"
        )
    return state


async def inspect_legacy_source(
    db: Any,
    *,
    site_id: str | None = None,
    idle_minutes: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    query: dict[str, Any] = {}
    if site_id:
        query["site_id"] = site_id
    count = await db.remote_account_probe_samples.count_documents(query)
    latest = await db.remote_account_probe_samples.find_one(
        query,
        {"sampled_at": 1, "site_id": 1},
        sort=[("sampled_at", -1)],
    )
    earliest = await db.remote_account_probe_samples.find_one(
        query,
        {"sampled_at": 1, "site_id": 1},
        sort=[("sampled_at", 1)],
    )
    idle = evaluate_source_idle(
        (latest or {}).get("sampled_at"),
        now=now or datetime.now(UTC),
        idle_minutes=idle_minutes,
    )
    return {
        "count": count,
        "site_id": site_id,
        "earliest_sampled_at": (earliest or {}).get("sampled_at"),
        "latest_sampled_at": (latest or {}).get("sampled_at"),
        "idle": idle["idle"],
        "idle_state": idle,
    }


async def pause_legacy_sample_ttl(db: Any) -> dict[str, Any]:
    indexes = await db.remote_account_probe_samples.index_information()
    for name, details in indexes.items():
        keys = details.get("key") or []
        if list(keys) == [("expires_at", 1)] and "expireAfterSeconds" in details:
            await db.remote_account_probe_samples.drop_index(name)
            return {"paused": True, "index_name": name, "expire_after_seconds": details.get("expireAfterSeconds")}
    return {"paused": False, "index_name": None, "expire_after_seconds": None}


async def restore_legacy_sample_ttl(db: Any) -> str:
    return await db.remote_account_probe_samples.create_index("expires_at", expireAfterSeconds=0)


async def verify_migrated_account_history(db: Any, migration_id: str) -> dict[str, Any]:
    ledger = await db.remote_account_history_migrations.find_one({"_id": migration_id})
    if not ledger:
        raise RuntimeError(f"account history migration not found: {migration_id}")
    if ledger.get("stage") not in {"converted", "verification_failed", "verified"}:
        raise RuntimeError(f"migration stage does not allow verification: {ledger.get('stage')}")
    cursor = db.remote_account_change_batches.find(
        {"migration_id": migration_id},
        {"observed_at": 1, "chunk_index": 1, "entries": 1},
    ).sort([("observed_at", 1), ("chunk_index", 1), ("_id", 1)])
    batches = [batch async for batch in cursor]
    expected_hashes = {
        str(item.get("identity_id")): str(item.get("state_hash"))
        for item in ledger.get("final_state_hashes") or []
        if isinstance(item, dict) and item.get("identity_id") and item.get("state_hash")
    }
    comparison = compare_reconstructed_states(
        expected_hashes,
        reconstruct_migrated_states(batches),
    )
    expected_source_count = int(ledger.get("source_documents_expected") or 0)
    processed_source_count = int(ledger.get("source_documents_processed") or 0)
    manifest_ids = [str(value) for value in ledger.get("checkpoint_manifest_ids") or [] if value]
    complete_manifests = (
        await db.remote_account_daily_checkpoints.count_documents(
            {"_id": {"$in": manifest_ids}, "complete": True}
        )
        if manifest_ids
        else 0
    )
    source_count_matches = expected_source_count == processed_source_count
    checkpoints_complete = complete_manifests == len(manifest_ids)
    ok = bool(comparison["ok"] and source_count_matches and checkpoints_complete)
    stage = "verified" if ok else "verification_failed"
    verified_at = datetime.now(UTC)
    summary = {
        **comparison,
        "ok": ok,
        "stage": stage,
        "migration_id": migration_id,
        "change_batches": len(batches),
        "source_count_matches": source_count_matches,
        "source_documents_expected": expected_source_count,
        "source_documents_processed": processed_source_count,
        "checkpoint_manifests_expected": len(manifest_ids),
        "checkpoint_manifests_complete": complete_manifests,
        "checkpoints_complete": checkpoints_complete,
        "verified_at": verified_at,
    }
    await db.remote_account_history_migrations.update_one(
        {"_id": migration_id},
        {
            "$set": {
                "stage": stage,
                "verification": summary,
                "verified_at": verified_at if ok else None,
                "updated_at": verified_at,
            }
        },
    )
    return summary


async def delete_verified_legacy_samples(
    db: Any,
    migration_id: str,
    *,
    batch_size: int = 2_000,
    idle_minutes: int = 10,
    now: datetime | None = None,
) -> dict[str, Any]:
    ledger = await db.remote_account_history_migrations.find_one({"_id": migration_id})
    if not ledger:
        raise RuntimeError(f"account history migration not found: {migration_id}")
    if ledger.get("stage") not in {"verified", "deleting", "completed"}:
        raise RuntimeError(f"migration must be verified before deletion: {ledger.get('stage')}")
    boundary = ledger.get("source_max_sampled_at")
    if not isinstance(boundary, datetime):
        raise RuntimeError("migration source boundary is missing")
    idle_state = await assert_legacy_source_idle(
        db,
        idle_minutes=idle_minutes,
        now=now,
    )
    batch_size = clamp_migration_batch_size(batch_size)
    deleted_documents = int(ledger.get("deleted_documents") or 0)
    query: dict[str, Any] = {"sampled_at": {"$lte": boundary}}
    if ledger.get("site_id"):
        query["site_id"] = ledger["site_id"]
    if ledger.get("stage") == "completed":
        remaining = await db.remote_account_probe_samples.count_documents(query)
        if remaining:
            raise RuntimeError(f"completed migration still has legacy samples: remaining={remaining}")
        expected = int(ledger.get("source_documents_expected") or 0)
        deleted_documents = max(deleted_documents, expected)
        reconciled_at = datetime.now(UTC)
        await db.remote_account_history_migrations.update_one(
            {"_id": migration_id},
            {
                "$set": {
                    "deleted_documents": deleted_documents,
                    "remaining_documents": 0,
                    "stage": "completed",
                    "updated_at": reconciled_at,
                }
            },
        )
        return {
            "ok": True,
            "stage": "completed",
            "migration_id": migration_id,
            "deleted_documents": deleted_documents,
            "remaining_documents": 0,
            "source_idle": idle_state,
        }
    started_at = datetime.now(UTC)
    await db.remote_account_history_migrations.update_one(
        {"_id": migration_id},
        {"$set": {"stage": "deleting", "deletion_started_at": started_at, "updated_at": started_at}},
    )
    while True:
        cursor = (
            db.remote_account_probe_samples.find(query, {"_id": 1})
            .sort([("sampled_at", 1)])
            .limit(batch_size)
        )
        ids = [item["_id"] async for item in cursor if item.get("_id") is not None]
        if not ids:
            break
        result = await db.remote_account_probe_samples.delete_many({"_id": {"$in": ids}})
        deleted = int(getattr(result, "deleted_count", 0) or 0)
        if deleted != len(ids):
            raise RuntimeError(f"legacy sample deletion mismatch: requested={len(ids)} deleted={deleted}")
        deleted_documents += deleted
        updated_at = datetime.now(UTC)
        await db.remote_account_history_migrations.update_one(
            {"_id": migration_id},
            {"$set": {"deleted_documents": deleted_documents, "updated_at": updated_at}},
        )
    remaining = await db.remote_account_probe_samples.count_documents(query)
    if remaining:
        raise RuntimeError(f"legacy sample deletion incomplete: remaining={remaining}")
    expected = int(ledger.get("source_documents_expected") or 0)
    deleted_documents = max(deleted_documents, expected)
    completed_at = datetime.now(UTC)
    await db.remote_account_history_migrations.update_one(
        {"_id": migration_id},
        {
            "$set": {
                "stage": "completed",
                "deleted_documents": deleted_documents,
                "remaining_documents": 0,
                "completed_at": completed_at,
                "updated_at": completed_at,
            }
        },
    )
    return {
        "ok": True,
        "stage": "completed",
        "migration_id": migration_id,
        "deleted_documents": deleted_documents,
        "remaining_documents": 0,
        "source_idle": idle_state,
    }


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def _sample_datetime(sample: dict[str, Any]) -> datetime:
    value = sample.get("sampled_at") or sample.get("created_at")
    if isinstance(value, datetime):
        return _as_utc(value)
    if isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return _as_utc(parsed)
    raise ValueError(f"legacy sample has no valid sampled_at: {sample.get('_id')}")
