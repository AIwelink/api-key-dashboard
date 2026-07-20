# Temporarily Hidden Navigation Design

## Goal

Temporarily remove these six pages from the application sidebar without deleting or disabling the pages:

- 上传账号
- 代办与错误账号处理
- 疑问账号分配面板
- 账号列表
- 可用池
- 使用备选池

## Design

Keep the page components, `ViewName` values, canonical paths, legacy path aliases, and conditional page rendering unchanged. Add a single set of hidden view names in `frontend/src/App.tsx` and filter every navigation group through it before rendering. Do not render groups that become empty after filtering, so the sidebar has no blank separators.

Both desktop and mobile layouts use the same filtered navigation data. Direct navigation to any of the six existing URLs remains supported.

Change the default view for `/` to `api-pools` on every layout. Authentication-expiry navigation also returns to `api-pools`. This prevents normal application entry from opening a page whose navigation item is hidden, while explicit direct URLs continue to work.

## Error Handling

No API or backend behavior changes. Unknown paths continue to use the existing default-view fallback, which will now resolve to `api-pools`.

## Verification

- Add a focused unit test for navigation visibility and route availability.
- Run the frontend test suite.
- Run the TypeScript and Vite production build.
- Confirm the six labels are absent from rendered navigation data while their paths still resolve to the original views.

## Restoration

Remove view names from the hidden-view set, or delete the set and filtering when all entries are restored. No page imports, paths, aliases, or page implementations need to be reconstructed.
