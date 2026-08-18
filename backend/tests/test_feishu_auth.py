from __future__ import annotations

import unittest
from datetime import UTC, datetime
from importlib.util import find_spec
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlparse

from fastapi import HTTPException

from app import schemas
from app import security
from app.config import Settings
from app.logging_config import SENSITIVE_KEYS, _redact_mapping
from app.modules.auth import feishu
from app.modules.system import bootstrap
from app.modules.system.user_projection import public_user


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
            ticket="ticket-token-at-least-20-characters",
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

        with patch.object(feishu, "write_audit_log", AsyncMock(), create=True) as audit_mock:
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
        self.assertEqual(audit_mock.await_args.kwargs["action"], "auth.feishu.user_bound")
        self.assertEqual(audit_mock.await_args.kwargs["resource_id"], "member@example.com")
        self.assertEqual(
            audit_mock.await_args.kwargs["after"],
            {"result_code": "bound", "bound_via": "feishu_email"},
        )

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

        with patch.object(feishu, "write_audit_log", AsyncMock()) as audit_mock:
            result = await feishu.resolve_feishu_user(
                SimpleNamespace(users=users),
                identity=identity(email="different@example.com"),
                purpose="bind",
                target_user_id="local@example.com",
            )

        self.assertEqual(result["_id"], "local@example.com")
        self.assertEqual(users.find_one.await_args_list[1].args[0], {"_id": "local@example.com"})
        self.assertEqual(audit_mock.await_args.kwargs["action"], "auth.feishu.user_bound")
        self.assertEqual(
            audit_mock.await_args.kwargs["after"],
            {"result_code": "bound", "bound_via": "password_binding"},
        )

    async def test_password_binding_recovers_auto_provisioned_pending_identity(self) -> None:
        source = {
            "_id": "feishu-pending",
            "email": "feishu-pending@identity.invalid",
            "email_is_placeholder": True,
            "name": "Feishu Member",
            "role": "viewer",
            "status": "active",
            "authorization_status": "pending",
            "created_by": "feishu",
            "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
        }
        target = {
            "_id": "owner@example.com",
            "email": "owner@example.com",
            "name": "Owner",
            "role": "owner",
            "status": "active",
            "authorization_status": "active",
        }
        merged_source = {
            **source,
            "status": "disabled",
            "merged_into_user_id": target["_id"],
        }
        recovered_target = {
            **target,
            "feishu_identity": {
                "source_user_id": source["_id"],
                "bound_via": "password_binding_recovery",
            },
        }
        users = SimpleNamespace(
            find_one=AsyncMock(side_effect=[source, target]),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(side_effect=[merged_source, recovered_target]),
            insert_one=AsyncMock(),
        )
        timestamp = datetime(2026, 8, 18, 14, 30, tzinfo=UTC)

        with (
            patch.object(feishu, "now_utc", return_value=timestamp),
            patch.object(feishu, "write_audit_log", AsyncMock()) as audit_mock,
        ):
            result = await feishu.resolve_feishu_user(
                SimpleNamespace(users=users),
                identity=identity(email=None),
                purpose="bind",
                target_user_id=target["_id"],
            )

        self.assertEqual(result["_id"], target["_id"])
        source_call, target_call = users.find_one_and_update.await_args_list
        self.assertEqual(
            source_call.args[0],
            {
                "_id": source["_id"],
                "feishu_identity.identity_key": "tenant-a:union:union-1",
                "created_by": "feishu",
                "authorization_status": "pending",
                "email_is_placeholder": True,
                "role": "viewer",
                "status": "active",
                "merged_into_user_id": {"$exists": False},
            },
        )
        self.assertEqual(source_call.args[1]["$set"]["merged_into_user_id"], target["_id"])
        self.assertEqual(source_call.args[1]["$set"]["status"], "disabled")
        target_update = target_call.args[1]["$set"]
        self.assertEqual(target_update["feishu_identity.source_user_id"], source["_id"])
        self.assertEqual(target_update["feishu_identity.bound_via"], "password_binding_recovery")
        self.assertNotIn("feishu_identity.identity_key", target_update)
        self.assertEqual(audit_mock.await_args.kwargs["action"], "auth.feishu.pending_identity_merged")
        self.assertEqual(
            audit_mock.await_args.kwargs["after"],
            {
                "result_code": "pending_identity_merged",
                "source_user_id": source["_id"],
                "bound_via": "password_binding_recovery",
            },
        )

    async def test_merged_identity_login_resolves_active_target(self) -> None:
        source = {
            "_id": "feishu-pending",
            "status": "disabled",
            "merged_into_user_id": "owner@example.com",
            "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
        }
        target = {
            "_id": "owner@example.com",
            "status": "active",
            "role": "owner",
            "feishu_identity": {"source_user_id": source["_id"]},
        }
        users = SimpleNamespace(
            find_one=AsyncMock(side_effect=[source, target]),
            update_one=AsyncMock(return_value=SimpleNamespace(matched_count=1)),
            find_one_and_update=AsyncMock(),
            insert_one=AsyncMock(),
        )

        result = await feishu.resolve_feishu_user(
            SimpleNamespace(users=users),
            identity=identity(email=None),
            purpose="login",
            target_user_id=None,
        )

        self.assertEqual(result["_id"], target["_id"])
        self.assertEqual(
            users.update_one.await_args.args[0],
            {
                "_id": target["_id"],
                "status": {"$ne": "disabled"},
                "feishu_identity.source_user_id": source["_id"],
            },
        )

    async def test_same_target_binding_completes_interrupted_proxy_merge(self) -> None:
        source = {
            "_id": "feishu-pending",
            "status": "disabled",
            "merged_into_user_id": "owner@example.com",
            "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
        }
        target = {
            "_id": "owner@example.com",
            "status": "active",
            "role": "owner",
        }
        recovered_target = {
            **target,
            "feishu_identity": {"source_user_id": source["_id"]},
        }
        users = SimpleNamespace(
            find_one=AsyncMock(side_effect=[source, target]),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(return_value=recovered_target),
            insert_one=AsyncMock(),
        )

        result = await feishu.resolve_feishu_user(
            SimpleNamespace(users=users),
            identity=identity(email=None),
            purpose="bind",
            target_user_id=target["_id"],
        )

        self.assertEqual(result["_id"], target["_id"])
        self.assertEqual(
            users.find_one_and_update.await_args.args[0]["_id"],
            target["_id"],
        )

    async def test_password_binding_does_not_take_over_nonrecoverable_identity(self) -> None:
        base_source = {
            "_id": "feishu-pending",
            "email": "feishu-pending@identity.invalid",
            "email_is_placeholder": True,
            "role": "viewer",
            "status": "active",
            "authorization_status": "pending",
            "created_by": "feishu",
            "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
        }
        cases = {
            "active authorization": {"authorization_status": "active"},
            "real email": {"email_is_placeholder": False},
            "privileged role": {"role": "maintainer"},
            "not auto provisioned": {"created_by": "admin@example.com"},
            "disabled source": {"status": "disabled"},
        }

        for label, changes in cases.items():
            with self.subTest(label=label):
                source = {**base_source, **changes}
                users = SimpleNamespace(
                    find_one=AsyncMock(return_value=source),
                    update_one=AsyncMock(),
                    find_one_and_update=AsyncMock(),
                    insert_one=AsyncMock(),
                )
                with self.assertRaises(feishu.FeishuBindingConflictError) as raised:
                    await feishu.resolve_feishu_user(
                        SimpleNamespace(users=users),
                        identity=identity(email=None),
                        purpose="bind",
                        target_user_id="owner@example.com",
                    )

                self.assertEqual(raised.exception.code, "identity_already_bound")
                users.find_one_and_update.assert_not_awaited()

    async def test_password_binding_recovery_rejects_disabled_target(self) -> None:
        source = {
            "_id": "feishu-pending",
            "email_is_placeholder": True,
            "role": "viewer",
            "status": "active",
            "authorization_status": "pending",
            "created_by": "feishu",
            "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
        }
        target = {
            "_id": "owner@example.com",
            "status": "disabled",
            "role": "owner",
        }
        users = SimpleNamespace(
            find_one=AsyncMock(side_effect=[source, target]),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(),
            insert_one=AsyncMock(),
        )

        with self.assertRaises(feishu.FeishuAuthError) as raised:
            await feishu.resolve_feishu_user(
                SimpleNamespace(users=users),
                identity=identity(email=None),
                purpose="bind",
                target_user_id=target["_id"],
            )

        self.assertEqual(raised.exception.code, "user_disabled")
        users.find_one_and_update.assert_not_awaited()

    async def test_password_binding_recovery_rejects_target_with_another_identity(self) -> None:
        source = {
            "_id": "feishu-pending",
            "email_is_placeholder": True,
            "role": "viewer",
            "status": "active",
            "authorization_status": "pending",
            "created_by": "feishu",
            "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
        }
        target = {
            "_id": "owner@example.com",
            "status": "active",
            "role": "owner",
            "feishu_identity": {"identity_key": "tenant-a:union:another-identity"},
        }
        users = SimpleNamespace(
            find_one=AsyncMock(side_effect=[source, target]),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(),
            insert_one=AsyncMock(),
        )

        with self.assertRaises(feishu.FeishuBindingConflictError) as raised:
            await feishu.resolve_feishu_user(
                SimpleNamespace(users=users),
                identity=identity(email=None),
                purpose="bind",
                target_user_id=target["_id"],
            )

        self.assertEqual(raised.exception.code, "user_already_bound")
        users.find_one_and_update.assert_not_awaited()

    async def test_unknown_identity_creates_pending_user(self) -> None:
        self.assertTrue(hasattr(feishu, "resolve_feishu_user"))
        users = SimpleNamespace(
            find_one=AsyncMock(side_effect=[None, None]),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(),
            insert_one=AsyncMock(),
        )

        with patch.object(feishu, "write_audit_log", AsyncMock(), create=True) as audit_mock:
            result = await feishu.resolve_feishu_user(
                SimpleNamespace(users=users),
                identity=identity(email=None),
                purpose="login",
                target_user_id=None,
            )

        self.assertEqual(result["authorization_status"], "pending")
        self.assertEqual(result["role"], "viewer")
        self.assertTrue(result["email"].endswith("@identity.invalid"))
        self.assertTrue(result["email_is_placeholder"])
        users.insert_one.assert_awaited_once()
        self.assertEqual(audit_mock.await_args.kwargs["action"], "auth.feishu.user_auto_created")
        self.assertEqual(
            audit_mock.await_args.kwargs["after"],
            {"result_code": "pending_authorization"},
        )

    async def test_concurrent_pending_user_creation_returns_unique_identity_winner(self) -> None:
        concurrent = {
            "_id": "feishu-existing",
            "email": "feishu-existing@identity.invalid",
            "email_is_placeholder": True,
            "status": "active",
            "authorization_status": "pending",
            "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
        }
        users = SimpleNamespace(
            find_one=AsyncMock(side_effect=[None, concurrent]),
            update_one=AsyncMock(),
            find_one_and_update=AsyncMock(),
            insert_one=AsyncMock(side_effect=feishu.DuplicateKeyError("duplicate identity")),
        )

        result = await feishu.resolve_feishu_user(
            SimpleNamespace(users=users),
            identity=identity(email=None),
            purpose="login",
            target_user_id=None,
        )

        self.assertEqual(result["_id"], "feishu-existing")

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

    async def test_replayed_ticket_emits_redacted_security_audits(self) -> None:
        auth_sessions = SimpleNamespace(
            find_one_and_update=AsyncMock(return_value=None),
            find_one=AsyncMock(
                return_value={
                    "_id": "session-1",
                    "result_user_id": "member@example.com",
                    "consumed_at": datetime(2026, 8, 18, 12, tzinfo=UTC),
                }
            ),
        )
        db = SimpleNamespace(feishu_auth_sessions=auth_sessions, users=SimpleNamespace())

        with patch.object(feishu, "write_audit_log", AsyncMock(), create=True) as audit_mock:
            with self.assertRaisesRegex(feishu.FeishuAuthError, "已使用"):
                await feishu.consume_login_ticket(db, ticket="secret-ticket-value")

        actions = [call.kwargs["action"] for call in audit_mock.await_args_list]
        self.assertEqual(actions, ["auth.feishu.ticket_replayed", "auth.feishu.login_failed"])
        for call in audit_mock.await_args_list:
            self.assertEqual(call.kwargs["resource_id"], "member@example.com")
            self.assertEqual(call.kwargs["after"]["session_id"], "session-1")
            self.assertIn("session-1", call.kwargs["dedupe_key"])
            self.assertNotIn("secret-ticket-value", repr(call.kwargs))

    async def test_unknown_ticket_is_rejected_without_persisting_unbounded_audit(self) -> None:
        auth_sessions = SimpleNamespace(
            find_one_and_update=AsyncMock(return_value=None),
            find_one=AsyncMock(return_value=None),
        )
        db = SimpleNamespace(feishu_auth_sessions=auth_sessions, users=SimpleNamespace())

        with patch.object(feishu, "write_audit_log", AsyncMock()) as audit_mock:
            with self.assertRaisesRegex(feishu.FeishuAuthError, "无效"):
                await feishu.consume_login_ticket(db, ticket="random-unknown-ticket")

        audit_mock.assert_not_awaited()


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

    async def test_binding_conflict_emits_conflict_and_failed_audits_without_identity_secrets(self) -> None:
        session = {
            "_id": "session-1",
            "purpose": "bind",
            "target_user_id": "member@example.com",
            "status": "processing",
        }
        auth_sessions = SimpleNamespace(
            find_one_and_update=AsyncMock(return_value=session),
            update_one=AsyncMock(),
        )
        db = SimpleNamespace(feishu_auth_sessions=auth_sessions)

        with (
            patch.object(feishu, "fetch_feishu_identity", AsyncMock(return_value=identity())),
            patch.object(
                feishu,
                "resolve_feishu_user",
                AsyncMock(
                    side_effect=feishu.FeishuBindingConflictError(
                        "binding conflict",
                        code="identity_already_bound",
                    )
                ),
            ),
            patch.object(feishu, "write_audit_log", AsyncMock(), create=True) as audit_mock,
        ):
            with self.assertRaises(feishu.FeishuBindingConflictError):
                await feishu.complete_authorization_session(
                    db,
                    state="secret-state",
                    code="secret-code",
                    settings=feishu_settings(),
                    http_client=SimpleNamespace(),
                )

        actions = [call.kwargs["action"] for call in audit_mock.await_args_list]
        self.assertEqual(actions, ["auth.feishu.binding_conflict", "auth.feishu.login_failed"])
        for call in audit_mock.await_args_list:
            self.assertEqual(call.kwargs["resource_id"], "member@example.com")
            self.assertEqual(
                call.kwargs["after"],
                {"result_code": "identity_already_bound", "session_id": "session-1"},
            )
            serialized = repr(call.kwargs)
            for secret in ("secret-state", "secret-code", "open-1", "union-1"):
                self.assertNotIn(secret, serialized)

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

    async def test_cancelled_callback_marks_pending_session_failed(self) -> None:
        self.assertTrue(hasattr(feishu, "fail_authorization_session"))
        auth_sessions = SimpleNamespace(
            find_one_and_update=AsyncMock(return_value={"_id": "session-1", "status": "failed"})
        )

        with patch.object(feishu, "write_audit_log", AsyncMock()) as audit_mock:
            result = await feishu.fail_authorization_session(
                SimpleNamespace(feishu_auth_sessions=auth_sessions),
                state="state-1",
                error_code="access_denied",
            )

        self.assertEqual(result, "session-1")
        update = auth_sessions.find_one_and_update.await_args.args[1]["$set"]
        self.assertEqual(update["status"], "failed")
        self.assertEqual(update["error_code"], "access_denied")
        self.assertEqual(audit_mock.await_args.kwargs["action"], "auth.feishu.login_failed")
        self.assertEqual(
            audit_mock.await_args.kwargs["after"],
            {"result_code": "access_denied", "session_id": "session-1"},
        )

    async def test_untrusted_callback_error_is_normalized_before_storage_and_audit(self) -> None:
        malicious_error = "code=secret&state=secret&ticket=secret&open_id=secret&union_id=secret"
        auth_sessions = SimpleNamespace(
            find_one_and_update=AsyncMock(
                return_value={"_id": "session-1", "status": "failed"}
            )
        )

        with patch.object(feishu, "write_audit_log", AsyncMock()) as audit_mock:
            result = await feishu.fail_authorization_session(
                SimpleNamespace(feishu_auth_sessions=auth_sessions),
                state="state-1",
                error_code=malicious_error,
            )

        self.assertEqual(result, "session-1")
        update = auth_sessions.find_one_and_update.await_args.args[1]["$set"]
        self.assertEqual(update["error_code"], "oauth_error")
        self.assertEqual(
            audit_mock.await_args.kwargs["after"],
            {"result_code": "oauth_error", "session_id": "session-1"},
        )
        self.assertNotIn(malicious_error, repr(audit_mock.await_args.kwargs))


class PendingAuthorizationBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_authenticated_lookup_allows_pending_user_for_auth_status(self) -> None:
        self.assertTrue(hasattr(security, "get_authenticated_user"))
        pending = {
            "_id": "pending-user",
            "status": "active",
            "authorization_status": "pending",
            "role": "viewer",
        }
        db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=pending)))
        credentials = SimpleNamespace(credentials="jwt-token")

        with patch.object(security, "decode_access_token", return_value={"sub": "pending-user"}):
            result = await security.get_authenticated_user(credentials=credentials, db=db)

        self.assertEqual(result, pending)

    async def test_business_lookup_rejects_pending_user(self) -> None:
        pending = {
            "_id": "pending-user",
            "status": "active",
            "authorization_status": "pending",
            "role": "viewer",
        }
        db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=pending)))
        credentials = SimpleNamespace(credentials="jwt-token")

        with patch.object(security, "decode_access_token", return_value={"sub": "pending-user"}):
            with self.assertRaises(HTTPException) as raised:
                await security.get_current_user(credentials=credentials, db=db)

        self.assertEqual(raised.exception.status_code, 403)
        self.assertEqual(raised.exception.detail, "尚未分配系统权限，请联系管理员")

    async def test_legacy_user_without_authorization_status_remains_authorized(self) -> None:
        legacy = {"_id": "member@example.com", "status": "active", "role": "maintainer"}
        db = SimpleNamespace(users=SimpleNamespace(find_one=AsyncMock(return_value=legacy)))
        credentials = SimpleNamespace(credentials="jwt-token")

        with patch.object(security, "decode_access_token", return_value={"sub": "member@example.com"}):
            result = await security.get_current_user(credentials=credentials, db=db)

        self.assertEqual(result, legacy)

    async def test_api_token_actor_is_not_blocked_by_user_authorization_status(self) -> None:
        actor = {
            "_id": "api_token:token-1",
            "actor_type": "api_token",
            "role": "viewer",
            "status": "active",
        }
        credentials = SimpleNamespace(credentials="akd_secret")

        with patch.object(security, "get_api_token_actor", AsyncMock(return_value=actor)):
            result = await security.get_current_user(credentials=credentials, db=SimpleNamespace())

        self.assertEqual(result, actor)


class FeishuStorageAndRedactionTests(unittest.IsolatedAsyncioTestCase):
    def test_placeholder_email_is_hidden_from_public_user_projection(self) -> None:
        result = public_user(
            {
                "_id": "feishu-user",
                "email": "feishu-user@identity.invalid",
                "email_is_placeholder": True,
                "feishu_identity": {"identity_key": "tenant-a:union:union-1"},
            }
        )

        self.assertIsNone(result["email"])
        self.assertTrue(result["email_is_placeholder"])

    async def test_storage_backfills_legacy_users_and_creates_partial_unique_indexes(self) -> None:
        self.assertTrue(hasattr(bootstrap, "ensure_feishu_auth_storage"))
        users = SimpleNamespace(update_many=AsyncMock(), create_index=AsyncMock())
        sessions = SimpleNamespace(create_index=AsyncMock())
        db = SimpleNamespace(users=users, feishu_auth_sessions=sessions)

        await bootstrap.ensure_feishu_auth_storage(db)

        users.update_many.assert_awaited_once_with(
            {"authorization_status": {"$exists": False}},
            {"$set": {"authorization_status": "active"}},
        )
        identity_index = users.create_index.await_args_list[0]
        self.assertEqual(identity_index.args[0], "feishu_identity.identity_key")
        self.assertTrue(identity_index.kwargs["unique"])
        self.assertEqual(
            identity_index.kwargs["partialFilterExpression"],
            {"feishu_identity.identity_key": {"$type": "string"}},
        )
        sessions.create_index.assert_any_await("expires_at", expireAfterSeconds=0)
        sessions.create_index.assert_any_await("state_hash", unique=True)
        sessions.create_index.assert_any_await("ticket_hash", unique=True)

    def test_feishu_callback_secrets_are_redacted(self) -> None:
        for key in ("code", "state", "ticket", "open_id", "union_id"):
            with self.subTest(key=key):
                self.assertIn(key, SENSITIVE_KEYS)
        self.assertEqual(
            _redact_mapping(
                {
                    "code": "authorization-code",
                    "state": "state-token",
                    "ticket": "login-ticket",
                    "open_id": "open-1",
                    "union_id": "union-1",
                }
            ),
            {
                "code": "***",
                "state": "***",
                "ticket": "***",
                "open_id": "***",
                "union_id": "***",
            },
        )


if __name__ == "__main__":
    unittest.main()
