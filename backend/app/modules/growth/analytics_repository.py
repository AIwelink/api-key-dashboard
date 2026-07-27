from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Integer, Text, Uuid, bindparam, text
from sqlalchemy.sql.elements import TextClause

from app.modules.growth.analytics_schemas import (
    TrafficAnalyticsFilters,
    TrafficUsersQuery,
    TrafficWindow,
)


def _public_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _public_row(row: Any) -> dict[str, Any]:
    return {key: _public_value(value) for key, value in dict(row).items()}


def _params(filters: TrafficAnalyticsFilters, window: TrafficWindow) -> dict[str, Any]:
    return {
        "start_at": window.start_at,
        "end_at": window.end_at,
        "bucket": window.bucket,
        "site_id": filters.site_id,
        "source_kind": filters.source_kind,
        "channel_id": filters.channel_id,
        "campaign_id": filters.campaign_id,
        "tracking_link_id": filters.tracking_link_id,
    }


def _analytics_text(
    sql: str,
    *,
    include_bucket: bool = False,
    include_pagination: bool = False,
) -> TextClause:
    parameters = [
        bindparam("start_at", type_=DateTime(timezone=True)),
        bindparam("end_at", type_=DateTime(timezone=True)),
        bindparam("site_id", type_=Text()),
        bindparam("source_kind", type_=Text()),
        bindparam("channel_id", type_=Uuid()),
        bindparam("campaign_id", type_=Uuid()),
        bindparam("tracking_link_id", type_=Uuid()),
    ]
    if include_bucket:
        parameters.append(bindparam("bucket", type_=Text()))
    if include_pagination:
        parameters.extend(
            (bindparam("limit", type_=Integer()), bindparam("offset", type_=Integer()))
        )
    return text(sql).bindparams(*parameters)


def _segment_predicate(filters: TrafficAnalyticsFilters) -> str:
    if filters.segment == "internal":
        return "internal.internal_user_id IS NOT NULL"
    if filters.segment == "ordinary":
        return "internal.internal_user_id IS NULL"
    return "TRUE"


_BUCKET_TIMEZONE_SQL = """
    COALESCE(
        (SELECT site.timezone FROM growth.sites AS site WHERE site.site_id = :site_id),
        'UTC'
    )
"""


_HOMEPAGE_VISITS_FROM_WHERE = """
    FROM growth.homepage_visits AS homepage
    LEFT JOIN growth.tracking_links AS active_link
      ON active_link.tracking_link_id = homepage.active_tracking_link_id
     AND active_link.site_id = homepage.active_site_id
    LEFT JOIN growth.campaigns AS active_campaign
      ON active_campaign.campaign_id = active_link.campaign_id
     AND active_campaign.site_id = active_link.site_id
    WHERE homepage.visited_at >= :start_at
      AND homepage.visited_at < :end_at
      AND (:source_kind IS NULL OR homepage.active_source_kind = :source_kind)
      AND (
          homepage.active_source_kind <> 'promotion'
          OR (
              (:site_id IS NULL OR homepage.active_site_id = :site_id)
              AND (:tracking_link_id IS NULL OR homepage.active_tracking_link_id = :tracking_link_id)
              AND (:campaign_id IS NULL OR active_link.campaign_id = :campaign_id)
              AND (:channel_id IS NULL OR active_campaign.channel_id = :channel_id)
          )
      )
"""


_LINK_VISITS_FROM_WHERE = """
    FROM growth.link_visits AS visit
    JOIN growth.tracking_links AS traffic_link
      ON traffic_link.tracking_link_id = visit.tracking_link_id
     AND traffic_link.site_id = visit.site_id
    JOIN growth.campaigns AS traffic_campaign
      ON traffic_campaign.campaign_id = traffic_link.campaign_id
     AND traffic_campaign.site_id = traffic_link.site_id
    WHERE visit.visited_at >= :start_at
      AND visit.visited_at < :end_at
      AND (:source_kind IS NULL OR :source_kind = 'promotion')
      AND (:site_id IS NULL OR visit.site_id = :site_id)
      AND (:tracking_link_id IS NULL OR visit.tracking_link_id = :tracking_link_id)
      AND (:campaign_id IS NULL OR traffic_link.campaign_id = :campaign_id)
      AND (:channel_id IS NULL OR traffic_campaign.channel_id = :channel_id)
"""


def _registration_cte(filters: TrafficAnalyticsFilters) -> str:
    return f"""
        WITH registration_rows AS (
            SELECT
                attribution.site_id,
                attribution.external_user_id,
                attribution.source_kind,
                attribution.tracking_link_id,
                attribution.registered_at,
                attribution.attributed_at,
                attribution.attribution_method,
                internal.internal_user_id IS NOT NULL AS is_internal,
                COALESCE(facts.account_label, '') AS account_label,
                facts.source_data_fresh_at,
                facts.computed_at,
                CASE
                    WHEN facts.external_user_id IS NULL THEN 'facts_pending'
                    WHEN facts.is_excluded THEN 'excluded'
                    ELSE 'normal'
                END AS fact_state,
                link.source_name,
                campaign.campaign_id,
                campaign.name AS campaign_name,
                channel.channel_id,
                channel.name AS channel_name,
                COALESCE(source_link_visit.visited_at, source_homepage_visit.visited_at)
                    AS source_touch_at
            FROM growth.user_attributions AS attribution
            LEFT JOIN growth.user_facts AS facts
              ON facts.site_id = attribution.site_id
             AND facts.external_user_id = attribution.external_user_id
            LEFT JOIN growth.internal_users AS internal
              ON internal.site_id = attribution.site_id
             AND internal.external_user_id = attribution.external_user_id
             AND internal.active_from <= attribution.registered_at
             AND (internal.active_until IS NULL OR internal.active_until > attribution.registered_at)
            LEFT JOIN growth.tracking_links AS link
              ON link.tracking_link_id = attribution.tracking_link_id
             AND link.site_id = attribution.site_id
            LEFT JOIN growth.campaigns AS campaign
              ON campaign.campaign_id = link.campaign_id
             AND campaign.site_id = link.site_id
            LEFT JOIN growth.channels AS channel
              ON channel.channel_id = campaign.channel_id
            LEFT JOIN growth.link_visits AS source_link_visit
              ON source_link_visit.visit_id = attribution.source_link_visit_id
            LEFT JOIN growth.homepage_visits AS source_homepage_visit
              ON source_homepage_visit.page_view_id = attribution.source_homepage_visit_id
            WHERE attribution.registered_at >= :start_at
              AND attribution.registered_at < :end_at
              AND (:site_id IS NULL OR attribution.site_id = :site_id)
              AND (:source_kind IS NULL OR attribution.source_kind = :source_kind)
              AND (:tracking_link_id IS NULL OR attribution.tracking_link_id = :tracking_link_id)
              AND (:campaign_id IS NULL OR link.campaign_id = :campaign_id)
              AND (:channel_id IS NULL OR campaign.channel_id = :channel_id)
              AND {_segment_predicate(filters)}
        )
    """


async def load_traffic_summary(
    connection: Any,
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> dict[str, Any]:
    params = _params(filters, window)
    traffic_result = await connection.execute(
        _analytics_text(
            f"""
            WITH homepage_summary AS (
                SELECT
                    COUNT(*)::BIGINT AS homepage_recorded_visits,
                    COUNT(*) FILTER (WHERE homepage.is_counted)::BIGINT AS homepage_counted_pv,
                    COUNT(DISTINCT homepage.anonymous_visitor_key)
                        FILTER (WHERE homepage.is_counted)::BIGINT AS homepage_session_uv,
                    COUNT(*) FILTER (WHERE NOT homepage.is_counted)::BIGINT
                        AS homepage_excluded_visits,
                    MAX(homepage.visited_at) AS homepage_latest_event_at
                {_HOMEPAGE_VISITS_FROM_WHERE}
            ),
            link_summary AS (
                SELECT
                    COUNT(*)::BIGINT AS link_recorded_visits,
                    COUNT(*) FILTER (WHERE visit.is_counted)::BIGINT AS link_counted_pv,
                    COUNT(DISTINCT visit.anonymous_visitor_key)
                        FILTER (WHERE visit.is_counted)::BIGINT AS link_session_uv,
                    COUNT(*) FILTER (WHERE NOT visit.is_counted)::BIGINT
                        AS link_excluded_visits,
                    COUNT(*) FILTER (WHERE visit.is_attribution_update)::BIGINT
                        AS link_attribution_updates,
                    MAX(visit.visited_at) AS link_latest_event_at
                {_LINK_VISITS_FROM_WHERE}
            )
            SELECT
                homepage_summary.*,
                link_summary.*,
                {_BUCKET_TIMEZONE_SQL} AS bucket_timezone
            FROM homepage_summary CROSS JOIN link_summary
            """
        ),
        params,
    )
    registration_result = await connection.execute(
        _analytics_text(
            _registration_cte(filters)
            + """
            SELECT
                COUNT(*) FILTER (WHERE fact_state <> 'excluded')::BIGINT
                    AS attributed_accounts,
                COUNT(*) FILTER (WHERE fact_state = 'excluded')::BIGINT
                    AS excluded_accounts,
                COUNT(*) FILTER (WHERE fact_state = 'facts_pending')::BIGINT
                    AS facts_pending_accounts
            FROM registration_rows
            """
        ),
        params,
    )
    return {
        **_public_row(traffic_result.mappings().one()),
        **_public_row(registration_result.mappings().one()),
    }


async def load_traffic_trends(
    connection: Any,
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> list[dict[str, Any]]:
    result = await connection.execute(
        _analytics_text(
            f"""
            WITH homepage_trend AS (
                SELECT
                    date_trunc(:bucket, homepage.visited_at, {_BUCKET_TIMEZONE_SQL}) AS bucket_at,
                    COUNT(*)::BIGINT AS homepage_pv,
                    COUNT(DISTINCT homepage.anonymous_visitor_key)::BIGINT AS homepage_uv
                {_HOMEPAGE_VISITS_FROM_WHERE}
                  AND homepage.is_counted
                GROUP BY 1
            ),
            link_trend AS (
                SELECT
                    date_trunc(:bucket, visit.visited_at, {_BUCKET_TIMEZONE_SQL}) AS bucket_at,
                    COUNT(*)::BIGINT AS link_pv,
                    COUNT(DISTINCT visit.anonymous_visitor_key)::BIGINT AS link_uv
                {_LINK_VISITS_FROM_WHERE}
                  AND visit.is_counted
                GROUP BY 1
            ),
            buckets AS (
                SELECT bucket_at FROM homepage_trend
                UNION SELECT bucket_at FROM link_trend
            )
            SELECT
                buckets.bucket_at,
                COALESCE(homepage_trend.homepage_pv, 0)::BIGINT AS homepage_pv,
                COALESCE(homepage_trend.homepage_uv, 0)::BIGINT AS homepage_uv,
                COALESCE(link_trend.link_pv, 0)::BIGINT AS link_pv,
                COALESCE(link_trend.link_uv, 0)::BIGINT AS link_uv
            FROM buckets
            LEFT JOIN homepage_trend USING (bucket_at)
            LEFT JOIN link_trend USING (bucket_at)
            ORDER BY buckets.bucket_at
            """,
            include_bucket=True,
        ),
        _params(filters, window),
    )
    return [_public_row(row) for row in result.mappings().all()]


async def load_active_source_breakdown(
    connection: Any,
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> list[dict[str, Any]]:
    result = await connection.execute(
        _analytics_text(
            f"""
            WITH ranked_events AS (
                SELECT
                    homepage.active_source_kind AS source_kind,
                    ROW_NUMBER() OVER (
                        PARTITION BY homepage.anonymous_visitor_key
                        ORDER BY homepage.visited_at DESC, homepage.page_view_id DESC
                    ) AS visitor_rank
                {_HOMEPAGE_VISITS_FROM_WHERE}
                  AND homepage.is_counted
            ),
            pv_sources AS (
                SELECT source_kind, COUNT(*)::BIGINT AS counted_pv
                FROM ranked_events
                GROUP BY source_kind
            ),
            uv_sources AS (
                SELECT source_kind, COUNT(*)::BIGINT AS session_uv
                FROM ranked_events
                WHERE visitor_rank = 1
                GROUP BY source_kind
            ),
            source_kinds AS (
                SELECT source_kind FROM pv_sources
                UNION SELECT source_kind FROM uv_sources
            )
            SELECT
                source_kinds.source_kind,
                COALESCE(pv_sources.counted_pv, 0)::BIGINT AS counted_pv,
                COALESCE(uv_sources.session_uv, 0)::BIGINT AS session_uv
            FROM source_kinds
            LEFT JOIN pv_sources USING (source_kind)
            LEFT JOIN uv_sources USING (source_kind)
            ORDER BY session_uv DESC, counted_pv DESC, source_kind
            """
        ),
        _params(filters, window),
    )
    return [_public_row(row) for row in result.mappings().all()]


async def load_classified_source_breakdown(
    connection: Any,
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> list[dict[str, Any]]:
    result = await connection.execute(
        _analytics_text(
            f"""
            SELECT
                homepage.classified_source_kind AS source_kind,
                COUNT(*)::BIGINT AS counted_pv,
                COUNT(DISTINCT homepage.anonymous_visitor_key)::BIGINT AS session_uv
            {_HOMEPAGE_VISITS_FROM_WHERE}
              AND homepage.is_counted
            GROUP BY homepage.classified_source_kind
            ORDER BY counted_pv DESC, source_kind
            """
        ),
        _params(filters, window),
    )
    return [_public_row(row) for row in result.mappings().all()]


async def load_link_performance(
    connection: Any,
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> list[dict[str, Any]]:
    result = await connection.execute(
        _analytics_text(
            _registration_cte(filters)
            + f"""
            , link_traffic AS (
                SELECT
                    visit.tracking_link_id,
                    visit.site_id,
                    COUNT(*)::BIGINT AS recorded_visits,
                    COUNT(*) FILTER (WHERE visit.is_counted)::BIGINT AS counted_pv,
                    COUNT(DISTINCT visit.anonymous_visitor_key)
                        FILTER (WHERE visit.is_counted)::BIGINT AS session_uv,
                    COUNT(*) FILTER (WHERE NOT visit.is_counted)::BIGINT AS excluded_visits,
                    COUNT(*) FILTER (WHERE visit.is_attribution_update)::BIGINT
                        AS attribution_updates
                {_LINK_VISITS_FROM_WHERE}
                GROUP BY visit.tracking_link_id, visit.site_id
            ),
            link_registrations AS (
                SELECT
                    site_id,
                    tracking_link_id,
                    COUNT(*) FILTER (
                        WHERE fact_state <> 'excluded'
                          AND COALESCE(fact_state = 'excluded', FALSE) = FALSE
                    )::BIGINT AS registered_accounts
                FROM registration_rows
                WHERE source_kind = 'promotion'
                  AND COALESCE(fact_state = 'excluded', FALSE) = FALSE
                GROUP BY site_id, tracking_link_id
            )
            SELECT
                link.tracking_link_id,
                link.site_id,
                link.code,
                link.source_name,
                link.status,
                link.valid_from,
                link.valid_until,
                campaign.campaign_id,
                campaign.name AS campaign_name,
                campaign.channel_id,
                channel.name AS channel_name,
                COALESCE(link_traffic.recorded_visits, 0)::BIGINT AS recorded_visits,
                COALESCE(link_traffic.counted_pv, 0)::BIGINT AS counted_pv,
                COALESCE(link_traffic.session_uv, 0)::BIGINT AS session_uv,
                COALESCE(link_traffic.excluded_visits, 0)::BIGINT AS excluded_visits,
                COALESCE(link_traffic.attribution_updates, 0)::BIGINT AS attribution_updates,
                COALESCE(link_registrations.registered_accounts, 0)::BIGINT
                    AS registered_accounts
            FROM growth.tracking_links AS link
            JOIN growth.campaigns AS campaign
              ON campaign.campaign_id = link.campaign_id
             AND campaign.site_id = link.site_id
            JOIN growth.channels AS channel ON channel.channel_id = campaign.channel_id
            LEFT JOIN link_traffic
              ON link_traffic.tracking_link_id = link.tracking_link_id
             AND link_traffic.site_id = link.site_id
            LEFT JOIN link_registrations
              ON link_registrations.tracking_link_id = link.tracking_link_id
             AND link_registrations.site_id = link.site_id
            WHERE (:source_kind IS NULL OR :source_kind = 'promotion')
              AND (:site_id IS NULL OR link.site_id = :site_id)
              AND (:tracking_link_id IS NULL OR link.tracking_link_id = :tracking_link_id)
              AND (:campaign_id IS NULL OR link.campaign_id = :campaign_id)
              AND (:channel_id IS NULL OR campaign.channel_id = :channel_id)
            ORDER BY registered_accounts DESC, session_uv DESC, counted_pv DESC
            LIMIT 50
            """
        ),
        _params(filters, window),
    )
    return [_public_row(row) for row in result.mappings().all()]


async def load_data_quality(
    connection: Any,
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> dict[str, Any]:
    params = _params(filters, window)
    exclusions_result = await connection.execute(
        _analytics_text(
            f"""
            SELECT event_scope, reason, COUNT(*)::BIGINT AS event_count
            FROM (
                SELECT
                    'homepage'::TEXT AS event_scope,
                    COALESCE(NULLIF(BTRIM(homepage.exclusion_reason), ''), 'unclassified') AS reason
                {_HOMEPAGE_VISITS_FROM_WHERE}
                  AND NOT homepage.is_counted
                UNION ALL
                SELECT
                    'link'::TEXT AS event_scope,
                    COALESCE(NULLIF(BTRIM(visit.exclusion_reason), ''), 'unclassified') AS reason
                {_LINK_VISITS_FROM_WHERE}
                  AND NOT visit.is_counted
            ) AS exclusions
            GROUP BY event_scope, reason
            ORDER BY event_count DESC, event_scope, reason
            """
        ),
        params,
    )
    diagnostic_result = await connection.execute(
        _analytics_text(
            _registration_cte(filters)
            + f"""
            , homepage_quality AS (
                SELECT COUNT(*) FILTER (WHERE homepage.is_bot)::BIGINT AS bot_visits
                {_HOMEPAGE_VISITS_FROM_WHERE}
            ),
            link_quality AS (
                SELECT COUNT(*) FILTER (WHERE visit.is_bot)::BIGINT AS bot_visits
                {_LINK_VISITS_FROM_WHERE}
            ),
            facts_quality AS (
                SELECT
                    MAX(source_data_fresh_at) AS latest_source_data_fresh_at,
                    MAX(computed_at) AS latest_computed_at,
                    EXTRACT(EPOCH FROM (MAX(computed_at) - MAX(source_data_fresh_at)))::BIGINT
                        AS facts_delay_seconds
                FROM registration_rows
                WHERE fact_state = 'normal'
            )
            SELECT
                homepage_quality.bot_visits AS homepage_bot_visits,
                link_quality.bot_visits AS link_bot_visits,
                facts_quality.*
            FROM homepage_quality CROSS JOIN link_quality CROSS JOIN facts_quality
            """
        ),
        params,
    )
    redirect_result = await connection.execute(
        _analytics_text(
            f"""
            SELECT visit.redirect_result, COUNT(*)::BIGINT AS event_count
            {_LINK_VISITS_FROM_WHERE}
            GROUP BY visit.redirect_result
            ORDER BY event_count DESC, visit.redirect_result
            """
        ),
        params,
    )
    http_result = await connection.execute(
        _analytics_text(
            f"""
            SELECT visit.http_status, COUNT(*)::BIGINT AS event_count
            {_LINK_VISITS_FROM_WHERE}
            GROUP BY visit.http_status
            ORDER BY event_count DESC, visit.http_status
            """
        ),
        params,
    )
    return {
        "exclusion_reasons": [_public_row(row) for row in exclusions_result.mappings().all()],
        **_public_row(diagnostic_result.mappings().one()),
        "redirect_results": [_public_row(row) for row in redirect_result.mappings().all()],
        "http_statuses": [_public_row(row) for row in http_result.mappings().all()],
    }


async def list_registration_attributions(
    connection: Any,
    query: TrafficUsersQuery,
    window: TrafficWindow,
) -> tuple[list[dict[str, Any]], int]:
    params = {**_params(query, window), "limit": query.limit, "offset": query.offset}
    registration_rows = _registration_cte(query)
    count_result = await connection.execute(
        _analytics_text(registration_rows + "SELECT COUNT(*)::BIGINT AS total FROM registration_rows"),
        params,
    )
    list_result = await connection.execute(
        _analytics_text(
            registration_rows
            + """
            SELECT
                site_id,
                external_user_id,
                account_label,
                is_internal,
                source_kind,
                tracking_link_id,
                source_name,
                campaign_id,
                campaign_name,
                channel_id,
                channel_name,
                registered_at,
                attributed_at,
                attribution_method,
                fact_state,
                source_touch_at
            FROM registration_rows
            ORDER BY registered_at DESC, site_id, external_user_id
            LIMIT :limit OFFSET :offset
            """,
            include_pagination=True,
        ),
        params,
    )
    return (
        [_public_row(row) for row in list_result.mappings().all()],
        int(count_result.mappings().one()["total"]),
    )
