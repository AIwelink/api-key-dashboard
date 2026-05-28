from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import get_settings
from app.security import hash_password
from app.utils import now_utc


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.users.create_index("email", unique=True)
    await db.accounts.create_index("metadata.email")
    await db.accounts.create_index("metadata.account_status")
    await db.accounts.create_index("metadata.payment_type")
    await db.accounts.create_index("metadata.self_produced")
    await db.accounts.create_index("metadata.purchase_source")
    await db.accounts.create_index("metadata.updated_at")
    await db.accounts.create_index("metadata.pool_status")
    await db.accounts.create_index("metadata.pool_id")
    await db.accounts.create_index("metadata.priority")
    await db.accounts.create_index("metadata.upload_intent")
    await db.accounts.create_index("metadata.sub2api_account_id")
    await db.accounts.create_index("metadata.sub2api_site_id")
    await db.accounts.create_index([("metadata.sub2api_site_id", 1), ("metadata.pool_id", 1), ("metadata.pool_status", 1)])
    await db.accounts.create_index("metadata.push_lock")
    await db.accounts.create_index("metadata.sub2api_return_lock")
    await db.accounts.create_index("metadata.sub2api_delete_status")
    await db.accounts.create_index("metadata.verification_lock")
    await db.accounts.create_index("metadata.verification_status")
    await db.accounts.create_index("metadata.verification_group_id")
    await db.accounts.create_index("metadata.upgrade_task_type")
    await db.accounts.create_index("metadata.upgrade_status")
    await db.accounts.create_index("metadata.upgrade_lock.expires_at")
    await db.accounts.create_index("metadata.upgrade_lock.locked_by_user_id")
    await db.accounts.create_index([("metadata.account_type", 1), ("metadata.pool_status", 1), ("metadata.updated_at", -1)])
    await db.accounts.create_index([("metadata.upgrade_task_type", 1), ("metadata.upgrade_status", 1), ("metadata.pool_status", 1), ("metadata.updated_at", -1)])
    await db.audit_logs.create_index("created_at")
    await db.import_batches.create_index("created_at")
    await db.import_batches.create_index("uploaded_by_user_id")
    await db.import_batches.create_index("status")
    await db.api_pools.create_index("status")
    await db.api_pools.create_index("site_id")
    await db.api_pools.create_index("active_group_id")
    await db.pool_actions.create_index("account_id")
    await db.pool_actions.create_index("pool_id")
    await db.pool_actions.create_index("action_type")
    await db.pool_actions.create_index("created_at")
    await db.account_operations.create_index("account_id")
    await db.account_operations.create_index("operation_class")
    await db.account_operations.create_index("occurred_at")
    await db.account_problems.create_index("account_id")
    await db.account_problems.create_index("problem_class")
    await db.account_problems.create_index("status")
    await db.account_problems.create_index("occurred_at")
    await db.operation_locks.create_index("expires_at", expireAfterSeconds=0)
    await db.todo_items.create_index([("dedupe_key", 1), ("status", 1)])
    await db.todo_items.create_index("pool_id")
    await db.todo_items.create_index("todo_type")
    await db.sub2api_sites.create_index("status")
    await db.sub2api_groups_cache.create_index([("site_id", 1), ("group_id", 1)], unique=True)
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("group_ids", 1), ("status", 1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("sub2api_account_id", 1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("plan_type", 1), ("status", 1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("email", 1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("subscription_expires_at", 1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("codex_7d_used_percent", -1), ("codex_5h_used_percent", -1)])
    await db.sub2api_cache_meta.create_index("fetched_at")
    await db.sub2api_dashboard_trends.create_index([("site_id", 1), ("granularity", 1), ("bucket_at", 1)])
    await db.sub2api_dashboard_trends.create_index([("site_id", 1), ("range_type", 1), ("bucket_at", 1)])
    await db.sub2api_dashboard_models.create_index([("site_id", 1), ("range_type", 1), ("model", 1)])
    await db.sub2api_dashboard_snapshots.create_index([("site_id", 1), ("range_type", 1)])
    await db.sub2api_dashboard_meta.create_index("updated_at")


async def ensure_initial_owner(db: AsyncIOMotorDatabase) -> None:
    settings = get_settings()
    users_count = await db.users.count_documents({})
    if users_count > 0:
        return
    if not settings.initial_owner_email or not settings.initial_owner_password:
        return

    now = now_utc()
    await db.users.insert_one(
        {
            "_id": settings.initial_owner_email.lower(),
            "email": settings.initial_owner_email.lower(),
            "name": settings.initial_owner_name,
            "role": "owner",
            "password_hash": hash_password(settings.initial_owner_password),
            "status": "active",
            "must_change_password": True,
            "created_by": "system",
            "updated_by": "system",
            "created_at": now,
            "updated_at": now,
        }
    )


async def ensure_bootstrap_data(db: AsyncIOMotorDatabase) -> None:
    await ensure_initial_owner(db)
