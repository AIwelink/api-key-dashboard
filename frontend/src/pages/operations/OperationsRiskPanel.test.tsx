// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { OperationsRiskPanel } from "./OperationsRiskPanel";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const overview = {
  banned_count: 2,
  high_risk_count: 3,
  shared_ip_cluster_count: 1,
  failed_action_count: 0,
  settings: {
    detector_enabled: true,
    auto_ban_enabled: false,
    poll_interval_seconds: 60,
    ip_window_days: 7,
    shared_ip_min_accounts: 3,
  },
  source_health: [
    { source_stream: "audit_logs", status: "current", latest_observed_at: "2026-08-17T11:59:00Z", last_error_code: "ConnectionError", last_error_message: "源库连接超时" },
    { source_stream: "usage_logs", status: "stale", latest_observed_at: "2026-08-16T10:00:00Z" },
  ],
};

const account = {
  risk_account_id: "00000000-0000-0000-0000-000000000042",
  external_user_id: "42",
  email: "a.b@example.com",
  risk_status: "high_risk",
  risk_reasons: {
    email_rules: ["email_local_part_dot"],
    protection_reasons: ["verified_payment_history"],
  },
  shared_ip_count: 1,
  max_linked_account_count: 3,
  manual_override_active: false,
  last_detected_at: "2026-08-17T12:00:00Z",
};

describe("OperationsRiskPanel", () => {
  let root: Root | null = null;
  let container: HTMLDivElement | null = null;

  afterEach(async () => {
    await act(async () => root?.unmount());
    container?.remove();
    vi.unstubAllGlobals();
    root = null;
    container = null;
  });

  it("loads risk data only while active and explains stale and paid protection states", async () => {
    const fetchMock = installRiskFetch();
    await renderPanel({ active: false, role: "admin" });
    expect(fetchMock).not.toHaveBeenCalled();

    await renderPanel({ active: true, role: "admin" });

    expect(fetchMock).toHaveBeenCalledTimes(4);
    expect(container?.textContent).toContain("调用日志已过期");
    expect(container?.textContent).toContain("历史付费，仅人工审核");
    expect(container?.textContent).toContain("7 天内同一 IP 至少关联 3 个账号");
    expect(container?.textContent).toContain("源库连接超时");
    expect(container?.textContent).not.toContain("AIGCLink 风控");
  });

  it("lets operators manage risk actions while automatic bans stay unavailable", async () => {
    installRiskFetch();
    await renderPanel({ active: true, role: "operator" });

    expect(container?.querySelector('button[aria-label="确认封禁 a.b@example.com"]')).not.toBeNull();
    const toggles = [...(container?.querySelectorAll('input[type="checkbox"]') || [])];
    expect(toggles.length).toBe(1);
    expect((toggles[0] as HTMLInputElement).disabled).toBe(false);
    expect(container?.textContent).toContain("人工审批");
    expect(container?.textContent).not.toContain("当前角色为只读权限");
    const statusFilter = container?.querySelector<HTMLSelectElement>('select');
    expect(statusFilter?.querySelector('option[value="high_risk"]')?.textContent).toBe("高风险待审批");
    expect(statusFilter?.querySelector('option[value="ban_pending"]')?.textContent).toBe("高风险待审批");
  });

  it("requires a note before an admin can submit a manual ban", async () => {
    installRiskFetch();
    await renderPanel({ active: true, role: "admin" });

    const ban = container?.querySelector<HTMLButtonElement>(
      'button[aria-label="确认封禁 a.b@example.com"]',
    );
    await act(async () => ban?.click());

    const dialog = document.body.querySelector<HTMLElement>('[role="dialog"]');
    expect(dialog?.textContent).toContain("确认封禁");
    expect(dialog?.textContent).toContain("处置说明");
    expect(dialog?.querySelector<HTMLButtonElement>('button[type="submit"]')?.disabled).toBe(true);
  });

  it("pages accounts, IP clusters, and events through server offsets", async () => {
    const fetchMock = installRiskFetch({ paginated: true });
    await renderPanel({ active: true, role: "admin" });

    expect(requestUrls(fetchMock)).toContainEqual(expect.stringContaining("/accounts?limit=25&offset=0"));
    await clickButton('button[aria-label="下一页风险账号"]');
    expect(requestUrls(fetchMock)).toContainEqual(expect.stringContaining("/accounts?limit=25&offset=25"));
    expect(container?.textContent).toContain("第 2 / 3 页");

    await clickTab("共享 IP");
    await clickButton('button[aria-label="下一页共享 IP"]');
    expect(requestUrls(fetchMock)).toContainEqual(expect.stringContaining("/ip-clusters?limit=25&offset=25"));

    await clickTab("处置记录");
    await clickButton('button[aria-label="下一页处置记录"]');
    expect(requestUrls(fetchMock)).toContainEqual(expect.stringContaining("/events?limit=25&offset=25"));
  });

  it("shows sanitized action results and source conflicts in account details", async () => {
    installRiskFetch({ withDetail: true });
    await renderPanel({ active: true, role: "admin" });

    await clickButton('button[aria-label="查看 a.b@example.com"]');

    expect(container?.textContent).toContain("动作记录");
    expect(container?.textContent).toContain("封禁前 active · 2 个 API Key");
    expect(container?.textContent).toContain("部分解除");
    expect(container?.textContent).toContain("已恢复 1 · 冲突 1");
    expect(container?.textContent).toContain("源状态已变化");
    expect(container?.textContent).toContain("自动封禁已取消");
    expect(container?.textContent).toContain("未执行封禁");
    expect(container?.textContent).not.toContain("0 个 API Key 已停用");
    expect(container?.textContent).not.toContain("key-secret-id");
  });

  it("sends IP, event, and date filters to the server", async () => {
    const fetchMock = installRiskFetch();
    await renderPanel({ active: true, role: "admin" });

    await clickTab("共享 IP");
    const ipInput = container?.querySelector<HTMLInputElement>('input[aria-label="搜索共享 IP"]');
    await act(async () => {
      if (ipInput) {
        ipInput.value = "14.31.212.25";
        ipInput.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    await clickButton('button[aria-label="查询共享 IP"]');
    expect(requestUrls(fetchMock)).toContainEqual(expect.stringContaining("search=14.31.212.25"));

    await clickTab("处置记录");
    const eventType = container?.querySelector<HTMLSelectElement>('select[aria-label="事件类型"]');
    const startDate = container?.querySelector<HTMLInputElement>('input[aria-label="开始日期"]');
    const endDate = container?.querySelector<HTMLInputElement>('input[aria-label="结束日期"]');
    await act(async () => {
      if (eventType) {
        eventType.value = "auto_ban_succeeded";
        eventType.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (startDate) {
        startDate.value = "2026-08-01";
        startDate.dispatchEvent(new Event("change", { bubbles: true }));
      }
      if (endDate) {
        endDate.value = "2026-08-18";
        endDate.dispatchEvent(new Event("change", { bubbles: true }));
      }
      await Promise.resolve();
      await Promise.resolve();
    });
    const urls = requestUrls(fetchMock);
    expect(urls).toContainEqual(expect.stringContaining("event_type=auto_ban_succeeded"));
    expect(urls).toContainEqual(expect.stringContaining("start_date=2026-08-01"));
    expect(urls).toContainEqual(expect.stringContaining("end_date=2026-08-18"));
  });

  async function renderPanel({ active, role }: { active: boolean; role: string }) {
    if (!container) {
      container = document.createElement("div");
      document.body.appendChild(container);
      root = createRoot(container);
    }
    await act(async () => {
      root?.render(
        <OperationsRiskPanel
          active={active}
          role={role}
          showToast={() => undefined}
          token="token"
        />,
      );
      await Promise.resolve();
      await Promise.resolve();
    });
  }

  async function clickButton(selector: string) {
    const button = container?.querySelector<HTMLButtonElement>(selector);
    expect(button).not.toBeNull();
    await act(async () => {
      button?.click();
      await Promise.resolve();
      await Promise.resolve();
      await Promise.resolve();
    });
  }

  async function clickTab(label: string) {
    const button = [...(container?.querySelectorAll<HTMLButtonElement>('[role="tab"]') || [])]
      .find((item) => item.textContent === label);
    expect(button).not.toBeNull();
    await act(async () => button?.click());
  }
});

function installRiskFetch(options: { paginated?: boolean; withDetail?: boolean } = {}) {
  const fetchMock = vi.fn(async (input: string | URL | Request) => {
    const url = String(input);
    const payload = url.includes("/overview")
      ? overview
      : url.includes(`/accounts/${account.risk_account_id}`)
        ? options.withDetail
          ? {
              ...account,
              ip_evidence: [],
              events: [],
              actions: [{
                risk_action_id: "action-1",
                action_type: "manual_release",
                action_status: "succeeded",
                decision_reason: "校园网误报",
                source_user_status_before: "active",
                source_api_key_count_before: 2,
                result_summary: {
                  user_restored: true,
                  restored_key_count: 1,
                  conflicted_key_count: 1,
                  partial: true,
                },
                error_code: "SourceStateConflict",
                error_message: "源状态已变化",
                requested_by: "admin-1",
                requested_at: "2026-08-17T12:00:00Z",
                completed_at: "2026-08-17T12:00:01Z",
              }, {
                risk_action_id: "action-legacy-auto-ban",
                action_type: "auto_ban",
                action_status: "cancelled",
                decision_reason: "email_and_shared_ip",
                source_user_status_before: "active",
                source_api_key_count_before: 1,
                error_code: "AutoBanDisabled",
                error_message: "Automatic bans require manual approval",
                requested_by: "system:risk-detector",
                requested_at: "2026-08-17T11:59:00Z",
                completed_at: "2026-08-18T14:32:19Z",
              }],
            }
          : account
      : url.includes("/accounts?")
        ? { items: [account], total: options.paginated ? 60 : 1, limit: 25, offset: queryOffset(url) }
        : url.includes("/ip-clusters")
          ? { items: [], total: options.paginated ? 51 : 0, limit: 25, offset: queryOffset(url) }
          : url.includes("/events")
            ? { items: [], total: options.paginated ? 76 : 0, limit: 25, offset: queryOffset(url) }
            : {};
    return new Response(JSON.stringify(payload), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function queryOffset(url: string) {
  return Number(new URL(url, "http://localhost").searchParams.get("offset") || 0);
}

function requestUrls(fetchMock: ReturnType<typeof vi.fn>) {
  return fetchMock.mock.calls.map(([input]) => String(input));
}
