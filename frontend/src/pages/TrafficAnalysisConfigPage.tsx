import { useEffect, useState } from "react";

import { api } from "../api/client";
import { errorMessage, formatDateTime } from "../utils/format";


type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

export type GrowthDatabaseSettings = {
  database_type: "postgresql";
  sql_dsn_configured: boolean;
  database_endpoint: string;
  last_database_test_at: string | null;
  last_database_test_ok: boolean | null;
  last_database_test_error: string;
  last_database_latency_ms: number | null;
  last_database_version: string;
};

export type GrowthSchemaStatus = {
  initialized: boolean;
  current_version: string | null;
  latest_version: string | null;
  pending_versions: string[];
  domain_table_count: number;
  applied_versions?: string[];
};

type GrowthDatabaseTestResponse = {
  ok: boolean;
  error?: string;
  settings: GrowthDatabaseSettings;
};

type FormProps = {
  settings: GrowthDatabaseSettings | null;
  schemaStatus: GrowthSchemaStatus | null;
  sqlDsn: string;
  loading: boolean;
  saving: boolean;
  testing: boolean;
  initializing: boolean;
  onSqlDsnChange: (value: string) => void;
  onSave: () => void;
  onTest: () => void;
  onInitialize: () => void;
};

export function TrafficAnalysisConfigPage({ token, showToast }: Props) {
  const [settings, setSettings] = useState<GrowthDatabaseSettings | null>(null);
  const [schemaStatus, setSchemaStatus] = useState<GrowthSchemaStatus | null>(null);
  const [sqlDsn, setSqlDsn] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [initializing, setInitializing] = useState(false);

  const loadSchemaStatus = async () => {
    const result = await api<GrowthSchemaStatus>("/settings/growth-database/schema", token);
    setSchemaStatus(result);
    return result;
  };

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<GrowthDatabaseSettings>("/settings/growth-database", token)
      .then(async (result) => {
        if (cancelled) return;
        setSettings(result);
        if (result.sql_dsn_configured) {
          try {
            const schema = await api<GrowthSchemaStatus>("/settings/growth-database/schema", token);
            if (!cancelled) setSchemaStatus(schema);
          } catch (error) {
            if (!cancelled) showToast(errorMessage(error), true);
          }
        } else {
          setSchemaStatus(null);
        }
      })
      .catch((error) => {
        if (!cancelled) showToast(errorMessage(error), true);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  const saveSettings = async () => {
    setSaving(true);
    try {
      const result = await api<GrowthDatabaseSettings>("/settings/growth-database", token, {
        method: "PUT",
        body: JSON.stringify({ sql_dsn: sqlDsn.trim() }),
      });
      setSettings(result);
      setSqlDsn("");
      if (result.sql_dsn_configured) await loadSchemaStatus();
      showToast("增长数据库配置已保存");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  };

  const initializeSchema = async () => {
    setInitializing(true);
    try {
      const result = await api<GrowthSchemaStatus>("/settings/growth-database/initialize", token, {
        method: "POST",
      });
      setSchemaStatus(result);
      const applied = result.applied_versions?.length || 0;
      showToast(applied ? `数据库结构已更新，执行 ${applied} 个版本` : "数据库结构已是最新版本");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setInitializing(false);
    }
  };

  const testConnection = async () => {
    setTesting(true);
    try {
      const result = await api<GrowthDatabaseTestResponse>("/settings/growth-database/test", token, {
        method: "POST",
      });
      setSettings(result.settings);
      showToast(result.ok ? "PostgreSQL 连接正常" : result.error || "PostgreSQL 连接失败", !result.ok);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setTesting(false);
    }
  };

  return (
    <GrowthDatabaseConfigForm
      settings={settings}
      schemaStatus={schemaStatus}
      sqlDsn={sqlDsn}
      loading={loading}
      saving={saving}
      testing={testing}
      initializing={initializing}
      onSqlDsnChange={setSqlDsn}
      onSave={saveSettings}
      onTest={testConnection}
      onInitialize={initializeSchema}
    />
  );
}

export function GrowthDatabaseConfigForm({
  settings,
  schemaStatus,
  sqlDsn,
  loading,
  saving,
  testing,
  initializing,
  onSqlDsnChange,
  onSave,
  onTest,
  onInitialize,
}: FormProps) {
  const configured = Boolean(settings?.sql_dsn_configured);
  const schemaPending = Boolean(schemaStatus?.pending_versions.length);
  const databaseBusy = saving || testing || initializing;
  return (
    <section className="view accounts-page">
      <div className="topbar">
        <div>
          <h2>访问流量分析配置</h2>
        </div>
      </div>

      <form
        className="panel site-config-panel"
        onSubmit={(event) => {
          event.preventDefault();
          onSave();
        }}
      >
        <div className="site-config-grid">
          <label>
            <span className="field-label"><strong>数据库类型</strong><span>（固定）</span></span>
            <input value="PostgreSQL" readOnly />
          </label>

          <label className="span-2">
            <span className="field-label"><strong>SQL_DSN</strong></span>
            <textarea
              value={sqlDsn}
              onChange={(event) => onSqlDsnChange(event.target.value)}
              placeholder={configured ? "已配置，留空不修改" : "postgresql://growth_app:password@postgres.example.com:5432/aiwelink_growth?sslmode=require"}
              autoComplete="new-password"
              className="sql-dsn-input"
              rows={5}
              spellCheck={false}
            />
            {configured && (
              <span className="cell-sub">已配置 · {settings?.database_endpoint || "连接信息已隐藏"}</span>
            )}
          </label>
        </div>

        <div className="button-row">
          <button type="submit" disabled={loading || databaseBusy || (!configured && !sqlDsn.trim())}>
            {saving ? "保存中..." : "保存配置"}
          </button>
          <button className="ghost" type="button" onClick={onTest} disabled={!configured || loading || databaseBusy}>
            {testing ? "测试中..." : "测试数据库连接"}
          </button>
          {loading && <span className="muted">正在加载...</span>}
        </div>

        {settings?.last_database_test_at && (
          <div className={`database-test-result ${settings.last_database_test_ok ? "is-success" : "is-error"}`}>
            <strong>{settings.last_database_test_ok ? "连接正常" : "连接失败"}</strong>
            <span>{formatDateTime(settings.last_database_test_at)}</span>
            {settings.last_database_test_ok ? (
              <span>
                {settings.last_database_latency_ms?.toFixed(0) ?? "-"} ms
                {settings.last_database_version ? ` · ${settings.last_database_version}` : ""}
              </span>
            ) : (
              <span>{settings.last_database_test_error || "未返回错误信息"}</span>
            )}
          </div>
        )}

        {configured && (
          <div className="growth-schema-status">
            <div>
              <span className="field-label"><strong>数据库结构</strong></span>
              {!schemaStatus ? (
                <span className="muted">正在读取结构状态...</span>
              ) : schemaStatus.initialized ? (
                <div className="growth-schema-summary is-ready">
                  <strong>结构已就绪</strong>
                  <span>{schemaStatus.current_version || schemaStatus.latest_version}</span>
                  <span>{schemaStatus.domain_table_count} 张业务表</span>
                </div>
              ) : (
                <div className="growth-schema-summary is-pending">
                  <strong>{schemaStatus.current_version ? "存在待执行更新" : "未初始化"}</strong>
                  <span>目标版本 {schemaStatus.latest_version || "-"}</span>
                  <span>{schemaStatus.domain_table_count} 张业务表</span>
                </div>
              )}
            </div>
            {schemaStatus && (!schemaStatus.initialized || schemaPending) && (
              <button
                type="button"
                onClick={onInitialize}
                disabled={loading || databaseBusy}
              >
                {initializing
                  ? "执行中..."
                  : schemaStatus.current_version
                    ? "执行结构更新"
                    : "初始化数据库"}
              </button>
            )}
          </div>
        )}
      </form>
    </section>
  );
}
