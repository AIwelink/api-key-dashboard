from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class Purpose(str, Enum):
    SALE = "sale"
    PROMOTION = "promotion"
    INTERNAL = "internal"
    COMPENSATION = "compensation"
    OTHER = "other"


class UserSegment(str, Enum):
    ORDINARY = "ordinary"
    INTERNAL = "internal"
    ALL = "all"


class CreditDirection(str, Enum):
    CREDIT = "credit"
    DEBIT = "debit"


class OperationsRange(str, Enum):
    TODAY = "today"
    SEVEN_DAYS = "7d"
    THIRTY_DAYS = "30d"
    CUSTOM = "custom"


def _trim(value: str) -> str:
    return value.strip()


def _validate_window(start_at: datetime | None, end_at: datetime | None) -> None:
    if start_at is not None and end_at is not None and end_at <= start_at:
        raise ValueError("active_until must be later than active_from")


def _validate_purpose_cash(purpose: Purpose, cash_amount_cny: Decimal) -> None:
    if purpose is Purpose.SALE and cash_amount_cny <= 0:
        raise ValueError("sale purpose requires a positive actual cash amount")
    if purpose is not Purpose.SALE and cash_amount_cny != 0:
        raise ValueError("non-sale purpose cannot record cash income")


class InternalUserCreate(BaseModel):
    site_id: str = Field(min_length=1, max_length=120)
    email: EmailStr
    reason: str = Field(default="", max_length=1000)
    active_from: datetime = Field(default_factory=lambda: datetime.now(UTC))
    active_until: datetime | None = None

    @field_validator("site_id", "reason")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return _trim(value)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @model_validator(mode="after")
    def validate_window(self):
        _validate_window(self.active_from, self.active_until)
        return self


class InternalUserUpdate(BaseModel):
    email: EmailStr | None = None
    reason: str | None = Field(default=None, max_length=1000)
    active_from: datetime | None = None
    active_until: datetime | None = None

    @field_validator("reason")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        return _trim(value) if value is not None else None

    @field_validator("email")
    @classmethod
    def normalize_optional_email(cls, value: EmailStr | None) -> str | None:
        return str(value).strip().lower() if value is not None else None

    @model_validator(mode="after")
    def validate_window(self):
        _validate_window(self.active_from, self.active_until)
        return self


class ConversionRateCreate(BaseModel):
    site_id: str = Field(min_length=1, max_length=120)
    balance_units_per_cny: Decimal = Field(gt=0, max_digits=30, decimal_places=10)
    effective_from: datetime = Field(default_factory=lambda: datetime.now(UTC))
    effective_until: datetime | None = None
    note: str = Field(default="", max_length=1000)

    @field_validator("site_id", "note")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return _trim(value)

    @model_validator(mode="after")
    def validate_window(self):
        if self.effective_until is not None and self.effective_until <= self.effective_from:
            raise ValueError("effective_until must be later than effective_from")
        return self


class RedemptionBatchCreate(BaseModel):
    site_id: str = Field(min_length=1, max_length=120)
    purpose: Purpose
    code_count: int = Field(gt=0, le=10000)
    balance_units_per_code: Decimal = Field(gt=0, max_digits=30, decimal_places=10)
    cash_amount_cny: Decimal = Field(default=Decimal("0"), ge=0, max_digits=30, decimal_places=10)
    note: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("site_id", "note", "idempotency_key")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return _trim(value)

    @model_validator(mode="after")
    def validate_cash_amount(self):
        _validate_purpose_cash(self.purpose, self.cash_amount_cny)
        return self


class RedemptionCodeListQuery(BaseModel):
    site_id: str = Field(min_length=1, max_length=120)
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=20, ge=1, le=100)
    status: Literal["unused", "used", "expired", "disabled"] | None = None
    origin: Literal["management_panel", "api_site"] | None = None
    search: str | None = Field(default=None, max_length=100)

    @field_validator("site_id")
    @classmethod
    def trim_site_id(cls, value: str) -> str:
        return _trim(value)

    @field_validator("search")
    @classmethod
    def trim_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = _trim(value)
        return normalized or None


class RedemptionCodeBatchDelete(BaseModel):
    site_id: str = Field(min_length=1, max_length=120)
    code_ids: list[int] = Field(min_length=1, max_length=100)

    @field_validator("site_id")
    @classmethod
    def trim_site_id(cls, value: str) -> str:
        return _trim(value)

    @field_validator("code_ids")
    @classmethod
    def validate_code_ids(cls, value: list[int]) -> list[int]:
        if any(code_id <= 0 for code_id in value):
            raise ValueError("code_ids must contain only positive IDs")
        if len(set(value)) != len(value):
            raise ValueError("code_ids must not contain duplicates")
        return value


class BalanceAdjustmentCreate(BaseModel):
    site_id: str = Field(min_length=1, max_length=120)
    external_user_id: str = Field(min_length=1, max_length=240)
    purpose: Purpose
    balance_units: Decimal = Field(max_digits=30, decimal_places=10)
    cash_amount_cny: Decimal = Field(default=Decimal("0"), ge=0, max_digits=30, decimal_places=10)
    note: str = Field(default="", max_length=2000)
    idempotency_key: str = Field(min_length=1, max_length=200)

    @field_validator("site_id", "external_user_id", "note", "idempotency_key")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return _trim(value)

    @model_validator(mode="after")
    def validate_business_rules(self):
        if self.balance_units == 0:
            raise ValueError("balance_units must not be zero")
        _validate_purpose_cash(self.purpose, self.cash_amount_cny)
        return self


class ClassificationUpdate(BaseModel):
    status: Literal["resolved", "ignored"] = "resolved"
    purpose: Purpose | None = None
    cash_amount_cny: Decimal = Field(default=Decimal("0"), ge=0, max_digits=30, decimal_places=10)
    note: str = Field(default="", max_length=2000)

    @field_validator("note")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return _trim(value)

    @model_validator(mode="after")
    def validate_resolution(self):
        if self.status == "ignored":
            if self.purpose is not None or self.cash_amount_cny != 0:
                raise ValueError("ignored tasks cannot record purpose or cash income")
            return self
        if self.purpose is None:
            raise ValueError("resolved tasks require a purpose")
        _validate_purpose_cash(self.purpose, self.cash_amount_cny)
        return self


class RefreshRequest(BaseModel):
    site_ids: list[str] | None = Field(default=None, max_length=100)

    @field_validator("site_ids")
    @classmethod
    def normalize_site_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        normalized = list(dict.fromkeys(item.strip() for item in value if item.strip()))
        if not normalized:
            raise ValueError("site_ids cannot be empty")
        return normalized


class OperationsQuery(BaseModel):
    site_id: str | None = Field(default=None, max_length=120)
    segment: UserSegment = UserSegment.ALL
    range: OperationsRange = OperationsRange.SEVEN_DAYS
    start_at: datetime | None = None
    end_at: datetime | None = None

    @model_validator(mode="after")
    def validate_custom_range(self):
        if self.range is OperationsRange.CUSTOM:
            if self.start_at is None or self.end_at is None:
                raise ValueError("custom range requires start_at and end_at")
            if self.end_at <= self.start_at:
                raise ValueError("start_at must be earlier than end_at")
        return self


class InternalUserRef(BaseModel):
    internal_user_id: UUID
