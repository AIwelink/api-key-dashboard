from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text


INITIAL_DOMAIN_TABLES = (
    "sites",
    "channels",
    "campaigns",
    "tracking_links",
    "link_visits",
    "user_attributions",
    "user_exclusions",
    "user_usage_daily",
    "billing_facts",
    "user_facts",
    "sync_cursors",
    "sync_runs",
)

OPERATIONS_DOMAIN_TABLES = (
    "internal_users",
    "balance_conversion_rates",
    "ops_user_snapshots",
    "credit_events",
    "redemption_batches",
    "balance_adjustment_requests",
    "usage_facts",
    "classification_tasks",
    "ops_hourly_stats",
    "ops_daily_stats",
)

REQUIRED_DOMAIN_TABLES = INITIAL_DOMAIN_TABLES + OPERATIONS_DOMAIN_TABLES


@dataclass(frozen=True)
class Migration:
    version: str
    description: str
    statements: tuple[str, ...]


INITIAL_MIGRATION = Migration(
    version="0001_initial",
    description="Create the initial Growth attribution schema",
    statements=(
        """
        CREATE TABLE IF NOT EXISTS growth.sites (
            site_id TEXT PRIMARY KEY,
            site_name TEXT NOT NULL,
            system_type TEXT NOT NULL CHECK (system_type IN ('sub2api', 'newapi')),
            public_origin TEXT NOT NULL,
            default_landing_path TEXT NOT NULL DEFAULT '/',
            timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
            currency CHAR(3) NOT NULL DEFAULT 'CNY' CHECK (currency ~ '^[A-Z]{3}$'),
            binding_mode TEXT NOT NULL DEFAULT 'disabled'
                CHECK (binding_mode IN ('shared_parent_cookie', 'signed_handoff', 'disabled')),
            adapter_name TEXT NOT NULL DEFAULT '',
            adapter_version TEXT NOT NULL DEFAULT '',
            registration_capability TEXT NOT NULL DEFAULT 'pending'
                CHECK (registration_capability IN ('pending', 'available', 'unsupported', 'error')),
            usage_capability TEXT NOT NULL DEFAULT 'pending'
                CHECK (usage_capability IN ('pending', 'available', 'unsupported', 'error')),
            payment_capability TEXT NOT NULL DEFAULT 'pending'
                CHECK (payment_capability IN ('pending', 'available', 'unsupported', 'error')),
            refund_capability TEXT NOT NULL DEFAULT 'pending'
                CHECK (refund_capability IN ('pending', 'available', 'unsupported', 'error')),
            sync_interval_seconds INTEGER NOT NULL DEFAULT 300
                CHECK (sync_interval_seconds BETWEEN 60 AND 3600),
            initial_sync_from TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'disabled', 'archived')),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.channels (
            channel_id UUID PRIMARY KEY,
            code VARCHAR(40) NOT NULL CHECK (code ~ '^[a-z0-9-]+$'),
            name VARCHAR(100) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'disabled', 'archived')),
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (code)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.campaigns (
            campaign_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            channel_id UUID NOT NULL REFERENCES growth.channels(channel_id),
            code VARCHAR(60) NOT NULL CHECK (code ~ '^[a-z0-9-]+$'),
            name VARCHAR(160) NOT NULL,
            description TEXT NOT NULL DEFAULT '',
            starts_at TIMESTAMPTZ,
            ends_at TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'draft'
                CHECK (status IN ('draft', 'active', 'paused', 'archived')),
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (site_id, code),
            UNIQUE (campaign_id, site_id),
            CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at > starts_at)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.tracking_links (
            tracking_link_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL,
            campaign_id UUID NOT NULL,
            code CHAR(8) NOT NULL CHECK (code ~ '^[a-hj-km-np-z2-9]{8}$'),
            source_type TEXT NOT NULL
                CHECK (source_type IN ('post', 'group', 'referrer', 'profile', 'other')),
            source_name VARCHAR(240) NOT NULL,
            source_url TEXT NOT NULL DEFAULT '',
            audience_group VARCHAR(160) NOT NULL DEFAULT '',
            promoter VARCHAR(160) NOT NULL DEFAULT '',
            landing_path TEXT,
            extra_dimensions JSONB NOT NULL DEFAULT '{}'::JSONB
                CHECK (jsonb_typeof(extra_dimensions) = 'object'),
            valid_from TIMESTAMPTZ,
            valid_until TIMESTAMPTZ,
            status TEXT NOT NULL DEFAULT 'active'
                CHECK (status IN ('active', 'paused', 'archived')),
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (code),
            UNIQUE (tracking_link_id, site_id),
            FOREIGN KEY (campaign_id, site_id)
                REFERENCES growth.campaigns(campaign_id, site_id),
            CHECK (valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from),
            CHECK (landing_path IS NULL OR landing_path = '/' OR landing_path ~ '^/[^/]')
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.link_visits (
            visit_id UUID PRIMARY KEY,
            request_id UUID NOT NULL UNIQUE,
            tracking_link_id UUID NOT NULL,
            site_id TEXT NOT NULL,
            anonymous_visitor_key CHAR(64) NOT NULL,
            visited_at TIMESTAMPTZ NOT NULL,
            is_first_touch BOOLEAN NOT NULL DEFAULT FALSE,
            is_bot BOOLEAN NOT NULL DEFAULT FALSE,
            is_counted BOOLEAN NOT NULL DEFAULT TRUE,
            exclusion_reason TEXT NOT NULL DEFAULT '',
            referer_origin TEXT NOT NULL DEFAULT '',
            user_agent_family VARCHAR(80) NOT NULL DEFAULT '',
            device_type VARCHAR(24) NOT NULL DEFAULT '',
            country_code CHAR(2),
            ip_hash CHAR(64),
            redirect_result TEXT NOT NULL
                CHECK (redirect_result IN ('redirected', 'fallback_redirected', 'failed')),
            http_status SMALLINT NOT NULL CHECK (http_status BETWEEN 100 AND 599),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            FOREIGN KEY (tracking_link_id, site_id)
                REFERENCES growth.tracking_links(tracking_link_id, site_id)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.user_attributions (
            site_id TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            tracking_link_id UUID NOT NULL,
            anonymous_visitor_key CHAR(64) NOT NULL,
            first_visit_id UUID REFERENCES growth.link_visits(visit_id),
            source_registration_id TEXT,
            registered_at TIMESTAMPTZ NOT NULL,
            attributed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            attribution_method TEXT NOT NULL
                CHECK (attribution_method IN ('shared_cookie', 'signed_handoff', 'reconciled')),
            evidence_hash CHAR(64),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (site_id, external_user_id),
            UNIQUE (site_id, source_registration_id),
            FOREIGN KEY (tracking_link_id, site_id)
                REFERENCES growth.tracking_links(tracking_link_id, site_id)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.user_exclusions (
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            external_user_id TEXT NOT NULL,
            reason TEXT NOT NULL,
            source TEXT NOT NULL CHECK (source IN ('manual', 'site_tag', 'rule')),
            is_active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (site_id, external_user_id)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.user_usage_daily (
            site_id TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            usage_date_utc DATE NOT NULL,
            successful_call_count BIGINT NOT NULL DEFAULT 0 CHECK (successful_call_count >= 0),
            first_successful_call_at TIMESTAMPTZ,
            last_successful_call_at TIMESTAMPTZ,
            input_tokens BIGINT CHECK (input_tokens IS NULL OR input_tokens >= 0),
            output_tokens BIGINT CHECK (output_tokens IS NULL OR output_tokens >= 0),
            source_updated_at TIMESTAMPTZ,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (site_id, external_user_id, usage_date_utc),
            FOREIGN KEY (site_id, external_user_id)
                REFERENCES growth.user_attributions(site_id, external_user_id)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.billing_facts (
            billing_fact_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            fact_type TEXT NOT NULL CHECK (fact_type IN ('payment', 'refund')),
            source_fact_id TEXT NOT NULL,
            related_payment_id TEXT,
            amount_minor BIGINT NOT NULL CHECK (amount_minor > 0),
            currency CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            effective_status TEXT NOT NULL CHECK (effective_status IN ('settled', 'reversed')),
            occurred_at TIMESTAMPTZ NOT NULL,
            source_updated_at TIMESTAMPTZ,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (site_id, fact_type, source_fact_id),
            FOREIGN KEY (site_id, external_user_id)
                REFERENCES growth.user_attributions(site_id, external_user_id)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.user_facts (
            site_id TEXT NOT NULL,
            external_user_id TEXT NOT NULL,
            tracking_link_id UUID NOT NULL,
            account_label TEXT NOT NULL DEFAULT '',
            registered_at TIMESTAMPTZ NOT NULL,
            successful_call_count BIGINT NOT NULL DEFAULT 0 CHECK (successful_call_count >= 0),
            first_successful_call_at TIMESTAMPTZ,
            last_successful_call_at TIMESTAMPTZ,
            has_continued_call BOOLEAN NOT NULL DEFAULT FALSE,
            settled_payment_count INTEGER NOT NULL DEFAULT 0 CHECK (settled_payment_count >= 0),
            first_payment_at TIMESTAMPTZ,
            first_payment_amount_minor BIGINT,
            second_payment_at TIMESTAMPTZ,
            second_payment_amount_minor BIGINT,
            payment_total_minor BIGINT NOT NULL DEFAULT 0 CHECK (payment_total_minor >= 0),
            settled_refund_count INTEGER NOT NULL DEFAULT 0 CHECK (settled_refund_count >= 0),
            first_refund_at TIMESTAMPTZ,
            last_refund_at TIMESTAMPTZ,
            refund_total_minor BIGINT NOT NULL DEFAULT 0 CHECK (refund_total_minor >= 0),
            currency CHAR(3) NOT NULL CHECK (currency ~ '^[A-Z]{3}$'),
            is_excluded BOOLEAN NOT NULL DEFAULT FALSE,
            exclusion_reason TEXT NOT NULL DEFAULT '',
            source_data_fresh_at TIMESTAMPTZ,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (site_id, external_user_id),
            FOREIGN KEY (site_id, external_user_id)
                REFERENCES growth.user_attributions(site_id, external_user_id),
            FOREIGN KEY (tracking_link_id, site_id)
                REFERENCES growth.tracking_links(tracking_link_id, site_id)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.sync_cursors (
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            adapter_name TEXT NOT NULL,
            stream_name TEXT NOT NULL
                CHECK (stream_name IN ('registration', 'usage', 'billing', 'exclusion')),
            cursor_value JSONB NOT NULL DEFAULT '{}'::JSONB,
            watermark_at TIMESTAMPTZ,
            last_success_at TIMESTAMPTZ,
            last_run_id UUID,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (site_id, adapter_name, stream_name)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.sync_runs (
            run_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            adapter_name TEXT NOT NULL,
            stream_name TEXT NOT NULL
                CHECK (stream_name IN ('registration', 'usage', 'billing', 'exclusion')),
            trigger_type TEXT NOT NULL
                CHECK (trigger_type IN ('schedule', 'manual', 'backfill', 'reconcile')),
            status TEXT NOT NULL CHECK (status IN ('running', 'succeeded', 'failed', 'partial')),
            cursor_before JSONB NOT NULL DEFAULT '{}'::JSONB,
            cursor_after JSONB NOT NULL DEFAULT '{}'::JSONB,
            rows_scanned BIGINT NOT NULL DEFAULT 0 CHECK (rows_scanned >= 0),
            rows_upserted BIGINT NOT NULL DEFAULT 0 CHECK (rows_upserted >= 0),
            rows_rejected BIGINT NOT NULL DEFAULT 0 CHECK (rows_rejected >= 0),
            started_at TIMESTAMPTZ NOT NULL,
            finished_at TIMESTAMPTZ,
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS growth_sites_status_idx ON growth.sites (status)",
        "CREATE INDEX IF NOT EXISTS growth_sites_system_status_idx ON growth.sites (system_type, status)",
        "CREATE INDEX IF NOT EXISTS growth_channels_status_name_idx ON growth.channels (status, name)",
        "CREATE INDEX IF NOT EXISTS growth_campaigns_site_channel_status_idx ON growth.campaigns (site_id, channel_id, status)",
        "CREATE INDEX IF NOT EXISTS growth_tracking_links_site_status_created_idx ON growth.tracking_links (site_id, status, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_tracking_links_campaign_status_created_idx ON growth.tracking_links (campaign_id, status, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_link_visits_link_time_idx ON growth.link_visits (tracking_link_id, visited_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_link_visits_site_time_idx ON growth.link_visits (site_id, visited_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_link_visits_visitor_time_idx ON growth.link_visits (anonymous_visitor_key, visited_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_link_visits_time_brin_idx ON growth.link_visits USING BRIN (visited_at)",
        "CREATE INDEX IF NOT EXISTS growth_user_attributions_link_registered_idx ON growth.user_attributions (tracking_link_id, registered_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_user_attributions_site_registered_idx ON growth.user_attributions (site_id, registered_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_usage_site_date_idx ON growth.user_usage_daily (site_id, usage_date_utc)",
        "CREATE INDEX IF NOT EXISTS growth_usage_source_updated_idx ON growth.user_usage_daily (site_id, source_updated_at)",
        "CREATE INDEX IF NOT EXISTS growth_billing_user_time_idx ON growth.billing_facts (site_id, external_user_id, occurred_at)",
        "CREATE INDEX IF NOT EXISTS growth_billing_source_updated_idx ON growth.billing_facts (site_id, source_updated_at)",
        "CREATE INDEX IF NOT EXISTS growth_billing_status_time_idx ON growth.billing_facts (site_id, fact_type, effective_status, occurred_at)",
        "CREATE INDEX IF NOT EXISTS growth_user_facts_link_registered_idx ON growth.user_facts (tracking_link_id, is_excluded, registered_at)",
        "CREATE INDEX IF NOT EXISTS growth_user_facts_link_called_idx ON growth.user_facts (tracking_link_id, is_excluded, first_successful_call_at)",
        "CREATE INDEX IF NOT EXISTS growth_user_facts_link_paid_idx ON growth.user_facts (tracking_link_id, is_excluded, first_payment_at)",
        "CREATE INDEX IF NOT EXISTS growth_sync_runs_site_stream_started_idx ON growth.sync_runs (site_id, stream_name, started_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_sync_runs_status_started_idx ON growth.sync_runs (status, started_at DESC)",
    ),
)


OPERATIONS_MIGRATION = Migration(
    version="0002_operations_analytics",
    description="Create cached operations analytics and internal-user schema",
    statements=(
        "CREATE EXTENSION IF NOT EXISTS btree_gist",
        """
        CREATE TABLE IF NOT EXISTS growth.internal_users (
            internal_user_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            external_user_id TEXT NOT NULL,
            account_label TEXT NOT NULL DEFAULT '',
            reason TEXT NOT NULL DEFAULT '',
            active_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            active_until TIMESTAMPTZ,
            created_by TEXT NOT NULL,
            updated_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (site_id, external_user_id),
            UNIQUE (internal_user_id, site_id),
            CHECK (active_until IS NULL OR active_until > active_from)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.balance_conversion_rates (
            conversion_rate_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            balance_units_per_cny NUMERIC(30, 10) NOT NULL
                CHECK (balance_units_per_cny > 0),
            effective_from TIMESTAMPTZ NOT NULL,
            effective_until TIMESTAMPTZ,
            note TEXT NOT NULL DEFAULT '',
            created_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (site_id, effective_from),
            CHECK (effective_until IS NULL OR effective_until > effective_from),
            EXCLUDE USING gist (
                site_id WITH =,
                tstzrange(effective_from, effective_until, '[)') WITH &&
            )
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.ops_user_snapshots (
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            external_user_id TEXT NOT NULL,
            account_label TEXT NOT NULL DEFAULT '',
            registered_at TIMESTAMPTZ,
            account_status TEXT NOT NULL DEFAULT 'active',
            balance_units NUMERIC(30, 10),
            is_internal BOOLEAN NOT NULL DEFAULT FALSE,
            internal_user_id UUID,
            source_created_at TIMESTAMPTZ,
            source_updated_at TIMESTAMPTZ,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (site_id, external_user_id),
            FOREIGN KEY (internal_user_id, site_id)
                REFERENCES growth.internal_users(internal_user_id, site_id),
            CHECK (
                (is_internal AND internal_user_id IS NOT NULL)
                OR (NOT is_internal AND internal_user_id IS NULL)
            )
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.credit_events (
            credit_event_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            external_user_id TEXT NOT NULL,
            source_type TEXT NOT NULL
                CHECK (source_type IN ('payment', 'redemption', 'admin_adjustment', 'refund', 'other')),
            source_record_id TEXT NOT NULL,
            direction TEXT NOT NULL CHECK (direction IN ('credit', 'debit')),
            purpose TEXT
                CHECK (purpose IN ('sale', 'promotion', 'internal', 'compensation', 'other')),
            classification_status TEXT NOT NULL DEFAULT 'pending'
                CHECK (classification_status IN ('pending', 'classified')),
            balance_units NUMERIC(30, 10) NOT NULL CHECK (balance_units >= 0),
            cash_amount_cny NUMERIC(30, 10) NOT NULL DEFAULT 0
                CHECK (cash_amount_cny >= 0),
            conversion_rate_id UUID REFERENCES growth.balance_conversion_rates(conversion_rate_id),
            occurred_at TIMESTAMPTZ NOT NULL,
            source_updated_at TIMESTAMPTZ,
            source_metadata JSONB NOT NULL DEFAULT '{}'::JSONB
                CHECK (jsonb_typeof(source_metadata) = 'object'),
            synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (site_id, source_type, source_record_id),
            CHECK (
                (classification_status = 'pending' AND purpose IS NULL)
                OR (classification_status = 'classified' AND purpose IS NOT NULL)
            ),
            CHECK (purpose = 'sale' OR cash_amount_cny = 0)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.redemption_batches (
            redemption_batch_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            idempotency_key TEXT NOT NULL,
            purpose TEXT NOT NULL
                CHECK (purpose IN ('sale', 'promotion', 'internal', 'compensation', 'other')),
            code_count INTEGER NOT NULL CHECK (code_count > 0),
            balance_units_per_code NUMERIC(30, 10) NOT NULL
                CHECK (balance_units_per_code > 0),
            cash_amount_cny NUMERIC(30, 10) NOT NULL DEFAULT 0
                CHECK (cash_amount_cny >= 0),
            note TEXT NOT NULL DEFAULT '',
            command_status TEXT NOT NULL
                CHECK (command_status IN ('pending', 'succeeded', 'failed')),
            source_batch_id TEXT,
            code_hashes JSONB NOT NULL DEFAULT '[]'::JSONB
                CHECK (jsonb_typeof(code_hashes) = 'array'),
            code_masks JSONB NOT NULL DEFAULT '[]'::JSONB
                CHECK (jsonb_typeof(code_masks) = 'array'),
            requested_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            UNIQUE (site_id, idempotency_key),
            CHECK (purpose = 'sale' OR cash_amount_cny = 0),
            CHECK (purpose <> 'sale' OR cash_amount_cny > 0)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.balance_adjustment_requests (
            adjustment_request_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            external_user_id TEXT NOT NULL,
            idempotency_key TEXT NOT NULL,
            purpose TEXT NOT NULL
                CHECK (purpose IN ('sale', 'promotion', 'internal', 'compensation', 'other')),
            balance_units NUMERIC(30, 10) NOT NULL CHECK (balance_units <> 0),
            cash_amount_cny NUMERIC(30, 10) NOT NULL DEFAULT 0
                CHECK (cash_amount_cny >= 0),
            note TEXT NOT NULL DEFAULT '',
            command_status TEXT NOT NULL
                CHECK (command_status IN ('pending', 'succeeded', 'failed')),
            source_record_id TEXT,
            requested_by TEXT NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            completed_at TIMESTAMPTZ,
            error_code TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            UNIQUE (site_id, idempotency_key),
            CHECK (purpose = 'sale' OR cash_amount_cny = 0),
            CHECK (purpose <> 'sale' OR cash_amount_cny > 0)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.usage_facts (
            usage_fact_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            external_user_id TEXT NOT NULL,
            source_type TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            successful_call_count BIGINT NOT NULL DEFAULT 1
                CHECK (successful_call_count > 0),
            consumed_balance_units NUMERIC(30, 10) NOT NULL DEFAULT 0
                CHECK (consumed_balance_units >= 0),
            cost_cny NUMERIC(30, 10) NOT NULL DEFAULT 0 CHECK (cost_cny >= 0),
            conversion_rate_id UUID REFERENCES growth.balance_conversion_rates(conversion_rate_id),
            occurred_at TIMESTAMPTZ NOT NULL,
            source_updated_at TIMESTAMPTZ,
            synced_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            UNIQUE (site_id, source_type, source_record_id)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.classification_tasks (
            classification_task_id UUID PRIMARY KEY,
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            credit_event_id UUID NOT NULL UNIQUE REFERENCES growth.credit_events(credit_event_id),
            status TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending', 'resolved', 'ignored')),
            resolved_purpose TEXT
                CHECK (resolved_purpose IN ('sale', 'promotion', 'internal', 'compensation', 'other')),
            resolved_cash_amount_cny NUMERIC(30, 10)
                CHECK (resolved_cash_amount_cny IS NULL OR resolved_cash_amount_cny >= 0),
            note TEXT NOT NULL DEFAULT '',
            resolved_by TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ,
            CHECK (
                (status = 'pending' AND resolved_at IS NULL)
                OR (status <> 'pending' AND resolved_at IS NOT NULL)
            )
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.ops_hourly_stats (
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            bucket_start TIMESTAMPTZ NOT NULL,
            user_segment TEXT NOT NULL CHECK (user_segment IN ('ordinary', 'internal', 'all')),
            registered_user_count BIGINT NOT NULL DEFAULT 0 CHECK (registered_user_count >= 0),
            active_user_count BIGINT NOT NULL DEFAULT 0 CHECK (active_user_count >= 0),
            successful_call_count BIGINT NOT NULL DEFAULT 0 CHECK (successful_call_count >= 0),
            consumed_balance_units NUMERIC(30, 10) NOT NULL DEFAULT 0,
            cost_cny NUMERIC(30, 10) NOT NULL DEFAULT 0,
            payer_count BIGINT NOT NULL DEFAULT 0 CHECK (payer_count >= 0),
            sale_event_count BIGINT NOT NULL DEFAULT 0 CHECK (sale_event_count >= 0),
            gross_income_cny NUMERIC(30, 10) NOT NULL DEFAULT 0,
            refund_cny NUMERIC(30, 10) NOT NULL DEFAULT 0,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (site_id, bucket_start, user_segment)
        )
        """.strip(),
        """
        CREATE TABLE IF NOT EXISTS growth.ops_daily_stats (
            site_id TEXT NOT NULL REFERENCES growth.sites(site_id),
            bucket_date DATE NOT NULL,
            timezone TEXT NOT NULL DEFAULT 'Asia/Shanghai',
            user_segment TEXT NOT NULL CHECK (user_segment IN ('ordinary', 'internal', 'all')),
            registered_user_count BIGINT NOT NULL DEFAULT 0 CHECK (registered_user_count >= 0),
            active_user_count BIGINT NOT NULL DEFAULT 0 CHECK (active_user_count >= 0),
            successful_call_count BIGINT NOT NULL DEFAULT 0 CHECK (successful_call_count >= 0),
            consumed_balance_units NUMERIC(30, 10) NOT NULL DEFAULT 0,
            cost_cny NUMERIC(30, 10) NOT NULL DEFAULT 0,
            payer_count BIGINT NOT NULL DEFAULT 0 CHECK (payer_count >= 0),
            sale_event_count BIGINT NOT NULL DEFAULT 0 CHECK (sale_event_count >= 0),
            gross_income_cny NUMERIC(30, 10) NOT NULL DEFAULT 0,
            refund_cny NUMERIC(30, 10) NOT NULL DEFAULT 0,
            computed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (site_id, bucket_date, timezone, user_segment)
        )
        """.strip(),
        "CREATE INDEX IF NOT EXISTS growth_internal_users_site_active_idx ON growth.internal_users (site_id, active_from, active_until)",
        "CREATE INDEX IF NOT EXISTS growth_conversion_rates_site_time_idx ON growth.balance_conversion_rates (site_id, effective_from DESC)",
        "CREATE INDEX IF NOT EXISTS growth_ops_users_site_segment_registered_idx ON growth.ops_user_snapshots (site_id, is_internal, registered_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_credit_events_site_time_idx ON growth.credit_events (site_id, occurred_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_credit_events_user_time_idx ON growth.credit_events (site_id, external_user_id, occurred_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_credit_events_classification_idx ON growth.credit_events (site_id, classification_status, occurred_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_usage_facts_site_time_idx ON growth.usage_facts (site_id, occurred_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_usage_facts_user_time_idx ON growth.usage_facts (site_id, external_user_id, occurred_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_classification_tasks_status_idx ON growth.classification_tasks (site_id, status, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS growth_ops_hourly_stats_lookup_idx ON growth.ops_hourly_stats (bucket_start DESC, site_id, user_segment)",
        "CREATE INDEX IF NOT EXISTS growth_ops_daily_stats_lookup_idx ON growth.ops_daily_stats (bucket_date DESC, site_id, user_segment)",
    ),
)


MIGRATIONS = (INITIAL_MIGRATION, OPERATIONS_MIGRATION)


async def apply_pending_migrations(connection: Any) -> dict[str, Any]:
    await connection.execute(text("CREATE SCHEMA IF NOT EXISTS growth"))
    await connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS growth.schema_migrations (
                version TEXT PRIMARY KEY,
                description TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    await connection.execute(text("SELECT pg_advisory_xact_lock(hashtext('aiwelink-growth-migrations'))"))
    result = await connection.execute(text("SELECT version FROM growth.schema_migrations ORDER BY version"))
    applied = set(result.scalars().all())
    newly_applied: list[str] = []

    for migration in MIGRATIONS:
        if migration.version in applied:
            continue
        for statement in migration.statements:
            await connection.execute(text(statement))
        await connection.execute(
            text(
                """
                INSERT INTO growth.schema_migrations (version, description)
                VALUES (:version, :description)
                """
            ),
            {"version": migration.version, "description": migration.description},
        )
        applied.add(migration.version)
        newly_applied.append(migration.version)

    pending = [migration.version for migration in MIGRATIONS if migration.version not in applied]
    return {
        "initialized": not pending,
        "current_version": max(applied) if applied else None,
        "latest_version": MIGRATIONS[-1].version if MIGRATIONS else None,
        "applied_versions": newly_applied,
        "pending_versions": pending,
        "domain_table_count": len(REQUIRED_DOMAIN_TABLES),
    }


async def inspect_growth_schema(connection: Any) -> dict[str, Any]:
    ledger_result = await connection.execute(text("SELECT to_regclass('growth.schema_migrations')"))
    ledger_exists = ledger_result.scalar_one_or_none() is not None
    latest_version = MIGRATIONS[-1].version if MIGRATIONS else None
    if not ledger_exists:
        return {
            "initialized": False,
            "current_version": None,
            "latest_version": latest_version,
            "pending_versions": [migration.version for migration in MIGRATIONS],
            "domain_table_count": 0,
        }

    version_result = await connection.execute(
        text("SELECT version FROM growth.schema_migrations ORDER BY version")
    )
    applied = set(version_result.scalars().all())
    table_names = ", ".join(f"'{table_name}'" for table_name in REQUIRED_DOMAIN_TABLES)
    table_result = await connection.execute(
        text(
            f"""
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'growth'
              AND table_name IN ({table_names})
            """
        )
    )
    domain_table_count = int(table_result.scalar_one_or_none() or 0)
    pending = [migration.version for migration in MIGRATIONS if migration.version not in applied]
    return {
        "initialized": not pending and domain_table_count == len(REQUIRED_DOMAIN_TABLES),
        "current_version": max(applied) if applied else None,
        "latest_version": latest_version,
        "pending_versions": pending,
        "domain_table_count": domain_table_count,
    }


async def run_growth_migrations(engine: Any) -> dict[str, Any]:
    async with engine.begin() as connection:
        return await apply_pending_migrations(connection)


async def inspect_growth_database(engine: Any) -> dict[str, Any]:
    async with engine.connect() as connection:
        return await inspect_growth_schema(connection)
