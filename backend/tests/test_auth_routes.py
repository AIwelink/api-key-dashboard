from __future__ import annotations

import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException

from app import schemas
from app.config import Settings
from app.modules.auth import feishu
from app.routers import auth as auth_router


def enabled_settings() -> Settings:
    return Settings(
        feishu_auth_enabled=True,
        feishu_app_id="cli_example",
        feishu_app_secret="secret",
        feishu_redirect_uri="https://account.example.com/api/auth/feishu/callback",
        feishu_allowed_tenant_keys="tenant-a",
        frontend_origin="https://account.example.com",
    )


def user(*, bound: bool = True, authorization_status: str = "active") -> dict:
    result = {
        "_id": "member@example.com",
        "email": "member@example.com",
        "name": "Member",
        "role": "maintainer",
        "status": "active",
        "authorization_status": authorization_status,
        "password_hash": "stored-hash",
    }
    if bound:
        result["feishu_identity"] = {"identity_key": "tenant-a:union:union-1"}
    return result


def auth_db(stored_user: dict | None = None):
    users = SimpleNamespace(
        find_one=AsyncMock(return_value=stored_user),
        update_one=AsyncMock(),
    )
    return SimpleNamespace(users=users)


class PasswordLoginBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_placeholder_email_cannot_use_password_login(self) -> None:
        placeholder = {
            **user(bound=True),
            "_id": "feishu-user",
            "email": "feishu-user@identity.invalid",
            "email_is_placeholder": True,
        }
        db = auth_db(placeholder)

        with (
            patch.object(auth_router, "verify_password", return_value=True) as verify_mock,
            patch.object(auth_router, "create_access_token", return_value="must-not-be-issued") as token_mock,
            patch.object(auth_router, "write_audit_log", AsyncMock()) as audit_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await auth_router.login(
                    SimpleNamespace(
                        email="feishu-user@identity.invalid",
                        password="password123",
                    ),
                    db=db,
                )

        self.assertEqual(raised.exception.status_code, 401)
        verify_mock.assert_not_called()
        token_mock.assert_not_called()
        self.assertEqual(audit_mock.await_args.kwargs["action"], "auth.login_failed")
        self.assertEqual(
            audit_mock.await_args.kwargs["after"],
            {"result_code": "password_login_unavailable"},
        )

    async def test_finish_login_rejects_user_disabled_during_token_issuance(self) -> None:
        authenticated = user(bound=True)
        disabled = {**authenticated, "status": "disabled"}
        db = auth_db(disabled)
        db.users.update_one.return_value = SimpleNamespace(matched_count=1, modified_count=1)

        with (
            patch.object(auth_router, "create_access_token", return_value="must-not-be-issued") as token_mock,
            patch.object(auth_router, "user_with_permissions", AsyncMock(return_value={"status": "disabled"})),
            patch.object(auth_router, "write_audit_log", AsyncMock()) as audit_mock,
        ):
            with self.assertRaises(HTTPException) as raised:
                await auth_router._finish_login(db, user=authenticated, audit_action="auth.login_succeeded")

        self.assertEqual(raised.exception.status_code, 401)
        token_mock.assert_not_called()
        audit_mock.assert_not_awaited()

    async def test_unbound_user_receives_binding_session_instead_of_jwt(self) -> None:
        session = feishu.FeishuAuthorizationSession(
            session_id="session-1",
            authorization_url="https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example",
            ticket="ticket-1",
            expires_at=datetime(2026, 8, 18, 12, 5, tzinfo=UTC),
        )
        db = auth_db(user(bound=False))

        with (
            patch.object(auth_router, "verify_password", return_value=True),
            patch.object(auth_router, "get_settings", return_value=enabled_settings(), create=True),
            patch.object(auth_router, "create_authorization_session", AsyncMock(return_value=session), create=True) as create_session,
            patch.object(auth_router, "user_with_permissions", AsyncMock(return_value={"role": "maintainer"})),
            patch.object(auth_router, "write_audit_log", AsyncMock()),
        ):
            result = await auth_router.login(
                schemas.LoginRequest(email="member@example.com", password="password123"),
                db=db,
            )

        self.assertTrue(hasattr(schemas, "LoginBindingRequiredResponse"))
        self.assertIsInstance(result, schemas.LoginBindingRequiredResponse)
        self.assertEqual(result.status, "binding_required")
        self.assertEqual(result.ticket, "ticket-1")
        create_session.assert_awaited_once_with(
            db,
            purpose="bind",
            target_user_id="member@example.com",
            settings=enabled_settings(),
        )
        db.users.update_one.assert_not_awaited()

    async def test_bound_user_keeps_direct_password_login(self) -> None:
        db = auth_db(user(bound=True))
        safe_user = {
            "id": "member@example.com",
            "email": "member@example.com",
            "role": "maintainer",
            "permissions": {"allowed_views": ["work-plans"], "default_view": "work-plans"},
        }

        with (
            patch.object(auth_router, "verify_password", return_value=True),
            patch.object(auth_router, "get_settings", return_value=enabled_settings(), create=True),
            patch.object(auth_router, "create_access_token", return_value="local-jwt"),
            patch.object(auth_router, "user_with_permissions", AsyncMock(return_value=safe_user)),
            patch.object(auth_router, "write_audit_log", AsyncMock()),
        ):
            result = await auth_router.login(
                schemas.LoginRequest(email="member@example.com", password="password123"),
                db=db,
            )

        self.assertIsInstance(result, schemas.LoginResponse)
        self.assertEqual(result.access_token, "local-jwt")
        db.users.update_one.assert_awaited_once()

    async def test_proxy_bound_user_keeps_direct_password_login(self) -> None:
        proxy_bound = user(bound=False)
        proxy_bound["feishu_identity"] = {"source_user_id": "feishu-pending"}
        db = auth_db(proxy_bound)
        db.users.update_one.return_value = SimpleNamespace(matched_count=1, modified_count=1)
        safe_user = {
            "id": "member@example.com",
            "email": "member@example.com",
            "role": "maintainer",
            "feishu_bound": True,
            "permissions": {"allowed_views": ["work-plans"], "default_view": "work-plans"},
        }

        with (
            patch.object(auth_router, "verify_password", return_value=True),
            patch.object(auth_router, "get_settings", return_value=enabled_settings(), create=True),
            patch.object(auth_router, "create_authorization_session", AsyncMock()) as create_session,
            patch.object(auth_router, "create_access_token", return_value="local-jwt"),
            patch.object(auth_router, "user_with_permissions", AsyncMock(return_value=safe_user)),
            patch.object(auth_router, "write_audit_log", AsyncMock()),
        ):
            result = await auth_router.login(
                schemas.LoginRequest(email="member@example.com", password="password123"),
                db=db,
            )

        self.assertIsInstance(result, schemas.LoginResponse)
        self.assertEqual(result.access_token, "local-jwt")
        create_session.assert_not_awaited()

    async def test_disabled_feishu_configuration_keeps_password_fallback(self) -> None:
        db = auth_db(user(bound=False))
        fallback_settings = Settings(feishu_auth_enabled=False)

        with (
            patch.object(auth_router, "verify_password", return_value=True),
            patch.object(auth_router, "get_settings", return_value=fallback_settings, create=True),
            patch.object(auth_router, "create_access_token", return_value="local-jwt"),
            patch.object(auth_router, "user_with_permissions", AsyncMock(return_value={"role": "maintainer"})),
            patch.object(auth_router, "write_audit_log", AsyncMock()),
        ):
            result = await auth_router.login(
                schemas.LoginRequest(email="member@example.com", password="password123"),
                db=db,
            )

        self.assertEqual(result.access_token, "local-jwt")


class FeishuAuthRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_binding_route_targets_current_user(self) -> None:
        self.assertTrue(hasattr(auth_router, "start_feishu_binding_session"))
        session = feishu.FeishuAuthorizationSession(
            session_id="session-bind-1",
            authorization_url="https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example",
            ticket="ticket-bind-1",
            expires_at=datetime(2026, 8, 18, 12, 5, tzinfo=UTC),
        )
        current = user(bound=False)
        with (
            patch.object(auth_router, "get_settings", return_value=enabled_settings(), create=True),
            patch.object(auth_router, "create_authorization_session", AsyncMock(return_value=session), create=True) as create_session,
            patch.object(auth_router, "write_audit_log", AsyncMock()),
        ):
            result = await auth_router.start_feishu_binding_session(user=current, db=SimpleNamespace())

        self.assertEqual(result.session_id, "session-bind-1")
        create_session.assert_awaited_once_with(
            SimpleNamespace(),
            purpose="bind",
            target_user_id="member@example.com",
            settings=enabled_settings(),
        )

    async def test_start_route_returns_ticket_protected_session(self) -> None:
        self.assertTrue(hasattr(auth_router, "start_feishu_session"))
        session = feishu.FeishuAuthorizationSession(
            session_id="session-1",
            authorization_url="https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example",
            ticket="ticket-1",
            expires_at=datetime(2026, 8, 18, 12, 5, tzinfo=UTC),
        )
        with (
            patch.object(auth_router, "get_settings", return_value=enabled_settings(), create=True),
            patch.object(auth_router, "create_authorization_session", AsyncMock(return_value=session), create=True),
        ):
            result = await auth_router.start_feishu_session(db=SimpleNamespace())

        self.assertEqual(result.session_id, "session-1")
        self.assertEqual(result.ticket, "ticket-1")

    async def test_start_route_reports_disabled_configuration(self) -> None:
        self.assertTrue(hasattr(auth_router, "start_feishu_session"))
        with patch.object(auth_router, "get_settings", return_value=Settings(feishu_auth_enabled=False), create=True):
            with self.assertRaises(HTTPException) as raised:
                await auth_router.start_feishu_session(db=SimpleNamespace())

        self.assertEqual(raised.exception.status_code, 503)

    async def test_callback_posts_only_session_id_to_trusted_origin(self) -> None:
        self.assertTrue(hasattr(auth_router, "feishu_callback"))
        with (
            patch.object(auth_router, "get_settings", return_value=enabled_settings(), create=True),
            patch.object(auth_router, "complete_authorization_session", AsyncMock(return_value="session-1"), create=True),
        ):
            response = await auth_router.feishu_callback(
                code="authorization-code",
                state="state-token",
                error=None,
                db=SimpleNamespace(),
            )

        body = response.body.decode("utf-8")
        self.assertIn("session-1", body)
        self.assertIn("https://account.example.com", body)
        self.assertNotIn("authorization-code", body)
        self.assertNotIn("state-token", body)

    async def test_callback_cancel_marks_session_failed(self) -> None:
        self.assertTrue(hasattr(auth_router, "feishu_callback"))
        with (
            patch.object(auth_router, "get_settings", return_value=enabled_settings(), create=True),
            patch.object(auth_router, "fail_authorization_session", AsyncMock(return_value="session-1"), create=True) as fail_session,
        ):
            response = await auth_router.feishu_callback(
                code=None,
                state="state-token",
                error="access_denied",
                db=SimpleNamespace(),
            )

        fail_session.assert_awaited_once()
        self.assertIn("授权已取消", response.body.decode("utf-8"))

    async def test_status_route_uses_ticket_and_returns_service_state(self) -> None:
        self.assertTrue(hasattr(auth_router, "feishu_session_status"))
        status_payload = {
            "session_id": "session-1",
            "status": "completed",
            "error_code": None,
            "expires_at": datetime(2026, 8, 18, 12, 5, tzinfo=UTC),
        }
        with patch.object(
            auth_router,
            "get_authorization_session_status",
            AsyncMock(return_value=status_payload),
            create=True,
        ):
            result = await auth_router.feishu_session_status(
                "session-1",
                ticket="ticket-1",
                db=SimpleNamespace(),
            )

        self.assertEqual(result.status, "completed")

    def test_status_ticket_is_read_from_header_not_query_string(self) -> None:
        route = next(route for route in auth_router.router.routes if route.path == "/auth/feishu/sessions/{session_id}")

        self.assertEqual([field.alias for field in route.dependant.query_params], [])
        self.assertEqual([field.alias for field in route.dependant.header_params], ["X-Feishu-Session-Ticket"])

    async def test_exchange_consumes_ticket_and_issues_local_jwt(self) -> None:
        self.assertTrue(hasattr(auth_router, "exchange_feishu_ticket"))
        pending = user(bound=True, authorization_status="pending")
        safe_pending = {
            "id": "member@example.com",
            "email": "member@example.com",
            "role": "viewer",
            "authorization_status": "pending",
            "permissions": {"allowed_views": [], "default_view": None},
        }
        db = auth_db(pending)
        with (
            patch.object(auth_router, "consume_login_ticket", AsyncMock(return_value=pending), create=True),
            patch.object(auth_router, "create_access_token", return_value="local-jwt"),
            patch.object(auth_router, "user_with_permissions", AsyncMock(return_value=safe_pending)),
            patch.object(auth_router, "write_audit_log", AsyncMock()),
        ):
            result = await auth_router.exchange_feishu_ticket(
                schemas.FeishuTicketExchangeRequest(ticket="ticket-token-at-least-20-characters"),
                db=db,
            )

        self.assertEqual(result.access_token, "local-jwt")
        self.assertEqual(result.user["authorization_status"], "pending")

    async def test_auth_user_projection_does_not_expose_feishu_identity_ids(self) -> None:
        raw_user = user(bound=True)
        raw_user["feishu_identity"].update(
            {
                "tenant_key": "tenant-a",
                "union_id": "union-1",
                "open_id": "open-1",
                "user_id": "user-1",
                "name": "飞书成员",
                "email": "member@feishu.example",
            }
        )
        with (
            patch.object(auth_router, "permissions_for_user", AsyncMock(return_value={"allowed_views": []})),
            patch.object(auth_router, "get_settings", return_value=enabled_settings()),
        ):
            result = await auth_router.user_with_permissions(SimpleNamespace(), raw_user)

        self.assertNotIn("feishu_identity", result)
        self.assertTrue(result["feishu_bound"])
        self.assertFalse(result["feishu_binding_required"])
        self.assertEqual(result["feishu_name"], "飞书成员")

    async def test_auth_user_projection_requires_binding_only_when_feishu_is_enabled(self) -> None:
        raw_user = user(bound=False)
        with (
            patch.object(auth_router, "permissions_for_user", AsyncMock(return_value={"allowed_views": []})),
            patch.object(auth_router, "get_settings", return_value=enabled_settings()),
        ):
            result = await auth_router.user_with_permissions(SimpleNamespace(), raw_user)

        self.assertTrue(result["feishu_binding_required"])


if __name__ == "__main__":
    unittest.main()
