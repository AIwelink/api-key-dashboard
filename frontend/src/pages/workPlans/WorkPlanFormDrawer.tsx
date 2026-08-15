import { Ban, CalendarDays, Check, Clock3, Plus, X } from "lucide-react";
import { useEffect, useMemo, useReducer, useState, type FormEvent } from "react";

import { useModalFocus } from "../../hooks/useModalFocus";
import {
  fortyEightHourOptions,
  formatOffsetInterval,
  isoDateRange,
  normalizeSelectedDates,
  resolveWeekdays,
  validateSelectedDates,
} from "./dateSelection";
import type {
  WorkPlan,
  WorkPlanOperationCreatePayload,
  WorkPlanOperationType,
  WorkPlanUpdatePayload,
} from "./types";

export type MoreDateMode = "single" | "range" | "multiple" | "weekday";

export type WorkPlanDraftState = {
  operationType: WorkPlanOperationType;
  selectedDates: string[];
  moreDateMode: MoreDateMode;
  rangeStart: string;
  rangeEnd: string;
  weekdays: number[];
  startOffsetMinute: number;
  endOffsetMinute: number;
  note: string;
  idempotencyKey: string;
};

export type WorkPlanDraftAction =
  | { type: "replace"; value: WorkPlanDraftState }
  | { type: "set-operation-type"; value: WorkPlanOperationType; fallbackDate?: string }
  | { type: "set-dates"; value: string[] }
  | { type: "set-more-date-mode"; value: MoreDateMode }
  | { type: "set-range-start"; value: string }
  | { type: "set-range-end"; value: string }
  | { type: "set-weekdays"; value: number[] }
  | { type: "set-start-offset"; value: number }
  | { type: "set-end-offset"; value: number }
  | { type: "set-note"; value: string };

type WorkPlanFormDrawerProps = {
  open: boolean;
  serverToday: string;
  initialPlan?: WorkPlan | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: WorkPlanOperationCreatePayload | WorkPlanUpdatePayload) => Promise<boolean>;
};

const WEEKDAYS = [
  { value: 1, label: "一" },
  { value: 2, label: "二" },
  { value: 3, label: "三" },
  { value: 4, label: "四" },
  { value: 5, label: "五" },
  { value: 6, label: "六" },
  { value: 0, label: "日" },
] as const;

function createIdempotencyKey(): string {
  if (globalThis.crypto?.randomUUID) return globalThis.crypto.randomUUID();
  const bytes = new Uint8Array(16);
  if (globalThis.crypto?.getRandomValues) {
    globalThis.crypto.getRandomValues(bytes);
  } else {
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Math.floor(Math.random() * 256);
    }
  }
  bytes[6] = (bytes[6] & 0x0f) | 0x40;
  bytes[8] = (bytes[8] & 0x3f) | 0x80;
  const value = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `${value.slice(0, 8)}-${value.slice(8, 12)}-${value.slice(12, 16)}-${value.slice(16, 20)}-${value.slice(20)}`;
}

function minuteToTime(value: number): string {
  if (value === 1_440) return "24:00";
  const hour = Math.floor(value / 60);
  const minute = value % 60;
  return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
}

function addIsoDays(value: string, days: number): string {
  const [year, month, day] = value.split("-").map(Number);
  const date = new Date(Date.UTC(year, month - 1, day + days));
  return [date.getUTCFullYear(), date.getUTCMonth() + 1, date.getUTCDate()]
    .map((part, index) => String(part).padStart(index === 0 ? 4 : 2, "0"))
    .join("-");
}

export function createInitialWorkPlanDraft(serverToday: string, initialPlan?: WorkPlan | null): WorkPlanDraftState {
  if (initialPlan) {
    return {
      operationType: initialPlan.plan_type === "work" ? "activate" : "cancel",
      selectedDates: [initialPlan.plan_date],
      moreDateMode: "single",
      rangeStart: initialPlan.plan_date,
      rangeEnd: initialPlan.plan_date,
      weekdays: [],
      startOffsetMinute: initialPlan.start_minute,
      endOffsetMinute: initialPlan.end_minute,
      note: initialPlan.note ?? "",
      idempotencyKey: createIdempotencyKey(),
    };
  }
  return {
    operationType: "activate",
    selectedDates: [serverToday],
    moreDateMode: "single",
    rangeStart: serverToday,
    rangeEnd: serverToday,
    weekdays: [],
    startOffsetMinute: 9 * 60,
    endOffsetMinute: 18 * 60,
    note: "",
    idempotencyKey: createIdempotencyKey(),
  };
}

export function workPlanDraftReducer(
  state: WorkPlanDraftState,
  action: WorkPlanDraftAction,
): WorkPlanDraftState {
  switch (action.type) {
    case "replace":
      return action.value;
    case "set-operation-type": {
      const cancellationDate = state.selectedDates[0]
        || action.fallbackDate
        || state.rangeStart
        || state.rangeEnd;
      return {
        ...state,
        operationType: action.value,
        selectedDates:
          action.value === "cancel" && cancellationDate ? [cancellationDate] : state.selectedDates,
        moreDateMode: action.value === "cancel" ? "single" : state.moreDateMode,
      };
    }
    case "set-dates":
      return { ...state, selectedDates: normalizeSelectedDates(action.value) };
    case "set-more-date-mode":
      return { ...state, moreDateMode: action.value };
    case "set-range-start":
      return { ...state, rangeStart: action.value };
    case "set-range-end":
      return { ...state, rangeEnd: action.value };
    case "set-weekdays":
      return { ...state, weekdays: action.value };
    case "set-start-offset":
      return { ...state, startOffsetMinute: action.value };
    case "set-end-offset":
      return { ...state, endOffsetMinute: action.value };
    case "set-note":
      return { ...state, note: action.value };
  }
}

export async function resetDraftAfterSuccessfulSubmit(
  submit: () => Promise<boolean>,
  reset: () => void,
): Promise<boolean> {
  const submitted = await submit();
  if (submitted) reset();
  return submitted;
}

export function WorkPlanFormDrawer({
  open,
  serverToday,
  initialPlan = null,
  busy,
  onClose,
  onSubmit,
}: WorkPlanFormDrawerProps) {
  const dialogRef = useModalFocus<HTMLElement>(open, onClose);
  const [draft, dispatch] = useReducer(
    workPlanDraftReducer,
    undefined,
    () => createInitialWorkPlanDraft(serverToday, initialPlan),
  );
  const [showMoreDates, setShowMoreDates] = useState(false);
  const [manualDate, setManualDate] = useState(serverToday);
  const [error, setError] = useState("");
  const quickDates = useMemo(() => isoDateRange(serverToday, addIsoDays(serverToday, 6)), [serverToday]);
  const offsetOptions = useMemo(() => fortyEightHourOptions(), []);
  const editing = Boolean(initialPlan);

  useEffect(() => {
    if (!open) return;
    dispatch({ type: "replace", value: createInitialWorkPlanDraft(serverToday, initialPlan) });
    setManualDate(serverToday);
    setShowMoreDates(false);
    setError("");
  }, [initialPlan, open, serverToday]);

  const updateDatesFromMode = () => {
    if (draft.moreDateMode === "single") {
      dispatch({ type: "set-dates", value: [manualDate] });
      return;
    }
    if (draft.moreDateMode === "range") {
      dispatch({ type: "set-dates", value: isoDateRange(draft.rangeStart, draft.rangeEnd, 6) });
      return;
    }
    if (draft.moreDateMode === "weekday") {
      dispatch({
        type: "set-dates",
        value: resolveWeekdays(draft.rangeStart, draft.rangeEnd, draft.weekdays, 6),
      });
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const dateError = validateSelectedDates(draft.selectedDates);
    if (dateError) {
      setError(dateError);
      return;
    }
    if (draft.endOffsetMinute <= draft.startOffsetMinute) {
      setError("结束时间必须晚于开始时间");
      return;
    }
    if (draft.operationType === "cancel" && draft.selectedDates.length !== 1) {
      setError("取消计划只能选择 1 个日期");
      return;
    }
    setError("");
    if (initialPlan) {
      await onSubmit({
        plan_type: draft.operationType === "activate" ? "work" : "temporary_unavailable",
        start_time: minuteToTime(draft.startOffsetMinute),
        end_time: minuteToTime(draft.endOffsetMinute),
        note: draft.note.trim() || null,
        expected_updated_at: initialPlan.updated_at,
      });
      return;
    }
    await resetDraftAfterSuccessfulSubmit(
      () => onSubmit({
        operation_type: draft.operationType,
        anchor_dates: draft.selectedDates,
        start_offset_minute: draft.startOffsetMinute,
        end_offset_minute: draft.endOffsetMinute,
        note: draft.note.trim() || null,
        idempotency_key: draft.idempotencyKey,
      }),
      () => dispatch({ type: "replace", value: createInitialWorkPlanDraft(serverToday) }),
    );
  };

  const toggleQuickDate = (value: string) => {
    const exists = draft.selectedDates.includes(value);
    const next = exists
      ? draft.selectedDates.filter((date) => date !== value)
      : [...draft.selectedDates, value];
    dispatch({
      type: "set-dates",
      value: draft.operationType === "cancel" ? [value] : next,
    });
  };

  return (
    <div className={`work-plan-drawer-layer ${open ? "open" : ""}`} inert={!open}>
      <button aria-label="关闭填写计划" className="work-plan-drawer-backdrop" onClick={onClose} type="button" />
      <aside
        aria-labelledby="work-plan-form-title"
        aria-modal="true"
        className={`work-plan-drawer ${open ? "open" : ""}`}
        ref={dialogRef}
        role="dialog"
        tabIndex={-1}
      >
        <header className="work-plan-drawer-header">
          <div>
            <span className="work-plan-header-icon"><CalendarDays size={18} /></span>
            <div><h3 id="work-plan-form-title">{editing ? "编辑计划" : "工作计划"}</h3><p>Asia/Shanghai · 最长 48 小时</p></div>
          </div>
          <button aria-label="关闭" className="work-plan-icon-button" onClick={onClose} title="关闭" type="button"><X size={19} /></button>
        </header>

        <form className="work-plan-form" onSubmit={submit}>
          <div className="work-plan-form-body">
            <fieldset className="work-plan-fieldset">
              <legend>操作类型</legend>
              <div className="work-plan-segmented">
                <button className={draft.operationType === "activate" ? "active" : ""} onClick={() => dispatch({ type: "set-operation-type", value: "activate" })} type="button"><Clock3 size={16} />创建工作计划</button>
                <button className={draft.operationType === "cancel" ? "active unavailable" : ""} onClick={() => dispatch({ type: "set-operation-type", value: "cancel", fallbackDate: serverToday })} type="button"><Ban size={16} />取消计划</button>
              </div>
            </fieldset>

            {!editing && (
              <fieldset className="work-plan-fieldset">
                <legend>日期</legend>
                <div className="work-plan-quick-dates">
                  {quickDates.map((value) => {
                    const selected = draft.selectedDates.includes(value);
                    return <button aria-pressed={selected} className={selected ? "selected" : ""} key={value} onClick={() => toggleQuickDate(value)} type="button"><span>{formatWeekday(value)}</span><strong>{value.slice(5).replace("-", "/")}</strong>{selected ? <Check size={14} /> : null}</button>;
                  })}
                </div>
                {draft.operationType === "activate" ? <button className="work-plan-more-date-toggle" onClick={() => setShowMoreDates((value) => !value)} type="button"><Plus size={15} />{showMoreDates ? "收起更多日期" : "更多日期"}</button> : null}
                {showMoreDates && draft.operationType === "activate" ? (
                  <div className="work-plan-more-dates">
                    <div className="work-plan-mode-tabs" role="tablist">
                      {(["single", "range", "multiple", "weekday"] as const).map((mode) => <button aria-selected={draft.moreDateMode === mode} className={draft.moreDateMode === mode ? "active" : ""} key={mode} onClick={() => dispatch({ type: "set-more-date-mode", value: mode })} role="tab" type="button">{{ single: "单日", range: "范围", multiple: "多日", weekday: "星期" }[mode]}</button>)}
                    </div>
                    {draft.moreDateMode === "single" ? <input aria-label="指定日期" min={serverToday} onChange={(event) => setManualDate(event.target.value)} type="date" value={manualDate} /> : null}
                    {draft.moreDateMode === "range" || draft.moreDateMode === "weekday" ? <div className="work-plan-range-inputs"><label>开始<input min={serverToday} onChange={(event) => dispatch({ type: "set-range-start", value: event.target.value })} type="date" value={draft.rangeStart} /></label><label>结束<input min={draft.rangeStart} onChange={(event) => dispatch({ type: "set-range-end", value: event.target.value })} type="date" value={draft.rangeEnd} /></label></div> : null}
                    {draft.moreDateMode === "multiple" ? <div className="work-plan-multi-date"><input aria-label="添加指定日期" min={serverToday} onChange={(event) => setManualDate(event.target.value)} type="date" value={manualDate} /><button onClick={() => dispatch({ type: "set-dates", value: [...draft.selectedDates, manualDate] })} type="button"><Plus size={15} />添加</button></div> : null}
                    {draft.moreDateMode === "weekday" ? <div className="work-plan-weekdays">{WEEKDAYS.map((day) => <label key={day.value}><input checked={draft.weekdays.includes(day.value)} onChange={() => dispatch({ type: "set-weekdays", value: draft.weekdays.includes(day.value) ? draft.weekdays.filter((value) => value !== day.value) : [...draft.weekdays, day.value] })} type="checkbox" /><span>{day.label}</span></label>)}</div> : null}
                    {draft.moreDateMode !== "multiple" ? <button className="work-plan-apply-dates" onClick={updateDatesFromMode} type="button">应用日期</button> : null}
                  </div>
                ) : null}
                <div className="work-plan-selected-dates">{draft.selectedDates.map((value) => <span key={value}>{value}<button aria-label={`移除 ${value}`} onClick={() => dispatch({ type: "set-dates", value: draft.selectedDates.filter((date) => date !== value) })} type="button"><X size={12} /></button></span>)}</div>
              </fieldset>
            )}

            <fieldset className="work-plan-fieldset">
              <legend>时间</legend>
              <div className="work-plan-time-fields">
                <label>开始时间<select onChange={(event) => dispatch({ type: "set-start-offset", value: Number(event.target.value) })} value={draft.startOffsetMinute}>{offsetOptions.slice(0, -1).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
                <span>至</span>
                <label>结束时间<select onChange={(event) => dispatch({ type: "set-end-offset", value: Number(event.target.value) })} value={draft.endOffsetMinute}>{offsetOptions.slice(1).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
              </div>
              <div className={`work-plan-time-preview ${draft.operationType}`}><Clock3 size={14} /><span>{draft.selectedDates[0] || serverToday}</span><strong>{formatOffsetInterval(draft.startOffsetMinute, draft.endOffsetMinute)}</strong></div>
            </fieldset>

            <label className="work-plan-note-field">备注<textarea maxLength={500} onChange={(event) => dispatch({ type: "set-note", value: event.target.value })} placeholder="可选" rows={4} value={draft.note} /><span>{draft.note.length}/500</span></label>
            {error ? <div className="work-plan-form-error" role="alert">{error}</div> : null}
          </div>
          <footer className="work-plan-drawer-footer"><button className="ghost" disabled={busy} onClick={onClose} type="button">关闭</button><button className={draft.operationType === "cancel" ? "work-plan-submit-cancel" : ""} disabled={busy} type="submit">{busy ? "提交中..." : editing ? "保存修改" : draft.operationType === "activate" ? "创建工作计划" : "提交取消"}</button></footer>
        </form>
      </aside>
    </div>
  );
}

function formatWeekday(value: string): string {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat("zh-CN", { weekday: "short", timeZone: "UTC" }).format(
    new Date(Date.UTC(year, month - 1, day)),
  );
}
