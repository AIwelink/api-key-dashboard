from datetime import UTC, datetime
from typing import Any

from bson import ObjectId


def now_utc() -> datetime:
    return datetime.now(UTC)


def object_id(value: str) -> ObjectId:
    if not ObjectId.is_valid(value):
        raise ValueError("Invalid ObjectId")
    return ObjectId(value)


def serialize_doc(value: Any) -> Any:
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.isoformat()
    if isinstance(value, list):
        return [serialize_doc(item) for item in value]
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            result["id" if key == "_id" else key] = serialize_doc(item)
        return result
    return value


def extract_email(account_json: dict[str, Any]) -> str | None:
    return credentials_email(account_json)


def credentials_email(account_json: dict[str, Any]) -> str | None:
    credentials = account_json.get("credentials")
    if not isinstance(credentials, dict):
        return None
    email = credentials.get("email")
    if isinstance(email, str) and email.strip():
        return email.strip()
    return None


def redact_auth_token(token: str) -> str:
    if len(token) <= 10:
        return "***"
    return f"{token[:6]}...{token[-4:]}"
