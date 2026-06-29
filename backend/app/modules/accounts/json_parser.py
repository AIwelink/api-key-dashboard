import base64
import binascii
import json
from json import JSONDecoder
from typing import Any

from fastapi import HTTPException, status


def parse_loose_json(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    text = value.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="JSON payload is empty")

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    decoder = JSONDecoder()
    index = 0
    items: list[Any] = []
    length = len(text)

    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break
        while index < length and text[index] not in "{[":
            index += 1
        if index >= length:
            break
        try:
            item, next_index = decoder.raw_decode(text, index)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Cannot parse JSON near position {index}: {exc.msg}",
            ) from exc
        items.append(item)
        index = next_index

        while index < length and text[index].isspace():
            index += 1
        if index < length and text[index] == ",":
            index += 1

    return items


def extract_account_objects(payload: Any, *, source_template: str = "sub2api") -> list[dict[str, Any]]:
    parsed = parse_loose_json(payload)
    candidates: list[Any]

    if isinstance(parsed, dict) and isinstance(parsed.get("accounts"), list):
        candidates = parsed["accounts"]
    elif isinstance(parsed, list):
        candidates = []
        for item in parsed:
            if isinstance(item, dict) and isinstance(item.get("accounts"), list):
                candidates.extend(item["accounts"])
            else:
                candidates.append(item)
    elif isinstance(parsed, dict):
        candidates = [parsed]
    else:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Payload must contain account objects")

    accounts: list[dict[str, Any]] = []
    invalid = 0
    for item in candidates:
        normalized = normalize_by_template(item, source_template)
        if isinstance(normalized, dict) and isinstance(normalized.get("credentials"), dict):
            accounts.append(normalized)
        else:
            invalid += 1

    if not accounts:
        if source_template == "purchased_jinyao":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No valid purchased account found. Each item must contain email, access_token, and mailbox_connection.",
            )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No valid account object found. Each account must contain credentials.",
        )

    return accounts


def normalize_by_template(item: Any, source_template: str) -> Any:
    if source_template == "purchased_jinyao":
        return normalize_purchased_jinyao(item)
    return item


def normalize_purchased_jinyao(item: Any) -> Any:
    if not isinstance(item, dict):
        return item
    if isinstance(item.get("credentials"), dict):
        return item

    email = first_text(item.get("email"), item.get("login_identity"), item.get("account_claims_email"))
    access_token = text_value(item.get("access_token"))
    mailbox_connection = text_value(item.get("mailbox_connection"))
    if not email or not access_token or not mailbox_connection:
        return item

    expires_at = jwt_exp(access_token)
    credentials = compact_dict(
        {
            "access_token": item.get("access_token"),
            "refresh_token": item.get("refresh_token"),
            "id_token": item.get("id_token"),
            "session_token": item.get("session_token"),
            "client_id": item.get("client_id"),
            "email": email,
            "expires_at": expires_at,
            "chatgpt_account_id": item.get("chatgpt_account_id"),
            "chatgpt_user_id": item.get("chatgpt_user_id"),
            "organization_id": item.get("organization_id"),
            "project_id": item.get("project_id"),
            "workspace_id": item.get("workspace_id"),
        }
    )
    extra = dict(item)
    extra.update(
        {
            "email": email,
            "email_session": mailbox_connection,
            "import_template": "purchased_jinyao",
        }
    )

    return compact_dict(
        {
            "name": email,
            "platform": "openai",
            "type": "oauth",
            "expires_at": expires_at,
            "auto_pause_on_expired": True,
            "concurrency": 10,
            "priority": 1,
            "credentials": credentials,
            "extra": extra,
        }
    )


def compact_dict(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def first_text(*values: Any) -> str:
    for value in values:
        text = text_value(value)
        if text:
            return text
    return ""


def text_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def jwt_exp(token: str) -> int | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii"))
        payload = json.loads(decoded)
    except (binascii.Error, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    exp = payload.get("exp")
    return exp if isinstance(exp, int) else None
