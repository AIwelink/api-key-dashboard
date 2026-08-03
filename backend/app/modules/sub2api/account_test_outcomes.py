from __future__ import annotations

import re
from typing import Any


MODEL_NOT_SUPPORTED_TEXT = "model is not supported when using codex with a chatgpt account"
CONFIRMED_DISABLE_REASONS = {
    "unauthorized": "token_invalidated",
    "payment_required": "deactivated_workspace",
    "inactive_owner": "inactive_token_owner",
}


def classify_test_result(
    verification: dict[str, Any] | None = None,
    *,
    transport_error: str | None = None,
) -> str:
    verification = verification or {}
    if transport_error:
        return "transport_error"

    error = str(verification.get("error") or "").strip().lower()
    if MODEL_NOT_SUPPORTED_TEXT in error:
        return "model_not_supported"
    if has_http_status(error, 401):
        return "unauthorized"
    if has_http_status(error, 402):
        return "payment_required"
    if has_http_status(error, 403):
        if (
            "personal access token owner is inactive" in error
            or "biscuit_baker_service_auth_credential_error_status" in error
        ):
            return "inactive_owner"
        return "forbidden_other"
    if verification.get("success") is True:
        return "passed"
    if has_http_status(error, 429):
        return "rate_limited"
    return "failed"


def disable_reason(outcome: str) -> str | None:
    return CONFIRMED_DISABLE_REASONS.get(outcome)


def has_http_status(value: Any, status_code: int) -> bool:
    text = str(value or "").strip().lower()
    if text == str(status_code):
        return True
    return re.search(
        rf"\b(?:returned|status|http(?:/\d(?:\.\d)?)?)[^0-9]{{0,12}}{status_code}\b",
        text,
    ) is not None


def snapshot_has_http_status(
    account: dict[str, Any],
    status_code: int,
) -> bool:
    nested = account.get("account") if isinstance(account.get("account"), dict) else {}
    return any(
        has_http_status(source.get(field), status_code)
        for source in (account, nested)
        for field in ("status", "error_message")
    )
