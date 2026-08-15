import { CalendarPlus, History, RefreshCw, TriangleAlert, UsersRound } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import type { User } from "../types";
import { errorMessage } from "../utils/format";
import { MyPlansDrawer } from "./workPlans/MyPlansDrawer";
import { WorkPlanFormDrawer } from "./workPlans/WorkPlanFormDrawer";
import { WorkPlanSchedule } from "./workPlans/WorkPlanSchedule";
import type {
  WorkPlan,
  WorkPlanCreatePayload,
  WorkPlanHistoryResponse,
  WorkPlanMutationResult,
  WorkPlanRange,
  WorkPlanScheduleResponse,
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

function compareHistoryPlans(left: WorkPlan, right: WorkPlan): number {
  return right.plan_date.localeCompare(left.plan_date)
    || right.created_at.localeCompare(left.created_at)
    || right.id.localeCompare(left.id);
}

export function mergeWorkPlans(existing: WorkPlan[], changed: WorkPlan[]): WorkPlan[] {
  const byId = new Map(existing.map((plan) => [plan.id, plan]));
  changed.forEach((plan) => byId.set(plan.id, plan));
  return Array.from(byId.values()).sort(compareHistoryPlans);
}

export function mergeHistoryPage(
  current: WorkPlanHistoryResponse,
  next: WorkPlanHistoryResponse,
): WorkPlanHistoryResponse {
  return {
    items: mergeWorkPlans(current.items, next.items),
    total: next.total,
    has_more: next.has_more,
    next_cursor: next.next_cursor,
  };
}

export function mergeSchedulePage(
  current: WorkPlanScheduleResponse,
  next: WorkPlanScheduleResponse,
): WorkPlanScheduleResponse {
  const members = new Map(current.members.map((member) => [member.member_id, member]));
  next.members.forEach((member) => members.set(member.member_id, member));
  const plans = new Map(current.plans.map((plan) => [plan.id, plan]));
  next.plans.forEach((plan) => plans.set(plan.id, plan));
  return {
    ...next,
    members: Array.from(members.values()),
    plans: Array.from(plans.values()).sort((left, right) => left.plan_date.localeCompare(right.plan_date)
      || left.start_minute - right.start_minute
      || left.id.localeCompare(right.id)),
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
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [editingPlan, setEditingPlan] = useState<WorkPlan | null>(null);
  const [cancelPlan, setCancelPlan] = useState<WorkPlan | null>(null);
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
        const items = mergeWorkPlans(current.items, personalChanges);
        return { ...current, items, total: Math.max(current.total, items.length) };
      });
    }
    setSchedule((current) => reconcileSchedulePlans(current, changed, { includeCancelled, memberId }));
  }, [currentUser.email, currentUser.id, includeCancelled, memberId]);

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

  const openEdit = (plan: WorkPlan) => {
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

  const submitPlan = async (payload: WorkPlanCreatePayload | WorkPlanUpdatePayload) => {
    setMutationBusy(true);
    try {
      if (editingPlan) {
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
      if (savedPlans.length) applyLocalMutation(savedPlans);
      if (mutationError) {
        void refreshAll(false).catch(() => undefined);
        throw new Error(mutationError);
      }
      setFormOpen(false);
      refreshAfterMutation(result.duplicate_submission ? "计划已提交，无需重复添加" : `已添加 ${result.total} 天计划`);
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
      const cancelled = await api<WorkPlan>(`/work-plans/${cancelPlan.id}/cancel`, token, { method: "POST" });
      applyLocalMutation([cancelled]);
      setCancelPlan(null);
      refreshAfterMutation("计划已取消，历史记录仍会保留");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setMutationBusy(false);
    }
  };

  const workCount = schedule.plans.filter((plan) => !plan.is_cancelled && plan.plan_type === "work").length;
  const unavailableCount = schedule.plans.filter((plan) => !plan.is_cancelled && plan.plan_type === "temporary_unavailable").length;
  const onlineCount = schedule.members.filter((member) => member.is_online).length;
  const serverToday = shanghaiDateFromTimestamp(schedule.observed_at);

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
        <div><span>临时有事</span><strong>{unavailableCount}</strong></div>
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
        {loading && !schedule.plans.length ? <div className="work-plan-loading">加载中...</div> : <WorkPlanSchedule currentUser={currentUser} onCancelPlan={setCancelPlan} onEditPlan={openEdit} range={range} response={schedule} />}
      </div>
      {schedule.has_more ? <div className="work-plan-pagination"><span>已显示 {schedule.plans.length} / {schedule.total} 条计划</span><button className="ghost" disabled={refreshing} onClick={loadOlderSchedule} type="button">{refreshing ? "加载中..." : "加载更早记录"}</button></div> : null}

      <WorkPlanFormDrawer busy={mutationBusy} initialPlan={editingPlan} onClose={() => { if (!mutationBusy) { setFormOpen(false); setEditingPlan(null); } }} onSubmit={submitPlan} open={formOpen} serverToday={serverToday} />
      <MyPlansDrawer blocked={Boolean(cancelPlan)} busy={mutationBusy} hasMore={history.has_more} items={history.items} loadingMore={historyLoadingMore} onCancel={setCancelPlan} onClose={() => setHistoryOpen(false)} onEdit={openEdit} onLoadMore={loadMoreHistory} open={historyOpen} total={history.total} />
      <ConfirmDialog cancelText="返回" confirmText="取消计划" details={cancelPlan ? [["成员", cancelPlan.member_name], ["日期", cancelPlan.plan_date]] : []} message="取消后记录仍会保留。" onCancel={() => setCancelPlan(null)} onConfirm={confirmCancel} open={Boolean(cancelPlan)} title="确认取消这条计划？" tone="danger" />
    </section>
  );
}
