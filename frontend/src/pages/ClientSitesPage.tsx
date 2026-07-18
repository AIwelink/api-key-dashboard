import { useEffect, useState } from "react";
import { api } from "../api/client";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { errorMessage, formatDateTime } from "../utils/format";


type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type ClientSite = {
  id: string;
  name: string;
  client_type: "newapi" | "sub2api";
  base_url: string;
  api_key_configured?: boolean;
  admin_user_id?: string;
  status: "active" | "disabled";
  note?: string;
  updated_at?: string;
  sql_dsn_configured?: boolean;
  database_type?: "mysql" | "postgresql";
  database_endpoint?: string;
  data_retention_days?: number;
  last_database_test_at?: string;
  last_database_test_ok?: boolean;
  last_database_test_error?: string;
  last_database_latency_ms?: number;
  last_database_version?: string;
};

type ClientSiteForm = {
  id: string;
  name: string;
  client_type: "newapi" | "sub2api";
  base_url: string;
  api_key: string;
  admin_user_id: string;
  status: "active" | "disabled";
  note: string;
  sql_dsn: string;
  data_retention_days: number;
};

type DatabaseTestResult = {
  ok: boolean;
  database_type: "mysql" | "postgresql";
  database_endpoint: string;
  latency_ms: number;
  server_version?: string;
  error?: string;
  tested_at: string;
};

type ConfirmState = {
  title: string;
  message: string;
  details: Array<[string, string]>;
};

const emptyForm: ClientSiteForm = {
  id: "",
  name: "",
  client_type: "newapi",
  base_url: "",
  api_key: "",
  admin_user_id: "",
  status: "active",
  note: "",
  sql_dsn: "",
  data_retention_days: 90,
};

export function ClientSitesPage({ token, showToast }: Props) {
  const [sites, setSites] = useState<ClientSite[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<ClientSiteForm>(emptyForm);
  const [saving, setSaving] = useState(false);
  const [testingDatabase, setTestingDatabase] = useState(false);
  const [confirmState, setConfirmState] = useState<ConfirmState | null>(null);

  const loadSites = async () => {
    const data = await api<{ items: ClientSite[]; total: number }>("/client-sites", token);
    setSites(data.items);
    setSelectedId((current) => current || data.items[0]?.id || "");
  };

  useEffect(() => {
    loadSites().catch((error) => showToast(errorMessage(error), true));
  }, []);

  useEffect(() => {
    if (!selectedId) return;
    const site = sites.find((item) => item.id === selectedId);
    if (!site) return;
    setEditingId(site.id);
    setForm(siteToForm(site));
  }, [selectedId, sites]);

  const startCreate = () => {
    setSelectedId("");
    setEditingId(null);
    setForm(emptyForm);
  };

  const save = async () => {
    const payload = {
      id: form.id.trim(),
      name: form.name.trim(),
      client_type: form.client_type,
      base_url: form.base_url.trim(),
      admin_user_id: form.admin_user_id.trim(),
      status: form.status,
      note: form.note.trim(),
      data_retention_days: form.data_retention_days,
      ...(form.api_key.trim() ? { api_key: form.api_key.trim() } : {}),
      ...(form.sql_dsn.trim() ? { sql_dsn: form.sql_dsn.trim() } : {}),
    };
    if (!payload.id || !payload.base_url) {
      showToast("客户站点 ID 和 Base URL 必填", true);
      return;
    }
    if (payload.client_type === "newapi" && !payload.admin_user_id) {
      showToast("NewAPI 客户站点必须填写 Admin User ID", true);
      return;
    }
    setSaving(true);
    try {
      const saved = editingId
        ? await api<ClientSite>(`/client-sites/${encodeURIComponent(editingId)}`, token, {
            method: "PATCH",
            body: JSON.stringify(payload),
          })
        : await api<ClientSite>("/client-sites", token, {
            method: "POST",
            body: JSON.stringify(payload),
          });
      const data = await api<{ items: ClientSite[] }>("/client-sites", token);
      setSites(data.items);
      setSelectedId(saved.id);
      setEditingId(saved.id);
      setForm(siteToForm(saved));
      showToast("客户站点已保存");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  };

  const testDatabase = async () => {
    if (!editingId) {
      showToast("请先保存客户站点", true);
      return;
    }
    setTestingDatabase(true);
    try {
      const result = await api<DatabaseTestResult>(`/client-sites/${encodeURIComponent(editingId)}/database/test`, token, {
        method: "POST",
      });
      await loadSites();
      if (result.ok) {
        showToast(`数据库连接成功，延迟 ${result.latency_ms.toFixed(0)} ms`);
      } else {
        showToast(`数据库连接失败：${result.error || "未知错误"}`, true);
      }
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setTestingDatabase(false);
    }
  };

  const confirmDelete = () => {
    if (!editingId) return;
    setConfirmState({
      title: "删除客户站点",
      message: "删除后该站点不再出现在客户站点列表中，不会影响账号池后端配置。",
      details: [
        ["客户站点", form.name || editingId],
        ["类型", form.client_type === "newapi" ? "NewAPI" : "Sub2API"],
        ["Base URL", form.base_url],
      ],
    });
  };

  const deleteSite = async () => {
    if (!editingId) return;
    setSaving(true);
    try {
      await api(`/client-sites/${encodeURIComponent(editingId)}`, token, { method: "DELETE" });
      const data = await api<{ items: ClientSite[] }>("/client-sites", token);
      setSites(data.items);
      const next = data.items[0] || null;
      setSelectedId(next?.id || "");
      setEditingId(next?.id || null);
      setForm(next ? siteToForm(next) : emptyForm);
      showToast("客户站点已删除");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
      setConfirmState(null);
    }
  };

  const selectedSite = sites.find((site) => site.id === selectedId) || null;

  return (
    <section className="view accounts-page client-sites-page">
      <div className="topbar">
        <div>
          <h2>客户站点</h2>
          <p>管理向客户提供 API 服务的 NewAPI 与 Sub2API 节点，与账号池后端完全分离。</p>
        </div>
      </div>

      <section className="panel site-config-panel">
        <div className="panel-header">
          <div>
            <h3>客户服务连接</h3>
            <p>这里只保存客户访问节点及管理凭证，不参与账号池同步、容量预估和账号调度。</p>
          </div>
          <div className="button-row">
            <button className="compact-button" type="button" onClick={save} disabled={saving}>
              {saving ? "保存中..." : editingId ? "保存客户站点" : "创建客户站点"}
            </button>
            <button className="ghost compact-button" type="button" onClick={startCreate} disabled={saving}>
              新增客户站点
            </button>
            <button className="ghost compact-button danger-button" type="button" onClick={confirmDelete} disabled={!editingId || saving}>
              删除客户站点
            </button>
          </div>
        </div>

        <div className="site-config-grid">
          <label>
            <span className="field-label"><strong>已有客户站点</strong></span>
            <select value={editingId || ""} onChange={(event) => event.target.value && setSelectedId(event.target.value)}>
              <option value="">选择客户站点</option>
              {sites.map((site) => (
                <option key={site.id} value={site.id}>
                  [{site.client_type === "newapi" ? "NewAPI" : "Sub2API"}] {site.name || site.id}
                </option>
              ))}
            </select>
          </label>

          <fieldset className="span-2 segmented site-type-segment">
            <legend>客户站点类型</legend>
            <label>
              <input
                checked={form.client_type === "newapi"}
                name="client-site-type"
                onChange={() => setForm((current) => ({ ...current, client_type: "newapi" }))}
                type="radio"
              />
              NewAPI
            </label>
            <label>
              <input
                checked={form.client_type === "sub2api"}
                name="client-site-type"
                onChange={() => setForm((current) => ({ ...current, client_type: "sub2api", admin_user_id: "" }))}
                type="radio"
              />
              Sub2API
            </label>
          </fieldset>

          <label>
            <span className="field-label"><strong>客户站点 ID</strong></span>
            <input
              value={form.id}
              disabled={Boolean(editingId)}
              onChange={(event) => setForm((current) => ({ ...current, id: event.target.value }))}
              placeholder={form.client_type === "newapi" ? "customer-newapi-us01" : "customer-sub2api-us01"}
            />
          </label>

          <label>
            <span className="field-label"><strong>显示名称</strong></span>
            <input
              value={form.name}
              onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              placeholder="客户站点 US01"
            />
          </label>

          <label className="span-2">
            <span className="field-label"><strong>Base URL</strong></span>
            <input
              value={form.base_url}
              onChange={(event) => setForm((current) => ({ ...current, base_url: event.target.value }))}
              placeholder="https://api.customer.example.com"
              type="url"
            />
          </label>

          {form.client_type === "newapi" && (
            <label>
              <span className="field-label"><strong>Admin User ID</strong><span>（必填）</span></span>
              <input
                value={form.admin_user_id}
                onChange={(event) => setForm((current) => ({ ...current, admin_user_id: event.target.value }))}
                placeholder="例如 1"
                required
              />
            </label>
          )}

          <label>
            <span className="field-label"><strong>API Key</strong></span>
            <input
              value={form.api_key}
              onChange={(event) => setForm((current) => ({ ...current, api_key: event.target.value }))}
              placeholder={selectedSite?.api_key_configured ? "已配置，留空不修改" : "API Key"}
              type="password"
            />
            {selectedSite?.api_key_configured && <span className="cell-sub">密钥已配置</span>}
          </label>

          <label>
            <span className="field-label"><strong>状态</strong></span>
            <select value={form.status} onChange={(event) => setForm((current) => ({ ...current, status: event.target.value as ClientSiteForm["status"] }))}>
              <option value="active">active</option>
              <option value="disabled">disabled</option>
            </select>
          </label>

          <label className="span-2">
            <span className="field-label"><strong>备注</strong></span>
            <input value={form.note} onChange={(event) => setForm((current) => ({ ...current, note: event.target.value }))} placeholder="客户、区域或用途" />
          </label>
        </div>

        <div className="site-database-section">
          <div className="panel-header client-site-database-header">
            <div>
              <h3>数据库连接</h3>
              <p>用于后续历史数据分析；API 连接继续负责 RPM/TPM 等 URL 数据。</p>
            </div>
            <button
              className="ghost compact-button"
              type="button"
              onClick={testDatabase}
              disabled={!editingId || !selectedSite?.sql_dsn_configured || saving || testingDatabase}
            >
              {testingDatabase ? "测试中..." : "测试数据库连接"}
            </button>
          </div>

          <div className="site-config-grid">
            <label>
              <span className="field-label"><strong>数据库类型</strong><span>（固定）</span></span>
              <input value={form.client_type === "newapi" ? "MySQL" : "PostgreSQL"} readOnly />
            </label>

            <label className="span-2">
              <span className="field-label"><strong>SQL_DSN</strong></span>
              <textarea
                value={form.sql_dsn}
                onChange={(event) => setForm((current) => ({ ...current, sql_dsn: event.target.value }))}
                placeholder={selectedSite?.sql_dsn_configured ? "已配置，留空不修改" : sqlDsnPlaceholder(form.client_type)}
                autoComplete="new-password"
                className="sql-dsn-input"
                rows={5}
                spellCheck={false}
              />
              {selectedSite?.sql_dsn_configured && (
                <span className="cell-sub">已配置 · {selectedSite.database_endpoint || "连接信息已隐藏"}</span>
              )}
            </label>

            <label>
              <span className="field-label"><strong>本地 MongoDB 数据保留</strong><span>（天）</span></span>
              <input
                min={1}
                max={3650}
                type="number"
                value={form.data_retention_days}
                onChange={(event) => setForm((current) => ({ ...current, data_retention_days: Number(event.target.value) }))}
              />
            </label>
          </div>

          {selectedSite?.last_database_test_at && (
            <div className={`database-test-result ${selectedSite.last_database_test_ok ? "is-success" : "is-error"}`}>
              <strong>{selectedSite.last_database_test_ok ? "连接正常" : "连接失败"}</strong>
              <span>{formatDateTime(selectedSite.last_database_test_at)}</span>
              {selectedSite.last_database_test_ok ? (
                <span>
                  {selectedSite.last_database_latency_ms?.toFixed(0) ?? "-"} ms
                  {selectedSite.last_database_version ? ` · ${selectedSite.last_database_version}` : ""}
                </span>
              ) : (
                <span>{selectedSite.last_database_test_error || "未返回错误信息"}</span>
              )}
            </div>
          )}
        </div>

        {selectedSite?.updated_at && <div className="cell-sub client-site-updated-at">最后更新：{formatDateTime(selectedSite.updated_at)}</div>}
      </section>

      <ConfirmDialog
        confirmText="删除客户站点"
        details={confirmState?.details}
        message={confirmState?.message}
        onCancel={() => setConfirmState(null)}
        onConfirm={deleteSite}
        open={Boolean(confirmState)}
        title={confirmState?.title || ""}
        tone="danger"
      />
    </section>
  );
}


function siteToForm(site: ClientSite): ClientSiteForm {
  return {
    id: site.id,
    name: site.name || site.id,
    client_type: site.client_type || "newapi",
    base_url: site.base_url || "",
    api_key: "",
    admin_user_id: site.admin_user_id || "",
    status: site.status || "active",
    note: site.note || "",
    sql_dsn: "",
    data_retention_days: site.data_retention_days || 90,
  };
}


function sqlDsnPlaceholder(clientType: ClientSiteForm["client_type"]) {
  return clientType === "newapi"
    ? "user:password@tcp(host:3306)/database\n或粘贴 DATABASE_HOST / PORT / DBNAME / USER / PASSWORD"
    : "host=host port=5432 user=user password=password dbname=database sslmode=disable\n或粘贴 DATABASE_HOST / PORT / DBNAME / USER / PASSWORD";
}
