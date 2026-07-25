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


def _params(
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> dict[str, Any]:
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
            (
                bindparam("limit", type_=Integer()),
                bindparam("offset", type_=Integer()),
            )
        )
    return text(sql).bindparams(*parameters)


def _segment_predicate(filters: TrafficAnalyticsFilters) -> str:
    if filters.segment == "internal":
        return "internal.internal_user_id IS NOT NULL"
    if filters.segment == "ordinary":
        return (
            "internal.internal_user_id IS NULL "
            "AND COALESCE(facts.is_excluded, FALSE) = FALSE "
            "AND excluded.external_user_id IS NULL"
        )
    return (
        "(internal.internal_user_id IS NOT NULL "
        "OR (COALESCE(facts.is_excluded, FALSE) = FALSE "
        "AND excluded.external_user_id IS NULL))"
    )


_HOMEPAGE_VISITS_FROM_WHERE = """
    FROM growth.homepage_visits AS homepage
    LEFT JOIN growth.tracking_links AS active_link
      ON active_link.tracking_link_id = homepage.active_tracking_link_id
     AND active_link.site_id = homepage.active_site_id
    LEFT JOIN growth.campaigns AS active_campaign
      ON active_campaign.campaign_id = active_link.campaign_id
     AND active_campaign.site_id = active_link.site_id
    WHERE homepage.is_counted
      AND homepage.visited_at >= :start_at
      AND homepage.visited_at < :end_at
      AND (
          :source_kind IS NULL
          OR homepage.active_source_kind = :source_kind
      )
      AND (
          homepage.active_source_kind <> 'promotion'
          OR (
              (:site_id IS NULL OR homepage.active_site_id = :site_id)
              AND (
                  :tracking_link_id IS NULL
                  OR homepage.active_tracking_link_id = :tracking_link_id
              )
              AND (:campaign_id IS NULL OR active_link.campaign_id = :campaign_id)
              AND (:channel_id IS NULL OR active_campaign.channel_id = :channel_id)
          )
      )
"""

_BUCKET_TIMEZONE_SQL = """
    COALESCE(
        (
            SELECT site.timezone
            FROM growth.sites AS site
            WHERE site.site_id = :site_id
        ),
        'UTC'
    )
"""


def _cohort_cte(filters: TrafficAnalyticsFilters) -> str:
    segment_predicate = _segment_predicate(filters)
    return f"""
        WITH cohort AS (
            SELECT
                attribution.site_id,
                attribution.external_user_id,
                attribution.tracking_link_id,
                attribution.source_kind AS source_kind,
                attribution.registered_at,
                internal.internal_user_id IS NOT NULL AS is_internal,
                COALESCE(facts.account_label, '') AS account_label,
                facts.successful_call_count,
                facts.first_successful_call_at,
                facts.last_successful_call_at,
                facts.has_continued_call,
                facts.first_payment_at,
                facts.second_payment_at,
                facts.settled_refund_count,
                facts.first_refund_at,
                facts.last_refund_at,
                facts.payment_total_minor,
                facts.refund_total_minor,
                facts.currency,
                link.source_name,
                campaign.campaign_id,
                campaign.name AS campaign_name,
                channel.channel_id,
                channel.name AS channel_name
            FROM growth.user_attributions AS attribution
            LEFT JOIN growth.user_facts AS facts
              ON facts.site_id = attribution.site_id
             AND facts.external_user_id = attribution.external_user_id
            LEFT JOIN growth.internal_users AS internal
              ON internal.site_id = attribution.site_id
             AND internal.external_user_id = attribution.external_user_id
             AND internal.active_from <= attribution.registered_at
             AND (
                  internal.active_until IS NULL
                  OR internal.active_until > attribution.registered_at
             )
            LEFT JOIN growth.user_exclusions AS excluded
              ON excluded.site_id = attribution.site_id
             AND excluded.external_user_id = attribution.external_user_id
             AND excluded.is_active
            LEFT JOIN growth.tracking_links AS link
              ON link.tracking_link_id = attribution.tracking_link_id
             AND link.site_id = attribution.site_id
            LEFT JOIN growth.campaigns AS campaign
              ON campaign.campaign_id = link.campaign_id
             AND campaign.site_id = link.site_id
            LEFT JOIN growth.channels AS channel
              ON channel.channel_id = campaign.channel_id
            WHERE attribution.registered_at >= :start_at
              AND attribution.registered_at < :end_at
              AND (:site_id IS NULL OR attribution.site_id = :site_id)
              AND (:source_kind IS NULL OR attribution.source_kind = :source_kind)
              AND (
                  :tracking_link_id IS NULL
                  OR attribution.tracking_link_id = :tracking_link_id
              )
              AND (:campaign_id IS NULL OR link.campaign_id = :campaign_id)
              AND (:channel_id IS NULL OR campaign.channel_id = :channel_id)
              AND {segment_predicate}
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
            WITH homepage AS (
                SELECT
                    COUNT(*)::BIGINT AS homepage_pv,
                    COUNT(DISTINCT homepage.anonymous_visitor_key)::BIGINT AS homepage_uv
                {_HOMEPAGE_VISITS_FROM_WHERE}
            ),
            promotion AS (
                SELECT
                    COUNT(*)::BIGINT AS link_pv,
                    COUNT(DISTINCT visit.anonymous_visitor_key)::BIGINT AS link_uv
                FROM growth.link_visits AS visit
                JOIN growth.tracking_links AS link
                  ON link.tracking_link_id = visit.tracking_link_id
                 AND link.site_id = visit.site_id
                JOIN growth.campaigns AS campaign
                  ON campaign.campaign_id = link.campaign_id
                 AND campaign.site_id = link.site_id
                WHERE visit.is_counted
                  AND visit.visited_at >= :start_at
                  AND visit.visited_at < :end_at
                  AND (:source_kind IS NULL OR :source_kind = 'promotion')
                  AND (:site_id IS NULL OR visit.site_id = :site_id)
                  AND (:tracking_link_id IS NULL OR link.tracking_link_id = :tracking_link_id)
                  AND (:campaign_id IS NULL OR link.campaign_id = :campaign_id)
                  AND (:channel_id IS NULL OR campaign.channel_id = :channel_id)
            )
            SELECT
                homepage.*,
                promotion.*,
                {_BUCKET_TIMEZONE_SQL} AS bucket_timezone
            FROM homepage CROSS JOIN promotion
            """
        ),
        params,
    )
    traffic = _public_row(traffic_result.mappings().one())

    cohort_result = await connection.execute(
        _analytics_text(
            _cohort_cte(filters)
            + """
            SELECT
                COUNT(*)::BIGINT AS registered_accounts,
                COUNT(*) FILTER (
                    WHERE source_kind = 'promotion'
                )::BIGINT AS promotion_registered_accounts,
                COUNT(*) FILTER (
                    WHERE first_successful_call_at IS NOT NULL
                )::BIGINT AS called_accounts,
                COUNT(*) FILTER (
                    WHERE first_payment_at IS NOT NULL
                )::BIGINT AS paid_accounts,
                COUNT(*) FILTER (
                    WHERE second_payment_at IS NOT NULL
                )::BIGINT AS second_paid_accounts,
                COUNT(*) FILTER (
                    WHERE has_continued_call
                )::BIGINT AS continued_accounts,
                COUNT(*) FILTER (
                    WHERE COALESCE(settled_refund_count, 0) > 0
                )::BIGINT AS refunded_accounts
            FROM cohort
            """
        ),
        params,
    )
    cohort = _public_row(cohort_result.mappings().one())
    return {**traffic, **cohort}


async def load_traffic_trends(
    connection: Any,
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> list[dict[str, Any]]:
    params = _params(filters, window)
    result = await connection.execute(
        _analytics_text(
            _cohort_cte(filters)
            + f"""
            , homepage_trend AS (
                SELECT
                    date_trunc(
                        :bucket,
                        homepage.visited_at,
                        {_BUCKET_TIMEZONE_SQL}
                    ) AS bucket_at,
                    COUNT(*)::BIGINT AS homepage_pv,
                    COUNT(DISTINCT homepage.anonymous_visitor_key)::BIGINT AS homepage_uv
                {_HOMEPAGE_VISITS_FROM_WHERE}
                GROUP BY 1
            ),
            link_trend AS (
                SELECT
                    date_trunc(
                        :bucket,
                        visit.visited_at,
                        {_BUCKET_TIMEZONE_SQL}
                    ) AS bucket_at,
                    COUNT(*)::BIGINT AS link_pv,
                    COUNT(DISTINCT visit.anonymous_visitor_key)::BIGINT AS link_uv
                FROM growth.link_visits AS visit
                JOIN growth.tracking_links AS link
                  ON link.tracking_link_id = visit.tracking_link_id
                 AND link.site_id = visit.site_id
                JOIN growth.campaigns AS campaign
                  ON campaign.campaign_id = link.campaign_id
                 AND campaign.site_id = link.site_id
                WHERE visit.is_counted
                  AND visit.visited_at >= :start_at
                  AND visit.visited_at < :end_at
                  AND (:source_kind IS NULL OR :source_kind = 'promotion')
                  AND (:site_id IS NULL OR visit.site_id = :site_id)
                  AND (:tracking_link_id IS NULL OR link.tracking_link_id = :tracking_link_id)
                  AND (:campaign_id IS NULL OR link.campaign_id = :campaign_id)
                  AND (:channel_id IS NULL OR campaign.channel_id = :channel_id)
                GROUP BY 1
            ),
            cohort_trend AS (
                SELECT
                    date_trunc(
                        :bucket,
                        registered_at,
                        {_BUCKET_TIMEZONE_SQL}
                    ) AS bucket_at,
                    COUNT(*)::BIGINT AS registered_accounts,
                    COUNT(*) FILTER (
                        WHERE first_successful_call_at IS NOT NULL
                    )::BIGINT AS called_accounts,
                    COUNT(*) FILTER (
                        WHERE first_payment_at IS NOT NULL
                    )::BIGINT AS paid_accounts
                FROM cohort
                GROUP BY 1
            ),
            buckets AS (
                SELECT bucket_at FROM homepage_trend
                UNION SELECT bucket_at FROM link_trend
                UNION SELECT bucket_at FROM cohort_trend
            )
            SELECT
                buckets.bucket_at,
                COALESCE(homepage_trend.homepage_pv, 0) AS homepage_pv,
                COALESCE(homepage_trend.homepage_uv, 0) AS homepage_uv,
                COALESCE(link_trend.link_pv, 0) AS link_pv,
                COALESCE(link_trend.link_uv, 0) AS link_uv,
                COALESCE(cohort_trend.registered_accounts, 0) AS registered_accounts,
                COALESCE(cohort_trend.called_accounts, 0) AS called_accounts,
                COALESCE(cohort_trend.paid_accounts, 0) AS paid_accounts
            FROM buckets
            LEFT JOIN homepage_trend USING (bucket_at)
            LEFT JOIN link_trend USING (bucket_at)
            LEFT JOIN cohort_trend USING (bucket_at)
            ORDER BY buckets.bucket_at
            """,
            include_bucket=True,
        ),
        params,
    )
    return [_public_row(row) for row in result.mappings().all()]


async def load_source_breakdown(
    connection: Any,
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> list[dict[str, Any]]:
    params = _params(filters, window)
    result = await connection.execute(
        _analytics_text(
            _cohort_cte(filters)
            + f"""
            , traffic_sources AS (
                SELECT
                    homepage.active_source_kind AS source_kind,
                    COUNT(*)::BIGINT AS entry_pv,
                    COUNT(DISTINCT homepage.anonymous_visitor_key)::BIGINT AS entry_uv
                {_HOMEPAGE_VISITS_FROM_WHERE}
                GROUP BY homepage.active_source_kind
            ),
            cohort_sources AS (
                SELECT
                    source_kind,
                    COUNT(*)::BIGINT AS registered_accounts,
                    COUNT(*) FILTER (
                        WHERE first_successful_call_at IS NOT NULL
                    )::BIGINT AS called_accounts,
                    COUNT(*) FILTER (
                        WHERE first_payment_at IS NOT NULL
                    )::BIGINT AS paid_accounts
                FROM cohort
                GROUP BY source_kind
            ),
            kinds AS (
                SELECT source_kind FROM traffic_sources
                UNION SELECT source_kind FROM cohort_sources
            )
            SELECT
                kinds.source_kind,
                COALESCE(traffic_sources.entry_pv, 0) AS entry_pv,
                COALESCE(traffic_sources.entry_uv, 0) AS entry_uv,
                COALESCE(cohort_sources.registered_accounts, 0) AS registered_accounts,
                COALESCE(cohort_sources.called_accounts, 0) AS called_accounts,
                COALESCE(cohort_sources.paid_accounts, 0) AS paid_accounts
            FROM kinds
            LEFT JOIN traffic_sources USING (source_kind)
            LEFT JOIN cohort_sources USING (source_kind)
            ORDER BY registered_accounts DESC, entry_uv DESC, source_kind
            """
        ),
        params,
    )
    return [_public_row(row) for row in result.mappings().all()]


async def load_link_performance(
    connection: Any,
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> list[dict[str, Any]]:
    params = _params(filters, window)
    result = await connection.execute(
        _analytics_text(
            _cohort_cte(filters)
            + """
            , link_traffic AS (
                SELECT
                    visit.tracking_link_id,
                    visit.site_id,
                    COUNT(*)::BIGINT AS link_pv,
                    COUNT(DISTINCT visit.anonymous_visitor_key)::BIGINT AS link_uv
                FROM growth.link_visits AS visit
                JOIN growth.tracking_links AS traffic_link
                  ON traffic_link.tracking_link_id = visit.tracking_link_id
                 AND traffic_link.site_id = visit.site_id
                JOIN growth.campaigns AS traffic_campaign
                  ON traffic_campaign.campaign_id = traffic_link.campaign_id
                 AND traffic_campaign.site_id = traffic_link.site_id
                WHERE visit.is_counted
                  AND visit.visited_at >= :start_at
                  AND visit.visited_at < :end_at
                  AND (:source_kind IS NULL OR :source_kind = 'promotion')
                  AND (:site_id IS NULL OR visit.site_id = :site_id)
                  AND (
                      :tracking_link_id IS NULL
                      OR traffic_link.tracking_link_id = :tracking_link_id
                  )
                  AND (
                      :campaign_id IS NULL
                      OR traffic_link.campaign_id = :campaign_id
                  )
                  AND (
                      :channel_id IS NULL
                      OR traffic_campaign.channel_id = :channel_id
                  )
                GROUP BY visit.tracking_link_id, visit.site_id
            ),
            link_cohort AS (
                SELECT
                    tracking_link_id,
                    site_id,
                    COUNT(*)::BIGINT AS registered_accounts,
                    COUNT(*) FILTER (
                        WHERE first_successful_call_at IS NOT NULL
                    )::BIGINT AS called_accounts,
                    COUNT(*) FILTER (
                        WHERE first_payment_at IS NOT NULL
                    )::BIGINT AS paid_accounts,
                    COUNT(*) FILTER (
                        WHERE second_payment_at IS NOT NULL
                    )::BIGINT AS second_paid_accounts,
                    COUNT(*) FILTER (
                        WHERE has_continued_call
                    )::BIGINT AS continued_accounts
                FROM cohort
                WHERE source_kind = 'promotion'
                GROUP BY tracking_link_id, site_id
            )
            SELECT
                link.tracking_link_id,
                link.site_id,
                link.code,
                link.source_name,
                campaign.campaign_id,
                campaign.name AS campaign_name,
                campaign.channel_id,
                channel.name AS channel_name,
                COALESCE(link_traffic.link_pv, 0) AS link_pv,
                COALESCE(link_traffic.link_uv, 0) AS link_uv,
                COALESCE(link_cohort.registered_accounts, 0) AS registered_accounts,
                COALESCE(link_cohort.called_accounts, 0) AS called_accounts,
                COALESCE(link_cohort.paid_accounts, 0) AS paid_accounts,
                COALESCE(link_cohort.second_paid_accounts, 0) AS second_paid_accounts,
                COALESCE(link_cohort.continued_accounts, 0) AS continued_accounts
            FROM growth.tracking_links AS link
            JOIN growth.campaigns AS campaign
              ON campaign.campaign_id = link.campaign_id
             AND campaign.site_id = link.site_id
            JOIN growth.channels AS channel
              ON channel.channel_id = campaign.channel_id
            LEFT JOIN link_traffic
              ON link_traffic.tracking_link_id = link.tracking_link_id
             AND link_traffic.site_id = link.site_id
            LEFT JOIN link_cohort
              ON link_cohort.tracking_link_id = link.tracking_link_id
             AND link_cohort.site_id = link.site_id
            WHERE (:source_kind IS NULL OR :source_kind = 'promotion')
              AND (:site_id IS NULL OR link.site_id = :site_id)
              AND (:tracking_link_id IS NULL OR link.tracking_link_id = :tracking_link_id)
              AND (:campaign_id IS NULL OR link.campaign_id = :campaign_id)
              AND (:channel_id IS NULL OR campaign.channel_id = :channel_id)
            ORDER BY registered_accounts DESC, link_uv DESC, link_pv DESC
            LIMIT 50
            """
        ),
        params,
    )
    return [_public_row(row) for row in result.mappings().all()]


async def load_amounts(
    connection: Any,
    filters: TrafficAnalyticsFilters,
    window: TrafficWindow,
) -> list[dict[str, Any]]:
    params = _params(filters, window)
    segment_predicate = _segment_predicate(filters)
    result = await connection.execute(
        _analytics_text(
            f"""
            SELECT
                facts.currency,
                COALESCE(SUM(facts.payment_total_minor), 0)::BIGINT
                    AS payment_total_minor,
                COALESCE(SUM(facts.refund_total_minor), 0)::BIGINT
                    AS refund_total_minor
            FROM growth.user_attributions AS attribution
            JOIN growth.user_facts AS facts
              ON facts.site_id = attribution.site_id
             AND facts.external_user_id = attribution.external_user_id
            LEFT JOIN growth.internal_users AS internal
              ON internal.site_id = attribution.site_id
             AND internal.external_user_id = attribution.external_user_id
             AND internal.active_from <= attribution.registered_at
             AND (
                  internal.active_until IS NULL
                  OR internal.active_until > attribution.registered_at
             )
            LEFT JOIN growth.user_exclusions AS excluded
              ON excluded.site_id = attribution.site_id
             AND excluded.external_user_id = attribution.external_user_id
             AND excluded.is_active
            LEFT JOIN growth.tracking_links AS link
              ON link.tracking_link_id = attribution.tracking_link_id
             AND link.site_id = attribution.site_id
            LEFT JOIN growth.campaigns AS campaign
              ON campaign.campaign_id = link.campaign_id
             AND campaign.site_id = link.site_id
            WHERE attribution.registered_at >= :start_at
              AND attribution.registered_at < :end_at
              AND (:site_id IS NULL OR attribution.site_id = :site_id)
              AND (:source_kind IS NULL OR attribution.source_kind = :source_kind)
              AND (
                  :tracking_link_id IS NULL
                  OR attribution.tracking_link_id = :tracking_link_id
              )
              AND (:campaign_id IS NULL OR link.campaign_id = :campaign_id)
              AND (:channel_id IS NULL OR campaign.channel_id = :channel_id)
              AND {segment_predicate}
            GROUP BY facts.currency
            ORDER BY facts.currency
            """
        ),
        params,
    )
    return [_public_row(row) for row in result.mappings().all()]


_MILESTONE_PREDICATES = {
    "registered": "TRUE",
    "called": "facts.first_successful_call_at IS NOT NULL",
    "paid": "facts.first_payment_at IS NOT NULL",
    "second_paid": "facts.second_payment_at IS NOT NULL",
    "continued": "facts.has_continued_call",
    "refunded": "COALESCE(facts.settled_refund_count, 0) > 0",
}


async def list_milestone_users(
    connection: Any,
    query: TrafficUsersQuery,
    window: TrafficWindow,
) -> tuple[list[dict[str, Any]], int]:
    params = {
        **_params(query, window),
        "limit": query.limit,
        "offset": query.offset,
    }
    milestone = _MILESTONE_PREDICATES[query.milestone]
    cohort = _cohort_cte(query)
    count_result = await connection.execute(
        _analytics_text(
            cohort
            + f"SELECT COUNT(*)::BIGINT AS total FROM cohort AS facts WHERE {milestone}"
        ),
        params,
    )
    total = int(count_result.mappings().one()["total"])
    list_result = await connection.execute(
        _analytics_text(
            cohort
            + f"""
            SELECT
                facts.site_id,
                facts.external_user_id,
                facts.account_label,
                facts.is_internal,
                facts.source_kind,
                facts.tracking_link_id,
                facts.source_name,
                facts.campaign_id,
                facts.campaign_name,
                facts.channel_id,
                facts.channel_name,
                facts.registered_at,
                facts.first_successful_call_at,
                facts.last_successful_call_at,
                facts.first_payment_at,
                facts.second_payment_at,
                facts.first_refund_at,
                facts.last_refund_at,
                facts.has_continued_call
            FROM cohort AS facts
            WHERE {milestone}
            ORDER BY facts.registered_at DESC, facts.site_id, facts.external_user_id
            LIMIT :limit OFFSET :offset
            """,
            include_pagination=True,
        ),
        params,
    )
    return (
        [_public_row(row) for row in list_result.mappings().all()],
        total,
    )
