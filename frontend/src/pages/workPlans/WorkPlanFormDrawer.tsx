import { Ban, CalendarDays, Check, Clock3, Plus, X } from "lucide-react";
import { useEffect, useMemo, useReducer, useState, type FormEvent } from "react";

import {
  isoDateRange,
  normalizeSelectedDates,
  resolveWeekdays,
  thirtyMinuteOptions,
  validateSelectedDates,
} from "./dateSelection";
import type {
  WorkPlan,
  WorkPlanCreatePayload,
  WorkPlanType,
  WorkPlanUpdatePayload,
} from "./types";

export type MoreDateMode = "single" | "range" | "multiple" | "weekday";

export type WorkPlanDraftState = {
  planType: WorkPlanType;
  selectedDates: string[];
  moreDateMode: MoreDateMode;
  rangeStart: string;
  rangeEnd: string;
  weekdays: number[];
  startTime: string;
  endTime: string;
  note: string;
  idempotencyKey: string;
};

export type WorkPlanDraftAction =
  | { type: "replace"; value: WorkPlanDraftState }
  | { type: "set-plan-type"; value: WorkPlanType }
  | { type: "set-dates"; value: string[] }
  | { type: "set-more-date-mode"; value: MoreDateMode }
  | { type: "set-range-start"; value: string }
  | { type: "set-range-end"; value: string }
  | { type: "set-weekdays"; value: number[] }
  | { type: "set-start-time"; value: string }
  | { type: "set-end-time"; value: string }
  | { type: "set-note"; value: string };

type WorkPlanFormDrawerProps = {
  open: boolean;
  serverToday: string;
  initialPlan?: WorkPlan | null;
  busy: boolean;
  onClose: () => void;
  onSubmit: (payload: WorkPlanCreatePayload | WorkPlanUpdatePayload) => Promise<void>;
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
  return globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function minuteToTime(value: number): string {
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
      planType: initialPlan.plan_type,
      selectedDates: [initialPlan.plan_date],
      moreDateMode: "single",
      rangeStart: initialPlan.plan_date,
      rangeEnd: initialPlan.plan_date,
      weekdays: [],
      startTime: minuteToTime(initialPlan.start_minute),
      endTime: minuteToTime(initialPlan.end_minute),
      note: initialPlan.note ?? "",
      idempotencyKey: createIdempotencyKey(),
    };
  }
  return {
    planType: "work",
    selectedDates: [serverToday],
    moreDateMode: "single",
    rangeStart: serverToday,
    rangeEnd: serverToday,
    weekdays: [],
    startTime: "09:00",
    endTime: "18:00",
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
    case "set-plan-type":
      return {
        ...state,
        planType: action.value,
        selectedDates:
          action.value === "temporary_unavailable" ? state.selectedDates.slice(0, 1) : state.selectedDates,
        moreDateMode: action.value === "temporary_unavailable" ? "single" : state.moreDateMode,
      };
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
    case "set-start-time":
      return { ...state, startTime: action.value };
    case "set-end-time":
      return { ...state, endTime: action.value };
    case "set-note":
      return { ...state, note: action.value };
  }
}

export function WorkPlanFormDrawer({
  open,
  serverToday,
  initialPlan = null,
  busy,
  onClose,
  onSubmit,
}: WorkPlanFormDrawerProps) {
  const [draft, dispatch] = useReducer(
    workPlanDraftReducer,
    undefined,
    () => createInitialWorkPlanDraft(serverToday, initialPlan),
  );
  const [showMoreDates, setShowMoreDates] = useState(false);
  const [manualDate, setManualDate] = useState(serverToday);
  const [error, setError] = useState("");
  const quickDates = useMemo(() => isoDateRange(serverToday, addIsoDays(serverToday, 6)), [serverToday]);
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
      dispatch({ type: "set-dates", value: isoDateRange(draft.rangeStart, draft.rangeEnd) });
      return;
    }
    if (draft.moreDateMode === "weekday") {
      dispatch({
        type: "set-dates",
        value: resolveWeekdays(draft.rangeStart, draft.rangeEnd, draft.weekdays),
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
    if (draft.endTime <= draft.startTime) {
      setError("结束时间必须晚于开始时间");
      return;
    }
    if (draft.planType === "temporary_unavailable" && draft.selectedDates.length !== 1) {
      setError("临时有事只能选择 1 个日期");
      return;
    }
    setError("");
    if (initialPlan) {
      await onSubmit({
        plan_type: draft.planType,
        start_time: draft.startTime,
        end_time: draft.endTime,
        note: draft.note.trim() || null,
        expected_updated_at: initialPlan.updated_at,
      });
      return;
    }
    await onSubmit({
      plan_type: draft.planType,
      dates: draft.selectedDates,
      start_time: draft.startTime,
      end_time: draft.endTime,
      note: draft.note.trim() || null,
      idempotency_key: draft.idempotencyKey,
    });
    dispatch({ type: "replace", value: createInitialWorkPlanDraft(serverToday) });
  };

  const toggleQuickDate = (value: string) => {
    const exists = draft.selectedDates.includes(value);
    const next = exists
      ? draft.selectedDates.filter((date) => date !== value)
      : [...draft.selectedDates, value];
    dispatch({
      type: "set-dates",
      value: draft.planType === "temporary_unavailable" ? [value] : next,
    });
  };

  return (
    <div className={`work-plan-drawer-layer ${open ? "open" : ""}`} inert={!open}>
      <button aria-label="关闭填写计划" className="work-plan-drawer-backdrop" onClick={onClose} type="button" />
      <aside
        aria-labelledby="work-plan-form-title"
        aria-modal="true"
        className={`work-plan-drawer ${open ? "open" : ""}`}
        role="dialog"
      >
        <header className="work-plan-drawer-header">
          <div>
            <span className="work-plan-header-icon"><CalendarDays size={18} /></span>
            <div><h3 id="work-plan-form-title">{editing ? "编辑计划" : "填写我的计划"}</h3><p>Asia/Shanghai</p></div>
          </div>
          <button aria-label="关闭" className="work-plan-icon-button" onClick={onClose} title="关闭" type="button"><X size={19} /></button>
        </header>

        <form className="work-plan-form" onSubmit={submit}>
          <div className="work-plan-form-body">
            <fieldset className="work-plan-fieldset">
              <legend>计划类型</legend>
              <div className="work-plan-segmented">
                <button className={draft.planType === "work" ? "active" : ""} onClick={() => dispatch({ type: "set-plan-type", value: "work" })} type="button"><Clock3 size={16} />工作时间</button>
                <button className={draft.planType === "temporary_unavailable" ? "active unavailable" : ""} onClick={() => dispatch({ type: "set-plan-type", value: "temporary_unavailable" })} type="button"><Ban size={16} />临时有事</button>
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
                {draft.planType === "work" ? <button className="work-plan-more-date-toggle" onClick={() => setShowMoreDates((value) => !value)} type="button"><Plus size={15} />{showMoreDates ? "收起更多日期" : "更多日期"}</button> : null}
                {showMoreDates && draft.planType === "work" ? (
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
                <label>开始时间<select onChange={(event) => dispatch({ type: "set-start-time", value: event.target.value })} value={draft.startTime}>{thirtyMinuteOptions().map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
                <span>至</span>
                <label>结束时间<select onChange={(event) => dispatch({ type: "set-end-time", value: event.target.value })} value={draft.endTime}>{thirtyMinuteOptions().map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</select></label>
              </div>
            </fieldset>

            <label className="work-plan-note-field">备注<textarea maxLength={500} onChange={(event) => dispatch({ type: "set-note", value: event.target.value })} placeholder="可选" rows={4} value={draft.note} /><span>{draft.note.length}/500</span></label>
            {error ? <div className="work-plan-form-error" role="alert">{error}</div> : null}
          </div>
          <footer className="work-plan-drawer-footer"><button className="ghost" disabled={busy} onClick={onClose} type="button">取消</button><button disabled={busy} type="submit">{busy ? "提交中..." : editing ? "保存修改" : "提交计划"}</button></footer>
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
