import { describe, expect, it } from "vitest";

import { collaborationLabel, ganttGeometry, groupPlansByDate } from "./workPlanViewModel";
import type { WorkPlan } from "./types";

const basePlan: WorkPlan = {
  id: "plan-1",
  member_id: "member-1",
  member_name: "成员一",
  plan_type: "work",
  plan_date: "2026-08-16",
  start_minute: 570,
  end_minute: 1080,
  note: null,
  status: "active",
  is_cancelled: false,
  created_at: "2026-08-15T00:00:00+00:00",
  updated_at: "2026-08-15T00:00:00+00:00",
};

describe("work plan view model", () => {
  it("maps minutes to stable full-day Gantt percentages", () => {
    expect(ganttGeometry(570, 1080)).toEqual({
      leftPercent: 39.5833,
      widthPercent: 35.4167,
    });
  });

  it("clamps malformed geometry without producing negative widths", () => {
    expect(ganttGeometry(-30, 1_500)).toEqual({ leftPercent: 0, widthPercent: 100 });
    expect(ganttGeometry(900, 600)).toEqual({ leftPercent: 62.5, widthPercent: 0 });
  });

  it("groups plans by date and sorts each day by start time", () => {
    const groups = groupPlansByDate([
      { ...basePlan, id: "late", start_minute: 720 },
      { ...basePlan, id: "next", plan_date: "2026-08-17" },
      { ...basePlan, id: "early", start_minute: 480 },
    ]);

    expect(groups.map((group) => group.date)).toEqual(["2026-08-16", "2026-08-17"]);
    expect(groups[0].plans.map((plan) => plan.id)).toEqual(["early", "late"]);
  });

  it("uses neutral Chinese collaboration labels", () => {
    expect(collaborationLabel("in_plan")).toBe("计划工作中");
    expect(collaborationLabel("online")).toBe("当前在线");
    expect(collaborationLabel("planned_offline")).toBe("计划时段内，暂未在线");
    expect(collaborationLabel("temporary_unavailable")).toBe("临时有事");
    expect(collaborationLabel("offline")).toBe("当前离线");
  });
});
