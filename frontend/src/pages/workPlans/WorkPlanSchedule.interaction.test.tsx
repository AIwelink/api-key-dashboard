// @vitest-environment jsdom

import { act, useState } from "react";
import { createRoot, type Root } from "react-dom/client";
import { afterEach, describe, expect, it } from "vitest";

import { useModalFocus } from "../../hooks/useModalFocus";
import { WorkPlanSchedule } from "./WorkPlanSchedule";
import type { WorkPlan, WorkPlanScheduleResponse } from "./types";

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
