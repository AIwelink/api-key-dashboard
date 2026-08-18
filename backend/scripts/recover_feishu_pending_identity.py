from __future__ import annotations

import argparse
import asyncio
import json
from typing import Any

from app.database import close_mongo_connection, connect_to_mongo, get_db
from app.modules.auth.feishu import FeishuIdentity, has_feishu_binding, resolve_feishu_user


def safe_summary(user: dict[str, Any] | None) -> dict[str, Any] | None:
    if user is None:
        return None
    return {
        "id": str(user.get("_id")),
        "name": user.get("name"),
        "role": user.get("role"),
        "status": user.get("status"),
        "authorization_status": user.get("authorization_status"),
        "email_is_placeholder": bool(user.get("email_is_placeholder")),
        "created_by": user.get("created_by"),
        "feishu_bound": has_feishu_binding(user),
        "merged_into_user_id": user.get("merged_into_user_id"),
    }


async def recover(
    db: Any,
    *,
    source_user_id: str,
    target_user_id: str,
) -> dict[str, Any]:
    if source_user_id == target_user_id:
        raise ValueError("source and target users must be different")

    source = await db.users.find_one({"_id": source_user_id})
    target = await db.users.find_one({"_id": target_user_id})
    if source is None:
        raise ValueError("source user does not exist")
    if target is None:
        raise ValueError("target user does not exist")

    stored_identity = source.get("feishu_identity") or {}
    identity = FeishuIdentity(
        tenant_key=_required_text(stored_identity, "tenant_key"),
        open_id=_required_text(stored_identity, "open_id"),
        union_id=_optional_text(stored_identity.get("union_id")),
        user_id=_optional_text(stored_identity.get("user_id")),
        name=_optional_text(stored_identity.get("name")),
        email=_optional_text(stored_identity.get("email")),
        avatar_url=_optional_text(stored_identity.get("avatar_url")),
    )
    if stored_identity.get("identity_key") != identity.identity_key:
        raise ValueError("stored Feishu identity does not match its identity key")

    return await resolve_feishu_user(
        db,
        identity=identity,
        purpose="bind",
        target_user_id=target_user_id,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(
        description="Recover an auto-provisioned pending Feishu identity into a verified local user."
    )
    parser.add_argument("--source-user-id", required=True)
    parser.add_argument("--target-user-id", required=True)
    parser.add_argument("--yes", action="store_true", help="apply the recovery instead of previewing it")
    args = parser.parse_args()

    await connect_to_mongo()
    try:
        db = get_db()
        source = await db.users.find_one({"_id": args.source_user_id})
        target = await db.users.find_one({"_id": args.target_user_id})
        print(
            json.dumps(
                {
                    "mode": "apply" if args.yes else "preview",
                    "source": safe_summary(source),
                    "target": safe_summary(target),
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )
        if not args.yes:
            return

        recovered = await recover(
            db,
            source_user_id=args.source_user_id,
            target_user_id=args.target_user_id,
        )
        final_source = await db.users.find_one({"_id": args.source_user_id})
        print(
            json.dumps(
                {
                    "mode": "applied",
                    "source": safe_summary(final_source),
                    "target": safe_summary(recovered),
                },
                ensure_ascii=False,
                default=str,
                indent=2,
            )
        )
    finally:
        await close_mongo_connection()


def _required_text(mapping: dict[str, Any], key: str) -> str:
    value = _optional_text(mapping.get(key))
    if value is None:
        raise ValueError(f"stored Feishu identity is missing {key}")
    return value


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


if __name__ == "__main__":
    asyncio.run(main())
