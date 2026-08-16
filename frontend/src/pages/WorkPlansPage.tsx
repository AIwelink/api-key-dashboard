import { CalendarPlus, History, RefreshCw, TriangleAlert, UsersRound } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import type { User } from "../types";
import { errorMessage } from "../utils/format";
import { MyPlansDrawer } from "./workPlans/MyPlansDrawer";
import { createIdempotencyKey, WorkPlanFormDrawer } from "./workPlans/WorkPlanFormDrawer";
import { WorkPlanSchedule } from "./workPlans/WorkPlanSchedule";
import type {
  WorkPlan,
  WorkPlanCreatePayload,
  WorkPlanHistoryItem,
  WorkPlanOperationCreatePayload,
  WorkPlanOperationUpdatePayload,
  WorkPlanHistoryResponse,
  WorkPlanMutationResult,
  WorkPlanOperation,
  WorkPlanPriorityResult,
  WorkPlanRange,
  WorkPlanScheduleResponse,
  WorkPlanSegment,
  WorkPlanUpdatePayload,
} from "./workPlans/types";
import "./WorkPlansPage.css";

type WorkPlansPageProps = {
  token: string;
  currentUser: User;
  showToast: (message: string, isError?: boolean) => void;
};

export type CreateRequestState = {
  busy: boolean;
  idempotencyKey: string | null;
};

export const initialRequestState: CreateRequestState = {
  busy: false,
  idempotencyKey: null,
};

export function drawerExitDelay(reducedMotion: boolean): number {
  return reducedMotion ? 0 : 280;
}

export function beginCreate(state: CreateRequestState, idempotencyKey: string): CreateRequestState {
  if (state.busy) return state;
  return { busy: true, idempotencyKey };
}

export type LatestRequestGuard = {
  begin: () => number;
  isCurrent: (requestId: number) => boolean;
};

export function createLatestRequestGuard(): LatestRequestGuard {
  let currentRequestId = 0;
  return {
    begin: () => {
      currentRequestId += 1;
      return currentRequestId;
    },
    isCurrent: (requestId) => requestId === currentRequestId,
  };
}

export function mutationErrorMessage(result: WorkPlanMutationResult): string | null {
  const unsuccessful = result.results.filter(
    (item) => item.outcome === "failed" || item.outcome === "uncertain",
  );
  if (!unsuccessful.length) return null;
  return unsuccessful
    .map((item) => `${item.plan_date}：${item.error || "保存失败，请保留当前内容后重试"}`)
    .join("；");
}

function isOperation(item: WorkPlanHistoryItem): item is WorkPlanOperation {
  return "record_kind" in item && item.record_kind === "operation";
}

export function buildWorkPlanCancellationPayload(
  operation: WorkPlanOperation,
  segment: Pick<WorkPlanSegment, "start_at" | "end_at"> | null,
  idempotencyKey: string,
): WorkPlanOperationCreatePayload {
  const anchorStart = Date.parse(`${operation.anchor_date}T00:00:00+08:00`);
  const resolveOffset = (value: string, fallback: number): number => {
    const timestamp = Date.parse(value);
    if (!Number.isFinite(anchorStart) || !Number.isFinite(timestamp)) return fallback;
    return Math.round((timestamp - anchorStart) / 60_000);
  };
  const startOffsetMinute = resolveOffset(
    segment?.start_at || operation.effective_start_at,
    operation.effective_start_offset_minute,
  );
  const endOffsetMinute = resolveOffset(
    segment?.end_at || operation.effective_end_at,
    operation.effective_end_offset_minute,
  );
  return {
    operation_type: "cancel",
    anchor_dates: [operation.anchor_date],
    start_offset_minute: startOffsetMinute,
    end_offset_minute: endOffsetMinute,
    note: null,
    idempotency_key: idempotencyKey,
  };
}

export function cancellationStartsTooSoon(
  payload: WorkPlanOperationCreatePayload,
  observedAt: string,
): boolean {
  const observedTimestamp = Date.parse(observedAt);
  const anchorStart = Date.parse(`${payload.anchor_dates[0]}T00:00:00+08:00`);
  if (!Number.isFinite(observedTimestamp) || !Number.isFinite(anchorStart)) return true;
  const startTimestamp = anchorStart + payload.start_offset_minute * 60_000;
  return startTimestamp < observedTimestamp + 60 * 60_000;
}

type ScheduleStaleNoticeProps = {
  message: string;
  refreshing: boolean;
  onRetry: () => void;
};

export function ScheduleStaleNotice({ message, refreshing, onRetry }: ScheduleStaleNoticeProps) {
  return (
    <div className="work-plan-stale-notice" role="alert">
      <TriangleAlert aria-hidden="true" size={18} />
      <div>
        <strong>团队排班暂未更新</strong>
        <span>{message}。当前显示的数据可能不是最新。</span>
      </div>
      <button className="ghost" disabled={refreshing} onClick={onRetry} type="button">
        <RefreshCw className={refreshing ? "spinning" : ""} size={15} />
        {refreshing ? "加载中" : "重新加载"}
      </button>
    </div>
  );
}

function historyDate(item: WorkPlanHistoryItem): string {
  return "anchor_date" in item ? item.anchor_date : item.plan_date;
}

function compareHistoryItems(left: WorkPlanHistoryItem, right: WorkPlanHistoryItem): number {
  return historyDate(right).localeCompare(historyDate(left))
    || right.created_at.localeCompare(left.created_at)
    || Number("record_kind" in right) - Number("record_kind" in left)
    || right.id.localeCompare(left.id);
}

export function mergeWorkPlans(existing: WorkPlan[], changed: WorkPlan[]): WorkPlan[] {
  const byId = new Map(existing.map((plan) => [plan.id, plan]));
  changed.forEach((plan) => byId.set(plan.id, plan));
  return Array.from(byId.values()).sort(compareHistoryItems);
}

export function mergeHistoryItems(
  existing: WorkPlanHistoryItem[],
  changed: WorkPlanHistoryItem[],
): WorkPlanHistoryItem[] {
  const byId = new Map(existing.map((item) => [item.id, item]));
  changed.forEach((item) => byId.set(item.id, item));
  return Array.from(byId.values()).sort(compareHistoryItems);
}

export function operationHistoryFromMutation(result: WorkPlanMutationResult): WorkPlanOperation[] {
  const operations = new Map<string, WorkPlanOperation>();
  for (const item of result.results) {
    if (item.operation) operations.set(item.operation.id, item.operation);
    item.operations?.forEach((operation) => operations.set(operation.id, operation));
  }
  return Array.from(operations.values()).sort(compareHistoryItems);
}

export function mergeHistoryPage(
  current: WorkPlanHistoryResponse,
  next: WorkPlanHistoryResponse,
): WorkPlanHistoryResponse {
  return {
    items: mergeHistoryItems(current.items, next.items),
    total: next.total,
    has_more: next.has_more,
    next_cursor: next.next_cursor,
  };
}

export function withUpdatedMemberPriority(
  schedule: WorkPlanScheduleResponse,
  memberId: string,
  priority: number | null,
): WorkPlanScheduleResponse {
  return {
    ...schedule,
    members: schedule.members.map((member) => member.member_id === memberId
      ? { ...member, work_plan_priority: priority }
      : member),
  };
}

export function mergeSchedulePage(
  current: WorkPlanScheduleResponse,
  next: WorkPlanScheduleResponse,
): WorkPlanScheduleResponse {
  const segmentKey = (segment: NonNullable<WorkPlanScheduleResponse["segments"]>[number]) => [
    segment.member_id,
    segment.state,
    segment.start_at,
    segment.end_at,
    segment.winning_operation_id,
  ].join("\u0000");
  const members = new Map(current.members.map((member) => [member.member_id, member]));
  next.members.forEach((member) => members.set(member.member_id, member));
  const plans = new Map(current.plans.map((plan) => [plan.id, plan]));
  next.plans.forEach((plan) => plans.set(plan.id, plan));
  const segments = new Map(
    (current.segments || []).map((segment) => [segmentKey(segment), segment]),
  );
  (next.segments || []).forEach((segment) => segments.set(segmentKey(segment), segment));
  return {
    ...next,
    members: Array.from(members.values()),
    plans: Array.from(plans.values()).sort((left, right) => left.plan_date.localeCompare(right.plan_date)
      || left.start_minute - right.start_minute
      || left.id.localeCompare(right.id)),
    segments: current.segments || next.segments
      ? Array.from(segments.values()).sort((left, right) => left.start_at.localeCompare(right.start_at)
        || left.member_id.localeCompare(right.member_id)
        || left.winning_operation_id.localeCompare(right.winning_operation_id))
      : undefined,
    start_date: current.start_date < next.start_date ? current.start_date : next.start_date,
    end_date: current.end_date > next.end_date ? current.end_date : next.end_date,
  };
}

export function reconcileSchedulePlans(
  schedule: WorkPlanScheduleResponse,
  changed: WorkPlan[],
  options: { includeCancelled: boolean; memberId: string },
): WorkPlanScheduleResponse {
  const changedById = new Map(changed.map((plan) => [plan.id, plan]));
  const existingIds = new Set(schedule.plans.map((plan) => plan.id));
  const next = schedule.plans.flatMap((plan) => {
    const replacement = changedById.get(plan.id);
    if (!replacement) return [plan];
    if (replacement.is_cancelled && !options.includeCancelled) return [];
    return [replacement];
  });
  changed.forEach((plan) => {
    if (existingIds.has(plan.id) || (plan.is_cancelled && !options.includeCancelled)) return;
    const inDateRange = plan.plan_date >= schedule.start_date && plan.plan_date <= schedule.end_date;
    const memberMatches = !options.memberId || options.memberId === plan.member_id;
    if (inDateRange && memberMatches) next.push(plan);
  });
  next.sort((left, right) => left.plan_date.localeCompare(right.plan_date)
    || left.start_minute - right.start_minute
    || left.id.localeCompare(right.id));
  return { ...schedule, plans: next };
}

function todayInShanghai(): string {
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Shanghai",
  }).formatToParts(new Date());
  const value = new Map(parts.map((part) => [part.type, part.value]));
  return `${value.get("year")}-${value.get("month")}-${value.get("day")}`;
}

function shanghaiDateFromTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return todayInShanghai();
  const parts = new Intl.DateTimeFormat("en-CA", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    timeZone: "Asia/Shanghai",
  }).formatToParts(date);
  const result = new Map(parts.map((part) => [part.type, part.value]));
  return `${result.get("year")}-${result.get("month")}-${result.get("day")}`;
}

function emptySchedule(): WorkPlanScheduleResponse {
  const today = todayInShanghai();
  return {
    members: [],
    plans: [],
    start_date: today,
    end_date: today,
    observed_at: new Date().toISOString(),
    timezone: "Asia/Shanghai",
    total: 0,
    has_more: false,
    next_cursor: null,
  };
}

function emptyHistory(): WorkPlanHistoryResponse {
  return { items: [], total: 0, has_more: false, next_cursor: null };
}

export function WorkPlansPage({ token, currentUser, showToast }: WorkPlansPageProps) {
  const [schedule, setSchedule] = useState<WorkPlanScheduleResponse>(() => emptySchedule());
  const [history, setHistory] = useState<WorkPlanHistoryResponse>(() => emptyHistory());
  const [range, setRange] = useState<WorkPlanRange>("7d");
  const [memberId, setMemberId] = useState("");
  const [includeCancelled, setIncludeCancelled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [scheduleError, setScheduleError] = useState("");
  const [mutationBusy, setMutationBusy] = useState(false);
  const [priorityBusyMemberId, setPriorityBusyMemberId] = useState("");
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [editingPlan, setEditingPlan] = useState<WorkPlanHistoryItem | null>(null);
  const [cancelPlan, setCancelPlan] = useState<WorkPlanHistoryItem | null>(null);
  const [cancelSegment, setCancelSegment] = useState<Pick<WorkPlanSegment, "start_at" | "end_at"> | null>(null);
  const [cancelOperationKey, setCancelOperationKey] = useState<string | null>(null);
  const [scheduleRequestGuard] = useState(createLatestRequestGuard);
  const drawerHandoffTimer = useRef<number | null>(null);
  const canManageAll = currentUser.role === "owner" || currentUser.role === "admin";

  const schedulePath = useMemo(() => {
    const params = new URLSearchParams({ range });
    if (memberId) params.append("member_id", memberId);
    if (includeCancelled && canManageAll && range === "all") params.set("include_cancelled", "true");
    return `/work-plans/schedule?${params.toString()}`;
  }, [canManageAll, includeCancelled, memberId, range]);

  const loadSchedule = useCallback(async (notify = false) => {
    const requestId = scheduleRequestGuard.begin();
    setRefreshing(true);
    try {
      const next = await api<WorkPlanScheduleResponse>(schedulePath, token);
      if (!scheduleRequestGuard.isCurrent(requestId)) return;
      setSchedule(next);
      setScheduleError("");
    } catch (error) {
      if (!scheduleRequestGuard.isCurrent(requestId)) return;
      setScheduleError(errorMessage(error));
      if (notify) showToast(errorMessage(error), true);
      throw error;
    } finally {
      if (scheduleRequestGuard.isCurrent(requestId)) {
        setRefreshing(false);
        setLoading(false);
      }
    }
  }, [schedulePath, scheduleRequestGuard, showToast, token]);

  const loadHistory = useCallback(async (notify = false, pageCursor: string | null = null) => {
    try {
      const params = new URLSearchParams({ limit: "100" });
      if (pageCursor) params.set("cursor", pageCursor);
      const next = await api<WorkPlanHistoryResponse>(`/work-plans/mine?${params.toString()}`, token);
      setHistory((current) => pageCursor ? mergeHistoryPage(current, next) : next);
    } catch (error) {
      if (notify) showToast(errorMessage(error), true);
      throw error;
    }
  }, [showToast, token]);

  const refreshAll = useCallback(async (notify = false) => {
    const results = await Promise.allSettled([loadSchedule(notify), loadHistory(notify)]);
    if (results.some((result) => result.status === "rejected")) {
      throw new Error("工作计划刷新失败");
    }
  }, [loadHistory, loadSchedule]);

  const applyLocalMutation = useCallback((changed: WorkPlan[]) => {
    const actorIds = new Set([currentUser.id, currentUser.email].filter(Boolean));
    const personalChanges = changed.filter((plan) => actorIds.has(plan.member_id));
    if (personalChanges.length) {
      setHistory((current) => {
        const items = mergeHistoryItems(current.items, personalChanges);
        return { ...current, items, total: Math.max(current.total, items.length) };
      });
    }
    setSchedule((current) => reconcileSchedulePlans(current, changed, { includeCancelled, memberId }));
  }, [currentUser.email, currentUser.id, includeCancelled, memberId]);

  const applyOperationHistory = useCallback((changed: WorkPlanOperation[]) => {
    if (!changed.length) return;
    const actorIds = new Set([currentUser.id, currentUser.email].filter(Boolean));
    const personalChanges = changed.filter((operation) => actorIds.has(operation.member_id));
    if (!personalChanges.length) return;
    setHistory((current) => {
      const items = mergeHistoryItems(current.items, personalChanges);
      return { ...current, items, total: Math.max(current.total, items.length) };
    });
  }, [currentUser.email, currentUser.id]);

  const refreshAfterMutation = useCallback((successMessage: string) => {
    showToast(successMessage);
    void refreshAll(false).catch(() => {
      showToast(`${successMessage}，但列表刷新失败，请手动刷新`, true);
    });
  }, [refreshAll, showToast]);

  useEffect(() => {
    loadSchedule(true).catch(() => undefined);
  }, [loadSchedule]);

  useEffect(() => {
    loadHistory(true).catch(() => undefined);
  }, [loadHistory]);

  useEffect(() => {
    if (range !== "all" && includeCancelled) setIncludeCancelled(false);
  }, [includeCancelled, range]);

  useEffect(() => () => {
    if (drawerHandoffTimer.current !== null) window.clearTimeout(drawerHandoffTimer.current);
  }, []);

  usePageAutoRefresh(() => refreshAll(false), {
    paused: formOpen || historyOpen || mutationBusy,
    onError: () => undefined,
  });

  const loadMoreHistory = useCallback(async () => {
    if (!history.next_cursor || historyLoadingMore) return;
    setHistoryLoadingMore(true);
    try {
      await loadHistory(false, history.next_cursor);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setHistoryLoadingMore(false);
    }
  }, [history.next_cursor, historyLoadingMore, loadHistory, showToast]);

  const loadOlderSchedule = useCallback(async () => {
    if (!schedule.next_cursor || refreshing) return;
    const requestId = scheduleRequestGuard.begin();
    setRefreshing(true);
    try {
      const separator = schedulePath.includes("?") ? "&" : "?";
      const next = await api<WorkPlanScheduleResponse>(
        `${schedulePath}${separator}cursor=${encodeURIComponent(schedule.next_cursor)}`,
        token,
      );
      if (!scheduleRequestGuard.isCurrent(requestId)) return;
      setSchedule((current) => mergeSchedulePage(current, next));
      setScheduleError("");
    } catch (error) {
      if (!scheduleRequestGuard.isCurrent(requestId)) return;
      setScheduleError(errorMessage(error));
      showToast(errorMessage(error), true);
    } finally {
      if (scheduleRequestGuard.isCurrent(requestId)) setRefreshing(false);
    }
  }, [refreshing, schedule.next_cursor, schedulePath, scheduleRequestGuard, showToast, token]);

  const openCreate = () => {
    if (drawerHandoffTimer.current !== null) window.clearTimeout(drawerHandoffTimer.current);
    drawerHandoffTimer.current = null;
    setEditingPlan(null);
    setFormOpen(true);
  };

  const openEdit = (plan: WorkPlanHistoryItem) => {
    if (drawerHandoffTimer.current !== null) window.clearTimeout(drawerHandoffTimer.current);
    setHistoryOpen(false);
    const openForm = () => {
      drawerHandoffTimer.current = null;
      setEditingPlan(plan);
      setFormOpen(true);
    };
    const reducedMotion = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches ?? false;
    const delay = historyOpen ? drawerExitDelay(reducedMotion) : 0;
    if (delay === 0) openForm();
    else drawerHandoffTimer.current = window.setTimeout(openForm, delay);
  };

  const openCancel = useCallback((plan: WorkPlanHistoryItem, segment?: WorkPlanSegment) => {
    setCancelPlan(plan);
    setCancelSegment(segment ? { start_at: segment.start_at, end_at: segment.end_at } : null);
    setCancelOperationKey(isOperation(plan) ? createIdempotencyKey() : null);
  }, []);

  const submitPlan = async (
    payload: WorkPlanCreatePayload | WorkPlanOperationCreatePayload | WorkPlanOperationUpdatePayload | WorkPlanUpdatePayload,
  ) => {
    setMutationBusy(true);
    try {
      if (editingPlan) {
        if ("record_kind" in editingPlan) {
          const result = await api<WorkPlanMutationResult>(`/work-plans/${editingPlan.id}`, token, {
            method: "PATCH",
            body: JSON.stringify(payload),
          });
          const mutationError = mutationErrorMessage(result);
          applyOperationHistory(operationHistoryFromMutation(result));
          if (mutationError) throw new Error(mutationError);
          setFormOpen(false);
          setEditingPlan(null);
          refreshAfterMutation(result.duplicate_submission ? "编辑操作已提交" : "工作计划已更新");
          return true;
        }
        const updated = await api<WorkPlan>(`/work-plans/${editingPlan.id}`, token, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        applyLocalMutation([updated]);
        setFormOpen(false);
        setEditingPlan(null);
        refreshAfterMutation("计划已更新");
        return true;
      }
      const result = await api<WorkPlanMutationResult>("/work-plans", token, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const mutationError = mutationErrorMessage(result);
      const savedPlans = result.results.flatMap((item) => item.plan ? [item.plan] : []);
      const savedOperations = operationHistoryFromMutation(result);
      if (savedPlans.length) applyLocalMutation(savedPlans);
      applyOperationHistory(savedOperations);
      if (mutationError) {
        void refreshAll(false).catch(() => undefined);
        throw new Error(mutationError);
      }
      setFormOpen(false);
      refreshAfterMutation(result.duplicate_submission ? "操作已提交，无需重复添加" : `已提交 ${result.total} 天计划`);
      return true;
    } catch (error) {
      showToast(errorMessage(error), true);
      return false;
    } finally {
      setMutationBusy(false);
    }
  };

  const confirmCancel = async () => {
    if (!cancelPlan) return;
    setMutationBusy(true);
    try {
      if (isOperation(cancelPlan)) {
        const idempotencyKey = cancelOperationKey || createIdempotencyKey();
        const payload = buildWorkPlanCancellationPayload(cancelPlan, cancelSegment, idempotencyKey);
        if (cancellationStartsTooSoon(payload, schedule.observed_at)) {
          throw new Error("取消计划的开始时间至少晚于当前时间 1 小时");
        }
        const result = await api<WorkPlanMutationResult>("/work-plans", token, {
          method: "POST",
          body: JSON.stringify(payload),
        });
        const mutationError = mutationErrorMessage(result);
        applyOperationHistory(operationHistoryFromMutation(result));
        if (mutationError) throw new Error(mutationError);
        setCancelPlan(null);
        setCancelSegment(null);
        setCancelOperationKey(null);
        refreshAfterMutation(result.duplicate_submission ? "取消操作已提交" : "计划已取消，历史记录仍会保留");
        return;
      }
      const cancelled = await api<WorkPlan>(`/work-plans/${cancelPlan.id}/cancel`, token, { method: "POST" });
      applyLocalMutation([cancelled]);
      setCancelPlan(null);
      setCancelSegment(null);
      setCancelOperationKey(null);
      refreshAfterMutation("计划已取消，历史记录仍会保留");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setMutationBusy(false);
    }
  };

  const setMemberPriority = useCallback(async (targetMemberId: string, priority: number | null) => {
    if (priorityBusyMemberId) return;
    setPriorityBusyMemberId(targetMemberId);
    try {
      const updated = await api<WorkPlanPriorityResult>(
        `/work-plans/members/${encodeURIComponent(targetMemberId)}/priority`,
        token,
        { method: "PATCH", body: JSON.stringify({ priority }) },
      );
      setSchedule((current) => withUpdatedMemberPriority(
        current,
        updated.member_id,
        updated.work_plan_priority,
      ));
      showToast(priority == null ? "已恢复自动排序" : `已设置优先级 ${priority}`);
      try {
        await loadSchedule(false);
      } catch {
        showToast("优先级已保存，但排班顺序刷新失败，请手动刷新", true);
      }
    } catch (error) {
      const message = errorMessage(error);
      showToast(message, true);
      throw new Error(message);
    } finally {
      setPriorityBusyMemberId("");
    }
  }, [loadSchedule, priorityBusyMemberId, showToast, token]);

  const workCount = schedule.segments
    ? schedule.segments.filter((segment) => segment.state === "active").length
    : schedule.plans.filter((plan) => !plan.is_cancelled && plan.plan_type === "work").length;
  const cancelledCount = schedule.segments
    ? schedule.segments.filter((segment) => segment.state === "cancelled").length
    : schedule.plans.filter((plan) => plan.is_cancelled || plan.plan_type === "temporary_unavailable").length;
  const onlineCount = schedule.members.filter((member) => member.is_online).length;
  const serverToday = shanghaiDateFromTimestamp(schedule.observed_at);
  const editingExpectedSequence = editingPlan && "record_kind" in editingPlan
    ? history.items.reduce(
      (latest, item) => "record_kind" in item && item.member_id === editingPlan.member_id
        ? Math.max(latest, item.member_sequence)
        : latest,
      editingPlan.member_sequence,
    )
    : undefined;

  return (
    <section className="view work-plan-page">
      <header className="work-plan-page-header">
        <div><h2>工作计划</h2><p>{schedule.start_date} 至 {schedule.end_date} · 上海时间</p></div>
        <div className="work-plan-header-actions">
          <button className="ghost" onClick={() => setHistoryOpen(true)} type="button"><History size={17} />我的安排</button>
          <button onClick={openCreate} type="button"><CalendarPlus size={17} />填写我的计划</button>
        </div>
      </header>

      <div className="work-plan-summary-band" aria-label="排班概览">
        <div><span>团队成员</span><strong>{schedule.members.length}</strong></div>
        <div><span>当前在线</span><strong>{onlineCount}</strong></div>
        <div><span>工作计划</span><strong>{workCount}</strong></div>
        <div><span>取消区间</span><strong>{cancelledCount}</strong></div>
      </div>

      <div className="work-plan-toolbar">
        <div className="work-plan-range-control" aria-label="日期范围">
          {(["7d", "30d", "all"] as const).map((value) => <button aria-pressed={range === value} className={range === value ? "active" : ""} key={value} onClick={() => setRange(value)} type="button">{{ "7d": "未来 7 天", "30d": "未来 30 天", all: "全部记录" }[value]}</button>)}
        </div>
        <div className="work-plan-toolbar-filters">
          <label><UsersRound size={15} /><select aria-label="按成员筛选" onChange={(event) => setMemberId(event.target.value)} value={memberId}><option value="">全部成员</option>{schedule.members.map((member) => <option key={member.member_id} value={member.member_id}>{member.member_name}</option>)}</select></label>
          {canManageAll && range === "all" ? <label className="work-plan-cancelled-toggle"><input checked={includeCancelled} onChange={(event) => setIncludeCancelled(event.target.checked)} type="checkbox" />含已取消</label> : null}
          <button aria-label="刷新" className="work-plan-icon-button" disabled={refreshing} onClick={() => refreshAll(true).catch(() => undefined)} title="刷新" type="button"><RefreshCw className={refreshing ? "spinning" : ""} size={17} /></button>
        </div>
      </div>

      {scheduleError ? (
        <ScheduleStaleNotice
          message={scheduleError}
          onRetry={() => loadSchedule(true).catch(() => undefined)}
          refreshing={refreshing}
        />
      ) : null}

      <div className={`work-plan-schedule-frame ${loading ? "loading" : ""}`}>
        {loading && !schedule.plans.length && !schedule.segments?.length ? <div className="work-plan-loading">加载中...</div> : <WorkPlanSchedule currentUser={currentUser} onCancelPlan={openCancel} onEditPlan={openEdit} onSetMemberPriority={setMemberPriority} priorityBusy={Boolean(priorityBusyMemberId)} range={range} response={schedule} />}
      </div>
      {schedule.has_more ? <div className="work-plan-pagination"><span>已显示 {schedule.total_operations ?? schedule.plans.length} / {schedule.total} 条记录</span><button className="ghost" disabled={refreshing} onClick={loadOlderSchedule} type="button">{refreshing ? "加载中..." : "加载更早记录"}</button></div> : null}

      <WorkPlanFormDrawer busy={mutationBusy} expectedMemberSequence={editingExpectedSequence} initialPlan={editingPlan} onClose={() => { if (!mutationBusy) { setFormOpen(false); setEditingPlan(null); } }} onSubmit={submitPlan} open={formOpen} serverToday={serverToday} />
      <MyPlansDrawer blocked={Boolean(cancelPlan)} busy={mutationBusy} hasMore={history.has_more} items={history.items} loadingMore={historyLoadingMore} onCancel={(plan) => openCancel(plan)} onClose={() => setHistoryOpen(false)} onEdit={openEdit} onLoadMore={loadMoreHistory} open={historyOpen} total={history.total} />
      <ConfirmDialog cancelText="返回" confirmText="取消计划" details={cancelPlan ? [["成员", cancelPlan.member_name], ["日期", "anchor_date" in cancelPlan ? cancelPlan.anchor_date : cancelPlan.plan_date]] : []} message="取消后记录仍会保留。" onCancel={() => { setCancelPlan(null); setCancelSegment(null); setCancelOperationKey(null); }} onConfirm={confirmCancel} open={Boolean(cancelPlan)} title="确认取消这条计划？" tone="danger" />
    </section>
  );
}
