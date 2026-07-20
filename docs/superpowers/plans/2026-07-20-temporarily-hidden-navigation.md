# Temporarily Hidden Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Hide six account-management pages from the shared sidebar while preserving their direct URL routes.

**Architecture:** Keep navigation, path, and page-rendering ownership in `App.tsx`. Introduce one hidden-view set and one pure group-filtering helper so desktop and mobile navigation share the same visibility rule, while `viewFromPath` continues resolving every hidden page URL.

**Tech Stack:** React 19, TypeScript 5.9, Vitest 4, Vite 7

---

### Task 1: Filter Hidden Navigation Entries

**Files:**
- Create: `frontend/src/App.test.ts`
- Modify: `frontend/src/App.tsx:28-120, 183, 213`

- [ ] **Step 1: Write the failing navigation test**

Create `frontend/src/App.test.ts`:

```tsx
import { describe, expect, it } from "vitest";
import { getVisibleNavigationGroups, viewFromPath } from "./App";

const hiddenViews = [
  "upload",
  "todos",
  "push-error-todos",
  "accounts",
  "available-pool",
  "reserve-pool",
] as const;

describe("application navigation", () => {
  it("omits temporarily hidden pages and empty groups from the sidebar", () => {
    const groups = getVisibleNavigationGroups(true);
    const visibleViews = groups.flatMap((group) => group.map(([view]) => view));

    expect(groups.every((group) => group.length > 0)).toBe(true);
    hiddenViews.forEach((view) => expect(visibleViews).not.toContain(view));
  });

  it("keeps direct URLs for hidden pages available", () => {
    expect(viewFromPath("/upload-accounts")).toBe("upload");
    expect(viewFromPath("/todo-and-error-accounts")).toBe("todos");
    expect(viewFromPath("/question-account-assignment")).toBe("push-error-todos");
    expect(viewFromPath("/accounts")).toBe("accounts");
    expect(viewFromPath("/available-pool")).toBe("available-pool");
    expect(viewFromPath("/reserve-pool")).toBe("reserve-pool");
  });

  it("uses API pool status as the normal fallback", () => {
    expect(viewFromPath("/")).toBe("api-pools");
    expect(viewFromPath("/unknown-page")).toBe("api-pools");
  });
});
```

- [ ] **Step 2: Run the focused test and verify it fails**

Run: `npm test -- src/App.test.ts`

Expected: FAIL because `getVisibleNavigationGroups` and `viewFromPath` are not exported and `/` still defaults to `upload` on desktop.

- [ ] **Step 3: Add the hidden-view filter and safe default route**

In `frontend/src/App.tsx`, add the set after the navigation arrays:

```tsx
const hiddenNavItems = new Set<ViewName>([
  "upload",
  "todos",
  "push-error-todos",
  "accounts",
  "available-pool",
  "reserve-pool",
]);
```

Replace the layout-dependent default, export path resolution, and add the pure group helper:

```tsx
function defaultViewForLayout(): ViewName {
  return "api-pools";
}

export function viewFromPath(pathname: string): ViewName {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  if (normalized === "/") return defaultViewForLayout();
  const matched = Object.entries(viewPaths).find(([, path]) => path === normalized);
  return matched ? (matched[0] as ViewName) : pathAliases[normalized] || defaultViewForLayout();
}

export function getVisibleNavigationGroups(canViewPresence: boolean): Array<Array<[ViewName, string]>> {
  return [
    navItems,
    accountNavItems,
    poolNavItems,
    adminNavItems.filter(([key]) => key !== "presence" || canViewPresence),
  ]
    .map((group) => group.filter(([key]) => !hiddenNavItems.has(key)))
    .filter((group) => group.length > 0);
}
```

Render `getVisibleNavigationGroups(user?.role === "owner")` instead of the inline array of groups. In the `auth-expired` handler, replace `navigateToView("upload")` with `navigateToView("api-pools")`.

- [ ] **Step 4: Run the focused test and verify it passes**

Run: `npm test -- src/App.test.ts`

Expected: PASS with 3 passing tests.

- [ ] **Step 5: Run all frontend verification**

Run: `npm test`

Expected: all frontend tests pass.

Run: `npm run build`

Expected: TypeScript compilation and Vite production build complete successfully.

- [ ] **Step 6: Commit the implementation**

```bash
git add frontend/src/App.tsx frontend/src/App.test.ts
git commit -m "feat: temporarily hide account workflow navigation"
```
