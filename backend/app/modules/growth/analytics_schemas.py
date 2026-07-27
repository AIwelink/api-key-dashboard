from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


TrafficRange = Literal["24h", "7d", "30d", "90d"]
UserSegment = Literal["ordinary", "internal", "all"]
SourceKind = Literal["promotion", "direct", "organic_search", "referral"]
class TrafficAnalyticsFilters(BaseModel):
    range_key: TrafficRange = "7d"
    segment: UserSegment = "ordinary"
    site_id: str | None = None
    source_kind: SourceKind | None = None
    channel_id: UUID | None = None
    campaign_id: UUID | None = None
    tracking_link_id: UUID | None = None

    @field_validator("site_id")
    @classmethod
    def trim_site_id(cls, value: str | None) -> str | None:
        normalized = str(value or "").strip()
        return normalized or None

    @model_validator(mode="after")
    def normalize_promotion_dimensions(self):
        if self.channel_id or self.campaign_id or self.tracking_link_id:
            self.source_kind = "promotion"
        return self


class TrafficUsersQuery(TrafficAnalyticsFilters):
    limit: int = Field(default=50, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


@dataclass(frozen=True)
class TrafficWindow:
    range_key: TrafficRange
    start_at: datetime
    end_at: datetime
    bucket: Literal["hour", "day"]


_RANGE_DURATION: dict[TrafficRange, timedelta] = {
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
    "90d": timedelta(days=90),
}


def resolve_traffic_window(
    range_key: TrafficRange,
    *,
    now: datetime | None = None,
) -> TrafficWindow:
    end_at = now or datetime.now(UTC)
    if end_at.tzinfo is None:
        end_at = end_at.replace(tzinfo=UTC)
    return TrafficWindow(
        range_key=range_key,
        start_at=end_at - _RANGE_DURATION[range_key],
        end_at=end_at,
        bucket="hour" if range_key == "24h" else "day",
    )


def safe_rate(numerator: int, denominator: int) -> float | None:
    if denominator <= 0:
        return None
    return round(numerator / denominator, 6)
