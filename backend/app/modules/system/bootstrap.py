from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo.errors import OperationFailure

from app.config import get_settings
from app.security import hash_password
from app.utils import now_utc


async def ensure_tpm_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.sub2api_tpm_samples.create_index(
        [("site_id", 1), ("group_id", 1), ("bucket_at", 1)],
        unique=True,
    )
    await db.sub2api_tpm_samples.create_index("expires_at", expireAfterSeconds=0)
    await db.sub2api_tpm_samples.create_index([("site_id", 1), ("group_id", 1), ("sampled_at", -1)])
    await db.sub2api_tpm_backfill_state.create_index("updated_at")


async def ensure_client_metric_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.client_minute_metrics.create_index(
        [("site_id", 1), ("bucket_at", 1)],
        unique=True,
    )
    await db.client_minute_metrics.create_index([("site_id", 1), ("bucket_at", -1)])
    await db.client_minute_metrics.create_index([("site_id", 1), ("quality", 1), ("bucket_at", -1)])
    await db.client_minute_metrics.create_index("expires_at", expireAfterSeconds=0)
    await db.client_metric_sampler_state.create_index("updated_at")


async def ensure_auto_replenishment_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.auto_replenishment_settings.create_index(
        [("provider", 1), ("target_site_id", 1), ("target_group_id", 1)],
        unique=True,
        sparse=True,
    )
    await db.auto_replenishment_settings.create_index("updated_at")


async def ensure_capacity_sample_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.sub2api_capacity_samples.create_index(
        [("site_id", 1), ("group_id", 1), ("bucket_at", 1)],
        unique=True,
    )
    await db.sub2api_capacity_samples.create_index("expires_at", expireAfterSeconds=0)
    await db.sub2api_capacity_samples.create_index([("site_id", 1), ("group_id", 1), ("sampled_at", -1)])


async def ensure_smart_scheduling_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.sub2api_smart_scheduling_states.create_index(
        [("site_id", 1), ("remote_account_id", 1)],
        unique=True,
    )
    await db.sub2api_smart_scheduling_runs.create_index(
        [("site_id", 1), ("started_at", -1)]
    )
    await db.sub2api_smart_scheduling_runs.create_index(
        "expires_at",
        expireAfterSeconds=0,
    )
    try:
        outcome_indexes = await db.sub2api_smart_scheduling_outcomes.index_information()
    except OperationFailure as exc:
        if exc.code != 26:
            raise
        outcome_indexes = {}
    legacy_outcome_index = "site_id_1_run_id_1_remote_account_id_1"
    if legacy_outcome_index in outcome_indexes:
        await db.sub2api_smart_scheduling_outcomes.drop_index(legacy_outcome_index)
    await db.sub2api_smart_scheduling_outcomes.create_index(
        "expires_at",
        expireAfterSeconds=0,
    )


async def ensure_forecast_evaluation_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.sub2api_forecast_evaluations.create_index(
        [("site_id", 1), ("group_id", 1), ("kind", 1), ("status", 1), ("target_at", -1)]
    )
    await db.sub2api_forecast_evaluations.create_index(
        [("model", 1), ("version", 1), ("status", 1), ("target_at", -1)]
    )
    await db.sub2api_forecast_evaluations.create_index("expires_at", expireAfterSeconds=0)
    await db.sub2api_forecast_accuracy_summaries.create_index(
        [("site_id", 1), ("group_id", 1)],
        unique=True,
    )
    await db.sub2api_forecast_accuracy_summaries.create_index([("updated_at", -1)])


async def ensure_quota_detection_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.sub2api_quota_detection_states.create_index("expires_at", expireAfterSeconds=0)
    await db.sub2api_quota_detection_states.create_index([("site_id", 1), ("remote_account_id", 1), ("window_type", 1)])
    await db.sub2api_quota_limit_samples.create_index("expires_at", expireAfterSeconds=0)
    await db.sub2api_quota_limit_samples.create_index([("site_id", 1), ("account_type", 1), ("window_type", 1), ("hit_at", -1)])
    await db.sub2api_quota_limit_samples.create_index([("site_id", 1), ("account_type", 1), ("window_type", 1), ("generation", 1), ("classification", 1), ("hit_at", -1)])
    await db.sub2api_quota_limit_daily_rollups.create_index(
        [("site_id", 1), ("account_type", 1), ("window_type", 1), ("generation", 1), ("local_date", 1)],
        unique=True,
    )
    await db.sub2api_quota_limit_profiles.create_index(
        [("site_id", 1), ("account_type", 1), ("window_type", 1)],
        unique=True,
    )
    await db.sub2api_account_health_analyses.create_index("expires_at", expireAfterSeconds=0)


async def ensure_plus_self_produced_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.plus_self_produced_runs.create_index([("started_at", -1)])
    await db.plus_self_produced_account_results.create_index(
        [("site_id", 1), ("remote_account_id", 1)],
        unique=True,
    )
    await db.plus_self_produced_account_results.create_index([("tested_at", -1)])


async def ensure_account_test_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.sub2api_account_test_states.create_index(
        [("site_id", 1), ("remote_account_id", 1)],
        unique=True,
    )
    await db.sub2api_account_test_states.create_index("next_test_at")
    await db.sub2api_account_test_events.create_index(
        [("site_id", 1), ("remote_account_id", 1), ("tested_at", -1)]
    )
    await db.sub2api_account_test_events.create_index(
        [("dispatch.scheduling.status", 1), ("dispatch.scheduling.next_retry_at", 1)]
    )
    await db.sub2api_account_test_events.create_index(
        [("dispatch.plan_correction.status", 1), ("dispatch.plan_correction.next_retry_at", 1)]
    )
    await db.sub2api_account_test_events.create_index("expires_at", expireAfterSeconds=0)
    await db.sub2api_account_test_site_meta.create_index("site_id", unique=True)


async def ensure_account_history_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.remote_account_change_batches.create_index([("site_id", 1), ("observed_at", -1)])
    await db.remote_account_change_batches.create_index("expires_at", expireAfterSeconds=0)
    await db.remote_account_change_batches.create_index([("migration_id", 1), ("observed_at", 1)])
    await db.remote_account_daily_checkpoints.create_index([("site_id", 1), ("local_date", -1)])
    await db.remote_account_daily_checkpoints.create_index("expires_at", expireAfterSeconds=0)
    await db.remote_account_daily_checkpoints.create_index([("migration_id", 1), ("local_date", 1)])
    await db.remote_account_history_migrations.create_index([("updated_at", -1)])


async def ensure_frontend_presence_indexes(db: AsyncIOMotorDatabase) -> None:
    try:
        indexes = await db.frontend_presence.index_information()
    except OperationFailure as exc:
        if exc.code != 26:
            raise
        indexes = {}
    if "user_id_1_client_id_1" in indexes:
        await db.frontend_presence.drop_index("user_id_1_client_id_1")
        await db.frontend_presence.delete_many({})
    await db.frontend_presence.create_index([("user_id", 1), ("client_id", 1), ("session_id", 1)], unique=True)
    await db.frontend_presence.create_index([("last_seen_at", -1)])
    await db.frontend_presence.create_index("expires_at", expireAfterSeconds=0)


async def ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    await db.users.create_index("email", unique=True)
    await db.api_tokens.create_index("token_hash", unique=True)
    await db.api_tokens.create_index("token_prefix")
    await db.api_tokens.create_index("status")
    await db.api_tokens.create_index("created_at")
    await ensure_frontend_presence_indexes(db)
    await db.frontend_presence_minutes.create_index([("user_id", 1), ("bucket_at", 1)], unique=True)
    await db.frontend_presence_minutes.create_index([("bucket_at", -1)])
    await db.frontend_presence_minutes.create_index("expires_at", expireAfterSeconds=0)
    await db.notification_channels.create_index("channel_type")
    await db.notification_channels.create_index("status")
    await db.notification_channels.create_index("created_at")
    await db.notification_events.create_index([("event_type", 1), ("created_at", -1)])
    await db.notification_events.create_index([("resource_type", 1), ("resource_id", 1)])
    await db.notification_events.create_index("dedupe_key")
    await db.notification_batches.create_index([("event_type", 1), ("status", 1), ("window_end_at", 1)])
    await db.notification_batches.create_index([("source", 1), ("created_at", -1)])
    await db.notification_deliveries.create_index([("notification_event_id", 1), ("channel_id", 1)])
    await db.notification_deliveries.create_index([("event_type", 1), ("created_at", -1)])
    await db.notification_deliveries.create_index([("channel_id", 1), ("created_at", -1)])
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
    await db.accounts.create_index([("metadata.pool_status", 1), ("metadata.sub2api_site_id", 1), ("metadata.sub2api_group_id", 1), ("metadata.reserve_pinned_at", -1), ("metadata.updated_at", 1)])
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
    await db.api_pools.create_index("source_group_key")
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
    await db.client_sites.create_index("status")
    await db.client_sites.create_index("client_type")
    await db.client_sites.create_index("created_at")
    await ensure_auto_replenishment_indexes(db)
    await ensure_client_metric_indexes(db)
    await db.sub2api_groups_cache.create_index([("site_id", 1), ("group_id", 1)], unique=True)
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("group_ids", 1), ("status", 1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("group_ids", 1), ("created_at", -1), ("sub2api_account_id", -1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("sub2api_account_id", 1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("plan_type", 1), ("status", 1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("email", 1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("subscription_expires_at", 1)])
    await db.sub2api_accounts_cache.create_index([("site_id", 1), ("codex_7d_used_percent", -1), ("codex_5h_used_percent", -1)])
    await db.long_7d_account_probes.create_index([("site_id", 1), ("remote_account_id", 1)], unique=True)
    await db.long_7d_account_probes.create_index([("site_id", 1), ("last_attempt_at", 1)])
    await db.sub2api_cache_meta.create_index("fetched_at")
    await db.sub2api_dashboard_meta.create_index("updated_at")
    await db.sub2api_hourly_forecasts.create_index([("site_id", 1), ("group_id", 1), ("as_of", -1)])
    await db.sub2api_hourly_forecasts.create_index("expires_at", expireAfterSeconds=0)
    await ensure_tpm_indexes(db)
    await ensure_capacity_sample_indexes(db)
    await ensure_smart_scheduling_indexes(db)
    await ensure_forecast_evaluation_indexes(db)
    await ensure_quota_detection_indexes(db)
    await ensure_plus_self_produced_indexes(db)
    await ensure_account_test_indexes(db)
    await ensure_account_history_indexes(db)
    await db.sub2api_auto_refill_meta.create_index("last_finished_at")
    await db.group_observability_settings.create_index([("site_id", 1), ("group_id", 1)], unique=True)
    await db.group_observability_settings.create_index("status")
    await db.remote_account_identities.create_index([("site_id", 1), ("normalized_email", 1)], unique=True)
    await db.remote_account_identities.create_index([("site_id", 1), ("current_presence", 1)])
    await db.remote_account_identities.create_index([("site_id", 1), ("duplicate_remote_count", -1), ("current_presence", 1)])
    await db.remote_account_identities.create_index([("duplicate_email_alert_read_at", 1), ("duplicate_remote_count", -1)])
    await db.remote_account_identities.create_index([("site_id", 1), ("current_is_401", 1), ("last_401_at", -1)])
    await db.remote_account_identities.create_index("last_seen_at")
    await db.remote_account_identities.create_index([("site_id", 1), ("last_event_at", -1), ("updated_at", -1)])
    await db.remote_account_identities.create_index([("site_id", 1), ("plan_type", 1), ("last_event_at", -1)])
    await db.remote_account_sessions.create_index([("site_id", 1), ("normalized_email", 1), ("session_index", 1)])
    await db.remote_account_sessions.create_index([("site_id", 1), ("remote_account_id", 1), ("status", 1)])
    await db.remote_account_status_events.create_index([("site_id", 1), ("event_type", 1), ("detected_at", -1)])
    await db.remote_account_status_events.create_index([("site_id", 1), ("event_type", 1), ("details.is_pro_pool", 1), ("detected_at", -1)])
    await db.remote_account_status_events.create_index([("site_id", 1), ("normalized_email", 1), ("detected_at", -1)])
    await db.remote_account_status_events.create_index([("detected_at", -1), ("severity", 1)])
    await db.remote_account_status_events.create_index([("site_id", 1), ("current_group_ids", 1), ("detected_at", -1)])
    await db.remote_account_probe_samples.create_index("expires_at", expireAfterSeconds=0)
    await db.remote_account_probe_samples.create_index([("site_id", 1), ("sampled_at", -1)])
    await db.remote_account_probe_runs.create_index([("site_id", 1), ("started_at", -1)])
    await db.remote_account_probe_meta.create_index("last_probe_at")
    await db.sub2api_capacity_notification_meta.create_index([("site_id", 1), ("group_id", 1)], unique=True)
    await db.agent_runs.create_index([("created_at", -1)])
    await db.agent_runs.create_index([("status", 1), ("created_at", -1)])
    await db.agent_runs.create_index([("pool_id", 1), ("created_at", -1)])
    await db.agent_runs.create_index([("conversation_id", 1), ("created_at", -1)])
    await db.agent_messages.create_index([("conversation_id", 1), ("created_at", 1)])
    await db.agent_messages.create_index([("run_id", 1), ("created_at", 1)])
    await db.agent_decisions.create_index([("run_id", 1)])
    await db.agent_decisions.create_index([("pool_id", 1), ("created_at", -1)])
    await db.agent_decisions.create_index([("conversation_id", 1), ("created_at", -1)])
    await db.agent_memory_summaries.create_index([("pool_id", 1), ("memory_type", 1), ("period_end", -1)])
    await db.agent_memory_summaries.create_index([("site_id", 1), ("memory_type", 1), ("period_end", -1)])
    await db.agent_memory_summaries.create_index([("site_id", 1), ("pool_id", 1), ("memory_type", 1), ("period_start", 1), ("period_end", 1)])
    await db.agent_memory_summaries.create_index([("created_at", -1)])
    await db.agent_tasks.create_index([("pool_id", 1), ("status", 1), ("updated_at", -1)])
    await db.agent_tasks.create_index([("site_id", 1), ("status", 1), ("updated_at", -1)])
    await db.agent_tasks.create_index([("task_type", 1), ("status", 1), ("updated_at", -1)])
    await db.agent_tasks.create_index([("next_check_at", 1)])
    await db.agent_tasks.create_index([("review_after", 1)])
    await db.agent_tasks.create_index([("status", 1), ("next_check_at", 1)])
    await db.agent_tasks.create_index([("status", 1), ("alert_status", 1), ("updated_at", 1)])
    await db.agent_tasks.create_index([("scheduler_lock.expires_at", 1)])
    await db.agent_tasks.create_index([("created_at", -1)])
    await db.agent_run_steps.create_index([("run_id", 1), ("step_index", 1)])
    await db.agent_run_steps.create_index([("conversation_id", 1), ("created_at", -1)])
    await db.agent_run_steps.create_index([("task_id", 1), ("created_at", -1)])
    await db.agent_run_steps.create_index([("step_type", 1), ("created_at", -1)])
    await db.agent_run_steps.create_index([("created_at", -1)])
    await db.agent_event_triggers.create_index("dedupe_key", unique=True)
    await db.agent_event_triggers.create_index([("site_id", 1), ("pool_id", 1), ("created_at", -1)])
    await db.agent_event_triggers.create_index([("signal", 1), ("created_at", -1)])
    await db.agent_event_triggers.create_index([("status", 1), ("created_at", -1)])
    await db.agent_scheduler_ticks.create_index([("started_at", -1)])
    await db.agent_scheduler_ticks.create_index([("status", 1), ("started_at", -1)])
    await db.agent_scheduler_ticks.create_index([("reason", 1), ("started_at", -1)])
    await db.agent_scheduler_locks.create_index([("expires_at", 1)])
    await db.agent_patrol_runs.create_index([("pool_id", 1), ("started_at", -1)])
    await db.agent_patrol_runs.create_index([("scheduler_tick_id", 1), ("started_at", -1)])
    await db.agent_patrol_runs.create_index([("status", 1), ("started_at", -1)])
    await db.agent_patrol_runs.create_index([("created_at", -1)])
    await db.agent_eval_runs.create_index([("started_at", -1)])
    await db.agent_eval_runs.create_index([("suite", 1), ("started_at", -1)])
    await db.agent_eval_runs.create_index([("status", 1), ("started_at", -1)])
    await db.agent_eval_runs.create_index([("category", 1), ("started_at", -1)])
    await db.agent_eval_results.create_index([("eval_run_id", 1), ("case_id", 1)])
    await db.agent_eval_results.create_index([("case_id", 1), ("created_at", -1)])
    await db.agent_eval_results.create_index([("category", 1), ("created_at", -1)])
    await db.agent_eval_results.create_index([("status", 1), ("created_at", -1)])
    await db.agent_eval_results.create_index([("category", 1), ("status", 1), ("created_at", -1)])


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
    from app.modules.system.client_sites import migrate_legacy_client_sites
    from app.modules.system.permissions import ensure_role_permissions_settings

    await ensure_initial_owner(db)
    await ensure_role_permissions_settings(db)
    await migrate_legacy_client_sites(db)
