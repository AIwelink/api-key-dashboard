import { FormEvent, useEffect, useState } from "react";
import { api } from "../api/client";
import type { User, UserRole, UserStatus } from "../types";
import { errorMessage } from "../utils/format";

type Props = {
  token: string;
  showToast: (message: string, isError?: boolean) => void;
};

type UserForm = {
  name: string;
  role: UserRole;
  status: UserStatus;
  password: string;
};

const ROLE_OPTIONS: Array<{ label: string; value: UserRole }> = [
  { label: "owner", value: "owner" },
  { label: "admin", value: "admin" },
  { label: "maintainer", value: "maintainer" },
  { label: "viewer", value: "viewer" },
];

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

export function UsersPage({ token, showToast }: Props) {
  const [users, setUsers] = useState<User[]>([]);
  const [editingUser, setEditingUser] = useState<User | null>(null);
  const [editForm, setEditForm] = useState<UserForm>(emptyEditForm);
  const [busy, setBusy] = useState(false);

  const loadUsers = async () => {
    const data = await api<{ items: User[] }>("/users", token);
    setUsers(data.items);
  };

  useEffect(() => {
    loadUsers().catch((error) => showToast(errorMessage(error), true));
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
      showToast(password ? "用户已更新，密码已重置" : "用户已更新");
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
        <button onClick={() => loadUsers().catch((error) => showToast(errorMessage(error), true))} type="button">
          刷新
        </button>
      </div>
      <div className="grid two">
        <section className="panel">
          <h3>用户列表</h3>
          <div className="list">
            {users.map((item) => (
              <div className={`list-item user-list-item ${editingUser && userIdentity(editingUser) === userIdentity(item) ? "selected" : ""}`} key={item.id || item.email}>
                <div className="user-card-main">
                  <div className="user-card-head">
                    <strong>{item.name || "未命名用户"}</strong>
                    <span className={`status-pill ${statusTone(item.status)}`}>{statusLabel(item.status)}</span>
                  </div>
                  <div className="muted">{item.email}</div>
                  <div className="muted">角色：{item.role}</div>
                </div>
                <div className="user-list-actions">
                  <button className="ghost compact-button" disabled={busy} onClick={() => startEditing(item)} type="button">
                    编辑
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
              <h3>{isEditing ? "编辑用户" : "添加用户"}</h3>
              <p>{isEditing ? editingUser?.email : "创建后可在这里调整角色、状态或重置密码。"}</p>
            </div>
            {isEditing && (
              <button className="ghost compact-button" disabled={busy} onClick={cancelEditing} type="button">
                新增
              </button>
            )}
          </div>
          {isEditing ? (
            <form className="form-grid single" onSubmit={submitEdit}>
              <label>
                邮箱 <input value={editingUser?.email || ""} disabled readOnly />
              </label>
              <label>
                名称 <input value={editForm.name} onChange={(event) => setEditField("name", event.target.value)} required />
              </label>
              <label>
                角色
                <select value={editForm.role} onChange={(event) => setEditField("role", event.target.value as UserRole)}>
                  {roleOptionsFor(editingUser).map((option) => (
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
              <label>
                重置密码 <input minLength={8} value={editForm.password} onChange={(event) => setEditField("password", event.target.value)} placeholder="留空则不修改密码" type="password" />
              </label>
              <div className="button-row">
                <button disabled={busy} type="submit">
                  {busy ? "保存中..." : "保存修改"}
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
                <select name="role" defaultValue="maintainer">
                  {ROLE_OPTIONS.filter((option) => option.value !== "owner").map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                临时密码 <input name="password" minLength={8} placeholder="留空自动生成" />
              </label>
              <button disabled={busy} type="submit">
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

function normalizeRole(value: string): UserRole {
  if (value === "owner" || value === "admin" || value === "viewer" || value === "maintainer") return value;
  return "maintainer";
}

function roleOptionsFor(user: User | null) {
  return user?.role === "owner" ? ROLE_OPTIONS : ROLE_OPTIONS.filter((option) => option.value !== "owner");
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
