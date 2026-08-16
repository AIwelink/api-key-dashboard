// @vitest-environment jsdom

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { useModalFocus } from "../../hooks/useModalFocus";
import { WorkPlanSchedule } from "./WorkPlanSchedule";
import type { WorkPlan, WorkPlanOperation, WorkPlanScheduleResponse } from "./types";

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

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
  members: [{
    member_id: PLAN.member_id,
    member_name: PLAN.member_name,
    is_online: true,
    active_clients: 1,
    active_plan: PLAN,
    collaboration_status: "in_plan",
  }],
  plans: [PLAN],
  start_date: "2026-08-16",
  end_date: "2026-08-22",
  observed_at: "2026-08-16T02:00:00+00:00",
  timezone: "Asia/Shanghai",
  total: 1,
  has_more: false,
  next_cursor: null,
};

const LINEAR_OPERATION: WorkPlanOperation = {
  id: "operation-1",
  schema_version: 2,
  record_kind: "operation",
  member_id: PLAN.member_id,
  member_name: PLAN.member_name,
  operation_type: "activate",
  anchor_date: "2026-08-16",
  plan_date: "2026-08-16",
  requested_start_at: "2026-08-16T01:00:00+00:00",
  requested_end_at: "2026-08-16T04:00:00+00:00",
  effective_start_at: "2026-08-16T01:00:00+00:00",
  effective_end_at: "2026-08-16T04:00:00+00:00",
  start_offset_minute: 9 * 60,
  end_offset_minute: 12 * 60,
  requested_start_offset_minute: 9 * 60,
  requested_end_offset_minute: 12 * 60,
  effective_start_offset_minute: 9 * 60,
  effective_end_offset_minute: 12 * 60,
  member_sequence: 2,
  idempotency_key: "operation-key",
  batch_id: "operation-key",
  note: "线性段操作",
  created_by: PLAN.member_id,
  created_at: PLAN.created_at,
  history_state: "active",
};

const LINEAR_SCHEDULE = {
  ...SCHEDULE,
  plans: [],
  segments: [{
    member_id: PLAN.member_id,
    member_name: PLAN.member_name,
    state: "active" as const,
    start_at: LINEAR_OPERATION.effective_start_at,
    end_at: LINEAR_OPERATION.effective_end_at,
    winning_operation_id: LINEAR_OPERATION.id,
    operation_ids: [LINEAR_OPERATION.id],
    record: LINEAR_OPERATION,
  }],
} as WorkPlanScheduleResponse;

const CANCELLED_LINEAR_SCHEDULE = {
  ...LINEAR_SCHEDULE,
  segments: [{
    ...LINEAR_SCHEDULE.segments![0],
    state: "cancelled" as const,
    record: { ...LINEAR_OPERATION, operation_type: "cancel" as const, history_state: "cancelled" as const },
  }],
};

function SuccessorDialog({ label, onClose }: { label: string; onClose: () => void }) {
  const dialogRef = useModalFocus<HTMLDivElement>(true, onClose);
  return (
    <div aria-label={label} aria-modal="true" ref={dialogRef} role="dialog" tabIndex={-1}>
      <button onClick={onClose} type="button">关闭后继弹窗</button>
    </div>
  );
}

function Harness() {
  const [successor, setSuccessor] = useState("");
  return (
    <>
      <WorkPlanSchedule
        currentUser={{ email: PLAN.member_id, role: "viewer" }}
        onCancelPlan={() => setSuccessor("取消确认")}
        onEditPlan={() => setSuccessor("编辑计划")}
        range="7d"
        response={SCHEDULE}
      />
      {successor ? <SuccessorDialog label={successor} onClose={() => setSuccessor("")} /> : null}
    </>
  );
}

let root: Root | null = null;

afterEach(async () => {
  if (root) {
    await act(async () => root?.unmount());
    root = null;
  }
  document.body.replaceChildren();
});

describe("work plan detail modal handoff", () => {
  it("stages the first member row and newly rendered segment", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => root?.render(
      <WorkPlanSchedule
        currentUser={{ email: PLAN.member_id, role: "viewer" }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        range="7d"
        response={LINEAR_SCHEDULE}
      />,
    ));

    const row = document.querySelector<HTMLElement>(".work-plan-gantt-row");
    const mobileMember = document.querySelector<HTMLElement>(".work-plan-mobile-member");
    const segment = document.querySelector<HTMLElement>(".work-plan-segment");
    expect(row?.style.getPropertyValue("--work-plan-entry-delay")).toBe("220ms");
    expect(mobileMember?.style.getPropertyValue("--work-plan-entry-delay")).toBe("220ms");
    expect(segment?.classList.contains("work-plan-segment-enter")).toBe(true);
  });

  it("gives a changed segment one local feedback animation", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => root?.render(
      <WorkPlanSchedule
        currentUser={{ email: PLAN.member_id, role: "viewer" }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        range="7d"
        response={LINEAR_SCHEDULE}
      />,
    ));

    const changedSchedule: WorkPlanScheduleResponse = {
      ...LINEAR_SCHEDULE,
      segments: [{
        ...LINEAR_SCHEDULE.segments![0],
        end_at: "2026-08-16T05:00:00+00:00",
      }],
    };
    await act(async () => root?.render(
      <WorkPlanSchedule
        currentUser={{ email: PLAN.member_id, role: "viewer" }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        range="7d"
        response={changedSchedule}
      />,
    ));

    expect(document.querySelector(".work-plan-segment")?.classList
      .contains("work-plan-segment-feedback")).toBe(true);
  });

  it("opens editable actions for a linear segment source operation", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => root?.render(
      <WorkPlanSchedule
        currentUser={{ email: PLAN.member_id, role: "owner" }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        range="7d"
        response={LINEAR_SCHEDULE}
      />,
    ));

    const segment = document.querySelector<HTMLButtonElement>(".work-plan-segment.active");
    expect(segment).not.toBeNull();
    await act(async () => segment?.click());

    expect(document.querySelector('[role="dialog"][aria-label="计划详情"]')).not.toBeNull();
    expect(document.querySelector(".work-plan-detail-popover")?.textContent).toContain("创建工作计划");
    expect(Array.from(document.querySelectorAll<HTMLButtonElement>(".work-plan-detail-popover button"))
      .map((button) => button.textContent?.trim())).toEqual(expect.arrayContaining(["编辑", "取消计划"]));
  });

  it("keeps a cancelled linear segment editable without offering another cancellation", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => root?.render(
      <WorkPlanSchedule
        currentUser={{ email: PLAN.member_id, role: "owner" }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        range="7d"
        response={CANCELLED_LINEAR_SCHEDULE}
      />,
    ));

    const segment = document.querySelector<HTMLButtonElement>(".work-plan-segment.cancelled");
    expect(segment).not.toBeNull();
    await act(async () => segment?.click());

    const dialog = document.querySelector(".work-plan-detail-popover");
    expect(dialog?.textContent).toContain("取消计划");
    expect(Array.from(dialog?.querySelectorAll<HTMLButtonElement>("button") || [])
      .some((button) => button.textContent?.trim() === "编辑")).toBe(true);
    expect(Array.from(dialog?.querySelectorAll<HTMLButtonElement>("button") || [])
      .some((button) => button.textContent?.trim() === "取消计划")).toBe(false);
  });

  it("renders the priority editor outside schedule stacking contexts", async () => {
    const container = document.createElement("div");
    document.body.append(container);
    root = createRoot(container);
    await act(async () => root?.render(
      <WorkPlanSchedule
        currentUser={{ email: PLAN.member_id, role: "owner" }}
        onCancelPlan={() => undefined}
        onEditPlan={() => undefined}
        onSetMemberPriority={() => undefined}
        range="7d"
        response={SCHEDULE}
      />,
    ));

    const trigger = document.querySelector<HTMLButtonElement>(".work-plan-priority-trigger");
    await act(async () => trigger?.click());

    const popover = document.querySelector<HTMLFormElement>(".work-plan-priority-popover");
    expect(popover?.parentElement).toBe(document.body);
  });

  for (const actionName of ["编辑", "取消计划"]) {
    it(`restores the triggering plan after the ${actionName} successor closes`, async () => {
      const container = document.createElement("div");
      document.body.append(container);
      root = createRoot(container);
      await act(async () => root?.render(<Harness />));

      const planBar = document.querySelector<HTMLButtonElement>(".work-plan-bar");
      planBar?.focus();
      await act(async () => planBar?.click());
      await act(async () => undefined);

      const action = Array.from(document.querySelectorAll<HTMLButtonElement>(".work-plan-detail-popover button"))
        .find((button) => button.textContent?.trim() === actionName);
      await act(async () => action?.click());
      await act(async () => new Promise((resolve) => window.setTimeout(resolve, 0)));

      const successor = document.querySelector<HTMLElement>('[role="dialog"]');
      expect(successor?.getAttribute("aria-label")).toMatch(/编辑计划|取消确认/);
      document.dispatchEvent(new KeyboardEvent("keydown", { bubbles: true, cancelable: true, key: "Escape" }));
      await act(async () => undefined);

      expect(document.activeElement).toBe(planBar);
    });
  }
});
