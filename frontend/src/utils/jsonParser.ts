export function parseLooseJsonLocal(value: string): unknown {
  const text = value.trim();
  if (!text) throw new Error("JSON 内容为空");
  try {
    return JSON.parse(text);
  } catch {
    // Fall through to streaming parse for `{...} {...}`.
  }

  const items: unknown[] = [];
  let index = 0;
  while (index < text.length) {
    while (index < text.length && /[\s,]/.test(text[index])) index += 1;
    if (index >= text.length) break;
    while (index < text.length && text[index] !== "{" && text[index] !== "[") index += 1;
    if (index >= text.length) break;

    const start = index;
    const opener = text[index];
    const closer = opener === "{" ? "}" : opener === "[" ? "]" : null;
    if (!closer) throw new Error(`JSON 格式不正确，位置 ${index}`);

    let depth = 0;
    let inString = false;
    let escaped = false;
    for (; index < text.length; index += 1) {
      const char = text[index];
      if (inString) {
        if (escaped) escaped = false;
        else if (char === "\\") escaped = true;
        else if (char === "\"") inString = false;
        continue;
      }
      if (char === "\"") inString = true;
      else if (char === opener) depth += 1;
      else if (char === closer) {
        depth -= 1;
        if (depth === 0) {
          index += 1;
          items.push(JSON.parse(text.slice(start, index)));
          break;
        }
      }
    }
    if (depth !== 0) throw new Error("JSON 括号不完整");
  }
  return items;
}

export type LocalUploadTemplate = "sub2api" | "purchased_jinyao";

export function extractLocalAccounts(payload: string, template: LocalUploadTemplate = "sub2api"): Record<string, unknown>[] {
  const parsed = parseLooseJsonLocal(payload);
  const candidates: unknown[] = [];
  const pushItem = (item: unknown) => {
    if (isRecord(item) && Array.isArray(item.accounts)) candidates.push(...item.accounts);
    else candidates.push(item);
  };

  if (Array.isArray(parsed)) parsed.forEach(pushItem);
  else pushItem(parsed);

  const accounts = candidates
    .map((item) => normalizeByTemplate(item, template))
    .filter((item): item is Record<string, unknown> => isRecord(item) && isRecord(item.credentials));
  if (!accounts.length) {
    if (template === "purchased_jinyao") throw new Error("没有解析到金幺购买账号，需要包含 email、access_token 和 mailbox_connection");
    throw new Error("没有解析到包含 credentials 的账号");
  }
  return accounts;
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function normalizeByTemplate(item: unknown, template: LocalUploadTemplate): unknown {
  if (template === "purchased_jinyao") return normalizePurchasedJinyao(item);
  return item;
}

function normalizePurchasedJinyao(item: unknown): unknown {
  if (!isRecord(item)) return item;
  if (isRecord(item.credentials)) return item;

  const email = textValue(item.email) || textValue(item.login_identity) || textValue(item.account_claims_email);
  const accessToken = textValue(item.access_token);
  const mailboxConnection = textValue(item.mailbox_connection);
  if (!email || !accessToken || !mailboxConnection) return item;

  const credentials: Record<string, unknown> = {
    access_token: item.access_token,
    refresh_token: item.refresh_token,
    id_token: item.id_token,
    session_token: item.session_token,
    client_id: item.client_id,
    email,
    chatgpt_account_id: item.chatgpt_account_id,
    chatgpt_user_id: item.chatgpt_user_id,
    organization_id: item.organization_id,
    project_id: item.project_id,
    workspace_id: item.workspace_id,
  };
  const expiresAt = jwtExp(accessToken);
  if (expiresAt) credentials.expires_at = expiresAt;

  const extra: Record<string, unknown> = {
    ...item,
    email,
    email_session: mailboxConnection,
    import_template: "purchased_jinyao",
  };

  return {
    name: email,
    platform: "openai",
    type: "oauth",
    expires_at: expiresAt || undefined,
    auto_pause_on_expired: true,
    concurrency: 10,
    priority: 1,
    credentials,
    extra,
  };
}

function textValue(value: unknown) {
  return typeof value === "string" ? value.trim() : "";
}

function jwtExp(token: string): number | undefined {
  const [, payload] = token.split(".");
  if (!payload) return undefined;
  try {
    const normalized = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = normalized.padEnd(Math.ceil(normalized.length / 4) * 4, "=");
    const parsed = JSON.parse(atob(padded));
    return typeof parsed.exp === "number" ? parsed.exp : undefined;
  } catch {
    return undefined;
  }
}
