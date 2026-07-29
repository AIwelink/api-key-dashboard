import { useEffect, useState } from "react";

import { api } from "../api/client";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import { errorMessage, formatDateTime } from "../utils/format";


type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

export type PlusGroupOption = {
  id: number;
  name: string;
  status: string;
};

export type PlusGroupSelection = {
  source_group_id: number;
  plus_group_id: number;
  banned_group_id: number;
  plus_error_group_id: number;
};

type PlusGroupRole = keyof PlusGroupSelection;

export type PlusSelfProducedStatus = {
  site_id: string;
  source_group_id: number;
  plus_group_id: number;
  banned_group_id: number;
  plus_error_group_id: number;
  model: string;
  running: boolean;
  settings: {
    enabled: boolean;
    interval_minutes: number;
    source_group_id: number;
    plus_group_id: number;
    banned_group_id: number;
    plus_error_group_id: number;
    last_finished_at?: string | null;
    last_status?: string | null;
  };
  last_run: {
    status: string;
    candidates?: number;
    tested?: number;
    eligible?: number;
    promoted?: number;
    banned?: number;
    downgraded?: number;
    plus_errors?: number;
    failed?: number;
    started_at?: string | null;
    finished_at?: string | null;
    error?: string | null;
  } | null;
};

export type PlusSelfProducedResult = {
  id: string;
  remote_account_id: number | string;
  account_name: string;
  email?: string | null;
  classification: string;
  action_status: string;
  error?: string | null;
  model?: string;
  latency_ms?: number | null;
  resulting_name?: string;
  tested_at: string;
};

type ResultsResponse = {
  items: PlusSelfProducedResult[];
  total: number;
  page: number;
  page_size: number;
};

const RESULTS_PAGE_SIZE = 100;
const DEFAULT_GROUP_SELECTION: PlusGroupSelection = {
  source_group_id: 4,
  plus_group_id: 6,
  banned_group_id: 7,
  plus_error_group_id: 9,
};

type ViewProps = {
  status: PlusSelfProducedStatus | null;
  results: PlusSelfProducedResult[];
  resultsTotal: number;
  resultsPage: number;
  resultsPageSize: number;
  enabled: boolean;
  intervalMinutes: number;
  groups: PlusGroupOption[];
  groupSelection: PlusGroupSelection;
  configurationReady: boolean;
  loading: boolean;
  saving: boolean;
  running: boolean;
  onEnabledChange: (enabled: boolean) => void;
  onIntervalChange: (minutes: number) => void;
  onGroupChange: (role: PlusGroupRole, groupId: number) => void;
  onSave: () => void;
  onRun: () => void;
  onPageChange: (page: number) => void;
};

export function PlusSelfProducedPage({ token, showToast }: Props) {
  const [status, setStatus] = useState<PlusSelfProducedStatus | null>(null);
  const [results, setResults] = useState<PlusSelfProducedResult[]>([]);
  const [resultsTotal, setResultsTotal] = useState(0);
  const [resultsPage, setResultsPage] = useState(1);
  const [enabled, setEnabled] = useState(true);
  const [intervalMinutes, setIntervalMinutes] = useState(15);
  const [groups, setGroups] = useState<PlusGroupOption[]>([]);
  const [groupSelection, setGroupSelection] = useState<PlusGroupSelection>(DEFAULT_GROUP_SELECTION);
  const [configurationToken, setConfigurationToken] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [running, setRunning] = useState(false);

  const load = async (syncForm = false, page = resultsPage) => {
    const [nextStatus, nextResults] = await Promise.all([
      api<PlusSelfProducedStatus>("/plus-self-produced/status", token),
      api<ResultsResponse>(`/plus-self-produced/results?page=${page}&page_size=${RESULTS_PAGE_SIZE}`, token),
    ]);
    setStatus(nextStatus);
    setResults(nextResults.items);
    setResultsTotal(nextResults.total);
    setResultsPage(nextResults.page);
    if (syncForm) {
      setEnabled(nextStatus.settings.enabled);
      setIntervalMinutes(nextStatus.settings.interval_minutes);
      setGroupSelection(groupSelectionFromStatus(nextStatus));
    }
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setConfigurationToken(null);
    setGroups([]);
    setStatus(null);
    Promise.allSettled([
      api<PlusSelfProducedStatus>("/plus-self-produced/status", token),
      api<ResultsResponse>(`/plus-self-produced/results?page=1&page_size=${RESULTS_PAGE_SIZE}`, token),
      api<PlusGroupOption[]>("/plus-self-produced/groups", token),
    ])
      .then(([statusResult, resultsResult, groupsResult]) => {
        if (cancelled) return;
        const errors: unknown[] = [];
        if (statusResult.status === "fulfilled") {
          const nextStatus = statusResult.value;
          setStatus(nextStatus);
          setEnabled(nextStatus.settings.enabled);
          setIntervalMinutes(nextStatus.settings.interval_minutes);
          setGroupSelection(groupSelectionFromStatus(nextStatus));
        } else {
          errors.push(statusResult.reason);
        }
        if (resultsResult.status === "fulfilled") {
          setResults(resultsResult.value.items);
          setResultsTotal(resultsResult.value.total);
          setResultsPage(resultsResult.value.page);
        } else {
          errors.push(resultsResult.reason);
        }
        if (groupsResult.status === "fulfilled") {
          setGroups(groupsResult.value);
        } else {
          errors.push(groupsResult.reason);
        }
        if (statusResult.status === "fulfilled" && groupsResult.status === "fulfilled") {
          setConfigurationToken(token);
        }
        if (errors.length) {
          showToast(errorMessage(errors[0]), true);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  usePageAutoRefresh(
    () => load(false),
    {
      paused: saving || running,
      onError: (error) => showToast(errorMessage(error), true),
    },
  );

  const save = async () => {
    if (configurationToken !== token) {
      showToast("当前分组配置尚未加载完成", true);
      return;
    }
    if (!hasDistinctGroupSelection(groupSelection)) {
      showToast("四个分组必须一对一，不能重复", true);
      return;
    }
    setSaving(true);
    try {
      const settings = await api<PlusSelfProducedStatus["settings"]>("/plus-self-produced/settings", token, {
        method: "PATCH",
        body: JSON.stringify(buildSettingsPayload(enabled, intervalMinutes, groupSelection)),
      });
      setStatus((current) => current ? {
        ...current,
        source_group_id: settings.source_group_id,
        plus_group_id: settings.plus_group_id,
        banned_group_id: settings.banned_group_id,
        plus_error_group_id: settings.plus_error_group_id,
        settings,
      } : current);
      setEnabled(settings.enabled);
      setIntervalMinutes(settings.interval_minutes);
      setGroupSelection(groupSelectionFromSettings(settings));
      showToast("plus自产探测设置已保存");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  };

  const run = async () => {
    setRunning(true);
    try {
      const result = await api<{
        ok: boolean;
        promoted?: number;
        banned?: number;
        downgraded?: number;
        plus_errors?: number;
        error?: string;
      }>("/plus-self-produced/run", token, {
        method: "POST",
      });
      if (result.ok) {
        showToast(
          `探测完成：晋级 ${result.promoted || 0}，还原 Free ${result.downgraded || 0}，` +
          `Plus 错误 ${result.plus_errors || 0}，转封禁 ${result.banned || 0}`,
        );
      } else {
        showToast(result.error || "探测失败", true);
      }
      await load(true);
    } catch (error) {
      showToast(errorMessage(error), true);
      await load(true).catch(() => undefined);
    } finally {
      setRunning(false);
    }
  };

  const changePage = async (page: number) => {
    setLoading(true);
    try {
      const nextResults = await api<ResultsResponse>(
        `/plus-self-produced/results?page=${page}&page_size=${RESULTS_PAGE_SIZE}`,
        token,
      );
      setResults(nextResults.items);
      setResultsTotal(nextResults.total);
      setResultsPage(nextResults.page);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setLoading(false);
    }
  };

  const workflowRunning = running || status?.running === true;
  return (
    <PlusSelfProducedView
      status={status}
      results={results}
      resultsTotal={resultsTotal}
      resultsPage={resultsPage}
      resultsPageSize={RESULTS_PAGE_SIZE}
      enabled={enabled}
      intervalMinutes={intervalMinutes}
      groups={groups}
      groupSelection={groupSelection}
      configurationReady={configurationToken === token}
      loading={loading}
      saving={saving}
      running={workflowRunning}
      onEnabledChange={setEnabled}
      onIntervalChange={setIntervalMinutes}
      onGroupChange={(role, groupId) => setGroupSelection((current) => ({ ...current, [role]: groupId }))}
      onSave={save}
      onRun={run}
      onPageChange={changePage}
    />
  );
}

export function PlusSelfProducedView({
  status,
  results,
  resultsTotal,
  resultsPage,
  resultsPageSize,
  enabled,
  intervalMinutes,
  groups,
  groupSelection,
  configurationReady,
  loading,
  saving,
  running,
  onEnabledChange,
  onIntervalChange,
  onGroupChange,
  onSave,
  onRun,
  onPageChange,
}: ViewProps) {
  const siteId = status?.site_id || "US06-5002";
  const sourceGroupId = groupSelection.source_group_id;
  const plusGroupId = groupSelection.plus_group_id;
  const bannedGroupId = groupSelection.banned_group_id;
  const plusErrorGroupId = groupSelection.plus_error_group_id;
  const model = status?.model || "gpt-5.6-sol";
  const lastRun = status?.last_run;
  const invalidInterval = !Number.isFinite(intervalMinutes) || intervalMinutes < 1 || intervalMinutes > 1440;
  const groupsAreDistinct = hasDistinctGroupSelection(groupSelection);
  const settingsDisabled = loading || saving || running || !configurationReady;
  const resultsPages = Math.max(1, Math.ceil(resultsTotal / resultsPageSize));

  return (
    <section className="view plus-self-produced-page">
      <div className="topbar plus-self-produced-topbar">
        <div>
          <h2>plus自产</h2>
          <span className={`status-pill ${running ? "warning" : enabled ? "success" : ""}`}>
            {running ? "正在探测" : enabled ? "自动探测已启用" : "自动探测已停用"}
          </span>
        </div>
        <button type="button" onClick={onRun} disabled={loading || saving || running}>
          {running ? "探测中..." : "立即探测"}
        </button>
      </div>

      <section className="plus-workflow-band plus-settings-band">
        <div className="plus-workflow-facts" aria-label="探测目标">
          <WorkflowFact label="站点" value={siteId} />
          <WorkflowFact label="Plus 流向" value={`${sourceGroupId} → ${plusGroupId}`} />
          <WorkflowFact label="自产 401" value={`${sourceGroupId} → ${bannedGroupId}`} />
          <WorkflowFact label="Free 回退" value={`${plusGroupId} → ${sourceGroupId}`} />
          <WorkflowFact label="Plus 401" value={`${plusGroupId} → ${plusErrorGroupId}`} />
          <WorkflowFact label="模型" value={model} />
        </div>
        <div className="plus-settings-controls">
          <label className="switch-field">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(event) => onEnabledChange(event.target.checked)}
              disabled={settingsDisabled}
            />
            <span className="switch-track" aria-hidden="true"><span className="switch-thumb" /></span>
            <span className="switch-copy">
              <strong>自动探测</strong>
              <em>{enabled ? "已启用" : "已停用"}</em>
            </span>
          </label>
          <label className="plus-interval-field">
            <span>探测间隔</span>
            <span className="plus-interval-input">
              <input
                type="number"
                min={1}
                max={1440}
                value={intervalMinutes}
                onChange={(event) => onIntervalChange(Number(event.target.value))}
                disabled={settingsDisabled}
                aria-label="探测间隔分钟"
              />
              <b>分钟</b>
            </span>
          </label>
          <div className="plus-setting-summary">{intervalMinutes} 分钟</div>
          <button
            className="ghost"
            type="button"
            onClick={onSave}
            disabled={settingsDisabled || invalidInterval || !groupsAreDistinct}
          >
            {saving ? "保存中..." : "保存设置"}
          </button>
        </div>
        <div className="plus-routing-controls" aria-label="分组定向">
          <GroupSelect
            label="自产来源池"
            role="source_group_id"
            value={sourceGroupId}
            groups={groups}
            disabled={settingsDisabled}
            onChange={onGroupChange}
          />
          <GroupSelect
            label="Plus 正常池"
            role="plus_group_id"
            value={plusGroupId}
            groups={groups}
            disabled={settingsDisabled}
            onChange={onGroupChange}
          />
          <GroupSelect
            label="封禁池"
            role="banned_group_id"
            value={bannedGroupId}
            groups={groups}
            disabled={settingsDisabled}
            onChange={onGroupChange}
          />
          <GroupSelect
            label="Plus 错误池"
            role="plus_error_group_id"
            value={plusErrorGroupId}
            groups={groups}
            disabled={settingsDisabled}
            onChange={onGroupChange}
          />
        </div>
        {!groupsAreDistinct && (
          <p className="plus-settings-error" role="alert">四个分组必须一对一，不能重复</p>
        )}
      </section>

      <section className="plus-run-band" aria-label="最近运行">
        <RunMetric label="候选" value={lastRun?.candidates} />
        <RunMetric label="已测试" value={lastRun?.tested} />
        <RunMetric label="Plus 可用" value={lastRun?.eligible} />
        <RunMetric label="已晋级" value={lastRun?.promoted} tone="success" />
        <RunMetric label="已转封禁" value={lastRun?.banned} tone="danger" />
        <RunMetric label="已还原 Free" value={lastRun?.downgraded} tone="warning" />
        <RunMetric label="Plus 错误" value={lastRun?.plus_errors} tone="danger" />
        <RunMetric label="失败" value={lastRun?.failed} tone="warning" />
        <div className="plus-run-time">
          <span>最近完成</span>
          <strong>{formatDateTime(lastRun?.finished_at)}</strong>
        </div>
      </section>

      <section className="plus-results-section">
        <div className="panel-header">
          <div>
            <h3>探测结果</h3>
            <span className="muted">{loading ? "加载中..." : `${resultsTotal} 条`}</span>
          </div>
        </div>
        <div className="table-wrap plus-results-table-wrap">
          <table className="plus-results-table">
            <thead>
              <tr>
                <th>账号</th>
                <th>探测结果</th>
                <th>处理</th>
                <th>延迟</th>
                <th>错误</th>
                <th>时间</th>
              </tr>
            </thead>
            <tbody>
              {results.map((result) => {
                const classification = classificationDisplay(result.classification);
                const action = actionDisplay(result.action_status);
                return (
                  <tr key={result.id || `${result.remote_account_id}:${result.tested_at}`}>
                    <td className="plus-account-cell">
                      <strong>{result.account_name || result.email || `#${result.remote_account_id}`}</strong>
                      <span>#{result.remote_account_id}</span>
                    </td>
                    <td><span className={`status-pill ${classification.tone}`}>{classification.label}</span></td>
                    <td><span className={`status-pill ${action.tone}`}>{action.label}</span></td>
                    <td>{result.latency_ms === null || result.latency_ms === undefined ? "-" : `${result.latency_ms} ms`}</td>
                    <td className="plus-error-cell" title={result.error || ""}>{result.error || "-"}</td>
                    <td>{formatDateTime(result.tested_at)}</td>
                  </tr>
                );
              })}
              {!loading && results.length === 0 && (
                <tr><td colSpan={6} className="plus-empty-row">暂无探测结果</td></tr>
              )}
            </tbody>
          </table>
        </div>
        {resultsTotal > resultsPageSize && (
          <div className="pagination plus-results-pagination">
            <button
              className="ghost"
              type="button"
              disabled={loading || resultsPage <= 1}
              onClick={() => onPageChange(resultsPage - 1)}
            >
              上一页
            </button>
            <span>第 {resultsPage} / {resultsPages} 页</span>
            <button
              className="ghost"
              type="button"
              disabled={loading || resultsPage >= resultsPages}
              onClick={() => onPageChange(resultsPage + 1)}
            >
              下一页
            </button>
          </div>
        )}
      </section>
    </section>
  );
}

function WorkflowFact({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function GroupSelect({
  label,
  role,
  value,
  groups,
  disabled,
  onChange,
}: {
  label: string;
  role: PlusGroupRole;
  value: number;
  groups: PlusGroupOption[];
  disabled: boolean;
  onChange: (role: PlusGroupRole, groupId: number) => void;
}) {
  const selectedExists = groups.some((group) => group.id === value);
  return (
    <label className="plus-group-field">
      <span>{label}</span>
      <select
        aria-label={label}
        value={value}
        disabled={disabled}
        onChange={(event) => onChange(role, Number(event.target.value))}
      >
        {!selectedExists && <option value={value}>{value} · 当前分组不可用</option>}
        {groups.map((group) => (
          <option key={group.id} value={group.id}>{group.id} · {group.name}</option>
        ))}
      </select>
    </label>
  );
}

function RunMetric({ label, value, tone = "" }: { label: string; value?: number; tone?: string }) {
  return <div className={`plus-run-metric ${tone}`}><span>{label}</span><strong>{value ?? 0}</strong></div>;
}

function classificationDisplay(classification: string) {
  const displays: Record<string, { label: string; tone: string }> = {
    passed: { label: "测试通过", tone: "success" },
    rate_limited_but_eligible: { label: "429 可用", tone: "warning" },
    unauthorized_banned: { label: "401 封禁", tone: "danger" },
    model_not_supported: { label: "模型不支持", tone: "" },
    failed: { label: "失败", tone: "danger" },
  };
  return displays[classification] || { label: classification || "未知", tone: "" };
}

function actionDisplay(actionStatus: string) {
  const displays: Record<string, { label: string; tone: string }> = {
    promoted: { label: "已晋级", tone: "success" },
    verified_plus: { label: "Plus 正常", tone: "success" },
    banned: { label: "已转封禁", tone: "danger" },
    reverted_to_free: { label: "已还原 Free", tone: "warning" },
    plus_error: { label: "Plus 错误池", tone: "danger" },
    promotion_failed: { label: "晋级失败", tone: "danger" },
    ban_move_failed: { label: "转封禁失败", tone: "danger" },
    revert_failed: { label: "还原 Free 失败", tone: "danger" },
    plus_error_move_failed: { label: "转 Plus 错误池失败", tone: "danger" },
    model_reset_failed: { label: "模型重置失败", tone: "danger" },
    not_moved: { label: "未移动", tone: "" },
  };
  return displays[actionStatus] || { label: actionStatus || "-", tone: "" };
}

export function buildSettingsPayload(
  enabled: boolean,
  intervalMinutes: number,
  groupSelection: PlusGroupSelection,
) {
  return {
    enabled,
    interval_minutes: intervalMinutes,
    ...groupSelection,
  };
}

function hasDistinctGroupSelection(groupSelection: PlusGroupSelection) {
  return new Set(Object.values(groupSelection)).size === 4;
}

function groupSelectionFromStatus(status: PlusSelfProducedStatus): PlusGroupSelection {
  return {
    source_group_id: status.settings.source_group_id ?? status.source_group_id ?? DEFAULT_GROUP_SELECTION.source_group_id,
    plus_group_id: status.settings.plus_group_id ?? status.plus_group_id ?? DEFAULT_GROUP_SELECTION.plus_group_id,
    banned_group_id: status.settings.banned_group_id ?? status.banned_group_id ?? DEFAULT_GROUP_SELECTION.banned_group_id,
    plus_error_group_id: status.settings.plus_error_group_id ?? status.plus_error_group_id ?? DEFAULT_GROUP_SELECTION.plus_error_group_id,
  };
}

function groupSelectionFromSettings(
  settings: PlusSelfProducedStatus["settings"],
): PlusGroupSelection {
  return {
    source_group_id: settings.source_group_id,
    plus_group_id: settings.plus_group_id,
    banned_group_id: settings.banned_group_id,
    plus_error_group_id: settings.plus_error_group_id,
  };
}
