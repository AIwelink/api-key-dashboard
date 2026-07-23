import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  TrafficAnalysisWorkspace,
  buildTrackingLinkPayload,
  emptyCampaignForm,
  emptyChannelForm,
  emptySiteForm,
  emptyTrackingLinkForm,
  filterCampaigns,
  filterChannels,
  filterTrackingLinks,
  runMutationAndRefresh,
  type GrowthCampaign,
  type GrowthChannel,
  type GrowthSite,
  type GrowthTrackingLink,
  type TrackingLinkForm,
} from "./TrafficAnalysisPage";


const site: GrowthSite = {
  site_id: "aiwelink",
  site_name: "AIWeLink API",
  system_type: "sub2api",
  base_url: "https://api.aiwelink.cc",
  database_configured: true,
  configured: true,
  public_origin: "https://api.aiwelink.cc",
  default_landing_path: "/register",
  timezone: "Asia/Shanghai",
  currency: "CNY",
  binding_mode: "shared_parent_cookie",
  sync_interval_seconds: 300,
  status: "active",
};

const channel: GrowthChannel = {
  channel_id: "11111111-1111-1111-1111-111111111111",
  code: "xiaohongshu",
  name: "小红书",
  description: "",
  status: "active",
};

const campaign: GrowthCampaign = {
  campaign_id: "22222222-2222-2222-2222-222222222222",
  site_id: "aiwelink",
  channel_id: channel.channel_id,
  code: "summer-2026",
  name: "2026 夏季推广",
  description: "",
  status: "active",
  channel_name: "小红书",
  site_name: "AIWeLink API",
};

const trackingLink: GrowthTrackingLink = {
  tracking_link_id: "33333333-3333-3333-3333-333333333333",
  site_id: "aiwelink",
  campaign_id: campaign.campaign_id,
  channel_id: channel.channel_id,
  code: "7km4q2xd",
  public_url: "https://aiwelink.cc/r/7km4q2xd",
  source_type: "post",
  source_name: "Claude API 入门第 3 篇",
  source_url: "https://xiaohongshu.example/post/3",
  audience_group: "开发者",
  promoter: "运营 A",
  landing_path: "/register",
  extra_dimensions: {},
  status: "active",
  campaign_name: campaign.name,
  channel_name: channel.name,
  site_name: site.site_name,
};

const callbacks = {
  onTabChange: () => undefined,
  onLinkFormChange: () => undefined,
  onChannelFormChange: () => undefined,
  onCampaignFormChange: () => undefined,
  onSiteFormChange: () => undefined,
  onCreateLink: () => undefined,
  onCreateChannel: () => undefined,
  onCreateCampaign: () => undefined,
  onSaveSite: () => undefined,
  onToggleLink: () => undefined,
  onSelectSite: () => undefined,
  onCopyLink: () => undefined,
};

function renderWorkspace(
  activeTab: "links" | "sources" | "sites",
  loadError = "",
  campaignForm = { ...emptyCampaignForm, site_id: "aiwelink", channel_id: channel.channel_id },
) {
  return renderToStaticMarkup(
    <TrafficAnalysisWorkspace
      activeTab={activeTab}
      sites={[site]}
      channels={[channel]}
      campaigns={[campaign]}
      trackingLinks={[trackingLink]}
      selectedSiteId="aiwelink"
      linkForm={{
        ...emptyTrackingLinkForm,
        site_id: "aiwelink",
        channel_id: channel.channel_id,
        campaign_id: campaign.campaign_id,
      }}
      channelForm={emptyChannelForm}
      campaignForm={campaignForm}
      siteForm={{ ...emptySiteForm, public_origin: "https://api.aiwelink.cc" }}
      loading={false}
      saving={false}
      loadError={loadError}
      onRetry={() => undefined}
      {...callbacks}
    />,
  );
}

describe("traffic analysis configuration workspace", () => {
  it("renders real promotion-link configuration without fake analytics", () => {
    const html = renderWorkspace("links");

    expect(html).toContain('class="growth-stacked-flow"');
    expect(html).not.toContain("growth-links-layout");
    expect(html).toContain("推广链接");
    expect(html).toContain("渠道与活动");
    expect(html).toContain("站点接入");
    expect(html).toContain("具体来源");
    expect(html).toContain("https://aiwelink.cc/r/7km4q2xd");
    expect(html).toContain("Claude API 入门第 3 篇");
    expect(html).not.toContain("点击人数");
    expect(html).not.toContain("注册率");
  });

  it("renders channel and campaign configuration together", () => {
    const html = renderWorkspace("sources");
    const channelForm = html.indexOf('data-growth-section="channel-form"');
    const channelList = html.indexOf('data-growth-section="channel-list"');
    const campaignForm = html.indexOf('data-growth-section="campaign-form"');
    const campaignList = html.indexOf('data-growth-section="campaign-list"');

    expect(channelForm).toBeGreaterThan(-1);
    expect(channelList).toBeGreaterThan(channelForm);
    expect(campaignForm).toBeGreaterThan(channelList);
    expect(campaignList).toBeGreaterThan(campaignForm);
    expect(html).toContain("新建渠道");
    expect(html).toContain("新建活动");
    expect(html).toContain("小红书");
    expect(html).toContain("2026 夏季推广");
  });

  it("blocks an invalid campaign code and explains the accepted format", () => {
    const html = renderWorkspace("sources", "", {
      ...emptyCampaignForm,
      site_id: site.site_id,
      channel_id: channel.channel_id,
      code: "活动编码 test",
      name: "测试活动",
    });

    expect(html).toContain('aria-invalid="true"');
    expect(html).toContain("仅支持小写英文字母、数字和连字符");
    expect(html).toContain('<button type="submit" disabled="">创建活动</button>');
  });

  it("renders site integration fields and binding mode", () => {
    const html = renderWorkspace("sites");

    expect(html).toContain("AIWeLink API");
    expect(html).toContain("公开访问域名");
    expect(html).toContain("共享主域 Cookie");
    expect(html).toContain("同步间隔");
  });

  it("builds a trimmed tracking-link payload with at most three non-empty dimensions", () => {
    const form: TrackingLinkForm = {
      ...emptyTrackingLinkForm,
      site_id: " aiwelink ",
      channel_id: channel.channel_id,
      campaign_id: campaign.campaign_id,
      source_type: "post",
      source_name: " 帖子 A ",
      source_url: " https://example.com/post/a ",
      audience_group: " 开发者 ",
      promoter: " 运营 A ",
      landing_path: " /register ",
      dimensions: [
        { key: "region", value: " cn " },
        { key: "", value: "ignored" },
        { key: "topic", value: " api " },
      ],
    };

    expect(buildTrackingLinkPayload(form)).toEqual({
      site_id: "aiwelink",
      campaign_id: campaign.campaign_id,
      source_type: "post",
      source_name: "帖子 A",
      source_url: "https://example.com/post/a",
      audience_group: "开发者",
      promoter: "运营 A",
      landing_path: "/register",
      extra_dimensions: { region: "cn", topic: "api" },
      status: "active",
    });
  });

  it("keeps a channel without campaigns selectable when creating a tracking link", () => {
    const newChannel: GrowthChannel = {
      channel_id: "44444444-4444-4444-4444-444444444444",
      code: "telegram",
      name: "Telegram",
      description: "",
      status: "active",
    };
    const linkForm = {
      ...emptyTrackingLinkForm,
      site_id: site.site_id,
      channel_id: newChannel.channel_id,
      campaign_id: "",
      source_name: "Telegram 群推广",
    };

    const html = renderToStaticMarkup(
      <TrafficAnalysisWorkspace
        activeTab="links"
        sites={[site]}
        channels={[channel, newChannel]}
        campaigns={[campaign]}
        trackingLinks={[]}
        selectedSiteId={site.site_id}
        linkForm={linkForm}
        channelForm={emptyChannelForm}
        campaignForm={{ ...emptyCampaignForm, site_id: site.site_id }}
        siteForm={emptySiteForm}
        loading={false}
        saving={false}
        {...callbacks}
      />,
    );

    expect(html).toContain(`<option value="${newChannel.channel_id}" selected="">${newChannel.name}</option>`);
    expect(html).toContain("该渠道暂无活动，请先创建活动");
    expect(buildTrackingLinkPayload(linkForm)).not.toHaveProperty("channel_id");
  });

  it("keeps initial load failures visible and offers a retry action", () => {
    const html = renderWorkspace("links", "Growth database is unavailable");

    expect(html).toContain("流量配置加载失败");
    expect(html).toContain("Growth database is unavailable");
    expect(html).toContain("重新加载");
    expect(html).not.toContain("当前站点还没有推广链接");
  });

  it("filters tracking links by site, channel, campaign, status, and keyword together", () => {
    const candidates: GrowthTrackingLink[] = [
      trackingLink,
      { ...trackingLink, tracking_link_id: "site-mismatch", site_id: "other-site" },
      { ...trackingLink, tracking_link_id: "channel-mismatch", channel_id: "other-channel" },
      { ...trackingLink, tracking_link_id: "campaign-mismatch", campaign_id: "other-campaign" },
      { ...trackingLink, tracking_link_id: "status-mismatch", status: "paused" },
      { ...trackingLink, tracking_link_id: "keyword-mismatch", source_name: "另一篇内容", public_url: "https://aiwelink.cc/r/other" },
    ];

    expect(filterTrackingLinks(candidates, {
      site_id: site.site_id,
      channel_id: channel.channel_id,
      campaign_id: campaign.campaign_id,
      status: "active",
      keyword: "Claude API",
    })).toEqual([trackingLink]);
  });

  it("filters channels by status and keyword", () => {
    expect(filterChannels([
      channel,
      { ...channel, channel_id: "disabled-xiaohongshu", status: "disabled" },
      { ...channel, channel_id: "active-telegram", code: "telegram", name: "Telegram", status: "active" },
    ], {
      status: "active",
      keyword: "小红书",
    })).toEqual([channel]);
  });

  it("filters campaigns by site, channel, status, and keyword", () => {
    expect(filterCampaigns([
      campaign,
      { ...campaign, campaign_id: "other-site", site_id: "other" },
      { ...campaign, campaign_id: "other-channel", channel_id: "other" },
      { ...campaign, campaign_id: "paused", status: "paused" },
      { ...campaign, campaign_id: "other-name", name: "其他活动", code: "other" },
    ], {
      site_id: site.site_id,
      channel_id: channel.channel_id,
      status: "active",
      keyword: "summer",
    })).toEqual([campaign]);
  });

  it("keeps a successful mutation successful when the following refresh fails", async () => {
    const result = await runMutationAndRefresh(
      async () => ({ tracking_link_id: "created" }),
      async () => { throw new Error("refresh failed"); },
    );

    expect(result.status).toBe("saved_refresh_failed");
  });

  it("does not refresh after a failed mutation", async () => {
    let refreshCalls = 0;
    const result = await runMutationAndRefresh(
      async () => { throw new Error("mutation failed"); },
      async () => { refreshCalls += 1; },
    );

    expect(result.status).toBe("mutation_failed");
    expect(refreshCalls).toBe(0);
  });
});
