import { FormEvent, useMemo, useState, type Dispatch, type SetStateAction } from "react";
import { api } from "../api/client";
import type { ImportBatchResult, UploadFields, UploadMode, UploadTemplate } from "../types";
import { errorMessage, pretty, text } from "../utils/format";
import { extractLocalAccounts, isRecord } from "../utils/jsonParser";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

const initialUploadFields: UploadFields = {
  email_session: "",
  account_type: "plus",
  payment_type: "paypal_multi",
  twoFA: "",
  self_produced: "true",
  purchase_source: "",
  purchase_account_type: "",
  phone_bound: "true",
  phone_number: "",
  remark: "",
  manual_status_label: "",
  account_json: "",
};

const sampleJson = `{
  "exported_at": "2026-05-24T13:04:18.372Z",
  "proxies": [],
  "accounts": [
    {
      "name": "carlbarnes1890@outlook.com",
      "platform": "openai",
      "type": "oauth",
      "credentials": {
        "access_token": "eyJ...example",
        "chatgpt_account_id": "68f70276-db20-4b81-86c5-f1393a677673",
        "chatgpt_user_id": "user-G4X26dfSXXvmTxKNTa4yfe3p",
        "client_id": "app_EMoamEEZ73f0CkXaXp7hrann",
        "email": "carlbarnes1890@outlook.com",
        "expires_at": 1780491827,
        "id_token": "eyJ...example",
        "organization_id": "org-2d03woT0EPrJTsr5Aw4nmGiT",
        "plan_type": "plus",
        "refresh_token": "rt_...example"
      },
      "extra": {
        "email": "carlbarnes1890@outlook.com",
        "openai_oauth_responses_websockets_v2_enabled": false,
        "openai_oauth_responses_websockets_v2_mode": "off",
        "privacy_mode": "training_off",
        "2FA": "DP7...example"
      },
      "concurrency": 10,
      "priority": 1,
      "rate_multiplier": 1,
      "auto_pause_on_expired": true
    }
  ]
}`;

const purchasedSampleJson = `{
  "version": 1,
  "db_id": 1001,
  "platform": "chatgpt",
  "email": "demo@example.com",
  "password": "example-password",
  "login_identity": "demo@example.com",
  "phone": "+10000000000",
  "access_token": "eyJ...example",
  "refresh_token": "rt_...example",
  "id_token": "eyJ...example",
  "session_token": "",
  "client_id": "app_...example",
  "chatgpt_account_id": "account-id-example",
  "chatgpt_user_id": "user-id-example",
  "organization_id": "org-example",
  "status": "registered",
  "source": "login",
  "mailbox_connection": "demo@example.com----https://mail.example/latest",
  "mailbox_url": "https://mail.example/latest"
}`;

export function UploadPage({ token, showToast }: Props) {
  const [mode, setMode] = useState<UploadMode>("fill");
  const [uploadTemplate, setUploadTemplate] = useState<UploadTemplate>("sub2api");
  const [uploadIntent, setUploadIntent] = useState<"new" | "renew" | "purchase" | "historical" | "known_error">("new");
  const [fields, setFields] = useState<UploadFields>(initialUploadFields);
  const [parsedAccounts, setParsedAccounts] = useState<Record<string, unknown>[]>([]);
  const [parsedIndex, setParsedIndex] = useState(0);
  const [result, setResult] = useState<unknown>(null);
  const [bulkSaving, setBulkSaving] = useState(false);

  const currentParsed = parsedAccounts[parsedIndex];
  const parseInfo = useMemo(() => {
    if (!parsedAccounts.length) return "尚未导入账号 JSON。";
    return `当前 ${parsedIndex + 1} / ${parsedAccounts.length}：${parsedAccountLabel(currentParsed)}`;
  }, [currentParsed, parsedAccounts.length, parsedIndex]);

  const setField = <K extends keyof UploadFields>(key: K, value: UploadFields[K]) => {
    setFields((current) => ({ ...current, [key]: value }));
  };

  const applyPurchasedDefaults = (purchaseSource?: string) => {
    setUploadIntent("purchase");
    setFields((current) => ({
      ...current,
      account_type: "plus",
      payment_type: "no_card",
      self_produced: "false",
      purchase_source: purchaseSource ? current.purchase_source || purchaseSource : current.purchase_source,
      purchase_account_type: "plus",
      phone_bound: "true",
    }));
  };

  const applyPurchasedJinyaoDefaults = () => {
    applyPurchasedDefaults("金幺");
  };

  const loadParsedAccountIntoForm = (account: Record<string, unknown>) => {
    const credentials = isRecord(account.credentials) ? account.credentials : {};
    const extra = isRecord(account.extra) ? account.extra : {};
    const phoneNumber = text(extra.phone_number) || text(extra.phone) || text(account.phone);
    setFields((current) => ({
      ...current,
      account_json: pretty(account),
      email_session:
        current.email_session || text(extra.email_session) || text(extra.mailbox_connection) || text(credentials.email) || text(extra.email) || text(account.name),
      account_type: uploadTemplate === "purchased_jinyao" ? "plus" : normalizeAccountType(credentials.plan_type, current.account_type),
      twoFA: current.twoFA || text(extra["2FA"]),
      self_produced: uploadTemplate === "purchased_jinyao" ? "false" : current.self_produced,
      purchase_source: uploadTemplate === "purchased_jinyao" ? current.purchase_source || "金幺" : current.purchase_source,
      payment_type: uploadTemplate === "purchased_jinyao" ? "no_card" : current.payment_type,
      purchase_account_type: uploadTemplate === "purchased_jinyao" ? "plus" : current.purchase_account_type,
      phone_bound: uploadTemplate === "purchased_jinyao" || phoneNumber ? "true" : current.phone_bound,
      phone_number: current.phone_number || phoneNumber,
    }));
  };

  const parseUploadJson = async () => {
    try {
      const data = await api<unknown>("/imports/preview", token, {
        method: "POST",
        body: JSON.stringify({ payload: fields.account_json, metadata_defaults: {}, source_template: uploadTemplate }),
      });
      const accounts = extractLocalAccounts(fields.account_json, uploadTemplate);
      setParsedAccounts(accounts);
      setParsedIndex(0);
      setResult(data);
      loadParsedAccountIntoForm(accounts[0]);
      showToast("JSON 已解析，请逐个补充信息");
    } catch (error) {
      showToast(errorMessage(error), true);
    }
  };

  const previewUpload = async () => {
    try {
      const data = await api<unknown>("/imports/preview", token, {
        method: "POST",
        body: JSON.stringify({ payload: fields.account_json, metadata_defaults: {}, source_template: "sub2api" }),
      });
      setResult(data);
    } catch (error) {
      showToast(errorMessage(error), true);
    }
  };

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const metadata = buildUploadMetadata(fields, mode, uploadTemplate);
    const payload = mode === "parse" && parsedAccounts.length ? parsedAccounts[parsedIndex] : fields.account_json;
    const sourceTemplate = mode === "parse" && !parsedAccounts.length ? uploadTemplate : "sub2api";
    try {
      const data = await api<ImportBatchResult>("/import-batches", token, {
        method: "POST",
        body: JSON.stringify({
          payload,
          upload_intent: uploadIntent,
          source_template: sourceTemplate,
          metadata_defaults: metadata,
          remark: fields.remark || undefined,
        }),
      });
      setResult(data);
      if (mode === "parse" && parsedAccounts.length) {
        const wasLast = parsedIndex >= parsedAccounts.length - 1;
        if (wasLast) {
          setParsedAccounts([]);
          setParsedIndex(0);
          clearTextFields(setFields);
        } else {
          const nextIndex = parsedIndex + 1;
          setParsedIndex(nextIndex);
          clearTextFields(setFields);
          loadParsedAccountIntoForm(parsedAccounts[nextIndex]);
        }
      } else {
        clearTextFields(setFields);
      }
      const blockedCount = data.blocked?.length || 0;
      showToast(blockedCount ? `账号已保存，${blockedCount} 个 active 账号被阻止覆盖` : "账号已保存");
    } catch (error) {
      showToast(errorMessage(error), true);
    }
  };

  const moveParsed = (direction: -1 | 1) => {
    if (!parsedAccounts.length) return;
    const nextIndex = Math.min(parsedAccounts.length - 1, Math.max(0, parsedIndex + direction));
    setParsedIndex(nextIndex);
    loadParsedAccountIntoForm(parsedAccounts[nextIndex]);
  };

  const saveAllParsed = async () => {
    if (!parsedAccounts.length) {
      showToast("请先导入并解析 JSON", true);
      return;
    }
    const issues = validateParsedAccountsForBulkSave(parsedAccounts, fields);
    if (issues.length) {
      setResult({ message: "保存所有前校验未通过", issues });
      showToast(`有 ${issues.length} 个账号需要处理`, true);
      return;
    }

    setBulkSaving(true);
    try {
      const preparedAccounts = parsedAccounts.map((account) => applyBulkMetadataToAccountJson(account, fields));
      const data = await api<ImportBatchResult>("/import-batches", token, {
        method: "POST",
        body: JSON.stringify({
          payload: preparedAccounts,
          upload_intent: uploadIntent,
          source_template: "sub2api",
          metadata_defaults: buildBulkUploadMetadata(fields, mode, uploadTemplate),
          remark: fields.remark || undefined,
        }),
      });
      setResult(data);
      setParsedAccounts([]);
      setParsedIndex(0);
      clearTextFields(setFields);
      const blockedCount = data.blocked?.length || 0;
      showToast(blockedCount ? `已批量保存，${blockedCount} 个 active 账号被阻止覆盖` : `已批量保存 ${preparedAccounts.length} 个账号`);
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBulkSaving(false);
    }
  };

  return (
    <section className="view">
      <div className="topbar">
        <div>
          <h2>上传账号</h2>
          <p>填入模式适合直接保存；解析模式先导入 JSON，再逐个账号补齐信息。</p>
        </div>
      </div>
      <div className="upload-layout">
        <section className="panel">
          <div className="panel-header">
            <h3>上传账号</h3>
            <p>填入模式在前，解析模式在后。</p>
          </div>
          <form className="form-grid" onSubmit={submit}>
            <fieldset className="span-4 segmented">
              <legend>上传模式</legend>
              <label>
                <input checked={mode === "fill"} onChange={() => setMode("fill")} type="radio" />
                填入模式
              </label>
              <label>
                <input checked={mode === "parse"} onChange={() => setMode("parse")} type="radio" />
                解析模式
              </label>
            </fieldset>

            {mode === "parse" && (
              <label className="span-2">
                <span className="field-label">
                  <strong>解析模板</strong>
                  <span>（必填）</span>
                </span>
                <select
                  value={uploadTemplate}
                  onChange={(event) => {
                    const nextTemplate = event.target.value as UploadTemplate;
                    setUploadTemplate(nextTemplate);
                    if (nextTemplate === "purchased_jinyao") {
                      applyPurchasedJinyaoDefaults();
                    }
                  }}
                  required
                >
                  <option value="sub2api">sub2api 账号 JSON</option>
                  <option value="purchased_jinyao">购买账号：金幺</option>
                </select>
              </label>
            )}

            <label className="span-2">
              <span className="field-label">
                <strong>上传意图</strong>
                <span>（必填）</span>
              </span>
              <select
                value={uploadIntent}
                onChange={(event) => {
                  const nextIntent = event.target.value as typeof uploadIntent;
                  setUploadIntent(nextIntent);
                  if (nextIntent === "purchase") {
                    applyPurchasedDefaults();
                  }
                }}
                required
              >
                <option value="new">new：新制作账号</option>
                <option value="renew">renew：更新/续用旧账号 JSON</option>
                <option value="purchase">purchase：购买账号</option>
                <option value="historical">historical：历史账号</option>
                <option value="known_error">known_error：已知问题账号</option>
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
                  const value = event.target.value as UploadFields["self_produced"];
                  setFields((current) => ({
                    ...current,
                    self_produced: value,
                    account_type: value === "false" ? "plus" : current.account_type,
                    payment_type: value === "false" ? "no_card" : current.payment_type,
                    purchase_account_type: value === "false" ? "plus" : current.purchase_account_type,
                    phone_bound: value === "false" ? "true" : current.phone_bound,
                  }));
                }}
                required
              >
                <option value="true">是</option>
                <option value="false">否</option>
              </select>
            </label>
            {fields.self_produced === "false" && (
              <label>
                <span className="field-label">
                  <strong>购买来源</strong>
                  <span>（必填）</span>
                </span>
                <input value={fields.purchase_source} onChange={(event) => setField("purchase_source", event.target.value)} required />
              </label>
            )}
            {fields.self_produced === "false" && (
              <label>
                <span className="field-label">
                  <strong>购买时账号类型</strong>
                  <span>（必填）</span>
                </span>
                <select
                  value={fields.purchase_account_type}
                  onChange={(event) => setField("purchase_account_type", event.target.value as UploadFields["purchase_account_type"])}
                  required
                >
                  <option value="free">free</option>
                  <option value="plus">plus</option>
                  <option value="team">team子号</option>
                  <option value="pro">pro</option>
                  <option value="other">其他</option>
                </select>
              </label>
            )}

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
              <select
                value={fields.account_type}
                onChange={(event) => setField("account_type", event.target.value as UploadFields["account_type"])}
                required
              >
                <option value="plus">plus</option>
                <option value="team">team子号</option>
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
              <select
                value={fields.payment_type}
                onChange={(event) => setField("payment_type", event.target.value as UploadFields["payment_type"])}
                required
              >
                <option value="paypal_multi">PayPal 一卡多号</option>
                <option value="paypal_single">PayPal 一卡一号</option>
                <option value="no_card">不绑卡</option>
                <option value="gopay">gopay</option>
                <option value="other">其他</option>
              </select>
            </label>
            <label>
              <span className="field-label">
                <strong>是否绑定手机</strong>
                <span>（必填，布尔值）</span>
              </span>
              <select
                value={fields.phone_bound}
                onChange={(event) => setField("phone_bound", event.target.value as UploadFields["phone_bound"])}
                required
              >
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
            <label className="span-4">
              <span className="field-label">
                <strong>account_json 批量粘贴</strong>
                <span>（必填）</span>
              </span>
              <textarea
                className="json-input"
                value={fields.account_json}
                onChange={(event) => setField("account_json", event.target.value)}
                rows={18}
                spellCheck={false}
                required={mode === "fill" || !parsedAccounts.length}
                placeholder={mode === "parse" && uploadTemplate === "purchased_jinyao" ? purchasedSampleJson : sampleJson}
              />
            </label>

            {mode === "parse" && (
              <div className="span-4">
                <div className="button-row">
                  <button type="button" onClick={parseUploadJson}>
                    导入 JSON
                  </button>
                  <button type="button" className="ghost" onClick={() => moveParsed(-1)}>
                    上一个
                  </button>
                  <button type="button" className="ghost" onClick={() => moveParsed(1)}>
                    下一个
                  </button>
                  <button type="button" onClick={saveAllParsed} disabled={!parsedAccounts.length || bulkSaving}>
                    {bulkSaving ? "保存中..." : "保存所有"}
                  </button>
                </div>
                <div className="muted">{parseInfo}</div>
              </div>
            )}

            <div className="button-row span-4">
              {mode === "fill" && (
                <button type="button" onClick={previewUpload}>
                  预览
                </button>
              )}
              <button type="submit">保存账号</button>
            </div>
            {result !== null && <pre className="output inline-output span-4">{pretty(result)}</pre>}
          </form>
          <FieldHelp />
        </section>
      </div>
    </section>
  );
}

function FieldHelp() {
  return (
    <div className="field-help">
      <h3>字段说明</h3>
      <table>
        <thead>
          <tr>
            <th>字段</th>
            <th>参数</th>
            <th>必填</th>
            <th>作用</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td>解析模板</td>
            <td>
              <code>source_template</code>
            </td>
            <td>解析模式必填</td>
            <td>默认使用 sub2api 账号 JSON；金幺模板用于导入购买账号平铺 JSON。</td>
          </tr>
          <tr>
            <td>是否自产</td>
            <td>
              <code>self_produced</code>
            </td>
            <td>必填</td>
            <td>
              布尔值：<code>true</code> 表示自产，<code>false</code> 表示购买。
            </td>
          </tr>
          <tr>
            <td>购买来源</td>
            <td>
              <code>purchase_source</code>
            </td>
            <td>购买账号必填</td>
            <td>
              当 <code>self_produced = false</code> 时填写；金幺模板默认填入“金幺”。
            </td>
          </tr>
          <tr>
            <td>购买时账号类型</td>
            <td>
              <code>purchase_account_type</code>
            </td>
            <td>购买账号必填</td>
            <td>
              记录购买入库时的账号类型，和当前 <code>account_type</code> 分开保存；常见场景是购买时为 <code>free</code>，之后升级为 <code>plus</code>。
            </td>
          </tr>
          <tr>
            <td>邮箱和接码 session</td>
            <td>
              <code>email_session</code>
            </td>
            <td>必填</td>
            <td>
              维护邮箱和接码上下文；金幺模板会从 <code>mailbox_connection</code> 自动填入。
            </td>
          </tr>
          <tr>
            <td>账号类型</td>
            <td>
              <code>account_type</code>
            </td>
            <td>必填</td>
            <td>标记 plus/team子号/free/pro/其他。</td>
          </tr>
          <tr>
            <td>支付类型</td>
            <td>
              <code>payment_type</code>
            </td>
            <td>必填</td>
            <td>用于筛选、统计和风险判断。</td>
          </tr>
          <tr>
            <td>2FA</td>
            <td>
              <code>2FA</code>
            </td>
            <td>选填</td>
            <td>保存账号 2FA，填入模式会写入 account_json.extra。</td>
          </tr>
          <tr>
            <td>是否绑定手机</td>
            <td>
              <code>phone_bound</code>
            </td>
            <td>必填</td>
            <td>
              布尔值：<code>true</code> 表示是，<code>false</code> 表示否。
            </td>
          </tr>
          <tr>
            <td>手机号</td>
            <td>
              <code>phone_number</code>
            </td>
            <td>选填</td>
            <td>
              记录绑定手机号；当 <code>phone_bound = true</code> 时建议填写。
            </td>
          </tr>
          <tr>
            <td>备注</td>
            <td>
              <code>remark</code>
            </td>
            <td>选填</td>
            <td>
              任意补充信息；可补充说明 <code>phone_bound</code> 布尔值判断依据。
            </td>
          </tr>
          <tr>
            <td>状态标注</td>
            <td>
              <code>manual_status_label</code>
            </td>
            <td>选填</td>
            <td>团队内部人工状态。</td>
          </tr>
        </tbody>
      </table>
    </div>
  );
}

function buildUploadMetadata(fields: UploadFields, mode: UploadMode, uploadTemplate: UploadTemplate) {
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
    manual_status_label: fields.manual_status_label,
    remark: fields.remark,
    source_template: uploadTemplate,
    source: mode,
  };
}

function buildBulkUploadMetadata(fields: UploadFields, mode: UploadMode, uploadTemplate: UploadTemplate) {
  return {
    account_type: fields.account_type,
    payment_type: fields.payment_type,
    self_produced: fields.self_produced === "true",
    purchase_source: fields.purchase_source,
    purchase_account_type: fields.purchase_account_type,
    phone_bound: fields.phone_bound === "true",
    manual_status_label: fields.manual_status_label,
    remark: fields.remark,
    source_template: uploadTemplate,
    source: mode,
  };
}

function validateParsedAccountsForBulkSave(accounts: Record<string, unknown>[], fields: UploadFields) {
  const issues: Array<{ index: number; account: string; errors: string[] }> = [];
  accounts.forEach((account, index) => {
    const errors: string[] = [];
    if (!isRecord(account.credentials)) errors.push("缺少 credentials");
    if (!resolveAccountEmailSession(account, fields, accounts.length === 1)) errors.push("缺少邮箱和接码 session");
    if (!fields.account_type) errors.push("缺少账号类型");
    if (!fields.payment_type) errors.push("缺少支付类型");
    if (fields.self_produced === "false" && !fields.purchase_source.trim()) errors.push("购买账号缺少购买来源");
    if (fields.self_produced === "false" && !fields.purchase_account_type) errors.push("购买账号缺少购买时账号类型");
    if (fields.phone_bound !== "true" && fields.phone_bound !== "false") errors.push("是否绑定手机不是布尔值");
    if (errors.length) {
      issues.push({
        index: index + 1,
        account: parsedAccountLabel(account),
        errors,
      });
    }
  });
  return issues;
}

function applyBulkMetadataToAccountJson(account: Record<string, unknown>, fields: UploadFields) {
  const next = { ...account };
  const extra = isRecord(next.extra) ? { ...next.extra } : {};
  const credentials = isRecord(next.credentials) ? { ...next.credentials } : {};
  const emailSession = resolveAccountEmailSession(account, fields, false);
  const phoneNumber = resolveAccountPhoneNumber(account, fields, false);

  extra.email_session = emailSession;
  extra.account_type = fields.account_type;
  extra.payment_type = fields.payment_type;
  extra.self_produced = fields.self_produced === "true";
  extra.phone_bound = fields.phone_bound === "true";
  extra.source_template = "bulk_parse";
  if (fields.purchase_source) extra.purchase_source = fields.purchase_source;
  if (fields.purchase_account_type) extra.purchase_account_type = fields.purchase_account_type;
  if (phoneNumber) extra.phone_number = phoneNumber;
  if (fields.twoFA && !text(extra["2FA"])) extra["2FA"] = fields.twoFA;
  if (fields.remark) extra.remark = fields.remark;
  if (fields.manual_status_label) extra.manual_status_label = fields.manual_status_label;
  if (fields.account_type && !text(credentials.plan_type)) credentials.plan_type = fields.account_type;

  next.credentials = credentials;
  next.extra = extra;
  return next;
}

function resolveAccountEmailSession(account: Record<string, unknown>, fields: UploadFields, allowFormFallback: boolean) {
  const credentials = isRecord(account.credentials) ? account.credentials : {};
  const extra = isRecord(account.extra) ? account.extra : {};
  return (
    text(extra.email_session) ||
    text(extra.mailbox_connection) ||
    (allowFormFallback ? fields.email_session.trim() : "") ||
    text(credentials.email) ||
    text(extra.email) ||
    text(account.name)
  );
}

function resolveAccountPhoneNumber(account: Record<string, unknown>, fields: UploadFields, allowFormFallback: boolean) {
  const extra = isRecord(account.extra) ? account.extra : {};
  return text(extra.phone_number) || text(extra.phone) || text(account.phone) || (allowFormFallback ? fields.phone_number.trim() : "");
}

function clearTextFields(setFields: Dispatch<SetStateAction<UploadFields>>) {
  setFields((current) => ({
    ...current,
    email_session: "",
    twoFA: "",
    phone_number: "",
    manual_status_label: "",
    remark: "",
    account_json: "",
  }));
}

function parsedAccountLabel(account: Record<string, unknown> | undefined) {
  if (!account) return "未命名账号";
  const credentials = isRecord(account.credentials) ? account.credentials : {};
  return text(account.name) || text(credentials.email) || "未命名账号";
}

function isAccountType(value: unknown): value is UploadFields["account_type"] {
  return value === "plus" || value === "team" || value === "free" || value === "pro" || value === "other";
}

function normalizeAccountType(value: unknown, fallback: UploadFields["account_type"]): UploadFields["account_type"] {
  const normalized = text(value).trim().toLowerCase();
  if (["team", "team_sub", "team-sub", "team_child", "team_child_account", "team子号", "team 子号"].includes(normalized)) return "team";
  return isAccountType(normalized) ? normalized : fallback;
}
