import { Ban, CalendarClock, Pencil, X } from "lucide-react";

import { useModalFocus } from "../../hooks/useModalFocus";
import type { WorkPlan, WorkPlanHistoryItem, WorkPlanOperation } from "./types";

type MyPlansDrawerProps = {
  open: boolean;
  items: WorkPlanHistoryItem[];
  busy: boolean;
  blocked?: boolean;
  total: number;
  hasMore: boolean;
  loadingMore: boolean;
  onClose: () => void;
  onEdit: (item: WorkPlanHistoryItem) => void;
  onCancel: (plan: WorkPlan) => void;
  onLoadMore: () => void;
};

function isOperation(item: WorkPlanHistoryItem): item is WorkPlanOperation {
  return "record_kind" in item && item.record_kind === "operation";
}

function minuteLabel(value: number): string {
  const day = Math.floor(value / 1_440);
  const minute = value % 1_440;
  const clock = `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
  if (day === 0) return `当天 ${clock}`;
  if (day === 1) return `次日 ${clock}`;
  return `两日后 ${clock}`;
}

function operationState(item: WorkPlanOperation): "active" | "cancelled" | "replaced" {
  if (item.history_state) return item.history_state;
  return item.operation_type === "cancel" ? "cancelled" : "active";
}

function historyClass(item: WorkPlanHistoryItem): string {
  if (!isOperation(item)) return item.is_cancelled ? "cancelled" : "";
  return operationState(item) === "active" ? "" : "cancelled";
}

function operationInterval(item: WorkPlanOperation, requested = false): string {
  const start = requested ? item.requested_start_offset_minute : item.effective_start_offset_minute;
  const end = requested ? item.requested_end_offset_minute : item.effective_end_offset_minute;
  return `${minuteLabel(start)} - ${minuteLabel(end)}`;
}

export function MyPlansDrawer({ open, blocked = false, items, total, hasMore, loadingMore, busy, onClose, onEdit, onCancel, onLoadMore }: MyPlansDrawerProps) {
  const dialogRef = useModalFocus<HTMLElement>(open && !blocked, onClose);
  const latestOperationSequence = items.reduce(
    (latest, item) => isOperation(item) ? Math.max(latest, item.member_sequence) : latest,
    0,
  );
  return (
    <div className={`work-plan-drawer-layer ${open ? "open" : ""}`} inert={!open || blocked}>
      <button aria-label="关闭我的安排" className="work-plan-drawer-backdrop" onClick={onClose} type="button" />
      <aside aria-labelledby="my-plans-title" aria-modal="true" className={`work-plan-drawer work-plan-history-drawer ${open ? "open" : ""}`} ref={dialogRef} role="dialog" tabIndex={-1}>
        <header className="work-plan-drawer-header"><div><span className="work-plan-header-icon"><CalendarClock size={18} /></span><div><h3 id="my-plans-title">我的安排</h3><p>已加载 {items.length} / {total} 条记录</p></div></div><button aria-label="关闭" className="work-plan-icon-button" onClick={onClose} type="button"><X size={19} /></button></header>
        <div className="work-plan-history-list">
          {items.map((item) => isOperation(item) ? (
            <OperationHistoryItem busy={busy} editable={item.member_sequence === latestOperationSequence && operationState(item) !== "replaced"} item={item} key={item.id} onEdit={onEdit} />
          ) : (
            <LegacyHistoryItem busy={busy} item={item} key={item.id} onCancel={onCancel} onEdit={onEdit} />
          ))}
          {!items.length ? <div className="work-plan-history-empty"><CalendarClock size={28} /><strong>暂无安排</strong></div> : null}
          {hasMore ? <button className="ghost work-plan-load-more" disabled={loadingMore} onClick={onLoadMore} type="button">{loadingMore ? "加载中..." : "加载更多"}</button> : null}
        </div>
      </aside>
    </div>
  );
}

function OperationHistoryItem({ busy, editable, item, onEdit }: { busy: boolean; editable: boolean; item: WorkPlanOperation; onEdit: (item: WorkPlanOperation) => void }) {
  const state = operationState(item);
  const muted = state !== "active";
  return (
    <article className={`work-plan-history-item ${historyClass(item)}`}>
      <header>
        <div>
          <span className={`work-plan-history-type operation-${item.operation_type}`}>{item.operation_type === "activate" ? "创建工作计划" : "取消计划"}</span>
          {state === "replaced" ? <span className="work-plan-cancelled-label">已被后续计划覆盖</span> : null}
          {state === "cancelled" ? <span className="work-plan-cancelled-label">灰色保留</span> : null}
        </div>
        <strong>{item.anchor_date}</strong>
      </header>
      <div className="work-plan-history-time">{operationInterval(item)}</div>
      {item.is_clipped ? <p className="work-plan-history-adjustment">原请求 {operationInterval(item, true)}，已按当时有效计划补齐为上方区间。</p> : null}
      {item.note ? <p>{item.note}</p> : null}
      <dl>
        <div><dt>操作于</dt><dd>{formatTimestamp(item.created_at)}</dd></div>
        <div><dt>顺序</dt><dd>第 {item.member_sequence} 次变更</dd></div>
        {muted ? <div><dt>当前显示</dt><dd>历史保留，不覆盖后续生效区间</dd></div> : null}
      </dl>
      {editable ? <footer><button className="ghost" disabled={busy} onClick={() => onEdit(item)} type="button"><Pencil size={15} />编辑操作</button></footer> : null}
    </article>
  );
}

function LegacyHistoryItem({ busy, item, onEdit, onCancel }: { busy: boolean; item: WorkPlan; onEdit: (plan: WorkPlan) => void; onCancel: (plan: WorkPlan) => void }) {
  return (
    <article className={`work-plan-history-item ${historyClass(item)}`}>
      <header><div><span className={`work-plan-history-type ${item.plan_type}`}>{item.plan_type === "work" ? "工作计划" : "取消计划"}</span>{item.is_cancelled ? <span className="work-plan-cancelled-label">已取消</span> : null}</div><strong>{item.plan_date}</strong></header>
      <div className="work-plan-history-time">{minuteLabel(item.start_minute)} - {minuteLabel(item.end_minute)}</div>
      {item.note ? <p>{item.note}</p> : null}
      <dl><div><dt>创建于</dt><dd>{formatTimestamp(item.created_at)}</dd></div><div><dt>最后修改</dt><dd>{formatTimestamp(item.updated_at)}</dd></div>{item.cancelled_at ? <div><dt>取消于</dt><dd>{formatTimestamp(item.cancelled_at)}</dd></div> : null}</dl>
      {!item.is_cancelled ? <footer><button className="ghost" disabled={busy} onClick={() => onEdit(item)} title="编辑计划" type="button"><Pencil size={15} />编辑计划</button><button className="danger-ghost" disabled={busy} onClick={() => onCancel(item)} type="button"><Ban size={15} />取消计划</button></footer> : null}
    </article>
  );
}

function formatTimestamp(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return "时间未知";
  return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Shanghai" }).format(parsed);
}
