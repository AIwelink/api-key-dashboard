# 推广链接双域名复制 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task with review checkpoints.

**Goal:** 在推广链接列表中为每条推广码提供上下排列的主页域名和 API 域名复制按钮。

**Architecture:** 保留后端返回的 `public_url` 作为主页链接，前端用同一条记录的 `code` 生成固定的 `https://api.aiwelink.cc/r/{code}`。复制动作继续由页面统一处理，按钮只传入目标 URL 和区分提示，不创建第二条推广记录。

**Tech Stack:** React 19, TypeScript, Vite, Vitest。

---

### Task 1: Add the failing dual-copy contract test

**Files:**
- Modify: `frontend/src/pages/TrafficAnalysisPage.test.tsx`
- Test: `frontend/src/pages/TrafficAnalysisPage.test.tsx`

- [ ] **Step 1: Add the expected URL helper and two-button assertions to the existing link-list test.**

Import `apiTrackingUrl` from `TrafficAnalysisPage`, then extend the promotion-link rendering test with:

```tsx
expect(apiTrackingUrl("7km4q2xd")).toBe("https://api.aiwelink.cc/r/7km4q2xd");
expect(html).toContain("复制主页链接");
expect(html).toContain("复制 API 链接");
```

- [ ] **Step 2: Run the focused test and verify it fails for the missing helper/buttons.**

Run from `frontend`:

```bash
npm test -- --run src/pages/TrafficAnalysisPage.test.tsx
```

Expected: FAIL because `apiTrackingUrl` and the two copy controls are not implemented yet.

### Task 2: Implement the two-line copy controls

**Files:**
- Modify: `frontend/src/pages/TrafficAnalysisPage.tsx:70-100, 900-925, 930-970, 1190-1210`

- [ ] **Step 1: Add a small pure helper for the API tracking URL.**

Add the fixed API origin and export:

```ts
export const API_TRACKING_ORIGIN = "https://api.aiwelink.cc";

export function apiTrackingUrl(code: string) {
  return `${API_TRACKING_ORIGIN}/r/${encodeURIComponent(code)}`;
}
```

- [ ] **Step 2: Allow copy feedback to identify the copied domain.**

Change the page copy handler and workspace callback type to accept an optional success message. Keep the clipboard behavior unchanged and use the existing failure toast.

```ts
const copyLink = async (url: string, successMessage = "推广链接已复制") => {
  try {
    await navigator.clipboard.writeText(url);
    showToast(successMessage);
  } catch {
    showToast("复制失败，请手动复制", true);
  }
};
```

- [ ] **Step 3: Render both copy buttons in a vertical group directly after the displayed homepage URL.**

Keep the existing anchor and right-side edit/status controls. Add:

```tsx
<div className="growth-link-copy-actions">
  <button type="button" onClick={() => onCopyLink(link.public_url, "主页推广链接已复制")}>复制主页链接</button>
  <button type="button" onClick={() => onCopyLink(apiTrackingUrl(link.code), "API 推广链接已复制")}>复制 API 链接</button>
</div>
```

The URL helper must encode the code segment, and the API button must use the same `link.code` as the homepage link.

- [ ] **Step 4: Add focused styling for the two-line group without changing the row action area.**

Use the existing growth-list styles and add a narrow vertical flex group, keeping both buttons immediately adjacent to the homepage URL and readable on narrow screens.

### Task 3: Verify, commit, and publish

**Files:**
- Modify: `frontend/src/pages/TrafficAnalysisPage.test.tsx`
- Modify: `frontend/src/pages/TrafficAnalysisPage.tsx`
- Modify: `frontend/styles.css` only if the focused group needs a new rule

- [ ] **Step 1: Run the focused frontend test.**

```bash
npm test -- --run src/pages/TrafficAnalysisPage.test.tsx
```

Expected: all tests in the file pass.

- [ ] **Step 2: Run the complete frontend test suite and production build.**

```bash
npm test
npm run build
```

Expected: Vitest exits successfully and the TypeScript/Vite build exits with code 0.

- [ ] **Step 3: Inspect the diff and commit only the implementation files.**

```bash
git diff --check
git add frontend/src/pages/TrafficAnalysisPage.tsx frontend/src/pages/TrafficAnalysisPage.test.tsx frontend/styles.css
git commit -m "feat: add API promotion link copy"
```

- [ ] **Step 4: Push the current branch and open a draft PR.**

```bash
git push -u origin $(git branch --show-current)
gh pr create --draft --fill --head $(git branch --show-current)
```

The PR body must state that the same tracking code now has separate homepage/API copy actions, no backend schema changed, and include the test/build commands.
