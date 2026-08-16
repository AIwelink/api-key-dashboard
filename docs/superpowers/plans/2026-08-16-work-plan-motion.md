# 工作计划页流畅动效 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 在不增加动画依赖的前提下，为工作计划页补齐页面入场、刷新、筛选、时间条、抽屉、弹层和确认反馈，并降低加载时的白屏与布局跳动。

**Architecture:** CSS-first 动效，React 只提供稳定的阶段 class、刷新状态和发生变化的 segment key。新增一个纯函数 motion helper 负责错峰延迟和 segment 变化识别；`WorkPlansPage` 编排页面级状态，`WorkPlanSchedule` 编排时间条局部状态，抽屉和弹层沿用现有焦点/portal 结构。

**Tech Stack:** React 19, TypeScript, Vite, CSS keyframes/transitions, Vitest + jsdom。

---

### Task 1: 建立动效纯函数和失败测试

**Files:**
- Create: `frontend/src/pages/workPlans/workPlanMotion.ts`
- Create: `frontend/src/pages/workPlans/workPlanMotion.test.ts`

- [ ] **Step 1: Write the failing tests**

测试以下契约：首屏前三行分别使用 220/260/300ms，之后统一使用 260ms；相同 segment 快照不反馈；新增、状态变化或时间变化的 segment key 会被识别；空快照和重复 key 不产生错误。

```ts
import { describe, expect, it } from "vitest";
import { findChangedSegmentKeys, memberEntryDelay } from "./workPlanMotion";

describe("work plan motion helpers", () => {
  it("stagger only the first three visible member rows", () => {
    expect([0, 1, 2, 3].map(memberEntryDelay)).toEqual([220, 260, 300, 260]);
  });

  it("detects new and changed timeline segments", () => {
    const previous = [{ key: "member:active:a", state: "active", startAt: "09", endAt: "12" }];
    const next = [
      previous[0],
      { key: "member:cancelled:b", state: "cancelled", startAt: "12", endAt: "13" },
      { key: "member:active:a", state: "active", startAt: "09", endAt: "13" },
    ];
    expect(findChangedSegmentKeys(previous, next)).toEqual(new Set([
      "member:cancelled:b",
      "member:active:a",
    ]));
  });

  it("treats an identical empty snapshot as unchanged", () => {
    expect(findChangedSegmentKeys([], [])).toEqual(new Set());
  });
});
```

- [ ] **Step 2: Run the focused test and verify RED**

Run from `frontend`:

```bash
npm test -- --run src/pages/workPlans/workPlanMotion.test.ts
```

Expected: FAIL because `workPlanMotion.ts` does not exist yet.

- [ ] **Step 3: Write the minimal helper**

```ts
export type SegmentMotionSnapshot = {
  key: string;
  state: string;
  startAt: string;
  endAt: string;
};

export function memberEntryDelay(index: number): number {
  return index < 3 ? 220 + index * 40 : 260;
}

export function findChangedSegmentKeys(
  previous: readonly SegmentMotionSnapshot[],
  next: readonly SegmentMotionSnapshot[],
): Set<string> {
  const previousByKey = new Map(previous.map((segment) => [segment.key, segment]));
  return new Set(next
    .filter((segment) => {
      const old = previousByKey.get(segment.key);
      return !old
        || old.state !== segment.state
        || old.startAt !== segment.startAt
        || old.endAt !== segment.endAt;
    })
    .map((segment) => segment.key));
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run the same command. Expected: 3 tests pass.

### Task 2: 页面级加载、刷新和入场状态

**Files:**
- Modify: `frontend/src/pages/WorkPlansPage.tsx`
- Modify: `frontend/src/pages/WorkPlansPage.css`
- Modify: `frontend/src/pages/WorkPlansPage.test.tsx`

- [ ] **Step 1: Write the failing component tests**

Add tests that render the page with the existing API mock and assert:

```tsx
it("keeps the schedule visible while a refresh is pending", async () => {
  expect(document.querySelector(".work-plan-refresh-line.active")).not.toBeNull();
  expect(document.querySelector(".work-plan-schedule")).not.toBeNull();
});

it("marks the page busy without replacing the current schedule", () => {
  expect(document.querySelector(".work-plan-page")?.getAttribute("aria-busy")).toBe("true");
});
```

- [ ] **Step 2: Run focused tests and verify RED**

```bash
npm test -- --run src/pages/WorkPlansPage.test.tsx
```

Expected: FAIL because the refresh line and `aria-busy` state are not rendered.

- [ ] **Step 3: Implement minimal page state classes**

Add a `pageEntered` state that flips on the next task after mount, use `is-entered`/`is-refreshing` class names, add `aria-busy={loading || refreshing || mutationBusy}`, and render:

```tsx
<section
  aria-busy={loading || refreshing || mutationBusy}
  className={`view work-plan-page ${pageEntered ? "is-entered" : ""} ${isPending ? "is-transitioning" : ""}`}
>
  <div aria-hidden="true" className={`work-plan-refresh-line ${refreshing ? "active" : ""}`} />
```

Use `useTransition` around range and member filter state updates so the interaction remains responsive while the schedule request starts. Keep the existing schedule state rendered during refresh.

- [ ] **Step 4: Add CSS-first staged entrance and anti-flicker visuals**

Add scoped tokens and classes to `WorkPlansPage.css`:

```css
.work-plan-page {
  --wp-motion-fast: 160ms;
  --wp-motion-standard: 220ms;
  --wp-motion-drawer: 280ms;
  --wp-motion-page: 560ms;
  --wp-motion-stagger: 40ms;
  --wp-ease-enter: cubic-bezier(.16, 1, .3, 1);
  --wp-ease-state: cubic-bezier(.22, 1, .36, 1);
  position: relative;
}

.work-plan-page > :not(.work-plan-refresh-line) {
  opacity: 0;
  transform: translate3d(0, 8px, 0);
}

.work-plan-page.is-entered > :not(.work-plan-refresh-line) {
  animation: work-plan-page-enter var(--wp-motion-page) var(--wp-ease-enter) both;
}

.work-plan-refresh-line {
  position: absolute;
  top: -1px;
  left: 0;
  z-index: 12;
  width: 100%;
  height: 2px;
  pointer-events: none;
  opacity: 0;
  transform: scaleX(0);
  transform-origin: left;
  background: var(--accent);
}

.work-plan-refresh-line.active {
  opacity: 1;
  animation: work-plan-refresh 1.15s var(--wp-ease-state) infinite;
}

@keyframes work-plan-page-enter {
  to { opacity: 1; transform: translate3d(0, 0, 0); }
}

@keyframes work-plan-refresh {
  0% { transform: scaleX(0); }
  72%, 100% { transform: scaleX(1); }
}
```

Use child-specific delays for header, summary, toolbar, stale notice, and schedule frame. Do not animate the full table with blur or box-shadow.

- [ ] **Step 5: Run focused tests and existing work-plan tests**

```bash
npm test -- --run src/pages/WorkPlansPage.test.tsx src/pages/workPlans/WorkPlanStyles.test.ts
```

Expected: all focused tests pass.

### Task 3: Schedule rows, segments and loading surface

**Files:**
- Modify: `frontend/src/pages/workPlans/WorkPlanSchedule.tsx`
- Modify: `frontend/src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx`
- Modify: `frontend/src/pages/WorkPlansPage.css`

- [ ] **Step 1: Write failing schedule tests**

Add assertions that a newly rendered segment has `work-plan-segment-enter`, a changed segment has `work-plan-segment-feedback`, and member rows expose the `--work-plan-entry-delay` style. Verify loading markup contains a status role and does not render an unbounded animated list.

- [ ] **Step 2: Run focused schedule tests and verify RED**

```bash
npm test -- --run src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx
```

Expected: FAIL because the classes and loading markup are not implemented.

- [ ] **Step 3: Implement snapshot comparison and stable segment classes**

In `WorkPlanSchedule`, keep a ref of segment snapshots, derive changed keys with `findChangedSegmentKeys`, clear the feedback set with a single timeout, and render segment classes without changing the key used for focus restoration. Use `memberEntryDelay(index)` for only the first three visible rows; give later rows the same 260ms delay.

- [ ] **Step 4: Implement the lightweight loading/empty surface**

Replace plain `加载中...` with a fixed-size status surface containing a small animated mark and line. Keep `min-height` stable so the page does not jump and retain the existing empty-state copy when the request resolves with no records.

- [ ] **Step 5: Add segment and row CSS**

Use `opacity`, `transform`, and `transform-origin: left` only:

```css
.work-plan-gantt-row,
.work-plan-mobile-member {
  animation: work-plan-row-enter 420ms var(--wp-ease-enter) both;
  animation-delay: var(--work-plan-entry-delay, 260ms);
}

.work-plan-segment-enter {
  animation: work-plan-segment-enter 420ms var(--wp-ease-state) both;
  transform-origin: left center;
}

.work-plan-segment-feedback {
  animation: work-plan-segment-feedback 620ms ease-out both;
}

@keyframes work-plan-segment-enter {
  from { opacity: 0; transform: scaleX(0); }
  to { opacity: 1; transform: scaleX(1); }
}

@keyframes work-plan-segment-feedback {
  0% { filter: brightness(1); transform: scaleX(1); }
  34% { filter: brightness(1.08); transform: scaleX(1.015); }
  100% { filter: brightness(1); transform: scaleX(1); }
}
```

Use `filter` only for the short local feedback animation, never for page/table entry. Respect existing segment hover transform by moving the feedback effect to a wrapper if selector conflicts.

- [ ] **Step 6: Run focused schedule and style tests**

```bash
npm test -- --run src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx src/pages/workPlans/WorkPlanStyles.test.ts src/pages/workPlans/workPlanMotion.test.ts
```

Expected: all tests pass.

### Task 4: Drawers, advanced dates, priority, confirmation and reduced motion

**Files:**
- Modify: `frontend/src/pages/WorkPlansPage.css`
- Modify: `frontend/styles.css`
- Modify: `frontend/src/pages/workPlans/WorkPlanStyles.test.ts`

- [ ] **Step 1: Write failing style assertions**

Assert that drawer content, advanced dates, detail popover, priority popover, confirmation dialog, and toast have scoped enter transitions; assert reduced-motion rules disable both animation and transition.

- [ ] **Step 2: Run style tests and verify RED**

```bash
npm test -- --run src/pages/workPlans/WorkPlanStyles.test.ts
```

Expected: FAIL on the new selector assertions.

- [ ] **Step 3: Implement drawer and popover motion**

Add staggered drawer body children, `.work-plan-more-dates` expand entry, `.work-plan-detail-popover` entry, and `.work-plan-priority-popover` entry. Keep the existing layer visibility delay and focus behavior unchanged.

- [ ] **Step 4: Implement confirmation and toast motion**

Add `opacity + translateY` entry to `.confirm-backdrop/.confirm-dialog` and `.toast`, with the existing global z-index and mobile positions preserved. Do not modify confirmation behavior or copy.

- [ ] **Step 5: Add global reduced-motion overrides**

Extend the existing `@media (prefers-reduced-motion: reduce)` blocks so the confirmation and toast transitions become immediate, while loading text remains visible and focus management is unchanged.

- [ ] **Step 6: Run focused style/component tests**

```bash
npm test -- --run src/pages/workPlans/WorkPlanStyles.test.ts src/pages/WorkPlansPage.test.tsx src/pages/workPlans/WorkPlanSchedule.interaction.test.tsx
```

Expected: all tests pass.

### Task 5: Full verification and browser QA

**Files:**
- Modify only if verification exposes a defect in the files above.

- [ ] **Step 1: Run the complete frontend test suite**

```bash
npm test
```

Expected: all frontend tests pass.

- [ ] **Step 2: Run the production build**

```bash
npm run build
```

Expected: TypeScript and Vite build complete successfully.

- [ ] **Step 3: Start the Vite dev server**

```bash
npm run dev -- --host 127.0.0.1
```

Use the actual local URL returned by Vite for browser validation.

- [ ] **Step 4: Validate the desktop flow**

Check app load -> work plan page enters -> range/filter change -> refresh while old data stays visible -> click a time segment -> detail opens -> edit/cancel action -> drawer transition -> success toast.

- [ ] **Step 5: Validate the mobile flow**

Check the mobile member list, bottom drawer, quick dates, more dates expansion, priority popover, and confirmation dialog. Verify no page-level horizontal overflow or overlapping text.

- [ ] **Step 6: Validate reduced motion**

Use browser emulation or a temporary media override for `prefers-reduced-motion: reduce`; assert final states are visible immediately and no continuously running animations remain.

- [ ] **Step 7: Review the diff and commit**

```bash
git diff --check
git status --short
git add frontend/src/pages frontend/styles.css docs/superpowers/plans/2026-08-16-work-plan-motion.md
git commit -m "feat: smooth work plan interactions"
```
