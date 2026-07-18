import { useEffect, useMemo, useState } from "react";
import { api } from "../api/client";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import { errorMessage, formatDateTime } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type WorkbenchTab =
  | "tasks"
  | "runs"
  | "trace"
  | "triggers"
  | "evals"
  | "memory"
  | "notifications"
  | "pricing"
  | "usage_attribution";
type AgentTaskStatus = "open" | "observing" | "waiting_human" | "alert_drafted" | "review_due" | "closed" | "failed";
type AgentRunTrigger =
  | "all"
  | "manual_analyze"
  | "manual_chat"
  | "scheduler_patrol"
  | "scheduler_task_due"
  | "scheduler_review_due"
  | "event_spike"
  | "memory_daily_summary"
  | "memory_weekly_summary"
  | "notification_dispatch";

type AgentTask = {
  _id?: string;
  task_id?: string;
  pool_id?: string | null;
  site_id?: string | null;
  task_type?: string | null;
  status?: AgentTaskStatus | string;
  severity?: string | null;
  title?: string | null;
  summary?: string | null;
  suggested_account_type?: string | null;
  suggested_add_count?: number | null;
  refill_plan_summary?: string | null;
  requires_human_confirm?: boolean;
  alert_status?: string | null;
  next_check_at?: string | null;
  review_after?: string | null;
  current_decision_id?: string | null;
  current_run_id?: string | null;
  latest_state_reason?: string | null;
  updated_at?: string | null;
  state_history?: AgentTaskStateHistory[];
  last_review?: Record<string, unknown> | null;
  feedback_result?: Record<string, unknown> | null;
};

type AgentTaskStateHistory = {
  from_status?: string | null;
  to_status?: string | null;
  reason?: string | null;
  run_id?: string | null;
  decision_id?: string | null;
  changed_at?: string | null;
};

type AgentTasksResponse = {
  items: AgentTask[];
  total: number;
};

type AgentRun = {
  _id?: string;
  run_id?: string;
  trigger?: string | null;
  pool_id?: string | null;
  site_id?: string | null;
  conversation_id?: string | null;
  status?: string | null;
  severity?: string | null;
  decision_id?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  error?: string | null;
  agent?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
  trigger_metadata?: Record<string, unknown> | null;
};

type AgentRunsResponse = {
  items: AgentRun[];
  total: number;
};

type AgentRunStep = {
  _id?: string;
  step_id?: string;
  run_id?: string | null;
  conversation_id?: string | null;
  task_id?: string | null;
  step_index?: number | null;
  step_type?: string | null;
  status?: string | null;
  intent?: string | null;
  input_summary?: Record<string, unknown> | null;
  output_summary?: Record<string, unknown> | null;
  llm?: Record<string, unknown> | null;
  capability_calls?: Array<Record<string, unknown>>;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  error?: string | null;
};

type AgentRunStepsResponse = {
  items: AgentRunStep[];
  total: number;
};

type AgentSchedulerTick = {
  _id?: string;
  tick_id?: string;
  status?: string | null;
  reason?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
  processed?: Record<string, unknown> | null;
  errors?: unknown[];
};

type AgentEventTrigger = {
  _id?: string;
  trigger_id?: string;
  signal?: string | null;
  site_id?: string | null;
  pool_id?: string | null;
  dedupe_key?: string | null;
  evidence?: Record<string, unknown> | null;
  status?: string | null;
  run_id?: string | null;
  created_at?: string | null;
  error?: string | null;
};

type AgentPatrolRun = {
  _id?: string;
  patrol_id?: string;
  scheduler_tick_id?: string | null;
  site_id?: string | null;
  pool_id?: string | null;
  status?: string | null;
  reason?: string | null;
  skip_reason?: string | null;
  required_patrol?: boolean;
  run_id?: string | null;
  decision_id?: string | null;
  task_id?: string | null;
  severity?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};

type AgentListResponse<T> = {
  items: T[];
  total: number;
};

type AgentEvalCase = {
  case_id?: string;
  category?: string | null;
  description?: string | null;
  input_mode?: string | null;
  min_score?: number | null;
  critical_assertions?: string[];
};

type AgentEvalRun = {
  _id?: string;
  eval_run_id?: string;
  suite?: string | null;
  category?: string | null;
  mode?: string | null;
  status?: string | null;
  summary?: Record<string, unknown> | null;
  started_at?: string | null;
  finished_at?: string | null;
  duration_ms?: number | null;
};

type AgentEvalResult = {
  _id?: string;
  eval_run_id?: string | null;
  case_id?: string | null;
  category?: string | null;
  description?: string | null;
  status?: string | null;
  score?: number | null;
  assertions?: Array<Record<string, unknown>>;
  failure_reasons?: string[];
  output_summary?: Record<string, unknown> | null;
  duration_ms?: number | null;
  created_at?: string | null;
};

type AgentEvalCasesResponse = AgentListResponse<AgentEvalCase> & {
  categories?: string[];
};

type AgentMemorySummary = {
  memory_id?: string;
  site_id?: string | null;
  pool_id?: string | null;
  memory_type?: string | null;
  period_start?: string | null;
  period_end?: string | null;
  summary?: string | null;
  facts?: unknown[];
  patterns?: unknown[];
  lessons?: unknown[];
  risk_baselines?: Record<string, unknown> | null;
  source_run_ids?: string[];
  source_decision_ids?: string[];
  created_at?: string | null;
};

type AgentMemoryResponse = AgentListResponse<AgentMemorySummary> & {
  memory_types?: string[];
};

type AgentNotificationItem = {
  task_id?: string;
  pool_id?: string | null;
  site_id?: string | null;
  task_status?: string | null;
  severity?: string | null;
  alert_status?: string | null;
  alert_title?: string | null;
  alert_content?: string | null;
  alert_draft?: Record<string, unknown> | null;
  source_decision_id?: string | null;
  notification_event_id?: string | null;
  notification_event?: Record<string, unknown> | null;
  deliveries?: Array<Record<string, unknown>>;
  delivery_status?: string | null;
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  alert_sent_at?: string | null;
};

const taskStatuses: AgentTaskStatus[] = ["open", "observing", "waiting_human", "alert_drafted", "review_due", "closed", "failed"];
const runTriggers: AgentRunTrigger[] = [
  "all",
  "manual_analyze",
  "manual_chat",
  "scheduler_patrol",
  "scheduler_task_due",
  "scheduler_review_due",
  "event_spike",
  "memory_daily_summary",
  "memory_weekly_summary",
  "notification_dispatch",
];

const tabs: Array<{ key: WorkbenchTab; label: string; description: string }> = [
  { key: "tasks", label: "Tasks", description: "持续任务、人工等待、告警草稿和复盘状态。" },
  { key: "runs", label: "Runs", description: "手动、调度、事件触发和巡检生成的 Agent runs。" },
  { key: "trace", label: "Trace", description: "按 run 展开 step loop、能力调用和任务更新结果。" },
  { key: "triggers", label: "Triggers", description: "event_spike、scheduler_patrol、review_due 等自动触发来源。" },
  { key: "evals", label: "Evals", description: "自动评测集、回归样例、通过率和失败原因。" },
  { key: "memory", label: "Memory", description: "长期记忆、每日/每周总结、复盘经验和后续 playbook 来源。" },
  { key: "notifications", label: "Notifications", description: "告警草稿、钉钉发送审计、失败重试和通知策略结果。" },
  { key: "pricing", label: "Pricing", description: "价格策略建议的预留入口，只建议、不自动改价。" },
  { key: "usage_attribution", label: "Usage Attribution", description: "用户用量归因的预留入口，追溯上涨来自单用户还是多用户。" },
];

export function AgentWorkbenchPage({ token, showToast }: Props) {
  const [activeTab, setActiveTab] = useState<WorkbenchTab>("tasks");
  const [tasks, setTasks] = useState<AgentTask[]>([]);
  const [taskTotal, setTaskTotal] = useState(0);
  const [tasksLoading, setTasksLoading] = useState(false);
  const [taskBusyKey, setTaskBusyKey] = useState("");
  const [runs, setRuns] = useState<AgentRun[]>([]);
  const [runTotal, setRunTotal] = useState(0);
  const [runsLoading, setRunsLoading] = useState(false);
  const [runTriggerFilter, setRunTriggerFilter] = useState<AgentRunTrigger>("all");
  const [traceRunId, setTraceRunId] = useState("");
  const [traceSteps, setTraceSteps] = useState<AgentRunStep[]>([]);
  const [traceTotal, setTraceTotal] = useState(0);
  const [traceLoading, setTraceLoading] = useState(false);
  const [schedulerTicks, setSchedulerTicks] = useState<AgentSchedulerTick[]>([]);
  const [eventTriggers, setEventTriggers] = useState<AgentEventTrigger[]>([]);
  const [patrolRuns, setPatrolRuns] = useState<AgentPatrolRun[]>([]);
  const [triggersLoading, setTriggersLoading] = useState(false);
  const [evalCases, setEvalCases] = useState<AgentEvalCase[]>([]);
  const [evalCategories, setEvalCategories] = useState<string[]>([]);
  const [evalRuns, setEvalRuns] = useState<AgentEvalRun[]>([]);
  const [evalResults, setEvalResults] = useState<AgentEvalResult[]>([]);
  const [selectedEvalRunId, setSelectedEvalRunId] = useState("");
  const [evalCategoryFilter, setEvalCategoryFilter] = useState("all");
  const [evalMode, setEvalMode] = useState<"llm_live" | "llm_mock">("llm_live");
  const [evalsLoading, setEvalsLoading] = useState(false);
  const [evalRunning, setEvalRunning] = useState(false);
  const [memories, setMemories] = useState<AgentMemorySummary[]>([]);
  const [memoryTypes, setMemoryTypes] = useState<string[]>([]);
  const [memoryTypeFilter, setMemoryTypeFilter] = useState("all");
  const [memoryPoolFilter, setMemoryPoolFilter] = useState("");
  const [memoryLoading, setMemoryLoading] = useState(false);
  const [notifications, setNotifications] = useState<AgentNotificationItem[]>([]);
  const [notificationStatusFilter, setNotificationStatusFilter] = useState("all");
  const [notificationsLoading, setNotificationsLoading] = useState(false);
  const [notificationBusyTaskId, setNotificationBusyTaskId] = useState("");

  const groupedTasks = useMemo(() => groupTasksByStatus(tasks), [tasks]);
  const taskCounts = useMemo(() => countTasks(tasks), [tasks]);

  const loadTasks = async (silent = false) => {
    setTasksLoading(true);
    try {
      const data = await api<AgentTasksResponse>("/agent/tasks?limit=200", token);
      setTasks(data.items || []);
      setTaskTotal(data.total || 0);
    } catch (error) {
      if (!silent) showToast(errorMessage(error), true);
    } finally {
      setTasksLoading(false);
    }
  };

  const loadRuns = async (triggerFilter = runTriggerFilter, silent = false) => {
    setRunsLoading(true);
    try {
      const query = new URLSearchParams({ limit: "100" });
      if (triggerFilter !== "all") {
        query.set("trigger", triggerFilter);
      }
      const data = await api<AgentRunsResponse>(`/agent/runs?${query.toString()}`, token);
      setRuns(data.items || []);
      setRunTotal(data.total || 0);
    } catch (error) {
      if (!silent) showToast(errorMessage(error), true);
    } finally {
      setRunsLoading(false);
    }
  };

  const loadTraceSteps = async (runId = traceRunId, silent = false) => {
    const normalizedRunId = runId.trim();
    if (!normalizedRunId) {
      showToast("请先选择或输入 run_id", true);
      return;
    }
    setTraceLoading(true);
    try {
      const data = await api<AgentRunStepsResponse>(`/agent/runs/${encodeURIComponent(normalizedRunId)}/steps?limit=200`, token);
      setTraceSteps(data.items || []);
      setTraceTotal(data.total || 0);
      setTraceRunId(normalizedRunId);
    } catch (error) {
      if (!silent) showToast(errorMessage(error), true);
    } finally {
      setTraceLoading(false);
    }
  };

  const loadTriggerData = async (silent = false) => {
    setTriggersLoading(true);
    try {
      const [ticksData, eventData, patrolData] = await Promise.all([
        api<AgentListResponse<AgentSchedulerTick>>("/agent/scheduler/ticks?limit=30", token),
        api<AgentListResponse<AgentEventTrigger>>("/agent/event-triggers?limit=30", token),
        api<AgentListResponse<AgentPatrolRun>>("/agent/patrol/runs?limit=30", token),
      ]);
      setSchedulerTicks(ticksData.items || []);
      setEventTriggers(eventData.items || []);
      setPatrolRuns(patrolData.items || []);
    } catch (error) {
      if (!silent) showToast(errorMessage(error), true);
    } finally {
      setTriggersLoading(false);
    }
  };

  const loadEvalData = async (preferredEvalRunId = selectedEvalRunId, silent = false) => {
    setEvalsLoading(true);
    try {
      const [casesData, runsData] = await Promise.all([
        api<AgentEvalCasesResponse>("/agent/evals/cases", token),
        api<AgentListResponse<AgentEvalRun>>("/agent/evals/runs?limit=30", token),
      ]);
      const nextCases = casesData.items || [];
      const nextRuns = runsData.items || [];
      setEvalCases(nextCases);
      setEvalCategories(casesData.categories || Array.from(new Set(nextCases.map((item) => String(item.category || "unknown")))));
      setEvalRuns(nextRuns);

      const nextEvalRunId = preferredEvalRunId || evalRunIdentifier(nextRuns[0] || {});
      setSelectedEvalRunId(nextEvalRunId);
      if (nextEvalRunId) {
        const resultsData = await api<AgentListResponse<AgentEvalResult>>(
          `/agent/evals/results?eval_run_id=${encodeURIComponent(nextEvalRunId)}&limit=200`,
          token
        );
        setEvalResults(resultsData.items || []);
      } else {
        const resultsData = await api<AgentListResponse<AgentEvalResult>>("/agent/evals/results?limit=100", token);
        setEvalResults(resultsData.items || []);
      }
    } catch (error) {
      if (!silent) showToast(errorMessage(error), true);
    } finally {
      setEvalsLoading(false);
    }
  };

  const loadEvalResults = async (evalRunId: string) => {
    const normalizedEvalRunId = evalRunId.trim();
    setSelectedEvalRunId(normalizedEvalRunId);
    if (!normalizedEvalRunId) {
      setEvalResults([]);
      return;
    }
    setEvalsLoading(true);
    try {
      const data = await api<AgentListResponse<AgentEvalResult>>(
        `/agent/evals/results?eval_run_id=${encodeURIComponent(normalizedEvalRunId)}&limit=200`,
        token
      );
      setEvalResults(data.items || []);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setEvalsLoading(false);
    }
  };

  const runEvalSuite = async () => {
    setEvalRunning(true);
    try {
      const payload = {
        suite: "default",
        mode: evalMode,
        category: evalCategoryFilter === "all" ? null : evalCategoryFilter,
        persist: true,
      };
      const result = await api<{ eval_run_id?: string; results?: AgentEvalResult[] }>("/agent/evals/run", token, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const nextEvalRunId = String(result.eval_run_id || "");
      if (Array.isArray(result.results)) {
        setEvalResults(result.results);
      }
      await loadEvalData(nextEvalRunId);
      showToast("评测运行完成");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setEvalRunning(false);
    }
  };

  const loadMemoryData = async (typeFilter = memoryTypeFilter, poolFilter = memoryPoolFilter, silent = false) => {
    setMemoryLoading(true);
    try {
      const query = new URLSearchParams({ limit: "100" });
      if (typeFilter !== "all") {
        query.set("memory_type", typeFilter);
      }
      if (poolFilter.trim()) {
        query.set("pool_id", poolFilter.trim());
      }
      const data = await api<AgentMemoryResponse>(`/agent/memory?${query.toString()}`, token);
      setMemories(data.items || []);
      setMemoryTypes(data.memory_types || []);
    } catch (error) {
      if (!silent) showToast(errorMessage(error), true);
    } finally {
      setMemoryLoading(false);
    }
  };

  const loadNotifications = async (statusFilter = notificationStatusFilter, silent = false) => {
    setNotificationsLoading(true);
    try {
      const query = new URLSearchParams({ limit: "100" });
      if (statusFilter !== "all") {
        query.set("status", statusFilter);
      }
      const data = await api<AgentListResponse<AgentNotificationItem>>(`/agent/notifications?${query.toString()}`, token);
      setNotifications(data.items || []);
    } catch (error) {
      if (!silent) showToast(errorMessage(error), true);
    } finally {
      setNotificationsLoading(false);
    }
  };

  const refreshActiveTab = async () => {
    if (activeTab === "tasks") await loadTasks(true);
    if (activeTab === "runs") await loadRuns(runTriggerFilter, true);
    if (activeTab === "trace") {
      if (traceRunId) await loadTraceSteps(traceRunId, true);
      else await loadRuns("all", true);
    }
    if (activeTab === "triggers") await loadTriggerData(true);
    if (activeTab === "evals") await loadEvalData(selectedEvalRunId, true);
    if (activeTab === "memory") await loadMemoryData(memoryTypeFilter, memoryPoolFilter, true);
    if (activeTab === "notifications") await loadNotifications(notificationStatusFilter, true);
  };

  usePageAutoRefresh(refreshActiveTab, {
    paused: Boolean(taskBusyKey || evalRunning || notificationBusyTaskId),
  });

  const dispatchNotification = async (taskId: string) => {
    if (!taskId) {
      showToast("task_id 缺失，无法发送告警草稿", true);
      return;
    }
    setNotificationBusyTaskId(taskId);
    try {
      await api(`/agent/tasks/${encodeURIComponent(taskId)}/dispatch-alert`, token, {
        method: "POST",
        body: JSON.stringify({ force: true }),
      });
      await loadNotifications();
      showToast("告警草稿已发送");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setNotificationBusyTaskId("");
    }
  };

  useEffect(() => {
    if (activeTab === "tasks") {
      void loadTasks();
    }
    if (activeTab === "runs") {
      void loadRuns();
    }
    if (activeTab === "trace" && !runs.length) {
      void loadRuns("all");
    }
    if (activeTab === "triggers") {
      void loadTriggerData();
    }
    if (activeTab === "evals") {
      void loadEvalData();
    }
    if (activeTab === "memory") {
      void loadMemoryData();
    }
    if (activeTab === "notifications") {
      void loadNotifications();
    }
  }, [activeTab]);

  useEffect(() => {
    if (activeTab === "runs") {
      void loadRuns(runTriggerFilter);
    }
  }, [runTriggerFilter]);

  const runTaskAction = async (task: AgentTask, action: string, callback: () => Promise<unknown>, successMessage: string) => {
    const taskId = taskIdentifier(task);
    if (!taskId) {
      showToast("task_id 缺失，无法操作", true);
      return;
    }
    const busyKey = `${taskId}:${action}`;
    setTaskBusyKey(busyKey);
    try {
      await callback();
      await loadTasks();
      showToast(successMessage);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setTaskBusyKey("");
    }
  };

  const feedbackTask = (task: AgentTask, kind: "accounts_added" | "observe") => {
    const taskId = taskIdentifier(task);
    if (!taskId) return;
    const payload =
      kind === "accounts_added"
        ? {
            message: "人工确认已补号或已处理，进入复盘验证。",
            feedback_type: "accounts_added",
            next_status: "review_due",
            reason: "Operator confirmed accounts were added or the issue was handled.",
          }
        : {
            message: "人工确认先观察。",
            feedback_type: "observe_confirmed",
            next_status: "observing",
            reason: "Operator confirmed to continue observing.",
          };
    return runTaskAction(
      task,
      kind,
      () => api(`/agent/tasks/${encodeURIComponent(taskId)}/feedback`, token, { method: "POST", body: JSON.stringify(payload) }),
      kind === "accounts_added" ? "已记录补号反馈，任务已推进" : "已转入观察"
    );
  };

  const transitionTask = (task: AgentTask, nextStatus: AgentTaskStatus, reason: string, action: string, message: string) => {
    const taskId = taskIdentifier(task);
    if (!taskId) return;
    return runTaskAction(
      task,
      action,
      () =>
        api(`/agent/tasks/${encodeURIComponent(taskId)}/transition`, token, {
          method: "POST",
          body: JSON.stringify({ next_status: nextStatus, reason }),
        }),
      message
    );
  };

  const dispatchAlert = (task: AgentTask) => {
    const taskId = taskIdentifier(task);
    if (!taskId) return;
    return runTaskAction(
      task,
      "dispatch-alert",
      () => api(`/agent/tasks/${encodeURIComponent(taskId)}/dispatch-alert`, token, { method: "POST", body: JSON.stringify({ force: true }) }),
      "告警草稿已发送"
    );
  };

  const copyTaskId = async (task: AgentTask) => {
    const taskId = taskIdentifier(task);
    if (!taskId) return;
    try {
      await navigator.clipboard.writeText(taskId);
      showToast("task_id 已复制");
    } catch {
      showToast(taskId);
    }
  };

  return (
    <section className="view agent-workbench-page">
      <div className="topbar">
        <div>
          <h2>Agent工作台</h2>
          <p>面向运维和调试的完整工作台，用来追踪 task、run、trace、trigger、eval、memory 和 notification。</p>
        </div>
        {activeTab === "tasks" ? (
          <button className="ghost" type="button" onClick={() => void loadTasks()} disabled={tasksLoading}>
            {tasksLoading ? "刷新中..." : "刷新 Tasks"}
          </button>
        ) : activeTab === "runs" ? (
          <button className="ghost" type="button" onClick={() => loadRuns()} disabled={runsLoading}>
            {runsLoading ? "刷新中..." : "刷新 Runs"}
          </button>
        ) : activeTab === "trace" ? (
          <button className="ghost" type="button" onClick={() => loadTraceSteps()} disabled={traceLoading || !traceRunId.trim()}>
            {traceLoading ? "刷新中..." : "刷新 Trace"}
          </button>
        ) : activeTab === "triggers" ? (
          <button className="ghost" type="button" onClick={() => void loadTriggerData()} disabled={triggersLoading}>
            {triggersLoading ? "刷新中..." : "刷新 Triggers"}
          </button>
        ) : activeTab === "evals" ? (
          <button className="ghost" type="button" onClick={() => loadEvalData()} disabled={evalsLoading || evalRunning}>
            {evalsLoading ? "刷新中..." : "刷新 Evals"}
          </button>
        ) : activeTab === "memory" ? (
          <button className="ghost" type="button" onClick={() => loadMemoryData()} disabled={memoryLoading}>
            {memoryLoading ? "刷新中..." : "刷新 Memory"}
          </button>
        ) : activeTab === "notifications" ? (
          <button className="ghost" type="button" onClick={() => loadNotifications()} disabled={notificationsLoading}>
            {notificationsLoading ? "刷新中..." : "刷新 Notifications"}
          </button>
        ) : activeTab === "pricing" || activeTab === "usage_attribution" ? (
          <span className="status-pill muted">reserved</span>
        ) : (
          null
        )}
      </div>

      <section className="panel agent-workbench-overview">
        <div className="compact-stats agent-workbench-stats">
          <Metric label="任务总数" value={taskTotal || tasks.length} />
          <Metric label="等待人工" value={taskCounts.waiting_human || "0"} />
          <Metric label="告警草稿" value={taskCounts.alert_drafted || "0"} />
          <Metric label="待复盘" value={taskCounts.review_due || "0"} />
        </div>
      </section>

      <section className="panel agent-workbench-shell">
        <div className="agent-workbench-tabs" role="tablist" aria-label="Agent workbench tabs">
          {tabs.map((tab) => (
            <button
              className={activeTab === tab.key ? "active" : ""}
              key={tab.key}
              onClick={() => setActiveTab(tab.key)}
              type="button"
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === "tasks" ? (
          <TasksTab
            groupedTasks={groupedTasks}
            loading={tasksLoading}
            busyKey={taskBusyKey}
            onAccountsAdded={(task) => feedbackTask(task, "accounts_added")}
            onObserve={(task) => feedbackTask(task, "observe")}
            onClose={(task) => transitionTask(task, "closed", "Operator confirmed no further handling is needed.", "close", "任务已关闭")}
            onReviewDue={(task) => transitionTask(task, "review_due", "Operator requested decision review.", "review-due", "任务已转入复盘")}
            onDispatchAlert={dispatchAlert}
            onCopyTaskId={copyTaskId}
          />
        ) : activeTab === "runs" ? (
          <RunsTab
            runs={runs}
            total={runTotal}
            loading={runsLoading}
            triggerFilter={runTriggerFilter}
            onTriggerFilterChange={setRunTriggerFilter}
          />
        ) : activeTab === "trace" ? (
          <TraceTab
            runs={runs}
            runId={traceRunId}
            steps={traceSteps}
            total={traceTotal}
            loading={traceLoading}
            onRunIdChange={setTraceRunId}
            onLoadTrace={loadTraceSteps}
          />
        ) : activeTab === "triggers" ? (
          <TriggersTab
            loading={triggersLoading}
            schedulerTicks={schedulerTicks}
            eventTriggers={eventTriggers}
            patrolRuns={patrolRuns}
          />
        ) : activeTab === "evals" ? (
          <EvalsTab
            cases={evalCases}
            categories={evalCategories}
            runs={evalRuns}
            results={evalResults}
            selectedEvalRunId={selectedEvalRunId}
            categoryFilter={evalCategoryFilter}
            mode={evalMode}
            loading={evalsLoading}
            running={evalRunning}
            onCategoryFilterChange={setEvalCategoryFilter}
            onModeChange={setEvalMode}
            onRunEvalSuite={runEvalSuite}
            onSelectEvalRun={loadEvalResults}
          />
        ) : activeTab === "memory" ? (
          <MemoryTab
            memories={memories}
            memoryTypes={memoryTypes}
            memoryTypeFilter={memoryTypeFilter}
            poolFilter={memoryPoolFilter}
            loading={memoryLoading}
            onMemoryTypeFilterChange={setMemoryTypeFilter}
            onPoolFilterChange={setMemoryPoolFilter}
            onLoadMemory={loadMemoryData}
          />
        ) : activeTab === "notifications" ? (
          <NotificationsTab
            notifications={notifications}
            statusFilter={notificationStatusFilter}
            loading={notificationsLoading}
            busyTaskId={notificationBusyTaskId}
            onStatusFilterChange={setNotificationStatusFilter}
            onLoadNotifications={loadNotifications}
            onDispatchNotification={dispatchNotification}
          />
        ) : activeTab === "pricing" ? (
          <PricingReservedTab />
        ) : activeTab === "usage_attribution" ? (
          <UsageAttributionReservedTab />
        ) : (
          null
        )}
      </section>
    </section>
  );
}

function TasksTab({
  groupedTasks,
  loading,
  busyKey,
  onAccountsAdded,
  onObserve,
  onClose,
  onReviewDue,
  onDispatchAlert,
  onCopyTaskId,
}: {
  groupedTasks: Record<AgentTaskStatus, AgentTask[]>;
  loading: boolean;
  busyKey: string;
  onAccountsAdded: (task: AgentTask) => void;
  onObserve: (task: AgentTask) => void;
  onClose: (task: AgentTask) => void;
  onReviewDue: (task: AgentTask) => void;
  onDispatchAlert: (task: AgentTask) => void;
  onCopyTaskId: (task: AgentTask) => void;
}) {
  return (
    <div className="agent-workbench-tab-body">
      <div className="panel-header">
        <div>
          <h3>Tasks</h3>
          <p>持续运营问题看板，按状态展示并支持人工推进。</p>
        </div>
        <span className="status-pill accent">{loading ? "loading" : "live"}</span>
      </div>
      <div className="agent-task-board">
        {taskStatuses.map((status) => (
          <section className="agent-task-column" key={status}>
            <div className="agent-task-column-head">
              <strong>{taskStatusLabel(status)}</strong>
              <span>{groupedTasks[status]?.length || 0}</span>
            </div>
            <div className="agent-task-card-list">
              {(groupedTasks[status] || []).map((task) => (
                <TaskCard
                  busyKey={busyKey}
                  key={taskIdentifier(task)}
                  task={task}
                  onAccountsAdded={onAccountsAdded}
                  onObserve={onObserve}
                  onClose={onClose}
                  onReviewDue={onReviewDue}
                  onDispatchAlert={onDispatchAlert}
                  onCopyTaskId={onCopyTaskId}
                />
              ))}
              {!groupedTasks[status]?.length && <div className="agent-task-empty">暂无 {taskStatusLabel(status)} task</div>}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

function RunsTab({
  runs,
  total,
  loading,
  triggerFilter,
  onTriggerFilterChange,
}: {
  runs: AgentRun[];
  total: number;
  loading: boolean;
  triggerFilter: AgentRunTrigger;
  onTriggerFilterChange: (value: AgentRunTrigger) => void;
}) {
  return (
    <div className="agent-workbench-tab-body">
      <div className="panel-header">
        <div>
          <h3>Runs</h3>
          <p>每一次 Agent 执行记录，覆盖手动分析、聊天、scheduler、事件突增、巡检、记忆和通知调度。</p>
        </div>
        <span className="status-pill accent">{loading ? "loading" : `${runs.length}/${total}`}</span>
      </div>
      <div className="agent-runs-toolbar">
        <label className="inline-select">
          <span>trigger</span>
          <select value={triggerFilter} onChange={(event) => onTriggerFilterChange(event.target.value as AgentRunTrigger)}>
            {runTriggers.map((trigger) => (
              <option key={trigger} value={trigger}>
                {trigger}
              </option>
            ))}
          </select>
        </label>
        <span>当前显示最近 {runs.length} 条 run；详情 trace 会在后续 Trace tab 展开。</span>
      </div>
      <div className="table-wrap agent-runs-table-wrap">
        <table className="agent-runs-table">
          <thead>
            <tr>
              <th>run_id</th>
              <th>trigger</th>
              <th>pool / task</th>
              <th>conversation</th>
              <th>status</th>
              <th>severity</th>
              <th>decision</th>
              <th>started</th>
              <th>finished</th>
              <th>duration</th>
              <th>error</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((run) => (
              <tr key={runIdentifier(run)}>
                <td>
                  <code className="agent-run-id">{shortId(runIdentifier(run))}</code>
                </td>
                <td>
                  <span className="agent-run-trigger">{run.trigger || "-"}</span>
                </td>
                <td>
                  <div className="agent-run-stack">
                    <strong>{run.pool_id || "-"}</strong>
                    <span>task {shortId(runTaskId(run))}</span>
                  </div>
                </td>
                <td>{shortId(run.conversation_id)}</td>
                <td>
                  <span className={`status-pill ${runStatusTone(run.status)}`}>{run.status || "-"}</span>
                </td>
                <td>{run.severity || "-"}</td>
                <td>{shortId(run.decision_id)}</td>
                <td>{formatOptionalDate(run.started_at)}</td>
                <td>{formatOptionalDate(run.finished_at)}</td>
                <td>{formatDuration(run.duration_ms)}</td>
                <td>
                  <span className={run.error ? "agent-run-error" : ""}>{run.error || "-"}</span>
                </td>
              </tr>
            ))}
            {!runs.length && (
              <tr>
                <td colSpan={11}>
                  <div className="agent-table-empty">{loading ? "Runs 加载中..." : "暂无 run 记录"}</div>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function TraceTab({
  runs,
  runId,
  steps,
  total,
  loading,
  onRunIdChange,
  onLoadTrace,
}: {
  runs: AgentRun[];
  runId: string;
  steps: AgentRunStep[];
  total: number;
  loading: boolean;
  onRunIdChange: (value: string) => void;
  onLoadTrace: (runId?: string) => void;
}) {
  return (
    <div className="agent-workbench-tab-body">
      <div className="panel-header">
        <div>
          <h3>Trace</h3>
          <p>按 run 展开 step loop，只展示安全调试摘要、能力调用和任务更新结果。</p>
        </div>
        <span className="status-pill accent">{loading ? "loading" : `${steps.length}/${total}`}</span>
      </div>
      <div className="agent-trace-toolbar">
        <label>
          <span>最近 run</span>
          <select
            value={runs.some((run) => runIdentifier(run) === runId) ? runId : ""}
            onChange={(event) => {
              onRunIdChange(event.target.value);
              if (event.target.value) onLoadTrace(event.target.value);
            }}
          >
            <option value="">选择最近 run</option>
            {runs.map((run) => (
              <option key={runIdentifier(run)} value={runIdentifier(run)}>
                {shortId(runIdentifier(run))} · {run.trigger || "-"} · {run.pool_id || "-"}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>run_id</span>
          <input value={runId} onChange={(event) => onRunIdChange(event.target.value)} placeholder="粘贴 run_id 后查询 trace" />
        </label>
        <button type="button" onClick={() => onLoadTrace()} disabled={loading || !runId.trim()}>
          查询 Trace
        </button>
      </div>
      <div className="agent-trace-safety-note">
        <strong>安全展示</strong>
        <span>这里只显示 thought_summary、input/output summary、capability_calls 和 task_update_result，不展示隐藏推理链。</span>
      </div>
      <div className="agent-trace-list">
        {steps.map((step) => (
          <TraceStepCard key={stepIdentifier(step)} step={step} />
        ))}
        {!steps.length && <div className="agent-task-empty">{loading ? "Trace 加载中..." : "请选择一个 run 查看 step trace"}</div>}
      </div>
    </div>
  );
}

function TriggersTab({
  loading,
  schedulerTicks,
  eventTriggers,
  patrolRuns,
}: {
  loading: boolean;
  schedulerTicks: AgentSchedulerTick[];
  eventTriggers: AgentEventTrigger[];
  patrolRuns: AgentPatrolRun[];
}) {
  return (
    <div className="agent-workbench-tab-body">
      <div className="panel-header">
        <div>
          <h3>Triggers</h3>
          <p>查看 scheduler tick、事件突增唤醒和 patrol 巡检历史，解释 Agent 为什么自动运行。</p>
        </div>
        <span className="status-pill accent">{loading ? "loading" : "live"}</span>
      </div>

      <section className="agent-trigger-section">
        <div className="agent-trigger-section-head">
          <div>
            <strong>Scheduler ticks</strong>
            <span>每一轮 loop 处理了哪些调度器。</span>
          </div>
          <span>{schedulerTicks.length}</span>
        </div>
        <div className="table-wrap agent-trigger-table-wrap">
          <table className="agent-trigger-table">
            <thead>
              <tr>
                <th>tick_id</th>
                <th>status</th>
                <th>reason</th>
                <th>started</th>
                <th>duration</th>
                <th>due</th>
                <th>review</th>
                <th>spikes</th>
                <th>patrols</th>
                <th>memory</th>
                <th>alerts</th>
                <th>errors</th>
              </tr>
            </thead>
            <tbody>
              {schedulerTicks.map((tick) => (
                <tr key={schedulerTickIdentifier(tick)}>
                  <td>{shortId(schedulerTickIdentifier(tick))}</td>
                  <td>
                    <span className={`status-pill ${runStatusTone(tick.status)}`}>{tick.status || "-"}</span>
                  </td>
                  <td>{tick.reason || "-"}</td>
                  <td>{formatOptionalDate(tick.started_at)}</td>
                  <td>{formatDuration(tick.duration_ms)}</td>
                  <td>{processedCount(tick.processed, "due_tasks")}</td>
                  <td>{processedCount(tick.processed, "review_tasks")}</td>
                  <td>{processedCount(tick.processed, "event_spikes")}</td>
                  <td>{processedCount(tick.processed, "pool_patrols")}</td>
                  <td>{processedCount(tick.processed, "memory_summaries")}</td>
                  <td>{processedCount(tick.processed, "alert_drafts")}</td>
                  <td>
                    <span className={tick.errors?.length ? "agent-run-error" : ""}>{tick.errors?.length || 0}</span>
                  </td>
                </tr>
              ))}
              {!schedulerTicks.length && (
                <tr>
                  <td colSpan={12}>
                    <div className="agent-table-empty">{loading ? "Scheduler ticks 加载中..." : "暂无 scheduler tick"}</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="agent-trigger-section">
        <div className="agent-trigger-section-head">
          <div>
            <strong>Event triggers</strong>
            <span>事件突增只表示唤醒 Agent，不直接代表最终风险等级。</span>
          </div>
          <span>{eventTriggers.length}</span>
        </div>
        <div className="agent-event-trigger-grid">
          {eventTriggers.map((trigger) => (
            <article className="agent-event-trigger-card" key={eventTriggerIdentifier(trigger)}>
              <div className="agent-event-trigger-head">
                <div>
                  <strong>{trigger.signal || "event_spike"}</strong>
                  <span>{shortId(eventTriggerIdentifier(trigger))}</span>
                </div>
                <span className={`status-pill ${runStatusTone(trigger.status)}`}>{trigger.status || "-"}</span>
              </div>
              <div className="agent-task-meta-grid">
                <TaskMeta label="site_id" value={trigger.site_id || "-"} />
                <TaskMeta label="pool_id" value={trigger.pool_id || "-"} />
                <TaskMeta label="run_id" value={shortId(trigger.run_id)} />
                <TaskMeta label="created" value={formatOptionalDate(trigger.created_at)} />
              </div>
              <div className="agent-trigger-dedupe">
                <span>dedupe_key</span>
                <strong>{trigger.dedupe_key || "-"}</strong>
              </div>
              <JsonBlock title="evidence" value={trigger.evidence || {}} />
              {trigger.error && <div className="agent-trace-error">{trigger.error}</div>}
            </article>
          ))}
          {!eventTriggers.length && <div className="agent-task-empty">{loading ? "Event triggers 加载中..." : "暂无 event trigger"}</div>}
        </div>
      </section>

      <section className="agent-trigger-section">
        <div className="agent-trigger-section-head">
          <div>
            <strong>Patrol runs</strong>
            <span>查看巡检处理结果、跳过原因和必巡池标记。</span>
          </div>
          <span>{patrolRuns.length}</span>
        </div>
        <div className="table-wrap agent-trigger-table-wrap">
          <table className="agent-trigger-table patrol-history-table">
            <thead>
              <tr>
                <th>patrol_id</th>
                <th>status</th>
                <th>pool_id</th>
                <th>required</th>
                <th>reason</th>
                <th>skip_reason</th>
                <th>run_id</th>
                <th>decision</th>
                <th>task</th>
                <th>severity</th>
                <th>started</th>
              </tr>
            </thead>
            <tbody>
              {patrolRuns.map((patrol) => (
                <tr key={patrolIdentifier(patrol)}>
                  <td>{shortId(patrolIdentifier(patrol))}</td>
                  <td>
                    <span className={`status-pill ${runStatusTone(patrol.status)}`}>{patrol.status || "-"}</span>
                  </td>
                  <td>{patrol.pool_id || "-"}</td>
                  <td>{patrol.required_patrol ? "是" : "否"}</td>
                  <td>{patrol.reason || "-"}</td>
                  <td>
                    <span className={patrol.skip_reason ? "agent-run-error" : ""}>{patrol.skip_reason || "-"}</span>
                  </td>
                  <td>{shortId(patrol.run_id)}</td>
                  <td>{shortId(patrol.decision_id)}</td>
                  <td>{shortId(patrol.task_id)}</td>
                  <td>{patrol.severity || "-"}</td>
                  <td>{formatOptionalDate(patrol.started_at)}</td>
                </tr>
              ))}
              {!patrolRuns.length && (
                <tr>
                  <td colSpan={11}>
                    <div className="agent-table-empty">{loading ? "Patrol runs 加载中..." : "暂无 patrol run"}</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}

function EvalsTab({
  cases,
  categories,
  runs,
  results,
  selectedEvalRunId,
  categoryFilter,
  mode,
  loading,
  running,
  onCategoryFilterChange,
  onModeChange,
  onRunEvalSuite,
  onSelectEvalRun,
}: {
  cases: AgentEvalCase[];
  categories: string[];
  runs: AgentEvalRun[];
  results: AgentEvalResult[];
  selectedEvalRunId: string;
  categoryFilter: string;
  mode: "llm_live" | "llm_mock";
  loading: boolean;
  running: boolean;
  onCategoryFilterChange: (value: string) => void;
  onModeChange: (value: "llm_live" | "llm_mock") => void;
  onRunEvalSuite: () => void;
  onSelectEvalRun: (evalRunId: string) => void;
}) {
  const selectedRun = runs.find((run) => evalRunIdentifier(run) === selectedEvalRunId);
  const failedResults = results.filter((item) => item.status === "failed").length;
  return (
    <div className="agent-workbench-tab-body">
      <div className="panel-header">
        <div>
          <h3>Evals</h3>
          <p>把 Agent 回归样例变成可见工具，用来验证 prompt、Context Pack、Router、Validator 和安全边界。</p>
        </div>
        <span className={`status-pill ${failedResults ? "warning" : "accent"}`}>{loading || running ? "running" : `${results.length} results`}</span>
      </div>

      <section className="agent-eval-control">
        <div className="agent-eval-control-grid">
          <label>
            <span>category</span>
            <select value={categoryFilter} onChange={(event) => onCategoryFilterChange(event.target.value)}>
              <option value="all">all</option>
              {categories.map((category) => (
                <option key={category} value={category}>
                  {category}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>mode</span>
            <select value={mode} onChange={(event) => onModeChange(event.target.value as "llm_live" | "llm_mock")}>
              <option value="llm_live">llm_live</option>
              <option value="llm_mock">llm_mock</option>
            </select>
          </label>
          <button type="button" onClick={onRunEvalSuite} disabled={running || loading}>
            {running ? "运行中..." : "运行评测"}
          </button>
        </div>
        <div className="agent-eval-case-strip">
          <Metric label="cases" value={cases.length} />
          <Metric label="categories" value={categories.length} />
          <Metric label="mode" value={mode} />
          <Metric label="failed results" value={failedResults} />
        </div>
      </section>

      <section className="agent-trigger-section">
        <div className="agent-trigger-section-head">
          <div>
            <strong>Eval runs</strong>
            <span>发布前或 prompt 改动后运行一次，观察通过率是否退化。</span>
          </div>
          <span>{runs.length}</span>
        </div>
        <div className="table-wrap agent-eval-table-wrap">
          <table className="agent-eval-runs-table">
            <thead>
              <tr>
                <th>eval_run_id</th>
                <th>suite</th>
                <th>mode</th>
                <th>status</th>
                <th>score</th>
                <th>passed</th>
                <th>failed</th>
                <th>started</th>
                <th>duration</th>
                <th>results</th>
              </tr>
            </thead>
            <tbody>
              {runs.map((run) => {
                const runId = evalRunIdentifier(run);
                const summary = run.summary || {};
                return (
                  <tr className={runId === selectedEvalRunId ? "selected-row" : ""} key={runId}>
                    <td>{shortId(runId)}</td>
                    <td>{run.suite || "default"}</td>
                    <td>{run.mode || "-"}</td>
                    <td>
                      <span className={`status-pill ${runStatusTone(run.status)}`}>{run.status || "-"}</span>
                    </td>
                    <td>{percentValue(recordValue(summary, "score"))}</td>
                    <td>{displayValue(recordValue(summary, "passed"))}</td>
                    <td>
                      <span className={Number(recordValue(summary, "failed") || 0) ? "agent-run-error" : ""}>
                        {displayValue(recordValue(summary, "failed"))}
                      </span>
                    </td>
                    <td>{formatOptionalDate(run.started_at)}</td>
                    <td>{formatDuration(run.duration_ms)}</td>
                    <td>
                      <button className="compact-button ghost" type="button" onClick={() => onSelectEvalRun(runId)}>
                        查看
                      </button>
                    </td>
                  </tr>
                );
              })}
              {!runs.length && (
                <tr>
                  <td colSpan={10}>
                    <div className="agent-table-empty">{loading ? "Eval runs 加载中..." : "暂无 eval run"}</div>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>

      <section className="agent-trigger-section">
        <div className="agent-trigger-section-head">
          <div>
            <strong>Eval results</strong>
            <span>{selectedRun ? `当前 run：${shortId(evalRunIdentifier(selectedRun))}` : "选择一个 eval run 查看 case 明细。"}</span>
          </div>
          <span>{results.length}</span>
        </div>
        <div className="agent-eval-result-grid">
          {results.map((result) => (
            <EvalResultCard key={evalResultIdentifier(result)} result={result} />
          ))}
          {!results.length && <div className="agent-task-empty">{loading ? "Eval results 加载中..." : "暂无 eval result"}</div>}
        </div>
      </section>

      <section className="agent-trigger-section">
        <div className="agent-trigger-section-head">
          <div>
            <strong>Eval cases</strong>
            <span>当前默认评测样例，帮助定位是 Router、Context Pack、决策输出还是安全边界退化。</span>
          </div>
          <span>{cases.length}</span>
        </div>
        <div className="agent-eval-case-grid">
          {cases.slice(0, 12).map((item) => (
            <div className="agent-eval-case-card" key={item.case_id}>
              <strong>{item.case_id || "-"}</strong>
              <span>{item.category || "unknown"} · {item.input_mode || "unknown"} · min {item.min_score ?? 1}</span>
              <p>{item.description || "无描述"}</p>
            </div>
          ))}
          {!cases.length && <div className="agent-task-empty">{loading ? "Eval cases 加载中..." : "暂无 eval case"}</div>}
        </div>
      </section>
    </div>
  );
}

function MemoryTab({
  memories,
  memoryTypes,
  memoryTypeFilter,
  poolFilter,
  loading,
  onMemoryTypeFilterChange,
  onPoolFilterChange,
  onLoadMemory,
}: {
  memories: AgentMemorySummary[];
  memoryTypes: string[];
  memoryTypeFilter: string;
  poolFilter: string;
  loading: boolean;
  onMemoryTypeFilterChange: (value: string) => void;
  onPoolFilterChange: (value: string) => void;
  onLoadMemory: (typeFilter?: string, poolFilter?: string) => void;
}) {
  const typeCounts = countMemoriesByType(memories);
  return (
    <div className="agent-workbench-tab-body">
      <div className="panel-header">
        <div>
          <h3>Memory</h3>
          <p>查看长期记忆是否沉淀了事实、模式、经验和风险基线。</p>
        </div>
        <span className="status-pill accent">{loading ? "loading" : `${memories.length} memories`}</span>
      </div>

      <section className="agent-memory-toolbar">
        <label>
          <span>memory_type</span>
          <select
            value={memoryTypeFilter}
            onChange={(event) => {
              onMemoryTypeFilterChange(event.target.value);
              onLoadMemory(event.target.value, poolFilter);
            }}
          >
            <option value="all">all</option>
            {memoryTypes.map((type) => (
              <option key={type} value={type}>
                {type}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>pool_id</span>
          <input value={poolFilter} onChange={(event) => onPoolFilterChange(event.target.value)} placeholder="可选，按 pool_id 过滤" />
        </label>
        <button type="button" onClick={() => onLoadMemory()} disabled={loading}>
          查询 Memory
        </button>
      </section>

      <div className="agent-memory-type-strip">
        {["operator_feedback_summary", "decision_review", "pool_daily_summary", "pool_weekly_summary", "survival_pattern", "future_playbook"].map((type) => (
          <Metric key={type} label={type} value={typeCounts[type] || 0} />
        ))}
      </div>

      <div className="agent-memory-grid">
        {memories.map((memory) => (
          <MemoryCard key={memoryIdentifier(memory)} memory={memory} />
        ))}
        {!memories.length && <div className="agent-task-empty">{loading ? "Memory 加载中..." : "暂无长期记忆"}</div>}
      </div>
    </div>
  );
}

function NotificationsTab({
  notifications,
  statusFilter,
  loading,
  busyTaskId,
  onStatusFilterChange,
  onLoadNotifications,
  onDispatchNotification,
}: {
  notifications: AgentNotificationItem[];
  statusFilter: string;
  loading: boolean;
  busyTaskId: string;
  onStatusFilterChange: (value: string) => void;
  onLoadNotifications: (statusFilter?: string) => void;
  onDispatchNotification: (taskId: string) => void;
}) {
  const counts = countNotificationsByStatus(notifications);
  return (
    <div className="agent-workbench-tab-body">
      <div className="panel-header">
        <div>
          <h3>Notifications</h3>
          <p>查看 Agent 告警草稿、钉钉通知发送审计和投递失败原因。</p>
        </div>
        <span className="status-pill accent">{loading ? "loading" : `${notifications.length} alerts`}</span>
      </div>

      <section className="agent-notification-toolbar">
        <label>
          <span>alert_status</span>
          <select
            value={statusFilter}
            onChange={(event) => {
              onStatusFilterChange(event.target.value);
              onLoadNotifications(event.target.value);
            }}
          >
            <option value="all">all</option>
            <option value="drafted">drafted</option>
            <option value="sent">sent</option>
            <option value="failed">failed</option>
          </select>
        </label>
        <div className="agent-notification-stats">
          <Metric label="drafted" value={counts.drafted || 0} />
          <Metric label="sent" value={counts.sent || 0} />
          <Metric label="failed" value={counts.failed || 0} />
          <Metric label="unknown" value={counts.unknown || 0} />
        </div>
      </section>

      <div className="agent-notification-grid">
        {notifications.map((item) => (
          <NotificationCard
            busy={busyTaskId === String(item.task_id || "")}
            item={item}
            key={notificationIdentifier(item)}
            onDispatchNotification={onDispatchNotification}
          />
        ))}
        {!notifications.length && <div className="agent-task-empty">{loading ? "Notifications 加载中..." : "暂无 Agent notification"}</div>}
      </div>
    </div>
  );
}

function PricingReservedTab() {
  return (
    <ReservedWorkbenchTab
      title="Pricing Decisions"
      description="价格策略 Agent 的预留视图。阶段九只展示未来字段和审计原则，不自动改价格。"
      status="reserved"
      fields={[
        ["pricing_decision_id", "价格建议记录 ID"],
        ["pool_id / site_id", "建议适用的池和站点"],
        ["current_price", "当前中转站价格"],
        ["suggested_price", "Agent 建议价格"],
        ["reason", "建议调价原因"],
        ["evidence", "封号、容量、成本、需求变化等证据"],
        ["user_usage_attribution", "用量上涨来自单用户还是多用户"],
        ["risk_of_change", "调价风险和可能影响"],
        ["requires_human_confirm", "是否必须人工确认"],
        ["status", "drafted / waiting_human / approved / rejected / closed"],
      ]}
      principles={["只建议价格", "不自动改价格", "需要人工确认", "写审计", "后续可关联 user_usage_windows 和 pricing_decision"]}
    />
  );
}

function UsageAttributionReservedTab() {
  return (
    <ReservedWorkbenchTab
      title="User Usage Attribution"
      description="用户用量归因的预留视图。后续进入 Context Pack 后，用于解释用量上涨来自单用户、少数用户还是整体增长。"
      status="reserved"
      fields={[
        ["top_users", "窗口内用量最高用户列表"],
        ["top_user_share", "第一大用户占比"],
        ["top_3_share", "前三用户合计占比"],
        ["active_user_count", "窗口内活跃用户数"],
        ["distribution", "用量分布，例如 head/tail 或分位数"],
        ["single_user_dominant", "是否由单用户主导"],
        ["multi_user_growth", "是否为多用户共同上涨"],
        ["peak_hour", "峰值小时"],
        ["change_vs_previous_window", "相对上一窗口变化"],
      ]}
      principles={[
        "先作为只读归因输入",
        "进入 Context Pack 后可被 Agent 决策追溯",
        "辅助补号、告警、调价判断",
        "不直接限制用户、不直接改价格",
      ]}
    />
  );
}

function ReservedWorkbenchTab({
  title,
  description,
  status,
  fields,
  principles,
}: {
  title: string;
  description: string;
  status: string;
  fields: Array<[string, string]>;
  principles: string[];
}) {
  return (
    <div className="agent-workbench-tab-body">
      <div className="panel-header">
        <div>
          <h3>{title}</h3>
          <p>{description}</p>
        </div>
        <span className="status-pill muted">{status}</span>
      </div>
      <div className="agent-reserved-grid">
        <section className="agent-reserved-section">
          <strong>Reserved fields</strong>
          <div className="agent-reserved-field-list">
            {fields.map(([name, body]) => (
              <div key={name}>
                <code>{name}</code>
                <span>{body}</span>
              </div>
            ))}
          </div>
        </section>
        <section className="agent-reserved-section">
          <strong>Principles</strong>
          <div className="agent-reserved-principles">
            {principles.map((item) => (
              <span key={item}>{item}</span>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
}

function NotificationCard({
  item,
  busy,
  onDispatchNotification,
}: {
  item: AgentNotificationItem;
  busy: boolean;
  onDispatchNotification: (taskId: string) => void;
}) {
  const taskId = String(item.task_id || "");
  const deliveries = Array.isArray(item.deliveries) ? item.deliveries : [];
  return (
    <article className={`agent-notification-card notification-status-${item.alert_status || "unknown"}`}>
      <div className="agent-event-trigger-head">
        <div>
          <strong>{item.alert_title || "Agent alert draft"}</strong>
          <span>{shortId(taskId)} · {item.pool_id || "-"}</span>
        </div>
        <span className={`status-pill ${notificationTone(item.alert_status || item.delivery_status)}`}>{item.alert_status || item.delivery_status || "-"}</span>
      </div>
      <div className="agent-task-meta-grid">
        <TaskMeta label="severity" value={item.severity || "-"} />
        <TaskMeta label="task_status" value={item.task_status || "-"} />
        <TaskMeta label="source_decision" value={shortId(item.source_decision_id)} />
        <TaskMeta label="event_id" value={shortId(item.notification_event_id)} />
        <TaskMeta label="delivery" value={item.delivery_status || "-"} />
        <TaskMeta label="sent_at" value={formatOptionalDate(item.alert_sent_at)} />
      </div>
      <div className="agent-notification-content">
        <span>content</span>
        <strong>{item.alert_content || "暂无告警草稿内容"}</strong>
      </div>
      {item.error && <div className="agent-trace-error">{item.error}</div>}
      <div className="agent-notification-actions">
        {item.alert_status === "drafted" && (
          <button type="button" onClick={() => onDispatchNotification(taskId)} disabled={busy || !taskId}>
            {busy ? "发送中..." : "发送告警草稿"}
          </button>
        )}
        <span>{deliveries.length ? `${deliveries.length} delivery record(s)` : "暂无 delivery 记录"}</span>
      </div>
      <div className="agent-trace-json-grid">
        <JsonBlock title="notification_event" value={item.notification_event || {}} />
        <JsonBlock title="deliveries" value={deliveries} />
        <JsonBlock title="alert_draft" value={item.alert_draft || {}} />
      </div>
    </article>
  );
}

function MemoryCard({ memory }: { memory: AgentMemorySummary }) {
  return (
    <article className="agent-memory-card">
      <div className="agent-event-trigger-head">
        <div>
          <strong>{memory.memory_type || "memory"}</strong>
          <span>{shortId(memoryIdentifier(memory))}</span>
        </div>
        <span className="status-pill muted">{formatOptionalDate(memory.created_at)}</span>
      </div>
      <div className="agent-task-meta-grid">
        <TaskMeta label="pool_id" value={memory.pool_id || "-"} />
        <TaskMeta label="site_id" value={memory.site_id || "-"} />
        <TaskMeta label="period_start" value={formatOptionalDate(memory.period_start)} />
        <TaskMeta label="period_end" value={formatOptionalDate(memory.period_end)} />
      </div>
      <div className="agent-memory-summary">
        <span>summary</span>
        <strong>{memory.summary || "暂无摘要"}</strong>
      </div>
      <div className="agent-memory-lists">
        <MemoryList title="facts" items={memory.facts} />
        <MemoryList title="patterns" items={memory.patterns} />
        <MemoryList title="lessons" items={memory.lessons} />
      </div>
      <div className="agent-trace-json-grid">
        <JsonBlock title="risk_baselines" value={memory.risk_baselines || {}} />
        <JsonBlock
          title="sources"
          value={{
            source_run_ids: memory.source_run_ids || [],
            source_decision_ids: memory.source_decision_ids || [],
          }}
        />
      </div>
    </article>
  );
}

function MemoryList({ title, items }: { title: string; items?: unknown[] }) {
  const values = (items || []).map((item) => String(item)).filter(Boolean).slice(0, 5);
  return (
    <div className="agent-memory-list">
      <span>{title}</span>
      {values.map((value, index) => (
        <strong key={`${title}:${index}`}>{value}</strong>
      ))}
      {!values.length && <em>暂无</em>}
    </div>
  );
}

function EvalResultCard({ result }: { result: AgentEvalResult }) {
  const failedAssertions = (result.assertions || []).filter((item) => !item.passed);
  return (
    <article className={`agent-eval-result-card eval-status-${result.status || "unknown"}`}>
      <div className="agent-event-trigger-head">
        <div>
          <strong>{result.case_id || "-"}</strong>
          <span>{result.category || "unknown"}</span>
        </div>
        <span className={`status-pill ${runStatusTone(result.status)}`}>{result.status || "-"}</span>
      </div>
      <div className="agent-task-meta-grid">
        <TaskMeta label="score" value={percentValue(result.score)} />
        <TaskMeta label="duration" value={formatDuration(result.duration_ms)} />
        <TaskMeta label="failed assertions" value={failedAssertions.length} />
        <TaskMeta label="created" value={formatOptionalDate(result.created_at)} />
      </div>
      <div className="agent-eval-failure-list">
        {(result.failure_reasons || []).slice(0, 4).map((reason, index) => (
          <span key={`${result.case_id}:failure:${index}`}>{reason}</span>
        ))}
        {!result.failure_reasons?.length && <span>无失败原因</span>}
      </div>
      <div className="agent-trace-json-grid">
        <JsonBlock title="assertions" value={result.assertions || []} />
        <JsonBlock title="output_summary" value={result.output_summary || {}} />
      </div>
    </article>
  );
}

function TraceStepCard({ step }: { step: AgentRunStep }) {
  const output = step.output_summary && typeof step.output_summary === "object" ? step.output_summary : {};
  const thoughtSummary = textFromRecord(output, "thought_summary") || textFromRecord(output, "summary") || textFromRecord(output, "reason");
  const llmModel = textFromRecord(step.llm, "model");
  const llmFramework = textFromRecord(step.llm, "framework");
  const capabilityCalls = Array.isArray(step.capability_calls) ? step.capability_calls : [];
  const taskUpdateResult = recordValue(output, "task_update_result");
  return (
    <article className={`agent-trace-step step-status-${step.status || "unknown"}`}>
      <div className="agent-trace-step-head">
        <div>
          <strong>
            #{step.step_index ?? "-"} {step.step_type || "unknown_step"}
          </strong>
          <span>{step.intent || "no intent"}</span>
        </div>
        <span className={`status-pill ${runStatusTone(step.status)}`}>{step.status || "-"}</span>
      </div>
      <div className="agent-trace-step-grid">
        <TaskMeta label="started" value={formatOptionalDate(step.started_at)} />
        <TaskMeta label="finished" value={formatOptionalDate(step.finished_at)} />
        <TaskMeta label="duration" value={formatDuration(step.duration_ms)} />
        <TaskMeta label="llm" value={[llmModel, llmFramework].filter(Boolean).join(" / ") || "-"} />
      </div>
      <div className="agent-trace-summary">
        <span>thought_summary</span>
        <strong>{thoughtSummary || "暂无摘要"}</strong>
      </div>
      <div className="agent-trace-json-grid">
        <JsonBlock title="input_summary" value={step.input_summary || {}} />
        <JsonBlock title="output_summary" value={safeOutputSummary(output)} />
        <JsonBlock title="capability_calls" value={capabilityCalls} />
        <JsonBlock title="task_update_result" value={taskUpdateResult || {}} />
      </div>
      {step.error && <div className="agent-trace-error">{step.error}</div>}
    </article>
  );
}

function JsonBlock({ title, value }: { title: string; value: unknown }) {
  return (
    <details className="agent-json-block">
      <summary>{title}</summary>
      <pre>{formatJson(value)}</pre>
    </details>
  );
}

function TaskCard({
  task,
  busyKey,
  onAccountsAdded,
  onObserve,
  onClose,
  onReviewDue,
  onDispatchAlert,
  onCopyTaskId,
}: {
  task: AgentTask;
  busyKey: string;
  onAccountsAdded: (task: AgentTask) => void;
  onObserve: (task: AgentTask) => void;
  onClose: (task: AgentTask) => void;
  onReviewDue: (task: AgentTask) => void;
  onDispatchAlert: (task: AgentTask) => void;
  onCopyTaskId: (task: AgentTask) => void;
}) {
  const taskId = taskIdentifier(task);
  const status = normalizeTaskStatus(task.status);
  const latestReason = taskLatestReason(task);
  return (
    <article className={`agent-task-card task-status-${status}`}>
      <div className="agent-task-card-title">
        <div>
          <strong>{task.title || "Agent task"}</strong>
          <button className="link-button" type="button" onClick={() => onCopyTaskId(task)}>
            {shortId(taskId)}
          </button>
        </div>
        <span className={`status-pill ${taskStatusTone(status)}`}>{taskStatusLabel(status)}</span>
      </div>
      <div className="agent-task-meta-grid">
        <TaskMeta label="pool_id" value={task.pool_id || "-"} />
        <TaskMeta label="severity" value={task.severity || "-"} />
        <TaskMeta label="补号方案" value={task.refill_plan_summary || typedTaskRefillPlan(task)} />
        <TaskMeta label="next_check" value={formatOptionalDate(task.next_check_at)} />
        <TaskMeta label="updated" value={formatOptionalDate(task.updated_at)} />
      </div>
      <details className="agent-task-more">
        <summary>更多字段</summary>
        <div className="agent-task-meta-grid">
          <TaskMeta label="task_type" value={task.task_type || "-"} />
          <TaskMeta label="alert" value={task.alert_status || "-"} />
          <TaskMeta label="review_after" value={formatOptionalDate(task.review_after)} />
          <TaskMeta label="decision" value={shortId(task.current_decision_id)} />
        </div>
      </details>
      <div className="agent-task-reason-line">
        <span>最近原因</span>
        <strong>{latestReason || "暂无状态变化原因"}</strong>
      </div>
      <div className="agent-task-actions">
        {canAccountsAdded(status) && (
          <button type="button" onClick={() => onAccountsAdded(task)} disabled={isBusy(busyKey, task, "accounts_added")}>
            已补号
          </button>
        )}
        {canObserve(status) && (
          <button className="ghost" type="button" onClick={() => onObserve(task)} disabled={isBusy(busyKey, task, "observe")}>
            先观察
          </button>
        )}
        {canReviewDue(status) && (
          <button className="ghost" type="button" onClick={() => onReviewDue(task)} disabled={isBusy(busyKey, task, "review-due")}>
            转复盘
          </button>
        )}
        {canDispatchAlert(task, status) && (
          <button className="warning-button" type="button" onClick={() => onDispatchAlert(task)} disabled={isBusy(busyKey, task, "dispatch-alert")}>
            发送告警草稿
          </button>
        )}
        {canClose(status) && (
          <button className="danger-button" type="button" onClick={() => onClose(task)} disabled={isBusy(busyKey, task, "close")}>
            关闭
          </button>
        )}
      </div>
    </article>
  );
}

function TaskMeta({ label, value }: { label: string; value: unknown }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{String(value ?? "-")}</strong>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: unknown }) {
  return (
    <div className="compact-stat agent-metric">
      <span>{label}</span>
      <strong>{String(value ?? "-")}</strong>
    </div>
  );
}

function groupTasksByStatus(tasks: AgentTask[]): Record<AgentTaskStatus, AgentTask[]> {
  const grouped: Record<AgentTaskStatus, AgentTask[]> = {
    open: [],
    observing: [],
    waiting_human: [],
    alert_drafted: [],
    review_due: [],
    closed: [],
    failed: [],
  };
  for (const task of tasks) {
    grouped[normalizeTaskStatus(task.status)].push(task);
  }
  return grouped;
}

function countTasks(tasks: AgentTask[]): Record<AgentTaskStatus, number> {
  const counts: Record<AgentTaskStatus, number> = {
    open: 0,
    observing: 0,
    waiting_human: 0,
    alert_drafted: 0,
    review_due: 0,
    closed: 0,
    failed: 0,
  };
  for (const task of tasks) {
    counts[normalizeTaskStatus(task.status)] += 1;
  }
  return counts;
}

function countMemoriesByType(memories: AgentMemorySummary[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const memory of memories) {
    const type = String(memory.memory_type || "unknown");
    counts[type] = (counts[type] || 0) + 1;
  }
  return counts;
}

function countNotificationsByStatus(items: AgentNotificationItem[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const item of items) {
    const status = String(item.alert_status || item.delivery_status || "unknown");
    counts[status] = (counts[status] || 0) + 1;
  }
  return counts;
}

function normalizeTaskStatus(value?: string | null): AgentTaskStatus {
  return taskStatuses.includes(value as AgentTaskStatus) ? (value as AgentTaskStatus) : "open";
}

function taskIdentifier(task: AgentTask): string {
  return String(task.task_id || task._id || "");
}

function runIdentifier(run: AgentRun): string {
  return String(run.run_id || run._id || "");
}

function stepIdentifier(step: AgentRunStep): string {
  return String(step.step_id || step._id || `${step.run_id || "run"}:${step.step_index ?? "step"}`);
}

function schedulerTickIdentifier(tick: AgentSchedulerTick): string {
  return String(tick.tick_id || tick._id || "");
}

function eventTriggerIdentifier(trigger: AgentEventTrigger): string {
  return String(trigger.trigger_id || trigger._id || "");
}

function patrolIdentifier(patrol: AgentPatrolRun): string {
  return String(patrol.patrol_id || patrol.run_id || patrol._id || "");
}

function evalRunIdentifier(run: AgentEvalRun): string {
  return String(run.eval_run_id || run._id || "");
}

function evalResultIdentifier(result: AgentEvalResult): string {
  return String(result._id || `${result.eval_run_id || "eval"}:${result.case_id || "case"}`);
}

function memoryIdentifier(memory: AgentMemorySummary): string {
  return String(memory.memory_id || "");
}

function notificationIdentifier(item: AgentNotificationItem): string {
  return String(item.task_id || item.notification_event_id || `${item.pool_id || "pool"}:${item.updated_at || item.created_at || "notification"}`);
}

function runTaskId(run: AgentRun): string {
  const agentTask = recordValue(run.agent, "task");
  const taskFromAgent = typeof agentTask === "object" && agentTask ? recordValue(agentTask as Record<string, unknown>, "task_id") : null;
  const taskFromMetadata = recordValue(run.metadata, "task_id") || recordValue(run.trigger_metadata, "task_id");
  return String(taskFromAgent || taskFromMetadata || "");
}

function recordValue(record: Record<string, unknown> | null | undefined, key: string): unknown {
  return record && typeof record === "object" ? record[key] : null;
}

function textFromRecord(record: Record<string, unknown> | null | undefined, key: string): string {
  const value = recordValue(record, key);
  return typeof value === "string" ? value.trim() : "";
}

function safeOutputSummary(output: Record<string, unknown>): Record<string, unknown> {
  const hiddenKeys = new Set(["raw_thoughts", "chain_of_thought", "reasoning", "hidden_reasoning"]);
  return Object.fromEntries(Object.entries(output).filter(([key]) => !hiddenKeys.has(key)));
}

function formatJson(value: unknown): string {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value ?? "");
  }
}

function shortId(value?: string | null): string {
  const text = String(value || "");
  return text.length > 14 ? `${text.slice(0, 7)}...${text.slice(-4)}` : text || "-";
}

function typedTaskRefillPlan(task: AgentTask): string {
  const count = Number(task.suggested_add_count || 0);
  if (count <= 0) return "-";
  const accountType = String(task.suggested_account_type || "").trim().toLowerCase();
  const labels: Record<string, string> = { k12: "K12", plus: "Plus", pro: "Pro", team: "Team", free: "Free" };
  return `${labels[accountType] || accountType.toUpperCase() || "待指定"} ${count} 个`;
}

function taskLatestReason(task: AgentTask): string {
  const direct = String(task.latest_state_reason || "").trim();
  if (direct) return direct;
  const history = Array.isArray(task.state_history) ? task.state_history : [];
  for (let index = history.length - 1; index >= 0; index -= 1) {
    const reason = String(history[index]?.reason || "").trim();
    if (reason) return reason;
  }
  return "";
}

function formatOptionalDate(value?: string | null): string {
  return value ? formatDateTime(value) : "-";
}

function formatDuration(value?: number | null): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  if (value < 1000) return `${value}ms`;
  return `${(value / 1000).toFixed(1)}s`;
}

function processedCount(processed: Record<string, unknown> | null | undefined, key: string): string {
  const value = recordValue(processed, key);
  if (typeof value === "number") return String(value);
  if (Array.isArray(value)) return String(value.length);
  if (value && typeof value === "object") {
    const record = value as Record<string, unknown>;
    for (const countKey of ["total_processed", "processed", "selected", "total_created", "sent"]) {
      const count = record[countKey];
      if (typeof count === "number") return String(count);
    }
    if (Array.isArray(record.processed)) return String(record.processed.length);
    if (Array.isArray(record.generated)) return String(record.generated.length);
  }
  return value == null ? "-" : String(value);
}

function percentValue(value: unknown): string {
  if (typeof value !== "number" || Number.isNaN(value)) return "-";
  return `${Math.round(value * 100)}%`;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function taskStatusLabel(status: AgentTaskStatus): string {
  const labels: Record<AgentTaskStatus, string> = {
    open: "open",
    observing: "observing",
    waiting_human: "waiting_human",
    alert_drafted: "alert_drafted",
    review_due: "review_due",
    closed: "closed",
    failed: "failed",
  };
  return labels[status];
}

function taskStatusTone(status: AgentTaskStatus): "success" | "warning" | "danger" | "muted" | "accent" {
  if (status === "closed") return "success";
  if (status === "failed") return "danger";
  if (status === "waiting_human" || status === "alert_drafted" || status === "review_due") return "warning";
  return "accent";
}

function runStatusTone(status?: string | null): "success" | "warning" | "danger" | "muted" | "accent" {
  if (status === "success" || status === "processed" || status === "sent") return "success";
  if (status === "failed") return "danger";
  if (status === "running" || status === "created" || status === "partial") return "warning";
  return "muted";
}

function notificationTone(status?: string | null): "success" | "warning" | "danger" | "muted" | "accent" {
  if (status === "sent" || status === "success") return "success";
  if (status === "failed") return "danger";
  if (status === "drafted" || status === "pending" || status === "partial") return "warning";
  return "muted";
}

function canAccountsAdded(status: AgentTaskStatus): boolean {
  return status === "waiting_human";
}

function canObserve(status: AgentTaskStatus): boolean {
  return status === "open" || status === "waiting_human";
}

function canReviewDue(status: AgentTaskStatus): boolean {
  return status === "observing" || status === "waiting_human" || status === "alert_drafted";
}

function canDispatchAlert(task: AgentTask, status: AgentTaskStatus): boolean {
  return status === "alert_drafted" && task.alert_status === "drafted";
}

function canClose(status: AgentTaskStatus): boolean {
  return status !== "closed" && status !== "failed";
}

function isBusy(busyKey: string, task: AgentTask, action: string): boolean {
  return busyKey === `${taskIdentifier(task)}:${action}`;
}
