from __future__ import annotations

import unittest
from datetime import UTC, datetime
from importlib.util import find_spec
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from app import schemas
from app.config import Settings
from app.modules.auth import feishu


class FeishuConfigurationTests(unittest.TestCase):
    def test_allowed_tenant_keys_are_normalized(self) -> None:
        settings = Settings(feishu_allowed_tenant_keys=" tenant-b,tenant-a, tenant-b ,, ")

        self.assertTrue(hasattr(settings, "allowed_feishu_tenant_keys"))
        self.assertEqual(settings.allowed_feishu_tenant_keys(), {"tenant-a", "tenant-b"})

    def test_binding_required_response_is_explicit_and_timezone_aware(self) -> None:
        self.assertTrue(hasattr(schemas, "LoginBindingRequiredResponse"))
        response_type = schemas.LoginBindingRequiredResponse
        expires_at = datetime(2026, 8, 18, 12, tzinfo=UTC)

        response = response_type(
            authorization_url="https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example",
            session_id="session-1",
            expires_at=expires_at,
        )

        self.assertEqual(response.status, "binding_required")
        self.assertEqual(response.expires_at, expires_at)

    def test_binding_required_response_rejects_naive_expiration(self) -> None:
        self.assertTrue(hasattr(schemas, "LoginBindingRequiredResponse"))
        response_type = schemas.LoginBindingRequiredResponse

        with self.assertRaises(ValueError):
            response_type(
                authorization_url="https://accounts.feishu.cn/open-apis/authen/v1/authorize?app_id=cli_example",
                session_id="session-1",
                expires_at=datetime(2026, 8, 18, 12),
            )


class FeishuAuthModuleTests(unittest.TestCase):
    def test_feishu_auth_module_exists(self) -> None:
        try:
            spec = find_spec("app.modules.auth.feishu")
        except ModuleNotFoundError:
            spec = None
        self.assertIsNotNone(spec)

    def test_identity_key_prefers_union_id_and_falls_back_to_open_id(self) -> None:
        self.assertTrue(hasattr(feishu, "FeishuIdentity"))
        identity_type = feishu.FeishuIdentity

        union_identity = identity_type(
            tenant_key="tenant-a",
            open_id="open-1",
            union_id="union-1",
            user_id="user-1",
            name="Member",
            email="member@example.com",
            avatar_url=None,
        )
        open_identity = identity_type(
            tenant_key="tenant-a",
            open_id="open-2",
            union_id=None,
            user_id=None,
            name=None,
            email=None,
            avatar_url=None,
        )

        self.assertEqual(union_identity.identity_key, "tenant-a:union:union-1")
        self.assertEqual(open_identity.identity_key, "tenant-a:open:open-2")


def feishu_settings() -> Settings:
    return Settings(
        feishu_auth_enabled=True,
        feishu_app_id="cli_example",
        feishu_app_secret="secret",
        feishu_redirect_uri="https://account.example.com/api/auth/feishu/callback",
        feishu_allowed_tenant_keys="tenant-a",
    )


def identity(*, email: str | None = "member@example.com"):
    return feishu.FeishuIdentity(
        tenant_key="tenant-a",
        open_id="open-1",
        union_id="union-1",
        user_id="user-1",
        name="Member",
        email=email,
        avatar_url="https://example.com/avatar.png",
    )


class FeishuAuthorizationSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_session_stores_only_hashed_state_and_ticket(self) -> None:
        self.assertTrue(hasattr(feishu, "create_authorization_session"))
        collection = SimpleNamespace(insert_one=AsyncMock())
        db = SimpleNamespace(feishu_auth_sessions=collection)

        with patch.object(feishu, "now_utc", return_value=datetime(2026, 8, 18, 12, tzinfo=UTC)):
            result = await feishu.create_authorization_session(
                db,
                purpose="login",
                settings=feishu_settings(),
            )

        document = collection.insert_one.await_args.args[0]
        query = parse_qs(urlparse(result.authorization_url).query)
        self.assertEqual(query["app_id"], ["cli_example"])
        self.assertEqual(query["redirect_uri"], ["https://account.example.com/api/auth/feishu/callback"])
        self.assertNotEqual(document["state_hash"], query["state"][0])
        self.assertNotEqual(document["ticket_hash"], result.ticket)
        self.assertNotIn("state", document)
        self.assertNotIn("ticket", document)

    async def test_bind_session_requires_a_target_user(self) -> None:
        self.assertTrue(hasattr(feishu, "create_authorization_session"))
        db = SimpleNamespace(feishu_auth_sessions=SimpleNamespace(insert_one=AsyncMock()))

        with self.assertRaisesRegex(ValueError, "target user"):
            await feishu.create_authorization_session(
                db,
                purpose="bind",
                settings=feishu_settings(),
            )


class FeishuIdentityResolutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_existing_identity_logs_in_without_rebinding(self) -> None:
        self.assertTrue(hasattr(feishu, "resolve_feishu_user"))
        existing = {
            "_id": "member@example.com",
            "email": "member@example.com",
            "role": "maintainer",
            "status": "active",
            "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
        }
        users = SimpleNamespace(
            find_one=AsyncMock(return_value=existing),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(),
            insert_one=AsyncMock(),
        )

        result = await feishu.resolve_feishu_user(
            SimpleNamespace(users=users),
            identity=identity(),
            purpose="login",
            target_user_id=None,
        )

        self.assertEqual(result["_id"], "member@example.com")
        users.find_one_and_update.assert_not_awaited()
        users.insert_one.assert_not_awaited()

    async def test_login_binds_existing_unbound_user_by_normalized_email(self) -> None:
        self.assertTrue(hasattr(feishu, "resolve_feishu_user"))
        existing = {
            "_id": "member@example.com",
            "email": "member@example.com",
            "role": "maintainer",
            "status": "active",
        }
        bound = {
            **existing,
            "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
        }
        users = SimpleNamespace(
            find_one=AsyncMock(side_effect=[None, existing]),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(return_value=bound),
            insert_one=AsyncMock(),
        )

        result = await feishu.resolve_feishu_user(
            SimpleNamespace(users=users),
            identity=identity(email=" Member@Example.com "),
            purpose="login",
            target_user_id=None,
        )

        self.assertEqual(result["_id"], "member@example.com")
        write_filter = users.find_one_and_update.await_args.args[0]
        self.assertEqual(write_filter["_id"], "member@example.com")
        self.assertEqual(write_filter["feishu_identity.identity_key"], {"$exists": False})

    async def test_password_binding_targets_authenticated_user_even_when_email_differs(self) -> None:
        self.assertTrue(hasattr(feishu, "resolve_feishu_user"))
        existing = {
            "_id": "local@example.com",
            "email": "local@example.com",
            "role": "maintainer",
            "status": "active",
        }
        bound = {
            **existing,
            "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
        }
        users = SimpleNamespace(
            find_one=AsyncMock(side_effect=[None, existing]),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(return_value=bound),
            insert_one=AsyncMock(),
        )

        result = await feishu.resolve_feishu_user(
            SimpleNamespace(users=users),
            identity=identity(email="different@example.com"),
            purpose="bind",
            target_user_id="local@example.com",
        )

        self.assertEqual(result["_id"], "local@example.com")
        self.assertEqual(users.find_one.await_args_list[1].args[0], {"_id": "local@example.com"})

    async def test_unknown_identity_creates_pending_user(self) -> None:
        self.assertTrue(hasattr(feishu, "resolve_feishu_user"))
        users = SimpleNamespace(
            find_one=AsyncMock(side_effect=[None, None]),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(),
            insert_one=AsyncMock(),
        )

        result = await feishu.resolve_feishu_user(
            SimpleNamespace(users=users),
            identity=identity(email=None),
            purpose="login",
            target_user_id=None,
        )

        self.assertEqual(result["authorization_status"], "pending")
        self.assertEqual(result["role"], "viewer")
        self.assertTrue(result["email"].endswith("@identity.invalid"))
        users.insert_one.assert_awaited_once()

    async def test_disabled_identity_is_rejected(self) -> None:
        self.assertTrue(hasattr(feishu, "resolve_feishu_user"))
        users = SimpleNamespace(
            find_one=AsyncMock(return_value={"_id": "member@example.com", "status": "disabled"}),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(),
            insert_one=AsyncMock(),
        )

        with self.assertRaisesRegex(feishu.FeishuAuthError, "禁用"):
            await feishu.resolve_feishu_user(
                SimpleNamespace(users=users),
                identity=identity(),
                purpose="login",
                target_user_id=None,
            )


class FeishuTicketTests(unittest.IsolatedAsyncioTestCase):
    async def test_ticket_is_consumed_once_and_returns_user(self) -> None:
        self.assertTrue(hasattr(feishu, "consume_login_ticket"))
        auth_sessions = SimpleNamespace(
            find_one_and_update=AsyncMock(return_value={"result_user_id": "member@example.com"})
        )
        users = SimpleNamespace(
            find_one=AsyncMock(
                return_value={"_id": "member@example.com", "status": "active", "role": "maintainer"}
            )
        )

        result = await feishu.consume_login_ticket(
            SimpleNamespace(feishu_auth_sessions=auth_sessions, users=users),
            ticket="ticket-1",
        )

        self.assertEqual(result["_id"], "member@example.com")
        consume_filter = auth_sessions.find_one_and_update.await_args.args[0]
        self.assertEqual(consume_filter["status"], "completed")
        self.assertEqual(consume_filter["consumed_at"], {"$exists": False})


class _FakeHttpResponse:
    def __init__(self, payload: dict, *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FeishuOAuthExchangeTests(unittest.IsolatedAsyncioTestCase):
    async def test_oauth_v2_exchange_returns_normalized_identity(self) -> None:
        self.assertTrue(hasattr(feishu, "fetch_feishu_identity"))
        client = SimpleNamespace(
            post=AsyncMock(
                return_value=_FakeHttpResponse(
                    {"code": 0, "access_token": "user-token", "token_type": "Bearer"}
                )
            ),
            get=AsyncMock(
                return_value=_FakeHttpResponse(
                    {
                        "code": 0,
                        "data": {
                            "tenant_key": "tenant-a",
                            "open_id": "open-1",
                            "union_id": "union-1",
                            "user_id": "user-1",
                            "name": "Member",
                            "enterprise_email": " Member@Example.com ",
                            "avatar_url": "https://example.com/avatar.png",
                        },
                    }
                )
            ),
        )

        result = await feishu.fetch_feishu_identity(
            code="authorization-code",
            settings=feishu_settings(),
            http_client=client,
        )

        self.assertEqual(result.email, "member@example.com")
        self.assertEqual(result.identity_key, "tenant-a:union:union-1")
        token_request = client.post.await_args
        self.assertTrue(token_request.args[0].endswith("/open-apis/authen/v2/oauth/token"))
        self.assertEqual(token_request.kwargs["json"]["grant_type"], "authorization_code")
        self.assertEqual(client.get.await_args.kwargs["headers"]["Authorization"], "Bearer user-token")

    async def test_oauth_exchange_rejects_unapproved_tenant(self) -> None:
        self.assertTrue(hasattr(feishu, "fetch_feishu_identity"))
        client = SimpleNamespace(
            post=AsyncMock(return_value=_FakeHttpResponse({"code": 0, "access_token": "user-token"})),
            get=AsyncMock(
                return_value=_FakeHttpResponse(
                    {
                        "code": 0,
                        "data": {
                            "tenant_key": "tenant-other",
                            "open_id": "open-1",
                        },
                    }
                )
            ),
        )

        with self.assertRaisesRegex(feishu.FeishuAuthError, "租户"):
            await feishu.fetch_feishu_identity(
                code="authorization-code",
                settings=feishu_settings(),
                http_client=client,
            )


class FeishuCallbackCompletionTests(unittest.IsolatedAsyncioTestCase):
    async def test_callback_claims_state_once_and_completes_session(self) -> None:
        self.assertTrue(hasattr(feishu, "complete_authorization_session"))
        session = {
            "_id": "session-1",
            "purpose": "login",
            "target_user_id": None,
            "status": "processing",
        }
        auth_sessions = SimpleNamespace(
            find_one_and_update=AsyncMock(return_value=session),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(feishu_auth_sessions=auth_sessions)
        resolved_user = {"_id": "member@example.com", "status": "active"}

        with (
            patch.object(feishu, "fetch_feishu_identity", AsyncMock(return_value=identity())),
            patch.object(feishu, "resolve_feishu_user", AsyncMock(return_value=resolved_user)),
            patch.object(feishu, "now_utc", return_value=datetime(2026, 8, 18, 12, tzinfo=UTC)),
        ):
            result = await feishu.complete_authorization_session(
                db,
                state="state-1",
                code="authorization-code",
                settings=feishu_settings(),
                http_client=SimpleNamespace(),
            )

        self.assertEqual(result, "session-1")
        claim_filter = auth_sessions.find_one_and_update.await_args.args[0]
        self.assertEqual(claim_filter["status"], "pending")
        completion = auth_sessions.update_one.await_args.args[1]["$set"]
        self.assertEqual(completion["status"], "completed")
        self.assertEqual(completion["result_user_id"], "member@example.com")
        self.assertEqual(completion["ticket_expires_at"], datetime(2026, 8, 18, 12, 1, tzinfo=UTC))

    async def test_replayed_callback_state_is_rejected(self) -> None:
        self.assertTrue(hasattr(feishu, "complete_authorization_session"))
        db = SimpleNamespace(
            feishu_auth_sessions=SimpleNamespace(
                find_one_and_update=AsyncMock(return_value=None),
                update_one=AsyncMock(),
            )
        )

        with self.assertRaisesRegex(feishu.FeishuAuthError, "过期|使用"):
            await feishu.complete_authorization_session(
                db,
                state="state-1",
                code="authorization-code",
                settings=feishu_settings(),
                http_client=SimpleNamespace(),
            )

    async def test_session_status_requires_matching_ticket(self) -> None:
        self.assertTrue(hasattr(feishu, "get_authorization_session_status"))
        auth_sessions = SimpleNamespace(
            find_one=AsyncMock(
                return_value={
                    "_id": "session-1",
                    "status": "completed",
                    "expires_at": datetime(2026, 8, 18, 12, 5, tzinfo=UTC),
                    "ticket_expires_at": datetime(2026, 8, 18, 12, 1, tzinfo=UTC),
                }
            )
        )

        with patch.object(feishu, "now_utc", return_value=datetime(2026, 8, 18, 12, tzinfo=UTC)):
            result = await feishu.get_authorization_session_status(
                SimpleNamespace(feishu_auth_sessions=auth_sessions),
                session_id="session-1",
                ticket="ticket-1",
            )

        self.assertEqual(result["status"], "completed")
        status_filter = auth_sessions.find_one.await_args.args[0]
        self.assertEqual(status_filter["_id"], "session-1")
        self.assertNotEqual(status_filter["ticket_hash"], "ticket-1")


if __name__ == "__main__":
    unittest.main()
