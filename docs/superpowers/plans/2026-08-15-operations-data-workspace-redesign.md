# Operations Data Workspace Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved open data-workspace redesign for Traffic Analysis and Operations Management, including accessible metric definitions, anchored long-page navigation, clearer trends, and Cohort heat mapping.

**Architecture:** Add two focused shared primitives for metric definitions and long-page navigation, while leaving domain definitions beside their owning pages. Restructure only the overview surfaces; preserve existing API requests, permissions, configuration tabs, tables, and modal workflows. Apply final CSS layers scoped to each page so the redesign does not change unrelated console pages.

**Tech Stack:** React 19, TypeScript, CSS, Vitest, React server rendering, Vite

---

### Task 1: Shared data-workspace primitives

**Files:**
- Create: `frontend/src/components/dataWorkspace/MetricDefinition.tsx`
- Create: `frontend/src/components/dataWorkspace/WorkspaceRail.tsx`
- Create: `frontend/src/components/dataWorkspace/DataWorkspace.css`
- Test: `frontend/src/components/dataWorkspace/DataWorkspace.test.tsx`

- [ ] **Step 1: Write the failing shared-component tests**

```tsx
const html = renderToStaticMarkup(
  <MetricDefinition label="活跃用户" details={{
    definition: "至少成功调用一次的用户。",
    formula: "COUNT(DISTINCT user_id)",
    included: "普通用户",
    excluded: "内部用户",
    source: "usage_records",
    freshness: "15 分钟",
  }} />,
);
expect(html).toContain('role="tooltip"');
expect(html).toContain("COUNT(DISTINCT user_id)");
expect(html).toContain("纳入");
expect(html).toContain("排除");
expect(html).toContain("来源");
expect(html).toContain("更新");
```

Add a second assertion that `WorkspaceRail` renders a named navigation region and `href="#summary"` anchor.

- [ ] **Step 2: Run the test and verify RED**

Run: `npm test -- src/components/dataWorkspace/DataWorkspace.test.tsx`

Expected: FAIL because the shared component modules do not exist.

- [ ] **Step 3: Implement the minimal accessible components and styles**

Implement `MetricDefinitionDetails`, a button trigger linked by `aria-describedby` to a `role="tooltip"`, and `WorkspaceRail` with an `aria-label`, anchor items, optional counts, and optional status text. Scope all CSS under `.metric-definition` and `.workspace-rail`.

- [ ] **Step 4: Run the shared tests and verify GREEN**

Run: `npm test -- src/components/dataWorkspace/DataWorkspace.test.tsx`

Expected: PASS.

### Task 2: Traffic Analysis open workspace

**Files:**
- Modify: `frontend/src/pages/trafficAnalysis/TrafficOverview.tsx`
- Modify: `frontend/styles.css`
- Modify: `frontend/src/pages/trafficAnalysis/TrafficOverview.test.tsx`
- Modify: `frontend/src/pages/trafficAnalysis/TrafficOverviewStyles.test.ts`

- [ ] **Step 1: Write failing traffic layout and metric tests**

Add assertions that rendered markup contains `traffic-overview-workspace`, `traffic-overview-kpi-strip`, `访问流量页面索引`, `注册转化率`, `12.5%`, the formula `归因注册账号 ÷ 有效 Session UV × 100%`, and `traffic-overview-trend-chart`.

Add a style test that the final `.traffic-overview-section` rule has no background, border radius, or shadow; the workspace uses a `176px minmax(0, 1fr)` grid; and the query bar is sticky on desktop.

- [ ] **Step 2: Run focused traffic tests and verify RED**

Run: `npm test -- src/pages/trafficAnalysis/TrafficOverview.test.tsx src/pages/trafficAnalysis/TrafficOverviewStyles.test.ts`

Expected: FAIL on missing workspace, KPI, rail, tooltip, trend chart, and final layout rules.

- [ ] **Step 3: Implement traffic structure and metric definitions**

Import the shared primitives directly. Wrap overview content in `WorkspaceRail` plus `traffic-overview-main`, keep all seven filters, replace the framed hero with a six-column KPI strip, calculate registration conversion with a zero-denominator guard, show generated/latest timestamps outside KPI values, and retain every existing table and quality diagnostic.

- [ ] **Step 4: Implement the traffic trend chart and open styles**

Add a pure `buildTrafficTrendPoints(values, width, height)` helper and code-native SVG chart for homepage PV, homepage UV, and link PV. Add a final scoped CSS section that creates the rail/main layout, sticky query bar, open data bands, two-column source section, stable KPI tracks, tooltip positioning, and mobile collapse.

- [ ] **Step 5: Run focused traffic tests and verify GREEN**

Run: `npm test -- src/pages/trafficAnalysis/TrafficOverview.test.tsx src/pages/trafficAnalysis/TrafficOverviewStyles.test.ts`

Expected: PASS.

### Task 3: Operations Management open workspace

**Files:**
- Modify: `frontend/src/pages/OperationsManagementPage.tsx`
- Modify: `frontend/src/pages/OperationsManagementPage.css`
- Modify: `frontend/src/pages/OperationsManagementPage.test.tsx`

- [ ] **Step 1: Write failing operations workspace tests**

Add assertions for `operations-overview-workspace`, `运营概览页面索引`, `数据截至`, `付费 / 计费用户`, metric tooltip formula/source content, and heat classes returned by `retentionHeatTone` for null, low, medium, and high retention.

- [ ] **Step 2: Run the operations test and verify RED**

Run: `npm test -- src/pages/OperationsManagementPage.test.tsx`

Expected: FAIL because the overview wrapper, rail, definitions, latest-data header, and heat-tone helper do not exist.

- [ ] **Step 3: Implement operations metric definitions and workspace structure**

Extend `Metric` with an optional `MetricDefinitionDetails`. Add definitions for the six summary KPIs and eight lifecycle metrics. Render latest sync time in the page header, use `WorkspaceRail` only for the overview tab, and keep the three configuration tabs unchanged.

- [ ] **Step 4: Implement Cohort heat mapping and open styles**

Export `retentionHeatTone(rate)` returning `pending`, `low`, `medium-low`, `medium`, `high`, or `very-high`. Apply it only to mature Cohort values. Append scoped CSS overrides for the `176px` rail, sticky query bar, open metric strip, `4 x 2` lifecycle matrix, borderless data sections, heat cells, and responsive collapse.

- [ ] **Step 5: Run the operations test and verify GREEN**

Run: `npm test -- src/pages/OperationsManagementPage.test.tsx`

Expected: PASS.

### Task 4: Regression and production verification

**Files:**
- Modify only if verification exposes a covered defect.

- [ ] **Step 1: Run the complete frontend suite**

Run: `npm test -- --run`

Expected: all tests pass with no failed test files.

- [ ] **Step 2: Run the production build**

Run with bundled Node 24: `node.exe node_modules/typescript/bin/tsc -p tsconfig.json` and `node.exe node_modules/vite/bin/vite.js build`.

Expected: TypeScript and Vite build complete successfully. Existing bundle-size warnings may remain; no new build error is accepted.

- [ ] **Step 3: Run rendered desktop and mobile QA**

Start Vite on an unused localhost port. Use Playwright fallback only if the in-app Browser runtime remains unavailable. Verify page identity, meaningful DOM, no framework overlay, no relevant console error, hover/focus metric details, overview/config tab switching, page-level horizontal overflow, wide-table local overflow, and screenshots at `1600 x 1000`, `1024 x 900`, and `390 x 844`.

- [ ] **Step 4: Compare accepted concept and implementation screenshots**

Use `view_image` on the accepted concept and final rendered screenshots. Record at least five checks: open container model, KPI density, rail behavior, table continuity, Cohort heat treatment, tooltip content, and responsive collapse. Fix every material mismatch before proceeding.

### Task 5: Publish the branch

**Files:**
- Update plan checkboxes and design documentation only if implementation decisions changed.

- [ ] **Step 1: Review the final diff and status**

Run: `git status --short`, `git diff --check`, and `git diff --stat origin/achernar/dev...HEAD`.

Expected: only intended frontend and documentation files; no whitespace errors or QA artifacts.

- [ ] **Step 2: Commit intentionally**

Commit shared primitives/tests, traffic redesign, operations redesign, and final documentation as coherent commits using `feat:` or `test:` prefixes.

- [ ] **Step 3: Push and open the PR**

Push `codex/operations-workspace-redesign` and create a PR with base `achernar/dev`. The PR body must summarize the two page redesigns, metric definition behavior, preserved business logic, and exact verification commands/results.

