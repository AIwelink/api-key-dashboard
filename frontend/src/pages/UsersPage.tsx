import { FormEvent, useEffect, useState } from "react";
import { RefreshCw, UserRoundCog } from "lucide-react";
import { api } from "../api/client";
import { usePageAutoRefresh } from "../hooks/usePageAutoRefresh";
import type { User, UserRole, UserRoleCatalog, UserStatus } from "../types";
import { errorMessage, formatDateTime } from "../utils/format";

type Props = {
  canManageOwners: boolean;
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type UserForm = {
  name: string;
  role: UserRole;
  status: UserStatus;
  password: string;
};

const STATUS_OPTIONS: Array<{ label: string; value: UserStatus }> = [
  { label: "正常", value: "active" },
  { label: "停用", value: "disabled" },
  { label: "待改密码", value: "pending_password_reset" },
];

const emptyEditForm: UserForm = {
  name: "",
  role: "maintainer",
  status: "active",
  password: "",
};

export function UsersPage({ canManageOwners, token, showToast }: Props) {
  const [users, setUsers] = useState<User[]>([]);
  const [roleCatalog, setRoleCatalog] = useState<UserRoleCatalog | null>(null);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [editForm, setEditForm] = useState<UserForm>(emptyEditForm);
  const [busy, setBusy] = useState(false);

  const loadUsers = async () => {
    const data = await api<{ items: User[] }>("/users", token);
    setUsers(sortUsersForManagement(data.items));
  };

  const loadPageData = async () => {
    const [usersData, settingsData] = await Promise.all([
      api<{ items: User[] }>("/users", token),
      api<UserRoleCatalog>("/settings/user-roles", token),
    ]);
    setUsers(sortUsersForManagement(usersData.items));
    setRoleCatalog(settingsData);
  };

  usePageAutoRefresh(loadPageData, { paused: Boolean(editingUser || busy) });

  useEffect(() => {
    loadPageData().catch((error) => showToast(errorMessage(error), true));
  }, []);

  const submitCreate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = Object.fromEntries(new FormData(form).entries());
    const password = String(values.password || "").trim();
    const payload: Record<string, unknown> = {
      email: String(values.email || "").trim(),
      name: String(values.name || "").trim(),
      role: values.role,
    };
    if (password) payload.password = password;

    setBusy(true);
    try {
      const data = await api<{ temporary_password?: string }>("/users", token, {
        method: "POST",
        body: JSON.stringify(payload),
      });
      await loadUsers();
      showToast(data.temporary_password ? `用户已创建，临时密码：${data.temporary_password}` : "用户已创建");
      form.reset();
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const startEditing = (item: User) => {
    setEditingUser(item);
    setEditForm({
      name: item.name || "",
      role: normalizeRole(item.role),
      status: normalizeStatus(item.status),
      password: "",
    });
  };

  const cancelEditing = () => {
    setEditingUser(null);
    setEditForm(emptyEditForm);
  };

  const submitEdit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editingUser) return;
    const userId = userIdentity(editingUser);
    if (!userId) {
      showToast("缺少用户 ID，无法保存", true);
      return;
    }

    const password = editForm.password.trim();
    const payload = {
      name: editForm.name.trim(),
      role: editForm.role,
      status: editForm.status,
    };

    setBusy(true);
    try {
      if (password) {
        await api<{ ok: boolean }>(`/users/${encodeURIComponent(userId)}/reset-password`, token, {
          method: "POST",
          body: JSON.stringify({ password }),
        });
      }
      const updated = await api<User>(`/users/${encodeURIComponent(userId)}`, token, {
        method: "PATCH",
        body: JSON.stringify(payload),
      });
      await loadUsers();
      setEditingUser(updated);
      setEditForm((current) => ({ ...current, password: "" }));
      showToast(
        editingUser.authorization_status === "pending"
          ? "权限已分配"
          : password ? "用户已更新，密码已重置" : "用户已更新",
      );
    } catch (error) {
      showToast(errorMessage(error), true);
    } finally {
      setBusy(false);
    }
  };

  const setEditField = <K extends keyof UserForm>(key: K, value: UserForm[K]) => {
    setEditForm((current) => ({ ...current, [key]: value }));
  };

  const isEditing = Boolean(editingUser);

  return (
    <section className="view">
      <div className="topbar">
        <div>
          <h2>用户</h2>
          <p>系统不开放注册，用户由后台创建。</p>
        </div>
        <button aria-label="刷新用户" onClick={() => loadPageData().catch((error) => showToast(errorMessage(error), true))} type="button">
          <RefreshCw aria-hidden="true" />
          刷新
        </button>
      </div>
      <div className="grid two">
        <section className="panel">
          <h3>用户列表</h3>
          <div className="list">
            {users.map((item) => (
              <div className={`list-item user-list-item ${item.authorization_status === "pending" ? "pending-authorization" : ""} ${editingUser && userIdentity(editingUser) === userIdentity(item) ? "selected" : ""}`} key={item.id || item.email || item.feishu_name || "user"}>
                <div className="user-card-main">
                  <div className="user-card-identity">
                    <span className="user-feishu-avatar" aria-hidden={!item.feishu_avatar_url}>
                      {item.feishu_avatar_url
                        ? <img src={item.feishu_avatar_url} alt="" />
                        : (item.name || item.email || "飞").slice(0, 1).toUpperCase()}
                    </span>
                    <div className="user-card-copy">
                      <div className="user-card-head">
                        <strong>{item.name || "未命名用户"}</strong>
                        <span className={`status-pill ${statusTone(item.status)}`}>{statusLabel(item.status)}</span>
                      </div>
                      <div className="muted">{userEmailLabel(item)}</div>
                    </div>
                  </div>
                  <div className="user-feishu-meta">
                    <span className={`user-feishu-status ${item.authorization_status === "pending" ? "is-pending" : item.feishu_bound ? "is-bound" : ""}`}>
                      {authorizationLabel(item)}
                    </span>
                    {item.last_feishu_login_at && <span>最后登录 {formatDateTime(item.last_feishu_login_at)}</span>}
                  </div>
                  <div className="muted">角色：{roleLabel(item.role, roleCatalog)}</div>
                </div>
                <div className="user-list-actions">
                  <button
                    className="ghost compact-button"
                    disabled={busy || !canEditUser(item, canManageOwners)}
                    onClick={() => startEditing(item)}
                    type="button"
                  >
                    <UserRoundCog aria-hidden="true" />
                    {userManagementActionLabel(item)}
                  </button>
                </div>
              </div>
            ))}
            {!users.length && <div className="muted">还没有用户。</div>}
          </div>
        </section>
        <section className="panel">
          <div className="panel-header">
            <div>
              <h3>{isEditing ? userManagementActionLabel(editingUser as User) : "添加用户"}</h3>
              <p>{isEditing && editingUser ? userEmailLabel(editingUser) : "创建后可在这里调整角色、状态或重置密码。"}</p>
            </div>
            {isEditing && (
              <button className="ghost compact-button" disabled={busy} onClick={cancelEditing} type="button">
                新增
              </button>
            )}
          </div>
          {isEditing ? (
            <form className="form-grid single" onSubmit={submitEdit}>
              {editingUser?.authorization_status === "pending" && (
                <div className="user-authorization-notice">
                  <strong>飞书身份已确认</strong>
                  <span>选择角色并分配权限后，该成员即可进入系统。</span>
                </div>
              )}
              <label>
                邮箱 <input value={editingUser ? userEmailLabel(editingUser) : ""} disabled readOnly />
              </label>
              <label>
                名称 <input value={editForm.name} onChange={(event) => setEditField("name", event.target.value)} required />
              </label>
              <label>
                角色
                <select
                  disabled={!roleCatalog}
                  value={editForm.role}
                  onChange={(event) => setEditField("role", event.target.value as UserRole)}
                >
                  {roleOptionsFor(roleCatalog, editingUser, canManageOwners).map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                状态
                <select value={editForm.status} onChange={(event) => setEditField("status", event.target.value as UserStatus)}>
                  {STATUS_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              {editingUser?.authorization_status !== "pending" && !editingUser?.email_is_placeholder && (
                <label>
                  重置密码 <input minLength={8} value={editForm.password} onChange={(event) => setEditField("password", event.target.value)} placeholder="留空则不修改密码" type="password" />
                </label>
              )}
              <div className="button-row">
                <button disabled={busy} type="submit">
                  {busy ? "保存中..." : userManagementActionLabel(editingUser as User)}
                </button>
                <button className="ghost" disabled={busy} onClick={cancelEditing} type="button">
                  取消
                </button>
              </div>
            </form>
          ) : (
            <form className="form-grid single" onSubmit={submitCreate}>
              <label>
                邮箱 <input name="email" type="email" required />
              </label>
              <label>
                名称 <input name="name" required />
              </label>
              <label>
                角色
                <select
                  defaultValue={defaultCreateRole(roleCatalog, canManageOwners)}
                  disabled={!roleCatalog}
                  key={roleCatalog?.role_order.join("|") || "loading"}
                  name="role"
                >
                  {roleOptionsFromCatalog(roleCatalog, canManageOwners).map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                临时密码 <input name="password" minLength={8} placeholder="留空自动生成" />
              </label>
              <button disabled={busy || !roleCatalog} type="submit">
                {busy ? "创建中..." : "创建用户"}
              </button>
            </form>
          )}
        </section>
      </div>
    </section>
  );
}

function userIdentity(user: User) {
  return user.id || user.email;
}

export function userEmailLabel(user: Pick<User, "email" | "email_is_placeholder">) {
  if (user.email_is_placeholder || !user.email) return "飞书未提供邮箱";
  return user.email;
}

function normalizeRole(value: string): UserRole {
  return value || "maintainer";
}

export function roleOptionsFromCatalog(catalog: UserRoleCatalog | null, includeOwner: boolean) {
  if (!catalog) return [];
  return catalog.role_order
    .filter((role) => Boolean(catalog.roles[role]))
    .filter((role) => includeOwner || role !== "owner")
    .map((role) => ({ label: catalog.roles[role].label, value: role }));
}

function roleOptionsFor(catalog: UserRoleCatalog | null, user: User | null, canManageOwners: boolean) {
  const options = roleOptionsFromCatalog(catalog, canManageOwners);
  if (!user?.role || options.some((option) => option.value === user.role)) return options;
  return [{ label: user.role, value: user.role }, ...options];
}

function defaultCreateRole(catalog: UserRoleCatalog | null, canManageOwners: boolean) {
  const options = roleOptionsFromCatalog(catalog, canManageOwners);
  return options.some((option) => option.value === "maintainer") ? "maintainer" : options[0]?.value || "";
}

function roleLabel(value: UserRole, catalog: UserRoleCatalog | null) {
  return catalog?.roles[value]?.label || value;
}

export function canEditUser(user: Pick<User, "role">, canManageOwners: boolean) {
  return user.role !== "owner" || canManageOwners;
}

export function sortUsersForManagement(users: User[]) {
  return [...users].sort((left, right) => {
    const leftRank = left.authorization_status === "pending" ? 0 : 1;
    const rightRank = right.authorization_status === "pending" ? 0 : 1;
    return leftRank - rightRank;
  });
}

export function authorizationLabel(user: Pick<User, "authorization_status" | "feishu_bound">) {
  if (user.authorization_status === "pending") return "飞书用户 · 待分配权限";
  return user.feishu_bound ? "飞书已绑定" : "未绑定飞书";
}

export function userManagementActionLabel(user: Pick<User, "authorization_status">) {
  return user.authorization_status === "pending" ? "分配权限" : "编辑";
}

function normalizeStatus(value: string | undefined): UserStatus {
  if (value === "active" || value === "disabled" || value === "pending_password_reset") return value;
  return "active";
}

function statusLabel(value: string | undefined) {
  return STATUS_OPTIONS.find((option) => option.value === value)?.label || value || "未知";
}

function statusTone(value: string | undefined) {
  if (value === "active") return "success";
  if (value === "disabled") return "danger";
  if (value === "pending_password_reset") return "warning";
  return "";
}
