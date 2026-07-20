# Traffic Analysis Placeholder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an authenticated blank “访问流量分析” management page immediately above “运营管理” in the existing navigation.

**Architecture:** Follow the existing single-page view registry in `App.tsx`: add one `ViewName`, one route mapping, one navigation item, and one render branch. Keep the page as a focused component that mirrors the existing blank `OperationsManagementPage` and does not introduce backend or state dependencies.

**Tech Stack:** React 19, TypeScript, Vite, Vitest

---

### Task 1: Lock Navigation and Routing Behavior with Tests

**Files:**
- Modify: `frontend/src/App.test.ts`

- [x] **Step 1: Update the owner navigation expectation**

Change the operations navigation group expectation to:

```ts
["traffic-analysis", "operations-management"],
```

- [x] **Step 2: Update the non-owner navigation expectation**

Change the operations navigation group expectation to:

```ts
["traffic-analysis", "operations-management"],
```

- [x] **Step 3: Add the new route expectation**

Add this assertion to the route test:

```ts
expect(viewFromPath("/traffic-analysis")).toBe("traffic-analysis");
```

- [x] **Step 4: Run the focused test and verify it fails**

Run:

```powershell
npm test -- App.test.ts
```

Expected: FAIL because `traffic-analysis` is not yet present in `ViewName`, the visible navigation groups, or route mapping.

### Task 2: Add the Blank Page and Register It

**Files:**
- Create: `frontend/src/pages/TrafficAnalysisPage.tsx`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/App.tsx`

- [x] **Step 1: Create the blank page component**

Create `frontend/src/pages/TrafficAnalysisPage.tsx` with:

```tsx
export function TrafficAnalysisPage() {
  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>访问流量分析</h2>
        </div>
      </div>
    </section>
  );
}
```

- [x] **Step 2: Add the view type**

Add `"traffic-analysis"` immediately before `"operations-management"` in the `ViewName` union in `frontend/src/types.ts`.

- [x] **Step 3: Register the page and navigation item**

In `frontend/src/App.tsx`:

```ts
import { TrafficAnalysisPage } from "./pages/TrafficAnalysisPage";
```

Change the operations group to:

```ts
const operationsManagementNavItems: Array<[ViewName, string]> = [
  ["traffic-analysis", "访问流量分析"],
  ["operations-management", "运营管理"],
];
```

Add the collapsed label and route:

```ts
"traffic-analysis": "流",
```

```ts
"traffic-analysis": "/traffic-analysis",
```

Render the page before the existing operations page branch:

```tsx
{view === "traffic-analysis" && <TrafficAnalysisPage />}
```

Update `navigationGroupClass` so the operations group keeps its existing separator styling when its first item becomes `traffic-analysis`:

```ts
if (firstKey === "traffic-analysis") return "nav-group operations-management-nav-group";
```

- [x] **Step 4: Run the focused test and verify it passes**

Run:

```powershell
npm test -- App.test.ts
```

Expected: 4 tests pass.

### Task 3: Verify the Frontend and Commit the Feature

**Files:**
- Verify: `frontend/src/App.test.ts`
- Verify: `frontend/src/App.tsx`
- Verify: `frontend/src/types.ts`
- Verify: `frontend/src/pages/TrafficAnalysisPage.tsx`

- [x] **Step 1: Run the complete frontend test suite**

Run:

```powershell
npm test
```

Expected: all frontend Vitest suites pass.

- [x] **Step 2: Run the production build**

Run:

```powershell
npm run build
```

Expected: TypeScript and Vite production build complete successfully.

- [x] **Step 3: Verify desktop and mobile layouts**

Start the existing Vite development server and inspect `/traffic-analysis` while authenticated at desktop and mobile widths. Confirm the navigation item appears immediately above “运营管理”, the title reads “访问流量分析”, and no text overlaps or overflows.

- [x] **Step 4: Commit the implementation**

```powershell
git add frontend/src/App.test.ts frontend/src/App.tsx frontend/src/types.ts frontend/src/pages/TrafficAnalysisPage.tsx docs/superpowers/plans/2026-07-21-traffic-analysis-placeholder.md
git commit -m "feat: add traffic analysis page"
```
