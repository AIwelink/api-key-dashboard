from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.modules.events.records import list_event_records
from app.modules.sub2api.account_probe import CONFIRMED_401_RECOVERY_COUNT
from app.utils import now_utc, serialize_doc


EVENT_WINDOW_RANGES = ("1h", "6h", "24h", "7d")


async def read_agent_event_windows(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str | None,
    group_id: int | None = None,
    pool_id: str | None = None,
    now: Any = None,
    account_type: str | None = None,
    detail_24h_limit: int = 80,
) -> dict[str, Any]:
    """Read event details and multi-window summaries for Agent context.

    This only reads the existing event-record collections via the main event
    module. It does not refresh sub2api or run account probes.
    """

    current_time = _coerce_datetime(now) or now_utc()
    group_id = group_id if group_id is not None else _group_id_from_pool_id(pool_id)
    if not site_id or group_id is None:
        return _empty_event_windows(site_id=site_id, group_id=group_id, pool_id=pool_id, reason="site_id_or_group_id_missing")

    only_pro = str(account_type or "").strip().lower() == "pro"
    detail_response = await list_event_records(
        db,
        site_id=site_id,
        group_id=group_id,
        account_type=account_type,
        range_value="24h",
        only_pro=only_pro,
        limit=max(80, min(_summary_sample_limit("24h"), 300)),
    )
    detail_items = [item for item in detail_response.get("items", []) if isinstance(item, dict)]
    resolved_pool_id = pool_id or _pool_id(site_id=site_id, group_id=group_id)
    capacity_consensus = await _read_capacity_notification_consensus(
        db,
        site_id=site_id,
        group_id=group_id,
        now=current_time,
    )

    summaries: dict[str, dict[str, Any]] = {}
    for range_value in EVENT_WINDOW_RANGES:
        response = detail_response if range_value == "24h" else await list_event_records(
            db,
            site_id=site_id,
            group_id=group_id,
            account_type=account_type,
            range_value=range_value,
            only_pro=only_pro,
            limit=_summary_sample_limit(range_value),
        )
        items = [item for item in response.get("items", []) if isinstance(item, dict)]
        summaries[range_value] = _window_summary(
            range_value=range_value,
            response=response,
            items=items,
            site_id=site_id,
            group_id=group_id,
            pool_id=resolved_pool_id,
        )
        summaries[range_value]["capacity_consensus"] = _capacity_consensus_for_window(
            capacity_consensus,
            range_value=range_value,
            now=current_time,
        )

    notable_patterns = _merge_notable_patterns(summaries)
    return serialize_doc(
        {
            "data_source": "event_records",
            "refresh_behavior": "read_existing_cache_only",
            "detail_24h": {
                "window_hours": 24,
                "max_items": 80,
                "total": detail_response.get("total"),
                "selection": "prioritize_high_value_events_then_recent",
                "items": _detail_events(
                    _prioritized_detail_items(detail_items, limit=_normalize_limit(detail_24h_limit, default=80, maximum=80)),
                    site_id=site_id,
                    group_id=group_id,
                    pool_id=resolved_pool_id,
                ),
            },
            "summary_1h": summaries["1h"],
            "summary_6h": summaries["6h"],
            "summary_24h": summaries["24h"],
            "summary_7d": summaries["7d"],
            "notable_patterns": notable_patterns,
            "consensus_evidence": {
                "capacity_notifications": capacity_consensus,
            },
            "data_quality": {
                "available": True,
                "detail_24h_limited_to": 80,
                "summary_windows": list(EVENT_WINDOW_RANGES),
                "source": "event_records+notification_events",
            },
        }
    )


async def _read_capacity_notification_consensus(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    now: datetime,
) -> dict[str, Any]:
    query = {
        "event_type": {"$in": ["sub2api.capacity.low", "sub2api.capacity.recovered"]},
        "source": "sub2api_capacity",
        "resource_id": f"{site_id}:{group_id}",
        "created_at": {"$gte": now - timedelta(days=7)},
    }
    warnings: list[str] = []
    try:
        events = [
            _compact_capacity_notification_event(item)
            async for item in db.notification_events.find(query).sort("created_at", -1).limit(100)
        ]
    except Exception as exc:  # noqa: BLE001 - notification evidence must not hide account events.
        events = []
        warnings.append(f"notification_events_unavailable:{exc}")
    try:
        meta = await db.sub2api_capacity_notification_meta.find_one({"_id": f"{site_id}:{group_id}"}) or {}
    except Exception as exc:  # noqa: BLE001 - notification evidence is optional context.
        meta = {}
        warnings.append(f"capacity_notification_meta_unavailable:{exc}")
    latest = events[0] if events else None
    if meta.get("active_alert") is True:
        current_state = "active_low_capacity_alert"
    elif latest and latest.get("event_type") == "sub2api.capacity.recovered":
        current_state = "recovered"
    elif latest and latest.get("event_type") == "sub2api.capacity.low":
        current_state = "low_capacity_alert_recorded"
    else:
        current_state = "no_capacity_notification_evidence"
    return {
        "source": "notification_events+sub2api_capacity_notification_meta",
        "site_id": site_id,
        "group_id": group_id,
        "current_state": current_state,
        "active_alert": meta.get("active_alert") is True,
        "last_observed_status": meta.get("last_observed_status"),
        "last_observed_at": meta.get("last_observed_at"),
        "last_notified_status": meta.get("last_notified_status"),
        "last_recovered_at": meta.get("last_recovered_at"),
        "last_delivery_status": meta.get("last_delivery_status"),
        "latest_event": latest,
        "events_7d": events,
        "event_count_7d": len(events),
        "warnings": warnings,
    }


def _compact_capacity_notification_event(item: dict[str, Any]) -> dict[str, Any]:
    payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
    return {
        "notification_event_id": str(item.get("_id") or item.get("id") or "") or None,
        "event_type": item.get("event_type"),
        "notification_type": payload.get("notification_type"),
        "severity": item.get("severity"),
        "delivery_status": item.get("status"),
        "success_count": _int_or_none(item.get("success_count")) or 0,
        "failed_count": _int_or_none(item.get("failed_count")) or 0,
        "health_status": payload.get("health_status"),
        "trigger_reason": payload.get("trigger_reason"),
        "created_at": item.get("created_at"),
        "finished_at": item.get("finished_at"),
    }


def _capacity_consensus_for_window(consensus: dict[str, Any], *, range_value: str, now: datetime) -> dict[str, Any]:
    hours = {"1h": 1, "6h": 6, "24h": 24, "7d": 24 * 7}.get(range_value, 24)
    cutoff = now - timedelta(hours=hours)
    events = [
        item
        for item in consensus.get("events_7d", [])
        if isinstance(item, dict) and (_coerce_datetime(item.get("created_at")) or datetime.min.replace(tzinfo=UTC)) >= cutoff
    ]
    return {
        "current_state": consensus.get("current_state"),
        "active_alert": consensus.get("active_alert") is True,
        "alert_count": sum(1 for item in events if item.get("event_type") == "sub2api.capacity.low"),
        "recovery_count": sum(1 for item in events if item.get("event_type") == "sub2api.capacity.recovered"),
        "latest_event": events[0] if events else None,
    }


async def read_agent_event_stream_summary(
    db: AsyncIOMotorDatabase,
    *,
    site_id: str,
    group_id: int,
    account_type: str | None = None,
    limit: int = 80,
) -> dict[str, Any]:
    """Compatibility wrapper for the previous stage-three context field."""

    event_windows = await read_agent_event_windows(
        db,
        site_id=site_id,
        group_id=group_id,
        account_type=account_type,
        detail_24h_limit=limit,
    )
    summary_24h = event_windows.get("summary_24h") if isinstance(event_windows.get("summary_24h"), dict) else {}
    return serialize_doc(
        {
            "data_source": event_windows.get("data_source"),
            "range": "24h",
            "total": summary_24h.get("total_events"),
            "summary": summary_24h.get("summary"),
            "event_type_counts": summary_24h.get("event_type_counts"),
            "status_transition_counts": summary_24h.get("status_transition_counts"),
            "error_category_counts": summary_24h.get("error_category_counts"),
            "recent_timeline": event_windows.get("detail_24h", {}).get("items", [])[:30],
            "notable_patterns": event_windows.get("notable_patterns"),
            "event_windows": event_windows,
        }
    )


def _window_summary(
    *,
    range_value: str,
    response: dict[str, Any],
    items: list[dict[str, Any]],
    site_id: str,
    group_id: int,
    pool_id: str,
) -> dict[str, Any]:
    summary = response.get("summary") if isinstance(response.get("summary"), dict) else {}
    daily_counts = _daily_event_counts(items)
    hourly_counts = _hourly_event_counts(items)
    event_type_counts = _event_type_counts(items)
    return {
        "window": range_value,
        "site_id": site_id,
        "pool_id": pool_id,
        "group_id": group_id,
        "total_events": response.get("total"),
        "account_count": _account_count(items),
        "event_type_counts": event_type_counts,
        "status_transition_counts": _status_transition_counts(items),
        "error_category_counts": _error_category_counts(items),
        "severity_counts": _severity_counts(items),
        "daily_event_counts": daily_counts,
        "hourly_event_counts": hourly_counts,
        "busiest_day": _busiest_bucket(daily_counts),
        "busiest_hour": _busiest_bucket(hourly_counts),
        "high_value_event_count": sum(1 for item in items if _is_high_value_event(item)),
        "first_event_at": _first_event_at(items),
        "last_event_at": _last_event_at(items) or summary.get("last_event_at"),
        "summary": summary,
        "special_events": _special_event_summary(summary=summary, event_type_counts=event_type_counts),
        "clusters": _event_clusters(items),
        "top_accounts": _top_accounts(items),
        "interpretation": _window_interpretation(range_value, response=response, items=items),
        "sample_size": len(items),
        "sample_limit": response.get("limit"),
        "full_7d_detail_included": False,
    }


def _detail_events(items: list[dict[str, Any]], *, site_id: str, group_id: int, pool_id: str) -> list[dict[str, Any]]:
    return [_event_detail(item, site_id=site_id, group_id=group_id, pool_id=pool_id) for item in items[:80]]


def _event_detail(item: dict[str, Any], *, site_id: str, group_id: int, pool_id: str) -> dict[str, Any]:
    return {
        "event_id": item.get("id"),
        "occurred_at": item.get("occurred_at") or item.get("detected_at"),
        "detected_at": item.get("detected_at"),
        "site_id": item.get("site_id") or site_id,
        "pool_id": pool_id,
        "group_id": group_id,
        "group_ids": item.get("group_ids"),
        "account_id": item.get("identity_id") or item.get("remote_account_id"),
        "remote_account_id": item.get("remote_account_id"),
        "account_email_masked": _redact_email(item.get("email") or item.get("normalized_email")),
        "event_type": item.get("event_type"),
        "severity": item.get("severity"),
        "from_status": item.get("previous_status"),
        "to_status": item.get("current_status"),
        "previous_status": item.get("previous_status"),
        "current_status": item.get("current_status"),
        "previous_schedulable": item.get("previous_schedulable"),
        "current_schedulable": item.get("current_schedulable"),
        "error_category": item.get("error_category"),
        "is_401": item.get("is_401"),
        "normal_use_seconds": item.get("normal_use_seconds"),
        "usage_duration_seconds": item.get("usage_duration_seconds"),
        "message": _short_text(item.get("current_error_message") or item.get("raw_excerpt"), limit=180),
        "evidence": _event_evidence(item),
    }


def _special_event_summary(*, summary: dict[str, Any], event_type_counts: dict[str, int]) -> dict[str, Any]:
    official_refresh_count = _int_or_none(summary.get("official_usage_refreshes"))
    recovered_count = _int_or_none(summary.get("recovered_401"))
    return {
        "official_usage_refresh": {
            "confirmed_account_count": official_refresh_count
            if official_refresh_count is not None
            else int(event_type_counts.get("official_usage_refresh") or 0),
            "meaning": "同账号类型达到共识的官方额度提前刷新，不应解释为用户消耗骤降。",
        },
        "duplicate_email_resolved": {
            "event_count": int(event_type_counts.get("duplicate_email_resolved") or 0),
            "meaning": "重复邮箱对应的多个远端账号已收敛，容量重复计算风险已解除。",
        },
        "confirmed_401_recovery": {
            "account_count": recovered_count if recovered_count is not None else int(event_type_counts.get("401_recovered") or 0),
            "required_consecutive_healthy_probes": CONFIRMED_401_RECOVERY_COUNT,
            "meaning": "只有连续健康探测达到阈值后才记录 401_recovered。",
        },
    }


def _event_evidence(item: dict[str, Any]) -> dict[str, Any] | None:
    event_type = str(item.get("event_type") or "")
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    if event_type == "official_usage_refresh":
        return {
            "official_refresh_confirmed": details.get("official_refresh_confirmed") is True,
            "confirmed_account_types": _string_list(details.get("confirmed_account_types")),
            "candidate_count": _int_or_none(details.get("candidate_count")),
            "eligible_account_count": _int_or_none(details.get("eligible_account_count")),
            "type_consensus": _compact_type_consensus(details.get("type_consensus")),
            "previous_used_percent": _number_or_none(details.get("previous_used_percent")),
            "current_used_percent": _number_or_none(details.get("current_used_percent")),
            "previous_reset_at": details.get("previous_reset_at"),
            "current_reset_at": details.get("current_reset_at"),
        }
    if event_type == "duplicate_email_resolved":
        return {
            "duplicate_state": details.get("duplicate_state") or "resolved",
            "previous_remote_account_count": _int_or_none(details.get("previous_count")),
            "current_remote_account_count": _int_or_none(details.get("count")),
        }
    if event_type == "401_recovered":
        return {
            "recovery_confirmed": True,
            "healthy_probe_streak": _int_or_none(details.get("healthy_probe_streak")) or CONFIRMED_401_RECOVERY_COUNT,
            "required_consecutive_healthy_probes": _int_or_none(details.get("required_healthy_probes")) or CONFIRMED_401_RECOVERY_COUNT,
        }
    return None


def _compact_type_consensus(value: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(value, dict):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for account_type, item in list(value.items())[:8]:
        if not isinstance(item, dict):
            continue
        result[str(account_type)] = {
            "confirmed": item.get("confirmed") is True,
            "candidate_count": _int_or_none(item.get("candidate_count")),
            "eligible_account_count": _int_or_none(item.get("eligible_account_count")),
            "candidate_ratio": _number_or_none(item.get("candidate_ratio")),
        }
    return result


def _event_type_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(item.get("event_type") or "unknown") for item in items)
    return dict(counter.most_common(20))


def _status_transition_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        previous_status = str(item.get("previous_status") or "-")
        current_status = str(item.get("current_status") or "-")
        if previous_status != "-" or current_status != "-":
            counter[f"{previous_status}->{current_status}"] += 1
    return dict(counter.most_common(20))


def _error_category_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        if item.get("error_category"):
            counter[str(item.get("error_category"))] += 1
        elif item.get("is_401"):
            counter["401"] += 1
    return dict(counter.most_common(20))


def _severity_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter = Counter(str(item.get("severity") or "unknown") for item in items)
    return dict(counter.most_common())


def _account_count(items: list[dict[str, Any]]) -> int:
    keys = {_account_key(item) for item in items if _account_key(item)}
    return len(keys)


def _top_accounts(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        key = _account_key(item)
        if key:
            grouped[key].append(item)
    ranked = sorted(grouped.items(), key=lambda pair: len(pair[1]), reverse=True)
    result: list[dict[str, Any]] = []
    for key, account_items in ranked[:10]:
        latest = max(account_items, key=lambda item: _datetime_sort_key(item))
        result.append(
            {
                "account_key": key,
                "account_email_masked": _redact_email(latest.get("email") or latest.get("normalized_email")),
                "remote_account_id": latest.get("remote_account_id"),
                "event_count": len(account_items),
                "event_type_counts": _event_type_counts(account_items),
                "last_event_at": latest.get("detected_at") or latest.get("occurred_at"),
                "last_status": latest.get("current_status"),
                "last_error_category": latest.get("error_category"),
            }
        )
    return result


def _event_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    clusters.extend(_time_burst_clusters(items))
    clusters.extend(_account_clusters(items))
    clusters.extend(_pool_site_clusters(items))
    clusters.extend(_type_clusters(items))
    clusters.extend(_error_clusters(items))
    clusters.extend(_status_transition_clusters(items))
    clusters.sort(key=lambda item: int(item.get("event_count") or 0), reverse=True)
    return clusters[:16]


def _time_burst_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    interesting_items = [
        item
        for item in items
        if item.get("is_401")
        or str(item.get("event_type") or "") in {"401_detected", "remote_removed_confirmed", "missing_suspected"}
        or str(item.get("severity") or "") == "critical"
    ]
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in interesting_items:
        by_type[_cluster_event_label(item)].append(item)
    for label, label_items in by_type.items():
        window = _largest_time_window(label_items, minutes=10)
        if int(window.get("count") or 0) >= 3:
            clusters.append(
                {
                    "cluster_type": "time_burst",
                    "dominant_event_type": label,
                    "dominant_error_category": _dominant_error_category(label_items),
                    "event_count": window.get("count"),
                    "account_count": window.get("account_count"),
                    "window_start": window.get("started_at"),
                    "window_end": window.get("ended_at"),
                    "duration_minutes": window.get("duration_minutes"),
                    "interpretation": f"{label} 在约 {window.get('duration_minutes')} 分钟内集中出现 {window.get('count')} 次，涉及 {window.get('account_count')} 个账号。",
                }
            )
    return clusters


def _account_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        key = _account_key(item)
        if key:
            grouped[key].append(item)
    for key, account_items in grouped.items():
        if len(account_items) >= 3:
            latest = max(account_items, key=lambda item: _datetime_sort_key(item))
            clusters.append(
                {
                    "cluster_type": "account_repeated_events",
                    "account_key": key,
                    "account_email_masked": _redact_email(latest.get("email") or latest.get("normalized_email")),
                    "remote_account_id": latest.get("remote_account_id"),
                    "event_count": len(account_items),
                    "account_count": 1,
                    "event_type_counts": _event_type_counts(account_items),
                    "first_event_at": _first_event_at(account_items),
                    "last_event_at": _last_event_at(account_items),
                    "interpretation": f"同一账号在窗口内连续出现 {len(account_items)} 条事件，可能需要关注单账号反复异常。",
                }
            )
    return clusters


def _pool_site_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        site_id = str(item.get("site_id") or "").strip()
        if site_id:
            by_site[site_id].append(item)
        for group_id in item.get("group_ids") if isinstance(item.get("group_ids"), list) else []:
            by_group[str(group_id)].append(item)
    for site_id, site_items in by_site.items():
        if len(site_items) >= 3:
            clusters.append(
                {
                    "cluster_type": "site_cluster",
                    "site_id": site_id,
                    "event_count": len(site_items),
                    "account_count": _account_count(site_items),
                    "interpretation": f"同一站点 {site_id} 在窗口内出现 {len(site_items)} 条事件，涉及 {_account_count(site_items)} 个账号。",
                }
            )
    for group_id, group_items in by_group.items():
        if len(group_items) >= 3:
            clusters.append(
                {
                    "cluster_type": "pool_cluster",
                    "group_id": _int_or_none(group_id),
                    "event_count": len(group_items),
                    "account_count": _account_count(group_items),
                    "interpretation": f"同一账号池 group #{group_id} 在窗口内出现 {len(group_items)} 条事件，涉及 {_account_count(group_items)} 个账号。",
                }
            )
    return clusters


def _type_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    by_type: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        by_type[str(item.get("event_type") or "unknown")].append(item)
    for event_type, event_items in by_type.items():
        if len(event_items) >= 3:
            clusters.append(
                {
                    "cluster_type": "event_type_cluster",
                    "event_type": event_type,
                    "event_count": len(event_items),
                    "account_count": _account_count(event_items),
                    "interpretation": f"窗口内出现 {len(event_items)} 条 {event_type} 事件，涉及 {_account_count(event_items)} 个账号。",
                }
            )
    return clusters


def _error_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    by_error: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        if item.get("error_category"):
            by_error[str(item["error_category"])].append(item)
        elif item.get("is_401"):
            by_error["401"].append(item)
    for error_category, error_items in by_error.items():
        if len(error_items) >= 3:
            clusters.append(
                {
                    "cluster_type": "error_category_cluster",
                    "error_category": error_category,
                    "event_count": len(error_items),
                    "account_count": _account_count(error_items),
                    "interpretation": f"窗口内出现 {len(error_items)} 条 {error_category} 错误相关事件，涉及 {_account_count(error_items)} 个账号。",
                }
            )
    return clusters


def _status_transition_clusters(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clusters: list[dict[str, Any]] = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in items:
        previous_status = str(item.get("previous_status") or "-")
        current_status = str(item.get("current_status") or "-")
        if previous_status != "-" or current_status != "-":
            grouped[f"{previous_status}->{current_status}"].append(item)
    for transition, transition_items in grouped.items():
        if len(transition_items) >= 3:
            clusters.append(
                {
                    "cluster_type": "status_transition_cluster",
                    "transition": transition,
                    "event_count": len(transition_items),
                    "account_count": _account_count(transition_items),
                    "interpretation": f"窗口内出现 {len(transition_items)} 条状态迁移 {transition}，涉及 {_account_count(transition_items)} 个账号。",
                }
            )
    return clusters


def _window_interpretation(range_value: str, *, response: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    interpretations: list[str] = []
    total = response.get("total")
    account_count = _account_count(items)
    if total:
        interpretations.append(f"最近 {range_value} 事件流共记录 {total} 条事件，样本中涉及 {account_count} 个账号。")
    summary = response.get("summary") if isinstance(response.get("summary"), dict) else {}
    detected_401 = summary.get("detected_401")
    if detected_401:
        interpretations.append(f"最近 {range_value} 记录到 {detected_401} 个账号出现 401。")
    recovered_401 = summary.get("recovered_401")
    if recovered_401:
        interpretations.append(
            f"最近 {range_value} 记录到 {recovered_401} 个账号经连续 {CONFIRMED_401_RECOVERY_COUNT} 次健康探测确认从 401 恢复。"
        )
    official_refreshes = summary.get("official_usage_refreshes")
    if official_refreshes:
        interpretations.append(
            f"最近 {range_value} 有 {official_refreshes} 个账号经同类型共识确认发生官方额度提前刷新，不应把额度归零解释为消耗骤降或容量异常。"
        )
    duplicate_resolved = _event_type_counts(items).get("duplicate_email_resolved")
    if duplicate_resolved:
        interpretations.append(f"最近 {range_value} 出现 {duplicate_resolved} 条重复邮箱已解决事件，相关容量重复计算风险已解除。")
    usage_rollovers = summary.get("usage_rollovers")
    if usage_rollovers:
        interpretations.append(f"最近 {range_value} 出现 {usage_rollovers} 个额度重置或限额相关事件。")
    removed_events = summary.get("removed_events")
    if removed_events:
        interpretations.append(f"最近 {range_value} 出现 {removed_events} 个远端移除相关事件。")
    high_value_count = sum(1 for item in items if _is_high_value_event(item))
    if high_value_count:
        interpretations.append(f"最近 {range_value} 样本中包含 {high_value_count} 条高价值异常事件，包括 401、封禁/移除、恢复、限额或错误变化。")
    if range_value == "1h" and high_value_count >= 3:
        interpretations.append("最近 1h 存在即时突发特征，需要结合突发消耗趋势判断是否同向恶化。")
    if range_value == "6h":
        clusters = _time_burst_clusters(items)
        if clusters:
            interpretations.append("最近 6h 存在短时间集中异常，需判断是单点爆发后停止，还是仍在持续。")
        elif high_value_count:
            interpretations.append("最近 6h 有异常事件但未形成明显集中爆发，偏向短期零散波动。")
    if range_value == "24h":
        busiest_hour = _busiest_bucket(_hourly_event_counts(items))
        if busiest_hour:
            interpretations.append(f"最近 24h 事件最集中的小时段是 {busiest_hour.get('bucket')}，事件数 {busiest_hour.get('count')}。")
    if range_value == "7d":
        busiest_day = _busiest_bucket(_daily_event_counts(items))
        busiest_hour = _busiest_bucket(_hourly_event_counts(items))
        if busiest_day:
            interpretations.append(f"最近 7d 样本中事件最多的日期是 {busiest_day.get('bucket')}，事件数 {busiest_day.get('count')}。")
        if busiest_hour:
            interpretations.append(f"最近 7d 样本中事件最常出现的小时是 {busiest_hour.get('bucket')} 点，事件数 {busiest_hour.get('count')}。")
    for cluster in _event_clusters(items)[:5]:
        text = cluster.get("interpretation")
        if isinstance(text, str) and text.strip():
            interpretations.append(text.strip())
    if not interpretations:
        interpretations.append(f"最近 {range_value} 未发现明显事件异常。")
    return interpretations[:12]


def _merge_notable_patterns(summaries: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    patterns: list[dict[str, Any]] = []
    for window, summary in summaries.items():
        for cluster in summary.get("clusters") if isinstance(summary.get("clusters"), list) else []:
            if not isinstance(cluster, dict):
                continue
            patterns.append({"window": window, **cluster})
    patterns.sort(key=lambda item: int(item.get("event_count") or 0), reverse=True)
    return patterns[:20]


def _largest_time_window(items: list[dict[str, Any]], *, minutes: int) -> dict[str, Any]:
    parsed = sorted((_datetime_text(item), item) for item in items if _datetime_text(item))
    best: list[tuple[str, dict[str, Any]]] = []
    for index, (started_at, _) in enumerate(parsed):
        window = [(started_at, parsed[index][1])]
        for candidate, item in parsed[index + 1 :]:
            if _minutes_between(started_at, candidate) <= minutes:
                window.append((candidate, item))
            else:
                break
        if len(window) > len(best):
            best = window
    if not best:
        return {"count": 0}
    return {
        "count": len(best),
        "account_count": _account_count([item for _time, item in best]),
        "started_at": best[0][0],
        "ended_at": best[-1][0],
        "duration_minutes": _minutes_between(best[0][0], best[-1][0]),
    }


def _cluster_event_label(item: dict[str, Any]) -> str:
    if item.get("is_401") or item.get("event_type") == "401_detected":
        return "401_detected"
    return str(item.get("event_type") or item.get("error_category") or "unknown")


def _first_event_at(items: list[dict[str, Any]]) -> str | None:
    values = [_datetime_text(item) for item in items if _datetime_text(item)]
    return min(values) if values else None


def _last_event_at(items: list[dict[str, Any]]) -> str | None:
    values = [_datetime_text(item) for item in items if _datetime_text(item)]
    return max(values) if values else None


def _datetime_text(item: dict[str, Any]) -> str | None:
    value = item.get("detected_at") or item.get("occurred_at")
    return str(value) if value else None


def _datetime_sort_key(item: dict[str, Any]) -> datetime:
    text = _datetime_text(item)
    if not text:
        return datetime.min
    return _parse_datetime_text(text) or datetime.min


def _parse_datetime_text(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _coerce_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _minutes_between(start: str, end: str) -> int:
    try:
        start_dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end.replace("Z", "+00:00"))
    except ValueError:
        return 0
    return max(0, int((end_dt - start_dt).total_seconds() / 60))


def _account_key(item: dict[str, Any]) -> str | None:
    for value in (
        item.get("identity_id"),
        item.get("normalized_email"),
        item.get("email"),
        item.get("remote_account_id"),
    ):
        if value is not None and str(value).strip():
            return str(value).strip().lower()
    return None


def _prioritized_detail_items(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    ranked = sorted(items, key=lambda item: (_event_priority(item), _datetime_sort_key(item)), reverse=True)
    return ranked[:limit]


def _event_priority(item: dict[str, Any]) -> int:
    score = 0
    event_type = str(item.get("event_type") or "")
    current_status = str(item.get("current_status") or "").lower()
    previous_status = str(item.get("previous_status") or "").lower()
    error_category = str(item.get("error_category") or "").lower()
    message = str(item.get("current_error_message") or item.get("raw_excerpt") or "").lower()
    if item.get("is_401") or event_type == "401_detected" or "401" in error_category or "authentication" in error_category:
        score += 100
    if event_type in {"remote_removed_confirmed", "missing_suspected"} or current_status in {"disabled", "banned", "invalid", "failed", "error"}:
        score += 90
    if event_type in {"401_recovered", "remote_account_reappeared"}:
        score += 75
    if event_type == "official_usage_refresh":
        score += 80
    if event_type == "duplicate_email_resolved":
        score += 70
    if event_type == "usage_rollover" or "limit" in message or "quota" in message:
        score += 65
    if previous_status and current_status and previous_status != current_status:
        score += 55
    if item.get("error_category") or item.get("current_error_message"):
        score += 45
    if str(item.get("severity") or "") == "critical":
        score += 30
    if str(item.get("severity") or "") == "warning":
        score += 20
    return score


def _is_high_value_event(item: dict[str, Any]) -> bool:
    return _event_priority(item) >= 45


def _daily_event_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        parsed = _parse_datetime_text(_datetime_text(item))
        if parsed:
            counter[parsed.date().isoformat()] += 1
    return dict(sorted(counter.items()))


def _hourly_event_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counter: Counter[str] = Counter()
    for item in items:
        parsed = _parse_datetime_text(_datetime_text(item))
        if parsed:
            counter[f"{parsed.hour:02d}"] += 1
    return dict(sorted(counter.items()))


def _busiest_bucket(counts: dict[str, int]) -> dict[str, Any] | None:
    if not counts:
        return None
    bucket, count = max(counts.items(), key=lambda item: item[1])
    return {"bucket": bucket, "count": count}


def _dominant_error_category(items: list[dict[str, Any]]) -> str | None:
    counts = _error_category_counts(items)
    if not counts:
        return None
    return next(iter(counts.keys()))


def _pool_id(*, site_id: str, group_id: int) -> str:
    return f"sub2api:{site_id}:{group_id}"


def _group_id_from_pool_id(pool_id: str | None) -> int | None:
    parts = str(pool_id or "").split(":")
    if len(parts) == 3 and parts[0] == "sub2api":
        return _int_or_none(parts[2])
    return None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:8]


def _number_or_none(value: Any) -> float | None:
    if isinstance(value, bool) or value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return None


def _empty_event_windows(*, site_id: str | None, group_id: int | None, pool_id: str | None, reason: str) -> dict[str, Any]:
    resolved_pool_id = pool_id or (f"sub2api:{site_id}:{group_id}" if site_id and group_id is not None else None)
    return serialize_doc(
        {
            "data_source": "event_records",
            "refresh_behavior": "read_existing_cache_only",
            "detail_24h": {"window_hours": 24, "max_items": 80, "total": 0, "selection": "none", "items": []},
            "summary_1h": _empty_window_summary("1h", site_id=site_id, group_id=group_id, pool_id=resolved_pool_id),
            "summary_6h": _empty_window_summary("6h", site_id=site_id, group_id=group_id, pool_id=resolved_pool_id),
            "summary_24h": _empty_window_summary("24h", site_id=site_id, group_id=group_id, pool_id=resolved_pool_id),
            "summary_7d": _empty_window_summary("7d", site_id=site_id, group_id=group_id, pool_id=resolved_pool_id),
            "notable_patterns": [],
            "consensus_evidence": {
                "capacity_notifications": {
                    "source": "notification_events+sub2api_capacity_notification_meta",
                    "current_state": "unavailable",
                    "active_alert": False,
                    "events_7d": [],
                    "event_count_7d": 0,
                }
            },
            "data_quality": {
                "available": False,
                "detail_24h_limited_to": 80,
                "summary_windows": list(EVENT_WINDOW_RANGES),
                "source": "event_records",
                "warnings": [reason],
            },
        }
    )


def _empty_window_summary(
    window: str,
    *,
    site_id: str | None,
    group_id: int | None,
    pool_id: str | None,
) -> dict[str, Any]:
    return {
        "window": window,
        "site_id": site_id,
        "pool_id": pool_id,
        "group_id": group_id,
        "total_events": 0,
        "account_count": 0,
        "event_type_counts": {},
        "status_transition_counts": {},
        "error_category_counts": {},
        "severity_counts": {},
        "daily_event_counts": {},
        "hourly_event_counts": {},
        "busiest_day": None,
        "busiest_hour": None,
        "high_value_event_count": 0,
        "first_event_at": None,
        "last_event_at": None,
        "summary": {},
        "special_events": {},
        "clusters": [],
        "top_accounts": [],
        "interpretation": [],
        "sample_size": 0,
        "sample_limit": 0,
        "full_7d_detail_included": False,
    }


def _int_or_none(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _summary_sample_limit(range_value: str) -> int:
    if range_value == "1h":
        return 120
    if range_value == "6h":
        return 240
    if range_value == "24h":
        return 300
    return 500


def _normalize_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, min(number, maximum))


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
