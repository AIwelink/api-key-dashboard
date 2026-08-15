import { Ban, CalendarClock, Pencil, X } from "lucide-react";

import type { WorkPlan } from "./types";

type MyPlansDrawerProps = {
  open: boolean;
  items: WorkPlan[];
  busy: boolean;
  onClose: () => void;
  onEdit: (plan: WorkPlan) => void;
  onCancel: (plan: WorkPlan) => void;
};

function minuteLabel(value: number): string {
  if (value === 1_440) return "24:00";
  return `${String(Math.floor(value / 60)).padStart(2, "0")}:${String(value % 60).padStart(2, "0")}`;
}

export function MyPlansDrawer({ open, items, busy, onClose, onEdit, onCancel }: MyPlansDrawerProps) {
  return (
    <div className={`work-plan-drawer-layer ${open ? "open" : ""}`} aria-hidden={!open}>
      <button aria-label="关闭我的安排" className="work-plan-drawer-backdrop" onClick={onClose} type="button" />
      <aside aria-labelledby="my-plans-title" aria-modal="true" className={`work-plan-drawer work-plan-history-drawer ${open ? "open" : ""}`} role="dialog">
        <header className="work-plan-drawer-header"><div><span className="work-plan-header-icon"><CalendarClock size={18} /></span><div><h3 id="my-plans-title">我的安排</h3><p>{items.length} 条记录</p></div></div><button aria-label="关闭" className="work-plan-icon-button" onClick={onClose} type="button"><X size={19} /></button></header>
        <div className="work-plan-history-list">
          {items.map((plan) => <article className={`work-plan-history-item ${plan.is_cancelled ? "cancelled" : ""}`} key={plan.id}><header><div><span className={`work-plan-history-type ${plan.plan_type}`}>{plan.plan_type === "work" ? "工作时间" : "临时有事"}</span>{plan.is_cancelled ? <span className="work-plan-cancelled-label">已取消</span> : null}</div><strong>{plan.plan_date}</strong></header><div className="work-plan-history-time">{minuteLabel(plan.start_minute)} - {minuteLabel(plan.end_minute)}</div>{plan.note ? <p>{plan.note}</p> : null}<dl><div><dt>创建于</dt><dd>{formatTimestamp(plan.created_at)}</dd></div><div><dt>最后修改</dt><dd>{formatTimestamp(plan.updated_at)}</dd></div>{plan.cancelled_at ? <div><dt>取消于</dt><dd>{formatTimestamp(plan.cancelled_at)}</dd></div> : null}</dl>{!plan.is_cancelled ? <footer><button className="ghost" disabled={busy} onClick={() => onEdit(plan)} title="编辑计划" type="button"><Pencil size={15} />编辑计划</button><button className="danger-ghost" disabled={busy} onClick={() => onCancel(plan)} type="button"><Ban size={15} />取消计划</button></footer> : null}</article>)}
          {!items.length ? <div className="work-plan-history-empty"><CalendarClock size={28} /><strong>暂无安排</strong></div> : null}
        </div>
      </aside>
    </div>
  );
}

function formatTimestamp(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Shanghai" }).format(new Date(value));
}
