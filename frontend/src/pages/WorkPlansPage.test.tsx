import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { MyPlansDrawer } from "./workPlans/MyPlansDrawer";
import {
  createInitialWorkPlanDraft,
  WorkPlanFormDrawer,
  workPlanDraftReducer,
} from "./workPlans/WorkPlanFormDrawer";
import { canManagePlan, WorkPlanSchedule } from "./workPlans/WorkPlanSchedule";
import type { WorkPlan, WorkPlanScheduleResponse } from "./workPlans/types";
import { beginCreate, initialRequestState } from "./WorkPlansPage";

const PLAN: WorkPlan = {
  id: "plan-1",
  member_id: "member@example.com",
  member_name: "成员一",
  plan_type: "work",
  plan_date: "2026-08-16",
  start_minute: 570,
  end_minute: 1080,
  note: "发布前确认",
  status: "active",
  is_cancelled: false,
  created_at: "2026-08-15T00:00:00+00:00",
  updated_at: "2026-08-15T00:00:00+00:00",
};

const SCHEDULE: WorkPlanScheduleResponse = {
  members: [
    {
      member_id: PLAN.member_id,
      member_name: PLAN.member_name,
      is_online: false,
      active_clients: 0,
      last_seen_at: "2026-08-15T01:00:00+00:00",
      active_plan: null,
      collaboration_status: "offline",
    },
  ],
  plans: [PLAN],
  start_date: "2026-08-16",
  end_date: "2026-08-22",
  observed_at: "2026-08-15T02:00:00+00:00",
  timezone: "Asia/Shanghai",
};

describe("work plan components", () => {
  it("reuses one idempotency key while a create request is in flight", () => {
    const started = beginCreate(initialRequestState, "request-key");
    expect(beginCreate(started, "different-key").idempotencyKey).toBe("request-key");
  });

  it("temporary unavailable keeps one date and disables bulk modes", () => {
    const draft = {
      ...createInitialWorkPlanDraft("2026-08-15"),
      selectedDates: ["2026-08-15", "2026-08-16"],
      moreDateMode: "multiple" as const,
    };

    const next = workPlanDraftReducer(draft, {
      type: "set-plan-type",
      value: "temporary_unavailable",
    });

    expect(next.selectedDates).toEqual(["2026-08-15"]);
    expect(next.moreDateMode).toBe("single");
  });

  it("renders the sticky member schedule and readable time range", () => {
    const html = renderToStaticMarkup(
      <WorkPlanSchedule
        currentUser={{ email: "viewer@example.com", role: "viewer" }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        range="7d"
        response={SCHEDULE}
      />,
    );

    expect(html).toContain("work-plan-member-cell");
    expect(html).toContain("09:30 - 18:00");
    expect(html).toContain("work-plan-mobile-list");
  });

  it("shows manager controls for another member without exposing them to ordinary viewers", () => {
    expect(canManagePlan({ email: "admin@example.com", role: "admin" }, PLAN)).toBe(true);
    expect(canManagePlan({ email: "viewer@example.com", role: "viewer" }, PLAN)).toBe(false);
    expect(canManagePlan({ email: PLAN.member_id, role: "viewer" }, PLAN)).toBe(true);
  });

  it("renders cancelled history as traceable but not editable", () => {
    const html = renderToStaticMarkup(
      <MyPlansDrawer
        busy={false}
        items={[{ ...PLAN, status: "cancelled", is_cancelled: true, cancelled_at: PLAN.updated_at }]}
        onCancel={() => undefined}
        onClose={() => undefined}
        onEdit={() => undefined}
        open
      />,
    );

    expect(html).toContain("已取消");
    expect(html).toContain("创建于");
    expect(html).not.toContain("编辑计划");
  });

  it("renders an accessible open form drawer with a reachable submit action", () => {
    const html = renderToStaticMarkup(
      <WorkPlanFormDrawer
        busy={false}
        onClose={() => undefined}
        onSubmit={async () => undefined}
        open
        serverToday="2026-08-15"
      />,
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain("提交计划");
    expect(html).toContain("work-plan-drawer-footer");
  });

  it("makes closed drawers inert without hiding a retained focused descendant", () => {
    const historyHtml = renderToStaticMarkup(
      <MyPlansDrawer
        busy={false}
        items={[PLAN]}
        onCancel={() => undefined}
        onClose={() => undefined}
        onEdit={() => undefined}
        open={false}
      />,
    );
    const formHtml = renderToStaticMarkup(
      <WorkPlanFormDrawer
        busy={false}
        onClose={() => undefined}
        onSubmit={async () => undefined}
        open={false}
        serverToday="2026-08-15"
      />,
    );

    const historyLayer = historyHtml.slice(0, historyHtml.indexOf(">") + 1);
    const formLayer = formHtml.slice(0, formHtml.indexOf(">") + 1);
    expect(historyLayer).toContain("inert");
    expect(formLayer).toContain("inert");
    expect(historyLayer).not.toContain("aria-hidden");
    expect(formLayer).not.toContain("aria-hidden");
  });
});
