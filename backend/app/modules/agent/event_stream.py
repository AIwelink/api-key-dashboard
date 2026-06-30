from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.events.records import list_event_records
from app.utils import serialize_doc


async def read_agent_event_stream_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    account_type: str | None = None,
    limit: int = 80,
) -> dict[str, Any]:
    """Read and summarize the existing event-record stream for Agent context."""

    response = await list_event_records(
        db,
        site_id=site_id,
        group_id=group_id,
        account_type=account_type,
        range_value="24h",
        only_pro=str(account_type or "").strip().lower() == "pro",
        limit=limit,
    )
    items = [item for item in response.get("items", []) if isinstance(item, dict)]
    return serialize_doc(
        {
            "data_source": "event_records",
            "range": "24h",
            "total": response.get("total"),
            "summary": response.get("summary") if isinstance(response.get("summary"), dict) else {},
            "event_type_counts": _event_type_counts(items),
            "status_transition_counts": _status_transition_counts(items),
            "error_category_counts": _error_category_counts(items),
            "recent_timeline": _recent_timeline(items[:30]),
            "notable_patterns": _notable_patterns(items),
        }
    )


def _event_type_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(item.get("event_type") or "unknown") for item in items)
    return dict(counter.most_common())


def _status_transition_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        previous_status = str(item.get("previous_status") or "-")
        current_status = str(item.get("current_status") or "-")
        if previous_status != "-" or current_status != "-":
            counter[f"{previous_status}->{current_status}"] += 1
    return dict(counter.most_common(12))


def _error_category_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(item.get("error_category") or "unknown") for item in items if item.get("error_category") or item.get("is_401"))
    return dict(counter.most_common(12))


def _recent_timeline(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    timeline: list[dict[str, Any]] = []
    for item in items:
        timeline.append(
            {
                "event_type": item.get("event_type"),
                "severity": item.get("severity"),
                "detected_at": item.get("detected_at") or item.get("occurred_at"),
                "email": _redact_email(item.get("email") or item.get("normalized_email")),
                "remote_account_id": item.get("remote_account_id"),
                "previous_status": item.get("previous_status"),
                "current_status": item.get("current_status"),
                "current_schedulable": item.get("current_schedulable"),
                "error_category": item.get("error_category"),
                "is_401": item.get("is_401"),
                "normal_use_seconds": item.get("normal_use_seconds"),
                "usage_duration_seconds": item.get("usage_duration_seconds"),
                "current_error_message": _short_text(item.get("current_error_message"), limit=180),
            }
        )
    return timeline


def _notable_patterns(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_type[str(item.get("event_type") or "unknown")].append(item)

    for event_type, event_items in by_type.items():
        if len(event_items) >= 3:
            patterns.append(
                {
                    "type": "event_type_cluster",
                    "event_type": event_type,
                    "count": len(event_items),
                    "message": f"最近 24h 出现 {len(event_items)} 个 {event_type} 事件。",
                }
            )

    detected_401_items = by_type.get("401_detected", [])
    if detected_401_items:
        same_window = _largest_time_window(detected_401_items, minutes=180)
        if same_window.get("count", 0) >= 3:
            patterns.append(
                {
                    "type": "same_time_window_401",
                    "count": same_window["count"],
                    "started_at": same_window.get("started_at"),
                    "ended_at": same_window.get("ended_at"),
                    "duration_minutes": same_window.get("duration_minutes"),
                    "message": f"最近 24h 有 {same_window['count']} 个 401 集中出现在约 {same_window.get('duration_minutes')} 分钟窗口内。",
                }
            )

    rollover_items = by_type.get("usage_rollover", [])
    if rollover_items:
        patterns.append(
            {
                "type": "usage_limit_events",
                "count": len(rollover_items),
                "message": f"最近 24h 出现 {len(rollover_items)} 个额度重置/限额相关事件。",
            }
        )

    return patterns[:10]


def _largest_time_window(items: list[dict[str, Any]], *, minutes: int) -> dict[str, Any]:
    parsed = sorted((_datetime_text(item), item) for item in items if _datetime_text(item))
    best: list[str] = []
    for index, (started_at, _) in enumerate(parsed):
        window = [started_at]
        for candidate, _item in parsed[index + 1 :]:
            if _minutes_between(started_at, candidate) <= minutes:
                window.append(candidate)
            else:
                break
        if len(window) > len(best):
            best = window
    if not best:
        return {"count": 0}
    return {
        "count": len(best),
        "started_at": best[0],
        "ended_at": best[-1],
        "duration_minutes": _minutes_between(best[0], best[-1]),
    }


def _datetime_text(item: dict[str, Any]) -> str | None:
    value = item.get("detected_at") or item.get("occurred_at")
    return str(value) if value else None


def _minutes_between(start: str, end: str) -> int:
    from datetime import datetime

    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, int((end_dt - start_dt).total_seconds() / 60))


def _redact_email(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text or "@" not in text:
        return text or None
    name, domain = text.split("@", 1)
    prefix = name[:2] if len(name) > 2 else name[:1]
    return f"{prefix}***@{domain}"


def _short_text(value: Any, *, limit: int) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text if len(text) <= limit else f"{text[:limit]}..."
