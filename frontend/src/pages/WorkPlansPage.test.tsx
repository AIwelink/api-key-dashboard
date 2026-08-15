import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";

import { MyPlansDrawer } from "./workPlans/MyPlansDrawer";
import {
  createInitialWorkPlanDraft,
  resetDraftAfterSuccessfulSubmit,
  WorkPlanFormDrawer,
  workPlanDraftReducer,
} from "./workPlans/WorkPlanFormDrawer";
import { canManagePlan, WorkPlanDetailDialog, WorkPlanSchedule } from "./workPlans/WorkPlanSchedule";
import type {
  WorkPlan,
  WorkPlanOperation,
  WorkPlanScheduleResponse,
} from "./workPlans/types";
import {
  beginCreate,
  createLatestRequestGuard,
  drawerExitDelay,
  initialRequestState,
  mergeHistoryPage,
  mergeSchedulePage,
  mergeWorkPlans,
  mutationErrorMessage,
  reconcileSchedulePlans,
  ScheduleStaleNotice,
} from "./WorkPlansPage";

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
  total: 1,
  has_more: false,
  next_cursor: null,
};

const LINEAR_SCHEDULE: WorkPlanScheduleResponse = {
  ...SCHEDULE,
  plans: [],
  start_at: "2026-08-15T16:00:00+00:00",
  end_at: "2026-08-22T16:00:00+00:00",
  segments: [
    {
      member_id: PLAN.member_id,
      member_name: PLAN.member_name,
      state: "active",
      start_at: "2026-08-16T01:00:00+00:00",
      end_at: "2026-08-16T04:00:00+00:00",
      winning_operation_id: "operation-1",
      operation_ids: ["operation-1"],
    },
    {
      member_id: PLAN.member_id,
      member_name: PLAN.member_name,
      state: "cancelled",
      start_at: "2026-08-16T04:00:00+00:00",
      end_at: "2026-08-16T06:00:00+00:00",
      winning_operation_id: "operation-2",
      operation_ids: ["operation-2"],
    },
  ],
};

describe("work plan components", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("generates a valid idempotency UUID when randomUUID is unavailable", () => {
    vi.stubGlobal("crypto", {});

    const draft = createInitialWorkPlanDraft("2026-08-15");

    expect(draft.idempotencyKey).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  it("preserves the draft after a failed create submission", async () => {
    let resetCount = 0;

    const submitted = await resetDraftAfterSuccessfulSubmit(
      async () => false,
      () => { resetCount += 1; },
    );

    expect(submitted).toBe(false);
    expect(resetCount).toBe(0);
  });

  it("reuses one idempotency key while a create request is in flight", () => {
    const started = beginCreate(initialRequestState, "request-key");
    expect(beginCreate(started, "different-key").idempotencyKey).toBe("request-key");
  });

  it("keeps only the latest schedule request authoritative", () => {
    const guard = createLatestRequestGuard();
    const first = guard.begin();
    const second = guard.begin();

    expect(guard.isCurrent(first)).toBe(false);
    expect(guard.isCurrent(second)).toBe(true);
  });

  it("treats an uncertain create outcome as retryable without clearing the draft", () => {
    const message = mutationErrorMessage({
      duplicate_submission: false,
      total: 1,
      results: [{
        plan_date: "2026-08-16",
        outcome: "uncertain",
        error: "保存结果暂时无法确认，请使用相同提交标识重试",
      }],
    });

    expect(message).toContain("2026-08-16");
    expect(message).toContain("重试");
  });

  it("sequences drawer handoff while respecting reduced motion", () => {
    expect(drawerExitDelay(false)).toBe(280);
    expect(drawerExitDelay(true)).toBe(0);
  });

  it("reconciles authoritative mutation responses before a refresh completes", () => {
    const updated = { ...PLAN, note: "已更新", updated_at: "2026-08-15T03:00:00+00:00" };
    expect(mergeWorkPlans([PLAN], [updated])).toEqual([updated]);

    const cancelled = { ...updated, status: "cancelled" as const, is_cancelled: true };
    expect(
      reconcileSchedulePlans(SCHEDULE, [cancelled], { includeCancelled: false, memberId: "" }).plans,
    ).toEqual([]);
    expect(
      reconcileSchedulePlans(SCHEDULE, [cancelled], { includeCancelled: true, memberId: "" }).plans,
    ).toEqual([cancelled]);
  });

  it("merges cursor pages without duplicating plans", () => {
    const nextPlan = { ...PLAN, id: "plan-2", plan_date: "2026-08-15" };
    expect(mergeHistoryPage(
      { items: [PLAN], total: 2, has_more: true, next_cursor: "next" },
      { items: [PLAN, nextPlan], total: 2, has_more: false, next_cursor: null },
    )).toEqual({ items: [PLAN, nextPlan], total: 2, has_more: false, next_cursor: null });

    const mergedSchedule = mergeSchedulePage(SCHEDULE, {
      ...SCHEDULE,
      plans: [nextPlan],
      start_date: nextPlan.plan_date,
      has_more: false,
      next_cursor: null,
      total: 2,
    });
    expect(mergedSchedule.plans.map((plan) => plan.id)).toEqual(["plan-2", "plan-1"]);
    expect(mergedSchedule.start_date).toBe("2026-08-15");
  });

  it("cancel plan keeps one date and disables bulk modes", () => {
    const draft = {
      ...createInitialWorkPlanDraft("2026-08-15"),
      selectedDates: ["2026-08-15", "2026-08-16"],
      moreDateMode: "multiple" as const,
    };

    const next = workPlanDraftReducer(draft, {
      type: "set-operation-type",
      value: "cancel",
    });

    expect(next.selectedDates).toEqual(["2026-08-15"]);
    expect(next.moreDateMode).toBe("single");
    expect(next.operationType).toBe("cancel");
  });

  it("restores a date when cancel plan is selected from an empty draft", () => {
    const draft = {
      ...createInitialWorkPlanDraft("2026-08-15"),
      selectedDates: [],
    };

    const next = workPlanDraftReducer(draft, {
      type: "set-operation-type",
      value: "cancel",
    });

    expect(next.selectedDates).toEqual(["2026-08-15"]);
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

  it("renders one member track with active and cancelled segments", () => {
    const html = renderToStaticMarkup(
      <WorkPlanSchedule
        currentUser={{ email: "viewer@example.com", role: "viewer" }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        range="7d"
        response={LINEAR_SCHEDULE}
      />,
    );

    expect(html.match(/work-plan-member-track/g)?.length).toBe(1);
    expect(html).toContain("work-plan-segment active");
    expect(html).toContain("work-plan-segment cancelled");
  });

  it("shows priority editing only to managers", () => {
    const renderSchedule = (role: "admin" | "viewer") => renderToStaticMarkup(
      <WorkPlanSchedule
        currentUser={{ email: `${role}@example.com`, role }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        range="7d"
        response={LINEAR_SCHEDULE}
      />,
    );

    expect(renderSchedule("admin")).toContain("设置排班优先级");
    expect(renderSchedule("viewer")).not.toContain("设置排班优先级");
  });

  it("shows a clear empty state whenever the selected result has no plans", () => {
    const html = renderToStaticMarkup(
      <WorkPlanSchedule
        currentUser={{ email: "viewer@example.com", role: "viewer" }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        range="7d"
        response={{ ...SCHEDULE, plans: [], total: 0 }}
      />,
    );

    expect(html).toContain("暂无工作计划");
    expect(html).not.toContain("work-plan-gantt-row");
  });

  it("keeps schedule refresh failures visible beside potentially stale data", () => {
    const html = renderToStaticMarkup(
      <ScheduleStaleNotice message="网络暂时不可用" onRetry={() => undefined} refreshing={false} />,
    );

    expect(html).toContain('role="alert"');
    expect(html).toContain("当前显示的数据可能不是最新");
    expect(html).toContain("重新加载");
  });

  it("marks plan details as a keyboard-managed modal dialog", () => {
    const html = renderToStaticMarkup(
      <WorkPlanDetailDialog
        currentUser={{ email: PLAN.member_id, role: "viewer" }}
        onCancelPlan={() => undefined}
        onClose={() => undefined}
        onEditPlan={() => undefined}
        plan={PLAN}
      />,
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain('aria-modal="true"');
    expect(html).toContain('tabindex="-1"');
    expect(html).toContain('aria-label="关闭详情"');
  });

  it("renders plan context, independent presence, last seen, and current time orientation", () => {
    const member = {
      ...SCHEDULE.members[0],
      is_online: true,
      active_clients: 1,
      active_plan: PLAN,
      collaboration_status: "in_plan" as const,
    };
    const html = renderToStaticMarkup(
      <WorkPlanSchedule
        currentUser={{ email: "viewer@example.com", role: "viewer" }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        range="7d"
        response={{ ...SCHEDULE, members: [member], observed_at: "2026-08-16T02:00:00+00:00" }}
      />,
    );

    expect(html).toContain("计划工作中");
    expect(html).toContain("当前在线");
    expect(html).toContain("最后在线");
    expect(html).toContain("work-plan-mobile-presence");
    expect(html).toContain("work-plan-current-day");
    expect(html).toContain("work-plan-now-marker");
  });

  it("shows manager controls for another member without exposing them to ordinary viewers", () => {
    expect(canManagePlan({ email: "admin@example.com", role: "admin" }, PLAN)).toBe(true);
    expect(canManagePlan({ email: "viewer@example.com", role: "viewer" }, PLAN)).toBe(false);
    expect(canManagePlan({ email: PLAN.member_id, role: "viewer" }, PLAN)).toBe(true);
  });

  it("renders cancelled history as traceable but not editable", () => {
    const html = renderToStaticMarkup(
      <MyPlansDrawer
        blocked
        busy={false}
        items={[{ ...PLAN, status: "cancelled", is_cancelled: true, cancelled_at: PLAN.updated_at }]}
        onCancel={() => undefined}
        onClose={() => undefined}
        onEdit={() => undefined}
        open
        total={1}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => undefined}
      />,
    );

    expect(html).toContain("已取消");
    expect(html).toContain("创建于");
    expect(html).not.toContain("编辑计划");
    expect(html.slice(0, html.indexOf(">") + 1)).toContain("inert");
  });

  it("keeps immutable cancellation history grey after later coverage", () => {
    const operation: WorkPlanOperation = {
      id: "operation-2",
      schema_version: 2,
      record_kind: "operation",
      member_id: PLAN.member_id,
      member_name: PLAN.member_name,
      operation_type: "cancel",
      anchor_date: "2026-08-16",
      plan_date: "2026-08-16",
      requested_start_at: "2026-08-16T04:00:00+00:00",
      requested_end_at: "2026-08-16T06:00:00+00:00",
      effective_start_at: "2026-08-16T04:00:00+00:00",
      effective_end_at: "2026-08-16T06:00:00+00:00",
      start_offset_minute: 12 * 60,
      end_offset_minute: 14 * 60,
      requested_start_offset_minute: 12 * 60,
      requested_end_offset_minute: 14 * 60,
      effective_start_offset_minute: 12 * 60,
      effective_end_offset_minute: 14 * 60,
      member_sequence: 2,
      idempotency_key: "key",
      batch_id: "key",
      note: null,
      created_by: PLAN.member_id,
      created_at: PLAN.created_at,
      history_state: "cancelled",
    };
    const html = renderToStaticMarkup(
      <MyPlansDrawer
        busy={false}
        hasMore={false}
        items={[operation]}
        loadingMore={false}
        onCancel={() => undefined}
        onClose={() => undefined}
        onEdit={() => undefined}
        onLoadMore={() => undefined}
        open
        total={1}
      />,
    );

    expect(html).toContain("work-plan-history-item cancelled");
    expect(html).toContain("取消计划");
    expect(html).toContain("灰色保留");
  });

  it("exposes explicit history progress and a load-more action", () => {
    const html = renderToStaticMarkup(
      <MyPlansDrawer
        busy={false}
        hasMore
        items={[PLAN]}
        loadingMore={false}
        onCancel={() => undefined}
        onClose={() => undefined}
        onEdit={() => undefined}
        onLoadMore={() => undefined}
        open
        total={3}
      />,
    );

    expect(html).toContain("已加载 1 / 3 条记录");
    expect(html).toContain("加载更多");
  });

  it("renders an accessible open form drawer with a reachable submit action", () => {
    const html = renderToStaticMarkup(
      <WorkPlanFormDrawer
        busy={false}
        onClose={() => undefined}
        onSubmit={async () => true}
        open
        serverToday="2026-08-15"
      />,
    );

    expect(html).toContain('role="dialog"');
    expect(html).toContain("创建工作计划");
    expect(html).toContain("取消计划");
    expect(html).not.toContain("临时有事");
    expect(html).toContain("当天 09:00 - 当天 18:00");
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
        total={1}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => undefined}
      />,
    );
    const formHtml = renderToStaticMarkup(
      <WorkPlanFormDrawer
        busy={false}
        onClose={() => undefined}
        onSubmit={async () => true}
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
