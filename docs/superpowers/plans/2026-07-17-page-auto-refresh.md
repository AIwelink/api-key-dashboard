# Page-level Auto Refresh Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refresh the active page's server-backed data every 60 seconds, with the API pool status page as the primary integration, without resetting user context or duplicating in-flight requests.

**Architecture:** Add one reusable React hook backed by a small testable scheduler. Pages supply a current refresh callback and pause flag; the hook owns timing, document visibility recovery, overlap prevention, cleanup, and silent error handling. The API pool status page replaces its existing site-configured timer with this hook and continues reading backend cache rather than triggering remote synchronization.

**Tech Stack:** React 19, TypeScript 5.9, Vite 7, Vitest, existing `api` client.

---

### Task 1: Auto-refresh scheduler and hook

**Files:**
- Create: `frontend/src/hooks/usePageAutoRefresh.ts`
- Create: `frontend/src/hooks/usePageAutoRefresh.test.ts`
- Modify: `frontend/package.json`
- Modify: `frontend/package-lock.json`

- [ ] **Step 1: Add Vitest and a focused test command**

Add `vitest` as a development dependency and add:

```json
"test": "vitest run"
```

- [ ] **Step 2: Write failing scheduler tests**

Cover these behaviors with fake timers:

```ts
it("refreshes after sixty seconds while visible")
it("does not overlap a pending refresh")
it("pauses while hidden and catches up when visible")
it("stops timers and visibility listeners on cleanup")
it("swallows automatic refresh failures through the optional error callback")
```

- [ ] **Step 3: Run tests and verify RED**

Run: `npm.cmd test -- src/hooks/usePageAutoRefresh.test.ts`

Expected: FAIL because `usePageAutoRefresh.ts` does not exist.

- [ ] **Step 4: Implement the scheduler and React hook**

Export a 60-second default and an injectable scheduler for deterministic tests:

```ts
export const PAGE_AUTO_REFRESH_INTERVAL_MS = 60_000;

export function createPageAutoRefreshScheduler(options: SchedulerOptions): AutoRefreshScheduler {
  // Track last attempt and in-flight state, skip hidden ticks, catch errors,
  // expose visibilityChanged(), and release the interval from stop().
}

export function usePageAutoRefresh(
  refresh: () => void | Promise<void>,
  options: { enabled?: boolean; paused?: boolean; intervalMs?: number } = {},
) {
  // Keep refresh in a ref so current filters/pagination are used without
  // restarting the timer on every render.
}
```

- [ ] **Step 5: Run focused tests and verify GREEN**

Run: `npm.cmd test -- src/hooks/usePageAutoRefresh.test.ts`

Expected: all scheduler tests pass.

### Task 2: API pool status page integration

**Files:**
- Modify: `frontend/src/pages/ApiPoolStatusPage.tsx`

- [ ] **Step 1: Import and register the shared hook**

Replace the existing `refreshIntervalMinutes` timer effect with:

```ts
usePageAutoRefresh(refreshStatusData, {
  enabled: Boolean(selectedSiteId && selectedGroupId !== null),
  paused: Boolean(refreshingRemote || refreshingFrontend || remoteActionBusyId || confirmState),
});
```

- [ ] **Step 2: Add the silent status snapshot callback**

The callback first fetches current sites and status preferences, then fetches groups and the visible account page using the preserved site, group, page, page size, status filter, and pinned preferences. Do not commit any state until every required request succeeds; React then batches the snapshot updates:

```ts
const refreshStatusData = async () => {
  const [sitesData, preferences] = await Promise.all([
    api<SitesResponse>("/sub2api-sites?site_type=sub2api", token),
    api<StatusPreferences>("/api-pools/status-preferences", token),
  ]);
  const nextSiteId = chooseSiteId(sitesData.items, selectedSiteId, preferences.pinned_site_id);
  const groupsData = nextSiteId
    ? await api<GroupsResponse>(`/sub2api-sites/${nextSiteId}/groups?page=1&page_size=100`, token)
    : null;
  const nextGroupId = chooseGroupId(groupsData?.items || [], selectedGroupId, preferences.pinned_group_id);
  const accountsData = nextSiteId && nextGroupId !== null
    ? await fetchAccountPage(nextSiteId, nextGroupId, accountPage)
    : null;
  applyStatusSnapshot({ sitesData, preferences, groupsData, accountsData, nextSiteId, nextGroupId });
};
```

Extract `fetchAccountPage` from the request-building portion of `loadAccounts`. `applyStatusSnapshot` updates the selected group's `capacity_summary` from the accounts response, writes the page cache, then updates sites, preferences, groups, selected IDs, accounts, totals, data key, and cache timestamp together.

It must not call `POST /sub2api-sites/:id/refresh`, display a success toast, clear page cache before data arrives, reset page/filter state, or scroll the document.

- [ ] **Step 3: Keep manual refresh behavior explicit**

Keep `refreshAll` as the remote synchronization action. Reuse `refreshStatusData` from `refreshFrontendData`, while retaining its manual loading state and success/error toast.

- [ ] **Step 4: Verify the API status page build**

Run: `npm.cmd run build`

Expected: TypeScript and Vite build complete successfully.

### Task 3: Other collection and operations pages

**Files:**
- Modify: `frontend/src/pages/AccountsPage.tsx`
- Modify: `frontend/src/pages/TodoPage.tsx`
- Modify: `frontend/src/pages/ManualPoolPage.tsx`
- Modify: `frontend/src/pages/EventRecordsPage.tsx`
- Modify: `frontend/src/pages/AlertCenterPage.tsx`
- Modify: `frontend/src/pages/AuditPage.tsx`
- Modify: `frontend/src/pages/UsersPage.tsx`

- [ ] **Step 1: Register current-page loaders**

Use `usePageAutoRefresh` with the existing current-filter loaders:

```ts
usePageAutoRefresh(loadAccounts, { paused: Boolean(editingAccount || busyId || bulkBusy || confirmState) });
usePageAutoRefresh(refreshTodoData, { paused: Boolean(editingAccount || resurrectionWorkspace || busyId) });
usePageAutoRefresh(refreshManualPoolData, { paused: Boolean(busyId || bulkBusy || confirmState) });
usePageAutoRefresh(() => loadData({ force: true }), { paused: Boolean(detailIdentityId) });
usePageAutoRefresh(loadAlerts, { paused: Boolean(markingId) });
usePageAutoRefresh(loadAudit);
usePageAutoRefresh(loadUsers, { paused: Boolean(editingUser || busy) });
```

`refreshTodoData` reloads the three currently paged todo collections. `refreshManualPoolData` reloads accounts, groups, and refill logs without changing selected site/group. The push-error todo subpage registers its own current-filter account loader.

- [ ] **Step 2: Keep operation state intact**

Do not refresh while an edit drawer, resurrection workspace, confirmation modal, bulk operation, or account action is active. Automatic callbacks catch errors silently through the scheduler and do not call `showToast`.

- [ ] **Step 3: Type-check the collection page integrations**

Run: `npm.cmd run build`

Expected: TypeScript and Vite build complete successfully.

### Task 4: Management and agent pages

**Files:**
- Modify: `frontend/src/pages/AccountPoolsPage.tsx`
- Modify: `frontend/src/pages/AgentAnalysisPage.tsx`
- Modify: `frontend/src/pages/AgentWorkbenchPage.tsx`
- Modify: `frontend/src/pages/ApiTokensPage.tsx`

- [ ] **Step 1: Refresh read-only management data without replacing forms**

On site configuration, refresh observability settings for the selected Sub2API site only. Do not call `loadSites` or `loadCapacityLimits` automatically because both populate editable forms.

On system management, reload only the active tab's collection and pause while `busy` or while a notification channel is being edited.

- [ ] **Step 2: Refresh agent status by active context**

Agent Analysis reloads pools, agent state, and scheduler status while no analysis/chat request is active. Agent Workbench reloads only the active tab using its current filters and pauses during task/evaluation/notification mutations.

- [ ] **Step 3: Run all frontend tests**

Run: `npm.cmd test`

Expected: all auto-refresh scheduler tests pass.

- [ ] **Step 4: Run the production build and repository checks**

Run: `npm.cmd run build`

Run from repository root: `git diff --check` and `git status --short --branch`

Expected: build succeeds, no whitespace errors, and only intentional frontend, test-infrastructure, and plan changes remain.
