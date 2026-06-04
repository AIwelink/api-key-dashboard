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
from pymongo import ReturnDocument

from app.database import db_dependency
from app.schemas import Sub2ApiAccountTestRequest, Sub2ApiManualDeleteRequest, Sub2ApiOAuthApplyRequest, Sub2ApiOAuthExchangeRequest, Sub2ApiRecentMailRequest, Sub2ApiResurrectionFailRequest
from app.security import require_roles
from app.services.audit import write_audit_log
from app.services.account_records import write_account_operation
from app.services.pool_lifecycle import actor_name, operation_actor_updates, pool_reference_unsets, write_pool_action
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
from app.services.sub2api_return import manual_delete_sub2api_account, remote_usage_snapshot
from app.services.sub2api_verify import test_remote_sub2api_account
from app.utils import credentials_email, now_utc, serialize_doc


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
        callback_url = payload.callback_url.replace("&amp;", "&").strip()
        parsed = parse_qs(urlparse(callback_url).query)
        code = code or (parsed.get("code") or [None])[0]
        state = state or (parsed.get("state") or [None])[0]
    if not payload.session_id or not code or not state:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id, code and state are required. 请先获取授权链接，并粘贴包含 code 和 state 的 localhost 回调 URL。")
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
    update_payload = {**apply_payload, "status": "active", "schedulable": True}
    try:
        result = await client.request_admin("POST", f"/accounts/{account_id}/apply-oauth-credentials", json=apply_payload)
    except HTTPException as exc:
        detail = str(exc.detail)
        if "404" not in detail and "not found" not in detail.lower():
            raise
        refreshed = await client.update_account(account_id, update_payload)
        result = {
            "ok": True,
            "fallback": "update_account",
            "message": "sub2api apply-oauth-credentials endpoint not found; credentials were applied through account update",
            "original_error": detail,
        }
    else:
        refreshed = result.get("data", result) if isinstance(result, dict) else {}

    schedulable_result: dict[str, Any]
    try:
        schedulable_result = await client.set_account_schedulable(account_id, True)
        if isinstance(schedulable_result, dict) and schedulable_result.get("id") is not None:
            refreshed = schedulable_result
    except HTTPException as exc:
        detail = str(exc.detail)
        if "404" not in detail and "not found" not in detail.lower():
            raise
        refreshed = await client.update_account(account_id, {"status": "active", "schedulable": True})
        schedulable_result = {
            "ok": True,
            "fallback": "update_account",
            "message": "sub2api schedulable endpoint not found; schedulable was enabled through account update",
            "original_error": detail,
        }
    recover_result: dict[str, Any]
    try:
        recover_result = await client.recover_account_state(account_id)
        if isinstance(recover_result, dict) and recover_result.get("id") is not None:
            refreshed = recover_result
    except HTTPException as exc:
        detail = str(exc.detail)
        if "404" not in detail and "not found" not in detail.lower():
            raise
        recover_result = {
            "ok": False,
            "skipped": True,
            "message": "sub2api recover-state endpoint not found; state reset was skipped",
            "original_error": detail,
        }
    if isinstance(refreshed, dict) and refreshed.get("id") is not None:
        await upsert_cached_account_snapshot(db, site_id, refreshed)
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.account.resurrection_apply_oauth",
        resource_type="sub2api_account",
        resource_id=str(account_id),
        after={"site_id": site_id, "status": "active", "schedulable": True, "recover_state": recover_result},
    )
    return {"apply": result, "schedulable": schedulable_result, "recover_state": recover_result, "account": refreshed}


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
    local_account = await _find_local_account_for_remote(db, site_id=site_id, remote_account=remote_account)
    delete_result = await client.delete_account(account_id)
    await db.sub2api_accounts_cache.delete_many({"site_id": site_id, "sub2api_account_id": {"$in": [account_id, str(account_id)]}})
    updated_local = None
    if local_account:
        updated_local = await _mark_resurrection_failed_local_account(
            db,
            account=local_account,
            site_id=site_id,
            remote_account=remote_account,
            reason=payload.reason.strip(),
            decision=payload.decision,
            delete_result=delete_result,
            actor=actor,
        )
    await write_audit_log(
        db,
        actor=actor,
        action="sub2api.account.resurrection_failed",
        resource_type="sub2api_account",
        resource_id=str(account_id),
        after={
            "site_id": site_id,
            "reason": payload.reason,
            "decision": payload.decision,
            "remote_deleted": True,
            "local_account_id": str(local_account.get("_id")) if local_account else None,
        },
    )
    return {
        "remote_account": remote_account,
        "delete_remote": delete_result,
        "local_account": serialize_doc(updated_local) if updated_local else None,
        "local_decision": payload.decision,
    }


async def _find_local_account_for_remote(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    remote_account: dict[str, Any],
) -> dict[str, Any] | None:
    remote_id = remote_account.get("id")
    remote_ids = [value for value in {remote_id, str(remote_id) if remote_id is not None else None} if value is not None]
    email_value = credentials_email(remote_account) or str(remote_account.get("email") or "").strip()
    matchers: list[dict[str, Any]] = []
    if remote_ids:
        matchers.append({"metadata.sub2api_site_id": site_id, "metadata.sub2api_account_id": {"$in": remote_ids}})
    if email_value:
        email_matcher = re.compile(f"^{re.escape(email_value)}$", re.IGNORECASE)
        matchers.extend(
            [
                {"metadata.email": email_matcher},
                {"account_json.credentials.email": email_matcher},
                {"account_json.extra.email": email_matcher},
            ]
        )
    if not matchers:
        return None
    return await db.accounts.find_one({"metadata.deleted_at": {"$exists": False}, "$or": matchers})


async def _mark_resurrection_failed_local_account(
    db: AsyncIOMotorDatabase,
    *,
    account: dict[str, Any],
    site_id: str,
    remote_account: dict[str, Any],
    reason: str,
    decision: str,
    delete_result: dict[str, Any],
    actor: dict[str, Any],
) -> dict[str, Any] | None:
    now = now_utc()
    remote_id = remote_account.get("id")
    account_id = str(account.get("_id"))
    decision_is_archive = decision == "banned_archive"
    operation_name = "复活失败封禁归档" if decision_is_archive else "复活失败进入错误账号池"
    updates: dict[str, Any] = {
        "metadata.pool_status": "discarded" if decision_is_archive else "problem",
        "metadata.last_error": reason,
        "metadata.sub2api_manual_deleted": True,
        "metadata.sub2api_delete_status": "succeeded",
        "metadata.sub2api_delete_error": None,
        "metadata.sub2api_delete_finished_at": now,
        "metadata.sub2api_delete_result": delete_result,
        "metadata.sub2api_delete_remote_snapshot": remote_account,
        "metadata.sub2api_delete_usage_snapshot": remote_usage_snapshot(remote_account),
        "metadata.sub2api_delete_remote_last_used_at": remote_account.get("last_used_at"),
        "metadata.sub2api_delete_remote_status": remote_account.get("status"),
        "metadata.sub2api_delete_remote_error_message": remote_account.get("error_message"),
        "metadata.problem_source": "resurrection",
        "metadata.problem_error": reason,
        "metadata.problem_remote_account_id": remote_id,
        "metadata.problem_site_id": site_id,
        "metadata.problem_detected_at": now,
        "metadata.problem_last_test_status": "failed",
        "metadata.problem_last_test_at": now,
        "metadata.problem_last_test_error": reason,
        **operation_actor_updates(actor, operation_name, at=now),
    }
    if decision_is_archive:
        updates.update(
            {
                "metadata.problem_status": "closed",
                "metadata.problem_task_status": "archived",
                "metadata.problem_resolution": "banned_archive",
                "metadata.problem_resolved_at": now,
                "metadata.problem_resolved_by_user_id": actor.get("_id"),
                "metadata.problem_resolved_by_name": actor_name(actor),
                "metadata.problem_resolution_note": reason,
                "metadata.discarded_at": now,
            }
        )
    else:
        updates.update(
            {
                "metadata.problem_status": "open",
                "metadata.problem_task_status": "pending",
                "metadata.problem_resolution": None,
                "metadata.problem_class": "resurrection_failed",
                "metadata.problem_name": "账号复活失败",
                "metadata.problem_remark_zh": "复活失败后已从 sub2api 远端删除，等待后续人工处理。",
            }
        )
    unsets = {
        "metadata.problem_lock": "",
        "metadata.sub2api_account_id": "",
        "metadata.sub2api_group_id": "",
        "metadata.sub2api_group_ids": "",
        "metadata.sub2api_group_name": "",
        "metadata.sub2api_last_sync_at": "",
        "metadata.sub2api_pushed_at": "",
        "metadata.push_lock": "",
        "metadata.reserve_pinned_at": "",
        "metadata.reserve_pinned_by_user_id": "",
        "metadata.reserve_pinned_by_name": "",
        "metadata.pool_id": "",
        **pool_reference_unsets(),
    }
    updated = await db.accounts.find_one_and_update(
        {"_id": account["_id"]},
        {"$set": updates, "$unset": unsets},
        return_document=ReturnDocument.AFTER,
    )
    await write_account_operation(
        db,
        operation_class="resurrection_failed_remote_deleted",
        operation_name=operation_name,
        remark_zh="复活失败后已删除 sub2api 远端账号，并按操作人选择更新本地状态。",
        actor=actor,
        account_id=account_id,
        details={"site_id": site_id, "remote_account_id": remote_id, "decision": decision, "reason": reason, "delete_result": delete_result},
    )
    await write_pool_action(
        db,
        action_type="resurrection_failed_remote_deleted",
        actor=actor,
        account_id=account_id,
        status_value="succeeded",
        reason=reason,
        remote_snapshot=remote_account,
        after={"decision": decision, "pool_status": updates["metadata.pool_status"], "remote_deleted": True},
    )
    return updated


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
