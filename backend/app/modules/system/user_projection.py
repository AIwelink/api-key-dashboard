from app.utils import serialize_doc


def public_user(user: dict) -> dict:
    data = serialize_doc(user)
    data.pop("password_hash", None)
    if data.get("email_is_placeholder"):
        data["email"] = None
    identity = data.pop("feishu_identity", None) or {}
    data.update(
        {
            "feishu_bound": bool(identity.get("identity_key")),
            "feishu_name": identity.get("name"),
            "feishu_avatar_url": identity.get("avatar_url"),
            "feishu_email": identity.get("email"),
            "feishu_bound_at": identity.get("bound_at"),
        }
    )
    return data
