from __future__ import annotations

import re
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator


CHANNEL_CODE_PATTERN = re.compile(r"^[a-z0-9-]+$")


def _code(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not CHANNEL_CODE_PATTERN.fullmatch(normalized):
        raise ValueError("code must contain only lowercase letters, numbers, and hyphens")
    return normalized


def _landing_path(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None
    if not normalized.startswith("/") or normalized.startswith("//"):
        raise ValueError("landing_path must be a site-relative path")
    return normalized


class GrowthSiteUpdate(BaseModel):
    public_origin: str = Field(min_length=1, max_length=500)
    default_landing_path: str = "/"
    timezone: str = "Asia/Shanghai"
    currency: str = Field(default="CNY", min_length=3, max_length=3)
    binding_mode: Literal["shared_parent_cookie", "signed_handoff", "disabled"] = "disabled"
    sync_interval_seconds: int = Field(default=300, ge=60, le=3600)
    initial_sync_from: datetime | None = None
    status: Literal["active", "disabled", "archived"] = "active"

    @field_validator("public_origin")
    @classmethod
    def validate_public_origin(cls, value: str) -> str:
        normalized = value.strip().rstrip("/")
        parsed = urlsplit(normalized)
        if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment:
            raise ValueError("public_origin must be an HTTPS origin without path or query")
        return normalized

    @field_validator("default_landing_path")
    @classmethod
    def validate_default_landing_path(cls, value: str) -> str:
        return _landing_path(value) or "/"

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return normalized

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not normalized.isalpha() or len(normalized) != 3 or not normalized.isascii():
            raise ValueError("currency must be a three-letter ISO code")
        return normalized


class ChannelCreate(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=1000)
    status: Literal["active", "disabled", "archived"] = "active"

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return _code(value)

    @field_validator("name", "description")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    status: Literal["active", "disabled", "archived"] | None = None

    @field_validator("name", "description")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class CampaignCreate(BaseModel):
    site_id: str = Field(min_length=1, max_length=120)
    channel_id: UUID
    code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: Literal["draft", "active", "paused", "archived"] = "active"

    @field_validator("site_id", "name", "description")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("code")
    @classmethod
    def normalize_code(cls, value: str) -> str:
        return _code(value)

    @model_validator(mode="after")
    def validate_window(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class CampaignUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    status: Literal["draft", "active", "paused", "archived"] | None = None

    @field_validator("name", "description")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_window(self):
        if self.starts_at and self.ends_at and self.ends_at <= self.starts_at:
            raise ValueError("ends_at must be later than starts_at")
        return self


class TrackingLinkCreate(BaseModel):
    site_id: str = Field(min_length=1, max_length=120)
    campaign_id: UUID
    source_type: Literal["post", "group", "referrer", "profile", "other"]
    source_name: str = Field(min_length=1, max_length=240)
    source_url: str = Field(default="", max_length=2000)
    audience_group: str = Field(default="", max_length=160)
    promoter: str = Field(default="", max_length=160)
    landing_path: str | None = Field(default=None, max_length=1000)
    extra_dimensions: dict[str, str] = Field(default_factory=dict)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    status: Literal["active", "paused", "archived"] = "active"

    @field_validator("site_id", "source_name", "audience_group", "promoter")
    @classmethod
    def trim_text(cls, value: str) -> str:
        return value.strip()

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            return ""
        parsed = urlsplit(normalized)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source_url must be an HTTP or HTTPS URL")
        return normalized

    @field_validator("landing_path")
    @classmethod
    def validate_landing_path(cls, value: str | None) -> str | None:
        return _landing_path(value)

    @field_validator("extra_dimensions")
    @classmethod
    def validate_extra_dimensions(cls, value: dict[str, str]) -> dict[str, str]:
        if len(value) > 3:
            raise ValueError("extra_dimensions supports at most three entries")
        normalized: dict[str, str] = {}
        for key, item in value.items():
            clean_key = str(key).strip()
            clean_value = str(item).strip()
            if not clean_key or len(clean_key) > 40 or len(clean_value) > 160:
                raise ValueError("dimension keys and values must be non-empty and within length limits")
            normalized[clean_key] = clean_value
        return normalized

    @model_validator(mode="after")
    def validate_window(self):
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self


class TrackingLinkUpdate(BaseModel):
    source_type: Literal["post", "group", "referrer", "profile", "other"] | None = None
    source_name: str | None = Field(default=None, min_length=1, max_length=240)
    source_url: str | None = Field(default=None, max_length=2000)
    audience_group: str | None = Field(default=None, max_length=160)
    promoter: str | None = Field(default=None, max_length=160)
    landing_path: str | None = Field(default=None, max_length=1000)
    extra_dimensions: dict[str, str] | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    status: Literal["active", "paused", "archived"] | None = None

    @field_validator("source_name", "audience_group", "promoter")
    @classmethod
    def trim_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("source_url")
    @classmethod
    def validate_optional_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return TrackingLinkCreate.validate_source_url(value)

    @field_validator("landing_path")
    @classmethod
    def validate_optional_landing_path(cls, value: str | None) -> str | None:
        return _landing_path(value)

    @field_validator("extra_dimensions")
    @classmethod
    def validate_optional_dimensions(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        return None if value is None else TrackingLinkCreate.validate_extra_dimensions(value)

    @model_validator(mode="after")
    def validate_window(self):
        if self.valid_from and self.valid_until and self.valid_until <= self.valid_from:
            raise ValueError("valid_until must be later than valid_from")
        return self
