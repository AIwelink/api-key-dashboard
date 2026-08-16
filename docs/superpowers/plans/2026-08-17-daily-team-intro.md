# Daily Team Intro Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show a polished light-gradient AIwelink encouragement intro once per authenticated member per Shanghai calendar day.

**Architecture:** A pure storage helper owns Shanghai date and per-member claiming. A focused React gate claims once and mounts a self-contained animated overlay; `App` only supplies the authenticated user and a stable member key. CSS owns the visual timeline, while React owns dismissal, timers, scroll locking, and cleanup.

**Tech Stack:** React 19, TypeScript, CSS animations, Vitest, jsdom, Playwright QA.

---

## File Structure

- Create `frontend/src/components/dailyIntro/dailyIntro.ts`: date, identity, storage key, and atomic daily claim helpers.
- Create `frontend/src/components/dailyIntro/dailyIntro.test.ts`: time-zone and persistence behavior.
- Create `frontend/src/components/dailyIntro/DailyTeamIntro.tsx`: authenticated gate and animated overlay lifecycle.
- Create `frontend/src/components/dailyIntro/DailyTeamIntro.css`: light-gradient visual system and responsive/reduced-motion rules.
- Create `frontend/src/components/dailyIntro/DailyTeamIntro.test.tsx`: rendered content, auto completion, Escape, and scroll restoration.
- Modify `frontend/src/App.tsx`: mount the gate before `.app-shell` when token and user exist.

### Task 1: Daily Claim Helper

**Files:**
- Create: `frontend/src/components/dailyIntro/dailyIntro.ts`
- Test: `frontend/src/components/dailyIntro/dailyIntro.test.ts`

- [ ] **Step 1: Write the failing storage tests**

```ts
it("changes day exactly at Shanghai midnight", () => {
  expect(shanghaiDateKey(new Date("2026-08-16T15:59:59Z"))).toBe("2026-08-16");
  expect(shanghaiDateKey(new Date("2026-08-16T16:00:00Z"))).toBe("2026-08-17");
});

it("claims once per member and allows a new claim on the next Shanghai day", () => {
  const storage = memoryStorage();
  const member = { id: "member-1", email: "member@example.com" };
  expect(claimDailyIntro(storage, member, new Date("2026-08-16T16:00:00Z"))).toBe(true);
  expect(claimDailyIntro(storage, member, new Date("2026-08-17T15:59:59Z"))).toBe(false);
  expect(claimDailyIntro(storage, member, new Date("2026-08-17T16:00:00Z"))).toBe(true);
});
```

- [ ] **Step 2: Run the test and verify RED**

Run: `npm test -- src/components/dailyIntro/dailyIntro.test.ts`

Expected: FAIL because `dailyIntro.ts` does not exist.

- [ ] **Step 3: Implement the helper**

```ts
export interface DailyIntroStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function shanghaiDateKey(now: Date): string {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(now);
  const value = (type: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === type)?.value ?? "";
  return `${value("year")}-${value("month")}-${value("day")}`;
}

export function dailyIntroIdentity(member: { id?: string; email: string }): string {
  return (member.id?.trim() || member.email.trim().toLowerCase());
}

export function claimDailyIntro(storage: DailyIntroStorage, member: { id?: string; email: string }, now = new Date()): boolean {
  const date = shanghaiDateKey(now);
  const key = `aiwelink.daily-team-intro.v1:${encodeURIComponent(dailyIntroIdentity(member))}`;
  try {
    if (storage.getItem(key) === date) return false;
    storage.setItem(key, date);
  } catch {
    return true;
  }
  return true;
}
```

- [ ] **Step 4: Run the focused test and verify GREEN**

Run: `npm test -- src/components/dailyIntro/dailyIntro.test.ts`

Expected: all daily claim tests pass.

### Task 2: Animated Intro Overlay

**Files:**
- Create: `frontend/src/components/dailyIntro/DailyTeamIntro.tsx`
- Create: `frontend/src/components/dailyIntro/DailyTeamIntro.css`
- Test: `frontend/src/components/dailyIntro/DailyTeamIntro.test.tsx`

- [ ] **Step 1: Write failing component tests**

```tsx
it("renders the real logo, complete quote, and accessible skip control", () => {
  const html = renderToStaticMarkup(<DailyTeamIntro onComplete={() => undefined} />);
  expect(html).toContain('role="dialog"');
  expect(html).toContain("世事浮沉，皆为淬炼；商海浩瀚，唯有坚毅。");
  expect(html).toContain("愿君心有赤焰，足履薄冰，终见日月新天。");
  expect(html).toContain('aria-label="跳过开场"');
});

it("exits on Escape and restores document scrolling", async () => {
  document.documentElement.style.overflow = "clip";
  renderIntro();
  expect(document.documentElement.style.overflow).toBe("hidden");
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
  await vi.advanceTimersByTimeAsync(400);
  expect(onComplete).toHaveBeenCalledOnce();
  unmountIntro();
  expect(document.documentElement.style.overflow).toBe("clip");
});
```

- [ ] **Step 2: Run the component test and verify RED**

Run: `npm test -- src/components/dailyIntro/DailyTeamIntro.test.tsx`

Expected: FAIL because the component does not exist.

- [ ] **Step 3: Implement the lifecycle and gate**

```tsx
export function DailyTeamIntro({ onComplete }: { onComplete: () => void }) {
  const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
  const [stage, setStage] = useState<"opening" | "reduced" | "exiting">(reducedMotion ? "reduced" : "opening");
  const completed = useRef(false);
  const timers = useRef<number[]>([]);
  const beginExit = useCallback((duration: number) => {
    if (completed.current) return;
    completed.current = true;
    timers.current.forEach(window.clearTimeout);
    setStage("exiting");
    timers.current = [window.setTimeout(onComplete, duration)];
  }, [onComplete]);

  useEffect(() => {
    const root = document.documentElement;
    const previousOverflow = root.style.overflow;
    root.style.overflow = "hidden";
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") beginExit(360);
    };
    window.addEventListener("keydown", handleKeyDown);
    timers.current = [window.setTimeout(
      () => beginExit(reducedMotion ? 300 : 900),
      reducedMotion ? 300 : 3000,
    )];
    return () => {
      timers.current.forEach(window.clearTimeout);
      window.removeEventListener("keydown", handleKeyDown);
      root.style.overflow = previousOverflow;
    };
  }, [beginExit, reducedMotion]);

  return (
    <section aria-label="AIwelink 每日团队寄语" aria-modal="true" className="daily-team-intro" data-stage={stage} role="dialog">
      <button aria-label="跳过开场" className="daily-team-intro-skip" type="button"><X /></button>
      <div className="daily-team-intro-content">
        <img alt="AIwelink" className="daily-team-intro-logo" src={logoUrl} />
        <p aria-label={DAILY_TEAM_MESSAGE} className="daily-team-intro-message">
          <span>世事浮沉，皆为淬炼；商海浩瀚，唯有坚毅。</span>
          <span>愿君心有赤焰，足履薄冰，终见日月新天。</span>
        </p>
      </div>
    </section>
  );
}
```

`DailyTeamIntroGate` calls `claimDailyIntro` in its state initializer and renders the overlay only when the claim succeeds. The CSS uses one full-screen pearl/fog-blue/rose linear gradient layer, 0.96-to-1 logo focus, two delayed message groups, a 900ms exit, fixed desktop/mobile font sizes, and a reduced-motion override with no blur or translation.

- [ ] **Step 4: Run the component test and verify GREEN**

Run: `npm test -- src/components/dailyIntro/DailyTeamIntro.test.tsx`

Expected: overlay lifecycle tests pass with no act warnings.

### Task 3: Authenticated App Integration

**Files:**
- Modify: `frontend/src/App.tsx`
- Test: `frontend/src/components/dailyIntro/DailyTeamIntro.test.tsx`

- [ ] **Step 1: Add a failing integration assertion**

```ts
const appSource = readFileSync(new URL("../../App.tsx", import.meta.url), "utf8");
expect(appSource).toContain("<DailyTeamIntroGate");
expect(appSource).toContain("key={dailyIntroIdentity(user)}");
expect(appSource.indexOf("<DailyTeamIntroGate")).toBeLessThan(appSource.indexOf('<div className={`app-shell'));
```

- [ ] **Step 2: Run the component test and verify RED**

Run: `npm test -- src/components/dailyIntro/DailyTeamIntro.test.tsx`

Expected: FAIL because App has no gate.

- [ ] **Step 3: Mount the gate before the shell**

```diff
 return (
+  <>
+    {token && user ? <DailyTeamIntroGate key={dailyIntroIdentity(user)} user={user} /> : null}
     <div className={`app-shell ${sidebarCollapsed ? "sidebar-collapsed" : ""}`}>
       <aside className="sidebar">
@@
       </main>
     </div>
+  </>
 );
```

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `npm test -- src/components/dailyIntro/dailyIntro.test.ts src/components/dailyIntro/DailyTeamIntro.test.tsx src/App.test.ts`

Expected: all focused tests pass.

### Task 4: Verification and Publishing

**Files:**
- Verify only; no new production files.

- [ ] **Step 1: Run all automated checks**

Run: `npm test`

Expected: all test files pass.

Run: `npm run build`

Expected: TypeScript and Vite build exit 0; existing chunk-size warning may remain.

Run: `git diff --check`

Expected: no output.

- [ ] **Step 2: Verify the rendered experience**

Run the Vite server and use browser automation with an authenticated mocked user. Verify 1440x960 and 390x844 viewports, full quote visibility, no horizontal overflow, Escape/skip, 3.9-second automatic completion, same-day refresh suppression, next-day replay, reduced motion, and zero relevant console errors.

- [ ] **Step 3: Commit and update the existing PR**

```bash
git add frontend/src/App.tsx frontend/src/components/dailyIntro docs/superpowers/plans/2026-08-17-daily-team-intro.md
git commit -m "feat: add daily team intro"
git push origin codex/flexible-work-plans
```

Expected: the existing PR targeting `achernar/dev` includes the daily intro commits.
