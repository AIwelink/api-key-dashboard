from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text

from app.modules.risk.domain import IpObservation, normalize_email, normalize_ip


AUDIT_PAGE_QUERY = """
SELECT id, created_at, actor_user_id, actor_email, action, path, client_ip, request_body
FROM audit_logs
WHERE id > :after_id
  AND created_at >= :since
ORDER BY id
LIMIT :limit
"""

USAGE_PAGE_QUERY = """
SELECT usage.id, usage.user_id, users.email, usage.ip_address, usage.created_at
FROM usage_logs AS usage
JOIN users ON users.id = usage.user_id AND users.deleted_at IS NULL
WHERE usage.id > :after_id
  AND usage.created_at >= :since
  AND usage.user_id IS NOT NULL
ORDER BY usage.id
LIMIT :limit
"""


class SourceStateConflict(RuntimeError):
    """The source changed after the enforcement action was prepared."""


@dataclass(frozen=True)
class SourcePage:
    observations: tuple[IpObservation, ...]
    rows_read: int
    last_source_id: int
    latest_created_at: datetime | None


@dataclass(frozen=True)
class ApiKeyState:
    id: str
    status: str
    updated_at: datetime | None


@dataclass(frozen=True)
class SourceAccountState:
    external_user_id: str
    email: str
    user_status: str
    user_updated_at: datetime | None
    api_keys: tuple[ApiKeyState, ...]


@dataclass(frozen=True)
class EnforcementResult:
    user_status: str
    user_updated_at: datetime | None
    api_keys: tuple[ApiKeyState, ...]


@dataclass(frozen=True)
class ReleaseResult:
    user_restored: bool
    restored_key_ids: tuple[str, ...]
    conflicted_key_ids: tuple[str, ...]
    partial: bool


class Sub2ApiRiskAdapter:
    async def has_completed_payment(
        self,
        connection: Any,
        external_user_id: str,
    ) -> bool:
        result = await connection.execute(
            text(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM payment_orders
                    WHERE CAST(user_id AS TEXT) = :external_user_id
                      AND status = 'COMPLETED'
                      AND pay_amount > 0
                ) AS has_paid_history
                """
            ),
            {"external_user_id": str(external_user_id)},
        )
        rows = [dict(row) for row in result.mappings().all()]
        return bool(rows and rows[0].get("has_paid_history"))

    async def read_audit_observations(
        self,
        connection: Any,
        *,
        after_id: int,
        since: datetime,
        limit: int,
    ) -> SourcePage:
        result = await connection.execute(
            text(AUDIT_PAGE_QUERY),
            {"after_id": max(int(after_id), 0), "since": since, "limit": max(1, min(limit, 5000))},
        )
        rows = [dict(row) for row in result.mappings().all()]
        if not rows:
            return SourcePage((), 0, max(int(after_id), 0), None)

        identities = [_audit_identity(row) for row in rows]
        unresolved_emails = sorted(
            {email for user_id, email in identities if email and not user_id}
        )
        unresolved_user_ids = sorted(
            {user_id for user_id, email in identities if user_id and not email}
        )
        users_by_email: dict[str, tuple[str, str]] = {}
        users_by_id: dict[str, tuple[str, str]] = {}
        if unresolved_emails or unresolved_user_ids:
            user_result = await connection.execute(
                text(
                    """
                    SELECT id, email
                    FROM users
                    WHERE deleted_at IS NULL
                      AND (
                          lower(trim(email)) = ANY(CAST(:emails AS TEXT[]))
                          OR CAST(id AS TEXT) = ANY(CAST(:user_ids AS TEXT[]))
                      )
                    """
                ),
                {"emails": unresolved_emails, "user_ids": unresolved_user_ids},
            )
            for user in user_result.mappings().all():
                user_id = str(user["id"])
                email = normalize_email(user.get("email"))
                users_by_id[user_id] = (user_id, email)
                if email:
                    users_by_email[email] = (user_id, email)

        observations: list[IpObservation] = []
        for row, (user_id, email) in zip(rows, identities, strict=True):
            identity = users_by_id.get(user_id) if user_id and not email else None
            if identity is None and email and not user_id:
                identity = users_by_email.get(email)
            if identity is not None:
                user_id, email = identity
            normalized_ip = normalize_ip(row.get("client_ip"))
            if not user_id or not email or normalized_ip is None:
                continue
            registration = _is_registration(row)
            observations.append(
                IpObservation(
                    external_user_id=user_id,
                    email=email,
                    ip_address=normalized_ip,
                    source_type="registration_audit" if registration else "user_audit",
                    observed_at=row["created_at"],
                    source_id=int(row["id"]),
                )
            )
        return SourcePage(
            observations=tuple(observations),
            rows_read=len(rows),
            last_source_id=max(int(row["id"]) for row in rows),
            latest_created_at=max(row["created_at"] for row in rows),
        )

    async def read_usage_observations(
        self,
        connection: Any,
        *,
        after_id: int,
        since: datetime,
        limit: int,
    ) -> SourcePage:
        result = await connection.execute(
            text(USAGE_PAGE_QUERY),
            {"after_id": max(int(after_id), 0), "since": since, "limit": max(1, min(limit, 5000))},
        )
        rows = [dict(row) for row in result.mappings().all()]
        observations = []
        for row in rows:
            normalized_ip = normalize_ip(row.get("ip_address"))
            email = normalize_email(row.get("email"))
            user_id = str(row.get("user_id") or "")
            if normalized_ip is None or not email or not user_id:
                continue
            observations.append(
                IpObservation(
                    external_user_id=user_id,
                    email=email,
                    ip_address=normalized_ip,
                    source_type="usage_log",
                    observed_at=row["created_at"],
                    source_id=int(row["id"]),
                )
            )
        return SourcePage(
            observations=tuple(observations),
            rows_read=len(rows),
            last_source_id=max((int(row["id"]) for row in rows), default=max(int(after_id), 0)),
            latest_created_at=max((row["created_at"] for row in rows), default=None),
        )

    async def capture_account_state(
        self,
        connection: Any,
        external_user_id: str,
    ) -> SourceAccountState:
        user_result = await connection.execute(
            text(
                """
                SELECT id, email, status, updated_at
                FROM users
                WHERE CAST(id AS TEXT) = :external_user_id AND deleted_at IS NULL
                """
            ),
            {"external_user_id": str(external_user_id)},
        )
        users = [dict(row) for row in user_result.mappings().all()]
        if not users:
            raise LookupError("AIWeLink user not found")
        user = users[0]
        keys_result = await connection.execute(
            text(
                """
                SELECT id, status, updated_at
                FROM api_keys
                WHERE CAST(user_id AS TEXT) = :external_user_id AND deleted_at IS NULL
                ORDER BY id
                """
            ),
            {"external_user_id": str(external_user_id)},
        )
        return SourceAccountState(
            external_user_id=str(user["id"]),
            email=normalize_email(user.get("email")),
            user_status=str(user.get("status") or ""),
            user_updated_at=user.get("updated_at"),
            api_keys=tuple(_key_state(dict(row)) for row in keys_result.mappings().all()),
        )

    async def disable_account(
        self,
        connection: Any,
        *,
        before: SourceAccountState,
        changed_at: datetime,
    ) -> EnforcementResult:
        user_result = await connection.execute(
            text(
                """
                SELECT id, status, updated_at
                FROM users
                WHERE CAST(id AS TEXT) = :external_user_id AND deleted_at IS NULL
                FOR UPDATE
                """
            ),
            {"external_user_id": before.external_user_id},
        )
        users = [dict(row) for row in user_result.mappings().all()]
        if not users:
            raise LookupError("AIWeLink user not found")
        current_user = users[0]
        if (
            str(current_user.get("status") or "") != before.user_status
            or current_user.get("updated_at") != before.user_updated_at
        ):
            raise SourceStateConflict("AIWeLink user state changed before ban")

        keys_result = await connection.execute(
            text(
                """
                SELECT id, status, updated_at
                FROM api_keys
                WHERE CAST(user_id AS TEXT) = :external_user_id AND deleted_at IS NULL
                ORDER BY id
                FOR UPDATE
                """
            ),
            {"external_user_id": before.external_user_id},
        )
        current_keys = tuple(_key_state(dict(row)) for row in keys_result.mappings().all())
        if current_keys != before.api_keys:
            raise SourceStateConflict("AIWeLink API key state changed before ban")

        updated_user_result = await connection.execute(
            text(
                """
                UPDATE users
                SET status = 'disabled', updated_at = :changed_at
                WHERE CAST(id AS TEXT) = :external_user_id
                RETURNING updated_at
                """
            ),
            {"external_user_id": before.external_user_id, "changed_at": changed_at},
        )
        updated_users = [dict(row) for row in updated_user_result.mappings().all()]
        active_key_ids = tuple(key.id for key in before.api_keys if key.status == "active")
        updated_keys: tuple[ApiKeyState, ...] = ()
        if active_key_ids:
            updated_keys_result = await connection.execute(
                text(
                    """
                    UPDATE api_keys
                    SET status = 'inactive', updated_at = :changed_at
                    WHERE CAST(id AS TEXT) = ANY(CAST(:key_ids AS TEXT[]))
                      AND status = 'active'
                    RETURNING id, updated_at
                    """
                ),
                {"key_ids": list(active_key_ids), "changed_at": changed_at},
            )
            updated_keys = tuple(
                ApiKeyState(str(row["id"]), "inactive", row.get("updated_at"))
                for row in updated_keys_result.mappings().all()
            )
            if {key.id for key in updated_keys} != set(active_key_ids):
                raise SourceStateConflict("AIWeLink API key changed during ban")
        return EnforcementResult(
            user_status="disabled",
            user_updated_at=updated_users[0].get("updated_at") if updated_users else changed_at,
            api_keys=updated_keys,
        )

    async def release_account(
        self,
        connection: Any,
        *,
        before: SourceAccountState,
        enforced: EnforcementResult,
        changed_at: datetime,
    ) -> ReleaseResult:
        user_result = await connection.execute(
            text(
                """
                SELECT id, status, updated_at
                FROM users
                WHERE CAST(id AS TEXT) = :external_user_id AND deleted_at IS NULL
                FOR UPDATE
                """
            ),
            {"external_user_id": before.external_user_id},
        )
        users = [dict(row) for row in user_result.mappings().all()]
        user_restored = bool(
            users
            and str(users[0].get("status") or "") == enforced.user_status
            and users[0].get("updated_at") == enforced.user_updated_at
        )
        if user_restored:
            await connection.execute(
                text(
                    """
                    UPDATE users
                    SET status = :status, updated_at = :changed_at
                    WHERE CAST(id AS TEXT) = :external_user_id
                    RETURNING updated_at
                    """
                ),
                {
                    "external_user_id": before.external_user_id,
                    "status": before.user_status,
                    "changed_at": changed_at,
                },
            )

        enforced_by_id = {key.id: key for key in enforced.api_keys}
        current_by_id: dict[str, ApiKeyState] = {}
        if enforced_by_id:
            keys_result = await connection.execute(
                text(
                    """
                    SELECT id, status, updated_at
                    FROM api_keys
                    WHERE CAST(id AS TEXT) = ANY(CAST(:key_ids AS TEXT[]))
                    ORDER BY id
                    FOR UPDATE
                    """
                ),
                {"key_ids": list(enforced_by_id)},
            )
            current_by_id = {
                key.id: key
                for key in (_key_state(dict(row)) for row in keys_result.mappings().all())
            }
        restored_key_ids = tuple(
            key_id
            for key_id, enforced_key in enforced_by_id.items()
            if current_by_id.get(key_id) == enforced_key
        )
        conflicted_key_ids = tuple(
            key_id for key_id in enforced_by_id if key_id not in restored_key_ids
        )
        if restored_key_ids:
            await connection.execute(
                text(
                    """
                    UPDATE api_keys
                    SET status = 'active', updated_at = :changed_at
                    WHERE CAST(id AS TEXT) = ANY(CAST(:key_ids AS TEXT[]))
                      AND status = 'inactive'
                    RETURNING id, updated_at
                    """
                ),
                {"key_ids": list(restored_key_ids), "changed_at": changed_at},
            )
        return ReleaseResult(
            user_restored=user_restored,
            restored_key_ids=restored_key_ids,
            conflicted_key_ids=conflicted_key_ids,
            partial=not user_restored or bool(conflicted_key_ids),
        )


def _audit_identity(row: dict[str, Any]) -> tuple[str, str]:
    user_id = str(row.get("actor_user_id") or "").strip()
    email = normalize_email(row.get("actor_email"))
    if _is_registration(row) and not email:
        email = _registration_email(row.get("request_body"))
    return user_id, email


def _is_registration(row: dict[str, Any]) -> bool:
    action = str(row.get("action") or "").strip().lower()
    path = str(row.get("path") or "").strip().lower()
    return action == "auth.register" or path.endswith("/auth/register")


def _registration_email(value: Any) -> str:
    if isinstance(value, str):
        try:
            payload = json.loads(value)
        except (TypeError, ValueError):
            return ""
    elif isinstance(value, dict):
        payload = value
    else:
        return ""
    candidate = payload.get("email")
    if candidate is None and isinstance(payload.get("data"), dict):
        candidate = payload["data"].get("email")
    return normalize_email(candidate)


def _key_state(row: dict[str, Any]) -> ApiKeyState:
    return ApiKeyState(
        id=str(row["id"]),
        status=str(row.get("status") or ""),
        updated_at=row.get("updated_at"),
    )
