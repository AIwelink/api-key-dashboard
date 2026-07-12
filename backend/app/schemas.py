from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field, model_validator


Role = Literal["owner", "admin", "maintainer", "viewer"]
UploadSourceTemplate = Literal["sub2api", "purchased_jinyao"]


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: dict[str, Any]


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


class UserCreate(BaseModel):
    email: EmailStr
    name: str
    role: Role = "maintainer"
    password: str | None = Field(default=None, min_length=8)


class UserUpdate(BaseModel):
    name: str | None = None
    role: Role | None = None
    status: Literal["active", "disabled", "pending_password_reset"] | None = None


class PasswordResetRequest(BaseModel):
    password: str = Field(min_length=8)


class ApiTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    role: Role = "maintainer"
    expires_in_days: int | None = Field(default=None, ge=1, le=3650)
    note: str | None = Field(default=None, max_length=500)


class NotificationChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    channel_type: Literal["dingtalk", "telegram"] = "dingtalk"
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
        return self


class NotificationChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    status: Literal["active", "disabled"] | None = None
    webhook_url: str | None = Field(default=None, min_length=1, max_length=1000)
    signing_secret: str | None = Field(default=None, min_length=1, max_length=500)
    telegram_bot_token: str | None = Field(default=None, min_length=1, max_length=500)
    telegram_chat_id: str | None = Field(default=None, min_length=1, max_length=200)
    note: str | None = Field(default=None, max_length=500)


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


class ApiPoolStatusPreferenceUpdate(BaseModel):
    pinned_site_id: str | None = None
    pinned_group_id: int | None = None


class GroupObservabilitySettingUpdate(BaseModel):
    enabled: bool | None = None
    detailed_enabled: bool | None = None
    probe_interval_seconds: int | None = Field(default=None, ge=60, le=3600)
    sample_retention_days: int | None = Field(default=None, ge=1, le=90)
    record_usage_samples: bool | None = None
    record_status_events: bool | None = None
    record_duplicate_email_warning: bool | None = None


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
