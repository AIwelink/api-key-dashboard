from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from ipaddress import ip_address
from typing import Iterable, Literal


EmailRule = Literal["email_local_part_dot", "email_plus_tag"]
RiskSource = Literal["registration_audit", "user_audit", "usage_log"]
SourceHealth = Literal["current", "delayed", "stale", "empty"]


class RiskDecision(str, Enum):
    CLEAR = "clear"
    HIGH_RISK = "high_risk"
    BAN = "ban"


@dataclass(frozen=True)
class IpObservation:
    external_user_id: str
    email: str
    ip_address: str
    source_type: RiskSource
    observed_at: datetime
    source_id: int = 0


@dataclass(frozen=True)
class SharedIpEvidence:
    ip_address: str
    distinct_account_count: int
    external_user_ids: tuple[str, ...]
    sources: tuple[RiskSource, ...]
    first_seen_at: datetime
    last_seen_at: datetime


def normalize_email(value: object) -> str:
    return str(value or "").strip().lower()


def match_email_rules(value: object) -> tuple[EmailRule, ...]:
    normalized = normalize_email(value)
    if normalized.count("@") != 1:
        return ()
    local_part, domain = normalized.split("@", 1)
    if not local_part or not domain:
        return ()
    rules: list[EmailRule] = []
    if "." in local_part:
        rules.append("email_local_part_dot")
    plus_index = local_part.find("+")
    if plus_index >= 0 and plus_index < len(local_part) - 1:
        rules.append("email_plus_tag")
    return tuple(rules)


def normalize_ip(value: object) -> str | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        return str(ip_address(candidate))
    except ValueError:
        return None


def shared_ip_evidence(
    observations: Iterable[IpObservation],
    *,
    now: datetime,
    window: timedelta = timedelta(days=7),
    minimum_accounts: int = 3,
) -> tuple[SharedIpEvidence, ...]:
    cutoff = now - window
    grouped: dict[str, list[IpObservation]] = {}
    for observation in observations:
        normalized_ip = normalize_ip(observation.ip_address)
        if normalized_ip is None or observation.observed_at < cutoff:
            continue
        grouped.setdefault(normalized_ip, []).append(observation)

    evidence: list[SharedIpEvidence] = []
    for normalized_ip, rows in grouped.items():
        external_user_ids = tuple(sorted({row.external_user_id for row in rows if row.external_user_id}))
        if len(external_user_ids) < minimum_accounts:
            continue
        evidence.append(
            SharedIpEvidence(
                ip_address=normalized_ip,
                distinct_account_count=len(external_user_ids),
                external_user_ids=external_user_ids,
                sources=tuple(sorted({row.source_type for row in rows})),
                first_seen_at=min(row.observed_at for row in rows),
                last_seen_at=max(row.observed_at for row in rows),
            )
        )
    return tuple(sorted(evidence, key=lambda item: item.ip_address))


def decide_risk(
    *,
    email_rules: tuple[EmailRule, ...],
    shared_ips: tuple[SharedIpEvidence, ...],
    manual_override: bool,
) -> RiskDecision:
    if manual_override:
        return RiskDecision.CLEAR
    if email_rules and shared_ips:
        return RiskDecision.BAN
    if email_rules or shared_ips:
        return RiskDecision.HIGH_RISK
    return RiskDecision.CLEAR


def source_health(
    *,
    latest_observed_at: datetime | None,
    now: datetime,
    current_for: timedelta = timedelta(minutes=15),
    delayed_for: timedelta = timedelta(hours=24),
) -> SourceHealth:
    if latest_observed_at is None:
        return "empty"
    age = now - latest_observed_at
    if age <= current_for:
        return "current"
    if age <= delayed_for:
        return "delayed"
    return "stale"
