from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


QUALITY_COMPLETE = "complete"
QUALITY_MISSING = "missing"
QUALITY_DELAYED = "delayed"
QUALITY_COUNTER_RESET = "counter_reset"


@dataclass(slots=True)
class AdapterSample:
    rpm: float | None
    tpm: float | None
    quality: str
    source: str
    source_updated_at: datetime | None = None
    total_requests: int | None = None
    total_tokens: int | None = None
    elapsed_seconds: float | None = None
    error_code: str | None = None
    cursor: dict[str, Any] = field(default_factory=dict)


def nonnegative_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def missing_sample(*, source: str, error_code: str) -> AdapterSample:
    return AdapterSample(
        rpm=None,
        tpm=None,
        quality=QUALITY_MISSING,
        source=source,
        error_code=error_code,
    )
