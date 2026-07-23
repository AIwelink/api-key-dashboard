import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api/client";
import { GrowthCreateModal } from "../components/GrowthCreateModal";
import { errorMessage } from "../utils/format";


type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

export type GrowthStatus = "active" | "disabled" | "paused" | "draft" | "archived";
export type TrafficAnalysisTab = "links" | "channels" | "campaigns" | "sites";
export type GrowthCreateKind = "link" | "channel" | "campaign";
export type BindingMode = "shared_parent_cookie" | "signed_handoff" | "disabled";
export type TrackingSourceType = "post" | "group" | "referrer" | "profile" | "other";

export type GrowthSite = {
  site_id: string;
  site_name: string;
  system_type: string;
  base_url: string;
  database_configured: boolean;
  configured: boolean;
  public_origin?: string;
  default_landing_path?: string;
  timezone?: string;
  currency?: string;
  binding_mode?: BindingMode;
  sync_interval_seconds?: number;
  status?: GrowthStatus;
};

export type GrowthChannel = {
  channel_id: string;
  code: string;
  name: string;
  description: string;
  status: GrowthStatus;
};

export type GrowthCampaign = {
  campaign_id: string;
  site_id: string;
  channel_id: string;
  code: string;
  name: string;
  description: string;
  status: GrowthStatus;
  channel_name?: string;
  site_name?: string;
};

export type GrowthTrackingLink = {
  tracking_link_id: string;
  site_id: string;
  campaign_id: string;
  channel_id: string;
  code: string;
  public_url: string;
  source_type: TrackingSourceType;
  source_name: string;
  source_url: string;
  audience_group: string;
  promoter: string;
  landing_path: string | null;
  extra_dimensions: Record<string, string>;
  status: GrowthStatus;
  campaign_name?: string;
  channel_name?: string;
  site_name?: string;
};

export type TrackingLinkForm = {
  site_id: string;
  channel_id: string;
  campaign_id: string;
  source_type: TrackingSourceType;
  source_name: string;
  source_url: string;
  audience_group: string;
  promoter: string;
  landing_path: string;
  dimensions: Array<{ key: string; value: string }>;
};

export type ChannelForm = {
  code: string;
  name: string;
  description: string;
};

export type CampaignForm = {
  site_id: string;
  channel_id: string;
  code: string;
  name: string;
  description: string;
};

export type SiteForm = {
  public_origin: string;
  default_landing_path: string;
  timezone: string;
  currency: string;
  binding_mode: BindingMode;
  sync_interval_seconds: number;
  status: "active" | "disabled" | "archived";
};

type ListResponse<T> = {
  items: T[];
  total: number;
};

export type TrackingLinkFilters = {
  site_id: string;
  channel_id: string;
  campaign_id: string;
  status: "" | "active" | "paused" | "archived";
  keyword: string;
};

export type ChannelFilters = {
  status: "" | "active" | "disabled" | "archived";
  keyword: string;
};

export type CampaignFilters = {
  site_id: string;
  channel_id: string;
  status: "" | "draft" | "active" | "paused" | "archived";
  keyword: string;
};

export type MutationRefreshResult =
  | { status: "saved" }
  | { status: "saved_refresh_failed"; error: unknown }
  | { status: "mutation_failed"; error: unknown };

export const emptyTrackingLinkFilters: TrackingLinkFilters = {
  site_id: "",
  channel_id: "",
  campaign_id: "",
  status: "",
  keyword: "",
};

export const emptyChannelFilters: ChannelFilters = { status: "", keyword: "" };

export const emptyCampaignFilters: CampaignFilters = {
  site_id: "",
  channel_id: "",
  status: "",
  keyword: "",
};

export const emptyTrackingLinkForm: TrackingLinkForm = {
  site_id: "",
  channel_id: "",
  campaign_id: "",
  source_type: "post",
  source_name: "",
  source_url: "",
  audience_group: "",
  promoter: "",
  landing_path: "",
  dimensions: [
    { key: "", value: "" },
    { key: "", value: "" },
    { key: "", value: "" },
  ],
};

export const emptyChannelForm: ChannelForm = {
  code: "",
  name: "",
  description: "",
};

export const emptyCampaignForm: CampaignForm = {
  site_id: "",
  channel_id: "",
  code: "",
  name: "",
  description: "",
};

export const emptySiteForm: SiteForm = {
  public_origin: "",
  default_landing_path: "/",
  timezone: "Asia/Shanghai",
  currency: "CNY",
  binding_mode: "disabled",
  sync_interval_seconds: 300,
  status: "active",
};

export function selectTrackingLinkFormSite(
  form: TrackingLinkForm,
  siteId: string,
  sites: GrowthSite[],
  campaigns: GrowthCampaign[],
): TrackingLinkForm {
  const site = sites.find((item) => item.site_id === siteId);
  const firstCampaign = campaigns.find((item) => item.site_id === siteId);
  return {
    ...form,
    site_id: siteId,
    channel_id: firstCampaign?.channel_id || "",
    campaign_id: firstCampaign?.campaign_id || "",
    landing_path: site?.default_landing_path || "/",
  };
}

export function initializeGrowthCreateForms(
  selectedSiteId: string,
  sites: GrowthSite[],
  campaigns: GrowthCampaign[],
) {
  const linkForm = selectTrackingLinkFormSite(
    {
      ...emptyTrackingLinkForm,
      dimensions: emptyTrackingLinkForm.dimensions.map((dimension) => ({ ...dimension })),
    },
    selectedSiteId,
    sites,
    campaigns,
  );
  return {
    linkForm,
    channelForm: { ...emptyChannelForm },
    campaignForm: { ...emptyCampaignForm, site_id: selectedSiteId },
  };
}

const GROWTH_CODE_PATTERN = /^[a-z0-9-]+$/;

function isValidGrowthCode(value: string) {
  return GROWTH_CODE_PATTERN.test(value.trim().toLowerCase());
}

export function campaignCodeConflict(
  campaigns: GrowthCampaign[],
  siteId: string,
  code: string,
) {
  const normalizedCode = code.trim().toLowerCase();
  return Boolean(
    siteId
      && normalizedCode
      && campaigns.some((campaign) =>
        campaign.site_id === siteId && campaign.code.trim().toLowerCase() === normalizedCode),
  );
}

export function buildTrackingLinkPayload(form: TrackingLinkForm) {
  const extraDimensions = Object.fromEntries(
    form.dimensions
      .slice(0, 3)
      .map(({ key, value }) => [key.trim(), value.trim()] as const)
      .filter(([key, value]) => Boolean(key && value)),
  );
  return {
    site_id: form.site_id.trim(),
    campaign_id: form.campaign_id.trim(),
    source_type: form.source_type,
    source_name: form.source_name.trim(),
    source_url: form.source_url.trim(),
    audience_group: form.audience_group.trim(),
    promoter: form.promoter.trim(),
    landing_path: form.landing_path.trim(),
    extra_dimensions: extraDimensions,
    status: "active" as const,
  };
}

export function filterTrackingLinks(links: GrowthTrackingLink[], filters: TrackingLinkFilters) {
  const keyword = filters.keyword.trim().toLocaleLowerCase();
  return links.filter((link) => {
    if (filters.site_id && link.site_id !== filters.site_id) return false;
    if (filters.channel_id && link.channel_id !== filters.channel_id) return false;
    if (filters.campaign_id && link.campaign_id !== filters.campaign_id) return false;
    if (filters.status && link.status !== filters.status) return false;
    if (!keyword) return true;
    return [
      link.code,
      link.public_url,
      link.source_name,
      link.source_url,
      link.audience_group,
      link.promoter,
      link.campaign_name,
      link.channel_name,
      link.site_name,
    ].some((value) => String(value || "").toLocaleLowerCase().includes(keyword));
  });
}

export function filterChannels(items: GrowthChannel[], filters: ChannelFilters) {
  const keyword = filters.keyword.trim().toLocaleLowerCase();
  return items.filter((item) => {
    if (filters.status && item.status !== filters.status) return false;
    if (!keyword) return true;
    return [item.code, item.name, item.description]
      .some((value) => String(value || "").toLocaleLowerCase().includes(keyword));
  });
}

export function filterCampaigns(items: GrowthCampaign[], filters: CampaignFilters) {
  const keyword = filters.keyword.trim().toLocaleLowerCase();
  return items.filter((item) => {
    if (filters.site_id && item.site_id !== filters.site_id) return false;
    if (filters.channel_id && item.channel_id !== filters.channel_id) return false;
    if (filters.status && item.status !== filters.status) return false;
    if (!keyword) return true;
    return [item.code, item.name, item.description, item.site_name, item.channel_name]
      .some((value) => String(value || "").toLocaleLowerCase().includes(keyword));
  });
}

export async function runMutationAndRefresh(
  mutation: () => Promise<unknown>,
  refresh: () => Promise<unknown>,
): Promise<MutationRefreshResult> {
  try {
    await mutation();
  } catch (error) {
    return { status: "mutation_failed", error };
  }
  try {
    await refresh();
    return { status: "saved" };
  } catch (error) {
    return { status: "saved_refresh_failed", error };
  }
}

function siteToForm(site?: GrowthSite): SiteForm {
  return {
    public_origin: site?.public_origin || "",
    default_landing_path: site?.default_landing_path || "/",
    timezone: site?.timezone || "Asia/Shanghai",
    currency: site?.currency || "CNY",
    binding_mode: site?.binding_mode || "disabled",
    sync_interval_seconds: site?.sync_interval_seconds || 300,
    status: site?.status === "disabled" || site?.status === "archived" ? site.status : "active",
  };
}

async function requestWorkspaceData(token: string) {
  const [siteResult, channelResult, campaignResult, linkResult] = await Promise.all([
    api<ListResponse<GrowthSite>>("/growth/sites", token),
    api<ListResponse<GrowthChannel>>("/growth/channels", token),
    api<ListResponse<GrowthCampaign>>("/growth/campaigns", token),
    api<ListResponse<GrowthTrackingLink>>("/growth/tracking-links", token),
  ]);
  return {
    sites: siteResult.items,
    channels: channelResult.items,
    campaigns: campaignResult.items,
    trackingLinks: linkResult.items,
  };
}

export function TrafficAnalysisPage({ token, showToast }: Props) {
  const [activeTab, setActiveTab] = useState<TrafficAnalysisTab>("links");
  const [sites, setSites] = useState<GrowthSite[]>([]);
  const [channels, setChannels] = useState<GrowthChannel[]>([]);
  const [campaigns, setCampaigns] = useState<GrowthCampaign[]>([]);
  const [trackingLinks, setTrackingLinks] = useState<GrowthTrackingLink[]>([]);
  const [selectedSiteId, setSelectedSiteId] = useState("");
  const [linkForm, setLinkForm] = useState<TrackingLinkForm>(emptyTrackingLinkForm);
  const [channelForm, setChannelForm] = useState<ChannelForm>(emptyChannelForm);
  const [campaignForm, setCampaignForm] = useState<CampaignForm>(emptyCampaignForm);
  const [siteForm, setSiteForm] = useState<SiteForm>(emptySiteForm);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [saving, setSaving] = useState(false);
  const [createModal, setCreateModal] = useState<GrowthCreateKind | null>(null);

  const applyWorkspaceData = (data: Awaited<ReturnType<typeof requestWorkspaceData>>, preferredSiteId?: string) => {
    setSites(data.sites);
    setChannels(data.channels);
    setCampaigns(data.campaigns);
    setTrackingLinks(data.trackingLinks);
    const requestedSiteId = preferredSiteId || selectedSiteId;
    const siteId = data.sites.some((item) => item.site_id === requestedSiteId)
      ? requestedSiteId
      : data.sites[0]?.site_id || "";
    const selectedSite = data.sites.find((item) => item.site_id === siteId);
    const siteCampaigns = data.campaigns.filter((item) => item.site_id === siteId);
    setSelectedSiteId(siteId);
    setLinkForm((current) => {
      const currentCampaign = siteCampaigns.find((item) => item.campaign_id === current.campaign_id);
      const channelId = data.channels.some((item) => item.channel_id === current.channel_id)
        ? current.channel_id
        : currentCampaign?.channel_id || siteCampaigns[0]?.channel_id || "";
      const channelCampaigns = siteCampaigns.filter((item) => item.channel_id === channelId);
      const campaignId = currentCampaign?.channel_id === channelId
        ? currentCampaign.campaign_id
        : channelCampaigns[0]?.campaign_id || "";
      return {
        ...current,
        site_id: siteId,
        channel_id: channelId,
        campaign_id: campaignId,
        landing_path:
          current.site_id === siteId
            ? current.landing_path || selectedSite?.default_landing_path || "/"
            : selectedSite?.default_landing_path || "/",
      };
    });
    setCampaignForm((current) => ({
      ...current,
      site_id: siteId,
      channel_id: data.channels.some((item) => item.channel_id === current.channel_id) ? current.channel_id : "",
    }));
    setSiteForm(siteToForm(selectedSite));
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError("");
    requestWorkspaceData(token)
      .then((data) => {
        if (!cancelled) {
          applyWorkspaceData(data);
          setLoadError("");
        }
      })
      .catch((error) => {
        if (!cancelled) {
          const message = errorMessage(error);
          setLoadError(message);
          showToast(message, true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const refreshWorkspace = async (siteId = selectedSiteId) => {
    const data = await requestWorkspaceData(token);
    applyWorkspaceData(data, siteId);
  };

  const retryLoad = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const data = await requestWorkspaceData(token);
      applyWorkspaceData(data);
    } catch (error) {
      const message = errorMessage(error);
      setLoadError(message);
      showToast(message, true);
    } finally {
      setLoading(false);
    }
  };

  const runMutation = async (action: () => Promise<unknown>, successMessage: string) => {
    setSaving(true);
    try {
      const result = await runMutationAndRefresh(action, refreshWorkspace);
      if (result.status === "mutation_failed") {
        showToast(errorMessage(result.error), true);
        return false;
      }
      if (result.status === "saved_refresh_failed") {
        showToast(`${successMessage}，但列表刷新失败：${errorMessage(result.error)}`, true);
        return true;
      }
      showToast(successMessage);
      return true;
    } finally {
      setSaving(false);
    }
  };

  const selectSite = (siteId: string) => {
    const site = sites.find((item) => item.site_id === siteId);
    const firstCampaign = campaigns.find((item) => item.site_id === siteId);
    setSelectedSiteId(siteId);
    setLinkForm((current) => ({
      ...current,
      site_id: siteId,
      channel_id: firstCampaign?.channel_id || "",
      campaign_id: firstCampaign?.campaign_id || "",
      landing_path: site?.default_landing_path || current.landing_path || "/",
    }));
    setCampaignForm((current) => ({ ...current, site_id: siteId }));
    setSiteForm(siteToForm(site));
  };

  const selectLinkSite = (siteId: string) => {
    setLinkForm((current) => selectTrackingLinkFormSite(current, siteId, sites, campaigns));
  };

  const resetCreateForm = (kind: GrowthCreateKind | null) => {
    if (!kind) return;
    const forms = initializeGrowthCreateForms(selectedSiteId, sites, campaigns);
    if (kind === "link") {
      setLinkForm(forms.linkForm);
    } else if (kind === "channel") {
      setChannelForm(forms.channelForm);
    } else {
      setCampaignForm(forms.campaignForm);
    }
  };

  const openCreateModal = (kind: GrowthCreateKind) => {
    resetCreateForm(kind);
    setCreateModal(kind);
  };

  const closeCreateModal = () => {
    if (saving) return;
    resetCreateForm(createModal);
    setCreateModal(null);
  };

  const createLink = async () => {
    const created = await runMutation(
      () => api("/growth/tracking-links", token, { method: "POST", body: JSON.stringify(buildTrackingLinkPayload(linkForm)) }),
      "推广链接已创建",
    );
    if (created) {
      setLinkForm((current) => ({
        ...emptyTrackingLinkForm,
        site_id: current.site_id,
        channel_id: current.channel_id,
        campaign_id: current.campaign_id,
        landing_path: sites.find((site) => site.site_id === current.site_id)?.default_landing_path || "/",
      }));
      setCreateModal(null);
    }
  };

  const createChannel = async () => {
    const created = await runMutation(
      () => api("/growth/channels", token, {
        method: "POST",
        body: JSON.stringify({
          code: channelForm.code.trim().toLowerCase(),
          name: channelForm.name.trim(),
          description: channelForm.description.trim(),
          status: "active",
        }),
      }),
      "渠道已创建",
    );
    if (created) {
      setChannelForm(emptyChannelForm);
      setCreateModal(null);
    }
  };

  const createCampaign = async () => {
    if (!sites.find((site) => site.site_id === campaignForm.site_id)?.configured) {
      showToast("当前站点尚未接入流量分析，请先在站点接入页保存站点配置", true);
      return;
    }
    if (!isValidGrowthCode(campaignForm.code)) {
      showToast("活动编码仅支持小写英文字母、数字和连字符", true);
      return;
    }
    if (campaignCodeConflict(campaigns, campaignForm.site_id, campaignForm.code)) {
      showToast("当前站点下已存在相同活动编码", true);
      return;
    }
    const created = await runMutation(
      () => api("/growth/campaigns", token, {
        method: "POST",
        body: JSON.stringify({
          site_id: campaignForm.site_id.trim(),
          channel_id: campaignForm.channel_id,
          code: campaignForm.code.trim().toLowerCase(),
          name: campaignForm.name.trim(),
          description: campaignForm.description.trim(),
          status: "active",
        }),
      }),
      "活动已创建",
    );
    if (created) {
      setCampaignForm((current) => ({ ...emptyCampaignForm, site_id: current.site_id }));
      setCreateModal(null);
    }
  };

  const saveSite = () =>
    runMutation(
      () => api(`/growth/sites/${encodeURIComponent(selectedSiteId)}`, token, {
        method: "PUT",
        body: JSON.stringify({
          ...siteForm,
          public_origin: siteForm.public_origin.trim(),
          default_landing_path: siteForm.default_landing_path.trim(),
          timezone: siteForm.timezone.trim(),
          currency: siteForm.currency.trim().toUpperCase(),
        }),
      }),
      "站点接入配置已保存",
    );

  const toggleLink = (link: GrowthTrackingLink) => {
    if (link.status === "archived") return;
    return runMutation(
      () => api(`/growth/tracking-links/${link.tracking_link_id}`, token, {
        method: "PATCH",
        body: JSON.stringify({ status: link.status === "active" ? "paused" : "active" }),
      }),
      link.status === "active" ? "推广链接已停用" : "推广链接已启用",
    );
  };

  const copyLink = async (url: string) => {
    try {
      await navigator.clipboard.writeText(url);
      showToast("推广链接已复制");
    } catch {
      showToast("复制失败，请手动复制", true);
    }
  };

  return (
    <TrafficAnalysisWorkspace
      activeTab={activeTab}
      sites={sites}
      channels={channels}
      campaigns={campaigns}
      trackingLinks={trackingLinks}
      selectedSiteId={selectedSiteId}
      linkForm={linkForm}
      channelForm={channelForm}
      campaignForm={campaignForm}
      siteForm={siteForm}
      loading={loading}
      loadError={loadError}
      saving={saving}
      createModal={createModal}
      onTabChange={setActiveTab}
      onOpenCreate={openCreateModal}
      onCloseCreate={closeCreateModal}
      onLinkFormChange={setLinkForm}
      onChannelFormChange={setChannelForm}
      onCampaignFormChange={setCampaignForm}
      onSiteFormChange={setSiteForm}
      onCreateLink={createLink}
      onCreateChannel={createChannel}
      onCreateCampaign={createCampaign}
      onSaveSite={saveSite}
      onToggleLink={toggleLink}
      onSelectSite={selectSite}
      onSelectLinkSite={selectLinkSite}
      onCopyLink={copyLink}
      onRetry={retryLoad}
    />
  );
}

type WorkspaceProps = {
  activeTab: TrafficAnalysisTab;
  sites: GrowthSite[];
  channels: GrowthChannel[];
  campaigns: GrowthCampaign[];
  trackingLinks: GrowthTrackingLink[];
  selectedSiteId: string;
  linkForm: TrackingLinkForm;
  channelForm: ChannelForm;
  campaignForm: CampaignForm;
  siteForm: SiteForm;
  loading: boolean;
  saving: boolean;
  createModal: GrowthCreateKind | null;
  loadError?: string;
  onTabChange: (tab: TrafficAnalysisTab) => void;
  onOpenCreate: (kind: GrowthCreateKind) => void;
  onCloseCreate: () => void;
  onLinkFormChange: (form: TrackingLinkForm) => void;
  onChannelFormChange: (form: ChannelForm) => void;
  onCampaignFormChange: (form: CampaignForm) => void;
  onSiteFormChange: (form: SiteForm) => void;
  onCreateLink: () => void;
  onCreateChannel: () => void;
  onCreateCampaign: () => void;
  onSaveSite: () => void;
  onToggleLink: (link: GrowthTrackingLink) => void;
  onSelectSite: (siteId: string) => void;
  onSelectLinkSite: (siteId: string) => void;
  onCopyLink: (url: string) => void;
  onRetry?: () => void;
};

const sourceTypeLabels: Record<TrackingSourceType, string> = {
  post: "内容帖子",
  group: "社群",
  referrer: "引荐页面",
  profile: "个人主页",
  other: "其他",
};

const bindingModeLabels: Record<BindingMode, string> = {
  shared_parent_cookie: "共享主域 Cookie",
  signed_handoff: "签名交接",
  disabled: "不绑定用户",
};

function trackingStatusLabel(status: GrowthStatus) {
  if (status === "draft") return "草稿";
  if (status === "active") return "启用";
  if (status === "archived") return "归档";
  return "停用";
}

function submit(event: FormEvent, action: () => void) {
  event.preventDefault();
  action();
}

function SiteSelect({
  sites,
  value,
  disabled = false,
  onChange,
}: {
  sites: GrowthSite[];
  value: string;
  disabled?: boolean;
  onChange: (value: string) => void;
}) {
  return (
    <label>
      <span className="field-label"><strong>站点</strong></span>
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled} required>
        <option value="">选择站点</option>
        {sites.map((site) => <option value={site.site_id} key={site.site_id}>{site.site_name}</option>)}
      </select>
    </label>
  );
}

export function TrafficAnalysisWorkspace(props: WorkspaceProps) {
  const {
    activeTab, sites, channels, campaigns, trackingLinks, selectedSiteId,
    linkForm, channelForm, campaignForm, siteForm, loading, saving, createModal, loadError = "",
    onTabChange, onOpenCreate, onCloseCreate, onLinkFormChange, onChannelFormChange, onCampaignFormChange,
    onSiteFormChange, onCreateLink, onCreateChannel, onCreateCampaign,
    onSaveSite, onToggleLink, onSelectSite, onSelectLinkSite, onCopyLink, onRetry = () => undefined,
  } = props;
  const [linkFilters, setLinkFilters] = useState<TrackingLinkFilters>(emptyTrackingLinkFilters);
  const [channelFilters, setChannelFilters] = useState<ChannelFilters>(emptyChannelFilters);
  const [campaignFilters, setCampaignFilters] = useState<CampaignFilters>(emptyCampaignFilters);
  const siteCampaigns = campaigns.filter((item) => item.site_id === linkForm.site_id);
  const channelCampaigns = siteCampaigns.filter((item) => item.channel_id === linkForm.channel_id);
  const campaignSiteMissing = Boolean(campaignForm.site_id)
    && !sites.find((site) => site.site_id === campaignForm.site_id)?.configured;
  const campaignCodeDuplicate = campaignCodeConflict(campaigns, campaignForm.site_id, campaignForm.code);
  const campaignCodeInvalid = Boolean(campaignForm.code.trim())
    && (!isValidGrowthCode(campaignForm.code) || campaignCodeDuplicate);
  const selectedSite = sites.find((item) => item.site_id === selectedSiteId);
  const linkFilterCampaigns = campaigns.filter((item) =>
    (!linkFilters.site_id || item.site_id === linkFilters.site_id)
    && (!linkFilters.channel_id || item.channel_id === linkFilters.channel_id),
  );
  const visibleLinks = filterTrackingLinks(trackingLinks, linkFilters);
  const visibleChannels = filterChannels(channels, channelFilters);
  const visibleCampaigns = filterCampaigns(campaigns, campaignFilters);

  return (
    <section className="view accounts-page growth-workspace-page">
      <div className="topbar growth-workspace-head">
        <div>
          <h2>访问流量分析</h2>
          <p>配置可追踪的访问入口与站点归属</p>
        </div>
      </div>

      <div className="growth-workspace-tabs" role="tablist" aria-label="访问流量分析配置">
        {([['links', '推广链接'], ['channels', '渠道管理'], ['campaigns', '活动管理'], ['sites', '站点接入']] as const).map(([key, label]) => (
          <button
            className={activeTab === key ? "active" : ""}
            type="button"
            role="tab"
            aria-selected={activeTab === key}
            onClick={() => onTabChange(key)}
            key={key}
          >
            {label}
          </button>
        ))}
      </div>

      {loading ? (
        <div className="growth-workspace-empty">正在加载流量配置...</div>
      ) : loadError ? (
        <div className="panel growth-workspace-error" role="alert">
          <div>
            <strong>流量配置加载失败</strong>
            <span>{loadError}</span>
          </div>
          <button type="button" onClick={onRetry}>重新加载</button>
        </div>
      ) : activeTab === "links" ? (
        <div className="panel growth-list-page">
          <div className="growth-section-head">
            <div><strong>推广链接</strong><span>{visibleLinks.length} / {trackingLinks.length} 条</span></div>
            <button type="button" onClick={() => onOpenCreate("link")}>新建推广链接</button>
          </div>
          <div className="growth-query-grid" aria-label="推广链接查询">
              <label>
                <span>站点</span>
                <select
                  value={linkFilters.site_id}
                  onChange={(event) => setLinkFilters({ ...linkFilters, site_id: event.target.value, campaign_id: "" })}
                >
                  <option value="">全部站点</option>
                  {sites.map((site) => <option value={site.site_id} key={site.site_id}>{site.site_name}</option>)}
                </select>
              </label>
              <label>
                <span>渠道</span>
                <select
                  value={linkFilters.channel_id}
                  onChange={(event) => setLinkFilters({ ...linkFilters, channel_id: event.target.value, campaign_id: "" })}
                >
                  <option value="">全部渠道</option>
                  {channels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.name}</option>)}
                </select>
              </label>
              <label>
                <span>活动</span>
                <select value={linkFilters.campaign_id} onChange={(event) => setLinkFilters({ ...linkFilters, campaign_id: event.target.value })}>
                  <option value="">全部活动</option>
                  {linkFilterCampaigns.map((campaign) => <option value={campaign.campaign_id} key={campaign.campaign_id}>{campaign.name}</option>)}
                </select>
              </label>
              <label>
                <span>状态</span>
                <select value={linkFilters.status} onChange={(event) => setLinkFilters({ ...linkFilters, status: event.target.value as TrackingLinkFilters["status"] })}>
                  <option value="">全部状态</option>
                  <option value="active">启用</option>
                  <option value="paused">停用</option>
                  <option value="archived">归档</option>
                </select>
              </label>
              <label className="growth-link-filter-search">
                <span>关键词</span>
                <input value={linkFilters.keyword} onChange={(event) => setLinkFilters({ ...linkFilters, keyword: event.target.value })} placeholder="搜索来源、链接或推广人" />
              </label>
          </div>
          {visibleLinks.length ? (
            <div className="growth-link-rows">
              {visibleLinks.map((link) => (
                <article className="growth-link-row" key={link.tracking_link_id}>
                  <div className="growth-link-main">
                    <div><strong>{link.source_name}</strong><span className={`status-pill ${link.status}`}>{trackingStatusLabel(link.status)}</span></div>
                    <a href={link.public_url} target="_blank" rel="noreferrer">{link.public_url}</a>
                    <span>{link.channel_name || "未命名渠道"} · {link.campaign_name || "未命名活动"} · {sourceTypeLabels[link.source_type]}</span>
                  </div>
                  <div className="growth-row-actions">
                    <button className="ghost" type="button" onClick={() => onCopyLink(link.public_url)}>复制</button>
                    <button className="ghost" type="button" disabled={saving || link.status === "archived"} onClick={() => onToggleLink(link)}>{link.status === "archived" ? "已归档" : link.status === "active" ? "停用" : "启用"}</button>
                  </div>
                </article>
              ))}
            </div>
          ) : (
            <div className="growth-workspace-empty">{trackingLinks.length ? "没有符合筛选条件的记录" : "尚未创建推广链接"}</div>
          )}
        </div>
      ) : activeTab === "channels" ? (
        <div className="panel growth-list-page" data-growth-page="channels">
          <div className="growth-section-head">
            <div><strong>渠道列表</strong><span>{visibleChannels.length} / {channels.length} 个</span></div>
            <button type="button" onClick={() => onOpenCreate("channel")}>新建渠道</button>
          </div>
          <div className="growth-query-grid growth-query-grid-compact" aria-label="渠道查询">
            <label>
              <span>状态</span>
              <select value={channelFilters.status} onChange={(event) => setChannelFilters({ ...channelFilters, status: event.target.value as ChannelFilters["status"] })}>
                <option value="">全部状态</option><option value="active">启用</option><option value="disabled">停用</option><option value="archived">归档</option>
              </select>
            </label>
            <label><span>关键词</span><input value={channelFilters.keyword} onChange={(event) => setChannelFilters({ ...channelFilters, keyword: event.target.value })} placeholder="搜索渠道名称、编码或说明" /></label>
          </div>
          {visibleChannels.length ? (
            <div className="growth-source-rows">
              {visibleChannels.map((channel) => (
                <div className="growth-source-row" key={channel.channel_id}>
                  <strong>{channel.name}</strong><code>{channel.code}</code><span>{channel.description || "无说明"}</span><span className={`status-pill ${channel.status}`}>{trackingStatusLabel(channel.status)}</span>
                </div>
              ))}
            </div>
          ) : <div className="growth-workspace-empty">{channels.length ? "没有符合筛选条件的记录" : "尚未创建渠道"}</div>}
        </div>
      ) : activeTab === "campaigns" ? (
        <div className="panel growth-list-page" data-growth-page="campaigns">
          <div className="growth-section-head">
            <div><strong>活动列表</strong><span>{visibleCampaigns.length} / {campaigns.length} 个</span></div>
            <button type="button" onClick={() => onOpenCreate("campaign")}>新建活动</button>
          </div>
          <div className="growth-query-grid" aria-label="活动查询">
            <label><span>站点</span><select value={campaignFilters.site_id} onChange={(event) => setCampaignFilters({ ...campaignFilters, site_id: event.target.value })}><option value="">全部站点</option>{sites.map((site) => <option value={site.site_id} key={site.site_id}>{site.site_name}</option>)}</select></label>
            <label><span>渠道</span><select value={campaignFilters.channel_id} onChange={(event) => setCampaignFilters({ ...campaignFilters, channel_id: event.target.value })}><option value="">全部渠道</option>{channels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.name}</option>)}</select></label>
            <label>
              <span>状态</span>
              <select value={campaignFilters.status} onChange={(event) => setCampaignFilters({ ...campaignFilters, status: event.target.value as CampaignFilters["status"] })}>
                <option value="">全部状态</option><option value="draft">草稿</option><option value="active">启用</option><option value="paused">停用</option><option value="archived">归档</option>
              </select>
            </label>
            <label><span>关键词</span><input value={campaignFilters.keyword} onChange={(event) => setCampaignFilters({ ...campaignFilters, keyword: event.target.value })} placeholder="搜索活动名称、编码或说明" /></label>
          </div>
          {visibleCampaigns.length ? (
            <div className="growth-source-rows">
              {visibleCampaigns.map((campaign) => (
                <div className="growth-source-row" key={campaign.campaign_id}>
                  <strong>{campaign.name}</strong><code>{campaign.code}</code><span>{campaign.site_name || campaign.site_id} · {campaign.channel_name || channels.find((item) => item.channel_id === campaign.channel_id)?.name}</span><span className={`status-pill ${campaign.status}`}>{trackingStatusLabel(campaign.status)}</span>
                </div>
              ))}
            </div>
          ) : <div className="growth-workspace-empty">{campaigns.length ? "没有符合筛选条件的记录" : "尚未创建活动"}</div>}
        </div>
      ) : (
        <form className="panel growth-site-panel" onSubmit={(event) => submit(event, onSaveSite)}>
          <div className="growth-section-head">
            <div><strong>站点接入</strong><span>定义推广链接的公开域名、落地页与用户绑定方式</span></div>
            <button type="submit" disabled={saving || !selectedSiteId || !siteForm.public_origin.trim()}>{saving ? "保存中..." : "保存站点接入"}</button>
          </div>
          <div className="growth-site-context">
            <SiteSelect sites={sites} value={selectedSiteId} disabled={saving} onChange={onSelectSite} />
            {selectedSite && (
              <span className={selectedSite.configured ? "is-ready" : "is-pending"}>
                {selectedSite.configured
                  ? "流量分析站点已接入"
                  : "流量分析站点未接入，请保存下方配置"}
              </span>
            )}
          </div>
          <div className="growth-form-grid site-fields">
            <label className="span-2"><span className="field-label"><strong>公开访问域名</strong></span><input type="url" value={siteForm.public_origin} onChange={(event) => onSiteFormChange({ ...siteForm, public_origin: event.target.value })} placeholder="https://api.example.com" required /></label>
            <label><span className="field-label"><strong>默认落地路径</strong></span><input value={siteForm.default_landing_path} onChange={(event) => onSiteFormChange({ ...siteForm, default_landing_path: event.target.value })} placeholder="/register" required /></label>
            <label><span className="field-label"><strong>用户绑定方式</strong></span><select value={siteForm.binding_mode} onChange={(event) => onSiteFormChange({ ...siteForm, binding_mode: event.target.value as BindingMode })}>{Object.entries(bindingModeLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}</select></label>
            <label><span className="field-label"><strong>时区</strong></span><input value={siteForm.timezone} onChange={(event) => onSiteFormChange({ ...siteForm, timezone: event.target.value })} /></label>
            <label><span className="field-label"><strong>币种</strong></span><input maxLength={3} value={siteForm.currency} onChange={(event) => onSiteFormChange({ ...siteForm, currency: event.target.value })} /></label>
            <label><span className="field-label"><strong>同步间隔</strong><span>（秒）</span></span><input type="number" min={60} max={3600} step={60} value={siteForm.sync_interval_seconds} onChange={(event) => onSiteFormChange({ ...siteForm, sync_interval_seconds: Number(event.target.value) })} /></label>
            <label><span className="field-label"><strong>状态</strong></span><select value={siteForm.status} onChange={(event) => onSiteFormChange({ ...siteForm, status: event.target.value as SiteForm['status'] })}><option value="active">启用</option><option value="disabled">停用</option><option value="archived">归档</option></select></label>
          </div>
        </form>
      )}

      {createModal === "link" && (
        <GrowthCreateModal title="新建推广链接" submitLabel="创建链接" saving={saving} submitDisabled={!linkForm.site_id || !linkForm.campaign_id || !linkForm.source_name.trim()} onClose={onCloseCreate} onSubmit={onCreateLink}>
          <div className="growth-form-grid" data-growth-form="link">
            <SiteSelect sites={sites} value={linkForm.site_id} disabled={saving} onChange={onSelectLinkSite} />
            <label>
              <span className="field-label"><strong>渠道</strong></span>
              <select
                value={linkForm.channel_id}
                onChange={(event) => {
                  const campaign = siteCampaigns.find((item) => item.channel_id === event.target.value);
                  onLinkFormChange({ ...linkForm, channel_id: event.target.value, campaign_id: campaign?.campaign_id || "" });
                }}
                disabled={!linkForm.site_id}
                required
              >
                <option value="">选择渠道</option>
                {channels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.name}</option>)}
              </select>
            </label>
            <label>
              <span className="field-label"><strong>活动</strong></span>
              <select
                value={linkForm.campaign_id}
                onChange={(event) => onLinkFormChange({ ...linkForm, campaign_id: event.target.value })}
                disabled={!linkForm.site_id || !linkForm.channel_id || channelCampaigns.length === 0}
                required
              >
                <option value="">{linkForm.channel_id && channelCampaigns.length === 0 ? "暂无可选活动" : "选择活动"}</option>
                {channelCampaigns.map((campaign) => <option value={campaign.campaign_id} key={campaign.campaign_id}>{campaign.name}</option>)}
              </select>
              {linkForm.channel_id && channelCampaigns.length === 0 && <span className="growth-field-message">该渠道暂无活动，请先创建活动</span>}
            </label>
            <label>
              <span className="field-label"><strong>具体来源类型</strong></span>
              <select value={linkForm.source_type} onChange={(event) => onLinkFormChange({ ...linkForm, source_type: event.target.value as TrackingSourceType })}>
                {Object.entries(sourceTypeLabels).map(([value, label]) => <option value={value} key={value}>{label}</option>)}
              </select>
            </label>
            <label className="span-2"><span className="field-label"><strong>具体来源名称</strong></span><input value={linkForm.source_name} onChange={(event) => onLinkFormChange({ ...linkForm, source_name: event.target.value })} placeholder="例如：Claude API 入门第 3 篇" required /></label>
            <label className="span-2"><span className="field-label"><strong>来源 URL</strong><span>（可选）</span></span><input type="url" value={linkForm.source_url} onChange={(event) => onLinkFormChange({ ...linkForm, source_url: event.target.value })} placeholder="https://..." /></label>
            <label><span className="field-label"><strong>受众</strong><span>（可选）</span></span><input value={linkForm.audience_group} onChange={(event) => onLinkFormChange({ ...linkForm, audience_group: event.target.value })} placeholder="开发者" /></label>
            <label><span className="field-label"><strong>推广人</strong><span>（可选）</span></span><input value={linkForm.promoter} onChange={(event) => onLinkFormChange({ ...linkForm, promoter: event.target.value })} placeholder="运营人员或合作方" /></label>
            <label><span className="field-label"><strong>落地路径</strong></span><input value={linkForm.landing_path} onChange={(event) => onLinkFormChange({ ...linkForm, landing_path: event.target.value })} placeholder="/register" /></label>
          </div>
          <fieldset className="growth-dimensions">
            <legend>扩展维度 <span>最多 3 个字符串键值</span></legend>
            {linkForm.dimensions.slice(0, 3).map((dimension, index) => (
              <div key={index}>
                <input
                  aria-label={`扩展维度 ${index + 1} 名称`}
                  value={dimension.key}
                  onChange={(event) => {
                    const dimensions = linkForm.dimensions.map((item, itemIndex) => itemIndex === index ? { ...item, key: event.target.value } : item);
                    onLinkFormChange({ ...linkForm, dimensions });
                  }}
                  placeholder="字段名"
                />
                <input
                  aria-label={`扩展维度 ${index + 1} 值`}
                  value={dimension.value}
                  onChange={(event) => {
                    const dimensions = linkForm.dimensions.map((item, itemIndex) => itemIndex === index ? { ...item, value: event.target.value } : item);
                    onLinkFormChange({ ...linkForm, dimensions });
                  }}
                  placeholder="字符串值"
                />
              </div>
            ))}
          </fieldset>
        </GrowthCreateModal>
      )}

      {createModal === "channel" && (
        <GrowthCreateModal title="新建渠道" submitLabel="创建渠道" saving={saving} submitDisabled={!channelForm.code.trim() || !channelForm.name.trim()} onClose={onCloseCreate} onSubmit={onCreateChannel}>
          <div className="growth-form-grid compact" data-growth-form="channel">
            <label><span className="field-label"><strong>渠道编码</strong></span><input value={channelForm.code} onChange={(event) => onChannelFormChange({ ...channelForm, code: event.target.value })} placeholder="xiaohongshu" required /></label>
            <label><span className="field-label"><strong>渠道名称</strong></span><input value={channelForm.name} onChange={(event) => onChannelFormChange({ ...channelForm, name: event.target.value })} placeholder="小红书" required /></label>
            <label className="span-2"><span className="field-label"><strong>说明</strong><span>（可选）</span></span><input value={channelForm.description} onChange={(event) => onChannelFormChange({ ...channelForm, description: event.target.value })} /></label>
          </div>
        </GrowthCreateModal>
      )}

      {createModal === "campaign" && (
        <GrowthCreateModal title="新建活动" submitLabel="创建活动" saving={saving} submitDisabled={!campaignForm.site_id || campaignSiteMissing || !campaignForm.channel_id || !isValidGrowthCode(campaignForm.code) || campaignCodeDuplicate || !campaignForm.name.trim()} onClose={onCloseCreate} onSubmit={onCreateCampaign}>
          <div className="growth-form-grid compact" data-growth-form="campaign">
            <SiteSelect sites={sites} value={campaignForm.site_id} disabled={saving} onChange={(siteId) => onCampaignFormChange({ ...campaignForm, site_id: siteId })} />
            <label><span className="field-label"><strong>渠道</strong></span><select value={campaignForm.channel_id} onChange={(event) => onCampaignFormChange({ ...campaignForm, channel_id: event.target.value })} required><option value="">选择渠道</option>{channels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.name}</option>)}</select></label>
            {campaignSiteMissing && (
              <span className="growth-field-message is-error span-2" role="alert">
                当前站点尚未接入流量分析，请先在站点接入页保存站点配置
              </span>
            )}
            <label>
              <span className="field-label"><strong>活动编码</strong></span>
              <input
                value={campaignForm.code}
                onChange={(event) => onCampaignFormChange({ ...campaignForm, code: event.target.value.toLowerCase() })}
                placeholder="summer-2026"
                maxLength={60}
                pattern="[a-z0-9-]+"
                autoCapitalize="none"
                spellCheck={false}
                aria-invalid={campaignCodeInvalid}
                aria-describedby="campaign-code-help"
                required
              />
              <span id="campaign-code-help" className={`growth-field-message ${campaignCodeInvalid ? "is-error" : "is-muted"}`}>
                {campaignCodeDuplicate
                  ? "当前站点下已存在相同活动编码"
                  : campaignCodeInvalid
                    ? "仅支持小写英文字母、数字和连字符"
                    : "例如：summer-2026"}
              </span>
            </label>
            <label><span className="field-label"><strong>活动名称</strong></span><input value={campaignForm.name} onChange={(event) => onCampaignFormChange({ ...campaignForm, name: event.target.value })} placeholder="2026 夏季推广" required /></label>
            <label className="span-2"><span className="field-label"><strong>说明</strong><span>（可选）</span></span><input value={campaignForm.description} onChange={(event) => onCampaignFormChange({ ...campaignForm, description: event.target.value })} /></label>
          </div>
        </GrowthCreateModal>
      )}
    </section>
  );
}

export default TrafficAnalysisPage;
