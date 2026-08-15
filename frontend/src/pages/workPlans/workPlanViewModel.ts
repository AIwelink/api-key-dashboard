import type { CollaborationStatus, WorkPlan } from "./types";

const MINUTES_PER_DAY = 1_440;
const COLLABORATION_LABELS: Record<CollaborationStatus, string> = {
  in_plan: "计划工作中",
  online: "当前在线",
  offline: "当前离线",
  planned_offline: "计划时段内，暂未在线",
  temporary_unavailable: "临时有事",
};

export type GanttGeometry = {
  leftPercent: number;
  widthPercent: number;
};

export type WorkPlanDateGroup = {
  date: string;
  plans: WorkPlan[];
};

function roundPercent(value: number): number {
  return Math.round(value * 10_000) / 10_000;
}

export function ganttGeometry(startMinute: number, endMinute: number): GanttGeometry {
  const start = Math.max(0, Math.min(MINUTES_PER_DAY, startMinute));
  const end = Math.max(start, Math.min(MINUTES_PER_DAY, endMinute));
  return {
    leftPercent: roundPercent((start / MINUTES_PER_DAY) * 100),
    widthPercent: roundPercent(((end - start) / MINUTES_PER_DAY) * 100),
  };
}

export function timelineGeometry(
  timelineStart: string,
  timelineEnd: string,
  segmentStart: string,
  segmentEnd: string,
): GanttGeometry {
  const startAt = Date.parse(timelineStart);
  const endAt = Date.parse(timelineEnd);
  const itemStartAt = Date.parse(segmentStart);
  const itemEndAt = Date.parse(segmentEnd);
  if (
    !Number.isFinite(startAt)
    || !Number.isFinite(endAt)
    || !Number.isFinite(itemStartAt)
    || !Number.isFinite(itemEndAt)
    || endAt <= startAt
  ) {
    return { leftPercent: 0, widthPercent: 0 };
  }
  const clippedStart = Math.max(startAt, Math.min(endAt, itemStartAt));
  const clippedEnd = Math.max(clippedStart, Math.min(endAt, itemEndAt));
  const duration = endAt - startAt;
  return {
    leftPercent: roundPercent(((clippedStart - startAt) / duration) * 100),
    widthPercent: roundPercent(((clippedEnd - clippedStart) / duration) * 100),
  };
}

export function groupPlansByDate(plans: WorkPlan[]): WorkPlanDateGroup[] {
  const groups = new Map<string, WorkPlan[]>();
  for (const plan of plans) {
    const group = groups.get(plan.plan_date);
    if (group) {
      group.push(plan);
    } else {
      groups.set(plan.plan_date, [plan]);
    }
  }
  return [...groups.entries()]
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([date, items]) => ({
      date,
      plans: [...items].sort(
        (left, right) => left.start_minute - right.start_minute || left.id.localeCompare(right.id),
      ),
    }));
}

export function collaborationLabel(status: CollaborationStatus): string {
  return COLLABORATION_LABELS[status];
}
