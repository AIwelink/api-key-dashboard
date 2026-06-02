import asyncio
import email
import html
import imaplib
import re
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, status
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.database import db_dependency
from app.schemas import Sub2ApiAccountTestRequest, Sub2ApiManualDeleteRequest, Sub2ApiOAuthApplyRequest, Sub2ApiOAuthExchangeRequest, Sub2ApiRecentMailRequest, Sub2ApiResurrectionFailRequest
from app.security import require_roles
from app.services.audit import write_audit_log
from app.services.sub2api import Sub2ApiClient
from app.services.sub2api_cache import (
    create_site_config,
    delete_site_config,
    get_site,
    list_cached_group_accounts,
    list_cached_groups,
    list_sites as list_cached_sites,
    request_debounced_refresh,
    update_site_config,
    upsert_cached_account_snapshot,
)
from app.services.sub2api_dashboard import get_stored_dashboard_snapshots, refresh_dashboard_snapshots
from app.services.sub2api_push import _move_remote_to_problem_group, _resolve_push_problem_group
from app.services.sub2api_return import manual_delete_sub2api_account
from app.services.sub2api_verify import test_remote_sub2api_account


router = APIRouter(prefix="/sub2api-sites", tags=["sub2api-sites"])


def _parse_outlook_session(session: str) -> dict[str, str] | None:
    parts = [part.strip() for part in session.split("----")]
    if len(parts) >= 18 and parts[16] and parts[17]:
        email_name = parts[0]
        password = parts[1]
        client_id = parts[16]
        refresh_token = parts[17]
    elif len(parts) >= 4:
        email_name, password, client_id, *token_parts = parts
        refresh_token = "----".join(token_parts).strip()
    else:
        return None
    if not email_name or not client_id or not refresh_token:
        return None
    return {"email": email_name, "password": password, "client_id": client_id, "refresh_token": refresh_token}


def _http_error_message(payload: Any, fallback: str) -> str:
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            return str(error.get("message") or error.get("error_description") or fallback)
        return str(payload.get("error_description") or payload.get("message") or payload.get("error") or fallback)
    return fallback


def _token_endpoint_options(parsed: dict[str, str], purpose: str) -> tuple[tuple[str, dict[str, str]], ...]:
    if purpose == "imap":
        return (
            (
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                {
                    "client_id": parsed["client_id"],
                    "refresh_token": parsed["refresh_token"],
                    "grant_type": "refresh_token",
                    "scope": "https://outlook.office.com/IMAP.AccessAsUser.All offline_access",
                },
            ),
            (
                "https://login.microsoftonline.com/common/oauth2/v2.0/token",
                {
                    "client_id": parsed["client_id"],
                    "refresh_token": parsed["refresh_token"],
                    "grant_type": "refresh_token",
                },
            ),
            (
                "https://login.live.com/oauth20_token.srf",
                {
                    "client_id": parsed["client_id"],
                    "refresh_token": parsed["refresh_token"],
                    "grant_type": "refresh_token",
                },
            ),
        )
    return (
        (
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            {
                "client_id": parsed["client_id"],
                "refresh_token": parsed["refresh_token"],
                "grant_type": "refresh_token",
                "scope": "https://graph.microsoft.com/Mail.Read offline_access",
            },
        ),
        (
            "https://login.live.com/oauth20_token.srf",
            {
                "client_id": parsed["client_id"],
                "refresh_token": parsed["refresh_token"],
                "grant_type": "refresh_token",
                "scope": "https://graph.microsoft.com/Mail.Read offline_access",
            },
        ),
        (
            "https://login.microsoftonline.com/common/oauth2/v2.0/token",
            {
                "client_id": parsed["client_id"],
                "refresh_token": parsed["refresh_token"],
                "grant_type": "refresh_token",
            },
        ),
    )


async def _access_token_from_session(session: str, purpose: str = "graph") -> tuple[str, str]:
    parsed = _parse_outlook_session(session)
    if not parsed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email_session must be email----password----client_id----refresh_token or xiaoshuidi long format")
    last_payload: Any = None
    last_status = 0
    async with httpx.AsyncClient(timeout=20) as client:
        for url, data in _token_endpoint_options(parsed, purpose):
            response = await client.post(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
            try:
                payload = response.json() if response.content else {}
            except ValueError:
                payload = {"message": response.text}
            if response.status_code < 400 and isinstance(payload, dict) and payload.get("access_token"):
                return str(payload["access_token"]), parsed["email"]
            last_payload = payload
            last_status = response.status_code
    raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_http_error_message(last_payload, f"Microsoft {purpose} token refresh failed ({last_status})"))


def _decode_mail_header(value: Any) -> str:
    if not value:
        return ""
    return str(make_header(decode_header(str(value))))


def _html_to_text(value: str) -> str:
    without_tags = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", value, flags=re.I | re.S)
    without_tags = re.sub(r"<[^>]+>", " ", without_tags)
    return re.sub(r"\s+", " ", html.unescape(without_tags)).strip()


def _message_body(message: email.message.Message) -> str:
    html_body = ""
    text_body = ""
    if message.is_multipart():
        for part in message.walk():
            if part.get_content_maintype() == "multipart":
                continue
            if part.get("Content-Disposition", "").lower().startswith("attachment"):
                continue
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            charset = part.get_content_charset() or "utf-8"
            decoded = payload.decode(charset, errors="ignore")
            if part.get_content_type() == "text/plain" and not text_body:
                text_body = decoded
            elif part.get_content_type() == "text/html" and not html_body:
                html_body = decoded
    else:
        payload = message.get_payload(decode=True)
        if payload:
            charset = message.get_content_charset() or "utf-8"
            text_body = payload.decode(charset, errors="ignore")
    return re.sub(r"\s+", " ", text_body or _html_to_text(html_body)).strip()


def _fetch_recent_mail_imap(email_name: str, access_token: str, limit: int) -> list[dict[str, str]]:
    auth_string = f"user={email_name}\1auth=Bearer {access_token}\1\1"
    mail = imaplib.IMAP4_SSL("outlook.live.com")
    try:
        mail.authenticate("XOAUTH2", lambda _: auth_string)
        result, _ = mail.select("inbox")
        if result != "OK":
            raise RuntimeError("IMAP inbox select failed")
        result, data = mail.search(None, "ALL")
        if result != "OK" or not data:
            raise RuntimeError("IMAP mail search failed")
        messages: list[dict[str, str]] = []
        for mail_id in sorted(data[0].split(), reverse=True)[:limit]:
            result, msg_data = mail.fetch(mail_id, "(RFC822)")
            if result != "OK" or not msg_data or not isinstance(msg_data[0], tuple):
                continue
            raw_email = msg_data[0][1]
            parsed = email.message_from_bytes(raw_email)
            body = _message_body(parsed)
            date_header = parsed.get("Date")
            try:
                received_at = parsedate_to_datetime(date_header).isoformat() if date_header else ""
            except (TypeError, ValueError, IndexError, OverflowError):
                received_at = str(date_header or "")
            messages.append(
                {
                    "subject": _decode_mail_header(parsed.get("Subject")),
                    "from": _decode_mail_header(parsed.get("From")).replace("<", "(").replace(">", ")"),
                    "preview": body,
                    "received_at": received_at,
                    "source": "imap",
                }
            )
        return messages
    finally:
        try:
            mail.logout()
        except Exception:
            pass


async def _fetch_recent_mail_graph(access_token: str, limit: int) -> list[dict[str, str]]:
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.get(
            "https://graph.microsoft.com/v1.0/me/messages",
            params={"$top": limit, "$orderby": "receivedDateTime desc", "$select": "subject,from,bodyPreview,receivedDateTime,body"},
            headers={"Authorization": f"Bearer {access_token}"},
        )
    data = response.json() if response.content else {}
    if response.status_code >= 400:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_http_error_message(data, f"Graph mail request failed ({response.status_code})"))
    values = data.get("value") if isinstance(data, dict) else []
    messages = []
    for item in values if isinstance(values, list) else []:
        if not isinstance(item, dict):
            continue
        sender = item.get("from") if isinstance(item.get("from"), dict) else {}
        address = sender.get("emailAddress") if isinstance(sender.get("emailAddress"), dict) else {}
        body = item.get("body") if isinstance(item.get("body"), dict) else {}
        body_content = str(body.get("content") or "")
        messages.append(
            {
                "subject": item.get("subject") or "",
                "from": address.get("address") or "",
                "preview": item.get("bodyPreview") or _html_to_text(body_content),
                "received_at": item.get("receivedDateTime") or "",
                "source": "graph",
            }
        )
    return messages


async def _client_for_site(db: AsyncIOMotorDatabase, site_id: str) -> Sub2ApiClient:
    site = await get_site(db, site_id, include_token=True)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    return Sub2ApiClient(base_url=site.get("base_url"), token=site.get("token"))


@router.get("")
async def list_sites(
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await list_cached_sites(db)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_site(
    payload: dict[str, Any],
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    created = await create_site_config(db, payload)
    if not created:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="site id and base_url are required")
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.site.create",
        resource_type="sub2api_site",
        resource_id=created["id"],
        after={key: value for key, value in created.items() if key != "token"},
    )
    return created


@router.patch("/{site_id}")
async def update_site(
    site_id: str,
    payload: dict[str, Any],
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    updated = await update_site_config(db, site_id, payload)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.site.update",
        resource_type="sub2api_site",
        resource_id=site_id,
        after={key: value for key, value in updated.items() if key != "token"},
    )
    if payload.get("auto_remove_abnormal_accounts") is True:
        try:
            updated["auto_remove_refresh"] = await request_debounced_refresh(db, site_id)
        except Exception as exc:  # noqa: BLE001 - keep the saved switch visible, but report the scan failure.
            updated["auto_remove_refresh"] = {
                "ok": False,
                "status": "failed",
                "message": str(exc),
            }
    return updated


@router.delete("/{site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_site(
    site_id: str,
    actor: dict = Depends(require_roles("owner", "admin")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> None:
    if not await delete_site_config(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.site.delete",
        resource_type="sub2api_site",
        resource_id=site_id,
    )


@router.post("/{site_id}/test")
async def test_site(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    return await (await _client_for_site(db, site_id)).test_connection()


@router.post("/{site_id}/refresh")
async def refresh_site(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    if not await get_site(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    return await request_debounced_refresh(db, site_id)


@router.post("/{site_id}/dashboard/refresh")
async def refresh_site_dashboard(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    if not await get_site(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    client = await _client_for_site(db, site_id)
    return await refresh_dashboard_snapshots(db, site_id=site_id, client=client, force=True)


@router.get("/{site_id}/dashboard")
async def get_site_dashboard(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    if not await get_site(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    return await get_stored_dashboard_snapshots(db, site_id=site_id)


@router.get("/{site_id}/groups")
async def list_site_groups(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=100, ge=1, le=500),
) -> dict:
    site = await get_site(db, site_id)
    if not site:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    data = await list_cached_groups(db, site_id, page=page, page_size=page_size)
    return {"site": site, **data}


@router.get("/{site_id}/groups/{group_id}/accounts")
async def list_site_group_accounts(
    site_id: str,
    group_id: int,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
) -> dict:
    if not await get_site(db, site_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="sub2api site not found")
    return await list_cached_group_accounts(db, site_id, group_id, status_filter=status_filter, page=page, page_size=page_size)


@router.post("/{site_id}/accounts/{account_id}/manual-delete")
async def post_manual_delete_remote_account(
    site_id: str,
    account_id: int,
    payload: Sub2ApiManualDeleteRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await manual_delete_sub2api_account(
        db,
        site_id=site_id,
        remote_account_id=account_id,
        target_status=payload.target_status,
        reason=payload.reason,
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.account.manual_delete",
        resource_type="sub2api_account",
        resource_id=str(account_id),
        after={
            "site_id": site_id,
            "target_status": payload.target_status,
            "reason": payload.reason,
            "local_account_id": result.get("account", {}).get("id"),
            "delete_result": result.get("delete_result", {}),
        },
    )
    return result


@router.post("/{site_id}/openai/generate-auth-url")
async def post_generate_openai_auth_url(
    site_id: str,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    client = await _client_for_site(db, site_id)
    response = await client.request_admin("POST", "/openai/generate-auth-url", json={})
    return response.get("data", response) if isinstance(response, dict) else {"data": response}


@router.post("/mail/recent")
async def post_recent_mail(
    payload: Sub2ApiRecentMailRequest,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
) -> dict:
    parsed = _parse_outlook_session(payload.email_session)
    if not parsed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email_session must be email----password----client_id----refresh_token or xiaoshuidi long format")
    email_name = parsed["email"]
    graph_error = ""
    try:
        graph_access_token, _ = await _access_token_from_session(payload.email_session, purpose="graph")
        messages = await _fetch_recent_mail_graph(graph_access_token, payload.limit)
        return {"email": email_name, "method": "graph", "items": messages, "total": len(messages)}
    except HTTPException as exc:
        graph_error = str(exc.detail)
    except Exception as exc:  # noqa: BLE001 - fallback to IMAP and report both errors if it also fails.
        graph_error = str(exc)

    try:
        imap_access_token, _ = await _access_token_from_session(payload.email_session, purpose="imap")
        messages = await asyncio.to_thread(_fetch_recent_mail_imap, email_name, imap_access_token, payload.limit)
        return {"email": email_name, "method": "imap", "graph_error": graph_error, "items": messages, "total": len(messages)}
    except Exception as exc:  # noqa: BLE001 - include Graph fallback context for troubleshooting mailbox credentials.
        detail = f"Graph 取件失败：{graph_error}；IMAP 取件失败：{exc}"
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=detail) from exc


@router.post("/{site_id}/openai/exchange-code")
async def post_exchange_openai_code(
    site_id: str,
    payload: Sub2ApiOAuthExchangeRequest,
    _: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    code = payload.code
    state = payload.state
    if payload.callback_url:
        parsed = parse_qs(urlparse(payload.callback_url).query)
        code = code or (parsed.get("code") or [None])[0]
        state = state or (parsed.get("state") or [None])[0]
    if not payload.session_id or not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id, code and state are required")
    client = await _client_for_site(db, site_id)
    return await client.request_admin(
        "POST",
        "/openai/exchange-code",
        json={"session_id": payload.session_id, "code": code, "state": state},
    )


@router.post("/{site_id}/accounts/{account_id}/apply-oauth-credentials")
async def post_apply_oauth_credentials(
    site_id: str,
    account_id: int,
    payload: Sub2ApiOAuthApplyRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    client = await _client_for_site(db, site_id)
    apply_payload = {"type": payload.account_type, "credentials": payload.credentials}
    result = await client.request_admin("POST", f"/accounts/{account_id}/apply-oauth-credentials", json=apply_payload)
    refreshed = await client.update_account(account_id, {"status": "active", "schedulable": True})
    if isinstance(refreshed, dict) and refreshed.get("id") is not None:
        await upsert_cached_account_snapshot(db, site_id, refreshed)
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.account.resurrection_apply_oauth",
        resource_type="sub2api_account",
        resource_id=str(account_id),
        after={"site_id": site_id, "status": "active", "schedulable": True},
    )
    return {"apply": result, "account": refreshed}


@router.post("/{site_id}/accounts/{account_id}/resurrection-fail")
async def post_resurrection_fail(
    site_id: str,
    account_id: int,
    payload: Sub2ApiResurrectionFailRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    if not payload.reason.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="reason is required")
    client = await _client_for_site(db, site_id)
    remote_account = await client.get_account(account_id)
    problem_group_id, problem_group_name = await _resolve_push_problem_group(db, site_id)
    moved = await _move_remote_to_problem_group(
        db,
        client=client,
        site_id=site_id,
        remote_account={**remote_account, "error_message": payload.reason},
        problem_group_id=problem_group_id,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.account.resurrection_failed",
        resource_type="sub2api_account",
        resource_id=str(account_id),
        after={"site_id": site_id, "reason": payload.reason, "problem_group_id": problem_group_id},
    )
    return {"account": moved, "problem_group_id": problem_group_id, "problem_group_name": problem_group_name}


@router.post("/{site_id}/accounts/{account_id}/test")
async def post_test_remote_account(
    site_id: str,
    account_id: int,
    payload: Sub2ApiAccountTestRequest,
    actor: dict = Depends(require_roles("owner", "admin", "maintainer")),
    db: AsyncIOMotorDatabase = Depends(db_dependency),
) -> dict:
    result = await test_remote_sub2api_account(
        db,
        site_id=site_id,
        remote_account_id=account_id,
        model_id=payload.model_id,
        prompt=payload.prompt,
        reason=payload.reason,
        actor=actor,
    )
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.account.test",
        resource_type="sub2api_account",
        resource_id=str(account_id),
        after={
            "site_id": site_id,
            "model_id": payload.model_id,
            "verification": result.get("verification", {}),
        },
    )
    return result
