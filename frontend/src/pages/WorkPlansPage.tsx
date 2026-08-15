import { CalendarPlus, History, RefreshCw, UsersRound } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

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

export function beginCreate(state: CreateRequestState, idempotencyKey: string): CreateRequestState {
  if (state.busy) return state;
  return { busy: true, idempotencyKey };
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
  };
}

export function WorkPlansPage({ token, currentUser, showToast }: WorkPlansPageProps) {
  const [schedule, setSchedule] = useState<WorkPlanScheduleResponse>(() => emptySchedule());
  const [history, setHistory] = useState<WorkPlan[]>([]);
  const [range, setRange] = useState<WorkPlanRange>("7d");
  const [memberId, setMemberId] = useState("");
  const [includeCancelled, setIncludeCancelled] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [mutationBusy, setMutationBusy] = useState(false);
  const [formOpen, setFormOpen] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [editingPlan, setEditingPlan] = useState<WorkPlan | null>(null);
  const [cancelPlan, setCancelPlan] = useState<WorkPlan | null>(null);
  const canManageAll = currentUser.role === "owner" || currentUser.role === "admin";

  const schedulePath = useMemo(() => {
    const params = new URLSearchParams({ range });
    if (memberId) params.append("member_id", memberId);
    if (includeCancelled && canManageAll && range === "all") params.set("include_cancelled", "true");
    return `/work-plans/schedule?${params.toString()}`;
  }, [canManageAll, includeCancelled, memberId, range]);

  const loadSchedule = useCallback(async (notify = false) => {
    setRefreshing(true);
    try {
      const next = await api<WorkPlanScheduleResponse>(schedulePath, token);
      setSchedule(next);
    } catch (error) {
      if (notify) showToast(errorMessage(error), true);
      throw error;
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }, [schedulePath, showToast, token]);

  const loadHistory = useCallback(async (notify = false) => {
    try {
      const next = await api<WorkPlanHistoryResponse>("/work-plans/mine?limit=4000", token);
      setHistory(next.items);
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

  useEffect(() => {
    loadSchedule(true).catch(() => undefined);
  }, [loadSchedule]);

  useEffect(() => {
    loadHistory(true).catch(() => undefined);
  }, [loadHistory]);

  useEffect(() => {
    if (range !== "all" && includeCancelled) setIncludeCancelled(false);
  }, [includeCancelled, range]);

  usePageAutoRefresh(() => refreshAll(false), {
    paused: formOpen || historyOpen || mutationBusy,
    onError: () => undefined,
  });

  const openCreate = () => {
    setEditingPlan(null);
    setFormOpen(true);
  };

  const openEdit = (plan: WorkPlan) => {
    setHistoryOpen(false);
    setEditingPlan(plan);
    setFormOpen(true);
  };

  const submitPlan = async (payload: WorkPlanCreatePayload | WorkPlanUpdatePayload) => {
    setMutationBusy(true);
    try {
      if (editingPlan) {
        await api<WorkPlan>(`/work-plans/${editingPlan.id}`, token, {
          method: "PATCH",
          body: JSON.stringify(payload),
        });
        await refreshAll(false);
        setFormOpen(false);
        setEditingPlan(null);
        showToast("计划已更新");
        return;
      }
      const result = await api<WorkPlanMutationResult>("/work-plans", token, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await refreshAll(false);
      const failed = result.results.filter((item) => item.outcome === "failed");
      if (failed.length) {
        throw new Error(failed.map((item) => `${item.plan_date}：${item.error || "保存失败"}`).join("；"));
      }
      setFormOpen(false);
      showToast(result.duplicate_submission ? "计划已提交，无需重复添加" : `已添加 ${result.total} 天计划`);
    } catch (error) {
      showToast(errorMessage(error), true);
      throw error;
    } finally {
      setMutationBusy(false);
    }
  };

  const confirmCancel = async () => {
    if (!cancelPlan) return;
    setMutationBusy(true);
    try {
      await api<WorkPlan>(`/work-plans/${cancelPlan.id}/cancel`, token, { method: "POST" });
      await refreshAll(false);
      setCancelPlan(null);
      showToast("计划已取消，历史记录仍会保留");
    } catch (error) {
      showToast(errorMessage(error), true);
      throw error;
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

      <div className={`work-plan-schedule-frame ${loading ? "loading" : ""}`}>
        {loading && !schedule.plans.length ? <div className="work-plan-loading">加载中...</div> : <WorkPlanSchedule currentUser={currentUser} onCancelPlan={setCancelPlan} onEditPlan={openEdit} range={range} response={schedule} />}
      </div>

      <WorkPlanFormDrawer busy={mutationBusy} initialPlan={editingPlan} onClose={() => { if (!mutationBusy) { setFormOpen(false); setEditingPlan(null); } }} onSubmit={submitPlan} open={formOpen} serverToday={serverToday} />
      <MyPlansDrawer busy={mutationBusy} items={history} onCancel={setCancelPlan} onClose={() => setHistoryOpen(false)} onEdit={openEdit} open={historyOpen} />
      <ConfirmDialog cancelText="返回" confirmText="取消计划" details={cancelPlan ? [["成员", cancelPlan.member_name], ["日期", cancelPlan.plan_date]] : []} message="取消后记录仍会保留。" onCancel={() => setCancelPlan(null)} onConfirm={confirmCancel} open={Boolean(cancelPlan)} title="确认取消这条计划？" tone="danger" />
    </section>
  );
}
