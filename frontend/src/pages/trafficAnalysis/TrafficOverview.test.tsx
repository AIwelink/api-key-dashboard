import { Children, isValidElement, type ReactElement, type ReactNode } from "react";
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
  generated_at: "2026-07-25T08:30:00Z",
  window: {
    range: "7d",
    start_at: "2026-07-18T08:30:00Z",
    end_at: "2026-07-25T08:30:00Z",
    bucket: "day",
    timezone: "UTC",
  },
  summary: {
    homepage_pv: 120,
    homepage_uv: 80,
    link_pv: 50,
    link_uv: 40,
    registered_accounts: 10,
    called_accounts: 8,
    paid_accounts: 4,
    second_paid_accounts: 2,
    continued_accounts: 6,
    refunded_accounts: 1,
  },
  rates: {
    homepage_registration_rate: 0.125,
    link_registration_rate: 0.2,
    call_rate: 0.8,
    payment_rate: 0.4,
    second_payment_rate: 0.2,
    continued_rate: 0.6,
  },
  amounts: [{ currency: "CNY", payment_total_minor: 3500, refund_total_minor: 500 }],
  trends: [{
    bucket_at: "2026-07-24T00:00:00Z",
    homepage_pv: 20,
    homepage_uv: 12,
    link_pv: 9,
    link_uv: 7,
    registered_accounts: 3,
    called_accounts: 2,
    paid_accounts: 1,
  }],
  source_breakdown: [{
    source_kind: "promotion",
    entry_pv: 50,
    entry_uv: 40,
    registered_accounts: 8,
    called_accounts: 6,
    paid_accounts: 3,
  }],
  link_performance: [{
    tracking_link_id: "33333333-3333-3333-3333-333333333333",
    site_id: "aiwelink",
    code: "7km4q2xd",
    source_name: "Claude API 入门第 3 篇",
    campaign_id: "22222222-2222-2222-2222-222222222222",
    campaign_name: "2026 夏季推广",
    channel_id: "11111111-1111-1111-1111-111111111111",
    channel_name: "小红书",
    link_pv: 50,
    link_uv: 40,
    registered_accounts: 8,
    called_accounts: 6,
    paid_accounts: 3,
    second_paid_accounts: 2,
    continued_accounts: 5,
  }],
};

const users: TrafficUsersResponse = {
  generated_at: "2026-07-25T08:30:00Z",
  total: 2,
  limit: 50,
  offset: 0,
  items: [
    {
      public_user_id: "usr_staff",
      site_id: "aiwelink",
      external_user_id: "42",
      account_label: "staff@example.com",
      is_internal: false,
      source_kind: "promotion",
      tracking_link_id: "33333333-3333-3333-3333-333333333333",
      source_name: "Claude API 入门第 3 篇",
      campaign_id: "22222222-2222-2222-2222-222222222222",
      campaign_name: "2026 夏季推广",
      channel_id: "11111111-1111-1111-1111-111111111111",
      channel_name: "小红书",
      registered_at: "2026-07-24T10:00:00Z",
      first_successful_call_at: "2026-07-24T10:05:00Z",
      last_successful_call_at: "2026-07-25T07:00:00Z",
      first_payment_at: "2026-07-24T11:00:00Z",
      second_payment_at: "2026-07-25T06:00:00Z",
      first_refund_at: null,
      last_refund_at: null,
      has_continued_call: true,
    },
    {
      public_user_id: "usr_user_42",
      site_id: "aiwelink",
      external_user_id: "43",
      account_label: "用户 42",
      is_internal: false,
      source_kind: "direct",
      tracking_link_id: null,
      source_name: null,
      campaign_id: null,
      campaign_name: null,
      channel_id: null,
      channel_name: null,
      registered_at: "2026-07-24T12:00:00Z",
      first_successful_call_at: null,
      last_successful_call_at: null,
      first_payment_at: null,
      second_payment_at: null,
      first_refund_at: null,
      last_refund_at: null,
      has_continued_call: false,
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
      milestone="registered"
      loading={false}
      usersLoading={false}
      error=""
      usersError=""
      onFiltersChange={() => undefined}
      onSelectMilestone={() => undefined}
      onRetry={() => undefined}
      onUsersPage={() => undefined}
      {...metadata}
      {...overrides}
    />
  );
}

describe("traffic analytics overview", () => {
  it("builds deterministic overview and milestone queries", () => {
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
    expect(buildTrafficAnalyticsQuery(detailedFilters, {
      milestone: "paid",
      limit: 25,
      offset: 50,
    })).toContain("&milestone=paid&limit=25&offset=50");
  });

  it("decides whether a filter update resets nothing, users pagination, or all data", () => {
    const decide = (
      trafficOverviewModule as typeof trafficOverviewModule & {
        decideTrafficOverviewFilterUpdate: (
          current: typeof filters,
          next: typeof filters,
          usersOffset: number,
        ) => "none" | "users-page" | "filters";
      }
    ).decideTrafficOverviewFilterUpdate;

    expect(typeof decide).toBe("function");
    expect(decide(filters, filters, 0)).toBe("none");
    expect(decide(filters, filters, 50)).toBe("users-page");
    expect(decide(filters, { ...filters, range: "30d" }, 50)).toBe("filters");
  });

  it("renders traffic, funnel, last-touch sources, rankings, and account details", () => {
    const html = renderToStaticMarkup(view());

    expect(html).toContain("主页 PV（全站）");
    expect(html).toContain("推广链接 UV");
    expect(html).toContain("注册转化漏斗");
    expect(html).toContain("末次触发归因");
    expect(html).toContain("查看付费账号");
    expect(html).toContain("traffic-overview-funnel-stage");
    expect(html).toContain("注册账号名单");
    expect(html).toContain("普通用户");
    expect(html).toContain("来源构成");
    expect(html).toContain("推广链接表现");
    expect(html).toContain("Claude API 入门第 3 篇");
    expect(html).not.toContain("staff@example.com");
    expect(html).toContain("s***@e***");
    expect(html).toContain("用户 42");
    expect(html).toContain("退款时间");
    expect(html).toContain("CNY 35.00");
  });

  it("makes every wide data table a named keyboard-focusable region", () => {
    const html = renderToStaticMarkup(view());
    const regions = [
      ["traffic-overview-table-trends", "访问与转化趋势表"],
      ["traffic-overview-table-source", "来源构成表"],
      ["traffic-overview-table-links", "推广链接表现表"],
      ["traffic-overview-table-users", "里程碑账号名单"],
    ] as const;

    expect(html.match(/role="region"/g)).toHaveLength(4);
    expect(html.match(/tabindex="0"/g)).toHaveLength(4);
    expect(new Set(regions.map(([, label]) => label)).size).toBe(4);
    regions.forEach(([variant, label]) => {
      expect(html).toContain(`traffic-overview-table-scroll ${variant}`);
      expect(html).toContain(`aria-label="${label}"`);
    });
  });

  it("labels homepage metrics as current-filter values when source dimensions apply", () => {
    const html = renderToStaticMarkup(view({
      filters: { ...filters, sourceKind: "direct" },
    }));

    expect(html).toContain("主页 PV（当前筛选）");
    expect(html).toContain("符合当前筛选的主页访问");
    expect(html).not.toContain("主页 PV（全站）");
  });

  it("renders trend buckets in the timezone declared by the API", () => {
    const html = renderToStaticMarkup(view({
      overview: {
        ...overview,
        window: { ...overview.window, timezone: "America/Los_Angeles" },
        trends: [{ ...overview.trends[0], bucket_at: "2026-01-01T00:00:00Z" }],
      },
    }));

    expect(html).toContain("12/31");
    expect(html).toContain("America/Los_Angeles");
  });

  it("renders report and account timestamps in the timezone declared by the API", () => {
    const boundaryTimestamp = "2026-01-01T00:00:00Z";
    const html = renderToStaticMarkup(view({
      overview: {
        ...overview,
        generated_at: boundaryTimestamp,
        window: { ...overview.window, timezone: "America/Los_Angeles" },
      },
      users: {
        ...users,
        total: 1,
        items: [{
          ...users.items[0],
          registered_at: boundaryTimestamp,
          first_successful_call_at: boundaryTimestamp,
          first_payment_at: boundaryTimestamp,
          second_payment_at: boundaryTimestamp,
          last_successful_call_at: boundaryTimestamp,
          first_refund_at: boundaryTimestamp,
          last_refund_at: boundaryTimestamp,
        }],
      },
    }));

    expect(html).toMatch(/生成于[^<]*12\/31/);
    expect(html.match(/12\/31/g)).toHaveLength(7);
  });

  it("falls back to UTC when account data arrives before the overview timezone", () => {
    const OriginalDateTimeFormat = Intl.DateTimeFormat;
    const dateTimeFormatSpy = vi.spyOn(Intl, "DateTimeFormat").mockImplementation(
      function DateTimeFormat(locales, options) {
        return new OriginalDateTimeFormat(locales, options);
      },
    );

    try {
      const html = renderToStaticMarkup(view({
        overview: null,
        users: {
          ...users,
          total: 1,
          items: [{
            ...users.items[0],
            registered_at: "2026-01-01T20:00:00Z",
            first_successful_call_at: null,
            first_payment_at: null,
            second_payment_at: null,
            last_successful_call_at: null,
            first_refund_at: null,
            last_refund_at: null,
          }],
        },
      }));

      expect(html).toContain("注册账号名单");
      expect(dateTimeFormatSpy).toHaveBeenCalledWith(
        "zh-CN",
        expect.objectContaining({ timeZone: "UTC" }),
      );
    } finally {
      dateTimeFormatSpy.mockRestore();
    }
  });

  it("renders readable labels for every attribution source", () => {
    const html = renderToStaticMarkup(view({
      overview: {
        ...overview,
        source_breakdown: [
          overview.source_breakdown[0],
          { ...overview.source_breakdown[0], source_kind: "direct" },
          { ...overview.source_breakdown[0], source_kind: "organic_search" },
          { ...overview.source_breakdown[0], source_kind: "referral" },
        ],
      },
    }));

    expect(html).toContain("推广链接");
    expect(html).toContain("直接访问");
    expect(html).toContain("自然搜索");
    expect(html).toContain("引荐流量");
  });

  it("renders unavailable rates and timestamps as dashes", () => {
    const emptyOverview = {
      ...overview,
      rates: Object.fromEntries(Object.keys(overview.rates).map((key) => [key, null])),
      amounts: [],
      trends: [],
      source_breakdown: [],
      link_performance: [],
    } as TrafficOverviewResponse;
    const emptyUsers = {
      ...users,
      items: [{ ...users.items[0], first_payment_at: null, second_payment_at: null }],
    };
    const html = renderToStaticMarkup(view({ overview: emptyOverview, users: emptyUsers }));

    expect(html).toContain("--");
    expect(html).toContain("当前范围暂无趋势数据");
    expect(html).toContain("当前筛选下暂无推广链接表现");
  });

  it("reports the selected funnel milestone", () => {
    const onSelectMilestone = vi.fn();
    const tree = TrafficOverviewView({
      overview,
      users,
      filters,
      milestone: "registered",
      loading: false,
      usersLoading: false,
      error: "",
      usersError: "",
      onFiltersChange: () => undefined,
      onSelectMilestone,
      onRetry: () => undefined,
      onUsersPage: () => undefined,
      ...metadata,
    });
    const paidButton = findMilestoneButton(tree, "paid");

    (paidButton.props as { onClick: () => void }).onClick();

    expect(onSelectMilestone).toHaveBeenCalledWith("paid");
  });

  it("renders loading and overview error states without fixture data", () => {
    const loadingHtml = renderToStaticMarkup(view({
      overview: null,
      users: null,
      loading: true,
      usersLoading: true,
    }));
    const errorHtml = renderToStaticMarkup(view({
      overview: null,
      users: null,
      error: "流量采集库未初始化",
    }));

    expect(loadingHtml).toContain("正在加载流量概览");
    expect(errorHtml).toContain("流量采集库未初始化");
    expect(errorHtml).toContain("重新加载");
  });

  it("renders empty overview tables and an isolated account-list error", () => {
    const emptyOverview: TrafficOverviewResponse = {
      ...overview,
      summary: Object.fromEntries(
        Object.keys(overview.summary).map((key) => [key, 0]),
      ) as unknown as TrafficOverviewResponse["summary"],
      rates: Object.fromEntries(
        Object.keys(overview.rates).map((key) => [key, null]),
      ) as TrafficOverviewResponse["rates"],
      amounts: [],
      trends: [],
      source_breakdown: [],
      link_performance: [],
    };
    const emptyUsers: TrafficUsersResponse = { ...users, total: 0, items: [] };
    const html = renderToStaticMarkup(view({
      overview: emptyOverview,
      users: emptyUsers,
      usersError: "账号名单暂时不可用",
    }));

    expect(html).toContain("当前范围暂无趋势数据");
    expect(html).toContain("当前筛选下暂无来源数据");
    expect(html).toContain("当前筛选下暂无推广链接表现");
    expect(html).toContain("账号名单暂时不可用");
    expect(html).toContain("当前里程碑暂无账号");
  });

  it("starts the data-owning component with 7d ordinary filters and loading UI", () => {
    const html = renderToStaticMarkup(
      <TrafficOverview
        token="token"
        showToast={() => undefined}
        {...metadata}
      />,
    );

    expect(html).toContain("最近 7 天");
    expect(html).toContain("普通用户");
    expect(html).toContain("正在加载流量概览");
  });

  it("masks email-shaped account labels and fallback identifiers", () => {
    const formatter = (
      trafficOverviewModule as typeof trafficOverviewModule & {
        formatTrafficAccountIdentifier: (value: string | null | undefined) => string;
      }
    ).formatTrafficAccountIdentifier;

    expect(typeof formatter).toBe("function");
    expect(formatter("staff@example.com")).toBe("s***@e***");
    expect(formatter("用户 42")).toBe("用户 42");

    const html = renderToStaticMarkup(view({
      users: {
        ...users,
        total: 1,
        items: [{
          ...users.items[0],
          account_label: "",
          external_user_id: "operator@example.com",
        }],
      },
    }));

    expect(html).not.toContain("operator@example.com");
    expect(html).toContain("o***@e***");
  });

  it("keys account rows by stable public ids when masked identifiers collide", () => {
    const publicIds = ["usr_staff", "usr_susan"];
    const collidingUsers = {
      ...users,
      items: users.items.map((item, index) => ({
        ...item,
        public_user_id: publicIds[index],
        external_user_id: "s***@e***",
        account_label: "s***@e***",
      })),
    } as unknown as TrafficUsersResponse;
    const tree = TrafficOverviewView({
      overview,
      users: collidingUsers,
      filters,
      milestone: "registered",
      loading: false,
      usersLoading: false,
      error: "",
      usersError: "",
      onFiltersChange: () => undefined,
      onSelectMilestone: () => undefined,
      onRetry: () => undefined,
      onUsersPage: () => undefined,
      ...metadata,
    });

    const accountRowKeys = collectElementKeys(tree).filter((key) => key.startsWith("usr_"));
    expect(accountRowKeys).toEqual(publicIds);
    expect(new Set(accountRowKeys).size).toBe(2);
  });

  it("hides stale overview and account rows while replacement data loads", () => {
    const html = renderToStaticMarkup(view({ loading: true, usersLoading: true }));

    expect(html).toContain("正在加载流量概览");
    expect(html).toContain("正在加载账号");
    expect(html).not.toContain(">120<");
    expect(html).not.toContain("staff@example.com");
    expect(html).not.toContain("用户 42");
  });

  it("does not restore stale account rows after the replacement request fails", () => {
    const html = renderToStaticMarkup(view({
      usersLoading: false,
      usersError: "账号名单暂时不可用",
    }));

    expect(html).toContain("账号名单暂时不可用");
    expect(html).toContain("当前里程碑暂无账号");
    expect(html).not.toContain("staff@example.com");
    expect(html).not.toContain("用户 42");
  });
});

function findMilestoneButton(node: ReactNode, milestone: string): ReactElement {
  let result: ReactElement | null = null;
  Children.forEach(node, (child) => {
    if (result || !isValidElement(child)) return;
    const props = child.props as Record<string, unknown>;
    if (props["data-milestone"] === milestone) {
      result = child;
      return;
    }
    result = findMilestoneButtonOrNull(props.children as ReactNode, milestone);
  });
  if (!result) throw new Error(`Milestone button not found: ${milestone}`);
  return result;
}

function findMilestoneButtonOrNull(node: ReactNode, milestone: string): ReactElement | null {
  try {
    return findMilestoneButton(node, milestone);
  } catch {
    return null;
  }
}

function collectElementKeys(node: ReactNode): string[] {
  const keys: string[] = [];
  Children.forEach(node, (child) => {
    if (!isValidElement(child)) return;
    if (child.key != null) keys.push(String(child.key));
    keys.push(...collectElementKeys((child.props as { children?: ReactNode }).children));
  });
  return keys;
}
