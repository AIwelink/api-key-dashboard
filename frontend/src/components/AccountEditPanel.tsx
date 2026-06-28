import { FormEvent, useState } from "react";
import { api } from "../api/client";
import type { AccountDocument, AccountType } from "../types";
import { errorMessage, pretty, text } from "../utils/format";
import { parseLooseJsonLocal } from "../utils/jsonParser";

type Props = {
  account: AccountDocument;
  token: string;
  showToast: (message: string, isError?: boolean) => void;
  onClose: () => void;
  onSaved: () => Promise<void>;
};

type EditFields = {
  email_session: string;
  account_type: AccountType;
  payment_type: "paypal_multi" | "paypal_single" | "no_card" | "gopay" | "other";
  twoFA: string;
  self_produced: "true" | "false";
  purchase_source: string;
  purchase_account_type: AccountType | "";
  phone_bound: "true" | "false";
  phone_number: string;
  remark: string;
  manual_status_label: string;
  account_json: string;
};

export function AccountEditPanel({ account, token, showToast, onClose, onSaved }: Props) {
  const [fields, setFields] = useState<EditFields>(() => buildEditFields(account));
  const [saving, setSaving] = useState(false);
  const [refreshJson, setRefreshJson] = useState("");
  const [refreshingJson, setRefreshingJson] = useState(false);
  const canRefreshCredentials = true;

  const setField = <K extends keyof EditFields>(key: K, value: EditFields[K]) => {
    setFields((current) => ({ ...current, [key]: value }));
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    try {
      await api<AccountDocument>(`/accounts/${account.id}`, token, {
        method: "PATCH",
        body: JSON.stringify({
          metadata: buildEditMetadata(fields),
        }),
      });
      showToast("账号已更新");
      await onSaved();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setSaving(false);
    }
  };

  const refreshCredentialsJson = async () => {
    if (!refreshJson.trim()) {
      showToast("请先粘贴新获取的账号 JSON", true);
      return;
    }
    setRefreshingJson(true);
    try {
      await api<AccountDocument>(`/accounts/${account.id}/refresh-credentials-json`, token, {
        method: "POST",
        body: JSON.stringify({ account_json: refreshJson }),
      });
      showToast("账号 JSON 参数已更新");
      await onSaved();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setRefreshingJson(false);
    }
  };

  return (
    <div className="drawer-backdrop" role="dialog" aria-modal="true">
      <aside className="drawer-panel">
        <div className="drawer-header">
          <div>
            <h3>编辑账号</h3>
            <p>{accountEmail(account)}</p>
          </div>
          <button className="ghost compact-button" type="button" onClick={onClose}>
            关闭
          </button>
        </div>

        <form className="form-grid drawer-form" onSubmit={submit}>
          <label className="span-2">
            <span className="field-label">
              <strong>邮箱和接码 session</strong>
              <span>（必填）</span>
            </span>
            <input value={fields.email_session} onChange={(event) => setField("email_session", event.target.value)} required />
          </label>
          <label>
            <span className="field-label">
              <strong>账号类型</strong>
              <span>（必填）</span>
            </span>
            <select value={fields.account_type} onChange={(event) => setField("account_type", event.target.value as EditFields["account_type"])} required>
              <option value="plus">plus</option>
              <option value="team">team子号</option>
              <option value="k12">k12</option>
              <option value="free">free</option>
              <option value="pro">pro</option>
              <option value="other">其他</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>支付类型</strong>
              <span>（必填）</span>
            </span>
            <select value={fields.payment_type} onChange={(event) => setField("payment_type", event.target.value as EditFields["payment_type"])} required>
              <option value="paypal_multi">PayPal 一卡多号</option>
              <option value="paypal_single">PayPal 一卡一号</option>
              <option value="no_card">不绑卡</option>
              <option value="gopay">gopay</option>
              <option value="other">其他</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>是否自产</strong>
              <span>（必填，布尔值）</span>
            </span>
            <select
              value={fields.self_produced}
              onChange={(event) => {
                const value = event.target.value as EditFields["self_produced"];
                setFields((current) => ({
                  ...current,
                  self_produced: value,
                  purchase_account_type: value === "false" ? current.purchase_account_type || "free" : current.purchase_account_type,
                }));
              }}
              required
            >
              <option value="true">是</option>
              <option value="false">否</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>购买来源</strong>
              {fields.self_produced === "false" && <span>（必填）</span>}
            </span>
            <input value={fields.purchase_source} onChange={(event) => setField("purchase_source", event.target.value)} required={fields.self_produced === "false"} />
          </label>
          <label>
            <span className="field-label">
              <strong>购买时账号类型</strong>
              {fields.self_produced === "false" && <span>（必填）</span>}
            </span>
            <select
              value={fields.purchase_account_type}
              onChange={(event) => setField("purchase_account_type", event.target.value as EditFields["purchase_account_type"])}
              required={fields.self_produced === "false"}
            >
              <option value="">未标注</option>
              <option value="free">free</option>
              <option value="plus">plus</option>
              <option value="team">team子号</option>
              <option value="k12">k12</option>
              <option value="pro">pro</option>
              <option value="other">其他</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>是否绑定手机</strong>
              <span>（必填，布尔值）</span>
            </span>
            <select value={fields.phone_bound} onChange={(event) => setField("phone_bound", event.target.value as EditFields["phone_bound"])} required>
              <option value="true">是</option>
              <option value="false">否</option>
            </select>
          </label>
          <label>
            <span className="field-label">
              <strong>手机号</strong>
            </span>
            <input value={fields.phone_number} onChange={(event) => setField("phone_number", event.target.value)} />
          </label>
          <label className="span-2">
            <span className="field-label">
              <strong>2FA</strong>
            </span>
            <input value={fields.twoFA} onChange={(event) => setField("twoFA", event.target.value)} />
          </label>
          <label className="span-2">
            <span className="field-label">
              <strong>备注</strong>
            </span>
            <textarea value={fields.remark} onChange={(event) => setField("remark", event.target.value)} rows={2} />
          </label>
          <label className="span-2">
            <span className="field-label">
              <strong>状态标注</strong>
            </span>
            <input value={fields.manual_status_label} onChange={(event) => setField("manual_status_label", event.target.value)} />
          </label>
          <label className="span-4" hidden>
            <span className="field-label">
              <strong>account_json</strong>
              <span>（必填）</span>
            </span>
            <textarea
              className="json-input edit-json-input"
              value={fields.account_json}
              onChange={(event) => setField("account_json", event.target.value)}
              spellCheck={false}
              disabled
              required
            />
          </label>
          {canRefreshCredentials && (
            <label className="span-4">
              <span className="field-label">
                <strong>更新 JSON</strong>
                <span>只更新 access_token / refresh_token / id_token / session_token / expires_at</span>
              </span>
              <textarea
                className="json-input edit-json-input"
                value={refreshJson}
                onChange={(event) => setRefreshJson(event.target.value)}
                placeholder="粘贴新导出的 JSON，可以是单个账号对象，也可以是包含 accounts 的导出包。只会更新重新获取的凭证字段，不会替换完整账号 JSON。"
                spellCheck={false}
              />
            </label>
          )}
          <div className="button-row span-4">
            <button type="submit" disabled={saving}>
              {saving ? "保存中..." : "保存修改"}
            </button>
            {canRefreshCredentials && (
              <button className="ghost" type="button" onClick={refreshCredentialsJson} disabled={refreshingJson || saving}>
                {refreshingJson ? "更新中..." : "更新 JSON"}
              </button>
            )}
            <button className="ghost" type="button" onClick={onClose} disabled={saving}>
              取消
            </button>
          </div>
        </form>
      </aside>
    </div>
  );
}

function buildEditFields(account: AccountDocument): EditFields {
  const metadata = account.metadata || {};
  const accountJson = account.account_json || {};
  const credentials = asRecord(accountJson.credentials);
  const extra = asRecord(accountJson.extra);
  const selfProduced = normalizeBooleanSelect(metadata.self_produced ?? extra.self_produced, "true");
  const purchaseSource = text(metadata.purchase_source) || text(extra.purchase_source);
  return {
    email_session: text(metadata.email_session) || text(extra.email_session) || text(metadata.email) || text(credentials.email) || text(accountJson.name),
    account_type: normalizeAccountType(text(metadata.account_type) || text(extra.account_type) || text(credentials.plan_type)),
    payment_type: normalizePaymentType(text(metadata.payment_type) || text(extra.payment_type)),
    twoFA: text(metadata["2FA"]) || text(extra["2FA"]),
    self_produced: selfProduced,
    purchase_source: purchaseSource,
    purchase_account_type: normalizePurchaseAccountType(
      text(metadata.purchase_account_type) || text(extra.purchase_account_type),
      purchaseSource || selfProduced === "false" ? "free" : "",
    ),
    phone_bound: normalizePhoneBoundSelect(metadata.phone_bound ?? extra.phone_bound),
    phone_number: text(metadata.phone_number) || text(extra.phone_number),
    remark: text(metadata.remark) || text(extra.remark),
    manual_status_label: text(metadata.manual_status_label) || text(extra.manual_status_label),
    account_json: pretty(accountJson),
  };
}

function buildEditMetadata(fields: EditFields) {
  return {
    email_session: fields.email_session,
    account_type: fields.account_type,
    payment_type: fields.payment_type,
    "2FA": fields.twoFA,
    self_produced: fields.self_produced === "true",
    purchase_source: fields.purchase_source,
    purchase_account_type: fields.purchase_account_type,
    phone_bound: fields.phone_bound === "true",
    phone_number: fields.phone_number,
    remark: fields.remark,
    manual_status_label: fields.manual_status_label,
    source: "edit",
  };
}

function normalizeEditedAccountJson(parsed: unknown): Record<string, unknown> {
  if (Array.isArray(parsed)) {
    if (parsed.length !== 1) throw new Error("编辑时只能保存一个账号 JSON");
    return normalizeEditedAccountJson(parsed[0]);
  }
  if (!isRecord(parsed)) throw new Error("account_json 必须是对象");
  const wrapperAccounts = parsed.accounts;
  if (Array.isArray(wrapperAccounts)) {
    if (wrapperAccounts.length !== 1) throw new Error("编辑时 accounts 数组只能包含一个账号");
    return normalizeEditedAccountJson(wrapperAccounts[0]);
  }
  if (!isRecord(parsed.credentials)) throw new Error("account_json 需要包含 credentials");
  return parsed;
}

function accountEmail(account: AccountDocument) {
  const credentials = asRecord(account.account_json.credentials);
  return text(account.metadata.email) || text(credentials.email) || text(account.account_json.name) || "未识别邮箱";
}

function normalizeAccountType(value: string): AccountType {
  const normalized = value.trim().toLowerCase();
  if (["team", "team_sub", "team-sub", "team_child", "team_child_account", "team子号", "team 子号"].includes(normalized)) return "team";
  if (normalized === "plus" || normalized === "k12" || normalized === "free" || normalized === "pro" || normalized === "other") return normalized;
  return "plus";
}

function normalizePurchaseAccountType(value: string, fallback: AccountType | ""): EditFields["purchase_account_type"] {
  const normalized = value.trim().toLowerCase();
  if (["team", "team_sub", "team-sub", "team_child", "team_child_account", "team子号", "team 子号"].includes(normalized)) return "team";
  if (normalized === "plus" || normalized === "k12" || normalized === "free" || normalized === "pro" || normalized === "other") return normalized;
  return fallback;
}

function normalizePaymentType(value: string): EditFields["payment_type"] {
  if (value === "paypal_multi" || value === "paypal_single" || value === "no_card" || value === "gopay" || value === "other") return value;
  return "paypal_multi";
}

function normalizePhoneBoundSelect(value: unknown): EditFields["phone_bound"] {
  return normalizeBooleanSelect(value, "true");
}

function normalizeBooleanSelect(value: unknown, fallback: "true" | "false"): "true" | "false" {
  if (value === false) return "false";
  if (value === true) return "true";
  const normalized = text(value).trim().toLowerCase();
  if (["false", "no", "0"].includes(normalized)) return "false";
  if (["true", "yes", "1"].includes(normalized)) return "true";
  return fallback;
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
