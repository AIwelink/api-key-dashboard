# Growth Configuration Editing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add safe editing for Growth tracking links, channels, and campaigns while preserving immutable attribution identity fields and the existing inline site integration editor.

**Architecture:** Keep server schemas and routes unchanged because PATCH support already exists. Add typed edit-form conversion helpers and isolated edit state to `TrafficAnalysisPage`, render edit actions and large modal forms through the existing modal component, and send allowlisted PATCH payloads through the existing mutation/refresh pipeline.

**Tech Stack:** React 19, TypeScript, Vitest, FastAPI, Pydantic, SQLAlchemy, unittest.

---

### Task 1: Typed edit forms and allowlisted payload builders

**Files:**
- Modify: `frontend/src/pages/TrafficAnalysisPage.tsx`
- Test: `frontend/src/pages/TrafficAnalysisPage.test.tsx`

- [ ] **Step 1: Write failing conversion tests**

Add tests that call `trackingLinkToEditForm`, `buildTrackingLinkUpdatePayload`, `channelToEditForm`, `buildChannelUpdatePayload`, `campaignToEditForm`, and `buildCampaignUpdatePayload`. Assert that identity fields never appear in PATCH payloads, dimensions are trimmed to three entries, empty optional timestamps become `null`, and archived links remain archived when the original status is supplied to the payload builder.

```tsx
expect(buildChannelUpdatePayload(channelToEditForm(channel))).toEqual({
  name: "小红书",
  description: "",
  status: "active",
});
expect(buildCampaignUpdatePayload(campaignToEditForm(campaign))).not.toHaveProperty("site_id");
expect(buildTrackingLinkUpdatePayload(trackingLinkToEditForm(trackingLink), trackingLink.status)).not.toHaveProperty("code");
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx`

Expected: FAIL because edit form helpers do not exist.

- [ ] **Step 3: Add typed edit forms and conversion helpers**

Extend loaded records with optional time fields and add forms that contain only mutable values:

```ts
export type TrackingLinkEditForm = {
  source_type: TrackingSourceType;
  source_name: string;
  source_url: string;
  audience_group: string;
  promoter: string;
  landing_path: string;
  dimensions: Array<{ key: string; value: string }>;
  valid_from: string;
  valid_until: string;
  status: "active" | "paused" | "archived";
};

export type ChannelEditForm = {
  name: string;
  description: string;
  status: "active" | "disabled" | "archived";
};

export type CampaignEditForm = {
  name: string;
  description: string;
  starts_at: string;
  ends_at: string;
  status: "draft" | "active" | "paused" | "archived";
};
```

Use `datetime-local` strings in forms, ISO strings or `null` in PATCH payloads, and one shared dimension normalizer. `buildTrackingLinkUpdatePayload(form, originalStatus)` must force `status: "archived"` when `originalStatus` is archived.

- [ ] **Step 4: Run focused tests and confirm GREEN**

Run: `npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx`

Expected: all focused tests pass.

### Task 2: Edit modal rendering and list actions

**Files:**
- Modify: `frontend/src/pages/TrafficAnalysisPage.tsx`
- Modify: `frontend/styles.css`
- Test: `frontend/src/pages/TrafficAnalysisPage.test.tsx`

- [ ] **Step 1: Write failing render tests**

Extend the workspace test helper with edit-target and edit-form props. Verify every list row exposes an edit button and each edit target renders the correct dialog, immutable identity summary, and mutable fields.

```tsx
expect(renderWorkspace("links")).toContain('aria-label="编辑推广链接 Claude API 入门第 3 篇"');
expect(renderWorkspace("channels")).toContain('aria-label="编辑渠道 小红书"');
expect(renderWorkspace("campaigns")).toContain('aria-label="编辑活动 2026 夏季推广"');
expect(linkEditHtml).toContain("链接编码");
expect(linkEditHtml).toContain("7km4q2xd");
expect(linkEditHtml).toContain('data-growth-edit-form="link"');
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx`

Expected: FAIL because edit actions and dialogs are absent.

- [ ] **Step 3: Add edit callbacks, identity summaries, and modal forms**

Add `GrowthEditTarget` and the edit props to `TrafficAnalysisWorkspace`. Render compact `编辑` buttons on link, channel, and campaign rows. Reuse `GrowthCreateModal` with titles `编辑推广链接`, `编辑渠道`, and `编辑活动`.

```tsx
<button
  aria-label={`编辑活动 ${campaign.name}`}
  className="ghost compact-button"
  onClick={() => onOpenCampaignEdit(campaign)}
  type="button"
>
  编辑
</button>
```

Display immutable values in `.growth-edit-identity`; render editable fields in the existing two-column form grid. Disable the tracking-link status selector when the target status is archived.

- [ ] **Step 4: Add restrained styling**

Give row actions stable flex sizing, keep identity information as an unframed full-width band, and preserve one-column mobile behavior:

```css
.growth-edit-identity {
  display: grid;
  grid-template-columns: repeat(3, minmax(0, 1fr));
  gap: 8px 16px;
  padding-bottom: 14px;
  border-bottom: 1px solid var(--line);
}
```

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run: `npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx`

Expected: all focused tests pass.

### Task 3: Isolated edit state and PATCH save flow

**Files:**
- Modify: `frontend/src/pages/TrafficAnalysisPage.tsx`
- Test: `frontend/src/pages/TrafficAnalysisPage.test.tsx`

- [ ] **Step 1: Write failing state and payload tests**

Add pure tests showing each record produces a fresh edit snapshot and that allowlisted PATCH payloads remain unchanged when create forms are modified. Add a render test confirming create and edit dialogs cannot render simultaneously.

```tsx
const edit = channelToEditForm(channel);
const changedCreate = { ...emptyChannelForm, name: "新渠道" };
expect(edit.name).toBe("小红书");
expect(changedCreate.name).toBe("新渠道");
```

- [ ] **Step 2: Run focused tests and confirm RED**

Run: `npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx`

Expected: FAIL until edit-target isolation is implemented.

- [ ] **Step 3: Implement page-level edit state**

Add one discriminated edit target plus three dedicated forms. Opening an edit closes any create modal and initializes only the corresponding edit form. Closing edit while not saving clears target and restores empty edit forms.

```ts
type GrowthEditTarget =
  | { kind: "link"; item: GrowthTrackingLink }
  | { kind: "channel"; item: GrowthChannel }
  | { kind: "campaign"; item: GrowthCampaign };
```

- [ ] **Step 4: Implement PATCH mutations**

Use the existing `runMutation` helper for all edits:

```ts
api(`/growth/channels/${editTarget.item.channel_id}`, token, {
  method: "PATCH",
  body: JSON.stringify(buildChannelUpdatePayload(channelEditForm)),
});
```

On success clear the edit target after refresh. On mutation failure leave target and form state untouched. Validate required names and time ordering before submitting.

- [ ] **Step 5: Run focused tests and confirm GREEN**

Run: `npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx`

Expected: all focused tests pass.

### Task 4: PATCH route audit compatibility coverage

**Files:**
- Test: `backend/tests/test_growth_routes.py`

- [ ] **Step 1: Add route audit characterization tests**

For channel, campaign, and tracking-link PATCH handlers, mock the repository result and `write_audit_log`. Assert the resource ID, action, and public result are recorded.

```python
self.assertEqual(audit_mock.await_args.kwargs["action"], "growth.channel.update")
self.assertEqual(audit_mock.await_args.kwargs["resource_id"], str(channel_id))
self.assertEqual(audit_mock.await_args.kwargs["after"], result)
```

- [ ] **Step 2: Run route tests**

Run: `.\.venv\Scripts\python.exe -m unittest tests.test_growth_routes -v`

Expected: tests pass against the existing PATCH implementation, confirming that the frontend can rely on the existing update and audit contract without backend production changes.

### Task 5: Full verification and visual acceptance

**Files:**
- Verify: `frontend/src/pages/TrafficAnalysisPage.tsx`
- Verify: `frontend/styles.css`
- Verify: `backend/app/routers/growth.py`

- [ ] **Step 1: Run complete automated verification**

Run:

```powershell
cd frontend
npm.cmd test
npm.cmd run build
cd ..\backend
.\.venv\Scripts\python.exe -m unittest discover -s tests
```

Expected: all frontend tests, all backend tests, and the production build pass.

- [ ] **Step 2: Run source quality checks**

Run: `git diff --check`

Expected: no whitespace errors.

- [ ] **Step 3: Verify desktop and mobile layouts**

Open `/traffic-analysis` at 1440x900 and 375x812. Check all three edit dialogs for non-overlapping identity rows, scrollable bodies, visible footer actions, single-column mobile fields, and no horizontal overflow.

- [ ] **Step 4: Commit the implementation**

```powershell
git add frontend/src/pages/TrafficAnalysisPage.tsx frontend/src/pages/TrafficAnalysisPage.test.tsx frontend/styles.css backend/tests/test_growth_routes.py
git commit -m "Add Growth configuration editing"
```
