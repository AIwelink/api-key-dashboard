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

type GrowthDatabaseTestResponse = {
  ok: boolean;
  error?: string;
  settings: GrowthDatabaseSettings;
};

type FormProps = {
  settings: GrowthDatabaseSettings | null;
  sqlDsn: string;
  loading: boolean;
  saving: boolean;
  testing: boolean;
  onSqlDsnChange: (value: string) => void;
  onSave: () => void;
  onTest: () => void;
};

export function TrafficAnalysisConfigPage({ token, showToast }: Props) {
  const [settings, setSettings] = useState<GrowthDatabaseSettings | null>(null);
  const [sqlDsn, setSqlDsn] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api<GrowthDatabaseSettings>("/settings/growth-database", token)
      .then((result) => {
        if (!cancelled) setSettings(result);
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
      showToast("增长数据库配置已保存");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
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
      sqlDsn={sqlDsn}
      loading={loading}
      saving={saving}
      testing={testing}
      onSqlDsnChange={setSqlDsn}
      onSave={saveSettings}
      onTest={testConnection}
    />
  );
}

export function GrowthDatabaseConfigForm({
  settings,
  sqlDsn,
  loading,
  saving,
  testing,
  onSqlDsnChange,
  onSave,
  onTest,
}: FormProps) {
  const configured = Boolean(settings?.sql_dsn_configured);
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
              placeholder={configured ? "已配置，留空不修改" : "host=postgres.example.com port=5432 user=growth_app password=secret dbname=aiwelink_growth sslmode=require"}
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
          <button type="submit" disabled={loading || saving || testing || (!configured && !sqlDsn.trim())}>
            {saving ? "保存中..." : "保存配置"}
          </button>
          <button className="ghost" type="button" onClick={onTest} disabled={!configured || loading || saving || testing}>
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
      </form>
    </section>
  );
}
