from __future__ import annotations

from pydantic import BaseModel, Field, field_validator, model_validator


class RiskActionRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=500)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("reason is required")
        return normalized


class RiskSettingsUpdate(BaseModel):
    detector_enabled: bool | None = None
    auto_ban_enabled: bool | None = None

    @model_validator(mode="after")
    def require_change(self) -> "RiskSettingsUpdate":
        if self.detector_enabled is None and self.auto_ban_enabled is None:
            raise ValueError("at least one risk setting is required")
        return self
