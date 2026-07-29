# Growth Workspace Vertical Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the Growth workspace's desktop side-by-side sections with a full-width vertical operational flow.

**Architecture:** Preserve all existing React state and API behavior. Express the page-level layout through explicit stacked section classes, while keeping the existing responsive field grids inside each form.

**Tech Stack:** React 19, TypeScript, CSS, Vitest.

---

## File Map

- Modify `frontend/src/pages/TrafficAnalysisPage.test.tsx`: assert the page-level sections use stacked layout semantics.
- Modify `frontend/src/pages/TrafficAnalysisPage.tsx`: replace side-by-side wrapper classes and order channel/campaign configuration by dependency.
- Modify `frontend/styles.css`: make all Growth page-level sections full-width vertical bands at every breakpoint.

## Task 1: Vertical Growth Workspace

- [ ] **Step 1: Write the failing layout contract test**

Add assertions to the existing workspace rendering tests:

```tsx
expect(html).toContain('growth-stacked-flow');
expect(html).not.toContain('growth-links-layout');
expect(html.indexOf('新建渠道')).toBeLessThan(html.indexOf('新建活动'));
```

- [ ] **Step 2: Verify RED**

Run:

```powershell
npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx
```

Expected: the new stacked-flow assertion fails because the current desktop wrappers still represent split layouts.

- [ ] **Step 3: Implement the vertical structure**

Use `growth-stacked-flow` for the promotion-link wrapper. Reorder the sources view as channel form, channel list, campaign form, campaign list. Keep the form field grids unchanged.

Update scoped CSS so these wrappers use one full-width track:

```css
.growth-stacked-flow,
.growth-source-editors,
.growth-source-lists {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 12px;
}
```

Remove desktop-only left borders and left/right padding between major sections. Use top borders and vertical spacing for separation.

- [ ] **Step 4: Verify GREEN**

Run:

```powershell
npm.cmd test -- --run src/pages/TrafficAnalysisPage.test.tsx src/App.test.ts
npm.cmd run build
```

Expected: all focused tests pass and the production build exits successfully.

- [ ] **Step 5: Inspect responsive layout**

At desktop and 375px widths, inspect all three tabs. Confirm the major sections are vertically ordered and the document has no horizontal overflow.
