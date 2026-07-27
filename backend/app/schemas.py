from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


Role = Literal["owner", "admin", "maintainer", "operator", "viewer"]
UserRoleId = Annotated[str, Field(min_length=1, max_length=32, pattern=r"^[a-z][a-z0-9-]{0,31}$")]
UploadSourceTemplate = Literal["sub2api", "purchased_jinyao"]
ViewName = Literal[
    "upload",
    "todos",
    "push-error-todos",
    "accounts",
    "available-pool",
    "reserve-pool",
    "api-pools",
    "plus-self-produced",
    "traffic-analysis",
    "operations-management",
    "event-records",
    "alert-center",
    "pool-lifecycle",
    "client-sites",
    "traffic-analysis-config",
    "agent-analysis",
    "agent-workbench",
    "system-management",
    "api-tokens",
    "presence",
    "users",
    "logs",
]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict[str, Any]


class FrontendPresenceHeartbeat(BaseModel):
    client_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    session_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    client_label: str = Field(default="Unknown client", max_length=100)
    device_type: Literal["desktop", "mobile", "tablet", "unknown"] = "unknown"
    view: str = Field(default="", max_length=64)
    path: str = Field(default="", max_length=200)
    foreground_since_at: datetime | None = None


class FrontendPresenceLeave(BaseModel):
    client_id: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")
    session_id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9._:-]+$")


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class UserCreate(BaseModel):
    email: EmailStr
    name: str
    role: UserRoleId = "maintainer"
    password: str | None = Field(default=None, min_length=8)


class UserUpdate(BaseModel):
    name: str | None = None
    role: UserRoleId | None = None
    status: Literal["active", "disabled", "pending_password_reset"] | None = None


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=8)


class RolePermissionEntry(BaseModel):
    label: str | None = Field(default=None, min_length=1, max_length=40)
    builtin: bool | None = None
    allowed_views: list[ViewName] = Field(default_factory=list, max_length=50)
    default_view: ViewName | None = None

    @field_validator("allowed_views")
    @classmethod
    def dedupe_allowed_views(cls, values: list[ViewName]) -> list[ViewName]:
        return list(dict.fromkeys(values))

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("label must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_default_view(self) -> "RolePermissionEntry":
        if self.default_view is not None and self.default_view not in self.allowed_views:
            raise ValueError("default_view must be included in allowed_views")
        return self


class RolePermissionsUpdate(BaseModel):
    roles: dict[UserRoleId, RolePermissionEntry] = Field(min_length=1)


class UserRoleCreate(BaseModel):
    id: UserRoleId
    label: str = Field(min_length=1, max_length=40)

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("label must not be empty")
        return normalized


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: Role = "maintainer"
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    note: str | None = Field(default=None, max_length=500)


class NotificationChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    channel_type: Literal["dingtalk", "telegram", "feishu"] = "dingtalk"
    status: Literal["active", "disabled"] = "active"
    webhook_url: str | None = Field(default=None, min_length=1, max_length=1000)
    signing_secret: str | None = Field(default=None, min_length=1, max_length=500)
    telegram_bot_token: str | None = Field(default=None, min_length=1, max_length=500)
    telegram_chat_id: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)

    @model_validator(mode="after")
    def validate_channel_config(self) -> "NotificationChannelCreate":
        if self.channel_type == "dingtalk":
            if not self.webhook_url or not self.signing_secret:
                raise ValueError("钉钉通知需要 Webhook 地址和加签密钥")
        if self.channel_type == "telegram":
            if not self.telegram_bot_token or not self.telegram_chat_id:
                raise ValueError("Telegram 通知需要 Bot Token 和 Chat ID")
        if self.channel_type == "feishu" and not self.webhook_url:
            raise ValueError("飞书通知需要 Webhook 地址")
        return self


class NotificationChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    status: Literal["active", "disabled"] | None = None
    webhook_url: str | None = Field(default=None, min_length=1, max_length=1000)
    signing_secret: str | None = Field(default=None, min_length=1, max_length=500)
    telegram_bot_token: str | None = Field(default=None, min_length=1, max_length=500)
    telegram_chat_id: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)


class GrowthDatabaseSettingsUpdate(BaseModel):
    sql_dsn: str = ""


class AgentLlmSettingsUpdate(BaseModel):
    enabled: bool = False
    base_url: str | None = Field(default=None, max_length=1000)
    api_key: str | None = Field(default=None, max_length=2000)
    level1_model: str | None = Field(default=None, max_length=200)
    level1_temperature: float = Field(default=0.2, ge=0, le=2)
    level2_model: str | None = Field(default=None, max_length=200)
    level2_temperature: float = Field(default=0.2, ge=0, le=2)
    timeout_seconds: int = Field(default=60, ge=5, le=300)
    loop_enabled: bool = False
    loop_interval_seconds: int = Field(default=900, ge=60, le=86400)
    agent_loop_enabled: bool | None = None
    scheduler_interval_seconds: int = Field(default=300, ge=60, le=86400)
    max_tasks_per_tick: int = Field(default=5, ge=1, le=100)
    max_pool_patrols_per_tick: int = Field(default=3, ge=0, le=100)
    patrol_enabled: bool = False
    pool_patrol_interval_minutes: int = Field(default=30, ge=5, le=1440)
    pool_patrol_cooldown_minutes: int = Field(default=30, ge=0, le=1440)
    required_patrol_pool_ids: list[str] = Field(default_factory=list, max_length=100)
    excluded_agent_pool_ids: list[str] = Field(default_factory=list, max_length=100)
    max_event_triggers_per_tick: int = Field(default=3, ge=0, le=100)
    max_concurrent_runs: int = Field(default=1, ge=1, le=20)
    task_cooldown_minutes: int = Field(default=10, ge=0, le=1440)
    event_trigger_cooldown_minutes: int = Field(default=15, ge=0, le=1440)
    daily_memory_enabled: bool = True
    weekly_memory_enabled: bool = True
    max_memory_summaries_per_tick: int = Field(default=3, ge=0, le=100)
    memory_summary_catchup_enabled: bool = True
    notification_dispatch_enabled: bool = False
    decision_notification_enabled: bool = False
    decision_notification_min_severity: Literal["healthy", "watch", "warning", "danger", "critical"] = "warning"
    decision_notification_triggers: list[str] = Field(default_factory=lambda: ["event_spike", "scheduler_task_due", "scheduler_review_due", "scheduler_patrol"], max_length=20)
    decision_notification_cooldown_minutes: int = Field(default=30, ge=0, le=1440)
    pool_strategies: list[dict[str, Any]] = Field(default_factory=list)


class AccountCreate(BaseModel):
    account_json: dict[str, Any] | list[Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class AccountUpdate(BaseModel):
    account_json: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


class AccountCredentialsRefresh(BaseModel):
    account_json: dict[str, Any] | list[Any] | str


class ImportPreviewRequest(BaseModel):
    payload: dict[str, Any] | list[Any] | str
    metadata_defaults: dict[str, Any] = Field(default_factory=dict)
    source_template: UploadSourceTemplate = "sub2api"


class ImportCommitRequest(BaseModel):
    payload: dict[str, Any] | list[Any] | str
    metadata_defaults: dict[str, Any] = Field(default_factory=dict)
    source_template: UploadSourceTemplate = "sub2api"


class SyncRunRequest(BaseModel):
    account_ids: list[str] | None = None
    dry_run: bool = False


UploadIntent = Literal["new", "renew", "purchase", "historical", "known_error"]


class ImportBatchCreate(BaseModel):
    payload: dict[str, Any] | list[Any] | str
    name: str | None = None
    upload_intent: UploadIntent = "new"
    source_template: UploadSourceTemplate = "sub2api"
    remark: str | None = None
    metadata_defaults: dict[str, Any] = Field(default_factory=dict)


class ApiPoolCreate(BaseModel):
    name: str
    account_type: Literal["plus", "team", "k12", "free", "pro", "other"] = "plus"
    site_id: str = "default"
    active_group_id: int
    verification_group_id: int | None = None
    min_active: int = 20
    target_active: int = 30
    max_avg_5h_used: int = 70
    max_avg_7d_used: int = 80
    min_reserve: int = 10
    status: Literal["active", "disabled"] = "active"


class ApiPoolUpdate(BaseModel):
    name: str | None = None
    account_type: Literal["plus", "team", "k12", "free", "pro", "other"] | None = None
    site_id: str | None = None
    active_group_id: int | None = None
    verification_group_id: int | None = None
    min_active: int | None = None
    target_active: int | None = None
    max_avg_5h_used: int | None = None
    max_avg_7d_used: int | None = None
    min_reserve: int | None = None
    status: Literal["active", "disabled"] | None = None


class CapacityAccountLimit(BaseModel):
    five_hour_usd: float = Field(ge=0)
    seven_day_usd: float = Field(ge=0)


class CapacityAccountLimitsUpdate(BaseModel):
    limits: dict[Literal["free", "plus", "team", "bug_team", "k12", "pro"], CapacityAccountLimit]


class SmartSchedulingAccountRule(BaseModel):
    manual_priority_min: int = Field(ge=1, le=100_000)
    manual_priority_max: int = Field(ge=1, le=100_000)
    system_priority_min: int = Field(ge=1, le=100_000)
    system_priority_max: int = Field(ge=1, le=100_000)
    automatic_priority: int = Field(ge=1, le=100_000)
    normal_concurrency: int = Field(ge=1, le=10_000)
    extreme_entry_percent: float = Field(ge=0, le=100)
    recovery_percent: float = Field(ge=0, le=100)
    extreme_concurrency: int = Field(ge=1, le=10_000)


class SmartSchedulingAccountTypes(BaseModel):
    pro: SmartSchedulingAccountRule
    plus: SmartSchedulingAccountRule
    k12: SmartSchedulingAccountRule
    team: SmartSchedulingAccountRule


class SmartSchedulingExtremeRule(BaseModel):
    priority_min: int = Field(ge=1, le=100_000)
    priority_max: int = Field(ge=1, le=100_000)
    priority: int = Field(ge=1, le=100_000)


class SmartSchedulingRules(BaseModel):
    account_types: SmartSchedulingAccountTypes
    extreme: SmartSchedulingExtremeRule

    @model_validator(mode="after")
    def validate_rule_relationships(self) -> "SmartSchedulingRules":
        from app.modules.sub2api.smart_scheduling import normalize_smart_scheduling_rules

        normalize_smart_scheduling_rules(self.model_dump())
        return self


class SmartSchedulingSettingsUpdate(BaseModel):
    rules: SmartSchedulingRules


class ApiPoolStatusPreferenceUpdate(BaseModel):
    pinned_site_id: str | None = None
    pinned_group_id: int | None = None


class PlusSelfProducedSettingsUpdate(BaseModel):
    enabled: bool | None = None
    interval_minutes: int | None = Field(default=None, ge=1, le=1440)
    source_group_id: int | None = Field(default=None, ge=1)
    plus_group_id: int | None = Field(default=None, ge=1)
    banned_group_id: int | None = Field(default=None, ge=1)
    plus_error_group_id: int | None = Field(default=None, ge=1)


class GroupObservabilitySettingUpdate(BaseModel):
    enabled: bool | None = None
    detailed_enabled: bool | None = None
    type_priority_enabled: bool | None = None
    quota_acceleration_enabled: bool | None = None
    probe_interval_seconds: int | None = Field(default=None, ge=60, le=3600)
    sample_retention_days: int | None = Field(default=None, ge=1, le=90)
    record_usage_samples: bool | None = None
    record_status_events: bool | None = None
    record_duplicate_email_warning: bool | None = None
    capacity_notification_enabled: bool | None = None
    capacity_notification_threshold: Literal["tight", "danger", "exhausted"] | None = None
    capacity_notification_cooldown_minutes: int | None = Field(default=None, ge=5, le=1440)
    uptime_kuma_monitor_url: str | None = Field(default=None, max_length=1000)

    @field_validator("uptime_kuma_monitor_url")
    @classmethod
    def validate_uptime_kuma_monitor_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            return ""
        parsed = urlparse(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("uptime_kuma_monitor_url must be an http or https URL")
        return normalized


class AlertReadRequest(BaseModel):
    note: str | None = Field(default=None, max_length=500)


class EnterReserveRequest(BaseModel):
    pool_id: str
    priority: int = 0
    reason: str | None = None


ManualPoolStatus = Literal["library", "available", "reserve", "active", "problem", "discarded"]


class ManualTransferRequest(BaseModel):
    target_status: ManualPoolStatus
    pool_id: str | None = None
    site_id: str | None = None
    priority: int | None = None
    reason: str | None = None
    last_error: str | None = None


class ProblemInfoCorrectedRequest(BaseModel):
    note: str | None = None


class ReservePinRequest(BaseModel):
    pinned: bool = True


class PushToSub2ApiRequest(BaseModel):
    site_id: str = "default"
    group_id: int | None = None
    run_verification: bool = True
    model_id: str = "gpt-5.4-mini"
    prompt: str = ""
    concurrency: int = Field(default=10, ge=1)
    load_factor: int = Field(default=10, ge=1)
    priority: int = Field(default=100, ge=0)
    reason: str | None = None


class Sub2ApiManualDeleteRequest(BaseModel):
    target_status: Literal["available", "library", "problem"] = "available"
    reason: str | None = None


class Sub2ApiAccountTestRequest(BaseModel):
    model_id: str = "gpt-5.4-mini"
    prompt: str = ""
    reason: str | None = None


class Sub2ApiOAuthExchangeRequest(BaseModel):
    session_id: str
    callback_url: str | None = None
    code: str | None = None
    state: str | None = None


class Sub2ApiOAuthApplyRequest(BaseModel):
    credentials: dict[str, Any]
    account_type: str = "oauth"


class Sub2ApiResurrectionFailRequest(BaseModel):
    reason: str
    decision: Literal["problem_pool", "banned_archive"] = "problem_pool"


class Sub2ApiRecentMailRequest(BaseModel):
    email_session: str
    limit: int = Field(default=2, ge=1, le=5)


class VerifyViaSub2ApiRequest(BaseModel):
    site_id: str = "default"
    verification_group_id: int
    model_id: str = "gpt-5.4-mini"
    prompt: str = ""
    cleanup_remote: bool = True
    concurrency: int = Field(default=10, ge=1)
    load_factor: int = Field(default=10, ge=1)
    priority: int = Field(default=100, ge=0)
    reason: str | None = None


PaymentType = Literal["paypal_multi", "paypal_single", "no_card", "gopay", "other"]


class FreeToPlusCompleteRequest(BaseModel):
    payment_type: PaymentType
    note: str | None = None


class FreeToPlusFailRequest(BaseModel):
    error: str
    note: str | None = None


class PushErrorTestRequest(BaseModel):
    model_id: str = "gpt-5.4-mini"
    prompt: str = ""


class PushErrorDecisionRequest(BaseModel):
    decision: Literal["plus_reprocess", "problem_library"]
    note: str | None = None
