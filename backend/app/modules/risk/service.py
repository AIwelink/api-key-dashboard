from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from uuid import UUID, uuid4

from app.modules.risk import repository
from app.modules.risk.adapters.sub2api import SourcePage
from app.modules.risk.domain import (
    EmailRule,
    RiskDecision,
    SharedIpEvidence,
    decide_risk,
    match_email_rules,
    source_health,
)


RISK_WINDOW = timedelta(days=7)
RISK_PAGE_SIZE = 1000
RISK_MAX_PAGES_PER_CYCLE = 50
RiskStream = Literal["audit_logs", "usage_logs"]


@dataclass(frozen=True)
class AccountRiskEvaluation:
    external_user_id: str
    email: str
    email_rules: tuple[EmailRule, ...]
    shared_ips: tuple[SharedIpEvidence, ...]
    manual_override_active: bool
    has_paid_history: bool
    decision: RiskDecision


@dataclass(frozen=True)
class PreparedBanCandidate:
    risk_account_id: str
    evaluation: AccountRiskEvaluation


def source_window_start(*, now: datetime) -> datetime:
    return now - RISK_WINDOW


async def collect_stream_pages(
    adapter: Any,
    connection: Any,
    *,
    stream: RiskStream,
    after_id: int,
    since: datetime,
    page_size: int = RISK_PAGE_SIZE,
    max_pages: int = RISK_MAX_PAGES_PER_CYCLE,
) -> SourcePage:
    reader = (
        adapter.read_audit_observations
        if stream == "audit_logs"
        else adapter.read_usage_observations
    )
    cursor = max(int(after_id), 0)
    observations = []
    rows_read = 0
    latest_created_at = None
    for _ in range(max(1, max_pages)):
        page = await reader(
            connection,
            after_id=cursor,
            since=since,
            limit=max(1, min(page_size, 5000)),
        )
        observations.extend(page.observations)
        rows_read += page.rows_read
        cursor = max(cursor, page.last_source_id)
        if page.latest_created_at is not None:
            latest_created_at = (
                page.latest_created_at
                if latest_created_at is None
                else max(latest_created_at, page.latest_created_at)
            )
        if page.rows_read < page_size:
            break
    return SourcePage(tuple(observations), rows_read, cursor, latest_created_at)


def evaluate_account_input(row: dict[str, Any]) -> AccountRiskEvaluation:
    email = str(row.get("email") or "").strip().lower()
    rules = match_email_rules(email)
    evidence = tuple(_shared_ip_evidence(item) for item in (row.get("shared_ip_evidence") or []))
    manual_override_active = bool(row.get("manual_override_active"))
    has_paid_history = bool(
        row.get("has_paid_history") or row.get("has_verified_payment")
    )
    return AccountRiskEvaluation(
        external_user_id=str(row.get("external_user_id") or ""),
        email=email,
        email_rules=rules,
        shared_ips=evidence,
        manual_override_active=manual_override_active,
        has_paid_history=has_paid_history,
        decision=decide_risk(
            email_rules=rules,
            shared_ips=evidence,
            manual_override=manual_override_active,
            has_paid_history=has_paid_history,
        ),
    )


def desired_risk_status(
    evaluation: AccountRiskEvaluation,
    *,
    auto_ban_enabled: bool,
) -> str:
    if evaluation.decision == RiskDecision.BAN:
        return "ban_pending" if auto_ban_enabled else "high_risk"
    if evaluation.decision == RiskDecision.HIGH_RISK:
        return "high_risk"
    return "cleared"


def action_idempotency_key(site_id: str, evaluation: AccountRiskEvaluation) -> str:
    evidence = {
        "email_rules": evaluation.email_rules,
        "shared_ips": [
            {
                "ip_address": item.ip_address,
                "distinct_account_count": item.distinct_account_count,
                "external_user_ids": item.external_user_ids,
                "sources": item.sources,
                "first_seen_at": item.first_seen_at.isoformat(),
                "last_seen_at": item.last_seen_at.isoformat(),
            }
            for item in evaluation.shared_ips
        ],
    }
    digest = hashlib.sha256(
        json.dumps(evidence, ensure_ascii=True, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    return f"auto-ban:{site_id}:{evaluation.external_user_id}:{digest}"


def source_health_payload(cursor: dict[str, Any], *, now: datetime) -> dict[str, Any]:
    latest_observed_at = _datetime(cursor.get("latest_observed_at"))
    return {
        "source_stream": str(cursor.get("source_stream") or ""),
        "status": source_health(latest_observed_at=latest_observed_at, now=now),
        "latest_observed_at": latest_observed_at,
        "last_success_at": _datetime(cursor.get("last_success_at")),
        "last_rows_read": int(cursor.get("last_rows_read") or 0),
        "last_error_code": str(cursor.get("last_error_code") or ""),
        "last_error_message": str(cursor.get("last_error_message") or ""),
    }


async def reconcile_risk_inputs(
    connection: Any,
    *,
    rows: list[dict[str, Any]],
    auto_ban_enabled: bool,
    detected_at: datetime,
    source_payment_checker: Any,
) -> list[PreparedBanCandidate]:
    candidates: list[PreparedBanCandidate] = []
    for row in rows:
        evaluation = evaluate_account_input(row)
        if evaluation.decision == RiskDecision.BAN:
            has_source_payment = bool(
                await source_payment_checker(evaluation.external_user_id)
            )
            if has_source_payment:
                evaluation = evaluate_account_input({**row, "has_paid_history": True})

        previous_status = str(row.get("risk_status") or "")
        target_status = desired_risk_status(
            evaluation,
            auto_ban_enabled=auto_ban_enabled,
        )
        if target_status == "cleared" and not previous_status:
            continue
        reasons = {
            "email_rules": list(evaluation.email_rules),
            "shared_ips": _evidence_payload(evaluation.shared_ips),
            "protection_reasons": (
                ["verified_payment_history"] if evaluation.has_paid_history else []
            ),
        }
        account = await repository.upsert_risk_account(
            connection,
            site_id="aiwelink",
            external_user_id=evaluation.external_user_id,
            email=evaluation.email,
            risk_status=target_status,
            risk_reasons=reasons,
            detected_at=detected_at,
        )
        risk_account_id = str(account.get("risk_account_id") or "")
        if target_status != previous_status:
            event_type = "risk_cleared" if target_status == "cleared" else "high_risk_detected"
            event_key = (
                f"risk-state:{risk_account_id}:{target_status}:"
                f"{action_idempotency_key('aiwelink', evaluation).rsplit(':', 1)[-1]}"
            )
            await repository.append_event(
                connection,
                risk_event_id=uuid4(),
                idempotency_key=event_key,
                risk_account_id=UUID(risk_account_id),
                site_id="aiwelink",
                external_user_id=evaluation.external_user_id,
                email=evaluation.email,
                event_type=event_type,
                decision_reason=(
                    "verified_payment_review"
                    if evaluation.has_paid_history
                    else "email_and_shared_ip"
                    if evaluation.email_rules and evaluation.shared_ips
                    else "email_or_shared_ip"
                ),
                matched_email_rules=list(evaluation.email_rules),
                shared_ip_evidence=_evidence_payload(evaluation.shared_ips),
                created_at=detected_at,
                actor_id="system:risk-detector",
                actor_name="AIWeLink risk detector",
            )
        if target_status == "ban_pending":
            candidates.append(
                PreparedBanCandidate(
                    risk_account_id=risk_account_id,
                    evaluation=evaluation,
                )
            )
    return candidates


def _shared_ip_evidence(value: dict[str, Any]) -> SharedIpEvidence:
    return SharedIpEvidence(
        ip_address=str(value.get("ip_address") or ""),
        distinct_account_count=int(value.get("distinct_account_count") or 0),
        external_user_ids=tuple(str(item) for item in (value.get("external_user_ids") or [])),
        sources=tuple(str(item) for item in (value.get("sources") or [])),  # type: ignore[arg-type]
        first_seen_at=_datetime(value.get("first_seen_at")) or datetime.min.replace(tzinfo=UTC),
        last_seen_at=_datetime(value.get("last_seen_at")) or datetime.min.replace(tzinfo=UTC),
    )


def _evidence_payload(items: tuple[SharedIpEvidence, ...]) -> list[dict[str, Any]]:
    return [
        {
            "ip_address": item.ip_address,
            "distinct_account_count": item.distinct_account_count,
            "external_user_ids": list(item.external_user_ids),
            "sources": list(item.sources),
            "first_seen_at": item.first_seen_at.isoformat(),
            "last_seen_at": item.last_seen_at.isoformat(),
        }
        for item in items
    ]


def _datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
