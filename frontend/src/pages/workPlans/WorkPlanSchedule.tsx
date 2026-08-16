import { Ban, CalendarDays, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import { useModalFocus } from "../../hooks/useModalFocus";
import type { User } from "../../types";
import { isoDateRange } from "./dateSelection";
import type {
  WorkPlan,
  WorkPlanHistoryItem,
  WorkPlanMember,
  WorkPlanOperation,
  WorkPlanRange,
  WorkPlanScheduleResponse,
  WorkPlanSegment,
} from "./types";
import { WorkPlanPriorityPopover } from "./WorkPlanPriorityPopover";
import { collaborationLabel, timelineGeometry } from "./workPlanViewModel";

type WorkPlanScheduleProps = {
  response: WorkPlanScheduleResponse;
  range: WorkPlanRange;
  currentUser: Pick<User, "email" | "id" | "role">;
  onEditPlan: (plan: WorkPlanHistoryItem) => void;
  onCancelPlan: (plan: WorkPlanHistoryItem, segment?: WorkPlanSegment) => void;
  onSetMemberPriority?: (memberId: string, priority: number | null) => Promise<void> | void;
  priorityBusy?: boolean;
};

type RenderableSegment = {
  segment: WorkPlanSegment;
  plan?: WorkPlan;
  record?: WorkPlanHistoryItem;
};

const SHANGHAI_TIMEZONE_LABEL = "Asia/Shanghai (UTC+8)";

export function canManagePlan(
  currentUser: Pick<User, "email" | "id" | "role">,
  plan: WorkPlanHistoryItem,
): boolean {
  return currentUser.role === "owner" || currentUser.role === "admin" || (currentUser.id || currentUser.email) === plan.member_id;
}

function isOperation(item: WorkPlanHistoryItem): item is WorkPlanOperation {
  return "record_kind" in item && item.record_kind === "operation";
}

function canSetPriority(currentUser: Pick<User, "role">): boolean {
  return currentUser.role === "owner" || currentUser.role === "admin";
}

function minuteLabel(value: number): string {
  if (value === 1_440) return "24:00";
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function rangeLabel(plan: WorkPlan): string {
  return `${minuteLabel(plan.start_minute)} - ${minuteLabel(plan.end_minute)}`;
}

function historyTypeLabel(item: WorkPlanHistoryItem): string {
  if (isOperation(item)) return item.operation_type === "activate" ? "创建工作计划" : "取消计划";
  return item.plan_type === "work" ? "工作计划" : "取消计划";
}

function historyDateLabel(item: WorkPlanHistoryItem): string {
  return isOperation(item) ? item.anchor_date : item.plan_date;
}

function historyRangeLabel(item: WorkPlanHistoryItem): string {
  return isOperation(item)
    ? `${minuteLabel(item.requested_start_offset_minute)} - ${minuteLabel(item.requested_end_offset_minute)}`
    : rangeLabel(item);
}

function canCancelRecord(item: WorkPlanHistoryItem): boolean {
  if (isOperation(item)) return item.operation_type === "activate";
  return item.plan_type === "work" && !item.is_cancelled;
}

function canEditRecord(item: WorkPlanHistoryItem): boolean {
  return isOperation(item) || !item.is_cancelled;
}

function displayDates(response: WorkPlanScheduleResponse, range: WorkPlanRange): string[] {
  const dates = isoDateRange(response.start_date, response.end_date, range === "all" ? 63 : undefined);
  if (range !== "all" || dates.length <= 62) return dates;
  const recordedDates = new Set(response.plans.map((plan) => plan.plan_date));
  const focusedDates = dates.filter((date) => recordedDates.has(date));
  return (focusedDates.length ? focusedDates : dates).slice(0, 63);
}

function nextDate(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  return new Date(Date.UTC(year, month - 1, day + 1)).toISOString().slice(0, 10);
}

function timelineBounds(response: WorkPlanScheduleResponse): { startAt: string; endAt: string } {
  return {
    startAt: response.start_at || `${response.start_date}T00:00:00+08:00`,
    endAt: response.end_at || `${nextDate(response.end_date)}T00:00:00+08:00`,
  };
}

function legacySegment(plan: WorkPlan): RenderableSegment {
  const anchor = Date.parse(`${plan.plan_date}T00:00:00+08:00`);
  return {
    plan,
    segment: {
      member_id: plan.member_id,
      member_name: plan.member_name,
      state: plan.is_cancelled || plan.plan_type === "temporary_unavailable" ? "cancelled" : "active",
      start_at: new Date(anchor + plan.start_minute * 60_000).toISOString(),
      end_at: new Date(anchor + plan.end_minute * 60_000).toISOString(),
      winning_operation_id: plan.id,
      operation_ids: [plan.id],
    },
  };
}

function renderableSegments(response: WorkPlanScheduleResponse): RenderableSegment[] {
  if (response.segments?.length) return response.segments.map((segment) => ({ segment, record: segment.record }));
  return response.plans.map(legacySegment);
}

function observedShanghaiTime(value: string): { date: string; minute: number } | null {
  const observed = new Date(value);
  if (Number.isNaN(observed.getTime())) return null;
  const parts = new Intl.DateTimeFormat("en-CA", {
    day: "2-digit",
    hour: "2-digit",
    hourCycle: "h23",
    minute: "2-digit",
    month: "2-digit",
    timeZone: "Asia/Shanghai",
    year: "numeric",
  }).formatToParts(observed);
  const values = new Map(parts.map((part) => [part.type, part.value]));
  return {
    date: `${values.get("year")}-${values.get("month")}-${values.get("day")}`,
    minute: Number(values.get("hour")) * 60 + Number(values.get("minute")),
  };
}

function lastSeenLabel(value?: string | null): string {
  if (!value) return "暂无在线记录";
  return `最后在线 ${new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Shanghai",
  }).format(new Date(value))}`;
}

function mobilePresenceLabel(member?: WorkPlanMember): string {
  if (!member) return "在线状态未知";
  const presence = member.is_online ? "当前在线" : "当前离线";
  if (member.collaboration_status === "online" || member.collaboration_status === "offline") return presence;
  return `${presence} · ${collaborationLabel(member.collaboration_status)}`;
}

function segmentIntervalLabel(segment: WorkPlanSegment): string {
  const formatter = new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
    timeZone: "Asia/Shanghai",
  });
  return `${formatter.format(new Date(segment.start_at))} - ${formatter.format(new Date(segment.end_at))}`;
}

type WorkPlanDetailDialogProps = {
  plan: WorkPlanHistoryItem;
  segment?: WorkPlanSegment;
  currentUser: Pick<User, "email" | "id" | "role">;
  onEditPlan: (plan: WorkPlanHistoryItem) => void;
  onCancelPlan: (plan: WorkPlanHistoryItem, segment?: WorkPlanSegment) => void;
  onClose: () => void;
};

export function WorkPlanDetailDialog({
  plan,
  segment,
  currentUser,
  onEditPlan,
  onCancelPlan,
  onClose,
}: WorkPlanDetailDialogProps) {
  const dialogRef = useModalFocus<HTMLDivElement>(true, onClose);
  const manageable = canManagePlan(currentUser, plan);
  return (
    <div aria-label="计划详情" aria-modal="true" className="work-plan-detail-popover" ref={dialogRef} role="dialog" tabIndex={-1}>
      <header>
        <div><strong>{plan.member_name}</strong><span>{historyTypeLabel(plan)}</span></div>
        <button aria-label="关闭详情" className="work-plan-icon-button" onClick={onClose} type="button"><X size={17} /></button>
      </header>
      <dl>
        <div><dt>日期</dt><dd>{historyDateLabel(plan)}</dd></div>
        <div><dt>时间</dt><dd>{historyRangeLabel(plan)}</dd></div>
        {plan.note ? <div><dt>备注</dt><dd>{plan.note}</dd></div> : null}
      </dl>
      {manageable && canEditRecord(plan) ? (
        <footer>
          <button className="ghost" onClick={() => { onEditPlan(plan); onClose(); }} type="button"><Pencil size={15} />编辑</button>
          {canCancelRecord(plan) ? <button className="danger-ghost" onClick={() => { onCancelPlan(plan, segment); onClose(); }} type="button"><Ban size={15} />取消计划</button> : null}
        </footer>
      ) : null}
    </div>
  );
}

export function WorkPlanSchedule({
  response,
  range,
  currentUser,
  onEditPlan,
  onCancelPlan,
  onSetMemberPriority,
  priorityBusy = false,
}: WorkPlanScheduleProps) {
  const [selectedPlan, setSelectedPlan] = useState<{ plan: WorkPlanHistoryItem; segment: WorkPlanSegment } | null>(null);
  const modalHandoffTimer = useRef<number | null>(null);
  const dates = useMemo(() => displayDates(response, range), [range, response]);
  const bounds = useMemo(() => timelineBounds(response), [response]);
  const segments = useMemo(() => renderableSegments(response), [response]);
  const segmentsByMember = useMemo(() => {
    const index = new Map<string, RenderableSegment[]>();
    for (const item of segments) {
      const memberSegments = index.get(item.segment.member_id);
      if (memberSegments) memberSegments.push(item); else index.set(item.segment.member_id, [item]);
    }
    return index;
  }, [segments]);
  const observed = useMemo(() => observedShanghaiTime(response.observed_at), [response.observed_at]);
  const observedAt = Date.parse(response.observed_at);
  const timelineStart = Date.parse(bounds.startAt);
  const timelineEnd = Date.parse(bounds.endAt);
  const showNow = Number.isFinite(observedAt) && observedAt >= timelineStart && observedAt <= timelineEnd;
  const nowGeometry = useMemo(() => timelineGeometry(
    bounds.startAt,
    bounds.endAt,
    response.observed_at,
    new Date(Date.parse(response.observed_at) + 60_000).toISOString(),
  ), [bounds.endAt, bounds.startAt, response.observed_at]);

  useEffect(() => () => {
    if (modalHandoffTimer.current !== null) window.clearTimeout(modalHandoffTimer.current);
  }, []);

  const handoffModal = useCallback((callback: () => void) => {
    if (modalHandoffTimer.current !== null) window.clearTimeout(modalHandoffTimer.current);
    setSelectedPlan(null);
    modalHandoffTimer.current = window.setTimeout(() => {
      modalHandoffTimer.current = null;
      callback();
    }, 0);
  }, []);

  if (!segments.length) {
    return <div className="work-plan-empty"><CalendarDays size={28} /><strong>暂无工作计划</strong></div>;
  }

  const renderSegment = (item: RenderableSegment) => {
    const geometry = timelineGeometry(bounds.startAt, bounds.endAt, item.segment.start_at, item.segment.end_at);
    const record = item.record ?? item.plan;
    const interval = item.plan ? rangeLabel(item.plan) : segmentIntervalLabel(item.segment);
    const className = `work-plan-segment ${item.segment.state}${record ? " work-plan-bar" : ""}`;
    const style = { left: `${geometry.leftPercent}%`, width: `${geometry.widthPercent}%` };
    if (record) {
      return (
        <button aria-label={`${item.segment.member_name} ${interval}`} className={className} key={item.segment.winning_operation_id} onClick={() => setSelectedPlan({ plan: record, segment: item.segment })} style={style} title={`${interval}${record.note ? ` · ${record.note}` : ""}`} type="button"><span>{interval}</span></button>
      );
    }
    return (
      <span aria-label={`${item.segment.member_name} ${item.segment.state === "active" ? "工作计划" : "已取消"} ${interval}`} className={className} key={item.segment.winning_operation_id} role="img" style={style} title={`${item.segment.state === "active" ? "工作计划" : "已取消"} · ${interval}`}><span>{interval}</span></span>
    );
  };

  const timelineStyle = { "--work-plan-date-count": Math.max(1, dates.length) } as CSSProperties;

  return (
    <section className="work-plan-schedule" aria-label="团队工作计划">
      <div className="work-plan-timezone-label">时间基准：{SHANGHAI_TIMEZONE_LABEL}</div>
      <div className="work-plan-schedule-scroll">
        <div className="work-plan-gantt" style={timelineStyle}>
          <div className="work-plan-gantt-header work-plan-member-cell"><span>成员</span><small>{response.members.length} 人</small></div>
          <div className="work-plan-timeline-header">
            {dates.map((date) => (
              <div className={`work-plan-gantt-header ${observed?.date === date ? "work-plan-current-day" : ""}`} key={date}>
                <strong>{date.slice(5).replace("-", "/")}</strong><span>{weekday(date)}</span>
                <div className="work-plan-time-axis"><i>00</i><i>06</i><i>12</i><i>18</i><i>24</i></div>
              </div>
            ))}
          </div>
          {response.members.map((member) => (
            <div className="work-plan-gantt-row" key={member.member_id}>
              <div className="work-plan-member-cell">
                <span className={`work-plan-presence-dot ${member.is_online ? "online" : "offline"}`} />
                <div><strong>{member.member_name}</strong><small>{collaborationLabel(member.collaboration_status)}</small><small className="work-plan-last-seen">{lastSeenLabel(member.last_seen_at)}</small></div>
                {member.work_plan_priority != null ? <span className="work-plan-priority-value">#{member.work_plan_priority}</span> : null}
                {canSetPriority(currentUser) ? <WorkPlanPriorityPopover busy={priorityBusy} member={member} onChange={onSetMemberPriority} /> : null}
              </div>
              <div className="work-plan-member-track">
                {showNow ? <span aria-hidden="true" className="work-plan-now-marker" style={{ left: `${nowGeometry.leftPercent}%` }} /> : null}
                {segmentsByMember.get(member.member_id)?.map(renderSegment)}
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="work-plan-mobile-list">
        {response.members.map((member) => (
          <section className="work-plan-mobile-member" key={member.member_id}>
            <header>
              <span className={`work-plan-presence-dot ${member.is_online ? "online" : "offline"}`} />
              <div><strong>{member.member_name}</strong><small className="work-plan-mobile-presence">{mobilePresenceLabel(member)} · {lastSeenLabel(member.last_seen_at)}</small></div>
              {member.work_plan_priority != null ? <em>#{member.work_plan_priority}</em> : null}
              {canSetPriority(currentUser) ? <WorkPlanPriorityPopover busy={priorityBusy} member={member} onChange={onSetMemberPriority} /> : null}
            </header>
            <div className="work-plan-mobile-track-scroll">
              <div className="work-plan-mobile-track" style={timelineStyle}>
                <div className="work-plan-mobile-axis">{dates.map((date) => <span key={date}>{date.slice(5).replace("-", "/")}</span>)}</div>
                <div className="work-plan-mobile-member-rail">
                  {showNow ? <span aria-hidden="true" className="work-plan-now-marker" style={{ left: `${nowGeometry.leftPercent}%` }} /> : null}
                  {segmentsByMember.get(member.member_id)?.map(renderSegment)}
                </div>
              </div>
            </div>
          </section>
        ))}
      </div>

      {selectedPlan ? (
        <WorkPlanDetailDialog currentUser={currentUser} onCancelPlan={(plan, segment) => handoffModal(() => onCancelPlan(plan, segment))} onClose={() => setSelectedPlan(null)} onEditPlan={(plan) => handoffModal(() => onEditPlan(plan))} plan={selectedPlan.plan} segment={selectedPlan.segment} />
      ) : null}
    </section>
  );
}

function weekday(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("zh-CN", { weekday: "short", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, day)));
}
