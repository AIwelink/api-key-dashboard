import { useEffect, useState } from "react";

import { api } from "../api/client";
import { errorMessage, formatDateTime } from "../utils/format";


type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type BalanceSummary = {
  balance_fen?: number;
  held_fen?: number;
  available_fen?: number;
  currency?: string;
};

type InventorySummary = {
  available?: number;
  missing?: number;
  needs_production?: boolean;
  estimated_total_fen?: number;
  estimated_unit_price_fen?: number;
  minimum_remaining_seconds?: number;
  maximum_remaining_seconds?: number;
};

export type AutoReplenishmentSettings = {
  provider: "sogouedu";
  base_url: string;
  enabled: boolean;
  username: string;
  password_configured: boolean;
  minimum_account_count: number;
  minimum_runway_minutes: number;
  product: "oauth_7d";
  local_account_type: "team";
  target_site_id: string;
  target_group_id: number | null;
  target_group_name: string;
  last_test_at?: string | null;
  last_test_ok?: boolean | null;
  last_test_error?: string | null;
  last_test_balance?: BalanceSummary | null;
  last_test_inventory?: InventorySummary | null;
  updated_at?: string | null;
  updated_by_name?: string | null;
};

export type AutoReplenishmentFormValue = {
  enabled: boolean;
  username: string;
  password: string;
  minimum_account_count: number;
  minimum_runway_minutes: number;
};

type FormProps = {
  settings: AutoReplenishmentSettings;
  form: AutoReplenishmentFormValue;
  loading: boolean;
  saving: boolean;
  testing: boolean;
  onChange: (next: AutoReplenishmentFormValue) => void;
  onSave: () => void;
  onTest: () => void;
};

export function AutoReplenishmentPage({ token, showToast }: Props) {
  const [settings, setSettings] = useState<AutoReplenishmentSettings | null>(null);
  const [form, setForm] = useState<AutoReplenishmentFormValue | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);

  const loadSettings = async () => {
    const next = await api<AutoReplenishmentSettings>("/auto-replenishment/settings", token);
    setSettings(next);
    setForm((current) => current ? { ...settingsToForm(next), password: current.password } : settingsToForm(next));
    return next;
  };

  useEffect(() => {
    loadSettings()
      .catch((error) => showToast(errorMessage(error), true))
      .finally(() => setLoading(false));
  }, []);

  const save = async () => {
    if (!form || !settings) return;
    if (!form.username.trim()) {
      showToast("请填写客户账号", true);
      return;
    }
    if (!settings.password_configured && !form.password) {
      showToast("首次保存必须填写客户密码", true);
      return;
    }
    if (!validInteger(form.minimum_account_count, 1, 10_000)) {
      showToast("最小账号数量必须为 1 至 10000 的整数", true);
      return;
    }
    if (!validInteger(form.minimum_runway_minutes, 1, 1_440)) {
      showToast("最小可用时间必须为 1 至 1440 分钟的整数", true);
      return;
    }

    setSaving(true);
    try {
      const next = await api<AutoReplenishmentSettings>("/auto-replenishment/settings", token, {
        method: "PUT",
        body: JSON.stringify({
          ...form,
          username: form.username.trim(),
        }),
      });
      setSettings(next);
      setForm(settingsToForm(next));
      showToast("自动补号配置已保存");
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  };

  const testConnection = async () => {
    if (!settings?.password_configured) {
      showToast("请先保存供应商账号密码", true);
      return;
    }
    setTesting(true);
    try {
      await api("/auto-replenishment/settings/test", token, { method: "POST" });
      await loadSettings();
      showToast("SogouEdu 连接测试成功");
    } catch (error) {
      await loadSettings().catch(() => undefined);
      showToast(errorMessage(error), true);
    } finally {
      setTesting(false);
    }
  };

  if (!settings || !form) {
    return (
      <section className="view auto-replenishment-page">
        <div className="auto-replenishment-loading">{loading ? "正在加载自动补号配置..." : "配置加载失败"}</div>
      </section>
    );
  }

  return (
    <section className="view auto-replenishment-page">
      <AutoReplenishmentSettingsForm
        settings={settings}
        form={form}
        loading={loading}
        saving={saving}
        testing={testing}
        onChange={setForm}
        onSave={save}
        onTest={testConnection}
      />
    </section>
  );
}

export function AutoReplenishmentSettingsForm({
  settings,
  form,
  loading,
  saving,
  testing,
  onChange,
  onSave,
  onTest,
}: FormProps) {
  const busy = loading || saving || testing;
  const balance = settings.last_test_balance;
  const inventory = settings.last_test_inventory;

  return (
    <div className="auto-replenishment-settings">
      <header className="auto-replenishment-header">
        <div>
          <h2>自动补号</h2>
          <p>配置短生命周期 Team 账号的即时补给阈值。本阶段仅保存并测试连接，不会创建订单。</p>
        </div>
        <label className="switch-field auto-replenishment-switch">
          <input
            checked={form.enabled}
            disabled={busy}
            onChange={(event) => onChange({ ...form, enabled: event.target.checked })}
            type="checkbox"
          />
          <span className="switch-track"><span className="switch-thumb" /></span>
          <span className="switch-copy">
            <strong>{form.enabled ? "自动补号已启用" : "自动补号已关闭"}</strong>
            <em>当前版本不会执行真实购买</em>
          </span>
        </label>
      </header>

      <div className="auto-replenishment-facts" aria-label="固定补号参数">
        <div><span>供应商</span><strong>SogouEdu</strong><small>{settings.base_url}</small></div>
        <div><span>商品</span><strong>{settings.product}</strong><small>普通 Team</small></div>
        <div><span>目标站点</span><strong>{settings.target_site_id}</strong><small>{settings.target_group_name}</small></div>
        <div><span>目标分组</span><strong>{settings.target_group_id ? `#${settings.target_group_id}` : "待校验"}</strong><small>{settings.target_group_name}</small></div>
      </div>

      <div className="auto-replenishment-form-grid">
        <label>
          <span className="field-label"><strong>客户账号</strong><span>（必填）</span></span>
          <input
            autoComplete="username"
            disabled={busy}
            value={form.username}
            onChange={(event) => onChange({ ...form, username: event.target.value })}
            placeholder="SogouEdu 客户账号"
          />
        </label>
        <label>
          <span className="field-label"><strong>客户密码</strong></span>
          <input
            autoComplete="new-password"
            disabled={busy}
            type="password"
            value={form.password}
            onChange={(event) => onChange({ ...form, password: event.target.value })}
            placeholder={settings.password_configured ? "密码已配置，留空不修改" : "首次保存必须填写"}
          />
          <small className={settings.password_configured ? "is-success" : ""}>
            {settings.password_configured ? "密码已配置" : "尚未配置密码"}
          </small>
        </label>
        <label>
          <span className="field-label"><strong>最小账号数量</strong></span>
          <input
            disabled={busy}
            min={1}
            max={10_000}
            name="minimum_account_count"
            type="number"
            value={form.minimum_account_count}
            onChange={(event) => onChange({ ...form, minimum_account_count: Number(event.target.value) })}
          />
          <small>低于该数量时满足后续补号条件</small>
        </label>
        <label>
          <span className="field-label"><strong>最小可用时间</strong><span>（分钟）</span></span>
          <input
            disabled={busy}
            min={1}
            max={1_440}
            name="minimum_runway_minutes"
            type="number"
            value={form.minimum_runway_minutes}
            onChange={(event) => onChange({ ...form, minimum_runway_minutes: Number(event.target.value) })}
          />
          <small>取实际、P50、P90 三种可用时间中的最小值</small>
        </label>
      </div>

      <div className="auto-replenishment-actions">
        <button className="compact-button success-button" disabled={busy} onClick={onSave} type="button">
          {saving ? "保存中..." : "保存配置"}
        </button>
        <button
          className="ghost compact-button"
          disabled={busy || !settings.password_configured}
          onClick={onTest}
          type="button"
        >
          {testing ? "测试中..." : "测试连接"}
        </button>
        <span>测试只读取登录、余额和库存，不会下单。</span>
      </div>

      {settings.last_test_at && (
        <section className={`auto-replenishment-test-result ${settings.last_test_ok ? "is-success" : "is-error"}`}>
          <div className="auto-replenishment-test-head">
            <strong>{settings.last_test_ok ? "连接正常" : "连接失败"}</strong>
            <span>{formatDateTime(settings.last_test_at)}</span>
          </div>
          {settings.last_test_ok ? (
            <div className="auto-replenishment-test-metrics">
              <div><span>可用余额</span><strong>{formatFen(balance?.available_fen)}</strong></div>
              <div><span>冻结金额</span><strong>{formatFen(balance?.held_fen)}</strong></div>
              <div><span>现货账号</span><strong>{displayNumber(inventory?.available)}</strong></div>
              <div><span>单价预估</span><strong>{formatFen(inventory?.estimated_unit_price_fen)}</strong></div>
              <div><span>剩余时长</span><strong>{remainingRange(inventory)}</strong></div>
            </div>
          ) : (
            <p>{settings.last_test_error || "供应商连接测试失败"}</p>
          )}
        </section>
      )}

      {settings.updated_at && (
        <div className="auto-replenishment-updated">
          最后保存：{formatDateTime(settings.updated_at)}{settings.updated_by_name ? ` · ${settings.updated_by_name}` : ""}
        </div>
      )}
    </div>
  );
}

function settingsToForm(settings: AutoReplenishmentSettings): AutoReplenishmentFormValue {
  return {
    enabled: settings.enabled === true,
    username: settings.username || "",
    password: "",
    minimum_account_count: settings.minimum_account_count || 2,
    minimum_runway_minutes: settings.minimum_runway_minutes || 5,
  };
}

function validInteger(value: number, minimum: number, maximum: number) {
  return Number.isInteger(value) && value >= minimum && value <= maximum;
}

function displayNumber(value: number | undefined) {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : "-";
}

function formatFen(value: number | undefined) {
  return typeof value === "number" ? `¥${(value / 100).toFixed(2)}` : "-";
}

function remainingRange(inventory: InventorySummary | null | undefined) {
  const minimum = inventory?.minimum_remaining_seconds;
  const maximum = inventory?.maximum_remaining_seconds;
  if (typeof minimum !== "number" && typeof maximum !== "number") return "-";
  const values = [minimum, maximum].filter((value): value is number => typeof value === "number");
  return values.map(formatDuration).join(" - ");
}

function formatDuration(seconds: number) {
  if (seconds < 60) return `${Math.max(0, Math.round(seconds))} 秒`;
  const minutes = Math.round(seconds / 60);
  return minutes < 60 ? `${minutes} 分钟` : `${(minutes / 60).toFixed(1)} 小时`;
}
