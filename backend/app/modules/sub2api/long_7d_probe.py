from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import DuplicateKeyError

from app.modules.sub2api.cache import DEFAULT_LONG_7D_PROBE_MODEL, sub2api_site_query
from app.modules.sub2api.client import Sub2ApiClient
from app.utils import now_utc


logger = logging.getLogger("app.sub2api_long_7d_probe")

LONG_7D_PROBE_INTERVAL = timedelta(hours=24)
LONG_7D_MIN_RESET_SECONDS = 24 * 60 * 60
LONG_7D_PROBE_POLL_SECONDS = 5 * 60

_cycle_lock = asyncio.Lock()


def site_long_7d_probe_model(site: dict[str, Any]) -> str:
    return str(site.get("long_7d_probe_model") or "").strip() or DEFAULT_LONG_7D_PROBE_MODEL


def is_long_7d_probe_candidate(account_doc: dict[str, Any], *, now: datetime | None = None) -> bool:
    now = _as_utc(now or now_utc())
    if _account_value(account_doc, "schedulable") is not True:
        return False

    seven_day_used = _number(_account_value(account_doc, "codex_7d_used_percent"))
    if seven_day_used is None or seven_day_used < 100:
        return False

    reset_after = _number(_account_value(account_doc, "codex_7d_reset_after_seconds"))
    if reset_after is None:
        reset_at = _parse_datetime(_account_value(account_doc, "codex_7d_reset_at"))
        reset_after = max(0.0, (reset_at - now).total_seconds()) if reset_at is not None else None
    return reset_after is not None and reset_after > LONG_7D_MIN_RESET_SECONDS


def account_disable_reason(error: str | None) -> str | None:
    text = str(error or "").strip().lower()
    if not text:
        return None
    if _has_http_status(text, 401) and (
        "token_invalidated" in text or "authentication token has been invalidated" in text
    ):
        return "token_invalidated"
    if _has_http_status(text, 402) and "deactivated_workspace" in text:
        return "deactivated_workspace"
    if _has_http_status(text, 403) and (
        "personal access token owner is inactive" in text
        or "biscuit_baker_service_auth_credential_error_status" in text
    ):
        return "inactive_token_owner"
    return None


async def probe_site_long_7d_accounts(
    db: AsyncIOMotorDatabase,
    *,
    site: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    attempted_at = _as_utc(now or now_utc())
    site_id = str(site.get("_id") or site.get("id") or "").strip()
    if not site_id:
        return {"ok": False, "message": "sub2api site id is missing", "eligible": 0, "probed": 0}

    cursor = db.sub2api_accounts_cache.find({"site_id": site_id, "schedulable": True}).sort("sub2api_account_id", 1)
    candidates = [doc async for doc in cursor if is_long_7d_probe_candidate(doc, now=attempted_at)]
    remote_ids = [remote_id for doc in candidates if (remote_id := _remote_account_id(doc)) is not None]
    if not remote_ids:
        return {
            "ok": True,
            "site_id": site_id,
            "eligible": 0,
            "due": 0,
            "probed": 0,
            "passed": 0,
            "failed": 0,
            "disabled": 0,
        }

    history_cursor = db.long_7d_account_probes.find(
        {"site_id": site_id, "remote_account_id": {"$in": remote_ids}}
    )
    histories = {
        remote_id: doc
        async for doc in history_cursor
        if (remote_id := _remote_account_id(doc)) is not None
    }
    due = [
        doc
        for doc in candidates
        if _probe_is_due(histories.get(_remote_account_id(doc)), now=attempted_at)
    ]

    model = site_long_7d_probe_model(site)
    client = Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))
    passed = 0
    failed = 0
    probed = 0
    disabled = 0

    # Await each request before moving to the next recovering account.
    for account_doc in due:
        remote_id = _remote_account_id(account_doc)
        claim_at = attempted_at if now is not None else now_utc()
        if remote_id is None or not await _claim_probe(db, site_id=site_id, remote_account_id=remote_id, now=claim_at):
            continue
        probed += 1
        try:
            verification = await client.test_account(
                remote_id,
                model_id=model,
                prompt="",
                mode="default",
            )
            succeeded = verification.get("success") is True
            error = str(verification.get("error") or "") or None
        except asyncio.CancelledError:
            raise
        except HTTPException as exc:
            succeeded = False
            error = str(exc.detail)
            verification = _failed_verification(model=model, error=error)
        except Exception as exc:  # noqa: BLE001 - one failed account must not stop the queue.
            succeeded = False
            error = str(exc)
            verification = _failed_verification(model=model, error=error)

        disable_reason = account_disable_reason(error)
        schedulable_disabled = False
        disable_error: str | None = None
        if disable_reason is not None:
            try:
                await client.set_account_schedulable(remote_id, False)
                schedulable_disabled = True
                disabled += 1
                logger.warning(
                    "sub2api_long_7d_probe_schedulable_disabled site_id=%s account_id=%s reason=%s",
                    site_id,
                    remote_id,
                    disable_reason,
                )
            except asyncio.CancelledError:
                raise
            except HTTPException as exc:
                disable_error = str(exc.detail)
            except Exception as exc:  # noqa: BLE001 - record the failed safety action and continue.
                disable_error = str(exc)

        await _finish_probe(
            db,
            site_id=site_id,
            remote_account_id=remote_id,
            finished_at=now_utc(),
            verification=verification,
            succeeded=succeeded,
            error=error,
            disable_reason=disable_reason,
            schedulable_disabled=schedulable_disabled,
            disable_error=disable_error,
        )
        if succeeded:
            passed += 1
        else:
            failed += 1
            logger.warning(
                "sub2api_long_7d_probe_failed site_id=%s account_id=%s model=%s error=%s",
                site_id,
                remote_id,
                model,
                error,
            )
        if disable_reason is not None and not schedulable_disabled:
            logger.error(
                "sub2api_long_7d_probe_disable_failed site_id=%s account_id=%s reason=%s error=%s",
                site_id,
                remote_id,
                disable_reason,
                disable_error,
            )

    if probed:
        logger.info(
            "sub2api_long_7d_probe_finished site_id=%s eligible=%s due=%s probed=%s passed=%s failed=%s disabled=%s model=%s",
            site_id,
            len(candidates),
            len(due),
            probed,
            passed,
            failed,
            disabled,
            model,
        )
    return {
        "ok": failed == 0,
        "site_id": site_id,
        "eligible": len(candidates),
        "due": len(due),
        "probed": probed,
        "passed": passed,
        "failed": failed,
        "disabled": disabled,
        "model": model,
    }


async def run_long_7d_probe_cycle(db: AsyncIOMotorDatabase) -> dict[str, Any]:
    if _cycle_lock.locked():
        return {"ok": True, "skipped": True, "reason": "probe cycle already running"}

    async with _cycle_lock:
        sites = [
            site
            async for site in db.sub2api_sites.find(sub2api_site_query(status="active")).sort("_id", 1)
        ]
        results = []
        for site in sites:
            results.append(await probe_site_long_7d_accounts(db, site=site))
        return {
            "ok": all(result.get("ok") is True for result in results),
            "sites": len(results),
            "probed": sum(int(result.get("probed") or 0) for result in results),
            "failed": sum(int(result.get("failed") or 0) for result in results),
            "disabled": sum(int(result.get("disabled") or 0) for result in results),
            "results": results,
        }


async def long_7d_probe_scheduler_loop(db: AsyncIOMotorDatabase) -> None:
    while True:
        try:
            await run_long_7d_probe_cycle(db)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - keep the scheduler alive after infrastructure failures.
            logger.exception("sub2api_long_7d_probe_scheduler_failed")
        await asyncio.sleep(LONG_7D_PROBE_POLL_SECONDS)


async def _claim_probe(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account_id: int | str,
    now: datetime,
) -> bool:
    probe_id = f"{site_id}:{remote_account_id}"
    cutoff = now - LONG_7D_PROBE_INTERVAL
    try:
        result = await db.long_7d_account_probes.update_one(
            {
                "_id": probe_id,
                "$or": [
                    {"last_attempt_at": {"$exists": False}},
                    {"last_attempt_at": {"$lte": cutoff}},
                ],
            },
            {
                "$set": {
                    "site_id": site_id,
                    "remote_account_id": remote_account_id,
                    "status": "running",
                    "last_attempt_at": now,
                    "updated_at": now,
                },
                "$setOnInsert": {"created_at": now},
            },
            upsert=True,
        )
    except DuplicateKeyError:
        return False
    return bool(result.modified_count or result.upserted_id is not None)


async def _finish_probe(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account_id: int | str,
    finished_at: datetime,
    verification: dict[str, Any],
    succeeded: bool,
    error: str | None,
    disable_reason: str | None,
    schedulable_disabled: bool,
    disable_error: str | None,
) -> None:
    status = "passed" if succeeded else "failed"
    summary = {
        "success": succeeded,
        "model": verification.get("model"),
        "mode": verification.get("mode") or "default",
        "latency_ms": verification.get("latency_ms"),
        "response_preview": verification.get("response_preview"),
        "error": error,
        "disable_reason": disable_reason,
        "schedulable_disabled": schedulable_disabled,
        "disable_error": disable_error,
    }
    await db.long_7d_account_probes.update_one(
        {"_id": f"{site_id}:{remote_account_id}"},
        {
            "$set": {
                "status": status,
                "last_finished_at": finished_at,
                "last_result": summary,
                "model": summary["model"],
                "error": error,
                "updated_at": finished_at,
            }
        },
    )
    cache_updates: dict[str, Any] = {
        "long_7d_probe_status": status,
        "long_7d_probed_at": finished_at,
        "long_7d_probe_model": summary["model"],
        "long_7d_probe_error": error,
        "long_7d_probe_disable_reason": disable_reason,
        "long_7d_probe_disable_error": disable_error,
    }
    if schedulable_disabled:
        cache_updates["schedulable"] = False
        cache_updates["account.schedulable"] = False
    await db.sub2api_accounts_cache.update_one(
        {"site_id": site_id, "sub2api_account_id": remote_account_id},
        {"$set": cache_updates},
    )


def _probe_is_due(history: dict[str, Any] | None, *, now: datetime) -> bool:
    if not history:
        return True
    last_attempt_at = _parse_datetime(history.get("last_attempt_at"))
    return last_attempt_at is None or last_attempt_at <= now - LONG_7D_PROBE_INTERVAL


def _remote_account_id(doc: dict[str, Any]) -> int | str | None:
    value = doc.get("remote_account_id")
    if value is None:
        value = doc.get("sub2api_account_id")
    if value is None and isinstance(doc.get("account"), dict):
        value = doc["account"].get("id")
    return value if isinstance(value, (int, str)) and str(value).strip() else None


def _account_value(doc: dict[str, Any], key: str) -> Any:
    if key in doc and doc.get(key) is not None:
        return doc.get(key)
    account = doc.get("account") if isinstance(doc.get("account"), dict) else {}
    if key in account and account.get(key) is not None:
        return account.get(key)
    extra = account.get("extra") if isinstance(account.get("extra"), dict) else {}
    return extra.get(key)


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _has_http_status(text: str, status_code: int) -> bool:
    return re.search(rf"\b(?:returned|status)[^0-9]{{0,6}}{status_code}\b", text) is not None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return _as_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        return _as_utc(datetime.fromisoformat(value.strip().replace("Z", "+00:00")))
    except ValueError:
        return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _failed_verification(*, model: str, error: str) -> dict[str, Any]:
    return {
        "success": False,
        "model": model,
        "mode": "default",
        "prompt": "",
        "latency_ms": None,
        "response_preview": "",
        "error": error,
    }
