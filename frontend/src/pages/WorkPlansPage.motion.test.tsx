// @vitest-environment jsdom

import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it, vi } from "vitest";

import { api } from "../api/client";
import { WorkPlansPage } from "./WorkPlansPage";
import type { WorkPlanHistoryResponse, WorkPlanScheduleResponse } from "./workPlans/types";

vi.mock("../api/client", () => ({ api: vi.fn() }));

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const SCHEDULE: WorkPlanScheduleResponse = {
  members: [{
    member_id: "member@example.com",
    member_name: "成员一",
    is_online: true,
    active_clients: 1,
    last_seen_at: "2026-08-16T02:00:00+00:00",
    active_plan: null,
    collaboration_status: "online",
  }],
  plans: [{
    id: "plan-1",
    member_id: "member@example.com",
    member_name: "成员一",
    plan_type: "work",
    plan_date: "2026-08-17",
    start_minute: 540,
    end_minute: 1080,
    note: null,
    status: "active",
    is_cancelled: false,
    created_at: "2026-08-16T00:00:00+00:00",
    updated_at: "2026-08-16T00:00:00+00:00",
  }],
  start_date: "2026-08-16",
  end_date: "2026-08-22",
  observed_at: "2026-08-16T02:00:00+00:00",
  timezone: "Asia/Shanghai",
  total: 1,
  has_more: false,
  next_cursor: null,
};

const HISTORY: WorkPlanHistoryResponse = {
  items: [],
  total: 0,
  has_more: false,
  next_cursor: null,
};

describe("WorkPlansPage motion state", () => {
  let root: Root | null = null;
  let container: HTMLDivElement | null = null;

  afterEach(async () => {
    await act(async () => root?.unmount());
    container?.remove();
    vi.clearAllMocks();
    root = null;
    container = null;
  });

  it("renders one compact status surface during the initial schedule load", async () => {
    vi.mocked(api).mockImplementation((path) => {
      if (path.startsWith("/work-plans/schedule")) return new Promise(() => undefined) as never;
      return Promise.resolve(HISTORY) as never;
    });
    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);

    await act(async () => root?.render(
      <WorkPlansPage
        currentUser={{ email: "member@example.com", role: "viewer" }}
        showToast={() => undefined}
        token="token"
      />,
    ));

    const status = container.querySelector('[role="status"]');
    expect(status?.classList.contains("work-plan-loading")).toBe(true);
    expect(status?.textContent).toContain("正在同步排班");
    expect(container.querySelectorAll(".work-plan-loading-mark")).toHaveLength(1);
  });

  it("keeps the schedule visible and marks the page busy while refresh is pending", async () => {
    let resolveRefresh: ((value: WorkPlanScheduleResponse) => void) | null = null;
    const refreshRequest = new Promise<WorkPlanScheduleResponse>((resolve) => {
      resolveRefresh = resolve;
    });
    let scheduleCalls = 0;
    vi.mocked(api).mockImplementation((path) => {
      if (path.startsWith("/work-plans/schedule")) {
        scheduleCalls += 1;
        return (scheduleCalls === 1 ? Promise.resolve(SCHEDULE) : refreshRequest) as never;
      }
      return Promise.resolve(HISTORY) as never;
    });

    container = document.createElement("div");
    document.body.appendChild(container);
    root = createRoot(container);
    await act(async () => root?.render(
      <WorkPlansPage
        currentUser={{ email: "member@example.com", role: "viewer" }}
        showToast={() => undefined}
        token="token"
      />,
    ));
    await act(async () => undefined);

    expect(container.querySelector(".work-plan-schedule")).not.toBeNull();
    const refreshButton = container.querySelector<HTMLButtonElement>('button[aria-label="刷新"]');
    await act(async () => refreshButton?.click());

    expect(container.querySelector(".work-plan-refresh-line.active")).not.toBeNull();
    expect(container.querySelector(".work-plan-schedule")).not.toBeNull();
    expect(container.querySelector(".work-plan-page")?.getAttribute("aria-busy")).toBe("true");

    await act(async () => resolveRefresh?.(SCHEDULE));
  });
});
