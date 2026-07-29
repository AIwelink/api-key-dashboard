# Growth Query Modal Workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将访问流量分析改为“推广链接 / 渠道管理 / 活动管理 / 站点接入”四个页签，并让前三页以查询列表为主、通过居中大弹窗创建记录。

**Architecture:** `TrafficAnalysisPage` 继续拥有业务数据、表单和保存状态，并新增受控的创建弹窗状态；`TrafficAnalysisWorkspace` 负责四个查询列表页面和表单内容；新的 `GrowthCreateModal` 只负责通用模态行为、焦点、Escape、遮罩和固定头尾。所有查询继续在前端针对已加载数据执行，不改变 Growth API。

**Tech Stack:** React 19、TypeScript 5.9、Vitest、现有全局 CSS、Vite。

---

## File Map

- Create: `frontend/src/components/GrowthCreateModal.tsx`
  - Growth 创建弹窗的语义结构、焦点进入/返回、Escape、背景滚动锁定和关闭规则。
- Create: `frontend/src/components/GrowthCreateModal.test.tsx`
  - 锁定弹窗的 dialog 语义、标题、表单和保存禁用状态。
- Modify: `frontend/src/pages/TrafficAnalysisPage.tsx`
  - 四页签类型、创建弹窗状态、渠道/活动筛选、三个查询列表页面、三个弹窗表单和创建成功关闭逻辑。
- Modify: `frontend/src/pages/TrafficAnalysisPage.test.tsx`
  - 锁定四页签、渠道/活动独立页面、默认不显示创建表单、弹窗内容和筛选函数。
- Modify: `frontend/styles.css`
  - 查询列表布局、75vw 模态布局、固定头尾、内容滚动和移动端单列适配。
- Reference: `docs/superpowers/specs/2026-07-23-growth-query-modal-workspace-design.md`
  - 已确认的功能、交互、响应式和范围边界。

### Task 1: Add the reusable Growth creation modal

**Files:**
- Create: `frontend/src/components/GrowthCreateModal.test.tsx`
- Create: `frontend/src/components/GrowthCreateModal.tsx`
- Modify: `frontend/styles.css`

- [ ] **Step 1: Write the failing modal markup test**

Create `frontend/src/components/GrowthCreateModal.test.tsx`:

```tsx
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { GrowthCreateModal } from "./GrowthCreateModal";

describe("GrowthCreateModal", () => {
  it("renders an accessible creation form with disabled close controls while saving", () => {
    const html = renderToStaticMarkup(
      <GrowthCreateModal
        title="新建活动"
        submitLabel="创建活动"
        saving
        submitDisabled
        onClose={() => undefined}
        onSubmit={() => undefined}
      >
        <label>活动名称<input name="name" /></label>
      </GrowthCreateModal>,
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('aria-labelledby="growth-create-modal-title"');
    expect(html).toContain("新建活动");
    expect(html).toContain("保存中...");
    expect(html).toContain('aria-label="关闭"');
    expect(html).toContain('<button class="ghost icon-button" type="button" disabled="" aria-label="关闭">');
  });
});
```

- [ ] **Step 2: Run the test and verify RED**

Run:

```powershell
cd frontend
npm.cmd test -- --run src/components/GrowthCreateModal.test.tsx
```

Expected: FAIL because `./GrowthCreateModal` does not exist.

- [ ] **Step 3: Implement the modal component**

Create `frontend/src/components/GrowthCreateModal.tsx`:

```tsx
import { useEffect, useRef, type FormEvent, type ReactNode } from "react";

type Props = {
  title: string;
  submitLabel: string;
  saving: boolean;
  submitDisabled: boolean;
  onClose: () => void;
  onSubmit: () => void;
  children: ReactNode;
};

export function GrowthCreateModal({
  title,
  submitLabel,
  saving,
  submitDisabled,
  onClose,
  onSubmit,
  children,
}: Props) {
  const dialogRef = useRef<HTMLElement>(null);
  const returnFocusRef = useRef<HTMLElement | null>(null);
  const closeRef = useRef(onClose);
  const savingRef = useRef(saving);
  closeRef.current = onClose;
  savingRef.current = saving;

  useEffect(() => {
    returnFocusRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    dialogRef.current?.querySelector<HTMLElement>("input, select, textarea, button")?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !savingRef.current) {
        closeRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [href]',
      ) || []);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      window.removeEventListener("keydown", onKeyDown);
      document.body.style.overflow = previousOverflow;
      returnFocusRef.current?.focus();
    };
  }, []);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!saving && !submitDisabled) onSubmit();
  };

  return (
    <div className="growth-modal-backdrop" role="presentation" onMouseDown={() => { if (!saving) onClose(); }}>
      <section
        ref={dialogRef}
        className="growth-create-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="growth-create-modal-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <form className="growth-modal-form" onSubmit={submit}>
          <header className="growth-modal-header">
            <h3 id="growth-create-modal-title">{title}</h3>
            <button className="ghost icon-button" type="button" disabled={saving} aria-label="关闭" onClick={onClose}>×</button>
          </header>
          <div className="growth-modal-body">{children}</div>
          <footer className="growth-modal-footer">
            <button className="ghost" type="button" disabled={saving} onClick={onClose}>取消</button>
            <button type="submit" disabled={saving || submitDisabled}>{saving ? "保存中..." : submitLabel}</button>
          </footer>
        </form>
      </section>
    </div>
  );
}
```

- [ ] **Step 4: Add modal CSS using the existing design system**

Add near the Growth workspace styles in `frontend/styles.css`:

```css
.growth-modal-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1200;
  display: grid;
  place-items: center;
  padding: 24px;
  background: rgba(22, 27, 34, 0.42);
}

.growth-create-modal {
  width: min(75vw, 1440px);
  max-height: 85vh;
  overflow: hidden;
  border: 1px solid var(--line);
  border-radius: 8px;
  background: var(--panel);
  box-shadow: 0 20px 60px rgba(15, 23, 42, 0.24);
}

.growth-modal-form {
  display: grid;
  grid-template-rows: auto minmax(0, 1fr) auto;
  max-height: 85vh;
}

.growth-modal-header,
.growth-modal-footer {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 10px;
  padding: 14px 16px;
}

.growth-modal-header {
  border-bottom: 1px solid var(--line);
}

.growth-modal-header h3 {
  margin: 0;
  font-size: 15px;
  letter-spacing: 0;
}

.growth-modal-body {
  min-height: 0;
  overflow-y: auto;
  padding: 16px;
}

.growth-modal-footer {
  justify-content: flex-end;
  border-top: 1px solid var(--line);
}

@media (max-width: 760px) {
  .growth-modal-backdrop {
    padding: 12px;
  }

  .growth-create-modal {
    width: 100%;
    max-height: calc(100vh - 24px);
  }

  .growth-modal-form {
    max-height: calc(100vh - 24px);
  }
}
```

- [ ] **Step 5: Run the modal test and verify GREEN**

Run:

```powershell
cd frontend
npm.cmd test -- --run src/components/GrowthCreateModal.test.tsx
```

Expected: 1 test passes.

- [ ] **Step 6: Commit the modal unit**

```powershell
git add frontend/src/components/GrowthCreateModal.tsx frontend/src/components/GrowthCreateModal.test.tsx frontend/styles.css
git commit -m "Add Growth creation modal"
```

### Task 2: Add channel and campaign query filters

**Files:**
- Modify: `frontend/src/pages/TrafficAnalysisPage.test.tsx`
- Modify: `frontend/src/pages/TrafficAnalysisPage.tsx`

- [ ] **Step 1: Write failing filter tests**

Update the imports in `frontend/src/pages/TrafficAnalysisPage.test.tsx` to include `filterChannels` and `filterCampaigns`, then add:

```tsx
it("filters channels by status and keyword", () => {
  expect(filterChannels([
    channel,
    { ...channel, channel_id: "telegram", code: "telegram", name: "Telegram", status: "disabled" },
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
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
cd frontend
npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx
```

Expected: FAIL because `filterChannels` and `filterCampaigns` are not exported.

- [ ] **Step 3: Implement filter types, defaults, and functions**

Add to `frontend/src/pages/TrafficAnalysisPage.tsx` after `TrackingLinkFilters`:

```tsx
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

export const emptyChannelFilters: ChannelFilters = { status: "", keyword: "" };

export const emptyCampaignFilters: CampaignFilters = {
  site_id: "",
  channel_id: "",
  status: "",
  keyword: "",
};

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
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run:

```powershell
cd frontend
npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx
```

Expected: all TrafficAnalysisPage tests pass.

- [ ] **Step 5: Commit the query filter unit**

```powershell
git add frontend/src/pages/TrafficAnalysisPage.tsx frontend/src/pages/TrafficAnalysisPage.test.tsx
git commit -m "Add Growth channel and campaign filters"
```

### Task 3: Split the workspace into four query-first pages and wire modals

**Files:**
- Modify: `frontend/src/pages/TrafficAnalysisPage.test.tsx`
- Modify: `frontend/src/pages/TrafficAnalysisPage.tsx`

- [ ] **Step 1: Replace the combined-page tests with failing four-tab tests**

Change the `renderWorkspace` helper's `activeTab` type to `"links" | "channels" | "campaigns" | "sites"`. Add `createModal={null}`, `onOpenCreate={() => undefined}`, and `onCloseCreate={() => undefined}` to its props.

Replace the old “renders channel and campaign configuration together” test with:

```tsx
it("renders four independent query-first tabs", () => {
  const linksHtml = renderWorkspace("links");
  const channelsHtml = renderWorkspace("channels");
  const campaignsHtml = renderWorkspace("campaigns");

  expect(linksHtml).toContain("推广链接");
  expect(linksHtml).toContain("渠道管理");
  expect(linksHtml).toContain("活动管理");
  expect(linksHtml).toContain("站点接入");
  expect(linksHtml).toContain("新建推广链接");
  expect(linksHtml).not.toContain('data-growth-form="link"');

  expect(channelsHtml).toContain('data-growth-page="channels"');
  expect(channelsHtml).toContain("渠道列表");
  expect(channelsHtml).toContain("新建渠道");
  expect(channelsHtml).not.toContain("活动列表");
  expect(channelsHtml).not.toContain('data-growth-form="channel"');

  expect(campaignsHtml).toContain('data-growth-page="campaigns"');
  expect(campaignsHtml).toContain("活动列表");
  expect(campaignsHtml).toContain("新建活动");
  expect(campaignsHtml).not.toContain("渠道列表");
  expect(campaignsHtml).not.toContain('data-growth-form="campaign"');
});
```

Add a helper argument for `createModal`, then add:

```tsx
it("renders each creation form only inside its selected modal", () => {
  const channelModal = renderWorkspace("channels", "", undefined, "channel");
  const campaignModal = renderWorkspace("campaigns", "", undefined, "campaign");
  const linkModal = renderWorkspace("links", "", undefined, "link");

  expect(channelModal).toContain('role="dialog"');
  expect(channelModal).toContain('data-growth-form="channel"');
  expect(channelModal).not.toContain('data-growth-form="campaign"');

  expect(campaignModal).toContain('role="dialog"');
  expect(campaignModal).toContain('data-growth-form="campaign"');
  expect(campaignModal).not.toContain('data-growth-form="channel"');

  expect(linkModal).toContain('role="dialog"');
  expect(linkModal).toContain('data-growth-form="link"');
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run:

```powershell
cd frontend
npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx
```

Expected: FAIL because the tab type still contains `sources`, modal props do not exist, and forms are inline.

- [ ] **Step 3: Add controlled modal state and success behavior to the page container**

In `frontend/src/pages/TrafficAnalysisPage.tsx`:

```tsx
export type TrafficAnalysisTab = "links" | "channels" | "campaigns" | "sites";
export type GrowthCreateKind = "link" | "channel" | "campaign";
```

Add container state:

```tsx
const [createModal, setCreateModal] = useState<GrowthCreateKind | null>(null);
```

Add open/reset behavior:

```tsx
const openCreateModal = (kind: GrowthCreateKind) => {
  if (kind === "link") {
    const siteId = linkForm.site_id || selectedSiteId;
    const site = sites.find((item) => item.site_id === siteId);
    const currentCampaign = campaigns.find((item) => item.campaign_id === linkForm.campaign_id && item.site_id === siteId);
    setLinkForm({
      ...emptyTrackingLinkForm,
      site_id: siteId,
      channel_id: currentCampaign?.channel_id || linkForm.channel_id,
      campaign_id: currentCampaign?.campaign_id || "",
      landing_path: site?.default_landing_path || "/",
    });
  } else if (kind === "channel") {
    setChannelForm(emptyChannelForm);
  } else {
    setCampaignForm({ ...emptyCampaignForm, site_id: campaignForm.site_id || selectedSiteId });
  }
  setCreateModal(kind);
};

const closeCreateModal = () => {
  if (!saving) setCreateModal(null);
};
```

Replace the three create functions with the following versions so failure keeps the modal open and success resets the form and closes it:

```tsx
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
  if (!isValidGrowthCode(campaignForm.code)) {
    showToast("活动编码仅支持小写英文字母、数字和连字符", true);
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
```

Pass these props to `TrafficAnalysisWorkspace`:

```tsx
createModal={createModal}
onOpenCreate={openCreateModal}
onCloseCreate={closeCreateModal}
```

Add matching fields to `WorkspaceProps`:

```tsx
createModal: GrowthCreateKind | null;
onOpenCreate: (kind: GrowthCreateKind) => void;
onCloseCreate: () => void;
```

- [ ] **Step 4: Render four tabs and three query-first list pages**

Import the modal:

```tsx
import { GrowthCreateModal } from "../components/GrowthCreateModal";
```

Add workspace filter state and filtered collections:

```tsx
const [channelFilters, setChannelFilters] = useState<ChannelFilters>(emptyChannelFilters);
const [campaignFilters, setCampaignFilters] = useState<CampaignFilters>(emptyCampaignFilters);
const visibleChannels = filterChannels(channels, channelFilters);
const visibleCampaigns = filterCampaigns(campaigns, campaignFilters);
```

Rename the existing local array used by the promotion-link campaign select so it does not shadow the exported filter function:

```tsx
const linkFilterCampaigns = campaigns.filter((item) =>
  (!linkFilters.site_id || item.site_id === linkFilters.site_id)
  && (!linkFilters.channel_id || item.channel_id === linkFilters.channel_id),
);
```

Render tab definitions exactly as:

```tsx
([['links', '推广链接'], ['channels', '渠道管理'], ['campaigns', '活动管理'], ['sites', '站点接入']] as const)
```

For the links page, remove the inline editor form and render this complete query-list block:

```tsx
<div className="panel growth-list-page">
  <div className="growth-section-head">
    <div><strong>推广链接</strong><span>{visibleLinks.length} / {trackingLinks.length} 条</span></div>
    <button type="button" onClick={() => onOpenCreate("link")}>新建推广链接</button>
  </div>
  <div className="growth-query-grid" aria-label="推广链接查询">
    <label>
      <span>站点</span>
      <select value={linkFilters.site_id} onChange={(event) => setLinkFilters({ ...linkFilters, site_id: event.target.value, campaign_id: "" })}>
        <option value="">全部站点</option>
        {sites.map((site) => <option value={site.site_id} key={site.site_id}>{site.site_name}</option>)}
      </select>
    </label>
    <label>
      <span>渠道</span>
      <select value={linkFilters.channel_id} onChange={(event) => setLinkFilters({ ...linkFilters, channel_id: event.target.value, campaign_id: "" })}>
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
        <option value="">全部状态</option><option value="active">启用</option><option value="paused">停用</option><option value="archived">归档</option>
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
  ) : <div className="growth-workspace-empty">{trackingLinks.length ? "没有符合筛选条件的推广链接" : "尚未创建推广链接"}</div>}
</div>
```

For the channel page, render only:

```tsx
<div className="panel growth-list-page" data-growth-page="channels">
  <div className="growth-section-head">
    <div><strong>渠道列表</strong><span>{visibleChannels.length} / {channels.length} 个</span></div>
    <button type="button" onClick={() => onOpenCreate("channel")}>新建渠道</button>
  </div>
  <div className="growth-query-grid growth-query-grid-compact" aria-label="渠道查询">
    <label><span>状态</span><select value={channelFilters.status} onChange={(event) => setChannelFilters({ ...channelFilters, status: event.target.value as ChannelFilters["status"] })}><option value="">全部状态</option><option value="active">启用</option><option value="disabled">停用</option><option value="archived">归档</option></select></label>
    <label><span>关键词</span><input value={channelFilters.keyword} onChange={(event) => setChannelFilters({ ...channelFilters, keyword: event.target.value })} placeholder="搜索渠道名称、编码或说明" /></label>
  </div>
  {visibleChannels.length ? (
    <div className="growth-source-rows">{visibleChannels.map((channel) => <div className="growth-source-row" key={channel.channel_id}><strong>{channel.name}</strong><code>{channel.code}</code><span>{channel.description || "无说明"}</span><span className={`status-pill ${channel.status}`}>{trackingStatusLabel(channel.status)}</span></div>)}</div>
  ) : <div className="growth-workspace-empty">{channels.length ? "没有符合筛选条件的渠道" : "尚未创建渠道"}</div>}
</div>
```

For the campaign page, render only:

```tsx
<div className="panel growth-list-page" data-growth-page="campaigns">
  <div className="growth-section-head">
    <div><strong>活动列表</strong><span>{visibleCampaigns.length} / {campaigns.length} 个</span></div>
    <button type="button" onClick={() => onOpenCreate("campaign")}>新建活动</button>
  </div>
  <div className="growth-query-grid" aria-label="活动查询">
    <label><span>站点</span><select value={campaignFilters.site_id} onChange={(event) => setCampaignFilters({ ...campaignFilters, site_id: event.target.value })}><option value="">全部站点</option>{sites.map((site) => <option value={site.site_id} key={site.site_id}>{site.site_name}</option>)}</select></label>
    <label><span>渠道</span><select value={campaignFilters.channel_id} onChange={(event) => setCampaignFilters({ ...campaignFilters, channel_id: event.target.value })}><option value="">全部渠道</option>{channels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.name}</option>)}</select></label>
    <label><span>状态</span><select value={campaignFilters.status} onChange={(event) => setCampaignFilters({ ...campaignFilters, status: event.target.value as CampaignFilters["status"] })}><option value="">全部状态</option><option value="draft">草稿</option><option value="active">启用</option><option value="paused">停用</option><option value="archived">归档</option></select></label>
    <label><span>关键词</span><input value={campaignFilters.keyword} onChange={(event) => setCampaignFilters({ ...campaignFilters, keyword: event.target.value })} placeholder="搜索活动名称、编码或说明" /></label>
  </div>
  {visibleCampaigns.length ? (
    <div className="growth-source-rows">{visibleCampaigns.map((campaign) => <div className="growth-source-row" key={campaign.campaign_id}><strong>{campaign.name}</strong><code>{campaign.code}</code><span>{campaign.site_name || campaign.site_id} · {campaign.channel_name || channels.find((item) => item.channel_id === campaign.channel_id)?.name}</span><span className={`status-pill ${campaign.status}`}>{trackingStatusLabel(campaign.status)}</span></div>)}</div>
  ) : <div className="growth-workspace-empty">{campaigns.length ? "没有符合筛选条件的活动" : "尚未创建活动"}</div>}
</div>
```

Extend `trackingStatusLabel` so `draft` returns `草稿`, `archived` returns `归档`, `active` returns `启用`, and all remaining supported states return `停用`.

- [ ] **Step 5: Render each existing form inside the modal**

Render the complete form fields after the main active-tab content without changing payload behavior:

```tsx
{createModal === "link" && (
  <GrowthCreateModal title="新建推广链接" submitLabel="创建链接" saving={saving} submitDisabled={!linkForm.site_id || !linkForm.campaign_id || !linkForm.source_name.trim()} onClose={onCloseCreate} onSubmit={onCreateLink}>
    <div className="growth-form-grid" data-growth-form="link">
      <SiteSelect sites={sites} value={linkForm.site_id} disabled={saving} onChange={onSelectSite} />
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
  <GrowthCreateModal title="新建活动" submitLabel="创建活动" saving={saving} submitDisabled={!campaignForm.site_id || !campaignForm.channel_id || !isValidGrowthCode(campaignForm.code) || !campaignForm.name.trim()} onClose={onCloseCreate} onSubmit={onCreateCampaign}>
    <div className="growth-form-grid compact" data-growth-form="campaign">
      <SiteSelect sites={sites} value={campaignForm.site_id} disabled={saving} onChange={(siteId) => onCampaignFormChange({ ...campaignForm, site_id: siteId })} />
      <label><span className="field-label"><strong>渠道</strong></span><select value={campaignForm.channel_id} onChange={(event) => onCampaignFormChange({ ...campaignForm, channel_id: event.target.value })} required><option value="">选择渠道</option>{channels.map((channel) => <option value={channel.channel_id} key={channel.channel_id}>{channel.name}</option>)}</select></label>
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
          {campaignCodeInvalid ? "仅支持小写英文字母、数字和连字符" : "例如：summer-2026"}
        </span>
      </label>
      <label><span className="field-label"><strong>活动名称</strong></span><input value={campaignForm.name} onChange={(event) => onCampaignFormChange({ ...campaignForm, name: event.target.value })} placeholder="2026 夏季推广" required /></label>
      <label className="span-2"><span className="field-label"><strong>说明</strong><span>（可选）</span></span><input value={campaignForm.description} onChange={(event) => onCampaignFormChange({ ...campaignForm, description: event.target.value })} /></label>
    </div>
  </GrowthCreateModal>
)}
```

- [ ] **Step 6: Run the focused tests and verify GREEN**

Run:

```powershell
cd frontend
npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx src/components/GrowthCreateModal.test.tsx
```

Expected: both test files pass.

- [ ] **Step 7: Commit the workspace behavior**

```powershell
git add frontend/src/pages/TrafficAnalysisPage.tsx frontend/src/pages/TrafficAnalysisPage.test.tsx
git commit -m "Split Growth workspace into query-first pages"
```

### Task 4: Finish query-list and responsive modal styling

**Files:**
- Modify: `frontend/styles.css`
- Test: `frontend/src/pages/TrafficAnalysisPage.test.tsx`

- [ ] **Step 1: Add a structural regression assertion before CSS changes**

Add to the four-tab test in `frontend/src/pages/TrafficAnalysisPage.test.tsx`:

```tsx
expect(linksHtml).toContain('class="panel growth-list-page"');
expect(channelsHtml).toContain('class="growth-query-grid growth-query-grid-compact"');
expect(campaignsHtml).toContain('class="growth-query-grid"');
```

- [ ] **Step 2: Run the focused test and confirm it reflects current structure**

Run:

```powershell
cd frontend
npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx
```

Expected: PASS if Task 3 used the specified class names. If it fails, fix the JSX class names rather than weakening the assertions.

- [ ] **Step 3: Replace obsolete source-stage layout rules with query-list rules**

In `frontend/styles.css`, keep existing colors and controls, remove only rules no longer referenced by the Growth workspace, and add:

```css
.growth-list-page {
  width: 100%;
  min-width: 0;
  padding: 14px;
}

.growth-query-grid {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 8px;
  min-width: 0;
  margin-bottom: 12px;
  padding-bottom: 12px;
  border-bottom: 1px solid var(--line);
}

.growth-query-grid-compact {
  grid-template-columns: minmax(130px, 0.45fr) minmax(220px, 1fr);
}

.growth-query-grid label {
  min-width: 0;
}

.growth-query-grid label > span {
  color: var(--muted);
  font-size: 11px;
  font-weight: 650;
}

.growth-query-grid input,
.growth-query-grid select {
  width: 100%;
  min-width: 0;
}

.growth-source-rows {
  min-width: 0;
}

.growth-source-row {
  grid-template-columns: minmax(120px, 0.8fr) minmax(110px, 0.65fr) minmax(180px, 1fr) auto;
}

@media (max-width: 1040px) {
  .growth-query-grid {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 760px) {
  .growth-query-grid,
  .growth-query-grid-compact,
  .growth-source-row {
    grid-template-columns: 1fr;
  }

  .growth-modal-body .growth-form-grid {
    grid-template-columns: 1fr;
  }

  .growth-modal-body .growth-form-grid .span-2 {
    grid-column: span 1;
  }
}
```

Do not introduce gradients, decorative backgrounds, nested cards, or style changes outside the Growth workspace.

- [ ] **Step 4: Run focused tests after styling**

Run:

```powershell
cd frontend
npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx src/components/GrowthCreateModal.test.tsx
```

Expected: all focused tests pass.

- [ ] **Step 5: Commit responsive styling**

```powershell
git add frontend/src/pages/TrafficAnalysisPage.test.tsx frontend/styles.css
git commit -m "Style Growth query pages and creation modals"
```

### Task 5: Full verification and visual acceptance

**Files:**
- Verify: `frontend/src/pages/TrafficAnalysisPage.tsx`
- Verify: `frontend/src/components/GrowthCreateModal.tsx`
- Verify: `frontend/styles.css`

- [ ] **Step 1: Run the complete frontend test suite**

Run:

```powershell
cd frontend
npm.cmd test
```

Expected: every frontend test passes with zero failures.

- [ ] **Step 2: Run the production build**

Run:

```powershell
cd frontend
npm.cmd run build
```

Expected: TypeScript and Vite complete successfully. Record the existing chunk-size warning separately if it remains.

- [ ] **Step 3: Run diff hygiene checks**

Run:

```powershell
git diff --check
git status --short
```

Expected: no whitespace errors; only intended Growth workspace files are changed or committed. Preserve concurrent unrelated user changes.

- [ ] **Step 4: Start or reuse the local Vite server**

Run:

```powershell
cd frontend
npm.cmd run dev -- --port 5173
```

Expected: Vite serves `http://127.0.0.1:5173/traffic-analysis`. If 5173 is occupied, use the next free port and report it.

- [ ] **Step 5: Inspect desktop behavior at 1440×900**

Verify in the browser:

1. Four tab labels and order are exact.
2. Each of the first three pages opens with filters and list, not a visible creation form.
3. Each new button opens the correct modal.
4. Modal width is visually about three quarters of the viewport and does not imitate the discarded mockup styling.
5. Modal header and footer stay visible while the promotion form body scrolls.
6. Invalid activity code disables submission and shows the existing Chinese message.
7. Closing and reopening resets abandoned input; a failed request preserves input.

- [ ] **Step 6: Inspect mobile behavior at 375×812**

Verify in the browser:

1. Tabs wrap or remain usable without clipping text.
2. Query fields and list rows become single-column without page-level horizontal overflow.
3. Modal occupies the available width with 12px outer spacing.
4. Form fields become single-column and all footer buttons remain visible.
5. No text, select, button, or status pill overlaps another element.

- [ ] **Step 7: Re-run tests after any visual correction**

Run:

```powershell
cd frontend
npm.cmd test
npm.cmd run build
```

Expected: all tests and the production build pass after the final CSS adjustment.

## Plan Self-Review

- Spec coverage: Tasks 1-4 cover four tabs, three query pages, three creation modals, success/failure behavior, 75vw desktop size, mobile reflow, accessibility and unchanged site integration.
- Scope: No database, API, payload, dependency, edit/delete, pagination or analytics changes are included.
- Type consistency: `TrafficAnalysisTab`, `GrowthCreateKind`, `ChannelFilters`, `CampaignFilters`, `createModal`, `onOpenCreate` and `onCloseCreate` use the same names throughout.
- Regression protection: Existing tracking-link payload, channel-without-campaign and activity-code validation tests remain in place and move with their forms.
- Placeholder scan: No TBD/TODO, conditional token lookup, abbreviated JSX block or unspecified error-handling step remains.
