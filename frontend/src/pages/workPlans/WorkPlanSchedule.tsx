import { Ban, CalendarDays, Clock3, Pencil, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type CSSProperties } from "react";

import type { User } from "../../types";
import { useModalFocus } from "../../hooks/useModalFocus";
import { isoDateRange } from "./dateSelection";
import type { WorkPlan, WorkPlanMember, WorkPlanRange, WorkPlanScheduleResponse } from "./types";
import { collaborationLabel, ganttGeometry, groupPlansByDate } from "./workPlanViewModel";

type WorkPlanScheduleProps = {
  response: WorkPlanScheduleResponse;
  range: WorkPlanRange;
  currentUser: Pick<User, "email" | "id" | "role">;
  onEditPlan: (plan: WorkPlan) => void;
  onCancelPlan: (plan: WorkPlan) => void;
};

export function canManagePlan(
  currentUser: Pick<User, "email" | "id" | "role">,
  plan: WorkPlan,
): boolean {
  return currentUser.role === "owner" || currentUser.role === "admin" || (currentUser.id || currentUser.email) === plan.member_id;
}

function minuteLabel(value: number): string {
  if (value === 1_440) return "24:00";
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

function rangeLabel(plan: WorkPlan): string {
  return `${minuteLabel(plan.start_minute)} - ${minuteLabel(plan.end_minute)}`;
}

function displayDates(response: WorkPlanScheduleResponse, range: WorkPlanRange): string[] {
  const dates = isoDateRange(response.start_date, response.end_date, range === "all" ? 63 : undefined);
  if (range !== "all" || dates.length <= 62) return dates;
  return [...new Set(response.plans.map((plan) => plan.plan_date))].sort();
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

type WorkPlanDetailDialogProps = {
  plan: WorkPlan;
  currentUser: Pick<User, "email" | "id" | "role">;
  onEditPlan: (plan: WorkPlan) => void;
  onCancelPlan: (plan: WorkPlan) => void;
  onClose: () => void;
};

export function WorkPlanDetailDialog({
  plan,
  currentUser,
  onEditPlan,
  onCancelPlan,
  onClose,
}: WorkPlanDetailDialogProps) {
  const dialogRef = useModalFocus<HTMLDivElement>(true, onClose);
  return (
    <div
      aria-label="计划详情"
      aria-modal="true"
      className="work-plan-detail-popover"
      ref={dialogRef}
      role="dialog"
      tabIndex={-1}
    >
      <header>
        <div>
          <strong>{plan.member_name}</strong>
          <span>{plan.plan_type === "work" ? "工作时间" : "临时有事"}</span>
        </div>
        <button aria-label="关闭详情" className="work-plan-icon-button" onClick={onClose} type="button">
          <X size={17} />
        </button>
      </header>
      <dl>
        <div><dt>日期</dt><dd>{plan.plan_date}</dd></div>
        <div><dt>时间</dt><dd>{rangeLabel(plan)}</dd></div>
        {plan.note ? <div><dt>备注</dt><dd>{plan.note}</dd></div> : null}
      </dl>
      {canManagePlan(currentUser, plan) && !plan.is_cancelled ? (
        <footer>
          <button className="ghost" onClick={() => { onEditPlan(plan); onClose(); }} type="button">
            <Pencil size={15} />编辑
          </button>
          <button className="danger-ghost" onClick={() => { onCancelPlan(plan); onClose(); }} type="button">
            <Ban size={15} />取消计划
          </button>
        </footer>
      ) : null}
    </div>
  );
}

export function WorkPlanSchedule({ response, range, currentUser, onEditPlan, onCancelPlan }: WorkPlanScheduleProps) {
  const [selectedPlan, setSelectedPlan] = useState<WorkPlan | null>(null);
  const modalHandoffTimer = useRef<number | null>(null);
  const dates = useMemo(() => displayDates(response, range), [range, response]);
  const grouped = useMemo(() => groupPlansByDate(response.plans), [response.plans]);
  const membersById = useMemo(
    () => new Map(response.members.map((member) => [member.member_id, member])),
    [response.members],
  );
  const observed = useMemo(() => observedShanghaiTime(response.observed_at), [response.observed_at]);
  const plansByMemberDate = useMemo(() => {
    const index = new Map<string, WorkPlan[]>();
    for (const plan of response.plans) {
      const key = `${plan.member_id}\u0000${plan.plan_date}`;
      const items = index.get(key);
      if (items) items.push(plan); else index.set(key, [plan]);
    }
    return index;
  }, [response.plans]);

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

  if (!response.plans.length) {
    return <div className="work-plan-empty"><CalendarDays size={28} /><strong>暂无工作计划</strong></div>;
  }

  return (
    <section className="work-plan-schedule" aria-label="团队工作计划">
      <div className="work-plan-schedule-scroll">
        <div className="work-plan-gantt" style={{ "--work-plan-date-count": Math.max(1, dates.length) } as CSSProperties}>
          <div className="work-plan-gantt-header work-plan-member-cell"><span>成员</span><small>{response.members.length} 人</small></div>
          {dates.map((date) => <div className={`work-plan-gantt-header ${observed?.date === date ? "work-plan-current-day" : ""}`} key={date}><strong>{date.slice(5).replace("-", "/")}</strong><span>{weekday(date)}</span><div className="work-plan-time-axis"><i>00</i><i>06</i><i>12</i><i>18</i><i>24</i></div></div>)}
          {response.members.map((member) => (
            <div
              className="work-plan-gantt-row"
              key={member.member_id}
              style={{
                "--work-plan-row-height": `${Math.max(
                  72,
                  16 + Math.max(1, ...dates.map((date) => plansByMemberDate.get(`${member.member_id}\u0000${date}`)?.length ?? 0)) * 26,
                )}px`,
              } as CSSProperties}
            >
              <div className="work-plan-member-cell">
                <span className={`work-plan-presence-dot ${member.is_online ? "online" : "offline"}`} />
                <div><strong>{member.member_name}</strong><small>{collaborationLabel(member.collaboration_status)}</small><small className="work-plan-last-seen">{lastSeenLabel(member.last_seen_at)}</small></div>
              </div>
              {dates.map((date) => <div className={`work-plan-day-cell ${observed?.date === date ? "work-plan-current-day" : ""}`} key={date}>{observed?.date === date ? <span aria-hidden="true" className="work-plan-now-marker" style={{ left: `${(observed.minute / 1_440) * 100}%` }} /> : null}{(plansByMemberDate.get(`${member.member_id}\u0000${date}`) || []).map((plan, index) => { const geometry = ganttGeometry(plan.start_minute, plan.end_minute); return <button aria-label={`${plan.member_name} ${date} ${rangeLabel(plan)}`} className={`work-plan-bar ${plan.plan_type === "temporary_unavailable" ? "unavailable" : "work"} ${plan.is_cancelled ? "cancelled" : ""}`} key={plan.id} onClick={() => setSelectedPlan(plan)} style={{ left: `${geometry.leftPercent}%`, top: `${8 + index * 26}px`, width: `${geometry.widthPercent}%` }} title={`${rangeLabel(plan)}${plan.note ? ` · ${plan.note}` : ""}`} type="button"><span>{rangeLabel(plan)}</span></button>; })}</div>)}
            </div>
          ))}
        </div>
      </div>

      <div className="work-plan-mobile-list">
        {grouped.map((group) => <section className="work-plan-mobile-day" key={group.date}><header><strong>{formatDate(group.date)}</strong><span>{group.plans.length} 项</span></header><div>{group.plans.map((plan) => { const member = membersById.get(plan.member_id); return <button className={`work-plan-mobile-item ${plan.plan_type === "temporary_unavailable" ? "unavailable" : "work"}`} key={plan.id} onClick={() => setSelectedPlan(plan)} type="button"><span className="work-plan-mobile-type">{plan.plan_type === "work" ? <Clock3 size={16} /> : <Ban size={16} />}</span><span><strong>{plan.member_name}</strong><small>{rangeLabel(plan)}{plan.note ? ` · ${plan.note}` : ""}</small><small className="work-plan-mobile-presence"><i className={`work-plan-presence-dot ${member?.is_online ? "online" : "offline"}`} />{mobilePresenceLabel(member)}</small></span><em>{plan.is_cancelled ? "已取消" : plan.plan_type === "work" ? "工作" : "有事"}</em></button>; })}</div></section>)}
      </div>

      {selectedPlan ? (
        <WorkPlanDetailDialog
          currentUser={currentUser}
          onCancelPlan={(plan) => handoffModal(() => onCancelPlan(plan))}
          onClose={() => setSelectedPlan(null)}
          onEditPlan={(plan) => handoffModal(() => onEditPlan(plan))}
          plan={selectedPlan}
        />
      ) : null}
    </section>
  );
}

function weekday(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("zh-CN", { weekday: "short", timeZone: "UTC" }).format(new Date(Date.UTC(year, month - 1, day)));
}

function formatDate(value: string): string {
  return `${value.slice(5).replace("-", "月")}日 ${weekday(value)}`;
}
