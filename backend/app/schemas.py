from typing import Any, Literal

from pydantic import BaseModel, EmailStr, Field


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


class AccountCreate(BaseModel):
    account_json: dict[str, Any] | list[Any]
    metadata: dict[str, Any] = Field(default_factory=dict)


class AccountUpdate(BaseModel):
    account_json: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


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
    account_type: Literal["plus", "free", "pro", "other"] = "plus"
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
    account_type: Literal["plus", "free", "pro", "other"] | None = None
    site_id: str | None = None
    active_group_id: int | None = None
    verification_group_id: int | None = None
    min_active: int | None = None
    target_active: int | None = None
    max_avg_5h_used: int | None = None
    max_avg_7d_used: int | None = None
    min_reserve: int | None = None
    status: Literal["active", "disabled"] | None = None


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
    target_status: Literal["available", "library"] = "available"
    reason: str | None = None


class Sub2ApiAccountTestRequest(BaseModel):
    model_id: str = "gpt-5.4-mini"
    prompt: str = ""
    reason: str | None = None


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
