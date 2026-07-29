import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it, vi } from "vitest";

import * as trafficOverviewModule from "./TrafficOverview";
import {
  TrafficOverview,
  TrafficOverviewView,
  buildTrafficAnalyticsQuery,
  type TrafficOverviewResponse,
  type TrafficUsersResponse,
} from "./TrafficOverview";


const overview: TrafficOverviewResponse = {
  generated_at: "2026-07-27T08:30:00Z",
  window: {
    range: "7d",
    start_at: "2026-07-20T08:30:00Z",
    end_at: "2026-07-27T08:30:00Z",
    bucket: "day",
    timezone: "Asia/Shanghai",
  },
  capabilities: {
    homepage_traffic: "available",
    link_traffic: "available",
    registration_attribution: "available",
    downstream_facts: "unavailable",
  },
  homepage_summary: {
    recorded_visits: 132,
    counted_pv: 120,
    session_uv: 80,
    excluded_visits: 12,
    valid_rate: 0.909091,
    latest_event_at: "2026-07-27T08:25:00Z",
  },
  link_summary: {
    recorded_visits: 55,
    counted_pv: 50,
    session_uv: 40,
    excluded_visits: 5,
    attribution_updates: 31,
    latest_event_at: "2026-07-27T08:20:00Z",
  },
  registration_summary: {
    attributed_accounts: 10,
    excluded_accounts: 1,
    facts_pending_accounts: 2,
  },
  traffic_trends: [{
    bucket_at: "2026-07-26T16:00:00Z",
    homepage_pv: 20,
    homepage_uv: 12,
    link_pv: 9,
    link_uv: 7,
  }],
  active_source_breakdown: [
    { source_kind: "promotion", counted_pv: 50, session_uv: 40 },
    { source_kind: "direct", counted_pv: 35, session_uv: 20 },
  ],
  classified_source_breakdown: [
    { source_kind: "direct", counted_pv: 70, session_uv: 55 },
    { source_kind: "organic_search", counted_pv: 30, session_uv: 24 },
  ],
  link_performance: [{
    tracking_link_id: "33333333-3333-3333-3333-333333333333",
    site_id: "aiwelink",
    code: "7km4q2xd",
    source_name: "Claude API 入门第 3 篇",
    status: "active",
    valid_from: "2026-07-20T00:00:00Z",
    valid_until: null,
    campaign_id: "22222222-2222-2222-2222-222222222222",
    campaign_name: "2026 夏季推广",
    channel_id: "11111111-1111-1111-1111-111111111111",
    channel_name: "小红书",
    recorded_visits: 55,
    counted_pv: 50,
    session_uv: 40,
    excluded_visits: 5,
    attribution_updates: 31,
    registered_accounts: 8,
  }],
  quality: {
    exclusion_reasons: [
      { event_scope: "homepage", reason: "bot", event_count: 8 },
      { event_scope: "link", reason: "unclassified", event_count: 2 },
    ],
    homepage_bot_visits: 8,
    link_bot_visits: 2,
    redirect_results: [{ redirect_result: "redirected", event_count: 50 }],
    http_statuses: [{ http_status: 302, event_count: 50 }],
    facts_pending_accounts: 2,
    latest_source_data_fresh_at: "2026-07-27T08:00:00Z",
    latest_computed_at: "2026-07-27T08:05:00Z",
    facts_delay_seconds: 300,
  },
};

const users: TrafficUsersResponse = {
  generated_at: "2026-07-27T08:30:00Z",
  total: 2,
  limit: 50,
  offset: 0,
  items: [
    {
      public_user_id: "usr_staff",
      site_id: "aiwelink",
      external_user_id: "s***@e***",
      account_label: "s***@e***",
      is_internal: false,
      source_kind: "promotion",
      tracking_link_id: "33333333-3333-3333-3333-333333333333",
      source_name: "Claude API 入门第 3 篇",
      campaign_id: "22222222-2222-2222-2222-222222222222",
      campaign_name: "2026 夏季推广",
      channel_id: "11111111-1111-1111-1111-111111111111",
      channel_name: "小红书",
      registered_at: "2026-07-27T06:00:00Z",
      attributed_at: "2026-07-27T06:00:02Z",
      attribution_method: "signed_handoff",
      fact_state: "normal",
      source_touch_at: "2026-07-27T05:50:00Z",
    },
    {
      public_user_id: "usr_pending",
      site_id: "aiwelink",
      external_user_id: "43",
      account_label: "用户 43",
      is_internal: false,
      source_kind: "direct",
      tracking_link_id: null,
      source_name: null,
      campaign_id: null,
      campaign_name: null,
      channel_id: null,
      channel_name: null,
      registered_at: "2026-07-27T07:00:00Z",
      attributed_at: "2026-07-27T07:00:03Z",
      attribution_method: "homepage_session",
      fact_state: "facts_pending",
      source_touch_at: "2026-07-27T06:55:00Z",
    },
  ],
};

const filters = {
  range: "7d" as const,
  segment: "ordinary" as const,
  siteId: "",
  sourceKind: "" as const,
  channelId: "",
  campaignId: "",
  trackingLinkId: "",
};

const metadata = {
  sites: [{ site_id: "aiwelink", site_name: "AIWeLink API" }],
  channels: [{ channel_id: "11111111-1111-1111-1111-111111111111", name: "小红书" }],
  campaigns: [{
    campaign_id: "22222222-2222-2222-2222-222222222222",
    site_id: "aiwelink",
    channel_id: "11111111-1111-1111-1111-111111111111",
    name: "2026 夏季推广",
  }],
  trackingLinks: [{
    tracking_link_id: "33333333-3333-3333-3333-333333333333",
    site_id: "aiwelink",
    campaign_id: "22222222-2222-2222-2222-222222222222",
    channel_id: "11111111-1111-1111-1111-111111111111",
    code: "7km4q2xd",
    source_name: "Claude API 入门第 3 篇",
  }],
};

function view(overrides: Record<string, unknown> = {}) {
  return (
    <TrafficOverviewView
      overview={overview}
      users={users}
      filters={filters}
      loading={false}
      usersLoading={false}
      error=""
      usersError=""
      onFiltersChange={() => undefined}
      onRetry={() => undefined}
      onUsersPage={() => undefined}
      {...metadata}
      {...overrides}
    />
  );
}

describe("traffic analytics overview", () => {
  it("builds deterministic overview and registration attribution queries", () => {
    const detailedFilters = {
      ...filters,
      range: "30d" as const,
      segment: "internal" as const,
      siteId: "aiwelink",
      sourceKind: "promotion" as const,
      channelId: metadata.channels[0].channel_id,
      campaignId: metadata.campaigns[0].campaign_id,
      trackingLinkId: metadata.trackingLinks[0].tracking_link_id,
    };

    expect(buildTrafficAnalyticsQuery(detailedFilters)).toBe(
      `?range=30d&segment=internal&site_id=aiwelink&source_kind=promotion&channel_id=${metadata.channels[0].channel_id}&campaign_id=${metadata.campaigns[0].campaign_id}&tracking_link_id=${metadata.trackingLinks[0].tracking_link_id}`,
    );
    expect(buildTrafficAnalyticsQuery(detailedFilters, { limit: 25, offset: 50 }))
      .toContain("&limit=25&offset=50");
    expect(buildTrafficAnalyticsQuery(detailedFilters, { limit: 25, offset: 50 }))
      .not.toContain("milestone");
  });

  it("renders effective traffic with subordinate audit context", () => {
    const html = renderToStaticMarkup(view());

    expect(html).toContain("有效主页 PV");
    expect(html).toContain(">120<");
    expect(html).toContain("有效 Session UV");
    expect(html).toContain(">80<");
    expect(html).toContain("记录访问 132");
    expect(html).toContain("排除 12");
    expect(html).toContain("<dt>有效率</dt><dd>90.9%</dd>");
    expect(html).toContain("匿名浏览器 Session");
  });

  it("separates active attribution from natural-entry diagnostics", () => {
    const html = renderToStaticMarkup(view());

    expect(html).toContain("有效归因来源");
    expect(html).toContain("按窗口内最后一次有效主页事件互斥归类");
    expect(html).toContain("自然入口诊断");
    expect(html).toContain("来源间不可相加");
    expect(html).toContain("自然搜索");
  });

  it("renders promotion performance, immutable registrations, and fact states", () => {
    const html = renderToStaticMarkup(view());

    expect(html).toContain("推广链接表现");
    expect(html).toContain("归因更新");
    expect(html).toContain("链接状态 / 有效期");
    expect(html).toContain("Claude API 入门第 3 篇");
    expect(html).toContain("注册归因");
    expect(html).toContain("来源在注册成功时锁定");
    expect(html).toContain("同步待确认");
    expect(html).toContain("事实正常");
    expect(html).toContain("来源触点");
    expect(html).not.toContain("首次付费");
    expect(html).not.toContain("首次调用");
  });

  it("marks downstream metrics unavailable instead of rendering zeros", () => {
    const html = renderToStaticMarkup(view());

    expect(html).toContain("调用、充值、二次充值、继续调用、退款");
    expect(html).toContain("未接入");
    expect(html).not.toContain("注册转化漏斗");
    expect(html).not.toContain("注册率");
  });

  it("renders data-quality diagnostics without exposing sensitive evidence", () => {
    const html = renderToStaticMarkup(view());

    expect(html).toContain("数据质量与新鲜度");
    expect(html).toContain("未分类");
    expect(html).toContain("机器人识别");
    expect(html).toContain("HTTP 302");
    expect(html).toContain("redirected");
    expect(html).not.toContain("session_key_hash");
    expect(html).not.toContain("evidence_hash");
  });

  it("renders null valid rates and missing freshness as dashes", () => {
    const html = renderToStaticMarkup(view({
      overview: {
        ...overview,
        homepage_summary: { ...overview.homepage_summary, valid_rate: null, latest_event_at: null },
        quality: {
          ...overview.quality,
          latest_source_data_fresh_at: null,
          latest_computed_at: null,
          facts_delay_seconds: null,
        },
      },
    }));

    expect(html).toContain("<dt>有效率</dt><dd>--</dd>");
    expect(html).toContain("<dt>数据最新</dt><dd>--</dd>");
  });

  it("makes every wide table a named keyboard-focusable region", () => {
    const html = renderToStaticMarkup(view());
    const labels = [
      "有效流量趋势表",
      "有效归因来源表",
      "自然入口诊断表",
      "推广链接表现表",
      "注册归因账号表",
    ];

    expect(html.match(/role="region"/g)).toHaveLength(labels.length);
    expect(html.match(/tabindex="0"/g)).toHaveLength(labels.length);
    labels.forEach((label) => expect(html).toContain(`aria-label="${label}"`));
  });

  it("uses the API timezone for report and account timestamps", () => {
    const OriginalDateTimeFormat = Intl.DateTimeFormat;
    const spy = vi.spyOn(Intl, "DateTimeFormat").mockImplementation(
      function DateTimeFormat(locales, options) {
        return new OriginalDateTimeFormat(locales, options);
      },
    );

    try {
      renderToStaticMarkup(view());
      expect(spy).toHaveBeenCalledWith(
        "zh-CN",
        expect.objectContaining({ timeZone: "Asia/Shanghai" }),
      );
    } finally {
      spy.mockRestore();
    }
  });

  it("renders loading, overview error, and isolated user error states", () => {
    const loadingHtml = renderToStaticMarkup(view({
      overview: null, users: null, loading: true, usersLoading: true,
    }));
    const errorHtml = renderToStaticMarkup(view({
      overview: null, users: null, error: "流量采集库未初始化",
    }));
    const usersErrorHtml = renderToStaticMarkup(view({ usersError: "注册归因暂不可用" }));

    expect(loadingHtml).toContain("正在加载流量概览");
    expect(errorHtml).toContain("流量采集库未初始化");
    expect(errorHtml).toContain("重新加载");
    expect(usersErrorHtml).toContain("注册归因暂不可用");
    expect(usersErrorHtml).not.toContain("用户 43");
  });

  it("starts with 7d ordinary filters and loading UI", () => {
    const html = renderToStaticMarkup(
      <TrafficOverview token="token" showToast={() => undefined} {...metadata} />,
    );

    expect(html).toContain("最近 7 天");
    expect(html).toContain("普通账号");
    expect(html).toContain("正在加载流量概览");
  });

  it("masks email-shaped fallback identifiers", () => {
    const formatter = (
      trafficOverviewModule as typeof trafficOverviewModule & {
        formatTrafficAccountIdentifier: (value: string | null | undefined) => string;
      }
    ).formatTrafficAccountIdentifier;

    expect(formatter("staff@example.com")).toBe("s***@e***");
    expect(formatter("用户 43")).toBe("用户 43");
  });
});
